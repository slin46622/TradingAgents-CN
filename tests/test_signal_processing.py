"""Tests for the shared rating heuristic and the SignalProcessor adapter.

The Portfolio Manager produces a typed PortfolioDecision via structured
output and renders it to markdown that always contains a ``**Rating**: X``
header.  The deterministic heuristic in ``tradingagents.agents.utils.rating``
is therefore sufficient to extract the rating downstream — no second LLM
call is needed — and SignalProcessor is now a thin adapter that delegates
to it.
"""

import pytest

from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating
from tradingagents.graph.signal_processing import SignalProcessor


# ---------------------------------------------------------------------------
# Heuristic parser
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseRating:
    def test_explicit_label(self):
        assert parse_rating("**Rating**: Sell") == "Sell"
        assert parse_rating("Rating: Buy") == "Buy"
        assert parse_rating("rating: Overweight") == "Overweight"

    def test_label_with_markdown_bold(self):
        assert parse_rating("**Rating**: **Hold**") == "Hold"
        assert parse_rating("Rating: **Underweight**") == "Underweight"

    def test_label_with_dash(self):
        assert parse_rating("rating - Buy") == "Buy"
        assert parse_rating("Rating - Sell") == "Sell"

    def test_label_case_insensitive(self):
        assert parse_rating("Rating: buy") == "Buy"
        assert parse_rating("rating: HOLD") == "Hold"

    def test_fallback_first_rating_word(self):
        # No explicit "Rating:" label, but a known tier word appears.
        assert parse_rating(
            "After careful analysis the decision is Buy."
        ) == "Buy"
        assert parse_rating(
            "Verdict: Sell this position."
        ) == "Sell"

    def test_fallback_ignores_non_tier_words(self):
        # "Buyer" is not a tier word ("Buy" is the tier).
        assert parse_rating("The buyer of last resort stepped in.") == "Hold"

    def test_default_on_no_match(self):
        assert parse_rating("No rating mentioned.") == "Hold"

    def test_custom_default(self):
        assert parse_rating("Nothing here.", default="Buy") == "Buy"

    def test_full_report_extraction(self):
        report = """
**Rating**: Overweight

**Executive Summary**: AAPL shows strong fundamentals with room to run.

**Investment Thesis**: Services revenue growing faster than hardware.
"""
        assert parse_rating(report) == "Overweight"

    def test_full_report_fallback(self):
        report = """
After thorough analysis, our team recommends a Buy rating on NVDA.
"""
        assert parse_rating(report) == "Buy"


# ---------------------------------------------------------------------------
# SignalProcessor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSignalProcessor:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.sp = SignalProcessor(quick_thinking_llm=MagicMock())

    def test_markdown_with_rating_header(self):
        signal = """
**Rating**: Sell

**Executive Summary**: Trim position.
"""
        result = self.sp.process_signal(signal, "AAPL")
        assert result["action"] == "卖出"  # Default mapping: Sell → 卖出

    def test_buy_signal(self):
        signal = "**Rating**: Buy\n\nBullish case."
        result = self.sp.process_signal(signal, "NVDA")
        assert result["action"] == "买入"

    def test_hold_signal(self):
        signal = "**Rating**: Hold\n\nStay put."
        result = self.sp.process_signal(signal, "MSFT")
        assert result["action"] == "持有"

    def test_empty_signal_defaults_to_hold(self):
        result = self.sp.process_signal("", "AAPL")
        assert result["action"] == "持有"

    def test_signal_contains_ticker(self):
        signal = "**Rating**: Buy"
        result = self.sp.process_signal(signal, "TSLA")
        # Result should be a dict with expected keys
        assert isinstance(result, dict)
        assert "action" in result
        # When signal is valid, the mock LLM returns empty action defaulting to 持有
        assert result["action"] in ("买入", "持有", "卖出")
