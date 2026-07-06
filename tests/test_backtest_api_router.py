from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.backtest import BacktestRouteDeps, build_backtest_router
from tradecraft.backtest.engine import BacktestConfig


class _Manager:
    def __init__(self) -> None:
        self.started: dict[str, Any] | None = None
        self.stopped = False

    def status(self) -> dict[str, Any]:
        return {
            "updated_at": "2026-07-01T00:00:00+00:00",
            "job": {"status": "idle"},
            "progress": {},
            "result": None,
        }

    def start(
        self,
        *,
        session_rows: list[dict[str, Any]],
        config: BacktestConfig,
        scenario: str,
        session_source: str,
        emit_interval: int,
    ) -> dict[str, Any]:
        self.started = {
            "session_rows": session_rows,
            "config": config,
            "scenario": scenario,
            "session_source": session_source,
            "emit_interval": emit_interval,
        }
        return {"status": "running", "job_id": "job-1"}

    def stop(self) -> dict[str, Any]:
        self.stopped = True
        return {"ok": True, "detail": "stop requested"}


class _Registry:
    def __init__(self) -> None:
        self.observed: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {"updated_at": "2026-07-01T00:00:00+00:00", "symbol_count": 1}

    def observe_sessions(
        self,
        rows: list[dict[str, Any]],
        source: str,
    ) -> dict[str, Any]:
        self.observed = {"rows": rows, "source": source}
        return {"updated_at": "2026-07-01T00:00:00+00:00", "symbol_count": len(rows)}


@dataclass
class _Fixture:
    manager: _Manager
    registry: _Registry
    client: TestClient


def _fixture() -> _Fixture:
    manager = _Manager()
    registry = _Registry()
    app = FastAPI()
    app.include_router(
        build_backtest_router(
            BacktestRouteDeps(
                require_admin_auth=lambda: None,
                manager=lambda: manager,
                data_registry=lambda: registry,
                list_scenarios=lambda: [
                    {"key": "baseline", "label": "기본"},
                    {"key": "high_vol", "label": "고변동"},
                ],
                load_sessions=lambda: (
                    [
                        {
                            "session_id": "s1",
                            "venue_id": "kr_stock",
                            "strategy_id": "noop_balance",
                            "trade_symbol": "005930",
                        },
                        {
                            "session_id": "s2",
                            "venue_id": "binance",
                            "strategy_id": "noop_short_term",
                            "trade_symbol": "BTC/USDT",
                        },
                    ],
                    "fixture_sessions.json",
                ),
                build_config=lambda payload: BacktestConfig(
                    cycles=int(payload.get("cycles") or 720),
                    speed=float(payload.get("speed") or 120.0),
                ),
                emit_interval=lambda: 2,
            )
        )
    )
    return _Fixture(manager=manager, registry=registry, client=TestClient(app))


def test_backtest_router_serves_status_scenarios_and_data_status() -> None:
    fx = _fixture()

    status = fx.client.get("/api/backtest/status")
    scenarios = fx.client.get("/api/backtest/scenarios")
    data_status = fx.client.get("/api/backtest/data-status")

    assert status.status_code == 200
    assert status.json()["job"]["status"] == "idle"
    assert scenarios.status_code == 200
    assert [row["key"] for row in scenarios.json()["scenarios"]] == [
        "baseline",
        "high_vol",
    ]
    assert data_status.status_code == 200
    assert data_status.json()["symbol_count"] == 1


def test_backtest_start_filters_sessions_and_observes_data_registry() -> None:
    fx = _fixture()

    response = fx.client.post(
        "/api/backtest/start",
        json={"scenario": "high_vol", "cycles": 12, "session_ids": ["s2"]},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "running", "job_id": "job-1"}
    assert fx.manager.started is not None
    assert [row["session_id"] for row in fx.manager.started["session_rows"]] == ["s2"]
    assert fx.manager.started["config"].cycles == 12
    assert fx.manager.started["scenario"] == "high_vol"
    assert fx.manager.started["session_source"] == "fixture_sessions.json"
    assert fx.manager.started["emit_interval"] == 2
    assert fx.registry.observed == {
        "rows": fx.manager.started["session_rows"],
        "source": "fixture_sessions.json",
    }


def test_backtest_start_rejects_empty_filtered_sessions() -> None:
    fx = _fixture()

    response = fx.client.post(
        "/api/backtest/start",
        json={"session_ids": ["missing"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "no backtest sessions selected"
    assert fx.manager.started is None


def test_backtest_stop_delegates_to_manager() -> None:
    fx = _fixture()

    response = fx.client.post("/api/backtest/stop")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "detail": "stop requested"}
    assert fx.manager.stopped is True
