"""东方财富股吧情绪数据提供器

通过 AKShare 接口获取东方财富千股千评数据，包括：
  - stock_comment_em       — 综合得分、关注指数（全市场）
  - stock_news_em          — 个股新闻（帖子标题情绪分类）

情绪分类规则（基于新闻标题关键词）：
  positive: 涨、买入、看好、利好、上涨、增持、推荐、突破、强势
  negative: 跌、卖出、看空、利空、下跌、减持、风险、跌停、亏损
  neutral:  其他

所有请求失败时返回带默认值的字典，不抛异常。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 情绪关键词
_POSITIVE_KEYWORDS = re.compile(
    r"涨|买入|看好|利好|上涨|增持|推荐|突破|强势|走强|反弹|拉升|大涨|飙升|创新高"
)
_NEGATIVE_KEYWORDS = re.compile(
    r"跌|卖出|看空|利空|下跌|减持|风险|跌停|亏损|走弱|回调|下行|大跌|暴跌|创新低"
)


def _normalize_cn_code(stock_code: str) -> str:
    """去除交易所后缀，返回纯6位代码。"""
    code = stock_code.strip()
    if "." in code:
        return code.split(".")[0].strip()
    upper = code.upper()
    for prefix in ("SH", "SZ", "BJ"):
        if upper.startswith(prefix):
            return code[2:]
    return code


def _classify_title(title: str) -> str:
    """将帖子标题分类为 positive / negative / neutral。"""
    if _POSITIVE_KEYWORDS.search(title):
        return "positive"
    if _NEGATIVE_KEYWORDS.search(title):
        return "negative"
    return "neutral"


def _default_result(stock_code: str, reason: str = "数据不可用") -> dict:
    return {
        "stock_code": stock_code,
        "post_count_today": 0,
        "positive_ratio": 0.33,
        "negative_ratio": 0.33,
        "neutral_ratio": 0.34,
        "attention_index": None,
        "composite_score": None,
        "participation_desire": None,
        "timestamp": datetime.now().isoformat(),
        "note": reason,
    }


class EastMoneySentiment:
    """东方财富股吧情绪数据提供器。

    优先使用 AKShare 接口，失败时优雅降级返回默认值。
    """

    def get_sentiment(self, stock_code: str) -> dict:
        """获取东方财富股吧情绪数据。

        Parameters
        ----------
        stock_code : str
            股票代码，支持格式：'000001'、'000001.SZ'、'SZ000001'

        Returns
        -------
        dict
            {
                "stock_code": str,
                "post_count_today": int,       # 今日新闻/帖子数量（近20条）
                "positive_ratio": float,       # 正面帖子比例 0-1
                "negative_ratio": float,       # 负面帖子比例 0-1
                "neutral_ratio": float,        # 中性帖子比例 0-1
                "attention_index": float|None, # 关注指数（千股千评）
                "composite_score": float|None, # 综合得分（千股千评）
                "participation_desire": float|None,  # 最新市场参与意愿
                "timestamp": str,
                "note": str,
            }
        """
        try:
            return self._fetch(stock_code)
        except Exception as exc:
            logger.warning("EastMoneySentiment.get_sentiment failed for %s: %s", stock_code, exc)
            return _default_result(stock_code, f"获取失败: {type(exc).__name__}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self, stock_code: str) -> dict:
        import akshare as ak

        pure_code = _normalize_cn_code(stock_code)

        # 1. 千股千评综合得分 + 关注指数
        attention_index: Optional[float] = None
        composite_score: Optional[float] = None
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
                        raw_attention = match.iloc[0].get("关注指数")
                        if raw_score is not None:
                            composite_score = float(raw_score)
                        if raw_attention is not None:
                            attention_index = float(raw_attention)
        except Exception as exc:
            logger.debug("stock_comment_em error for %s: %s", stock_code, exc)

        # 2. 市场参与意愿（个股）
        participation_desire: Optional[float] = None
        try:
            df_desire = ak.stock_comment_detail_scrd_desire_em(symbol=pure_code)
            if df_desire is not None and not df_desire.empty:
                latest = df_desire.iloc[-1]
                val = latest.get("参与意愿")
                if val is not None:
                    participation_desire = float(val)
        except Exception as exc:
            logger.debug("stock_comment_detail_scrd_desire_em error for %s: %s", stock_code, exc)

        # 3. 个股新闻情绪分类
        post_count = 0
        positive = 0
        negative = 0
        neutral = 0
        try:
            df_news = ak.stock_news_em(symbol=pure_code)
            if df_news is not None and not df_news.empty:
                df_news = df_news.head(20)
                post_count = len(df_news)
                title_col = next(
                    (c for c in df_news.columns if "标题" in c or "title" in c.lower()),
                    None,
                )
                if title_col:
                    for title in df_news[title_col].astype(str):
                        cat = _classify_title(title)
                        if cat == "positive":
                            positive += 1
                        elif cat == "negative":
                            negative += 1
                        else:
                            neutral += 1
        except Exception as exc:
            logger.debug("stock_news_em error for %s: %s", stock_code, exc)

        # 4. 计算情绪比例
        if post_count > 0:
            positive_ratio = round(positive / post_count, 4)
            negative_ratio = round(negative / post_count, 4)
            neutral_ratio = round(neutral / post_count, 4)
        elif composite_score is not None:
            # 无新闻时用综合得分折算
            pos = max(0.05, min(0.90, composite_score / 100.0))
            neg = max(0.05, min(0.90, 1.0 - pos))
            positive_ratio = round(pos * 0.8, 4)
            negative_ratio = round(neg * 0.8, 4)
            neutral_ratio = round(1.0 - positive_ratio - negative_ratio, 4)
        else:
            positive_ratio = 0.33
            negative_ratio = 0.33
            neutral_ratio = 0.34

        # 构建 note
        notes = []
        if composite_score is not None:
            notes.append(f"综合得分={composite_score:.1f}")
        if attention_index is not None:
            notes.append(f"关注指数={attention_index:.1f}")
        if participation_desire is not None:
            notes.append(f"市场参与意愿={participation_desire:.1f}%")
        if post_count == 0:
            notes.append("近期无新闻数据")
        note = "东方财富千股千评" + ("；" + "；".join(notes) if notes else "")

        return {
            "stock_code": stock_code,
            "post_count_today": post_count,
            "positive_ratio": positive_ratio,
            "negative_ratio": negative_ratio,
            "neutral_ratio": neutral_ratio,
            "attention_index": attention_index,
            "composite_score": composite_score,
            "participation_desire": participation_desire,
            "timestamp": datetime.now().isoformat(),
            "note": note,
        }
