from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.rebalance import RebalanceRouteDeps, build_rebalance_router


def _client(
    *,
    payload: dict[str, Any] | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_rebalance_router(
            RebalanceRouteDeps(
                require_admin_auth=lambda: None,
                kis_rebalance_status=lambda: payload or {"status": "ok", "rebalance": True},
            )
        )
    )
    return TestClient(app)


def test_rebalance_kis_status_delegates_to_payload_builder() -> None:
    with _client() as client:
        response = client.get("/api/rebalance/kis-status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "rebalance": True}


def test_rebalance_router_does_not_serve_retired_kis_trader_routes() -> None:
    with _client() as client:
        status = client.get("/api/kis/trader/status")
        run_once = client.post("/api/kis/trader/run-once")

    assert status.status_code == 404
    assert run_once.status_code == 404
