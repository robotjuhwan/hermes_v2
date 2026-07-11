from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from tradecraft import main


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


class _FakeDailyDiscoveryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def latest_context(self, *, limit: int = 10) -> dict[str, Any]:
        self.calls.append({"method": "latest_context", "limit": limit})
        return {
            "status": "ok",
            "trading_day": "2026-05-21",
            "summary": {"selected_count": 10, "analyzed_count": 10},
            "items": [],
            "block_candidates": [],
        }

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
        return {
            "status": "ok",
            "trading_day": trading_day.isoformat(),
            "selected_count": 10,
            "analyzed_count": 10,
            "summary": {"block_candidate_count": 1},
            "results": [],
        }


def test_daily_discovery_endpoints_require_admin_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")

    with TestClient(main.app) as client:
        status = client.get("/api/discovery/status")
        latest = client.get("/api/discovery/latest")
        run_once = client.post("/api/discovery/run-once")

    assert status.status_code == 401
    assert latest.status_code == 401
    assert run_once.status_code == 401


def test_daily_discovery_status_shape_with_admin_token(monkeypatch) -> None:
    fake = _FakeDailyDiscoveryService()
    monkeypatch.setattr(main, "daily_discovery_service", fake)
    expected_limit = min(
        max(
            int(main.settings.daily_discovery_kospi_count)
            + int(main.settings.daily_discovery_kosdaq_count)
            + int(main.settings.daily_discovery_etf_count),
            10,
        ),
        120,
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/discovery/status",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["config"]["kospi_count"] == main.settings.daily_discovery_kospi_count
    assert payload["config"]["etf_count"] == main.settings.daily_discovery_etf_count
    assert payload["latest"]["trading_day"] == "2026-05-21"
    assert fake.calls == [{"method": "latest_context", "limit": expected_limit}]


def test_daily_discovery_run_once_uses_payload_day_and_force(monkeypatch) -> None:
    fake = _FakeDailyDiscoveryService()
    monkeypatch.setattr(main, "daily_discovery_service", fake)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/discovery/run-once",
            json={"trading_day": "2026-05-20", "force": True},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["analyzed_count"] == 10
    assert fake.calls == [
        {
            "method": "run_once",
            "trading_day": "2026-05-20",
            "force": True,
        }
    ]


def test_daily_discovery_run_once_rejects_bad_day(monkeypatch) -> None:
    fake = _FakeDailyDiscoveryService()
    monkeypatch.setattr(main, "daily_discovery_service", fake)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/discovery/run-once",
            json={"trading_day": "bad-date"},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "trading_day must be YYYY-MM-DD"
    assert fake.calls == []
