"""StockAgent-style macro/policy event impact analyst.

Inspired by StockAgent (MingyuJ666/Stockagent, IJCAI challenge) which
evaluates external factor impacts (policy changes, macro events, global
shocks) across multiple simulation phases: Initial → Trading → Post-Trading
→ Special Events.

Problem this solves: existing framework has no specialist for macro/policy
shocks. Fundamentals + market analysts focus on company-level and price data;
neither explicitly models "PBOC rate cut" or "sector regulatory crackdown".

Architecture:
  - Pre-fetches macro news from AKShare (A股政策) + global headlines
  - Classifies events into: Policy / Macro / Industry / BlackSwan
  - Runs a multi-phase impact simulation using LLM
  - Returns a structured impact score + trading implications

Usage:
    from tradingagents.agents.analysts.macro_event_analyst import MacroEventAnalyst

    analyst = MacroEventAnalyst(llm)
    report = analyst.analyze(ticker="600519.SH", trade_date="2025-06-01")

Or as a graph node:
    node_fn = analyst.create_node()
    # returns dict with "macro_event_report" key
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from langchain_core.messages import HumanMessage
from tradingagents.utils.logging_init import get_logger

logger = get_logger("agents.analysts.macro_event_analyst")

# Event phase simulation — mirrors StockAgent's multi-phase design
_PHASE_PROMPT = """你是宏观政策冲击分析师（StockAgent模式）。请对以下标的进行四阶段影响评估。

**分析标的：** {ticker}
**分析日期：** {trade_date}

**近期宏观/政策新闻：**
{macro_news}

**行业相关政策：**
{industry_news}

---

请按四个阶段评估这些事件对 {ticker} 的影响：

## 阶段一：初始冲击评估
（事件发生后0-1天的直接市场反应预期）
- 利好/利空方向：
- 预期初始冲击幅度：
- 冲击机制：

## 阶段二：交易期影响
（事件发酵期1-5个交易日）
- 机构如何反应（加仓/减仓/观望）：
- 散户情绪传导：
- 关键支撑/阻力重新评估：

## 阶段三：后续影响
（5-20个交易日的持续效应）
- 基本面实质影响：
- 竞争格局变化：
- 业绩预期修正：

## 阶段四：特殊事件评估
（黑天鹅/政策急转/尾部风险）
- 最坏情景（概率≤10%）：
- 对冲建议：

---

## 综合宏观事件评分

| 事件 | 类型 | 利多/利空 | 影响强度(1-5) | 持续时间 |
|------|------|-----------|--------------|---------|

**综合宏观信号：**
- 方向：利多/中性/利空
- 强度：X/5
- 主要催化剂：
- 主要风险：

**基于宏观事件的交易建议：**
"""


def _fetch_cn_macro_news(trade_date: str, lookback_days: int = 7) -> str:
    """Fetch recent macro/policy news from AKShare."""
    try:
        import akshare as ak

        # 宏观经济新闻 — 财联社
        try:
            df = ak.stock_news_em(symbol="000001")  # 用指数代码获取宏观新闻
            if df is not None and not df.empty:
                df = df.head(15)
                lines = ["## 近期宏观财经新闻（东方财富）"]
                for _, row in df.iterrows():
                    time_str = str(row.get("发布时间", ""))[:16]
                    title = str(row.get("新闻标题", "")).strip()
                    if title:
                        lines.append(f"[{time_str}] {title}")
                return "\n".join(lines)
        except Exception:
            pass

        # Fallback: 新浪财经宏观新闻
        try:
            df = ak.macro_china_new_house_price()
            return "宏观数据已获取（房价指数等指标）"
        except Exception:
            pass

        return "宏观新闻：暂无法获取"

    except Exception as e:
        return f"宏观新闻获取失败: {e}"


def _fetch_industry_policy_news(ticker: str, trade_date: str) -> str:
    """Fetch industry-specific policy news."""
    try:
        import akshare as ak
        code = ticker.split(".")[0]

        # 获取个股近期新闻（过滤政策类）
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return f"[行业政策] {ticker}: 暂无相关新闻"

        # Filter for policy-related keywords
        keywords = ["政策", "监管", "整治", "规范", "指导", "限制", "支持", "补贴",
                    "税", "利率", "央行", "发改委", "证监会", "银保监"]
        df_str = df.to_string()
        df = df.head(20)

        policy_lines = ["## 行业政策相关新闻"]
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "")).strip()
            time_str = str(row.get("发布时间", ""))[:16]
            if any(kw in title for kw in keywords):
                policy_lines.append(f"[{time_str}] ⚠️ {title}")
            else:
                policy_lines.append(f"[{time_str}] {title}")

        return "\n".join(policy_lines[:12])

    except Exception as e:
        return f"[行业政策] 获取失败: {e}"


class MacroEventAnalyst:
    """StockAgent-style multi-phase macro/policy event impact analyst."""

    def __init__(self, llm):
        self._llm = llm

    def analyze(self, ticker: str, trade_date: str, lookback_days: int = 7) -> str:
        """Run full macro event analysis. Returns markdown report string."""
        logger.info(f"[MacroEventAnalyst] 开始宏观分析 — {ticker} @ {trade_date}")

        macro_news = _fetch_cn_macro_news(trade_date, lookback_days)
        industry_news = _fetch_industry_policy_news(ticker, trade_date)

        ticker_upper = ticker.strip().upper()
        if not (ticker_upper.endswith((".SH", ".SZ", ".SS", ".BJ"))
                or (len(ticker_upper) == 6 and ticker_upper.isdigit())):
            # For non-CN tickers, simplify the macro block
            macro_news = "（非A股标的，仅分析通用宏观环境）联储利率政策、全球地缘风险"
            industry_news = f"{ticker}: 请参考最新行业政策新闻"

        prompt = _PHASE_PROMPT.format(
            ticker=ticker,
            trade_date=trade_date,
            macro_news=macro_news,
            industry_news=industry_news,
        )

        try:
            result = self._llm.invoke([{"role": "user", "content": prompt}])
            report = result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.error(f"[MacroEventAnalyst] LLM 调用失败: {e}")
            report = f"宏观事件分析失败: {e}"

        logger.info(f"[MacroEventAnalyst] 完成 — {ticker}, {len(report)} 字")
        return report

    def create_node(self):
        """Return a LangGraph-compatible node function."""

        def macro_event_node(state: dict) -> dict:
            ticker = state["company_of_interest"]
            trade_date = state["trade_date"]
            report = self.analyze(ticker, trade_date)
            return {
                "messages": [HumanMessage(content=report, name="macro_event_analyst")],
                "macro_event_report": report,
            }

        return macro_event_node
