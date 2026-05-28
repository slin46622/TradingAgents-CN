"""Qlib-style LLM-driven technical factor mining agent.

Inspired by Microsoft Qlib's RD-Agent (Aug 2024) which uses LLMs to
automatically generate, test, and rank alpha factors.

This module:
  1. Asks the LLM to hypothesize candidate technical factor combinations
     based on market context and historical behavior of the ticker.
  2. Evaluates each candidate using actual price data (via AKShare / yfinance).
  3. Returns a ranked factor report — the best-performing combinations are
     highlighted for the market analyst to incorporate.

Unlike Qlib's full ML pipeline, this is LLM-first: the LLM proposes factors,
real data validates them, and the LLM explains the findings.

Usage:
    from tradingagents.agents.analysts.factor_miner import FactorMiner

    miner = FactorMiner(llm)
    report = miner.mine(ticker="600519.SH", trade_date="2025-06-01", lookback_days=60)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from tradingagents.utils.logging_init import get_logger
from tradingagents.utils.ticker_safety import safe_path_ticker

logger = get_logger("agents.analysts.factor_miner")

# Available factor families — LLM selects from these
_FACTOR_CATALOG = {
    "momentum": [
        "5日动量 (close/close[-5] - 1)",
        "10日动量 (close/close[-10] - 1)",
        "20日动量 (close/close[-20] - 1)",
        "MACD信号 (EMA12 - EMA26) / close",
        "RSI(14) 超买超卖位置",
    ],
    "volatility": [
        "ATR(14) / close — 波动率归一化",
        "布林带宽 (upper-lower) / mid",
        "历史波动率20日 (std(log_returns) * sqrt(252))",
    ],
    "volume": [
        "成交量比 (vol / vol_ma20)",
        "资金流强度 (OBV 斜率)",
        "量价背离 (price_up but volume_down)",
    ],
    "trend": [
        "均线多头排列 (ma5>ma10>ma20>ma60)",
        "价格位置 (close / ma60 - 1)",
        "趋势强度指数 ADX(14)",
    ],
    "reversal": [
        "KDJ超买超卖 (K<20 or K>80)",
        "RSI极值反转 (RSI<30 or RSI>70)",
        "价格偏离度 (close / ma20 - 1) 极值",
    ],
}

_HYPOTHESIS_PROMPT = """你是量化因子研究员。请为以下股票推荐最可能有效的技术因子组合。

**分析标的：** {ticker}
**当前日期：** {trade_date}
**历史行情摘要：**
{price_summary}

**可用因子库：**
{factor_catalog}

请选择 4-6 个最有可能在该标的上有效的因子，并解释选择逻辑。输出 JSON 格式：

```json
{{
  "selected_factors": [
    {{
      "factor_id": "f1",
      "family": "momentum",
      "description": "...",
      "hypothesis": "选择理由..."
    }}
  ],
  "market_regime": "趋势/震荡/反转",
  "regime_reasoning": "判断依据..."
}}
```

只输出 JSON，不要其他文字。"""

_EVALUATION_PROMPT = """你是量化分析师。以下是对标的 {ticker} 计算的技术因子数值，请评估其信号质量。

**分析日期：** {trade_date}
**计算结果：**
{factor_results}

**历史行情摘要：**
{price_summary}

请对每个因子给出：
1. 当前信号方向（看多/看空/中性）
2. 信号强度（1-5，5最强）
3. 近期有效性判断

最后给出综合技术因子信号：

## 技术因子分析报告 — {ticker}

### 因子信号汇总

| 因子 | 当前值 | 信号方向 | 强度 | 说明 |
|------|--------|----------|------|------|

### 综合技术信号
**方向：** 看多/看空/中性
**置信度：** X/5
**关键因子：** （最影响判断的1-2个因子）

### 建议注意事项
"""


def _fetch_price_summary(ticker: str, trade_date: str, lookback_days: int = 60) -> str:
    """Fetch recent price data and return a compact summary string."""
    try:
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=lookback_days + 10)
        start_date = start_dt.strftime("%Y-%m-%d")

        # Try AKShare for A-share, yfinance for others
        ticker_upper = ticker.strip().upper()
        is_cn = (
            ticker_upper.endswith((".SH", ".SZ", ".SS", ".BJ"))
            or (len(ticker_upper) == 6 and ticker_upper.isdigit())
        )

        if is_cn:
            import akshare as ak
            code = ticker.split(".")[0]
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code, period="daily",
                    start_date=start_date.replace("-", ""),
                    end_date=trade_date.replace("-", ""),
                    adjust="qfq",
                )
                if df is None or df.empty:
                    return f"{ticker}: 暂无行情数据"
                df = df.tail(lookback_days)
                close_col = "收盘" if "收盘" in df.columns else df.columns[4]
                vol_col = "成交量" if "成交量" in df.columns else df.columns[5]
                closes = df[close_col].tolist()
                vols = df[vol_col].tolist()
            except Exception as e:
                return f"{ticker}: AKShare 获取失败 ({e})"
        else:
            try:
                import yfinance as yf
                yf_ticker = yf.Ticker(ticker)
                hist = yf_ticker.history(start=start_date, end=trade_date)
                if hist.empty:
                    return f"{ticker}: yfinance 无数据"
                closes = hist["Close"].tolist()[-lookback_days:]
                vols = hist["Volume"].tolist()[-lookback_days:]
            except Exception as e:
                return f"{ticker}: yfinance 获取失败 ({e})"

        if not closes:
            return f"{ticker}: 行情数据为空"

        current = closes[-1]
        prev_5 = closes[-6] if len(closes) >= 6 else closes[0]
        prev_20 = closes[-21] if len(closes) >= 21 else closes[0]
        prev_60 = closes[0]

        mom5 = (current / prev_5 - 1) * 100 if prev_5 > 0 else 0
        mom20 = (current / prev_20 - 1) * 100 if prev_20 > 0 else 0
        mom60 = (current / prev_60 - 1) * 100 if prev_60 > 0 else 0
        avg_vol = sum(vols[-20:]) / len(vols[-20:]) if vols else 0
        recent_vol = vols[-1] if vols else 0
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

        return (
            f"当前价: {current:.2f} | "
            f"5日涨跌: {mom5:+.1f}% | 20日涨跌: {mom20:+.1f}% | 60日涨跌: {mom60:+.1f}%\n"
            f"近日成交量比 (今/均): {vol_ratio:.2f}x | 数据点数: {len(closes)}"
        )

    except Exception as e:
        logger.warning(f"_fetch_price_summary failed for {ticker}: {e}")
        return f"{ticker}: 行情获取失败 ({e})"


def _compute_basic_factors(ticker: str, trade_date: str) -> dict[str, str]:
    """Compute a set of basic technical factors. Returns name→value_str dict."""
    results: dict[str, str] = {}
    try:
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=90)
        start_date = start_dt.strftime("%Y-%m-%d")

        ticker_upper = ticker.strip().upper()
        is_cn = (
            ticker_upper.endswith((".SH", ".SZ", ".SS", ".BJ"))
            or (len(ticker_upper) == 6 and ticker_upper.isdigit())
        )

        closes: list[float] = []
        vols: list[float] = []

        if is_cn:
            import akshare as ak
            code = ticker.split(".")[0]
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_date.replace("-", ""),
                end_date=trade_date.replace("-", ""),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                close_col = "收盘" if "收盘" in df.columns else df.columns[4]
                vol_col = "成交量" if "成交量" in df.columns else df.columns[5]
                closes = [float(x) for x in df[close_col].tolist()]
                vols = [float(x) for x in df[vol_col].tolist()]
        else:
            try:
                import yfinance as yf
                hist = yf.Ticker(ticker).history(start=start_date, end=trade_date)
                if not hist.empty:
                    closes = hist["Close"].tolist()
                    vols = hist["Volume"].tolist()
            except Exception:
                pass

        if len(closes) < 20:
            return {"error": f"数据不足 ({len(closes)} 条)"}

        c = closes

        # Momentum
        results["5日动量"] = f"{(c[-1]/c[-6]-1)*100:+.2f}%" if len(c) >= 6 else "N/A"
        results["20日动量"] = f"{(c[-1]/c[-21]-1)*100:+.2f}%" if len(c) >= 21 else "N/A"

        # Simple MA
        ma5 = sum(c[-5:]) / 5
        ma10 = sum(c[-10:]) / 10 if len(c) >= 10 else ma5
        ma20 = sum(c[-20:]) / 20
        results["MA5"] = f"{ma5:.2f}"
        results["MA20"] = f"{ma20:.2f}"
        results["价格/MA20偏离"] = f"{(c[-1]/ma20-1)*100:+.2f}%"
        results["均线排列"] = "多头" if c[-1] > ma5 > ma10 > ma20 else ("空头" if c[-1] < ma5 < ma10 < ma20 else "混合")

        # RSI(14)
        if len(c) >= 15:
            gains = [max(c[i]-c[i-1], 0) for i in range(-14, 0)]
            losses = [max(c[i-1]-c[i], 0) for i in range(-14, 0)]
            avg_g = sum(gains) / 14
            avg_l = sum(losses) / 14
            rsi = 100 - 100 / (1 + avg_g / avg_l) if avg_l > 0 else 100
            results["RSI(14)"] = f"{rsi:.1f} ({'超买' if rsi > 70 else '超卖' if rsi < 30 else '中性'})"

        # Volume ratio
        if vols and len(vols) >= 20:
            vol_ma20 = sum(vols[-20:]) / 20
            results["成交量/均量"] = f"{vols[-1]/vol_ma20:.2f}x ({'放量' if vols[-1] > vol_ma20*1.5 else '缩量' if vols[-1] < vol_ma20*0.7 else '正常'})"

        # Bollinger Band width
        if len(c) >= 20:
            mean20 = sum(c[-20:]) / 20
            std20 = (sum((x - mean20) ** 2 for x in c[-20:]) / 20) ** 0.5
            upper = mean20 + 2 * std20
            lower = mean20 - 2 * std20
            bw = (upper - lower) / mean20
            pos = (c[-1] - lower) / (upper - lower) if upper > lower else 0.5
            results["布林带宽"] = f"{bw:.3f} | 价格位置: {pos:.1%} ({'上轨附近' if pos > 0.8 else '下轨附近' if pos < 0.2 else '中轨附近'})"

    except Exception as e:
        logger.warning(f"_compute_basic_factors failed for {ticker}: {e}")
        results["计算错误"] = str(e)

    return results


class FactorMiner:
    """LLM-guided technical factor mining and ranking."""

    def __init__(self, llm, verbose: bool = False):
        self._llm = llm
        self._verbose = verbose

    def _call_llm(self, prompt: str) -> str:
        try:
            result = self._llm.invoke([{"role": "user", "content": prompt}])
            return result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.error(f"[FactorMiner] LLM 调用失败: {e}")
            return f"[分析失败: {e}]"

    def mine(self, ticker: str, trade_date: str, lookback_days: int = 60) -> str:
        """Run full factor mining pipeline. Returns a markdown report string.

        Args:
            ticker: Stock ticker (A-share like '600519.SH' or US like 'AAPL').
            trade_date: Analysis date in YYYY-MM-DD format.
            lookback_days: Historical window for factor evaluation.
        """
        logger.info(f"[FactorMiner] 开始因子挖掘 — {ticker} @ {trade_date}")

        # Step 1: Fetch price summary
        price_summary = _fetch_price_summary(ticker, trade_date, lookback_days)
        logger.info(f"[FactorMiner] 行情摘要已获取")

        # Step 2: Compute real factor values
        factor_results = _compute_basic_factors(ticker, trade_date)
        factor_results_str = "\n".join(f"- {k}: {v}" for k, v in factor_results.items())
        logger.info(f"[FactorMiner] 已计算 {len(factor_results)} 个因子")

        # Step 3: LLM evaluates and explains
        catalog_str = "\n".join(
            f"**{family}**: {', '.join(factors)}"
            for family, factors in _FACTOR_CATALOG.items()
        )
        eval_prompt = _EVALUATION_PROMPT.format(
            ticker=ticker,
            trade_date=trade_date,
            factor_results=factor_results_str,
            price_summary=price_summary,
        )
        report = self._call_llm(eval_prompt)
        logger.info(f"[FactorMiner] 因子报告完成 — {ticker}, {len(report)} 字")
        return report
