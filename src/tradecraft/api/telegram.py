from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Header


@dataclass(frozen=True)
class TelegramRouteDeps:
    status: Callable[[], dict[str, Any]]
    validate_webhook_secret: Callable[[str | None], None]
    process_text: Callable[[str, str], Any]


def build_telegram_router(deps: TelegramRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/telegram/status")
    async def telegram_status() -> dict[str, Any]:
        return deps.status()

    @router.post("/api/telegram/webhook")
    async def telegram_webhook(
        payload: dict[str, Any],
        telegram_secret: str | None = Header(
            default=None,
            alias="X-Telegram-Bot-Api-Secret-Token",
        ),
    ) -> dict[str, Any]:
        deps.validate_webhook_secret(telegram_secret)
        message = payload.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id") or "").strip()
        result = await _maybe_await(deps.process_text(text, chat_id))
        return {"ok": True, **result}

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
