"""FinRobot-style Chain-of-Thought financial reasoning decomposer.

Inspired by FinRobot (AI4Finance-Foundation/FinRobot) which decomposes
complex financial analysis into discrete sub-questions, each solved
independently, then aggregated by a synthesis call.

This avoids the "single-context overload" problem where a single LLM call
must simultaneously handle valuation, growth, competition, and risk — areas
that are analytically orthogonal and each deserve full attention.

Architecture:
  1. decompose()  — Split the question into 4 specialist sub-questions
  2. solve_all()  — Run each sub-question as a separate LLM call
  3. synthesize() — Aggregate sub-answers into a unified conclusion

Usage (within a fundamentals analyst or research manager):
    from tradingagents.agents.utils.cot_decomposer import FinancialCoTDecomposer

    decomposer = FinancialCoTDecomposer(llm)
    report = decomposer.analyze(
        ticker="600519.SH",
        context="贵州茅台 2024Q3 财报数据: 营收 416 亿...",
        trade_date="2025-01-15",
    )
"""

from __future__ import annotations

from typing import Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("agents.utils.cot_decomposer")

# The four analytical dimensions — each is a self-contained sub-question.
_SUB_QUESTIONS = [
    {
        "id": "valuation",
        "name": "估值分析",
        "prompt_template": (
            "你是专业估值分析师。基于以下数据，仅回答这一个问题：\n\n"
            "**标的：** {ticker}（分析日期：{trade_date}）\n\n"
            "**数据：**\n{context}\n\n"
            "**问题：** 该股票当前估值是否合理？请给出：\n"
            "1. 当前估值水平（PE/PB/PS等适用指标）\n"
            "2. 与历史均值和行业均值对比\n"
            "3. 内在价值估算（DCF或同行比较法）\n"
            "4. 估值结论：高估/合理/低估（附置信度）\n\n"
            "**要求：** 严格基于数据，不要推测其他维度（增长、竞争等）。"
        ),
    },
    {
        "id": "growth",
        "name": "增长动量分析",
        "prompt_template": (
            "你是专业增长分析师。基于以下数据，仅回答这一个问题：\n\n"
            "**标的：** {ticker}（分析日期：{trade_date}）\n\n"
            "**数据：**\n{context}\n\n"
            "**问题：** 该公司增长动量如何？请给出：\n"
            "1. 近期营收/利润增速（环比、同比）\n"
            "2. 增速趋势（加速/减速/平稳）\n"
            "3. 驱动增长的核心因素（1-3点）\n"
            "4. 未来12个月增长预期\n\n"
            "**要求：** 只聚焦增长，不分析估值、竞争或风险。"
        ),
    },
    {
        "id": "competition",
        "name": "竞争格局分析",
        "prompt_template": (
            "你是专业竞争分析师。基于以下数据，仅回答这一个问题：\n\n"
            "**标的：** {ticker}（分析日期：{trade_date}）\n\n"
            "**数据：**\n{context}\n\n"
            "**问题：** 该公司的竞争地位如何？请给出：\n"
            "1. 行业市场份额及变化趋势\n"
            "2. 核心竞争优势（护城河类型）\n"
            "3. 主要竞争威胁（现有竞争者+潜在进入者）\n"
            "4. 竞争格局判断：优势/均衡/劣势\n\n"
            "**要求：** 只聚焦竞争格局，不分析估值或增长数字。"
        ),
    },
    {
        "id": "risk",
        "name": "风险评估分析",
        "prompt_template": (
            "你是专业风险分析师。基于以下数据，仅回答这一个问题：\n\n"
            "**标的：** {ticker}（分析日期：{trade_date}）\n\n"
            "**数据：**\n{context}\n\n"
            "**问题：** 该股票的主要风险有哪些？请给出：\n"
            "1. 经营风险（前2-3个）\n"
            "2. 财务风险（杠杆、流动性、汇率等）\n"
            "3. 政策/行业监管风险\n"
            "4. 综合风险等级：低/中/高（附说明）\n\n"
            "**要求：** 只分析风险，不重复估值/增长内容。"
        ),
    },
]

_SYNTHESIS_TEMPLATE = """你是首席投资分析师。以下是同一标的的四个独立维度分析结果：

**标的：** {ticker}（分析日期：{trade_date}）

---
### 维度一：估值分析
{valuation}

---
### 维度二：增长动量分析
{growth}

---
### 维度三：竞争格局分析
{competition}

---
### 维度四：风险评估
{risk}

---

请综合以上四个维度，给出**统一的投资建议报告**：

## 综合分析结论

### 综合评分（各25分，满分100分）
- 估值合理性：X/25
- 增长质量：X/25
- 竞争优势：X/25
- 风险水平：X/25（风险低得分高）
- **总分：X/100**

### 投资建议
**建议方向：** 买入 / 持有 / 减持 / 卖出

**核心逻辑（3点）：**
1.
2.
3.

### 目标价格区间
- 短期（1个月）：
- 中期（3个月）：

### 关键催化剂与风险
- 上行催化剂：
- 主要风险：
"""


class FinancialCoTDecomposer:
    """Decompose financial analysis into 4 sub-questions, solve, then synthesize."""

    def __init__(self, llm, verbose: bool = False):
        self._llm = llm
        self._verbose = verbose

    def _call_llm(self, prompt: str, label: str = "") -> str:
        """Single LLM call with graceful error handling."""
        try:
            result = self._llm.invoke([{"role": "user", "content": prompt}])
            answer = result.content if hasattr(result, "content") else str(result)
            if self._verbose:
                logger.debug(f"[CoT] {label}: {len(answer)} chars")
            return answer
        except Exception as e:
            logger.error(f"[CoT] {label} 调用失败: {e}")
            return f"[{label}分析暂时不可用: {e}]"

    def analyze(self, ticker: str, context: str, trade_date: str,
                sub_questions: Optional[list[str]] = None) -> str:
        """Run full CoT analysis and return the synthesized report.

        Args:
            ticker: Stock ticker symbol.
            context: Pre-fetched data string (fundamentals, news, price data).
            trade_date: Analysis date in YYYY-MM-DD format.
            sub_questions: Optional subset of IDs to run (default: all four).

        Returns:
            Synthesized investment analysis report string.
        """
        selected = _SUB_QUESTIONS
        if sub_questions:
            selected = [q for q in _SUB_QUESTIONS if q["id"] in sub_questions]

        logger.info(f"[CoT] {ticker} 开始分解分析 — {len(selected)} 个子问题")

        sub_answers: dict[str, str] = {}
        for q in selected:
            prompt = q["prompt_template"].format(
                ticker=ticker, trade_date=trade_date, context=context
            )
            answer = self._call_llm(prompt, label=q["name"])
            sub_answers[q["id"]] = answer
            logger.info(f"[CoT] ✓ {q['name']} 完成 ({len(answer)} 字)")

        # Ensure all keys present for synthesis template
        sub_answers.setdefault("valuation", "数据不足，跳过估值分析")
        sub_answers.setdefault("growth", "数据不足，跳过增长分析")
        sub_answers.setdefault("competition", "数据不足，跳过竞争分析")
        sub_answers.setdefault("risk", "数据不足，跳过风险分析")

        synthesis_prompt = _SYNTHESIS_TEMPLATE.format(
            ticker=ticker,
            trade_date=trade_date,
            **sub_answers,
        )
        report = self._call_llm(synthesis_prompt, label="综合分析")
        logger.info(f"[CoT] {ticker} 分析完成，综合报告 {len(report)} 字")
        return report

    def get_sub_analysis(self, ticker: str, context: str, trade_date: str,
                         question_id: str) -> str:
        """Run a single sub-question without synthesis. Useful for targeted queries."""
        for q in _SUB_QUESTIONS:
            if q["id"] == question_id:
                prompt = q["prompt_template"].format(
                    ticker=ticker, trade_date=trade_date, context=context
                )
                return self._call_llm(prompt, label=q["name"])
        return f"未知子问题 ID: {question_id}"
