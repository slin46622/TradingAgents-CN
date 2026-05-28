"""Telegram Bot notification adapter.

Sends AI trade signals after analysis completes, and listens for
'确认' / '忽略' replies to trigger or skip paper orders.

Usage:
    notifier = TelegramNotifier(bot_token="...", chat_id="...")
    notifier.send_signal(symbol="000001", advice="买入", confidence=0.82,
                         reason="均线多头排列，MACD金叉", analysis_id="abc123")

Polling for replies:
    notifier.start_reply_listener(on_confirm=..., on_ignore=...)
    notifier.stop_reply_listener()
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    """Thin wrapper around Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 10):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_update_id: int = 0
        # pending confirms: {message_id: analysis_id}
        self._pending: dict[int, str] = {}

    # ── 发送 ────────────────────────────────────────────────────────────────

    def send_signal(
        self,
        symbol: str,
        advice: str,
        confidence: float,
        reason: str,
        analysis_id: Optional[str] = None,
    ) -> Optional[int]:
        """Push an AI trade signal. Returns Telegram message_id or None on failure."""
        direction_emoji = {"买入": "🟢", "卖出": "🔴", "持有": "🟡"}.get(advice, "⚪")
        conf_pct = f"{confidence * 100:.0f}%" if confidence <= 1 else f"{confidence:.0f}%"
        text = (
            f"{direction_emoji} *AI 交易信号*\n\n"
            f"标的：`{symbol}`\n"
            f"方向：*{advice}*\n"
            f"置信度：{conf_pct}\n"
            f"理由：{reason}\n\n"
            f"回复 *确认* 触发模拟下单，回复 *忽略* 跳过。"
        )
        resp = self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })
        if resp and resp.get("ok"):
            msg_id = resp["result"]["message_id"]
            if analysis_id:
                self._pending[msg_id] = analysis_id
            return msg_id
        logger.warning("[Telegram] sendMessage 失败: %s", resp)
        return None

    def send_text(self, text: str) -> bool:
        """Send a plain text message."""
        resp = self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
        })
        return bool(resp and resp.get("ok"))

    # ── 轮询回复 ─────────────────────────────────────────────────────────────

    def start_reply_listener(
        self,
        on_confirm: Callable[[str], None],
        on_ignore: Callable[[str], None],
        poll_interval: float = 3.0,
    ) -> None:
        """Start background thread polling for user replies."""
        if self._listener_thread and self._listener_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                self._poll_once(on_confirm, on_ignore)
                self._stop_event.wait(poll_interval)

        self._listener_thread = threading.Thread(target=_loop, daemon=True, name="telegram-poll")
        self._listener_thread.start()
        logger.info("[Telegram] 回复监听线程已启动 (interval=%.1fs)", poll_interval)

    def stop_reply_listener(self) -> None:
        self._stop_event.set()
        if self._listener_thread:
            self._listener_thread.join(timeout=5)
        logger.info("[Telegram] 回复监听线程已停止")

    def _poll_once(
        self,
        on_confirm: Callable[[str], None],
        on_ignore: Callable[[str], None],
    ) -> None:
        params: dict = {"timeout": 2, "allowed_updates": ["message"]}
        if self._last_update_id:
            params["offset"] = self._last_update_id + 1

        resp = self._call("getUpdates", params)
        if not resp or not resp.get("ok"):
            return

        for update in resp.get("result", []):
            self._last_update_id = max(self._last_update_id, update["update_id"])
            msg = update.get("message", {})
            text = (msg.get("text") or "").strip()
            reply_to = (msg.get("reply_to_message") or {}).get("message_id")

            if not text or reply_to is None:
                continue

            analysis_id = self._pending.get(reply_to)
            if analysis_id is None:
                continue

            if "确认" in text:
                logger.info("[Telegram] 收到确认 analysis_id=%s", analysis_id)
                try:
                    on_confirm(analysis_id)
                except Exception as e:
                    logger.error("[Telegram] on_confirm 回调异常: %s", e)
                self.send_text(f"✅ 已触发模拟下单：{analysis_id}")
                del self._pending[reply_to]
            elif "忽略" in text:
                logger.info("[Telegram] 收到忽略 analysis_id=%s", analysis_id)
                try:
                    on_ignore(analysis_id)
                except Exception as e:
                    logger.error("[Telegram] on_ignore 回调异常: %s", e)
                self.send_text(f"⏭️ 已忽略：{analysis_id}")
                del self._pending[reply_to]

    # ── 内部 ─────────────────────────────────────────────────────────────────

    def _call(self, method: str, params: dict) -> Optional[dict]:
        url = _TELEGRAM_API.format(token=self.bot_token, method=method)
        try:
            r = requests.post(url, json=params, timeout=self.timeout)
            return r.json()
        except Exception as e:
            logger.error("[Telegram] API 请求失败 %s: %s", method, e)
            return None

    def test_connection(self) -> bool:
        """Verify bot token by calling getMe."""
        resp = self._call("getMe", {})
        ok = bool(resp and resp.get("ok"))
        if ok:
            name = resp["result"].get("username", "?")
            logger.info("[Telegram] 连接成功，Bot 用户名: @%s", name)
        else:
            logger.warning("[Telegram] 连接失败: %s", resp)
        return ok
