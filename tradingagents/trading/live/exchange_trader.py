"""Multi-exchange live trading client backed by CCXT.

Replaces the hand-coded BinanceTrader. Any of the 100+ CCXT-supported
exchanges (binance, okx, bybit, gate, kraken, …) can be used by passing
the exchange_id at construction time.

Symbol format: always use CCXT slash notation — "BTC/USDT".
The helper ``normalize_symbol`` converts Binance-style "BTCUSDT" if needed.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("trading.live.exchange")

# Common USDT pairs — used for auto-normalisation
_KNOWN_BASES = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT",
    "MATIC", "LINK", "UNI", "ATOM", "LTC", "ETC", "NEAR", "APT", "ARB",
    "OP", "SUI", "TRX", "FIL", "INJ", "IMX", "PEPE", "WIF", "BONK",
]

# Exchanges that require password in addition to key/secret
_NEED_PASSWORD = {"okx", "kucoin", "bitget"}

# Supported exchange list for the UI
SUPPORTED_EXCHANGES = [
    {"id": "binance",  "name": "Binance"},
    {"id": "okx",      "name": "OKX"},
    {"id": "bybit",    "name": "Bybit"},
    {"id": "gate",     "name": "Gate.io"},
    {"id": "kraken",   "name": "Kraken"},
    {"id": "bitget",   "name": "Bitget"},
    {"id": "kucoin",   "name": "KuCoin"},
]


def normalize_symbol(symbol: str) -> str:
    """Convert Binance-style symbol to CCXT slash notation.

    'BTCUSDT' → 'BTC/USDT'   'BTC/USDT' → 'BTC/USDT' (no-op)
    """
    if "/" in symbol:
        return symbol.upper()
    sym = symbol.upper()
    for base in _KNOWN_BASES:
        if sym.startswith(base) and sym.endswith("USDT"):
            return f"{base}/USDT"
        if sym.startswith(base) and sym.endswith("BTC"):
            return f"{base}/BTC"
    # Fallback: try to split at known quote currencies
    for quote in ("USDT", "BUSD", "USD", "BTC", "ETH", "BNB"):
        if sym.endswith(quote):
            base = sym[: -len(quote)]
            if base:
                return f"{base}/{quote}"
    return symbol  # give up, return as-is


class ExchangeTrader:
    """Unified CCXT-backed trader — works with any supported exchange."""

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        password: Optional[str] = None,
        sandbox: bool = False,
        timeout: int = 10_000,
    ):
        import ccxt
        exchange_id = exchange_id.lower()
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Exchange '{exchange_id}' is not supported by CCXT")

        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": timeout,
        }
        if password and exchange_id in _NEED_PASSWORD:
            config["password"] = password

        # CCXT sets session.trust_env=False so env proxy vars are ignored.
        # Detect proxy from environment and pass explicitly.
        https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        if https_proxy:
            config["httpsProxy"] = https_proxy
        elif http_proxy:
            config["httpProxy"] = http_proxy

        self.exchange_id = exchange_id
        self.exchange = getattr(ccxt, exchange_id)(config)
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Binance exchange ids that support private_get_account()
    _BINANCE_IDS = frozenset({"binance", "binanceusdm", "binancecoinm"})

    def test_connection(self) -> dict:
        """Verify credentials with the lightest signed endpoint available.

        Binance: uses GET /api/v3/account (only needs Enable Reading, avoids
        the SAPI capital/config endpoint that requires elevated permissions).
        Other exchanges: uses fetch_balance({'type': 'spot'}).
        """
        if self.exchange_id in self._BINANCE_IDS:
            info = self.exchange.private_get_account()
            return {
                "connected": True,
                "exchange": self.exchange_id,
                "account_type": info.get("accountType", "SPOT"),
                "can_trade": info.get("canTrade", False),
            }
        else:
            balance = self.exchange.fetch_balance({"type": "spot"})
            non_zero = [k for k, v in balance.get("total", {}).items() if v and v > 0]
            return {
                "connected": True,
                "exchange": self.exchange_id,
                "account_type": "SPOT",
                "can_trade": True,
                "non_zero_assets": len(non_zero),
            }

    def get_account(self) -> dict:
        """Return all non-zero balances.

        Binance: uses GET /api/v3/account to avoid the SAPI capital endpoint.
        """
        if self.exchange_id in self._BINANCE_IDS:
            info = self.exchange.private_get_account()
            balances = [
                {
                    "asset": b["asset"],
                    "free": round(float(b.get("free", 0)), 8),
                    "locked": round(float(b.get("locked", 0)), 8),
                    "total": round(float(b.get("free", 0)) + float(b.get("locked", 0)), 8),
                }
                for b in info.get("balances", [])
                if float(b.get("free", 0)) + float(b.get("locked", 0)) > 0
            ]
        else:
            balance = self.exchange.fetch_balance({"type": "spot"})
            balances = [
                {
                    "asset": asset,
                    "free": round(float(balance["free"].get(asset, 0)), 8),
                    "locked": round(float(balance["used"].get(asset, 0)), 8),
                    "total": round(float(v), 8),
                }
                for asset, v in balance.get("total", {}).items()
                if v and float(v) > 0
            ]
        return {"exchange": self.exchange_id, "balances": balances}

    def get_ticker_price(self, symbol: str) -> float:
        """Latest price for *symbol* (e.g. BTC/USDT or BTCUSDT)."""
        ticker = self.exchange.fetch_ticker(normalize_symbol(symbol))
        return float(ticker["last"])

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "market",
        amount: Optional[float] = None,
        price: Optional[float] = None,
        cost: Optional[float] = None,
    ) -> dict:
        """Place a spot order.

        Args:
            symbol: e.g. 'BTC/USDT' or 'BTCUSDT'
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit'
            amount: base asset quantity
            price: required for limit orders
            cost: quote asset spend for market buy (alternative to amount)
        """
        sym = normalize_symbol(symbol)
        side = side.lower()
        order_type = order_type.lower()

        params: dict = {}

        if order_type == "market":
            if side == "buy" and cost is not None and amount is None:
                # spend a fixed USDT amount — exchange-specific param
                if self.exchange_id in ("binance", "binanceusdm"):
                    params["quoteOrderQty"] = cost
                    order = self.exchange.create_order(sym, "market", "buy", 0, None, params)
                else:
                    # CCXT createMarketBuyOrderWithCost when supported
                    order = self.exchange.create_market_buy_order_with_cost(sym, cost)
            else:
                order = self.exchange.create_order(sym, "market", side, amount or 0, None, params)
        else:
            order = self.exchange.create_limit_order(sym, side, amount, price)

        logger.info(
            f"[ExchangeTrader/{self.exchange_id}] 下单: {sym} {side} {order_type} "
            f"amount={amount} cost={cost} → id={order.get('id')}"
        )
        return order

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order."""
        result = self.exchange.cancel_order(str(order_id), normalize_symbol(symbol))
        logger.info(f"[ExchangeTrader/{self.exchange_id}] 撤单: {symbol} id={order_id}")
        return result

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """All open orders, optionally filtered by symbol."""
        sym = normalize_symbol(symbol) if symbol else None
        return self.exchange.fetch_open_orders(sym)

    def get_order_history(self, symbol: str, limit: int = 50) -> list:
        """Recent orders for *symbol*."""
        return self.exchange.fetch_orders(normalize_symbol(symbol), limit=limit)

    def get_my_trades(self, symbol: str, limit: int = 50) -> list:
        """Recent fills for *symbol*."""
        return self.exchange.fetch_my_trades(normalize_symbol(symbol), limit=limit)
