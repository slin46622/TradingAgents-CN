"""State checkpointer for resume capability.

Implements a simple JSON-file-based checkpointer that records the
entire AgentState dictionary at the end of each major pipeline phase
(analyst reports, research plan, trader proposal, final decision).

This lets a user pass ``--resume`` to the CLI to pick up from the
last recorded phase rather than starting over from scratch, which is
useful during development and when iterating on a specific ticker.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from tradingagents.agents.utils.agent_states import AgentState

_SAVE_DIR = Path.home() / ".tradingagents" / "checkpoints"


def _ensure_dir() -> None:
    _SAVE_DIR.mkdir(parents=True, exist_ok=True)


def checkpoint_path(ticker: str, run_id: str) -> Path:
    return _SAVE_DIR / f"{ticker.upper()}_{run_id}.json"


def save_checkpoint(
    state: AgentState,
    run_id: str,
    phase: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Persist the current agent state to a JSON checkpoint file.

    Returns the checkpoint file path.
    """
    _ensure_dir()
    path = checkpoint_path(state.get("company_of_interest", "UNKNOWN"), run_id)

    payload = {
        "phase": phase,
        "timestamp": datetime.utcnow().isoformat(),
        "state": dict(state),
    }
    if metadata:
        payload["metadata"] = metadata

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return str(path)


def load_checkpoint(ticker: str, run_id: str) -> Optional[dict]:
    """Load a previously saved checkpoint.

    Returns None if the checkpoint file does not exist or is corrupt.
    """
    path = checkpoint_path(ticker.upper(), run_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_checkpoints(ticker: Optional[str] = None) -> list[dict]:
    """List available checkpoints, optionally filtered by ticker.

    Returns a list of dicts with keys: ticker, run_id, phase, timestamp, path.
    """
    _ensure_dir()
    results = []
    for fpath in sorted(_SAVE_DIR.iterdir(), reverse=True):
        if not fpath.suffix == ".json":
            continue
        parts = fpath.stem.split("_", 1)
        ticker_part = parts[0] if len(parts) > 0 else "?"
        run_id_part = parts[1] if len(parts) > 1 else "?"
        if ticker and ticker.upper() != ticker_part:
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            meta = {}
        results.append({
            "ticker": ticker_part,
            "run_id": run_id_part,
            "phase": meta.get("phase", "?"),
            "timestamp": meta.get("timestamp", "?"),
            "path": str(fpath),
        })
    return results


def clean_old_checkpoints(max_age_days: int = 30) -> int:
    """Remove checkpoints older than ``max_age_days``. Returns count removed."""
    _ensure_dir()
    now = datetime.utcnow().timestamp()
    removed = 0
    for fpath in _SAVE_DIR.iterdir():
        if fpath.suffix == ".json" and fpath.stat().st_mtime < now - max_age_days * 86400:
            fpath.unlink()
            removed += 1
    return removed
