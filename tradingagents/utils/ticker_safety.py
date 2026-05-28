"""Ticker symbol sanitization for filesystem path safety.

Prevents path-traversal attacks where a malicious ticker like
``../../etc/passwd`` or ``../config`` could escape intended cache/data
directories when ticker strings are embedded in file paths.

Usage:
    from tradingagents.utils.ticker_safety import safe_path_ticker

    path = os.path.join(CACHE_DIR, safe_path_ticker(ticker) + ".csv")
"""

from __future__ import annotations
import re

# Allow: alphanumeric, dot (for exchange suffixes like .HK .SH .SZ), hyphen,
# underscore, caret (for index tickers like ^HSI). Everything else is stripped.
_ALLOWED = re.compile(r"[^A-Za-z0-9.\-_^]")

# Maximum length for a sanitized ticker (prevents absurdly long filenames)
_MAX_LEN = 32


def safe_path_ticker(ticker: str) -> str:
    """Return a filesystem-safe version of *ticker*.

    Strips any character that is not alphanumeric or one of ``. - _ ^``,
    collapses runs of dots to prevent ``..`` sequences, and truncates to
    ``_MAX_LEN`` characters.  Never raises — returns ``_INVALID_TICKER``
    for empty or fully-stripped input so callers always get a usable string.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        return "_INVALID_TICKER"

    sanitized = _ALLOWED.sub("", ticker.strip())
    # collapse any remaining consecutive dots (e.g. "..." → ".")
    sanitized = re.sub(r"\.{2,}", ".", sanitized)
    sanitized = sanitized[:_MAX_LEN]

    return sanitized if sanitized else "_INVALID_TICKER"
