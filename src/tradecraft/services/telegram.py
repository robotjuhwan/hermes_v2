from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @property
    def masked_token(self) -> str:
        token = self.bot_token or ""
        if len(token) < 10:
            return ""
        return f"{token[:6]}...{token[-4:]}"


class TelegramBridge:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.last_webhook_message: str = ""

    def status(self, include_sensitive: bool = False) -> dict:
        payload = {
            "ready": self.config.ready,
            "last_webhook_message": self.last_webhook_message,
        }
        if include_sensitive:
            payload["chat_id"] = self.config.chat_id
            payload["masked_token"] = self.config.masked_token
        return payload

    def update_config(self, bot_token: str, chat_id: str) -> dict:
        self.config.bot_token = (bot_token or "").strip()
        self.config.chat_id = (chat_id or "").strip()
        return self.status()

    async def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        if not self.config.bot_token:
            return {"ok": False, "detail": "telegram bot token missing"}

        target_chat_id = (chat_id or self.config.chat_id or "").strip()
        if not target_chat_id:
            return {"ok": False, "detail": "telegram chat_id missing"}

        base = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload: dict[str, object] = {"chat_id": target_chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(base, json=payload)
                data = response.json()
            except Exception as exc:
                return {"ok": False, "detail": f"telegram request failed: {exc}"}

        if response.status_code >= 400:
            return {"ok": False, "detail": data}
        return {"ok": True, "detail": data}

    async def get_updates(self, offset: int | None = None, timeout_sec: int = 15) -> dict:
        if not self.config.ready:
            return {"ok": False, "detail": "telegram config missing", "result": []}

        url = f"https://api.telegram.org/bot{self.config.bot_token}/getUpdates"
        payload: dict[str, object] = {
            "timeout": timeout_sec,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset

        timeout = httpx.Timeout(float(timeout_sec) + 3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(url, json=payload)
                data = response.json()
            except Exception as exc:
                return {"ok": False, "detail": f"telegram getUpdates failed: {exc}", "result": []}

        if response.status_code >= 400:
            return {"ok": False, "detail": data, "result": []}

        updates = data.get("result") or []
        return {"ok": bool(data.get("ok")), "result": updates, "detail": data}
