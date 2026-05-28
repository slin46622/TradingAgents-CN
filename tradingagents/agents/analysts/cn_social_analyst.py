"""A股社交情绪分析师 — 雪球 + 东方财富股吧双数据源。

本分析师专用于6位数字代码的A股标的。非A股标的直接返回空报告，不占用
LLM 调用资源。

数据流程：
  1. 调用 get_cn_social_sentiment() 预取雪球/股吧两路数据
  2. 构造结构化 prompt，一次性注入到 LLM
  3. LLM 不使用工具调用，输出中文情绪分析报告
  4. 写入 state["cn_social_report"]

与 social_media_analyst 的区别：
  - social_media_analyst：东方财富新闻 + 人气榜 + 评论统计（综合情绪）
  - cn_social_analyst：雪球看多看空比例 + 股吧情绪分类（散户情绪专注分析）
"""

from __future__ import annotations

import re
import logging

from langchain_core.messages import HumanMessage

from tradingagents.utils.logging_init import get_logger

logger = get_logger("analysts.cn_social")

_CN_TICKER_RE = re.compile(r"^\d{6}$")


def _is_cn_ticker(ticker: str) -> bool:
    """纯6位数字代码视为A股。"""
    return bool(_CN_TICKER_RE.match(ticker.strip()))


def _normalize_ticker(ticker: str) -> str:
    """去除交易所后缀，返回纯6位代码。"""
    code = ticker.strip()
    if "." in code:
        return code.split(".")[0].strip()
    upper = code.upper()
    for prefix in ("SH", "SZ", "BJ"):
        if upper.startswith(prefix):
            return code[2:]
    return code


def _build_prompt(ticker: str, date: str, xq: dict, em: dict) -> str:
    """构建情绪分析 prompt，已填充真实数据。"""
    bullish_ratio = xq.get("bullish_ratio", 0.5)
    bearish_ratio = 1.0 - bullish_ratio
    total_discussion = xq.get("total_discussion", 0)
    follow_count = xq.get("follow_count", 0)
    xq_score = xq.get("score")
    xq_note = xq.get("note", "数据来自雪球热度排行榜")

    positive_ratio = em.get("positive_ratio", 0.33)
    negative_ratio = em.get("negative_ratio", 0.33)
    participation_desire = em.get("participation_desire")
    composite_score = em.get("composite_score")
    attention_index = em.get("attention_index")
    em_note = em.get("note", "东方财富千股千评")

    xq_score_str = f"{xq_score:.1f}" if xq_score is not None else "无数据"
    participation_str = f"{participation_desire:.1f}%" if participation_desire is not None else "无数据"
    composite_str = f"{composite_score:.1f}" if composite_score is not None else "无数据"
    attention_str = f"{attention_index:.1f}" if attention_index is not None else "无数据"

    return f"""你是A股社交媒体情绪分析师，专注分析中国散户投资者情绪。

分析标的：{ticker}（{date}）

【雪球平台数据】
- 看多/看空比例：{bullish_ratio:.1%} / {bearish_ratio:.1%}
- 总讨论帖数：{total_discussion}
- 关注人数：{follow_count}
- 东方财富综合评分（辅助）：{xq_score_str}
- 数据说明：{xq_note}

【东方财富股吧数据】
- 正面/负面/中性情绪：{positive_ratio:.1%} / {negative_ratio:.1%} / {1.0 - positive_ratio - negative_ratio:.1%}
- 市场参与意愿：{participation_str}
- 综合评分（千股千评）：{composite_str}
- 关注指数：{attention_str}
- 数据说明：{em_note}

请基于以上真实数据分析：
1. 散户情绪整体偏多还是偏空？
2. 当前情绪是否存在极端化（过度乐观/悲观）？
3. 情绪信号对短期股价的含义（散户极度看好时往往是顶部信号）
4. 综合情绪建议

注意：如数据显示"无数据"或"未进入排行榜"，请明确说明，不要编造内容。
用中文回答，控制在200字以内。"""


def create_cn_social_analyst(llm, toolkit=None):
    """创建A股社交情绪分析师节点。

    Parameters
    ----------
    llm :
        LangChain LLM 实例（无需工具绑定）
    toolkit :
        可选，保持接口与其他分析师一致，本分析师不使用工具包

    Returns
    -------
    callable
        LangGraph 节点函数，接收 state，返回状态更新字典
    """

    def cn_social_analyst_node(state: dict) -> dict:
        ticker_raw = state.get("company_of_interest", "")
        trade_date = state.get("trade_date", "")

        # 标准化：去掉交易所后缀后检测
        ticker = _normalize_ticker(ticker_raw)

        if not _is_cn_ticker(ticker):
            msg = f"{ticker_raw} 为非A股标的，跳过中文社交情绪分析"
            logger.info(f"[CN社交情绪] {msg}")
            return {
                "messages": [HumanMessage(content=msg, name="cn_social_analyst")],
                "cn_social_report": msg,
                "cn_social_tool_call_count": 0,
            }

        logger.info(f"[CN社交情绪] 预取雪球+股吧数据 — {ticker} @ {trade_date}")

        try:
            from tradingagents.dataflows.cn_sentiment import get_cn_social_sentiment
            sentiment_data = get_cn_social_sentiment(ticker)
        except Exception as exc:
            logger.error(f"[CN社交情绪] get_cn_social_sentiment 调用失败: {exc}")
            sentiment_data = {"xueqiu": {}, "eastmoney": {}}

        xq = sentiment_data.get("xueqiu", {})
        em = sentiment_data.get("eastmoney", {})

        logger.info(
            f"[CN社交情绪] 雪球数据: 看多={xq.get('bullish_ratio', 0.5):.1%}, "
            f"讨论数={xq.get('total_discussion', 0)}"
        )
        logger.info(
            f"[CN社交情绪] 股吧数据: 正面={em.get('positive_ratio', 0.33):.1%}, "
            f"综合评分={em.get('composite_score')}"
        )

        user_prompt = _build_prompt(ticker, trade_date, xq, em)
        system_prompt = (
            "你是交易团队的A股社交情绪分析师，负责从散户情绪视角评估标的。"
            "请基于提供的真实数据撰写分析。如数据不足，明确说明，不要编造。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = llm.invoke(messages)
            report = result.content if hasattr(result, "content") else str(result)
        except Exception as exc:
            logger.error(f"[CN社交情绪] LLM 调用失败: {exc}")
            report = f"A股社交情绪分析失败: {exc}"

        logger.info(f"[CN社交情绪] 完成，报告长度: {len(report)} 字")

        return {
            "messages": [HumanMessage(content=report, name="cn_social_analyst")],
            "cn_social_report": report,
            "cn_social_tool_call_count": 0,
        }

    return cn_social_analyst_node
