"""Telegram Bot 配置与测试路由。

Endpoints:
  GET  /telegram/config          — 读取当前配置（脱敏 token）
  POST /telegram/config          — 保存 bot_token + chat_id
  POST /telegram/test            — 发送测试消息验证连通性
  POST /telegram/send_signal     — 手动推送信号（调试用）
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.routers.auth_db import get_current_user
from app.core.database import get_mongo_db
from app.core.response import ok

router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramConfigRequest(BaseModel):
    bot_token: str
    chat_id: str
    enabled: bool = True


class SendSignalRequest(BaseModel):
    symbol: str
    advice: str
    confidence: float = 0.75
    reason: str
    analysis_id: Optional[str] = None


@router.get("/config")
async def get_config(current_user: dict = Depends(get_current_user)):
    """读取 Telegram 配置（bot_token 仅展示后4位）。"""
    db = get_mongo_db()
    cfg = await db["telegram_config"].find_one(
        {"user_id": current_user["id"]}, {"_id": 0}
    )
    if not cfg:
        return ok({"configured": False})
    token = cfg.get("bot_token", "")
    masked = ("*" * (len(token) - 4) + token[-4:]) if len(token) > 4 else "****"
    return ok({
        "configured": True,
        "bot_token_masked": masked,
        "chat_id": cfg.get("chat_id", ""),
        "enabled": cfg.get("enabled", True),
    })


@router.post("/config")
async def save_config(
    req: TelegramConfigRequest,
    current_user: dict = Depends(get_current_user),
):
    """保存 Telegram Bot 配置。"""
    db = get_mongo_db()
    await db["telegram_config"].update_one(
        {"user_id": current_user["id"]},
        {"$set": {
            "user_id": current_user["id"],
            "bot_token": req.bot_token,
            "chat_id": req.chat_id,
            "enabled": req.enabled,
        }},
        upsert=True,
    )
    return ok({"saved": True})


@router.post("/test")
async def test_connection(current_user: dict = Depends(get_current_user)):
    """发送测试消息并验证 Bot 连通性。"""
    cfg = await _get_notifier(current_user["id"])
    if cfg is None:
        raise HTTPException(status_code=400, detail="Telegram 未配置，请先保存 Bot Token 和 Chat ID")
    notifier, _ = cfg
    ok_flag = notifier.test_connection()
    if ok_flag:
        notifier.send_text("✅ TradingAgents-CN Telegram 通知已连接成功！")
    return ok({"connected": ok_flag})


@router.post("/send_signal")
async def send_signal(
    req: SendSignalRequest,
    current_user: dict = Depends(get_current_user),
):
    """手动发送交易信号（用于调试）。"""
    cfg = await _get_notifier(current_user["id"])
    if cfg is None:
        raise HTTPException(status_code=400, detail="Telegram 未配置")
    notifier, _ = cfg
    msg_id = notifier.send_signal(
        symbol=req.symbol,
        advice=req.advice,
        confidence=req.confidence,
        reason=req.reason,
        analysis_id=req.analysis_id,
    )
    return ok({"message_id": msg_id, "sent": msg_id is not None})


# ── helpers ──────────────────────────────────────────────────────────────────

async def _get_notifier(user_id: str):
    """Load saved config and return (TelegramNotifier, config_dict) or None."""
    from tradingagents.notification.telegram import TelegramNotifier
    db = get_mongo_db()
    cfg = await db["telegram_config"].find_one({"user_id": user_id})
    if not cfg or not cfg.get("enabled") or not cfg.get("bot_token"):
        return None
    notifier = TelegramNotifier(
        bot_token=cfg["bot_token"],
        chat_id=cfg["chat_id"],
    )
    return notifier, cfg


async def notify_analysis_result(
    user_id: str,
    symbol: str,
    advice: str,
    confidence: float,
    reason: str,
    analysis_id: Optional[str] = None,
) -> None:
    """Called by analysis service after AI decision is ready.

    Silently skips if Telegram is not configured for this user.
    """
    cfg = await _get_notifier(user_id)
    if cfg is None:
        return
    notifier, _ = cfg
    try:
        notifier.send_signal(
            symbol=symbol,
            advice=advice,
            confidence=confidence,
            reason=reason,
            analysis_id=analysis_id,
        )
    except Exception as e:
        import logging
        logging.getLogger("webapi").warning("[Telegram] 推送失败 user=%s: %s", user_id, e)
