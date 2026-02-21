from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def test_kis_trader_status_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(main.kis_trader_store, "read_snapshot", lambda: None)

    with TestClient(main.app) as client:
        response = client.get("/api/kis/trader/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing"
    assert "enabled" in payload


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
