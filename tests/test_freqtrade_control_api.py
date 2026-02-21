from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def test_freqtrade_strategy_status_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main.freqtrade_process_manager,
        "list_statuses",
        lambda: [
            {
                "bot_id": "spot",
                "label": "Freqtrade Spot",
                "running": False,
                "pid": None,
                "config_path": "x",
                "config_exists": True,
                "executable_path": "y",
                "executable_exists": True,
                "log_path": "z",
            }
        ],
    )
    monkeypatch.setattr(main, "_is_api_reachable", lambda api_url: False)

    with TestClient(main.app) as client:
        response = client.get("/api/freqtrade/strategies")

    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert payload["items"][0]["bot_id"] == "spot"


def test_freqtrade_strategy_start_stop_endpoints(monkeypatch) -> None:
    state = {"running": False}

    def fake_statuses() -> list[dict]:
        return [
            {
                "bot_id": "spot",
                "label": "Freqtrade Spot",
                "running": state["running"],
                "pid": 777 if state["running"] else None,
                "config_path": "x",
                "config_exists": True,
                "executable_path": "y",
                "executable_exists": True,
                "log_path": "z",
            }
        ]

    def fake_start(bot_id: str) -> dict:
        state["running"] = True
        return {
            "bot_id": bot_id,
            "label": "Freqtrade Spot",
            "action": "started",
            "pid": 777,
        }

    def fake_stop(bot_id: str) -> dict:
        state["running"] = False
        return {
            "bot_id": bot_id,
            "label": "Freqtrade Spot",
            "action": "stopped",
            "pid": None,
        }

    monkeypatch.setattr(main.freqtrade_process_manager, "list_statuses", fake_statuses)
    monkeypatch.setattr(main.freqtrade_process_manager, "start", fake_start)
    monkeypatch.setattr(main.freqtrade_process_manager, "stop", fake_stop)
    monkeypatch.setattr(main, "_is_api_reachable", lambda api_url: state["running"])

    async def fake_cleanup(bot_id: str) -> dict:
        return {
            "bot_id": bot_id,
            "label": "Freqtrade Spot",
            "action": "positions_closed",
            "closed_trades": 1,
        }

    monkeypatch.setattr(main, "_close_all_positions_before_stop", fake_cleanup)

    with TestClient(main.app) as client:
        start_response = client.post("/api/freqtrade/strategies/spot/start")
        assert start_response.status_code == 200
        start_payload = start_response.json()
        assert start_payload["items"][0]["running"] is True
        assert start_payload["actions"][0]["action"] == "started"

        stop_response = client.post("/api/freqtrade/strategies/spot/stop")
        assert stop_response.status_code == 200
        stop_payload = stop_response.json()
        assert stop_payload["items"][0]["running"] is False
        assert stop_payload["actions"][1]["action"] == "stopped"


def test_freqtrade_strategy_set_usdt_limit_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main.freqtrade_process_manager,
        "list_statuses",
        lambda: [
            {
                "bot_id": "spot",
                "label": "Freqtrade Spot",
                "running": False,
                "pid": None,
                "config_path": "x",
                "config_exists": True,
                "executable_path": "y",
                "executable_exists": True,
                "log_path": "z",
                "usdt_limit": 150.0,
                "usdt_limit_default": 100.0,
                "usdt_limit_source": "override",
            }
        ],
    )
    monkeypatch.setattr(
        main.freqtrade_process_manager,
        "set_usdt_limit",
        lambda bot_id, usdt_limit: {
            "bot_id": bot_id,
            "label": "Freqtrade Spot",
            "action": "usdt_limit_updated",
            "usdt_limit": usdt_limit,
            "usdt_limit_default": 100.0,
            "usdt_limit_source": "override",
        },
    )
    monkeypatch.setattr(main, "_is_api_reachable", lambda api_url: False)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/freqtrade/strategies/spot/usdt-limit",
            json={"usdt_limit": 150},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["actions"][0]["action"] == "usdt_limit_updated"
