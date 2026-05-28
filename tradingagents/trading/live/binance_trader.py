"""Binance live trading client — authenticated REST API wrapper.

Supports spot trading via HMAC-SHA256 signed requests.
API key and secret are passed at construction time; never stored to disk here.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from tradingagents.utils.logging_init import get_logger

logger = get_logger("trading.live.binance")

_BASE_URL = "https://api.binance.com"


class BinanceTrader:
    """Authenticated Binance REST client for spot order management."""

    def __init__(self, api_key: str, api_secret: str, timeout: int = 10):
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "X-MBX-APIKEY": api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingAgents-CN/1.0",
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    def _get(self, path: str, params: dict | None = None, signed: bool = False) -> dict:
        params = params or {}
        if signed:
            params = self._sign(params)
        resp = self._session.get(_BASE_URL + path, params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, params: dict) -> dict:
        params = self._sign(params)
        resp = self._session.post(
            _BASE_URL + path, data=urlencode(params), timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, params: dict) -> dict:
        params = self._sign(params)
        resp = self._session.delete(
            _BASE_URL + path, params=params, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """Ping and return account status. Raises on auth failure."""
        info = self._get("/api/v3/account", signed=True)
        balances = [
            b for b in info.get("balances", [])
            if float(b["free"]) > 0 or float(b["locked"]) > 0
        ]
        return {
            "connected": True,
            "account_type": info.get("accountType", "SPOT"),
            "can_trade": info.get("canTrade", False),
            "non_zero_balances": len(balances),
        }

    def get_account(self) -> dict:
        """Return account info with all non-zero balances."""
        info = self._get("/api/v3/account", signed=True)
        balances = [
            {
                "asset": b["asset"],
                "free": float(b["free"]),
                "locked": float(b["locked"]),
                "total": float(b["free"]) + float(b["locked"]),
            }
            for b in info.get("balances", [])
            if float(b["free"]) > 0 or float(b["locked"]) > 0
        ]
        return {
            "account_type": info.get("accountType", "SPOT"),
            "can_trade": info.get("canTrade", False),
            "balances": balances,
        }

    def get_ticker_price(self, symbol: str) -> float:
        """Return the latest price for *symbol* (e.g. BTCUSDT)."""
        data = self._get("/api/v3/ticker/price", {"symbol": symbol.upper()})
        return float(data["price"])

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "MARKET",
        quantity: Optional[float] = None,
        quote_order_qty: Optional[float] = None,
        price: Optional[float] = None,
        time_in_force: str = "GTC",
    ) -> dict:
        """Place a spot order.

        Args:
            symbol: e.g. 'BTCUSDT'
            side: 'BUY' or 'SELL'
            order_type: 'MARKET' or 'LIMIT'
            quantity: base asset quantity (required for SELL; optional for MARKET BUY)
            quote_order_qty: quote asset amount for MARKET BUY (e.g. spend 100 USDT)
            price: required for LIMIT orders
            time_in_force: 'GTC', 'IOC', 'FOK' (for LIMIT)
        """
        params: dict = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
        }
        if order_type.upper() == "LIMIT":
            params["timeInForce"] = time_in_force
            params["price"] = f"{price:.8f}".rstrip("0").rstrip(".")
        if quantity is not None:
            params["quantity"] = f"{quantity:.8f}".rstrip("0").rstrip(".")
        if quote_order_qty is not None:
            params["quoteOrderQty"] = f"{quote_order_qty:.8f}".rstrip("0").rstrip(".")

        result = self._post("/api/v3/order", params)
        logger.info(
            f"[BinanceTrader] 下单成功: {symbol} {side} qty={quantity or quote_order_qty} "
            f"orderId={result.get('orderId')}"
        )
        return result

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel an open order by orderId."""
        result = self._delete("/api/v3/order", {"symbol": symbol.upper(), "orderId": order_id})
        logger.info(f"[BinanceTrader] 撤单成功: {symbol} orderId={order_id}")
        return result

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """Return all open orders, optionally filtered by symbol."""
        params: dict = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._get("/api/v3/openOrders", params, signed=True)

    def get_order_history(self, symbol: str, limit: int = 50) -> list:
        """Return recent order history for *symbol*."""
        return self._get(
            "/api/v3/allOrders",
            {"symbol": symbol.upper(), "limit": limit},
            signed=True,
        )

    def get_my_trades(self, symbol: str, limit: int = 50) -> list:
        """Return recent trade fills for *symbol*."""
        return self._get(
            "/api/v3/myTrades",
            {"symbol": symbol.upper(), "limit": limit},
            signed=True,
        )
