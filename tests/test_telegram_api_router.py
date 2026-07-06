from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tradecraft.api.telegram import TelegramRouteDeps, build_telegram_router


def _client() -> TestClient:
    calls: list[dict[str, Any]] = []

    def _status() -> dict[str, Any]:
        calls.append({"method": "status"})
        return {"ready": True}

    def _validate_secret(secret: str | None) -> None:
        calls.append({"method": "validate_secret", "secret": secret})
        if secret != "webhook-secret":
            raise HTTPException(status_code=401, detail="unauthorized")

    async def _process_text(text: str, chat_id: str) -> dict[str, Any]:
        calls.append({"method": "process_text", "text": text, "chat_id": chat_id})
        return {"handled": True, "sent": True}

    app = FastAPI()
    app.state.calls = calls
    app.include_router(
        build_telegram_router(
            TelegramRouteDeps(
                status=_status,
                validate_webhook_secret=_validate_secret,
                process_text=_process_text,
            )
        )
    )
    return TestClient(app)


def test_telegram_status_delegates_to_bridge_status() -> None:
    with _client() as client:
        response = client.get("/api/telegram/status")
        calls = client.app.state.calls

    assert response.status_code == 200
    assert response.json() == {"ready": True}
    assert calls == [{"method": "status"}]


def test_telegram_webhook_validates_secret_and_delegates_text() -> None:
    with _client() as client:
        response = client.post(
            "/api/telegram/webhook",
            json={"message": {"text": " /help ", "chat": {"id": 123}}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
        )
        calls = client.app.state.calls

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handled": True, "sent": True}
    assert calls == [
        {"method": "validate_secret", "secret": "webhook-secret"},
        {"method": "process_text", "text": "/help", "chat_id": "123"},
    ]


def test_telegram_webhook_rejects_bad_secret_before_processing_text() -> None:
    with _client() as client:
        response = client.post(
            "/api/telegram/webhook",
            json={"message": {"text": "/help", "chat": {"id": 123}}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        calls = client.app.state.calls

    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"
    assert calls == [{"method": "validate_secret", "secret": "wrong"}]
