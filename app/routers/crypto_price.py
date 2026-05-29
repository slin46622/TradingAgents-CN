"""Crypto real-time price endpoints.

Endpoints:
  GET  /api/crypto/price/{symbol}      — single symbol latest price (REST)
  GET  /api/crypto/prices              — batch prices for multiple symbols
  GET  /api/crypto/ohlcv/{symbol}      — recent OHLCV (last 60 days)
  WS   /api/crypto/ws/price/{symbol}   — WebSocket streaming price feed
"""

from __future__ import annotations

import asyncio
import logging
from typing import List

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.response import ok

router = APIRouter(prefix="/crypto", tags=["crypto-price"])
logger = logging.getLogger("webapi")


def _get_binance():
    from tradingagents.dataflows.providers.crypto.binance import BinanceProvider
    return BinanceProvider()


def _get_coingecko():
    from tradingagents.dataflows.providers.crypto.coingecko import CoinGeckoProvider
    return CoinGeckoProvider()


def _price_with_fallback(symbol: str) -> float:
    try:
        price = _get_binance().get_price(symbol)
        if price > 0:
            return price
    except Exception:
        pass
    try:
        return _get_coingecko().get_price(symbol)
    except Exception:
        return 0.0


@router.get("/price/{symbol}")
async def get_crypto_price(symbol: str):
    """Latest price for one crypto symbol (Binance → CoinGecko fallback)."""
    price = _price_with_fallback(symbol)
    return ok({"symbol": symbol.upper(), "price": price, "unit": "USDT"})


@router.get("/prices")
async def get_crypto_prices(symbols: List[str] = Query(..., description="交易对列表，如 BTCUSDT,ETHUSDT")):
    """Batch latest prices."""
    bp = _get_binance()
    result = {}
    for sym in symbols:
        try:
            p = bp.get_price(sym)
            result[sym.upper()] = p if p > 0 else _get_coingecko().get_price(sym)
        except Exception:
            result[sym.upper()] = 0.0
    return ok(result)


@router.get("/ohlcv/{symbol}")
async def get_crypto_ohlcv(
    symbol: str,
    limit: int = Query(60, ge=1, le=500),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """Recent OHLCV data for a crypto symbol."""
    try:
        bp = _get_binance()
        df = bp.get_ohlcv(symbol, start_date=start_date, end_date=end_date, limit=limit)
        if df is None or df.empty:
            cg = _get_coingecko()
            df = cg.get_ohlcv(symbol, limit=min(limit, 90))
        if df is None or df.empty:
            return ok({"symbol": symbol.upper(), "data": []})
        records = df.to_dict(orient="records")
        for r in records:
            if hasattr(r.get("date"), "strftime"):
                r["date"] = r["date"].strftime("%Y-%m-%d")
        return ok({"symbol": symbol.upper(), "data": records})
    except Exception as e:
        return ok({"symbol": symbol.upper(), "data": [], "error": str(e)})


@router.get("/supported")
async def get_supported_symbols():
    """Return list of commonly supported crypto symbols."""
    from tradingagents.dataflows.providers.crypto.binance import SUPPORTED_SYMBOLS
    return ok(SUPPORTED_SYMBOLS)


@router.websocket("/ws/price/{symbol}")
async def websocket_crypto_price(websocket: WebSocket, symbol: str):
    """WebSocket streaming price feed. Pushes latest price every 3 seconds.

    Client receives JSON: {"symbol": "BTCUSDT", "price": 67432.1, "unit": "USDT"}
    """
    await websocket.accept()
    sym = symbol.upper()
    logger.info(f"[CryptoWS] 客户端连接: {sym}")
    try:
        while True:
            price = _price_with_fallback(sym)
            await websocket.send_json({"symbol": sym, "price": price, "unit": "USDT"})
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        logger.info(f"[CryptoWS] 客户端断开: {sym}")
    except Exception as e:
        logger.warning(f"[CryptoWS] {sym} 异常: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
