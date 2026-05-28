"""Live trading router — Binance spot order management via authenticated REST API.

Endpoints:
  GET  /api/live/config        — read config (API key masked)
  POST /api/live/config        — save API key + secret
  POST /api/live/test          — test connection
  GET  /api/live/account       — balances
  POST /api/live/order         — place order
  DELETE /api/live/order/{id}  — cancel order
  GET  /api/live/orders        — open orders
  GET  /api/live/history       — order history for a symbol
  GET  /api/live/price/{symbol} — latest price
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.response import ok
from app.routers.auth_db import get_current_user

router = APIRouter(prefix="/live", tags=["live-trading"])
logger = logging.getLogger("webapi")

# Collection name in MongoDB
_COLLECTION = "live_trading_config"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LiveConfig(BaseModel):
    api_key: str = Field(..., min_length=10)
    api_secret: str = Field(..., min_length=10)
    enabled: bool = True


class PlaceLiveOrderRequest(BaseModel):
    symbol: str = Field(..., description="交易对，如 BTCUSDT")
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity: Optional[float] = Field(None, gt=0, description="基础资产数量")
    quote_order_qty: Optional[float] = Field(None, gt=0, description="MARKET BUY 消费的报价资产数量")
    price: Optional[float] = Field(None, gt=0, description="LIMIT 订单价格")
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_config(user_id: str, db) -> Optional[dict]:
    return await db[_COLLECTION].find_one({"user_id": user_id})


def _make_trader(cfg: dict):
    from tradingagents.trading.live.binance_trader import BinanceTrader
    return BinanceTrader(api_key=cfg["api_key"], api_secret=cfg["api_secret"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_live_config(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        return ok({"configured": False})
    return ok({
        "configured": True,
        "api_key_masked": cfg["api_key"][:8] + "..." + cfg["api_key"][-4:],
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
            "api_key": payload.api_key,
            "api_secret": payload.api_secret,
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
        trader = _make_trader(cfg)
        return ok(trader.get_account())
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/price/{symbol}")
async def get_price(symbol: str):
    """Public endpoint — no auth required."""
    try:
        from tradingagents.trading.live.binance_trader import BinanceTrader
        # price endpoint is public; pass dummy creds
        trader = BinanceTrader(api_key="", api_secret="")
        price = trader.get_ticker_price(symbol)
        return ok({"symbol": symbol.upper(), "price": price})
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
    if payload.order_type == "LIMIT" and not payload.price:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="LIMIT 订单必须提供 price")
    if payload.quantity is None and payload.quote_order_qty is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="quantity 或 quote_order_qty 必须提供一个")
    try:
        trader = _make_trader(cfg)
        result = trader.place_order(
            symbol=payload.symbol,
            side=payload.side,
            order_type=payload.order_type,
            quantity=payload.quantity,
            quote_order_qty=payload.quote_order_qty,
            price=payload.price,
            time_in_force=payload.time_in_force,
        )
        logger.info(
            f"[实盘] user={current_user['id']} {payload.symbol} {payload.side} "
            f"orderId={result.get('orderId')}"
        )
        return ok(result)
    except Exception as e:
        logger.error(f"[实盘] 下单失败: {e}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/order/{order_id}")
async def cancel_order(
    order_id: int,
    symbol: str,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    cfg = await _get_config(current_user["id"], db)
    if not cfg:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请先配置 API Key")
    try:
        trader = _make_trader(cfg)
        result = trader.cancel_order(symbol=symbol, order_id=order_id)
        return ok(result)
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
        trader = _make_trader(cfg)
        orders = trader.get_open_orders(symbol=symbol)
        return ok(orders)
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
        trader = _make_trader(cfg)
        history = trader.get_order_history(symbol=symbol, limit=limit)
        return ok(history)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
