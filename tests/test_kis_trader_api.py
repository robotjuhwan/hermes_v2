from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main
from tradecraft.api.kis_blocks import _compact_kis_blocks_payload


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def test_compact_kis_blocks_payload_drops_zero_quantity_allocation_noise() -> None:
    payload = {
        "status": "ok",
        "allocation": {
            "status": "ok",
            "items": [
                {
                    "symbol": "005930",
                    "name": "005930",
                    "account_qty": 0,
                    "block_qty": 0,
                    "unallocated_qty": 0,
                    "overallocated_qty": 0,
                },
                {
                    "symbol": "360750",
                    "name": "TIGER 미국S&P500",
                    "account_qty": 4,
                    "block_qty": 4,
                    "unallocated_qty": 0,
                    "overallocated_qty": 0,
                },
                {
                    "symbol": "033790",
                    "name": "피노",
                    "account_qty": 0,
                    "block_qty": 0,
                    "unallocated_qty": 2,
                    "overallocated_qty": 0,
                },
            ],
        },
    }

    compact = _compact_kis_blocks_payload(payload, active_only=True)

    assert compact["allocation"]["items"] == [
        {
            "symbol": "360750",
            "name": "TIGER 미국S&P500",
            "account_qty": 4,
            "block_qty": 4,
            "unallocated_qty": 0,
            "overallocated_qty": 0,
        },
        {
            "symbol": "033790",
            "name": "피노",
            "account_qty": 0,
            "block_qty": 0,
            "unallocated_qty": 2,
            "overallocated_qty": 0,
        },
    ]


def test_retired_kis_trader_endpoints_are_removed(monkeypatch) -> None:
    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        status_response = client.get("/api/kis/trader/status", headers=headers)
        run_once_response = client.post("/api/kis/trader/run-once", headers=headers)

    assert status_response.status_code == 404
    assert run_once_response.status_code == 404
    assert not hasattr(main, "kis_llm_trader")


def test_kis_blocks_status_and_list_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(
        main.kis_block_trader,
        "status",
        lambda: {"status": "ok", "open_block_count": 0, "kill_switch": {"enabled": False}},
    )
    monkeypatch.setattr(
        main.investment_memory_service,
        "validation_repair_ops_summary",
        lambda **_: {
            "status": "needs_repair",
            "scope": "kis",
            "backlog_count": 1,
            "top_backlog": [{"discipline_id": "cost_simulation"}],
        },
    )

    async def fake_snapshot() -> dict:
        return {"status": "ok", "blocks": [], "allocation": {"items": []}}

    monkeypatch.setattr(main.kis_block_trader, "snapshot", fake_snapshot)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        status_response = client.get("/api/kis/blocks/status", headers=headers)
        list_response = client.get("/api/kis/blocks", headers=headers)

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "ok"
    assert status_response.json()["validation_repair_ops"]["status"] == "needs_repair"
    assert (
        status_response.json()["validation_repair_ops"]["top_backlog"][0][
            "discipline_id"
        ]
        == "cost_simulation"
    )
    assert list_response.status_code == 200
    assert list_response.json()["blocks"] == []
    assert list_response.json()["validation_repair_ops"]["scope"] == "kis"


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
        headers = _admin_headers(monkeypatch)
        manager_response = client.post("/api/kis/blocks/manager/run-once", headers=headers)
        kill_response = client.post(
            "/api/kis/blocks/kill-switch",
            json={"reason": "test"},
            headers=headers,
        )

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
        response = client.post(
            "/api/kis/blocks/adopt-existing/run-once",
            headers=_admin_headers(monkeypatch),
        )

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
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["order"]["id"] == 7
    assert payload["reason"] == "test_cancel"


def test_kis_block_directive_endpoint(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_directive(
        block_id: str,
        *,
        message: str,
        preferred_horizon: str = "",
        scope: str = "block",
        source: str = "ui",
    ) -> dict:
        captured.update(
            {
                "block_id": block_id,
                "message": message,
                "preferred_horizon": preferred_horizon,
                "scope": scope,
                "source": source,
            }
        )
        return {"status": "ok", "directive": captured}

    monkeypatch.setattr(main.kis_block_trader, "add_user_directive", fake_directive)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/kis/blocks/blk_033790/directive",
            json={
                "message": "오늘 산 주식들은 단기보다는 중기로 다뤄줘.",
                "preferred_horizon": "mid",
            },
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert captured["block_id"] == "blk_033790"
    assert captured["message"] == "오늘 산 주식들은 단기보다는 중기로 다뤄줘."
    assert captured["preferred_horizon"] == "mid"
