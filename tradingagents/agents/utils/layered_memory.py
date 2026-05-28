"""FinMem-style layered memory architecture for the Portfolio Manager.

Inspired by FinMem (pipiku915/FinMem-LLM-StockTrading, IJCAI2024), this
module implements a three-tier memory system that mirrors how a professional
trader builds experiential knowledge:

  Short-term  (TTL: 3 days)   — Today's news, current price momentum.
                                 Stored in-process + JSON cache.
  Medium-term (TTL: 28 days)  — Trend analysis, sector rotation, pattern
                                 summaries from the last 4 weeks.
                                 Stored in per-ticker JSON files.
  Long-term   (permanent)     — Resolved trade decisions with realised
                                 returns and LLM-generated reflection.
                                 Builds on TradingMemoryLog (SQLite).

Usage:
    from tradingagents.agents.utils.layered_memory import LayeredMemory

    mem = LayeredMemory(config)

    # Store items at each layer
    mem.add_short(ticker, "今日急跌3%，量能放大", trade_date)
    mem.add_medium(ticker, "连续三周横盘整理后突破颈线", trade_date)
    mem.record_decision(ticker, trade_date, "买入")

    # Retrieve context for Portfolio Manager
    context = mem.get_context(ticker, n_short=5, n_medium=3, n_long=3)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("agents.utils.layered_memory")


# ---------------------------------------------------------------------------
# Direction extraction helpers
# ---------------------------------------------------------------------------

_BUY_KW = {
    "买入", "看涨", "做多", "推荐买入", "增持", "超配", "强力买入",
    "bullish", "buy", "strong buy", "overweight", "outperform", "上涨", "positive",
}
_SELL_KW = {
    "卖出", "看跌", "做空", "减持", "回避", "卖出建议", "强力卖出",
    "bearish", "sell", "strong sell", "underweight", "underperform", "下跌", "negative",
}


def extract_direction(text: str) -> str:
    """Infer analyst directional bias from report text.

    Returns 'buy', 'sell', or 'hold'.
    """
    t = text.lower()
    buy_count = sum(1 for kw in _BUY_KW if kw in t)
    sell_count = sum(1 for kw in _SELL_KW if kw in t)
    if buy_count > sell_count:
        return "buy"
    if sell_count > buy_count:
        return "sell"
    return "hold"

# Default storage root — overridable via env var
_DEFAULT_ROOT = Path.home() / ".tradingagents" / "layered_memory"
_MEMORY_ROOT = Path(os.getenv("TRADINGAGENTS_MEMORY_ROOT", str(_DEFAULT_ROOT)))


class ShortTermMemory:
    """In-process + JSON-backed cache with per-ticker TTL.

    Entries older than *ttl_days* are automatically pruned on read.
    Thread-safe for concurrent agent runs.
    """

    def __init__(self, root: Path, ttl_days: int = 3):
        self._root = root / "short_term"
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(days=ttl_days)
        self._lock = threading.Lock()

    def _path(self, ticker: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "", ticker)[:32]
        return self._root / f"{safe}.json"

    def add(self, ticker: str, content: str, trade_date: str) -> None:
        with self._lock:
            path = self._path(ticker)
            entries = self._load(path)
            entries.append({"date": trade_date, "content": content})
            self._save(path, entries)

    def get_recent(self, ticker: str, n: int = 5) -> list[dict]:
        with self._lock:
            path = self._path(ticker)
            entries = self._load(path)
            cutoff = (datetime.now() - self._ttl).strftime("%Y-%m-%d")
            fresh = [e for e in entries if e.get("date", "") >= cutoff]
            # prune stale entries back to disk
            if len(fresh) != len(entries):
                self._save(path, fresh)
            return fresh[-n:]

    def _load(self, path: Path) -> list[dict]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save(self, path: Path, entries: list[dict]) -> None:
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


class MediumTermMemory:
    """Rolling 4-week JSON store — one file per ticker.

    Entries are aggregated summaries (written by LLM or by structured
    analysis) retained for up to *ttl_days* days.
    """

    def __init__(self, root: Path, ttl_days: int = 28):
        self._root = root / "medium_term"
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(days=ttl_days)
        self._lock = threading.Lock()

    def _path(self, ticker: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "", ticker)[:32]
        return self._root / f"{safe}.json"

    def add(self, ticker: str, summary: str, trade_date: str, category: str = "trend") -> None:
        """Add a medium-term summary entry.

        *category* can be 'trend', 'sector', 'pattern', 'earnings', etc.
        """
        with self._lock:
            path = self._path(ticker)
            entries = self._load(path)
            entries.append({"date": trade_date, "category": category, "summary": summary})
            self._save(path, entries)

    def get_recent(self, ticker: str, n: int = 5) -> list[dict]:
        with self._lock:
            path = self._path(ticker)
            entries = self._load(path)
            cutoff = (datetime.now() - self._ttl).strftime("%Y-%m-%d")
            fresh = [e for e in entries if e.get("date", "") >= cutoff]
            if len(fresh) != len(entries):
                self._save(path, fresh)
            return fresh[-n:]

    def _load(self, path: Path) -> list[dict]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save(self, path: Path, entries: list[dict]) -> None:
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


class LongTermMemory:
    """Permanent SQLite store of resolved trade decisions with reflection.

    Schema mirrors TradingMemoryLog but adds:
      - layer = 'long_term'
      - reflection: LLM-generated post-mortem after outcome is known
      - outcome_date: when the return was realised

    Can be fed by TradingMemoryLog entries or by direct calls.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS long_term (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker      TEXT    NOT NULL,
        trade_date  TEXT    NOT NULL,
        decision    TEXT    NOT NULL,
        reasoning   TEXT,
        return_pct  REAL,
        outcome_date TEXT,
        reflection  TEXT,
        created_at  TEXT    NOT NULL
    );
    CREATE TABLE IF NOT EXISTS analyst_signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker       TEXT    NOT NULL,
        trade_date   TEXT    NOT NULL,
        analyst_name TEXT    NOT NULL,
        direction    TEXT    NOT NULL,
        was_correct  INTEGER,
        outcome_date TEXT,
        created_at   TEXT    NOT NULL
    )
    """

    def __init__(self, root: Path):
        self._db = root / "long_term.db"
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            for stmt in self._DDL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.commit()

    def record_decision(self, ticker: str, trade_date: str,
                        decision: str, reasoning: str = "") -> None:
        with self._lock:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO long_term (ticker, trade_date, decision, reasoning, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ticker, trade_date, decision, reasoning,
                     datetime.now().isoformat()),
                )
                conn.commit()

    def update_outcome(self, ticker: str, trade_date: str,
                       return_pct: float, reflection: str, outcome_date: str = "") -> None:
        outcome_date = outcome_date or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "UPDATE long_term SET return_pct=?, reflection=?, outcome_date=? "
                    "WHERE ticker=? AND trade_date=?",
                    (return_pct, reflection, outcome_date, ticker, trade_date),
                )
                conn.commit()

    def get_context(self, ticker: str, n_same: int = 5, n_cross: int = 2) -> list[dict]:
        """Return up to *n_same* same-ticker + *n_cross* cross-ticker entries."""
        with self._lock:
            with sqlite3.connect(self._db) as conn:
                conn.row_factory = sqlite3.Row
                same = conn.execute(
                    "SELECT * FROM long_term WHERE ticker=? AND reflection IS NOT NULL "
                    "ORDER BY trade_date DESC LIMIT ?",
                    (ticker, n_same),
                ).fetchall()
                cross = conn.execute(
                    "SELECT * FROM long_term WHERE ticker!=? AND reflection IS NOT NULL "
                    "ORDER BY trade_date DESC LIMIT ?",
                    (ticker, n_cross),
                ).fetchall()
            return [dict(r) for r in same] + [dict(r) for r in cross]

    def record_analyst_signals(self, ticker: str, trade_date: str,
                                signals: dict) -> None:
        """Store per-analyst directional signals for a given trade date.

        *signals* maps analyst_name → direction ('buy'|'sell'|'hold').
        """
        now = datetime.now().isoformat()
        with self._lock:
            with sqlite3.connect(self._db) as conn:
                for analyst, direction in signals.items():
                    conn.execute(
                        "INSERT INTO analyst_signals "
                        "(ticker, trade_date, analyst_name, direction, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (ticker, trade_date, analyst, direction, now),
                    )
                conn.commit()

    def update_analyst_outcomes(self, ticker: str, actual_direction: str,
                                outcome_date: str = "") -> None:
        """Mark all pending signals for *ticker* as correct or incorrect.

        Called when a position is closed so the return direction is known.
        *actual_direction*: 'buy' if price went up (profit), 'sell' if down (loss).
        """
        outcome_date = outcome_date or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute(
                    "SELECT id, direction FROM analyst_signals "
                    "WHERE ticker=? AND was_correct IS NULL",
                    (ticker,),
                ).fetchall()
                for row_id, direction in rows:
                    correct = 1 if direction == actual_direction else 0
                    conn.execute(
                        "UPDATE analyst_signals SET was_correct=?, outcome_date=? WHERE id=?",
                        (correct, outcome_date, row_id),
                    )
                conn.commit()

    def get_analyst_accuracy(self, window: int = 20) -> dict:
        """Return accuracy stats per analyst over the last *window* resolved signals.

        Returns dict mapping analyst_name → {'correct': int, 'total': int, 'accuracy': float}.
        """
        with self._lock:
            with sqlite3.connect(self._db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT analyst_name, was_correct FROM analyst_signals "
                    "WHERE was_correct IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT ?",
                    (window * 10,),
                ).fetchall()

        stats: dict = {}
        # count most recent *window* per analyst
        per_analyst: dict = {}
        for row in rows:
            name = row["analyst_name"]
            per_analyst.setdefault(name, [])
            if len(per_analyst[name]) < window:
                per_analyst[name].append(row["was_correct"])

        for name, results in per_analyst.items():
            total = len(results)
            correct = sum(results)
            stats[name] = {
                "correct": correct,
                "total": total,
                "accuracy": correct / total if total > 0 else 0.5,
            }
        return stats


class LayeredMemory:
    """Unified three-tier memory for the Portfolio Manager.

    Instantiate once per TradingAgentGraph run and pass to Portfolio Manager.

    Example integration in portfolio_manager.py:
        context = mem.get_context(ticker, n_short=5, n_medium=3, n_long=5)
        # inject context into the PM's system prompt
    """

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        root = Path(config.get("memory_root", str(_MEMORY_ROOT)))
        self.short = ShortTermMemory(root, ttl_days=int(config.get("short_ttl_days", 3)))
        self.medium = MediumTermMemory(root, ttl_days=int(config.get("medium_ttl_days", 28)))
        self.long = LongTermMemory(root)
        logger.info(f"[LayeredMemory] 初始化完成，存储路径: {root}")

    def add_short(self, ticker: str, content: str, trade_date: str) -> None:
        self.short.add(ticker, content, trade_date)

    def add_medium(self, ticker: str, summary: str, trade_date: str,
                   category: str = "trend") -> None:
        self.medium.add(ticker, summary, trade_date, category)

    def record_decision(self, ticker: str, trade_date: str,
                        decision: str, reasoning: str = "") -> None:
        self.long.record_decision(ticker, trade_date, decision, reasoning)

    def update_outcome(self, ticker: str, trade_date: str,
                       return_pct: float, reflection: str) -> None:
        self.long.update_outcome(ticker, trade_date, return_pct, reflection)

    def record_analyst_signals(self, ticker: str, trade_date: str,
                                signals: dict) -> None:
        self.long.record_analyst_signals(ticker, trade_date, signals)

    def update_analyst_outcomes(self, ticker: str, actual_direction: str,
                                outcome_date: str = "") -> None:
        self.long.update_analyst_outcomes(ticker, actual_direction, outcome_date)

    def get_analyst_weights(self, window: int = 20) -> dict:
        """Return weight coefficient per analyst based on rolling accuracy.

        weight = 0.5 + accuracy  →  range [0.5, 1.5]
        Analysts with no history default to 1.0 (neutral).
        """
        stats = self.long.get_analyst_accuracy(window=window)
        weights = {}
        for analyst, s in stats.items():
            weights[analyst] = round(0.5 + s["accuracy"], 3)
        return weights

    def get_analyst_weights_context(self, window: int = 20) -> str:
        """Return a formatted string for injection into the Portfolio Manager prompt."""
        stats = self.long.get_analyst_accuracy(window=window)
        if not stats:
            return ""
        lines = [f"## 分析师准确率权重（最近 {window} 次记录）"]
        _name_map = {
            "market": "市场分析师",
            "fundamentals": "基本面分析师",
            "news": "新闻分析师",
            "sentiment": "社媒分析师",
            "macro": "宏观事件分析师",
            "crypto": "加密货币分析师",
            "cn_social": "A股社交情绪分析师",
        }
        for analyst, s in sorted(stats.items()):
            display = _name_map.get(analyst, analyst)
            weight = round(0.5 + s["accuracy"], 2)
            lines.append(
                f"  - {display}: 准确率 {s['accuracy']*100:.0f}%"
                f"（{s['correct']}/{s['total']}）→ 权重系数 {weight:.2f}x"
            )
        lines.append("综合分析时，请按上述权重调整各分析师观点的参考比重。")
        return "\n".join(lines)

    def get_context(self, ticker: str,
                    n_short: int = 5, n_medium: int = 3, n_long: int = 5) -> str:
        """Return a formatted context string ready for prompt injection."""
        parts: list[str] = []

        # Short-term
        short_entries = self.short.get_recent(ticker, n=n_short)
        if short_entries:
            lines = [f"## 短期记忆（近3天）— {ticker}"]
            for e in reversed(short_entries):
                lines.append(f"[{e['date']}] {e['content']}")
            parts.append("\n".join(lines))

        # Medium-term
        med_entries = self.medium.get_recent(ticker, n=n_medium)
        if med_entries:
            lines = [f"## 中期记忆（近4周）— {ticker}"]
            for e in reversed(med_entries):
                lines.append(f"[{e['date']}] [{e.get('category', '')}] {e['summary']}")
            parts.append("\n".join(lines))

        # Long-term
        long_entries = self.long.get_context(ticker, n_same=n_long, n_cross=2)
        if long_entries:
            lines = [f"## 长期记忆（历史决策+反思）"]
            for e in long_entries:
                same = "同标的" if e["ticker"] == ticker else f"关联标的({e['ticker']})"
                ret = f"{e['return_pct']:+.1f}%" if e.get("return_pct") is not None else "未结算"
                lines.append(
                    f"[{e['trade_date']}] {same} {e['decision']} → 收益: {ret}"
                )
                if e.get("reflection"):
                    lines.append(f"  反思: {e['reflection'][:120]}…")
            parts.append("\n".join(lines))

        if not parts:
            return ""

        return "\n\n".join(parts)
