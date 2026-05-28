"""OpenBB-style MCP data server for TradingAgents-CN.

Inspired by OpenBB's "Connect once, consume everywhere" architecture which
exposes financial data as MCP (Model Context Protocol) tools so Claude, other
AI systems, and external agents can query data via a standard interface.

This server exposes the TradingAgents-CN data layer as MCP tools:

  Tools exposed:
    get_stock_price      — OHLCV data for A-share / HK / US tickers
    get_stock_news       — Recent news for a ticker
    get_fundamentals     — P/E, revenue, earnings for a ticker
    get_cn_sentiment     — A-share sentiment bundle (EastMoney)
    get_factor_analysis  — Technical factor values
    get_macro_news       — Recent macro/policy headlines
    run_cot_analysis     — Full CoT decomposition analysis

Usage:
    # Start as standalone MCP server (e.g., for Claude Desktop)
    python -m tradingagents.mcp_server

    # Or programmatically:
    from tradingagents.mcp_server import TradingMCPServer
    server = TradingMCPServer()
    server.run()

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "tradingagents": {
          "command": "python",
          "args": ["-m", "tradingagents.mcp_server"],
          "cwd": "/home/sun/TradingAgents-CN"
        }
      }
    }
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from tradingagents.utils.logging_init import get_logger
from tradingagents.utils.ticker_safety import safe_path_ticker

logger = get_logger("tradingagents.mcp_server")


# ---------------------------------------------------------------------------
# Tool implementations (callable regardless of MCP framework availability)
# ---------------------------------------------------------------------------

def tool_get_stock_price(ticker: str, start_date: str, end_date: str) -> str:
    """Get OHLCV price data for a ticker."""
    safe_ticker = safe_path_ticker(ticker)
    if safe_ticker == "_INVALID_TICKER":
        return json.dumps({"error": "Invalid ticker"})
    try:
        from tradingagents.dataflows.interface import get_YFin_data_online
        result = get_YFin_data_online(ticker, start_date, end_date)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception as e:
        # A-share fallback
        try:
            import akshare as ak
            code = ticker.split(".")[0]
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                return df.tail(30).to_string(index=False)
        except Exception:
            pass
        return json.dumps({"error": str(e)})


def tool_get_stock_news(ticker: str, trade_date: str, limit: int = 15) -> str:
    """Get recent news headlines for a ticker."""
    try:
        from tradingagents.dataflows.cn_sentiment import fetch_eastmoney_news
        return fetch_eastmoney_news(ticker, limit=limit)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_get_fundamentals(ticker: str, trade_date: str) -> str:
    """Get fundamental data (P/E, revenue, earnings) for a ticker."""
    try:
        ticker_upper = ticker.strip().upper()
        is_cn = (
            ticker_upper.endswith((".SH", ".SZ", ".SS", ".BJ"))
            or (len(ticker_upper) == 6 and ticker_upper.isdigit())
        )
        if is_cn:
            from tradingagents.dataflows.interface import get_china_stock_fundamentals_tushare
            result = get_china_stock_fundamentals_tushare(ticker, trade_date)
            return result if result else f"{ticker}: 基本面数据暂不可用"
        else:
            from tradingagents.dataflows.interface import get_fundamentals_openai
            result = get_fundamentals_openai(ticker, trade_date)
            return result if result else f"{ticker}: fundamentals not available"
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_get_cn_sentiment(ticker: str) -> str:
    """Get A-share sentiment bundle from EastMoney."""
    try:
        from tradingagents.dataflows.cn_sentiment import fetch_cn_sentiment_bundle
        news, hot, comment = fetch_cn_sentiment_bundle(ticker)
        return f"{news}\n\n---\n{hot}\n\n---\n{comment}"
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_get_factor_analysis(ticker: str, trade_date: str) -> str:
    """Get technical factor values for a ticker (no LLM — pure computation)."""
    try:
        from tradingagents.agents.analysts.factor_miner import _compute_basic_factors
        factors = _compute_basic_factors(ticker, trade_date)
        lines = [f"## 技术因子 — {ticker} @ {trade_date}"]
        for k, v in factors.items():
            lines.append(f"- {k}: {v}")
        return "\n".join(lines)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_get_macro_news(trade_date: str) -> str:
    """Get recent macro/policy headlines."""
    try:
        from tradingagents.agents.analysts.macro_event_analyst import _fetch_cn_macro_news
        return _fetch_cn_macro_news(trade_date)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# MCP server wrapper — supports both mcp library (if installed) and a
# minimal stdio-based JSON-RPC fallback for environments without mcp package.
# ---------------------------------------------------------------------------

_TOOLS_REGISTRY = {
    "get_stock_price": {
        "description": "Get OHLCV price data for a ticker (A-share, HK, or US)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol e.g. 600519.SH"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["ticker", "start_date", "end_date"],
        },
        "fn": tool_get_stock_price,
    },
    "get_stock_news": {
        "description": "Get recent news headlines for a ticker from EastMoney",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "trade_date": {"type": "string", "description": "Reference date YYYY-MM-DD"},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["ticker", "trade_date"],
        },
        "fn": tool_get_stock_news,
    },
    "get_fundamentals": {
        "description": "Get fundamental data (P/E, revenue, earnings) for a ticker",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "trade_date": {"type": "string"},
            },
            "required": ["ticker", "trade_date"],
        },
        "fn": tool_get_fundamentals,
    },
    "get_cn_sentiment": {
        "description": "Get A-share sentiment bundle: EastMoney news + hot-rank + comment stats",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "A-share ticker e.g. 600519.SH"},
            },
            "required": ["ticker"],
        },
        "fn": tool_get_cn_sentiment,
    },
    "get_factor_analysis": {
        "description": "Compute technical factor values (RSI, MACD, Bollinger, momentum) for a ticker",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "trade_date": {"type": "string"},
            },
            "required": ["ticker", "trade_date"],
        },
        "fn": tool_get_factor_analysis,
    },
    "get_macro_news": {
        "description": "Get recent macro/policy news headlines (A-share focused)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trade_date": {"type": "string"},
            },
            "required": ["trade_date"],
        },
        "fn": tool_get_macro_news,
    },
}


class _StdioMCPServer:
    """Minimal JSON-RPC 2.0 stdio server for MCP protocol.

    Used when the `mcp` Python package is not installed.
    Handles: initialize, tools/list, tools/call.
    """

    def _respond(self, req_id: Any, result: Any) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def _error(self, req_id: Any, code: int, message: str) -> None:
        msg = json.dumps({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message},
        })
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def run(self) -> None:
        logger.info("[MCP] TradingAgents-CN MCP server started (stdio mode)")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue

            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            if method == "initialize":
                self._respond(req_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tradingagents-cn", "version": "1.1.0"},
                })

            elif method == "tools/list":
                tools = []
                for name, spec in _TOOLS_REGISTRY.items():
                    tools.append({
                        "name": name,
                        "description": spec["description"],
                        "inputSchema": spec["inputSchema"],
                    })
                self._respond(req_id, {"tools": tools})

            elif method == "tools/call":
                tool_name = params.get("name", "")
                args = params.get("arguments", {})
                if tool_name not in _TOOLS_REGISTRY:
                    self._error(req_id, -32601, f"Unknown tool: {tool_name}")
                    continue
                try:
                    result_str = _TOOLS_REGISTRY[tool_name]["fn"](**args)
                    self._respond(req_id, {
                        "content": [{"type": "text", "text": result_str}]
                    })
                except Exception as e:
                    self._error(req_id, -32000, str(e))

            else:
                self._error(req_id, -32601, f"Method not found: {method}")


class TradingMCPServer:
    """Public entry point. Uses `mcp` library if available, else falls back."""

    def run(self) -> None:
        try:
            from mcp.server import Server
            from mcp.server.stdio import stdio_server
            import mcp.types as types

            server = Server("tradingagents-cn")

            @server.list_tools()
            async def list_tools():
                return [
                    types.Tool(
                        name=name,
                        description=spec["description"],
                        inputSchema=spec["inputSchema"],
                    )
                    for name, spec in _TOOLS_REGISTRY.items()
                ]

            @server.call_tool()
            async def call_tool(name: str, arguments: dict):
                if name not in _TOOLS_REGISTRY:
                    raise ValueError(f"Unknown tool: {name}")
                result_str = _TOOLS_REGISTRY[name]["fn"](**arguments)
                return [types.TextContent(type="text", text=result_str)]

            import asyncio
            logger.info("[MCP] TradingAgents-CN MCP server started (mcp library mode)")
            asyncio.run(stdio_server(server))

        except ImportError:
            logger.info("[MCP] mcp package not found, using built-in stdio fallback")
            _StdioMCPServer().run()


if __name__ == "__main__":
    TradingMCPServer().run()
