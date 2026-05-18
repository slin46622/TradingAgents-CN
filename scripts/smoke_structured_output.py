"""结构化输出端到端冒烟测试（适配 CN fork 版）。

直接运行三个决策 agent（Research Manager、Trader、Portfolio Manager）
的 structured output 绑定，打印结构化 Pydantic 实例 + 渲染后的 markdown。

用法：
    python scripts/smoke_structured_output.py local
    OPENAI_API_KEY=*** python scripts/smoke_structured_output.py openai

这个脚本不调用 propagate()，只测试结构化输出调用链。
"""

from __future__ import annotations

import argparse
import os
import sys
from unittest.mock import MagicMock

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.trader.trader import create_trader
from tradingagents.agents.utils.memory import FinancialSituationMemory


# Minimal mock state for the three agents
DEBATE_HISTORY = """
Bull Analyst: NVDA's data-center revenue grew 60% YoY last quarter, driven by
Blackwell ramp; sovereign AI deals with multiple governments add a $40B+
multi-year tailwind. Margins remain above peer average.

Bear Analyst: Concentration risk is real — top three customers are >40% of
revenue. Any pause in hyperscaler capex would compress the multiple. China
export restrictions still cap a meaningful portion of demand.
"""


def _make_rm_state():
    return {
        "company_of_interest": "NVDA",
        "investment_debate_state": {
            "history": DEBATE_HISTORY,
            "bull_history": "Bull Analyst: NVDA's data-center revenue grew 60% YoY...",
            "bear_history": "Bear Analyst: Concentration risk is real...",
            "current_response": "",
            "judge_decision": "",
            "count": 1,
        },
        "market_report": "Market: NVDA up 2.3% on strong volume.",
        "sentiment_report": "Sentiment: Mixed, retail bullish.",
        "news_report": "News: Blackwell ramp on track.",
        "fundamentals_report": "Fundamentals: PE 45x, revenue growth 60% YoY.",
    }


def _make_trader_state(investment_plan: str):
    return {
        "company_of_interest": "NVDA",
        "investment_plan": investment_plan,
        "market_report": "Market: NVDA up 2.3% on strong volume.",
        "sentiment_report": "Sentiment: Mixed, retail bullish.",
        "news_report": "News: Blackwell ramp on track.",
        "fundamentals_report": "Fundamentals: PE 45x, revenue growth 60% YoY.",
    }


def _make_pm_state(investment_plan: str, trader_plan: str):
    return {
        "company_of_interest": "NVDA",
        "past_context": "",
        "risk_debate_state": {
            "history": "Aggressive: lean in. Conservative: trim. Neutral: balanced sizing.",
            "aggressive_history": "Aggressive: NVDA has strong momentum...",
            "conservative_history": "Conservative: PE multiple is stretched...",
            "neutral_history": "Neutral: fundamentals solid, but pricey...",
            "judge_decision": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
        "market_report": "Market: NVDA up 2.3% on strong volume.",
        "sentiment_report": "Sentiment: Mixed, retail bullish.",
        "news_report": "News: Blackwell ramp on track.",
        "fundamentals_report": "Fundamentals: PE 45x, revenue growth 60% YoY.",
        "investment_plan": investment_plan,
        "trader_investment_plan": trader_plan,
    }


def _make_memory():
    """Create a dummy memory object (may be None — agents handle that gracefully)."""
    return None


def _print_section(title: str, content: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{title}\n{bar}\n{content}")


def build_local_llm(base_url="http://172.27.208.1:11434/v1", model="Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf"):
    """Build a LangChain ChatOpenAI pointed at a local llama-server instance.
    
    Disables thinking/reasoning mode which some Qwen GGUFs enable by default,
    causing the model to output reasoning_content instead of content.
    """
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key="sk-no-key-needed",
        temperature=0.3,
        max_tokens=2048,
        model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="local", help="Provider name (local, openai, etc.)")
    parser.add_argument("--model", default=None, help="Model name override")
    parser.add_argument("--base-url", default=None, help="Base URL override")
    args = parser.parse_args()

    # Build LLM
    if args.provider == "local":
        llm = build_local_llm(
            base_url=args.base_url or "http://172.27.208.1:11434/v1",
            model=args.model or "Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf",
        )
    else:
        from tradingagents.llm_clients import create_llm_client
        client = create_llm_client(provider=args.provider, model=args.model)
        llm = client.get_llm()

    print(f"Provider: {args.provider}")
    print(f"Model: {llm.model_name if hasattr(llm, 'model_name') else '?'}")

    memory = _make_memory()

    # 1) Research Manager
    print("\n--- [1] Research Manager ---")
    rm = create_research_manager(llm, memory)
    rm_result = rm(_make_rm_state())
    investment_plan = rm_result["investment_plan"]
    _print_section("investment_plan", investment_plan)

    # 2) Trader (consumes RM's plan)
    print("\n--- [2] Trader ---")
    trader = create_trader(llm, memory)
    trader_result = trader(_make_trader_state(investment_plan), name="Trader")
    trader_plan = trader_result["trader_investment_plan"]
    _print_section("trader_investment_plan", trader_plan)

    # 3) Portfolio Manager (consumes both)
    print("\n--- [3] Portfolio Manager ---")
    pm = create_portfolio_manager(llm)
    pm_result = pm(_make_pm_state(investment_plan, trader_plan))
    final_decision = pm_result["final_trade_decision"]
    _print_section("final_trade_decision", final_decision)

    # 4) Structure checks
    checks = [
        ("Research Manager",  investment_plan, ["**Recommendation**:", "**Rationale**:", "**Strategic Actions**:"]),
        ("Trader",            trader_plan,     ["**Action**:", "**Reasoning**:", "FINAL TRANSACTION PROPOSAL:"]),
        ("Portfolio Manager", final_decision,  ["**Rating**:", "**Executive Summary**:", "**Investment Thesis**:"]),
    ]
    print("\n" + "=" * 70 + "\nStructure checks\n" + "=" * 70)
    failures = 0
    for name, text, required in checks:
        for marker in required:
            ok = marker in text
            print(f"  {'PASS' if ok else 'FAIL'}  {name}: contains {marker!r}")
            failures += int(not ok)

    print()
    if failures:
        print(f"Smoke FAILED: {failures} structure check(s) missing.")
        return 1
    print("Smoke PASSED: structured output → rendered markdown chain works!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
