"""Backtest service — connects the BacktestEngine to TradingAgents data sources.

Usage:
    from tradingagents.backtest.service import BacktestService
    svc = BacktestService()
    result = svc.evaluate("000001", "买入", date(2026, 5, 10))
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from tradingagents.backtest.engine import (
    BacktestEngine,
    DailyBarLike,
    EvaluationConfig,
)

logger = logging.getLogger(__name__)


class _BarAdapter:
    """Adapt a DSA kline dict to DailyBarLike protocol."""
    def __init__(self, time: str, open_v: Optional[float] = None,
                 high: Optional[float] = None, low: Optional[float] = None,
                 close: Optional[float] = None, volume: Optional[float] = None):
        self.time = time
        self.high = high
        self.low = low
        self.close = close

    @property
    def date(self) -> date:
        if "-" in str(self.time):
            return date.fromisoformat(str(self.time)[:10])
        from datetime import datetime
        return datetime.fromtimestamp(float(self.time)).date()


class BacktestService:
    """回测服务 — 连接 BacktestEngine + DSA 数据源。

    提供 evaluate() 和 batch_evaluate() 两个入口。
    """

    def __init__(self, eval_window_days: int = 10, neutral_band_pct: float = 2.0):
        self.config = EvaluationConfig(
            eval_window_days=eval_window_days,
            neutral_band_pct=neutral_band_pct,
        )

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        operation_advice: str,
        analysis_date: date,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        """对个股的一个分析结论进行回测。

        流程:
        1. 通过 DSA 获取 analysis_date 向前 N 天的 K 线
        2. 获取分析日的收盘价作为入场价
        3. 调用 BacktestEngine.evaluate_single()
        4. 返回评估结果

        Args:
            symbol: 股票代码 (600519)
            operation_advice: 操作建议（买入/卖出/持有等）
            analysis_date: 分析日期
            stop_loss: 止损价（可选）
            take_profit: 止盈价（可选）

        Returns:
            评估结果字典
        """
        # 1) 获取入场价和 forward K 线
        start_price, forward_bars = self._fetch_data(symbol, analysis_date)
        if start_price is None or not forward_bars:
            return {
                "symbol": symbol,
                "operation_advice": operation_advice,
                "analysis_date": analysis_date,
                "eval_status": "insufficient_data",
                "error": f"无法获取 {symbol} 在 {analysis_date} 附近的行情数据",
            }

        # 2) 执行回测
        result = BacktestEngine.evaluate_single(
            operation_advice=operation_advice,
            analysis_date=analysis_date,
            start_price=start_price,
            forward_bars=forward_bars,
            stop_loss=stop_loss,
            take_profit=take_profit,
            config=self.config,
        )

        result["symbol"] = symbol
        logger.info(
            "✅ 回测完成: %s %s | 方向=%s 胜=%s 收益=%s%%",
            symbol, operation_advice,
            result.get("direction_expected", "?"),
            result.get("outcome", "?"),
            round(result.get("stock_return_pct", 0) or 0, 2),
        )
        return result

    def batch_evaluate(
        self,
        evaluations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """批量回测多个分析结论。

        Args:
            evaluations: 列表，每项包含:
                {symbol, operation_advice, analysis_date, stop_loss, take_profit}

        Returns:
            评估结果列表
        """
        results = []
        for ev in evaluations:
            result = self.evaluate(
                symbol=ev["symbol"],
                operation_advice=ev.get("operation_advice", ""),
                analysis_date=ev.get("analysis_date", date.today()),
                stop_loss=ev.get("stop_loss"),
                take_profit=ev.get("take_profit"),
            )
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # DSA 数据获取
    # ------------------------------------------------------------------

    def _fetch_data(
        self, symbol: str, analysis_date: date,
    ) -> tuple[Optional[float], List[DailyBarLike]]:
        """通过 DSA 获取入场价和 forward K 线。

        1. 获取 analysis_date 当天的日 K 线（确定入场价）
        2. 获取 analysis_date 之后 N 天的日 K 线

        Returns:
            (start_price, forward_bars)
        """
        try:
            from tradingagents.dataflows.unified_data_source import get_kline
        except ImportError:
            logger.error("DSA 数据源不可用")
            return None, []

        eval_days = self.config.eval_window_days

        # 拉 analysis_date 前到 eval_days 后的数据
        start_str = (analysis_date - timedelta(days=5)).isoformat()
        end_str = (analysis_date + timedelta(days=eval_days + 5)).isoformat()

        items, source = get_kline(symbol, period="day", limit=eval_days + 10)
        if not items:
            logger.warning("DSA 未返回 %s 的 K 线数据", symbol)
            return None, []

        # 将 K 线转成 DailyBarLike
        bars: List[_BarAdapter] = []
        for item in items:
            time_val = item.get("time") or item.get("date") or ""
            bar = _BarAdapter(
                time=str(time_val),
                open=_to_float(item.get("open")),
                high=_to_float(item.get("high")),
                low=_to_float(item.get("low")),
                close=_to_float(item.get("close")),
            )
            bars.append(bar)

        # 按日期排序（旧->新）
        bars.sort(key=lambda b: b.date)

        # 找到分析日的 bar 作为入场点
        start_bar = None
        for i, bar in enumerate(bars):
            if bar.date >= analysis_date:
                start_bar = bar
                forward_bars_raw = bars[i + 1 : i + 1 + eval_days]
                break

        if start_bar is None:
            logger.warning("%s 在 %s 无 K 线数据", symbol, analysis_date)
            return None, []

        start_price = start_bar.close or start_bar.high or start_bar.low
        if start_price is None or start_price <= 0:
            return None, []

        # 补足 forward bars 数量不够就用剩下的
        forward_bars: List[DailyBarLike] = list(forward_bars_raw) if len(forward_bars_raw) > 0 else []
        if len(forward_bars) < eval_days:
            logger.info(
                "%s forward bars 不足: %d/%d，用剩余数据",
                symbol, len(forward_bars), eval_days,
            )

        logger.info(
            "📊 DSA 数据: %s 入场价=%.4f forward=%d条 来源=%s",
            symbol, start_price, len(forward_bars), source or "?",
        )
        return start_price, forward_bars


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
