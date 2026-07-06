from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.helper import HelperRouteDeps, build_helper_router


def test_helper_ask_delegates_payload_to_helper_service() -> None:
    calls: list[dict[str, Any]] = []

    async def _ask(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {
            "status": "ok",
            "query": payload["query"],
            "answer": "근거 기반 답변",
        }

    app = FastAPI()
    app.include_router(
        build_helper_router(
            HelperRouteDeps(
                require_admin_auth=lambda: None,
                ask=_ask,
            )
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/helper/ask",
            json={"query": "삼성전자 근거", "limit": 5},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "query": "삼성전자 근거",
        "answer": "근거 기반 답변",
    }
    assert calls == [{"query": "삼성전자 근거", "limit": 5}]
