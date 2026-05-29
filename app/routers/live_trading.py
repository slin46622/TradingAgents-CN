"""Live trading router — multi-exchange via CCXT.

Endpoints:
  GET  /api/live/exchanges     — list supported exchanges
  GET  /api/live/config        — read config (key masked)
  POST /api/live/config        — save exchange + API key/secret
  POST /api/live/test          — test connection
  GET  /api/live/account       — balances
  GET  /api/live/price/{symbol}— latest price (public)
  POST /api/live/order         — place order
  DELETE /api/live/order/{id}  — cancel order
  GET  /api/live/orders        — open orders
  GET  /api/live/history       — order history for a symbol
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.response import ok
from app.routers.auth_db import get_current_user
from tradingagents.trading.live.exchange_trader import (
    ExchangeTrader,
    SUPPORTED_EXCHANGES,
    normalize_symbol,
)

router = APIRouter(prefix="/live", tags=["live-trading"])
logger = logging.getLogger("webapi")

_COLLECTION = "live_trading_config"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LiveConfig(BaseModel):
    exchange_id: str = Field("binance", description="CCXT exchange id, e.g. binance/okx/bybit")
    api_key: str = Field(..., min_length=5)
    api_secret: str = Field(..., min_length=5)
    password: Optional[str] = Field(None, description="Passphrase (OKX/KuCoin/Bitget only)")
    enabled: bool = True


class PlaceLiveOrderRequest(BaseModel):
    symbol: str = Field(..., description="交易对，如 BTC/USDT 或 BTCUSDT")
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"] = "market"
    amount: Optional[float] = Field(None, gt=0, description="基础资产数量")
    cost: Optional[float] = Field(None, gt=0, description="MARKET BUY 消费的报价资产金额")
    price: Optional[float] = Field(None, gt=0, description="LIMIT 订单价格")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_config(user_id: str, db) -> Optional[dict]:
    return await db[_COLLECTION].find_one({"user_id": user_id})


def _make_trader(cfg: dict) -> ExchangeTrader:
    return ExchangeTrader(
        exchange_id=cfg.get("exchange_id", "binance"),
        api_key=cfg["api_key"],
        api_secret=cfg["api_secret"],
        password=cfg.get("password"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/exchanges")
async def list_exchanges():
    """Return the list of supported exchanges for the UI dropdown."""
    return ok(SUPPORTED_EXCHANGES)


@router.get("/config")
async def get_live_config(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        return ok({"configured": False})
    key = cfg.get("api_key", "")
    return ok({
        "configured": True,
        "exchange_id": cfg.get("exchange_id", "binance"),
        "api_key_masked": key[:8] + "..." + key[-4:] if len(key) > 12 else "***",
        "enabled": cfg.get("enabled", True),
    })


@router.post("/config")
async def save_live_config(
    payload: LiveConfig,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    await db[_COLLECTION].update_one(
        {"user_id": current_user["id"]},
        {"$set": {
            "user_id": current_user["id"],
            "exchange_id": payload.exchange_id,
            "api_key": payload.api_key,
            "api_secret": payload.api_secret,
            "password": payload.password,
            "enabled": payload.enabled,
        }},
        upsert=True,
    )
    return ok({"saved": True})


@router.post("/test")
async def test_connection(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    try:
        trader = _make_trader(cfg)
        result = trader.test_connection()
        return ok(result)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"连接失败: {e}")


@router.get("/account")
async def get_account(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    try:
        return ok(_make_trader(cfg).get_account())
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/price/{symbol}")
async def get_price(symbol: str, exchange_id: str = Query("binance")):
    """Public endpoint — no auth. Pass exchange_id to query non-Binance prices."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange_id.lower())({'enableRateLimit': True})
        ticker = ex.fetch_ticker(normalize_symbol(symbol))
        return ok({"exchange": exchange_id, "symbol": normalize_symbol(symbol), "price": ticker["last"]})
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/order")
async def place_order(
    payload: PlaceLiveOrderRequest,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    if not cfg.get("enabled", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="实盘交易已禁用")
    if payload.order_type == "limit" and not payload.price:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="LIMIT 订单必须提供 price")
    if payload.amount is None and payload.cost is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="amount 或 cost 必须提供一个")
    try:
        trader = _make_trader(cfg)
        result = trader.place_order(
            symbol=payload.symbol,
            side=payload.side,
            order_type=payload.order_type,
            amount=payload.amount,
            price=payload.price,
            cost=payload.cost,
        )
        logger.info(
            f"[实盘] user={current_user['id']} exchange={cfg.get('exchange_id')} "
            f"{payload.symbol} {payload.side} id={result.get('id')}"
        )
        return ok(result)
    except Exception as e:
        logger.error(f"[实盘] 下单失败: {e}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/order/{order_id}")
async def cancel_order(
    order_id: str,
    symbol: str,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    try:
        return ok(_make_trader(cfg).cancel_order(order_id, symbol))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/orders")
async def get_open_orders(
    symbol: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    try:
        return ok(_make_trader(cfg).get_open_orders(symbol))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/history")
async def get_order_history(
    symbol: str,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    try:
        return ok(_make_trader(cfg).get_order_history(symbol, limit))
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
