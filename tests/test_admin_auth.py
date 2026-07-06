from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def _set_admin_token(monkeypatch, token: str = "test-admin") -> None:
    monkeypatch.setattr(main.settings, "admin_token", token)
    monkeypatch.setattr(main.settings, "admin_tokens", "")


def _auth_header(token: str = "test-admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_protected_endpoint_requires_configured_admin_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "admin_token", "")
    monkeypatch.setattr(main.settings, "admin_tokens", "")

    with TestClient(main.app) as client:
        response = client.get("/api/dashboard")

    assert response.status_code == 401
    assert response.json()["detail"] == "admin auth required"


def test_protected_endpoint_rejects_bad_admin_token(monkeypatch) -> None:
    _set_admin_token(monkeypatch)

    with TestClient(main.app) as client:
        response = client.get("/api/dashboard", headers=_auth_header("wrong"))

    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_protected_endpoint_accepts_bearer_admin_token(monkeypatch) -> None:
    _set_admin_token(monkeypatch)
    monkeypatch.setattr(main.settings, "upbit_access_key", "")
    monkeypatch.setattr(main.settings, "upbit_secret_key", "")
    monkeypatch.setattr(main.settings, "bithumb_access_key", "")
    monkeypatch.setattr(main.settings, "bithumb_secret_key", "")
    monkeypatch.setattr(main.settings, "binance_spot_api_key", "")
    monkeypatch.setattr(main.settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(main.settings, "binance_futures_api_key", "")
    monkeypatch.setattr(main.settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(main.settings, "kis_primary_app_key", "")
    monkeypatch.setattr(main.settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(main.settings, "kis_primary_account_no", "")
    monkeypatch.setattr(main.settings, "kis_primary_product_code", "")
    monkeypatch.setattr(main.settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(main.settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(main.settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(main.settings, "kis_secondary_product_code", "")

    with TestClient(main.app) as client:
        response = client.get("/api/dashboard", headers=_auth_header())

    assert response.status_code == 200
    assert "venues" in response.json()


def test_protected_endpoint_accepts_admin_token_header(monkeypatch) -> None:
    _set_admin_token(monkeypatch)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/memory/status",
            headers={"X-TradeCraft-Admin-Token": "test-admin"},
        )

    assert response.status_code == 200


def test_market_account_and_kis_manager_are_blocked_without_auth(monkeypatch) -> None:
    _set_admin_token(monkeypatch)

    with TestClient(main.app) as client:
        account = client.get("/api/market/account")
        quotes = client.get("/api/market/quotes")
        pulse_latest = client.get("/api/market/pulse/latest")
        pulse_history = client.get("/api/market/pulse/history")
        judgment_latest = client.get("/api/market/judgments/latest")
        manager = client.post("/api/kis/blocks/manager/run-once")

    assert account.status_code == 401
    assert quotes.status_code == 401
    assert pulse_latest.status_code == 401
    assert pulse_history.status_code == 401
    assert judgment_latest.status_code == 401
    assert manager.status_code == 401


def test_public_health_excludes_private_operational_details(monkeypatch) -> None:
    _set_admin_token(monkeypatch)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        ops = client.get("/api/ops/readiness")

    assert health.status_code == 200
    payload = health.json()
    assert payload == {
        "status": "ok",
        "service": "tradecraft-control",
        "ops_endpoint": "/api/ops/readiness",
        "ops_auth_required": True,
    }
    assert "runner_processes" not in payload
    assert "investment_memory" not in payload
    assert "llm_usage_db_path" not in payload
    assert ops.status_code == 401


def test_report_rag_memory_mutations_are_blocked_without_auth(monkeypatch) -> None:
    _set_admin_token(monkeypatch)

    with TestClient(main.app) as client:
        crawl = client.post("/api/reports/crawl-once")
        report_backfill = client.post("/api/reports/backfill-symbol-links")
        rag = client.post("/api/rag/sync")
        memory = client.post("/api/memory/update/run-once", json={"force": True})
        seed = client.post("/api/memory/seed-current")
        reflections = client.post("/api/memory/reflections/run-due")
        runtime_cleanup = client.post("/api/runtime/storage/cleanup")
        fundamentals_collect = client.post(
            "/api/symbols/fundamentals/collect",
            json={"symbols": ["005930"]},
        )
        review_queue = client.get("/api/portfolio-coach/review-queue")
        review_approve = client.post(
            "/api/portfolio-coach/review-queue/1/approve",
            json={},
        )
        review_reject = client.post(
            "/api/portfolio-coach/review-queue/1/reject",
            json={},
        )
        policy_rules = client.get("/api/memory/policies/rules")
        ops = client.get("/api/ops/readiness")
        runtime_storage = client.get("/api/runtime/storage")
        runtime_status = client.get("/api/runtime/status")
        schedule = client.get("/api/market/judgments/schedule")
        rebalance = client.get("/api/rebalance/kis-status")
        pulse_run = client.post("/api/market/pulse/run-once")
        ops_restart = client.post("/api/ops/restart", json={"keys": ["market_judge"]})
        watchdog = client.get("/api/ops/watchdog/status")
        wiki_rebuild = client.post("/api/wiki/rebuild", json={"scope": "kis"})
        wiki_lint = client.post("/api/wiki/lint", json={"scope": "kis"})

    assert crawl.status_code == 401
    assert report_backfill.status_code == 401
    assert rag.status_code == 401
    assert memory.status_code == 401
    assert seed.status_code == 401
    assert reflections.status_code == 401
    assert runtime_cleanup.status_code == 401
    assert fundamentals_collect.status_code == 401
    assert review_queue.status_code == 401
    assert review_approve.status_code == 401
    assert review_reject.status_code == 401
    assert policy_rules.status_code == 401
    assert ops.status_code == 401
    assert runtime_storage.status_code == 401
    assert runtime_status.status_code == 401
    assert schedule.status_code == 401
    assert rebalance.status_code == 401
    assert pulse_run.status_code == 401
    assert ops_restart.status_code == 401
    assert watchdog.status_code == 401
    assert wiki_rebuild.status_code == 401
    assert wiki_lint.status_code == 401


def test_ops_restart_schedules_allowlisted_runners(monkeypatch) -> None:
    _set_admin_token(monkeypatch)
    calls: list[tuple[list[str], float]] = []

    def fake_restart(keys: list[str], *, delay_sec: float = 0.5) -> dict[str, object]:
        calls.append((keys, delay_sec))
        return {
            "status": "scheduled",
            "keys": keys,
            "supervisor_pid": 1234,
            "delay_sec": delay_sec,
        }

    monkeypatch.setattr(main, "restart_runner_processes", fake_restart)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/ops/restart",
            headers=_auth_header(),
            json={"keys": ["market_judge", "kis_block_trader"]},
        )

    assert response.status_code == 200
    assert response.json()["keys"] == ["market_judge", "kis_block_trader"]
    assert calls == [(["market_judge", "kis_block_trader"], 0.5)]


def test_ops_watchdog_status_returns_admin_status(monkeypatch) -> None:
    _set_admin_token(monkeypatch)

    def fake_watchdog_status(settings) -> dict[str, object]:
        assert settings is main.settings
        return {"status": "ok", "interval_sec": 1800}

    monkeypatch.setattr(main, "watchdog_status", fake_watchdog_status)

    with TestClient(main.app) as client:
        response = client.get("/api/ops/watchdog/status", headers=_auth_header())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "interval_sec": 1800}
