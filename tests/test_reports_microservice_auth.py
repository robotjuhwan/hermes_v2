from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft.reports_api import main as reports_main
from tradecraft.runtime.state_store import RuntimeStateStore


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_v1_health_is_public(monkeypatch) -> None:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")

    with TestClient(reports_main.app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "readiness" in payload
    assert "worker" in payload


def test_v1_reports_status_requires_token(monkeypatch) -> None:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")

    with TestClient(reports_main.app) as client:
        no_header = client.get("/v1/reports/status")
        bad_header = client.get(
            "/v1/reports/status",
            headers=_auth_header("invalid"),
        )

    assert no_header.status_code == 401
    assert bad_header.status_code == 401


def test_v1_reports_status_with_valid_token(monkeypatch) -> None:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(
        reports_main.repository,
        "status",
        lambda: {
            "total_reports": 12,
            "last_updated_at": "2026-02-25T00:00:00+00:00",
            "last_published_at": "2026-02-24",
            "category_counts": {"company_analysis": 9},
            "total_symbols": 4,
            "symbol_last_updated_at": "2026-02-25T00:00:00+00:00",
            "db_path": ".runtime/naver_reports.db",
        },
    )

    with TestClient(reports_main.app) as client:
        response = client.get(
            "/v1/reports/status",
            headers=_auth_header("secret"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["repository"]["total_reports"] == 12
    assert payload["readiness"]["token_count"] == 1


def test_v1_reports_status_accepts_rotating_token_list(monkeypatch) -> None:
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "")
    monkeypatch.setattr(reports_main.settings, "reports_api_tokens", "next, current")
    monkeypatch.setattr(reports_main.repository, "status", lambda: {"total_reports": 1})

    with TestClient(reports_main.app) as client:
        response = client.get(
            "/v1/reports/status",
            headers=_auth_header("current"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["readiness"]["token_count"] == 2


def test_v1_health_exposes_worker_status(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "reports-worker.json"
    RuntimeStateStore(state_path).write_snapshot(
        {
            "status": "ok",
            "cycle": 3,
            "interval_sec": 300,
            "last_success_at": "2026-04-05T00:00:00+00:00",
        }
    )
    monkeypatch.setattr(reports_main.settings, "reports_api_token", "secret")
    monkeypatch.setattr(reports_main.settings, "reports_worker_state_path", str(state_path))
    monkeypatch.setattr(reports_main.settings, "naver_reports_enabled", True)

    with TestClient(reports_main.app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker"]["status"] == "ok"
    assert payload["worker"]["cycle"] == 3
