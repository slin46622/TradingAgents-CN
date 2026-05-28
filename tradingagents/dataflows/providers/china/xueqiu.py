"""雪球情绪数据提供器

通过 AKShare 的雪球热度接口获取 A 股讨论量与关注人数，
推算看多/看空比例。

数据来源：
  - stock_hot_tweet_xq  — 雪球讨论排行榜（帖子数）
  - stock_hot_follow_xq — 雪球关注排行榜（关注人数）

注意：AKShare 雪球接口返回的是 **排行榜**，无直接看多/看空字段。
本模块以"讨论量"作为总讨论量，以"关注人数"估算关注度，
并通过 stock_comment_em 的综合得分（0-100）折算 bullish_ratio。
所有请求失败时返回带默认值的字典，不抛异常。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 股票代码标准化映射
_EXCHANGE_MAP = {
    "6": "SH",  # 600xxx / 601xxx / 603xxx / 605xxx
    "0": "SZ",  # 000xxx / 002xxx / 003xxx
    "3": "SZ",  # 300xxx / 301xxx
    "4": "BJ",  # 430xxx / 920xxx
    "8": "BJ",  # 830xxx / 870xxx
    "9": "BJ",  # 920xxx
}


def _to_xq_code(stock_code: str) -> str:
    """将纯数字代码转换为雪球格式，如 '000001' → 'SZ000001'。"""
    code = stock_code.strip().upper()
    # 已带交易所前缀（SH/SZ/BJ）
    if code[:2] in ("SH", "SZ", "BJ"):
        return code
    # 带后缀格式（600519.SH）
    if "." in code:
        num, exch = code.split(".", 1)
        exch = exch.replace("SS", "SH")
        return f"{exch}{num}"
    # 纯6位数字
    if len(code) == 6 and code.isdigit():
        prefix = _EXCHANGE_MAP.get(code[0], "SZ")
        return f"{prefix}{code}"
    return code


def _normalize_cn_code(stock_code: str) -> str:
    """去除交易所后缀，返回纯6位代码。"""
    code = stock_code.strip()
    if "." in code:
        return code.split(".")[0].strip()
    # 去除 SH/SZ/BJ 前缀
    upper = code.upper()
    for prefix in ("SH", "SZ", "BJ"):
        if upper.startswith(prefix):
            return code[2:]
    return code


def _default_result(stock_code: str, reason: str = "数据不可用") -> dict:
    return {
        "stock_code": stock_code,
        "bullish_count": 0,
        "bearish_count": 0,
        "bullish_ratio": 0.5,
        "total_discussion": 0,
        "follow_count": 0,
        "score": None,
        "timestamp": datetime.now().isoformat(),
        "note": reason,
    }


class XueqiuSentiment:
    """雪球情绪数据提供器。

    优先使用 AKShare 接口，失败时优雅降级返回默认值。
    """

    def get_sentiment(self, stock_code: str) -> dict:
        """获取雪球情绪数据。

        Parameters
        ----------
        stock_code : str
            股票代码，支持格式：'000001'、'000001.SZ'、'SZ000001'

        Returns
        -------
        dict
            {
                "stock_code": str,
                "bullish_count": int,       # 估算看多人数
                "bearish_count": int,       # 估算看空人数
                "bullish_ratio": float,     # 看多比例 0-1
                "total_discussion": int,    # 讨论帖数
                "follow_count": int,        # 关注人数
                "score": float | None,      # 东方财富综合评分（辅助）
                "timestamp": str,
                "note": str,
            }
        """
        try:
            return self._fetch(stock_code)
        except Exception as exc:
            logger.warning("XueqiuSentiment.get_sentiment failed for %s: %s", stock_code, exc)
            return _default_result(stock_code, f"获取失败: {type(exc).__name__}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak

        xq_code = _to_xq_code(stock_code)
        pure_code = _normalize_cn_code(stock_code)

        # 1. 讨论排行榜
        tweet_count = 0
        try:
            df_tweet = ak.stock_hot_tweet_xq(symbol="最热门")
            if df_tweet is not None and not df_tweet.empty:
                match = df_tweet[df_tweet["股票代码"].astype(str) == xq_code]
                if not match.empty:
                    tweet_count = int(match.iloc[0].get("关注", 0) or 0)
        except Exception as exc:
            logger.debug("stock_hot_tweet_xq error: %s", exc)

        # 2. 关注排行榜
        follow_count = 0
        try:
            df_follow = ak.stock_hot_follow_xq(symbol="最热门")
            if df_follow is not None and not df_follow.empty:
                match = df_follow[df_follow["股票代码"].astype(str) == xq_code]
                if not match.empty:
                    follow_count = int(match.iloc[0].get("关注", 0) or 0)
        except Exception as exc:
            logger.debug("stock_hot_follow_xq error: %s", exc)

        # 3. 东方财富综合评分（0-100），辅助估算看多/看空比例
        score: Optional[float] = None
        try:
            df_comment = ak.stock_comment_em()
            if df_comment is not None and not df_comment.empty:
                code_col = next(
                    (c for c in df_comment.columns if "代码" in c or c.lower() == "code"),
                    None,
                )
                if code_col:
                    match = df_comment[df_comment[code_col].astype(str).str.strip() == pure_code]
                    if not match.empty:
                        raw_score = match.iloc[0].get("综合得分")
                        if raw_score is not None:
                            score = float(raw_score)
        except Exception as exc:
            logger.debug("stock_comment_em error: %s", exc)

        # 4. 基于综合得分折算 bullish_ratio
        #    综合得分：0-100，>60 偏多，<40 偏空，中间区域中性
        if score is not None:
            # 线性映射：score=0 → ratio=0.1，score=50 → ratio=0.5，score=100 → ratio=0.9
            bullish_ratio = max(0.05, min(0.95, (score / 100.0) * 0.8 + 0.1))
        elif tweet_count > 0:
            # 无评分时假设中性偏多（A股散户整体略偏多）
            bullish_ratio = 0.55
        else:
            bullish_ratio = 0.5

        total_discussion = tweet_count
        bearish_ratio = 1.0 - bullish_ratio
        bullish_count = int(total_discussion * bullish_ratio)
        bearish_count = int(total_discussion * bearish_ratio)

        note = "数据来自雪球热度排行榜"
        if tweet_count == 0 and follow_count == 0:
            note = "该股票未进入雪球热度排行榜（可能为小盘股）"
        if score is not None:
            note += f"；东方财富综合评分={score:.1f}"

        return {
            "stock_code": stock_code,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "bullish_ratio": round(bullish_ratio, 4),
            "total_discussion": total_discussion,
            "follow_count": follow_count,
            "score": score,
            "timestamp": datetime.now().isoformat(),
            "note": note,
        }
