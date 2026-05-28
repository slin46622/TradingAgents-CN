"""Social-media / sentiment analyst — grounded multi-source sentiment analysis.

Previously the analyst used tool-calling with only Yahoo Finance news available,
which caused LLMs to fabricate Reddit/StockTwits/股吧 content under prompt
pressure (verified live, same issue upstream fixed in #557).

Redesigned approach (mirrors upstream Grounded Sentiment Analyst, adapted for
A-share + HK + US markets):

  A股:
    1. 东方财富个股新闻  (institutional framing + retail news)
    2. 东方财富人气榜    (retail attention / hot-rank proxy)
    3. 东方财富股票评论  (discussion volume & sentiment stats)

  港股 / 美股:
    1. Yahoo Finance news (institutional framing)
    2. StockTwits messages (retail, cashtag-indexed, with Bullish/Bearish labels)
    3. Reddit posts (r/wallstreetbets, r/stocks, r/investing)

All data is pre-fetched and injected as structured blocks BEFORE the LLM is
invoked. The LLM does NOT use tools — it produces its report in a single call
from the injected data.  LLMs can still say "data limited" when blocks show
placeholder strings; they cannot fabricate real-looking posts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage

from tradingagents.utils.logging_init import get_logger
from tradingagents.agents.utils.instrument_utils import build_instrument_context

logger = get_logger("analysts.social_media")


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def _is_cn_ticker(ticker: str) -> bool:
    t = ticker.strip().upper()
    return (
        t.endswith((".SH", ".SZ", ".SS", ".BJ"))
        or (len(t) == 6 and t.isdigit())
    )


def _is_hk_ticker(ticker: str) -> bool:
    return ticker.strip().upper().endswith(".HK")


def _fetch_cn_blocks(ticker: str) -> tuple[str, str, str]:
    """Pre-fetch the three A-share sentiment sources."""
    from tradingagents.dataflows.cn_sentiment import fetch_cn_sentiment_bundle
    return fetch_cn_sentiment_bundle(ticker)


def _fetch_global_blocks(ticker: str, start_date: str, end_date: str) -> tuple[str, str, str]:
    """Pre-fetch news + StockTwits + Reddit for non-CN tickers."""
    # News via Yahoo Finance (use existing toolkit utility)
    try:
        from tradingagents.dataflows.interface import get_finnhub_news
        news_block = get_finnhub_news(ticker, end_date)
    except Exception:
        news_block = f"[News] {ticker}: data unavailable"

    # StockTwits
    try:
        from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages
        stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
    except Exception:
        stocktwits_block = f"[StockTwits] {ticker}: data unavailable"

    # Reddit
    try:
        from tradingagents.dataflows.reddit import fetch_reddit_posts
        reddit_block = fetch_reddit_posts(ticker)
    except Exception:
        reddit_block = f"[Reddit] {ticker}: data unavailable"

    return news_block, stocktwits_block, reddit_block


def _build_cn_prompt(ticker: str, company_name: str, trade_date: str,
                     news_block: str, hot_block: str, comment_block: str) -> str:
    return f"""你是专业的中国市场情绪分析师。以下是从东方财富平台**实时预取**的真实数据，请基于这些数据撰写情绪分析报告。

**分析标的：** {ticker}（{company_name}）
**分析日期：** {trade_date}

---
{news_block}

---
{hot_block}

---
{comment_block}

---

请基于以上**真实数据**，输出以下结构的中文分析报告：

## 一、情绪综合评分
- 情绪指数：X/10（1=极度悲观，10=极度乐观）
- 情绪趋势：上升/平稳/下降

## 二、新闻舆论分析
（基于东方财富新闻数据，分析近期报道的主要议题和情感倾向）

## 三、市场热度分析
（基于人气榜和评论数据，分析散户关注度变化）

## 四、关键风险信号
（从以上数据中识别的情绪风险点）

## 五、交易参考建议
- 情绪驱动的短期预期（1-5天）
- 基于情绪变化的时机建议

## 六、数据摘要表

| 指标 | 数值/状态 | 解读 |
|------|----------|------|
| 情绪指数 | | |
| 新闻条数 | | |
| 人气排名 | | |
| 情绪倾向 | | |

注意：如某项数据显示"获取失败"，请明确说明数据缺失，不要猜测或编造内容。"""


def _build_global_prompt(ticker: str, trade_date: str,
                         news_block: str, stocktwits_block: str, reddit_block: str) -> str:
    return f"""You are a professional sentiment analyst. Below is **live pre-fetched data** from multiple sources. Base your report strictly on this data — do not fabricate posts.

**Ticker:** {ticker}
**Analysis Date:** {trade_date}

---
{news_block}

---
{stocktwits_block}

---
{reddit_block}

---

Based on the above real data, write a structured sentiment report in Chinese:

## 一、Sentiment Score: X/10 (1=Extremely Bearish, 10=Extremely Bullish)

## 二、News & Institutional Framing Analysis

## 三、Retail Sentiment Analysis (StockTwits / Reddit)
- Bullish signal count vs Bearish signal count
- Key themes in retail discussion

## 四、Risk Signals from Sentiment

## 五、Short-term Outlook (1-5 days)

## 六、Summary Table

| Metric | Value | Interpretation |
|--------|-------|----------------|
| Sentiment Score | | |
| News Articles | | |
| StockTwits Bullish% | | |
| Reddit Activity | | |

Note: If a data block shows "unavailable", state that clearly — do not invent content."""


def create_social_media_analyst(llm, toolkit=None):
    """Create a grounded sentiment analyst node for the trading graph.

    Pre-fetches all sentiment data sources, injects them as structured blocks,
    and produces a report in a single LLM call.  No tool-calling loop.
    """

    def social_media_analyst_node(state):
        ticker = state["company_of_interest"]
        trade_date = state["trade_date"]
        instrument_context = build_instrument_context(ticker)

        logger.info(f"[情绪分析师] 预取数据 — {ticker} @ {trade_date}")

        # Pre-fetch all sources. Each fetcher degrades gracefully.
        if _is_cn_ticker(ticker):
            news_block, hot_block, comment_block = _fetch_cn_blocks(ticker)
            user_prompt = _build_cn_prompt(
                ticker=ticker,
                company_name=ticker,
                trade_date=trade_date,
                news_block=news_block,
                hot_block=hot_block,
                comment_block=comment_block,
            )
            logger.info(f"[情绪分析师] A股模式 — 东方财富新闻+人气榜+评论 已注入")
        else:
            start_date = _seven_days_back(trade_date)
            news_block, stocktwits_block, reddit_block = _fetch_global_blocks(
                ticker, start_date, trade_date
            )
            user_prompt = _build_global_prompt(
                ticker=ticker,
                trade_date=trade_date,
                news_block=news_block,
                stocktwits_block=stocktwits_block,
                reddit_block=reddit_block,
            )
            logger.info(f"[情绪分析师] 全球模式 — Yahoo+StockTwits+Reddit 已注入")

        system_prompt = (
            f"你是交易团队的情绪分析师。标的约束：{instrument_context}\n"
            "请基于提供的真实数据撰写分析。如数据不足，明确说明，不要编造。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = llm.invoke(messages)
            report = result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.error(f"[情绪分析师] LLM 调用失败: {e}")
            report = f"情绪分析失败: {e}"

        logger.info(f"[情绪分析师] 完成，报告长度: {len(report)} 字")

        return {
            "messages": [HumanMessage(content=report, name="social_media_analyst")],
            "sentiment_report": report,
            "sentiment_tool_call_count": 0,
        }

    return social_media_analyst_node
