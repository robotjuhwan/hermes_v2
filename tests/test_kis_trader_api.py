from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def test_kis_trader_status_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(main.kis_trader_store, "read_snapshot", lambda: None)
    monkeypatch.setattr(main.settings, "kis_trader_execute_orders", False)

    with TestClient(main.app) as client:
        response = client.get("/api/kis/trader/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing"
    assert "enabled" in payload
    assert payload["readiness"]["execution_mode"] == "dry_run"
    assert "dry_run_mode" in payload["readiness"]["warnings"]


def test_kis_trader_run_once_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "kis_primary_app_key", "dummy")
    monkeypatch.setattr(main.settings, "kis_primary_app_secret", "dummy")
    monkeypatch.setattr(main.settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(main.settings, "kis_primary_product_code", "01")

    async def fake_run_once() -> dict:
        return {"status": "ok", "orders": []}

    monkeypatch.setattr(main.kis_llm_trader, "run_once", fake_run_once)

    with TestClient(main.app) as client:
        response = client.post("/api/kis/trader/run-once")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["snapshot"]["status"] == "ok"


def test_kis_blocks_status_and_list_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(
        main.kis_block_trader,
        "status",
        lambda: {"status": "ok", "open_block_count": 0, "kill_switch": {"enabled": False}},
    )

    async def fake_snapshot() -> dict:
        return {"status": "ok", "blocks": [], "allocation": {"items": []}}

    monkeypatch.setattr(main.kis_block_trader, "snapshot", fake_snapshot)

    with TestClient(main.app) as client:
        status_response = client.get("/api/kis/blocks/status")
        list_response = client.get("/api/kis/blocks")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "ok"
    assert list_response.status_code == 200
    assert list_response.json()["blocks"] == []


def test_kis_blocks_manager_and_kill_switch_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "kis_primary_app_key", "dummy")
    monkeypatch.setattr(main.settings, "kis_primary_app_secret", "dummy")
    monkeypatch.setattr(main.settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(main.settings, "kis_primary_product_code", "01")

    async def fake_manager() -> dict:
        return {"status": "ok", "actions": {"create_blocks": []}}

    monkeypatch.setattr(main.kis_block_trader, "run_manager_once", fake_manager)
    monkeypatch.setattr(
        main.kis_block_trader,
        "set_kill_switch",
        lambda enabled, reason="": {"enabled": enabled, "reason": reason},
    )

    with TestClient(main.app) as client:
        manager_response = client.post("/api/kis/blocks/manager/run-once")
        kill_response = client.post("/api/kis/blocks/kill-switch", json={"reason": "test"})

    assert manager_response.status_code == 200
    assert manager_response.json()["status"] == "ok"
    assert kill_response.status_code == 200
    assert kill_response.json()["kill_switch"]["enabled"] is True


def test_kis_blocks_adopt_existing_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "kis_primary_app_key", "dummy")
    monkeypatch.setattr(main.settings, "kis_primary_app_secret", "dummy")
    monkeypatch.setattr(main.settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(main.settings, "kis_primary_product_code", "01")

    async def fake_adopt() -> dict:
        return {"status": "ok", "applied": {"adopted": [{"status": "ok"}]}}

    monkeypatch.setattr(main.kis_block_trader, "run_adoption_once", fake_adopt)

    with TestClient(main.app) as client:
        response = client.post("/api/kis/blocks/adopt-existing/run-once")

    assert response.status_code == 200
    assert response.json()["applied"]["adopted"][0]["status"] == "ok"


def test_kis_blocks_order_cancel_endpoint(monkeypatch) -> None:
    async def fake_cancel(order_id: int, reason: str = "") -> dict:
        return {"status": "ok", "order": {"id": order_id}, "reason": reason}

    monkeypatch.setattr(main.kis_block_trader, "cancel_order", fake_cancel)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/kis/blocks/orders/7/cancel",
            json={"reason": "test_cancel"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["order"]["id"] == 7
    assert payload["reason"] == "test_cancel"
