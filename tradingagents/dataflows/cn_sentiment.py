"""A-share sentiment data fetchers — analogous to reddit.py / stocktwits.py.

Pre-fetches real data from Chinese financial platforms before the LLM is
invoked, so the sentiment analyst never has to fabricate posts under prompt
pressure.

Sources (all via AKShare, no API key required):
  1. 东方财富个股新闻 — stock_news_em
  2. 东方财富人气榜 — stock_hot_rank_em (shows how 'hot' the ticker is)
  3. 东方财富股票评论数 — stock_comment_detail_zlkp_jgcyd_em

All fetchers degrade gracefully — return placeholder strings on any
exception so callers always get a usable string.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _is_cn_ticker(ticker: str) -> bool:
    t = ticker.strip().upper()
    return (
        (t.endswith(".SH") or t.endswith(".SZ") or t.endswith(".SS") or t.endswith(".BJ"))
        or (len(t) == 6 and t.isdigit())
        or (len(t) == 9 and t[:6].isdigit() and t[6] == "." and t[7:] in ("SH", "SZ", "SS", "BJ"))
    )


def _normalize_cn_code(ticker: str) -> str:
    """Strip exchange suffix: '600519.SH' → '600519'."""
    return ticker.split(".")[0].strip()


def fetch_eastmoney_news(ticker: str, limit: int = 20, timeout: float = 12.0) -> str:
    """Fetch recent news for *ticker* from 东方财富 (EastMoney).

    Returns a formatted block of news headlines + brief content.
    Degrades gracefully — returns a placeholder on any error.
    """
    try:
        import akshare as ak
        code = _normalize_cn_code(ticker) if _is_cn_ticker(ticker) else ticker

        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return f"[东方财富新闻] {ticker}: 暂无新闻数据"

        df = df.head(limit)
        lines = [f"## 东方财富个股新闻 — {ticker} (最近 {len(df)} 条)\n"]
        for _, row in df.iterrows():
            time_str = str(row.get("发布时间", ""))[:16]
            title = str(row.get("新闻标题", "")).strip()
            content = str(row.get("新闻内容", "")).strip()[:120]
            source = str(row.get("文章来源", "")).strip()
            lines.append(f"[{time_str}] 【{source}】{title}")
            if content:
                lines.append(f"  摘要: {content}…")
        return "\n".join(lines)

    except Exception as e:
        logger.warning("fetch_eastmoney_news failed for %s: %s", ticker, e)
        return f"[东方财富新闻] {ticker}: 数据获取失败 ({type(e).__name__})"


def fetch_hot_rank(ticker: str, timeout: float = 8.0) -> str:
    """Fetch EastMoney hot-rank position for *ticker*.

    The hot-rank reflects retail investor attention — a proxy for retail
    sentiment momentum.  Returns a short formatted block.
    """
    try:
        import akshare as ak
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            return f"[人气榜] {ticker}: 暂无人气榜数据"

        code = _normalize_cn_code(ticker) if _is_cn_ticker(ticker) else ticker

        # Search by stock code in the dataframe
        code_col = None
        for col in df.columns:
            if "代码" in col or "code" in col.lower():
                code_col = col
                break

        match = None
        if code_col:
            match = df[df[code_col].astype(str).str.contains(code, na=False)]

        if match is not None and not match.empty:
            row = match.iloc[0]
            rank = row.get("排名", row.get("rank", "N/A"))
            name = row.get("股票名称", row.get("name", ticker))
            change = row.get("涨跌幅", row.get("pct_chg", "N/A"))
            heat = row.get("关注度", row.get("hot", "N/A"))
            return (
                f"## 东方财富人气榜 — {ticker}\n"
                f"股票名称: {name} | 人气排名: 第 {rank} 位\n"
                f"关注度: {heat} | 当日涨跌幅: {change}%"
            )

        return f"[人气榜] {ticker}: 未上榜 (关注度较低或非A股)"

    except Exception as e:
        logger.warning("fetch_hot_rank failed for %s: %s", ticker, e)
        return f"[人气榜] {ticker}: 数据获取失败 ({type(e).__name__})"


def fetch_stock_comment(ticker: str, timeout: float = 8.0) -> str:
    """Fetch EastMoney retail-investor comment/discussion stats for *ticker*.

    Returns a block summarising discussion volume and sentiment indicators
    from the stock comment section.
    """
    try:
        import akshare as ak
        code = _normalize_cn_code(ticker) if _is_cn_ticker(ticker) else ticker

        # Try 个股讨论概况
        df = ak.stock_comment_em()
        if df is None or df.empty:
            return f"[股票评论] {ticker}: 暂无评论数据"

        code_col = None
        for col in df.columns:
            if "代码" in col or "code" in col.lower():
                code_col = col
                break

        match = None
        if code_col:
            match = df[df[code_col].astype(str).str.contains(code, na=False)]

        if match is not None and not match.empty:
            row = match.iloc[0]
            lines = [f"## 东方财富股票评论统计 — {ticker}"]
            for col in df.columns:
                val = row.get(col, "N/A")
                if val != "N/A" and str(val).strip():
                    lines.append(f"{col}: {val}")
            return "\n".join(lines)

        return f"[股票评论] {ticker}: 未找到评论统计数据"

    except Exception as e:
        logger.warning("fetch_stock_comment failed for %s: %s", ticker, e)
        return f"[股票评论] {ticker}: 数据获取失败 ({type(e).__name__})"


def fetch_cn_sentiment_bundle(ticker: str) -> tuple[str, str, str]:
    """Fetch all three CN sentiment sources and return (news, hot_rank, comment).

    Convenience wrapper — callers get three strings ready for prompt injection.
    """
    news_block = fetch_eastmoney_news(ticker, limit=20)
    hot_block = fetch_hot_rank(ticker)
    comment_block = fetch_stock_comment(ticker)
    return news_block, hot_block, comment_block


def get_cn_social_sentiment(stock_code: str) -> dict:
    """汇总雪球+股吧情绪数据，返回结构化字典。

    Parameters
    ----------
    stock_code : str
        股票代码，支持格式：'000001'、'000001.SZ'、'SZ000001'

    Returns
    -------
    dict
        {
            "stock_code": str,
            "xueqiu": {雪球情绪数据},
            "eastmoney": {东方财富股吧情绪数据},
        }

    Notes
    -----
    任一数据源失败时均优雅降级，不抛异常。
    """
    from .providers.china.xueqiu import XueqiuSentiment
    from .providers.china.eastmoney import EastMoneySentiment

    xq = XueqiuSentiment().get_sentiment(stock_code)
    em = EastMoneySentiment().get_sentiment(stock_code)
    return {"stock_code": stock_code, "xueqiu": xq, "eastmoney": em}
