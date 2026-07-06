from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.portfolio import (
    PortfolioCoachRouteDeps,
    build_portfolio_coach_router,
)


def _client(store: Any, send_message: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_portfolio_coach_router(
            PortfolioCoachRouteDeps(
                require_admin_auth=lambda: None,
                list_advice_messages=store.list_advice_messages,
                get_advice_message=store.get_advice_message,
                update_message_status=store.update_message_status,
                send_message=send_message,
            )
        )
    )
    return TestClient(app)


def test_portfolio_coach_review_queue_lists_advice_messages() -> None:
    calls: list[tuple[str, int]] = []

    class _Store:
        def list_advice_messages(self, *, status: str, limit: int) -> list[dict[str, Any]]:
            calls.append((status, limit))
            return [{"message_id": 7, "status": status, "message_md": "hello"}]

        def get_advice_message(self, message_id: int) -> dict[str, Any] | None:
            raise AssertionError("not used")

        def update_message_status(self, **_: Any) -> bool:
            raise AssertionError("not used")

    async def _send_message(_: str) -> dict[str, Any]:
        raise AssertionError("not used")

    with _client(_Store(), _send_message) as client:
        response = client.get(
            "/api/portfolio-coach/review-queue?status=ready&limit=12"
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "count": 1,
        "items": [{"message_id": 7, "status": "ready", "message_md": "hello"}],
    }
    assert calls == [("ready", 12)]


def test_portfolio_coach_review_approve_sends_message_and_marks_status() -> None:
    updates: list[dict[str, Any]] = []
    sent_messages: list[str] = []

    class _Store:
        def list_advice_messages(self, *, status: str, limit: int) -> list[dict[str, Any]]:
            raise AssertionError("not used")

        def get_advice_message(self, message_id: int) -> dict[str, Any] | None:
            assert message_id == 101
            return {"message_id": 101, "message_md": "send this"}

        def update_message_status(self, **kwargs: Any) -> bool:
            updates.append(kwargs)
            return True

    async def _send_message(message: str) -> dict[str, Any]:
        sent_messages.append(message)
        return {"ok": True}

    with _client(_Store(), _send_message) as client:
        response = client.post(
            "/api/portfolio-coach/review-queue/101/approve",
            json={"review_note": "looks good"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message_id": 101, "sent": True}
    assert sent_messages == ["send this"]
    assert updates == [
        {
            "message_id": 101,
            "status": "sent",
            "review_note": "looks good",
        }
    ]


def test_portfolio_coach_review_reject_marks_message_rejected() -> None:
    updates: list[dict[str, Any]] = []

    class _Store:
        def list_advice_messages(self, *, status: str, limit: int) -> list[dict[str, Any]]:
            raise AssertionError("not used")

        def get_advice_message(self, message_id: int) -> dict[str, Any] | None:
            raise AssertionError("not used")

        def update_message_status(self, **kwargs: Any) -> bool:
            updates.append(kwargs)
            return True

    async def _send_message(_: str) -> dict[str, Any]:
        raise AssertionError("not used")

    with _client(_Store(), _send_message) as client:
        response = client.post(
            "/api/portfolio-coach/review-queue/202/reject",
            json={"review_note": "skip"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message_id": 202, "updated": True}
    assert updates == [
        {
            "message_id": 202,
            "status": "rejected",
            "review_note": "skip",
        }
    ]


def test_portfolio_coach_review_approve_rejects_missing_or_empty_messages() -> None:
    class _Store:
        def list_advice_messages(self, *, status: str, limit: int) -> list[dict[str, Any]]:
            raise AssertionError("not used")

        def get_advice_message(self, message_id: int) -> dict[str, Any] | None:
            if message_id == 1:
                return None
            return {"message_id": message_id, "message_md": "   "}

        def update_message_status(self, **_: Any) -> bool:
            raise AssertionError("not used")

    async def _send_message(_: str) -> dict[str, Any]:
        raise AssertionError("not used")

    with _client(_Store(), _send_message) as client:
        missing = client.post(
            "/api/portfolio-coach/review-queue/1/approve",
            json={},
        )
        empty = client.post(
            "/api/portfolio-coach/review-queue/2/approve",
            json={},
        )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "message not found"
    assert empty.status_code == 400
    assert empty.json()["detail"] == "empty message"
