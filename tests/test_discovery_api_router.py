from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.discovery import DiscoveryRouteDeps, build_discovery_router


class _FakeDiscoveryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def should_run_for_day(self, trading_day: date) -> bool:
        self.calls.append(
            {"method": "should_run_for_day", "trading_day": trading_day.isoformat()}
        )
        return True

    def latest_context(self, *, limit: int = 10) -> dict[str, Any]:
        self.calls.append({"method": "latest_context", "limit": limit})
        return {"status": "ok", "items": [], "limit": limit}

    async def run_once(
        self,
        *,
        trading_day: date,
        force: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "run_once",
                "trading_day": trading_day.isoformat(),
                "force": force,
            }
        )
        return {"status": "ok", "trading_day": trading_day.isoformat()}


def _client(
    service: _FakeDiscoveryService,
    *,
    kospi_count: int = 5,
    kosdaq_count: int = 5,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_discovery_router(
            DiscoveryRouteDeps(
                require_admin_auth=lambda: None,
                service=lambda: service,
                today=lambda: date(2026, 6, 22),
                config_payload=lambda: {
                    "enabled": True,
                    "kospi_count": kospi_count,
                    "kosdaq_count": kosdaq_count,
                    "exclude_recent_days": 10,
                    "candidate_limit_per_market": 300,
                    "db_path": ".runtime/jue_daily_discovery.db",
                },
            )
        )
    )
    return TestClient(app)


def test_discovery_status_includes_config_latest_and_due_today() -> None:
    service = _FakeDiscoveryService()

    with _client(service) as client:
        response = client.get("/api/discovery/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "config": {
            "enabled": True,
            "kospi_count": 5,
            "kosdaq_count": 5,
            "exclude_recent_days": 10,
            "candidate_limit_per_market": 300,
            "db_path": ".runtime/jue_daily_discovery.db",
        },
        "latest": {"status": "ok", "items": [], "limit": 10},
        "due_today": True,
        "coverage": {
            "kospi_count": 5,
            "kosdaq_count": 5,
            "candidate_limit_per_market": 300,
        },
    }
    assert service.calls == [
        {"method": "should_run_for_day", "trading_day": "2026-06-22"},
        {"method": "latest_context", "limit": 10},
    ]


def test_discovery_latest_limit_tracks_configured_sampling_width() -> None:
    service = _FakeDiscoveryService()

    with _client(service, kospi_count=30, kosdaq_count=30) as client:
        status_response = client.get("/api/discovery/status")
        latest_response = client.get("/api/discovery/latest")

    assert status_response.status_code == 200
    assert latest_response.status_code == 200
    assert status_response.json()["latest"]["limit"] == 60
    assert latest_response.json()["limit"] == 60
    assert service.calls == [
        {"method": "should_run_for_day", "trading_day": "2026-06-22"},
        {"method": "latest_context", "limit": 60},
        {"method": "latest_context", "limit": 60},
    ]


def test_discovery_run_once_uses_payload_trading_day_and_force() -> None:
    service = _FakeDiscoveryService()

    with _client(service) as client:
        response = client.post(
            "/api/discovery/run-once",
            json={"trading_day": "2026-06-23", "force": True},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "trading_day": "2026-06-23"}
    assert service.calls == [
        {
            "method": "run_once",
            "trading_day": "2026-06-23",
            "force": True,
        }
    ]


def test_discovery_run_once_rejects_bad_trading_day() -> None:
    service = _FakeDiscoveryService()

    with _client(service) as client:
        response = client.post(
            "/api/discovery/run-once",
            json={"trading_day": "not-a-day"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "trading_day must be YYYY-MM-DD"
    assert service.calls == []
