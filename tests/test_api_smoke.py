import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import tradecraft.main as tradecraft_main
from tradecraft.api import ops_payloads
from tradecraft.api.ops_payloads import build_llm_operational_status
from tradecraft.main import (
    app,
    research_reader,
    binance,
    bithumb,
    fx_rates,
    kis_primary,
    kis_secondary,
    settings,
    upbit,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.jue_lifecycle import JueLifecycleRepository
from tradecraft.services.live_edge import LiveEdgeRepository
from tradecraft.services.llm_usage import LLMUsageRepository
from tradecraft.services.trading_validation import TradingValidationRepository


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


@pytest.fixture(autouse=True)
def _mock_fx_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(tradecraft_main.telegram.config, "bot_token", "")

    async def fake_get_snapshot() -> dict:
        return {
            "usdt_krw": 1400.0,
            "usd_krw": 1350.0,
            "usdt_source": "test",
            "usd_source": "test",
            "status": "ok",
            "fetched_at": "2026-02-15T00:00:00+00:00",
        }

    monkeypatch.setattr(fx_rates, "get_snapshot", fake_get_snapshot)


def test_health_and_dashboard(monkeypatch) -> None:
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "service": "tradecraft-control",
            "ops_endpoint": "/api/ops/readiness",
            "ops_auth_required": True,
        }

        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert payload["status"] == "ok"
        assert payload["updated_at"]
        assert "venues" in payload
        assert "fx" in payload
        assert payload["fx"]["status"] == "ok"
        assert "portfolio_total_krw" in payload
        assert "sessions" in payload
        assert len(payload["venues"]) >= 4
        assert all("assets" in venue for venue in payload["venues"])
        venue_ids = {venue["id"] for venue in payload["venues"]}
        assert all("venue_id" in session for session in payload["sessions"])
        assert all(session["venue_id"] in venue_ids for session in payload["sessions"])
        assert any(session["mode"] == "short_term" for session in payload["sessions"])
        assert any(
            session["mode"] == "mid_long_term" for session in payload["sessions"]
        )

        ops = client.get("/api/ops/readiness", headers=_admin_headers(monkeypatch))
        assert ops.status_code == 200
        assert "processes" in ops.json()
        assert "memory" in ops.json()
        assert "market_judge" in ops.json()
        assert "market_pulse" in ops.json()
        assert "binance_block_trader" in ops.json()
        assert "crypto_market_research" in ops.json()
        assert "crypto_alpha" in ops.json()


def test_live_authority_endpoint_requires_admin() -> None:
    with TestClient(app) as client:
        response = client.get("/api/live/authority")

    assert response.status_code == 401


def test_live_authority_endpoint_with_admin(monkeypatch) -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/live/authority",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "venues" in payload
    assert "kis" in payload["venues"]
    assert "binance" in payload["venues"]


def test_live_authority_endpoint_surfaces_repair_execution(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "live_evaluator.json"
    RuntimeStateStore(state_path).write_snapshot(
        {
            "repair_execution": {
                "version": "validation_repair_execution_v1",
                "status": "queued",
                "m1_execution_posture": "sequential_priority_queue",
                "actions": [
                    {
                        "venue": "binance",
                        "discipline_id": "walk_forward_analysis",
                        "priority": "p0",
                        "status": "queued_external_runner",
                        "validation_mode": "backtest_wfa_oos_rebuild",
                        "scale_up_blocked": True,
                        "live_shadow_required": True,
                        "artifact": "crypto_pattern_lab_runner",
                    }
                ],
            }
        }
    )
    monkeypatch.setattr(settings, "live_evaluator_db_path", str(tmp_path / "edge.db"))
    monkeypatch.setattr(
        settings,
        "live_performance_db_path",
        str(tmp_path / "performance.db"),
    )
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(tmp_path / "validation.db"),
    )
    monkeypatch.setattr(settings, "live_evaluator_state_path", str(state_path))

    with TestClient(app) as client:
        response = client.get(
            "/api/live/authority",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    repair = response.json()["venues"]["binance"]["repair_execution"]
    assert repair["status"] == "queued"
    assert repair["queued_count"] == 1
    assert repair["actions"][0]["discipline_id"] == "walk_forward_analysis"
    assert repair["actions"][0]["validation_mode"] == "backtest_wfa_oos_rebuild"
    assert repair["actions"][0]["live_shadow_required"] is True


def test_live_authority_endpoint_applies_trading_validation_gate(
    monkeypatch,
    tmp_path,
) -> None:
    edge_path = tmp_path / "live_edge.db"
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(settings, "live_evaluator_db_path", str(edge_path))
    monkeypatch.setattr(settings, "trading_validation_db_path", str(validation_path))
    monkeypatch.setattr(settings, "trading_validation_max_age_sec", 864000000)
    edge_repo = LiveEdgeRepository(edge_path)
    edge_repo.upsert_scorecard(
        venue="binance",
        strategy_family="futures_momentum",
        evidence_key="verified-edge",
        scorecard={
            "strategy_revision_id": "jue_edge_repair_v1",
            "sample_count": 30,
            "expectancy_pct": 0.8,
            "win_rate": 58.0,
            "rule_follow_rate": 92.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -2.0,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
        },
    )
    validation_repo = TradingValidationRepository(validation_path)
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-blocked",
            "venue": "binance",
            "scope": "live",
            "strategy_revision_id": "jue_edge_repair_v1",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 70.0,
                "readiness": "blocked_by_validation",
                "pass_count": 12,
                "warn_count": 4,
                "fail_count": 1,
                "missing_count": 2,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/live/authority",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    venue = response.json()["venues"]["binance"]
    assert venue["validation_gate"]["status"] == "blocked_by_validation"
    assert venue["allow_scale_up"] is False
    assert venue["max_budget_multiplier"] == pytest.approx(0.25)
    assert venue["validation_gate"]["applied_max_budget_multiplier"] == (
        pytest.approx(0.5)
    )
    assert venue["lane_authority"]["max_budget_multiplier"] == pytest.approx(0.25)
    assert venue["lane_authority"]["lane_actions"][
        "futures_momentum:verified-edge"
    ]["applied_max_budget_multiplier"] == (
        pytest.approx(0.25)
    )
    assert venue["validation_gate"]["discipline_count"] == 19
    assert venue["validation_gate"]["discipline_matrix"]["actual_count"] == 19
    assert venue["validation_gate"]["discipline_matrix"]["expected_count"] == 19
    assert venue["validation_gate"]["validation_passport"] == {
        "version": "trading_validation_passport_v1",
        "status": "blocked_by_validation",
        "readiness": "blocked_by_validation",
        "score": 70.0,
        "expected_count": 19,
        "actual_count": 19,
        "row_detail_count": 0,
        "row_detail_complete": False,
        "is_complete": True,
        "pass_count": 12,
        "warn_count": 4,
        "fail_count": 1,
        "missing_count": 2,
        "requires_revalidation": True,
    }


def test_live_authority_endpoint_surfaces_pending_active_revision_blocks(
    monkeypatch,
    tmp_path,
) -> None:
    revision_id = "jue_edge_repair_v1"
    edge_path = tmp_path / "live_edge.db"
    performance_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    block_path = tmp_path / "kis_blocks.db"
    monkeypatch.setattr(settings, "jue_strategy_revision_id", revision_id)
    monkeypatch.setattr(settings, "live_evaluator_db_path", str(edge_path))
    monkeypatch.setattr(settings, "live_performance_db_path", str(performance_path))
    monkeypatch.setattr(settings, "trading_validation_db_path", str(validation_path))
    monkeypatch.setattr(settings, "trading_validation_max_age_sec", 864000000)
    monkeypatch.setattr(settings, "kis_block_trader_db_path", str(block_path))
    monkeypatch.setattr(
        settings,
        "binance_block_trader_db_path",
        str(tmp_path / "missing_binance_blocks.db"),
    )
    monkeypatch.setattr(
        settings,
        "live_evaluator_state_path",
        str(tmp_path / "live_evaluator.json"),
    )

    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "kis-active-revision-pending",
            "venue": "kis",
            "scope": "live",
            "strategy_revision_id": revision_id,
            "computed_at": "2026-06-17T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 7.89,
                "readiness": "blocked_by_validation",
                "pass_count": 0,
                "warn_count": 3,
                "fail_count": 0,
                "missing_count": 16,
                "hard_blocking_count": 3,
            },
        }
    )
    with sqlite3.connect(block_path) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO blocks (
                block_id, symbol, status, created_by, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "pending-mid",
                    "005930",
                    "open",
                    "llm",
                    json.dumps(
                        {
                            "strategy_revision_id": revision_id,
                            "horizon": "mid",
                        }
                    ),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:10:00+00:00",
                ),
                (
                    "pending-etf",
                    "069500",
                    "proposed",
                    "llm",
                    json.dumps(
                        {
                            "strategy_revision_id": revision_id,
                            "horizon": "core_etf",
                            "name": "KODEX 200",
                        }
                    ),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:11:00+00:00",
                ),
                (
                    "closed-ignored",
                    "005930",
                    "closed",
                    "llm",
                    json.dumps({"strategy_revision_id": revision_id}),
                    "2026-06-16T00:00:00+00:00",
                    "2026-06-16T01:00:00+00:00",
                ),
            ],
        )

    with TestClient(app) as client:
        response = client.get(
            "/api/live/authority",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    venue = response.json()["venues"]["kis"]
    evidence = venue["active_revision_evidence"]
    assert evidence["status"] == "active_revision_samples_pending_close"
    assert evidence["authority_posture"] == "small_probe_until_pending_blocks_close"
    assert evidence["pending_block_count"] == 2
    assert evidence["pending_block_lane_counts"] == {"core_etf": 1, "mid": 1}
    assert venue["pending_active_revision_blocks"]["pending_block_status_counts"] == {
        "open": 1,
        "proposed": 1,
    }


def test_dashboard_surfaces_fx_error_without_static_fallback(monkeypatch) -> None:
    async def fail_get_snapshot() -> dict:
        raise RuntimeError("fx upstream offline")

    monkeypatch.setattr(fx_rates, "get_snapshot", fail_get_snapshot)
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))

    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["fx"]["status"] == "error"
    assert payload["fx"]["usdt_krw"] is None
    assert payload["fx"]["usd_krw"] is None
    assert "fx upstream offline" in payload["fx"]["error_message"]
    assert any(
        event["type"] == "fx" and "환율 조회 실패" in event["message"]
        for event in payload["events"]
    )
    assert not any(
        event["type"] == "fx" and "fallback" in event["message"].lower()
        for event in payload["events"]
    )


def test_dashboard_missing_credentials_do_not_expose_mock_balances(monkeypatch) -> None:
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))

    assert dashboard.status_code == 200
    payload = dashboard.json()
    venues = {row["id"]: row for row in payload["venues"]}
    for venue_id in ("upbit", "bithumb", "binance", "binance_futures", "kr_stock", "us_stock"):
        assert venues[venue_id]["assets"] == []
        assert venues[venue_id]["status"] == "not_configured"
    assert payload["portfolio_total_krw"] == 0
    assert not any("mock" in event["message"].lower() for event in payload["events"])


def test_ops_readiness_includes_binance_block_trader(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "binance_block_trader_enabled", True)
    monkeypatch.setattr(settings, "binance_block_trader_execute_spot_orders", False)
    monkeypatch.setattr(settings, "binance_block_trader_execute_futures_orders", False)
    monkeypatch.setattr(settings, "binance_block_trader_execute_upbit_orders", False)
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "binance_block_trader_account_risk_pct", 0.25)
    monkeypatch.setattr(settings, "binance_block_trader_max_symbol_exposure_pct", 25.0)
    monkeypatch.setattr(settings, "binance_block_trader_min_reward_risk", 1.3)

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "binance_block_trader" in payload
    assert payload["binance_block_trader"]["execution"]["spot_mode"] == "paper"
    assert payload["binance_block_trader"]["execution"]["futures_mode"] == "paper"
    assert payload["binance_block_trader"]["execution"]["upbit_spot_mode"] == "paper"
    assert payload["binance_block_trader"]["risk"]["account_risk_pct"] == 0.25
    assert payload["binance_block_trader"]["risk"]["min_reward_risk"] == 1.3


def test_ops_readiness_includes_codex_native_status(monkeypatch, tmp_path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    headers = _admin_headers(monkeypatch)
    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    monkeypatch.setattr(settings, "binance_block_trader_llm_model", "missing-model")
    monkeypatch.setattr(
        tradecraft_main.binance_manager_codex_runtime.config,
        "model",
        "missing-model",
    )
    CodexNativeStore(settings.codex_native_thread_db_path).record_runtime_event(
        component="research_ask",
        operation="helper_ask",
        workflow_id="",
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        status="error",
        error_message="sdk missing",
        detail={},
    )
    calls = {"account": 0, "models": 0}

    async def fake_account_check() -> dict:
        calls["account"] += 1
        CodexNativeStore(settings.codex_native_thread_db_path).record_account_check(
            status="ok",
            account_label="u***@example.com",
            detail={"email": "u***@example.com"},
            error_message="",
        )
        return {"status": "ok", "account": {"email": "u***@example.com"}}

    async def fake_model_check() -> dict:
        calls["models"] += 1
        store = CodexNativeStore(settings.codex_native_thread_db_path)
        store.record_model_check(
            model=settings.llm_model,
            available=True,
            detail={"id": settings.llm_model},
            error_message="",
        )
        return {"status": "ok", "models": [settings.llm_model]}

    monkeypatch.setattr(
        tradecraft_main.helper_codex_runtime,
        "check_account",
        fake_account_check,
    )
    monkeypatch.setattr(
        tradecraft_main.helper_codex_runtime,
        "list_models",
        fake_model_check,
    )

    with TestClient(app) as client:
        response = client.get("/api/ops/readiness", headers=headers)
        native = client.get("/api/codex/native/status", headers=headers)
        assert calls == {"account": 0, "models": 0}
        checked = client.post("/api/codex/native/check", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert "codex_native" in payload
    assert payload["codex_native"]["mode"] in {"sdk", "none"}
    assert "thread_mode" in payload["codex_native"]
    assert "models" in payload["codex_native"]
    assert "components" in payload["codex_native"]
    assert "last_error" in payload["codex_native"]
    assert payload["codex_native"]["last_error"]["error_message"] == "sdk missing"
    assert native.status_code == 200
    native_payload = native.json()
    assert native_payload["thread_db_path"] == str(tmp_path / "native_threads.db")
    assert native_payload["latest_account_check"] is None
    assert native_payload["components"]
    assert checked.status_code == 200
    checked_payload = checked.json()
    assert checked_payload["latest_account_check"]["status"] == "ok"
    unavailable = {
        row["model"]: row
        for row in checked_payload["models"]
        if row.get("available") is False
    }
    assert "missing-model" in unavailable
    assert calls == {"account": 1, "models": 1}


def test_codex_native_status_marks_recovered_runtime_error(monkeypatch, tmp_path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    store = CodexNativeStore(settings.codex_native_thread_db_path)
    store.record_runtime_event(
        component="research_ask",
        operation="helper_ask",
        workflow_id="",
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        status="error",
        error_message="sdk missing",
        detail={},
    )
    store.record_turn(
        thread_key="research_ask:helper_ask:2026-06-29",
        thread_id="thread_recovered",
        component="research_ask",
        operation="helper_ask",
        workflow_id="",
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        status="ok",
        latency_ms=120,
        input_hash="in",
        output_schema_hash="out",
        skill_refs=[],
        usage={},
        error_message="",
        result={"content": "{}"},
        thread_read=None,
    )

    payload = tradecraft_main._build_codex_native_status()

    assert payload["last_error"] is None
    assert payload["last_recovered_error"]["component"] == "research_ask"
    assert payload["last_recovered_error"]["error_message"] == "sdk missing"
    assert payload["last_recovered_error"]["recovered_at"]
    assert payload["recent_runtime_events"][0]["status"] == "recovered"
    assert payload["recent_runtime_events"][0]["recovered_at"]


def test_codex_native_status_recovers_installed_sdk_error(monkeypatch, tmp_path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    monkeypatch.setattr(
        tradecraft_main,
        "_codex_native_sdk_import_available",
        lambda: True,
    )
    CodexNativeStore(settings.codex_native_thread_db_path).record_runtime_event(
        component="research_ask",
        operation="helper_ask",
        workflow_id="",
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        status="error",
        error_message="openai-codex Python SDK is not installed; run `pip install openai-codex`",
        detail={},
    )

    payload = tradecraft_main._build_codex_native_status()

    assert payload["last_error"] is None
    assert payload["last_recovered_error"]["component"] == "research_ask"
    assert payload["last_recovered_error"]["recovery_reason"] == "codex_sdk_import_available"
    assert payload["recent_runtime_events"][0]["status"] == "recovered"
    assert (
        payload["recent_runtime_events"][0]["recovery_reason"]
        == "codex_sdk_import_available"
    )


def test_codex_native_status_recovers_from_component_service_success(
    monkeypatch,
    tmp_path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    CodexNativeStore(settings.codex_native_thread_db_path).record_runtime_event(
        component="binance_block_manager",
        operation="",
        workflow_id="binance_cycle",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="error",
        error_message="codex native sdk timed out after 600.0s",
        detail={},
    )
    monkeypatch.setattr(
        tradecraft_main.binance_block_trader,
        "status",
        lambda: {
            "status": "ok",
            "latest_manager_status": "ok",
            "latest_manager_run_at": "2999-01-01T00:00:00+00:00",
        },
    )

    payload = tradecraft_main._build_codex_native_status()

    assert payload["last_error"] is None
    assert payload["last_recovered_error"]["component"] == "binance_block_manager"
    assert (
        payload["last_recovered_error"]["recovery_reason"]
        == "component_service_status_ok"
    )
    assert payload["recent_runtime_events"][0]["status"] == "recovered"
    assert (
        payload["recent_runtime_events"][0]["recovery_reason"]
        == "component_service_status_ok"
    )


def test_codex_native_status_prefers_recent_successful_turn_for_model(
    monkeypatch,
    tmp_path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    store = CodexNativeStore(settings.codex_native_thread_db_path)
    store.record_model_check(
        model="gpt-5.5",
        available=False,
        detail={"source": "component_config"},
        error_message="configured model not returned by Codex SDK models()",
    )
    store.record_turn(
        thread_key="kis_block_manager:manager_run:2026-07-01",
        thread_id="thread_kis",
        component="kis_block_manager",
        operation="manager_run",
        workflow_id="kis_intraday_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="ok",
        latency_ms=120,
        input_hash="in",
        output_schema_hash="out",
        skill_refs=[],
        usage={},
        error_message="",
        result={"content": "{}"},
        thread_read=None,
    )

    payload = tradecraft_main._build_codex_native_status()

    model = next(row for row in payload["models"] if row["model"] == "gpt-5.5")
    assert model["available"] is True
    assert model["availability_source"] == "recent_successful_turn"
    assert model["last_successful_turn_at"]
    assert "error_message" not in model


def test_codex_native_status_omits_inactive_model_checks(
    monkeypatch,
    tmp_path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    monkeypatch.setattr(settings, "llm_model", "gpt-5.5")
    monkeypatch.setattr(settings, "binance_block_trader_llm_model", "gpt-5.5")
    monkeypatch.setattr(settings, "crypto_market_research_llm_model", "gpt-5.5")
    monkeypatch.setattr(settings, "crypto_alpha_llm_model", "gpt-5.5")
    monkeypatch.setattr(tradecraft_main.helper_codex_runtime.config, "model", "gpt-5.5")
    monkeypatch.setattr(
        tradecraft_main.binance_manager_codex_runtime.config,
        "model",
        "gpt-5.5",
    )
    monkeypatch.setattr(
        tradecraft_main.crypto_market_research_codex_runtime.config,
        "model",
        "gpt-5.5",
    )
    store = CodexNativeStore(settings.codex_native_thread_db_path)
    store.record_model_check(
        model="gpt-5.3-codex-spark",
        available=False,
        detail={"source": "component_config"},
        error_message="configured model not returned by Codex SDK models()",
    )
    store.record_model_check(
        model="gpt-5.5",
        available=False,
        detail={"source": "component_config"},
        error_message="configured model not returned by Codex SDK models()",
    )
    store.record_turn(
        thread_key="kis_block_manager:manager_run:2026-07-01",
        thread_id="thread_kis",
        component="kis_block_manager",
        operation="manager_run",
        workflow_id="kis_intraday_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="ok",
        latency_ms=120,
        input_hash="in",
        output_schema_hash="out",
        skill_refs=[],
        usage={},
        error_message="",
        result={"content": "{}"},
        thread_read=None,
    )

    payload = tradecraft_main._build_codex_native_status()

    models = {row["model"]: row for row in payload["models"]}
    assert set(models) == {"gpt-5.5"}
    assert models["gpt-5.5"]["available"] is True
    assert models["gpt-5.5"]["availability_source"] == "recent_successful_turn"


def test_codex_native_status_synthesizes_active_model_from_successful_turn(
    monkeypatch,
    tmp_path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        settings,
        "codex_native_thread_db_path",
        str(tmp_path / "native_threads.db"),
    )
    monkeypatch.setattr(settings, "llm_model", "gpt-5.5")
    monkeypatch.setattr(tradecraft_main.helper_codex_runtime.config, "model", "gpt-5.5")
    CodexNativeStore(settings.codex_native_thread_db_path).record_turn(
        thread_key="market_judge:judge:2026-07-01",
        thread_id="thread_market",
        component="market_judge",
        operation="judge",
        workflow_id="kis_market_judge",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="ok",
        latency_ms=120,
        input_hash="in",
        output_schema_hash="out",
        skill_refs=[],
        usage={},
        error_message="",
        result={"content": "{}"},
        thread_read=None,
    )

    payload = tradecraft_main._build_codex_native_status()

    models = {row["model"]: row for row in payload["models"]}
    assert models["gpt-5.5"]["available"] is True
    assert models["gpt-5.5"]["availability_source"] == "recent_successful_turn"
    assert models["gpt-5.5"]["check_source"] == "synthesized_from_recent_turn"


def test_ops_readiness_flags_recent_llm_failures(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")

    monkeypatch.setattr(
        tradecraft_main.kis_block_trader,
        "status",
        lambda: {
            "status": "ok",
            "latest_manager_run_at": "2026-06-02T05:58:39+00:00",
            "latest_manager_status": "error",
            "latest_manager_mode": "error",
            "config": {"manager_interval_sec": 1800},
            "kill_switch": {"enabled": False},
        },
    )
    monkeypatch.setattr(
        tradecraft_main.market_judgment_engine,
        "status",
        lambda: {
            "status": "ok",
            "latest_run_at": "2026-06-02T07:00:00+00:00",
            "latest_run_status": "quotes_only",
            "latest_run_mode": "error",
        },
    )
    monkeypatch.setattr(
        tradecraft_main.market_judgment_engine,
        "schedule",
        lambda: {
            "status": "ok",
            "recent_runs": [
                {
                    "run_at": "2026-06-02T07:00:00+00:00",
                    "status": "quotes_only",
                    "mode": "deterministic",
                },
                {
                    "run_at": "2026-06-02T06:58:32+00:00",
                    "status": "error",
                    "mode": "error",
                    "error_message": "authentication token invalidated",
                },
            ],
        },
    )
    running_since_before_failure = datetime(
        2026,
        6,
        2,
        5,
        0,
        tzinfo=timezone.utc,
    ).timestamp()

    def fake_processes() -> dict:
        return {
            key: {
                "status": "running",
                "direct_alive": True,
                "started_at_epoch": running_since_before_failure,
                "stale_process": False,
            }
            for key in [
                "control",
                "runtime",
                "research",
                "kis_block_trader",
                "binance_block_trader",
                "crypto_market_research",
                "crypto_alpha",
                "investment_memory",
                "live_evaluator",
                "market_judge",
                "market_pulse",
                "watchdog",
            ]
        }

    monkeypatch.setattr(tradecraft_main, "_build_core_runner_processes", fake_processes)

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "kis_block_manager_last_run_failed" in payload["warnings"]
    assert "market_judge_llm_recent_failure" in payload["warnings"]
    assert payload["llm"]["critical"]["kis_block_manager"]["status"] == "error"
    assert payload["llm"]["critical"]["market_judge"]["status"] == "error"


def test_ops_readiness_promotes_jue_wiki_action_reference_gap_warnings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "kis_block_trader_enabled", True)
    monkeypatch.setattr(settings, "binance_block_trader_enabled", True)

    manager_diagnostics = {
        "jue_wiki_action_reference_memory_status": "active",
        "jue_wiki_action_reference_memory_resolution_status": "unresolved",
        "jue_wiki_action_reference_status": "missing",
        "blocker_tags": {"unresolved_jue_wiki_action_reference_memory": 2},
    }
    monkeypatch.setattr(
        tradecraft_main.kis_block_trader,
        "status",
        lambda: {
            "status": "ok",
            "latest_manager_run_at": "2026-06-02T05:58:39+00:00",
            "latest_manager_status": "ok",
            "config": {"manager_interval_sec": 1800},
            "kill_switch": {"enabled": False},
            "manager_runs": [
                {"id": 1, "status": "ok", "diagnostics": manager_diagnostics}
            ],
        },
    )
    monkeypatch.setattr(
        tradecraft_main.binance_block_trader,
        "status",
        lambda: {
            "status": "ok",
            "latest_manager_run_at": "2026-06-02T05:58:39+00:00",
            "latest_manager_status": "ok",
            "manager_runs": [
                {"id": 2, "status": "ok", "diagnostics": manager_diagnostics}
            ],
        },
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "kis_jue_wiki_action_reference_gap_unresolved" in payload["warnings"]
    assert "binance_jue_wiki_action_reference_gap_unresolved" in payload["warnings"]
    assert payload["kis_block_trader"]["wiki_action_reference_gap"]["run_id"] == 1
    assert payload["binance_block_trader"]["wiki_action_reference_gap"]["run_id"] == 2
    action_ids = [row["id"] for row in payload["remediation_actions"]]
    assert "run_jue_wiki_action_reference_reflection" in action_ids


def test_llm_operational_status_marks_failures_stale_after_runner_restart() -> None:
    restarted_at = datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc).timestamp()

    payload = build_llm_operational_status(
        block_status={
            "latest_manager_run_at": "2026-06-02T05:58:39+00:00",
            "latest_manager_status": "error",
            "latest_manager_mode": "error",
        },
        market_schedule={
            "recent_runs": [
                {
                    "run_at": "2026-06-02T06:58:32+00:00",
                    "status": "error",
                    "mode": "error",
                    "error_message": "authentication token invalidated",
                }
            ]
        },
        processes={
            "kis_block_trader": {"started_at_epoch": restarted_at},
            "market_judge": {"started_at_epoch": restarted_at},
        },
        configured=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        native_mode="sdk",
    )

    critical = payload["critical"]
    assert critical["kis_block_manager"]["status"] == "error"
    assert critical["kis_block_manager"]["stale_after_restart"] is True
    assert critical["market_judge"]["status"] == "error"
    assert critical["market_judge"]["stale_after_restart"] is True


def test_ops_semantic_checks_flag_user_visible_stale_failures(
    monkeypatch,
    tmp_path,
) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    binance_db = tmp_path / "binance_blocks.db"
    for db_path in (kis_db, binance_db):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE blocks (
                    block_id TEXT,
                    status TEXT,
                    closed_at TEXT,
                    updated_at TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO blocks VALUES (
                    'closed_block',
                    'closed',
                    '2026-06-10T00:00:00+00:00',
                    '2026-06-10T00:00:00+00:00',
                    '2026-06-09T23:00:00+00:00'
                )
                """
            )
    state_path = tmp_path / "research.json"
    state_path.write_text("{}", encoding="utf-8")
    old_epoch = datetime(2026, 6, 8, tzinfo=timezone.utc).timestamp()
    os.utime(state_path, (old_epoch, old_epoch))

    monkeypatch.setattr(settings, "kis_block_trader_db_path", str(kis_db))
    monkeypatch.setattr(settings, "binance_block_trader_db_path", str(binance_db))
    monkeypatch.setattr(settings, "naver_reports_enabled", True)
    monkeypatch.setattr(settings, "naver_reports_interval_sec", 3600)
    monkeypatch.setattr(settings, "research_state_path", str(state_path))

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={
            "status": "ok",
            "latest_reflection_at": "2026-06-08T00:00:00+00:00",
        },
        reports_status={"last_updated_at": "2026-06-08T00:00:00+00:00"},
        llm_usage_payload={
            "today": {
                "by_component": [
                    {
                        "component": "kis_block_manager",
                        "call_count": 4,
                        "error_count": 2,
                        "max_input_chars": 320_000,
                        "avg_input_chars": 210_000,
                    }
                ]
            }
        },
    )

    assert "block_reflections_lagging" in payload["warnings"]
    assert "reports_db_stale" in payload["warnings"]
    assert "research_runner_state_stale" in payload["warnings"]
    assert "llm_prompt_payload_large" in payload["warnings"]
    assert "llm_error_rate_high" in payload["warnings"]
    assert payload["checks"]["block_reflections"]["lagging_venues"] == [
        "kis",
        "binance",
    ]
    assert payload["checks"]["llm_usage"]["prompt_large_components"][0]["component"] == (
        "kis_block_manager"
    )


def test_ops_semantic_checks_marks_long_running_research_state(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "research.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-06-10T19:22:13+00:00",
                "status": "report_collection_running",
                "reports_cycle_timeout_sec": 3600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "naver_reports_enabled", True)
    monkeypatch.setattr(settings, "naver_reports_interval_sec", 3600)
    monkeypatch.setattr(settings, "naver_reports_cycle_timeout_sec", 3600)
    monkeypatch.setattr(settings, "research_state_path", str(state_path))

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={"today": {"by_component": []}},
    )

    runner_state = payload["checks"]["reports"]["runner_state"]
    assert "research_runner_state_stale" in payload["warnings"]
    assert runner_state["status"] == "stale_running"
    assert runner_state["running_stale"] is True


def test_ops_semantic_checks_ignores_legacy_research_state_when_dedicated_reports_alive(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "research.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-06-10T19:22:13+00:00",
                "status": "report_collection_running",
                "reports_cycle_timeout_sec": 3600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "research_enabled", False)
    monkeypatch.setattr(settings, "naver_reports_enabled", True)
    monkeypatch.setattr(settings, "naver_reports_interval_sec", 3600)
    monkeypatch.setattr(settings, "naver_reports_cycle_timeout_sec", 3600)
    monkeypatch.setattr(settings, "research_state_path", str(state_path))

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={"today": {"by_component": []}},
        processes={"naver_reports": {"effective_alive": True}},
    )

    assert "research_runner_state_stale" not in payload["warnings"]
    assert payload["checks"]["reports"]["dedicated_reports_runner_alive"] is True
    assert (
        payload["checks"]["reports"]["legacy_research_state_authoritative"] is False
    )


def test_ops_semantic_checks_suppresses_report_stale_while_collection_running(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "research.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "status": "report_collection_running",
                "reports_cycle_timeout_sec": 3600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "naver_reports_enabled", True)
    monkeypatch.setattr(settings, "naver_reports_interval_sec", 3600)
    monkeypatch.setattr(settings, "naver_reports_cycle_timeout_sec", 3600)
    monkeypatch.setattr(settings, "research_state_path", str(state_path))

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": "2026-06-08T00:00:00+00:00"},
        llm_usage_payload={"today": {"by_component": []}},
    )

    assert "reports_db_stale" not in payload["warnings"]
    assert "research_runner_state_stale" not in payload["warnings"]
    assert payload["checks"]["reports"]["stale"] is True
    assert payload["checks"]["reports"]["runner_state"]["state_status"] == (
        "report_collection_running"
    )


def test_ops_semantic_checks_suppresses_recovered_llm_error_rate() -> None:
    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={
            "today": {
                "by_component": [
                    {
                        "component": "binance_block_manager",
                        "call_count": 19,
                        "error_count": 2,
                        "max_input_chars": 150_000,
                        "avg_input_chars": 140_000,
                        "latest_status": "ok",
                        "latest_error_at": "2026-06-14T00:00:00+00:00",
                        "ok_after_latest_error_count": 7,
                    }
                ]
            }
        },
    )

    assert "llm_error_rate_high" not in payload["warnings"]
    recovered = payload["checks"]["llm_usage"]["recovered_error_components"]
    assert recovered[0]["component"] == "binance_block_manager"
    assert recovered[0]["ok_after_latest_error_count"] == 7


@pytest.mark.parametrize(
    ("session_source", "expected_warning"),
    [
        (
            "safe_default_no_orders (missing: docs/runtime_sessions.btc_sma.json)",
            "runtime_sessions_missing",
        ),
        (
            "safe_default_no_orders (invalid file: docs/runtime_sessions.json)",
            "runtime_sessions_invalid",
        ),
    ],
)
def test_ops_semantic_checks_warns_on_runtime_safe_default_session_source(
    monkeypatch,
    tmp_path,
    session_source,
    expected_warning,
) -> None:
    runtime_state_path = tmp_path / "state.json"
    runtime_state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "runtime": {
                    "execution_mode": "state_writer_no_orders",
                    "executes_orders": False,
                    "session_source": session_source,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_state_path", str(runtime_state_path))
    monkeypatch.setattr(settings, "runtime_write_interval_sec", 5)
    monkeypatch.setattr(settings, "runtime_sessions_path", "docs/runtime_sessions.json")
    monkeypatch.setattr(settings, "naver_reports_enabled", False)
    monkeypatch.setattr(settings, "kis_block_trader_db_path", str(tmp_path / "kis.db"))
    monkeypatch.setattr(
        settings,
        "binance_block_trader_db_path",
        str(tmp_path / "binance.db"),
    )

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={"today": {"by_component": []}},
    )

    assert expected_warning in payload["warnings"]
    runtime_check = payload["checks"]["runtime_state_writer"]["runner_state"]
    assert runtime_check["runtime_session_source"] == session_source
    assert runtime_check["runtime_execution_mode"] == "state_writer_no_orders"
    assert runtime_check["runtime_executes_orders"] is False


def test_ops_semantic_checks_keeps_explicit_runtime_session_source_quiet(
    monkeypatch,
    tmp_path,
) -> None:
    runtime_state_path = tmp_path / "state.json"
    runtime_state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "runtime": {
                    "execution_mode": "state_writer_no_orders",
                    "executes_orders": False,
                    "session_source": "docs/runtime_sessions.json",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_state_path", str(runtime_state_path))
    monkeypatch.setattr(settings, "runtime_write_interval_sec", 5)
    monkeypatch.setattr(settings, "runtime_sessions_path", "docs/runtime_sessions.json")
    monkeypatch.setattr(settings, "naver_reports_enabled", False)
    monkeypatch.setattr(settings, "kis_block_trader_db_path", str(tmp_path / "kis.db"))
    monkeypatch.setattr(
        settings,
        "binance_block_trader_db_path",
        str(tmp_path / "binance.db"),
    )

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={"today": {"by_component": []}},
    )

    assert "runtime_sessions_missing" not in payload["warnings"]
    assert "runtime_sessions_invalid" not in payload["warnings"]


def test_ops_semantic_checks_suppresses_stale_large_prompt_after_runner_restart() -> None:
    restarted_at = datetime(2026, 6, 16, 7, 0, tzinfo=timezone.utc).timestamp()

    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={
            "today": {
                "by_component": [
                    {
                        "component": "kis_block_manager",
                        "call_count": 2,
                        "error_count": 0,
                        "max_input_chars": 212_930,
                        "avg_input_chars": 212_133,
                        "latest_started_at": "2026-06-16T06:42:09+00:00",
                        "latest_input_chars": 212_930,
                    }
                ]
            }
        },
        processes={
            "kis_block_trader": {"started_at_epoch": restarted_at},
        },
    )

    assert "llm_prompt_payload_large" not in payload["warnings"]
    stale = payload["checks"]["llm_usage"]["stale_prompt_large_components"]
    assert stale[0]["component"] == "kis_block_manager"


def test_ops_semantic_checks_suppresses_recovered_large_prompt_latest_safe() -> None:
    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={
            "today": {
                "by_component": [
                    {
                        "component": "kis_block_manager",
                        "call_count": 16,
                        "error_count": 0,
                        "max_input_chars": 210_122,
                        "avg_input_chars": 183_068,
                        "latest_status": "ok",
                        "latest_started_at": "2026-06-19T01:06:27+00:00",
                        "latest_input_chars": 132_575,
                    }
                ]
            }
        },
        processes={
            "kis_block_trader": {
                "started_at_epoch": datetime(
                    2026, 6, 19, 1, 4, tzinfo=timezone.utc
                ).timestamp()
            },
        },
    )

    assert "llm_prompt_payload_large" not in payload["warnings"]
    near_limit = payload["checks"]["llm_usage"]["prompt_near_limit_components"]
    assert near_limit[0]["component"] == "kis_block_manager"
    assert near_limit[0]["latest_input_chars"] == 132_575
    assert payload["checks"]["llm_usage"]["recovered_prompt_large_components"] == []


def test_ops_semantic_checks_suppresses_recovered_on_demand_research_ask() -> None:
    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={
            "today": {
                "by_component": [
                    {
                        "component": "research_ask",
                        "call_count": 67,
                        "error_count": 66,
                        "max_input_chars": 3_000,
                        "avg_input_chars": 2_000,
                        "latest_status": "ok",
                        "latest_error_at": "2026-06-14T00:00:00+00:00",
                        "ok_after_latest_error_count": 1,
                    }
                ]
            }
        },
    )

    assert "llm_error_rate_high" not in payload["warnings"]
    recovered = payload["checks"]["llm_usage"]["recovered_error_components"]
    assert recovered[0]["component"] == "research_ask"
    assert recovered[0]["ok_after_latest_error_count"] == 1


def test_ops_semantic_checks_classifies_disabled_llm_errors_as_inactive(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "research_enabled", False)
    payload = tradecraft_main._build_jue_semantic_checks(
        memory_status={"status": "ok"},
        reports_status={"last_updated_at": datetime.now(timezone.utc).isoformat()},
        llm_usage_payload={
            "today": {
                "by_component": [
                    {
                        "component": "research_pipeline",
                        "call_count": 10,
                        "error_count": 10,
                        "max_input_chars": 3_000,
                        "avg_input_chars": 2_000,
                        "latest_status": "error",
                    },
                    {
                        "component": "kis_legacy_trader",
                        "call_count": 4,
                        "error_count": 4,
                        "max_input_chars": 3_000,
                        "avg_input_chars": 2_000,
                        "latest_status": "error",
                    },
                ]
            }
        },
    )

    assert "llm_error_rate_high" not in payload["warnings"]
    inactive = payload["checks"]["llm_usage"]["inactive_error_components"]
    assert [row["component"] for row in inactive] == [
        "research_pipeline",
        "kis_legacy_trader",
    ]


def test_ops_readiness_flags_critical_disk_space(monkeypatch) -> None:
    class FakeDiskUsage:
        total = 100 * 1024 * 1024 * 1024
        used = 99_500 * 1024 * 1024
        free = 500 * 1024 * 1024

    monkeypatch.setattr(ops_payloads.shutil, "disk_usage", lambda _path: FakeDiskUsage)

    payload = tradecraft_main._build_ops_readiness()

    assert "disk_space_critical" in payload["blockers"]
    assert payload["disk_space"]["status"] == "critical"
    assert payload["disk_space"]["free_bytes"] == 500 * 1024 * 1024
    assert any(
        row["id"] == "cleanup_runtime_storage"
        for row in payload["remediation_actions"]
    )


def test_llm_probe_endpoint_runs_small_bridge_call(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "codex_runtime_timeout_ms", 180000)
    captured: dict[str, object] = {}

    async def fake_complete(payload, timeout_ms=None) -> dict:
        captured["payload"] = payload
        captured["timeout_ms"] = timeout_ms
        return {
            "ok": True,
            "mode": "sdk",
            "content": '{"ok": true, "message": "ready"}',
        }

    monkeypatch.setattr(tradecraft_main.helper_codex_runtime, "complete", fake_complete)

    with TestClient(app) as client:
        unauthorized = client.post("/api/llm/probe")
        response = client.post(
            "/api/llm/probe",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["native_runtime"] is True
    assert payload["mode"] == "sdk"
    assert payload["model"] == tradecraft_main.helper_codex_runtime.resolved_model
    assert "thread_mode" in payload
    assert captured["timeout_ms"] == 180000
    probe_payload = captured["payload"]
    assert isinstance(probe_payload, dict)
    assert probe_payload["telemetry"]["component"] == "llm_probe"


def test_ops_system_metrics_requires_auth_and_returns_light_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")

    with TestClient(app) as client:
        unauthorized = client.get("/api/ops/system-metrics")
        response = client.get(
            "/api/ops/system-metrics",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "unavailable"}
    assert "cache" in payload
    if payload["status"] == "ok":
        assert "cpu_percent" in payload["system"]
        assert "memory" in payload["system"]
        assert "recv_per_sec" in payload["network"]
        assert "processes" in payload["hermes"]


def test_jue_workflows_status_requires_auth_and_returns_packs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")

    with TestClient(app) as client:
        unauthorized = client.get("/api/jue/workflows/status")
        response = client.get(
            "/api/jue/workflows/status",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["workflow_count"] >= 14
    assert payload["workflows"]["kis_intraday_manager"]["workflow_id"] == "kis_intraday_manager"
    assert payload["workflows"]["instant_symbol_analysis"]["workflow_id"] == "instant_symbol_analysis"
    assert payload["workflows"]["kis_symbol_deep_dive"]["workflow_id"] == "kis_symbol_deep_dive"
    assert payload["workflows"]["portfolio_rebalance"]["workflow_id"] == "portfolio_rebalance"
    assert payload["workflows"]["binance_cycle"]["model_policy"]["expected_runtime_model"] == (
        "gpt-5.5"
    )


def test_jue_source_manifest_requires_auth_and_returns_mappings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")

    with TestClient(app) as client:
        unauthorized = client.get("/api/jue/source-manifest")
        response = client.get(
            "/api/jue/source-manifest",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["source_id"] == "financial_services"
    assert payload["mapping_count"] >= 3
    assert payload["manifest"]["mappings"][0]["local_skill_id"]


def test_jue_lifecycle_latest_requires_auth_and_filters_symbol(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    db_path = tmp_path / "memory.db"
    monkeypatch.setattr(settings, "investment_memory_db_path", str(db_path))
    repository = JueLifecycleRepository(db_path)
    repository.upsert_artifact(
        {
            "artifact_id": "deep_005930",
            "artifact_type": "symbol_deep_dive",
            "workflow_id": "kis_symbol_deep_dive",
            "symbol": "005930",
            "title": "삼성전자 딥다이브",
            "summary_md": "메모리로 재사용할 판단 근거",
            "payload": {
                "block_implications": [
                    {"intent": "watch_add", "reason": "valuation reset"}
                ]
            },
            "evidence": [{"source": "report", "id": "r1"}],
        }
    )
    repository.upsert_artifact(
        {
            "artifact_id": "deep_000660",
            "artifact_type": "symbol_deep_dive",
            "workflow_id": "kis_symbol_deep_dive",
            "symbol": "000660",
            "title": "SK하이닉스 딥다이브",
            "summary_md": "다른 종목",
            "evidence": [{"source": "report", "id": "r2"}],
        }
    )

    with TestClient(app) as client:
        unauthorized = client.get("/api/jue/lifecycle/latest")
        response = client.get(
            "/api/jue/lifecycle/latest?symbol=005930",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["items"][0]["artifact_id"] == "deep_005930"
    assert payload["items"][0]["payload"]["block_implications"][0]["intent"] == "watch_add"


def test_ops_readiness_includes_crypto_market_research(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "crypto_market_research_enabled", True)
    monkeypatch.setattr(
        settings,
        "crypto_market_research_kline_intervals",
        "1m:120,5m:96,15m:96,1h:168,4h:180",
    )
    monkeypatch.setattr(settings, "crypto_market_research_regime_enabled", True)
    monkeypatch.setattr(settings, "crypto_market_research_squeeze_guard_enabled", True)

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "crypto_market_research" in payload
    assert payload["crypto_market_research"]["enabled"] is True
    assert payload["crypto_market_research"]["model"] == settings.crypto_market_research_llm_model
    assert payload["crypto_market_research"]["kline_intervals"]["15m"] == 96
    assert payload["crypto_market_research"]["regime_enabled"] is True
    assert payload["crypto_market_research"]["squeeze_guard_enabled"] is True


def test_ops_readiness_includes_crypto_alpha(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "crypto_alpha_enabled", True)
    monkeypatch.setattr(settings, "crypto_alpha_context_limit", 12)
    monkeypatch.setattr(
        settings,
        "crypto_alpha_source_ids",
        "binance_announcements,coinbase_blog",
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "crypto_alpha" in payload
    assert payload["crypto_alpha"]["enabled"] is True
    assert payload["crypto_alpha"]["context_limit"] == 12
    assert payload["crypto_alpha"]["source_ids"] == [
        "binance_announcements",
        "coinbase_blog",
    ]


def test_llm_usage_summary_endpoint(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "llm_usage.db"
    LLMUsageRepository(str(db_path)).record_call(
        component="portfolio_coach",
        operation="build_advice",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=120,
        prompt_tokens=40,
        completion_tokens=10,
        total_tokens=50,
        usage_source="exact",
        input_chars=160,
        output_chars=40,
        started_at="2026-05-13T09:00:00+09:00",
        finished_at="2026-05-13T09:00:01+09:00",
    )
    monkeypatch.setattr(settings, "llm_usage_enabled", True)
    monkeypatch.setattr(settings, "llm_usage_db_path", str(db_path))

    with TestClient(app) as client:
        unauthorized = client.get("/api/llm/usage/summary?trading_day=2026-05-13")
        assert unauthorized.status_code == 401

        headers = _admin_headers(monkeypatch)
        summary = client.get(
            "/api/llm/usage/summary?trading_day=2026-05-13",
            headers=headers,
        )
        assert summary.status_code == 200
        payload = summary.json()
        assert payload["trading_day"] == "2026-05-13"
        assert payload["total"]["call_count"] == 1
        assert payload["total"]["total_tokens"] == 50
        component = payload["by_component"][0]
        assert component["component"] == "portfolio_coach"
        assert component["call_count"] == 1
        assert component["ok_count"] == 1
        assert component["total_tokens"] == 50
        assert component["exact_token_count"] == 1
        assert component["estimated_token_count"] == 0
        assert component["avg_latency_ms"] == 120

        status = client.get("/api/llm/usage/status", headers=headers)
        assert status.status_code == 200
        status_payload = status.json()
        assert status_payload["enabled"] is True
        assert status_payload["db_path"] == str(db_path)
        assert status_payload["today"]["total"]["call_count"] >= 0


def test_llm_usage_legacy_daily_endpoint_maps_days_to_period(
    monkeypatch,
    tmp_path,
) -> None:
    db_path = tmp_path / "llm_usage.db"
    repo = LLMUsageRepository(str(db_path))
    repo.record_call(
        component="kis_block_manager",
        operation="run_manager_once",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=100,
        prompt_tokens=100,
        completion_tokens=25,
        total_tokens=125,
        usage_source="estimated",
        input_chars=500,
        output_chars=100,
        started_at="2026-05-11T09:00:00+09:00",
        finished_at="2026-05-11T09:00:01+09:00",
    )
    repo.record_call(
        component="market_judge",
        operation="run_once",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=200,
        prompt_tokens=40,
        completion_tokens=10,
        total_tokens=50,
        usage_source="estimated",
        input_chars=180,
        output_chars=40,
        started_at="2026-05-13T09:00:00+09:00",
        finished_at="2026-05-13T09:00:01+09:00",
    )
    monkeypatch.setattr(settings, "llm_usage_enabled", True)
    monkeypatch.setattr(settings, "llm_usage_db_path", str(db_path))

    with TestClient(app) as client:
        response = client.get(
            "/api/llm/usage/daily?trading_day=2026-05-13&days=3",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "3d"
    assert payload["start_day"] == "2026-05-11"
    assert payload["end_day"] == "2026-05-13"
    assert payload["total"]["call_count"] == 2
    assert payload["total"]["total_tokens"] == 175


def test_symbol_analysis_api_routes(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _FakeSymbolAnalysisService:
        async def run(
            self,
            symbol: str,
            *,
            trigger: str = "user_request",
            force_collect: bool = True,
        ) -> dict:
            calls.append(("run", {"symbol": symbol, "trigger": trigger, "force_collect": force_collect}))
            return {
                "status": "ok",
                "symbol": symbol,
                "analysis": {
                    "symbol": symbol,
                    "name": "종목테스트",
                    "stance": "watch",
                    "confidence": 0.77,
                    "summary": "테스트 요약",
                },
            }

        def history(self, symbol: str, *, limit: int = 10) -> dict:
            calls.append(("history", {"symbol": symbol, "limit": limit}))
            return {
                "status": "ok",
                "symbol": symbol,
                "limit": limit,
                "items": [{"symbol": symbol, "summary": "old"}],
            }

        def special_watch(self) -> dict:
            calls.append(("special_watch", {}))
            return {
                "status": "ok",
                "count": 1,
                "items": [{"symbol": "033790", "name": "스페셜"}],
            }

    monkeypatch.setattr(
        tradecraft_main,
        "symbol_analysis_service",
        _FakeSymbolAnalysisService(),
    )

    with TestClient(app) as client:
        unauthorized = client.post("/api/symbols/033790/analysis/run")
        assert unauthorized.status_code == 401

        headers = _admin_headers(monkeypatch)
        run_response = client.post(
            "/api/symbols/033790/analysis/run",
            json={"trigger": "test", "force_collect": False},
            headers=headers,
        )
        history_response = client.get(
            "/api/symbols/033790/analysis/history?limit=500",
            headers=headers,
        )
        watch_response = client.get(
            "/api/symbols/special-watch",
            headers=headers,
        )

    assert run_response.status_code == 200
    assert run_response.json()["analysis"]["summary"] == "테스트 요약"
    assert history_response.status_code == 200
    assert history_response.json()["limit"] == 50
    assert watch_response.status_code == 200
    assert watch_response.json()["items"][0]["symbol"] == "033790"
    assert calls == [
        ("run", {"symbol": "033790", "trigger": "test", "force_collect": False}),
        ("history", {"symbol": "033790", "limit": 50}),
        ("special_watch", {}),
    ]


def test_llm_backed_endpoints_require_admin_for_token_spend(monkeypatch) -> None:
    class _FakeStrategyEngine:
        async def build_brief(self, **kwargs) -> dict:
            return {"status": "ok", "brief_mode": "llm" if kwargs.get("use_llm") else "deterministic"}

    monkeypatch.setattr(tradecraft_main, "strategy_intelligence", _FakeStrategyEngine())
    monkeypatch.setattr(tradecraft_main, "_read_strategy_research_feed", lambda: None)

    with TestClient(app) as client:
        helper = client.post("/api/helper/ask", json={"query": "테스트"})
        strategy_llm = client.post(
            "/api/strategy/brief",
            json={"query": "테스트", "use_llm": True},
        )
        strategy_deterministic = client.post(
            "/api/strategy/brief",
            json={"query": "테스트", "use_llm": False},
        )

    assert helper.status_code == 401
    assert strategy_llm.status_code == 401
    assert strategy_deterministic.status_code == 200


def test_reports_backfill_symbol_links_requires_auth_and_syncs_rag(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}
    configured = [
        tradecraft_main.ETFUniverseItem(symbol="069500", name="KODEX 200"),
    ]

    class FakeReportRepository:
        def seed_symbol_directory(self, items: list[dict]) -> int:
            calls["symbol_directory"] = items
            return len(items)

        def backfill_report_symbol_links(
            self,
            *,
            limit: int = 0,
            asset_class: str = "etf",
        ) -> dict:
            calls["backfill"] = {"limit": limit, "asset_class": asset_class}
            return {
                "ok": True,
                "scanned_reports": 3,
                "updated_reports": 2,
                "linked_symbols": ["069500", "102110"],
            }

    def fake_sync_report_rag(**kwargs) -> dict:
        calls["rag_sync"] = kwargs
        return {"status": "ok", "metadata_updated": 2}

    monkeypatch.setattr(
        tradecraft_main,
        "naver_report_repository",
        FakeReportRepository(),
    )
    monkeypatch.setattr(
        tradecraft_main,
        "_configured_etf_universe",
        lambda: configured,
    )
    monkeypatch.setattr(tradecraft_main, "sync_report_rag", fake_sync_report_rag)

    with TestClient(app) as client:
        unauthorized = client.post("/api/reports/backfill-symbol-links")
        assert unauthorized.status_code == 401

        response = client.post(
            "/api/reports/backfill-symbol-links?sync_rag_after=true&limit=5",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["backfill"]["updated_reports"] == 2
    assert payload["rag_sync"]["metadata_updated"] == 2
    assert calls["symbol_directory"] == [
        {
            "symbol": "069500",
            "name": "KODEX 200",
            "market": "ETF",
            "source": "configured_etf",
        }
    ]
    assert calls["backfill"] == {"limit": 5, "asset_class": "etf"}
    rag_call = calls["rag_sync"]
    assert rag_call["repository"] is tradecraft_main.naver_report_repository
    assert rag_call["metadata_only"] is True
    assert rag_call["prune_missing"] is False


def test_etf_research_seed_updates_naver_symbol_directory(monkeypatch) -> None:
    calls: dict[str, object] = {}
    configured = [
        tradecraft_main.ETFUniverseItem(symbol="069500", name="KODEX 200"),
        tradecraft_main.ETFUniverseItem(symbol="102110", name="TIGER 200"),
    ]

    class FakeETFResearchRepository:
        def upsert_universe(self, items) -> None:
            calls["etf_universe"] = items

    class FakeReportRepository:
        def seed_symbol_directory(self, items: list[dict]) -> int:
            calls["symbol_directory"] = items
            return len(items)

    monkeypatch.setattr(tradecraft_main, "_configured_etf_universe", lambda: configured)
    monkeypatch.setattr(
        tradecraft_main,
        "_discover_naver_etf_universe",
        lambda limit=200: [
            tradecraft_main.ETFUniverseItem(
                symbol="360750",
                name="TIGER 미국S&P500",
                category="naver_etf",
                tags=["naver_etf", "tab_4"],
            )
        ],
    )
    monkeypatch.setattr(
        tradecraft_main,
        "naver_report_repository",
        FakeReportRepository(),
    )

    seeded = tradecraft_main._seed_etf_research_universe(FakeETFResearchRepository())

    assert [item.symbol for item in seeded] == ["069500", "102110", "360750"]
    assert calls["etf_universe"] == seeded
    assert calls["symbol_directory"] == [
        {
            "symbol": "069500",
            "name": "KODEX 200",
            "market": "ETF",
            "source": "configured_etf",
        },
        {
            "symbol": "102110",
            "name": "TIGER 200",
            "market": "ETF",
            "source": "configured_etf",
        },
        {
            "symbol": "360750",
            "name": "TIGER 미국S&P500",
            "market": "ETF",
            "source": "naver_etf",
        },
    ]


def test_etf_research_get_routes_do_not_seed_universe(monkeypatch) -> None:
    calls: dict[str, int] = {
        "upsert_universe": 0,
        "seed_symbol_directory": 0,
    }
    configured = [
        tradecraft_main.ETFUniverseItem(symbol="069500", name="KODEX 200"),
    ]

    class FakeETFResearchRepository:
        def upsert_universe(self, items) -> None:
            calls["upsert_universe"] += 1

        def status(self) -> dict:
            return {
                "universe_count": 0,
                "latest_snapshot_at": "",
                "latest_score_at": "",
            }

        def list_universe(self) -> list[dict]:
            return [
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "category": "core",
                    "tags": [],
                }
            ]

        def latest_snapshot(self, symbol: str) -> dict:
            return {"symbol": symbol, "status": "ok"}

        def latest_score(self, symbol: str) -> dict:
            return {"symbol": symbol, "label": "core"}

    class FakeReportRepository:
        def seed_symbol_directory(self, items: list[dict]) -> int:
            calls["seed_symbol_directory"] += 1
            return len(items)

    monkeypatch.setattr(tradecraft_main, "_configured_etf_universe", lambda: configured)
    monkeypatch.setattr(tradecraft_main.settings, "etf_research_auto_collect", False)
    monkeypatch.setattr(
        tradecraft_main,
        "_etf_research_repository",
        lambda: FakeETFResearchRepository(),
    )
    monkeypatch.setattr(
        tradecraft_main,
        "naver_report_repository",
        FakeReportRepository(),
    )

    with TestClient(app) as client:
        headers = _admin_headers(monkeypatch)
        status = client.get("/api/etf/research/status", headers=headers)
        candidates = client.get("/api/etf/research/candidates", headers=headers)

    assert status.status_code == 200
    assert status.json()["configured_universe"] == [
        {
            "symbol": "069500",
            "name": "KODEX 200",
            "category": "core",
            "tags": [],
        }
    ]
    assert candidates.status_code == 200
    assert candidates.json()["items"][0]["symbol"] == "069500"
    assert calls == {"upsert_universe": 0, "seed_symbol_directory": 0}


def test_daily_discovery_status_includes_due_today_and_coverage(monkeypatch) -> None:
    class FakeDailyDiscoveryService:
        def latest_context(self, *, limit: int = 10) -> dict:
            _ = limit
            return {"status": "missing", "items": []}

        def should_run_for_day(self, trading_day) -> bool:
            calls["trading_day"] = trading_day
            return True

    calls: dict[str, object] = {}
    monkeypatch.setattr(settings, "daily_discovery_enabled", True)
    monkeypatch.setattr(settings, "daily_discovery_kospi_count", 7)
    monkeypatch.setattr(settings, "daily_discovery_kosdaq_count", 8)
    monkeypatch.setattr(settings, "daily_discovery_candidate_limit_per_market", 450)
    monkeypatch.setattr(
        tradecraft_main,
        "daily_discovery_service",
        FakeDailyDiscoveryService(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/discovery/status",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["due_today"] is True
    assert payload["coverage"] == {
        "kospi_count": 7,
        "kosdaq_count": 8,
        "candidate_limit_per_market": 450,
    }
    assert calls["trading_day"].isoformat()


def test_runner_status_with_cover_distinguishes_runner_process_from_covered_service() -> None:
    def fake_runner_process_status(key: str) -> dict:
        alive = key == "intelligence"
        return {
            "key": key,
            "label": key,
            "status": "running" if alive else "stopped",
            "alive": alive,
            "pid": 123 if alive else None,
            "pid_file": f".runtime/pids/{key}.pid",
            "pid_file_pid": 123 if alive else None,
            "pid_file_status": "ok" if alive else "missing",
            "matched_count": 1 if alive else 0,
            "matches": [],
        }

    intelligence = tradecraft_main._runner_status_with_cover(
        fake_runner_process_status("intelligence")
    )
    research = tradecraft_main._runner_status_with_cover(
        fake_runner_process_status("research"),
        covered_by=intelligence,
    )
    naver_reports = tradecraft_main._runner_status_with_cover(
        fake_runner_process_status("naver_reports"),
        covered_by=intelligence,
    )

    assert intelligence["direct_alive"] is True
    assert research["direct_alive"] is False
    assert research["effective_alive"] is True
    assert research["status"] == "covered"
    assert naver_reports["direct_alive"] is False
    assert naver_reports["effective_alive"] is True
    assert naver_reports["status"] == "covered"


def test_core_runner_processes_use_light_process_status(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_runner_process_status(
        key: str,
        *,
        include_matches: bool = True,
    ) -> dict:
        calls.append((key, include_matches))
        return {
            "key": key,
            "label": key,
            "status": "running",
            "alive": True,
            "pid": 123,
            "started_at_epoch": None,
            "pid_file": f".runtime/pids/{key}.pid",
            "pid_file_pid": 123,
            "pid_file_status": "ok",
            "matched_count": 1,
            "matches": [],
        }

    monkeypatch.setattr(tradecraft_main, "runner_process_status", fake_runner_process_status)

    processes = tradecraft_main._build_core_runner_processes()

    assert processes["control"]["direct_alive"] is True
    assert calls
    by_key: dict[str, list[bool]] = {}
    for key, include_matches in calls:
        by_key.setdefault(key, []).append(include_matches)
    for key, values in by_key.items():
        assert values[0] is False
        if key in tradecraft_main.DUPLICATE_SCAN_RUNNER_KEYS:
            assert values == [False, True]
        else:
            assert values == [False]


def test_core_runner_processes_omit_disabled_legacy_research(monkeypatch) -> None:
    monkeypatch.setattr(tradecraft_main.settings, "research_enabled", False)

    processes = tradecraft_main._build_core_runner_processes()

    assert "intelligence" not in processes
    assert "research" not in processes
    assert "naver_reports" in processes
    assert "strategy_insights" in processes


def test_ops_readiness_uses_light_market_status(monkeypatch) -> None:
    calls = {"market_status": 0, "pulse_status": 0}

    def fake_market_status() -> dict:
        calls["market_status"] += 1
        return {"status": "ok"}

    def fake_pulse_status() -> dict:
        calls["pulse_status"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(tradecraft_main.market_judgment_engine, "status", fake_market_status)
    monkeypatch.setattr(tradecraft_main.market_pulse_service, "status", fake_pulse_status)

    readiness = tradecraft_main._build_ops_readiness()

    assert readiness["status"] in {"green", "yellow", "red"}
    assert calls == {"market_status": 0, "pulse_status": 0}


def test_ops_readiness_cached_reuses_recent_payload(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_readiness() -> dict:
        calls["count"] += 1
        return {"status": "ok", "call": calls["count"]}

    monkeypatch.setattr(tradecraft_main, "_build_ops_readiness", fake_readiness)
    monkeypatch.setattr(
        tradecraft_main,
        "_ops_readiness_cache_payload",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        tradecraft_main,
        "_ops_readiness_cache_expires_at",
        0.0,
        raising=False,
    )

    first = tradecraft_main._build_ops_readiness_cached(ttl_sec=30)
    second = tradecraft_main._build_ops_readiness_cached(ttl_sec=30)
    refreshed = tradecraft_main._build_ops_readiness_cached(ttl_sec=0)

    assert first == {"status": "ok", "call": 1}
    assert second == first
    assert refreshed == {"status": "ok", "call": 2}
    assert calls["count"] == 2


def test_ops_readiness_cached_default_covers_status_polling_window(monkeypatch) -> None:
    calls = {"count": 0}
    now = {"value": 200.0}

    def fake_readiness() -> dict:
        calls["count"] += 1
        return {"status": "ok", "call": calls["count"]}

    monkeypatch.setattr(tradecraft_main, "_build_ops_readiness", fake_readiness)
    monkeypatch.setattr(tradecraft_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        tradecraft_main,
        "_ops_readiness_cache_payload",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        tradecraft_main,
        "_ops_readiness_cache_expires_at",
        0.0,
        raising=False,
    )

    first = tradecraft_main._build_ops_readiness_cached()
    now["value"] += 10.0
    second = tradecraft_main._build_ops_readiness_cached()

    assert second == first
    assert calls["count"] == 1


def test_ops_readiness_cached_expires_after_slow_build_finishes(monkeypatch) -> None:
    calls = {"count": 0}
    now = {"value": 100.0}

    def fake_readiness() -> dict:
        calls["count"] += 1
        now["value"] += 10.0
        return {"status": "ok", "call": calls["count"]}

    monkeypatch.setattr(tradecraft_main, "_build_ops_readiness", fake_readiness)
    monkeypatch.setattr(tradecraft_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(
        tradecraft_main,
        "_ops_readiness_cache_payload",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        tradecraft_main,
        "_ops_readiness_cache_expires_at",
        0.0,
        raising=False,
    )

    first = tradecraft_main._build_ops_readiness_cached(ttl_sec=5)
    second = tradecraft_main._build_ops_readiness_cached(ttl_sec=5)

    assert first == {"status": "ok", "call": 1}
    assert second == first
    assert calls["count"] == 1


def test_symbol_fundamentals_api_routes(monkeypatch) -> None:
    class FakeSymbolFundamentalsService:
        def __init__(self) -> None:
            self.collected_symbols: list[str] = []

        def latest(self, symbol: str) -> dict:
            return {
                "status": "ok",
                "symbol": symbol,
                "valuation": {"per": 7.0, "pbr": 0.9, "industry_per": 28.9},
                "score": {"label": "undervalued", "undervalued_score": 82},
                "financials": [],
            }

        async def collect_symbols(
            self,
            symbols: list[str],
            *,
            force: bool = False,
        ) -> dict:
            self.collected_symbols = list(symbols)
            return {
                "status": "ok",
                "target_count": len(symbols),
                "force": force,
                "items": [{"symbol": symbol, "status": "ok"} for symbol in symbols],
            }

        def status(self) -> dict:
            return {
                "status": "ok",
                "db_path": ".runtime/symbol_fundamentals.db",
                "total_snapshots": 1,
                "error_count": 0,
                "latest_crawled_at": "2026-05-06T00:00:00+00:00",
            }

    fake_service = FakeSymbolFundamentalsService()
    monkeypatch.setattr(
        tradecraft_main,
        "symbol_fundamentals_service",
        fake_service,
    )

    with TestClient(app) as client:
        latest = client.get("/api/symbols/005930/fundamentals")
        assert latest.status_code == 200
        assert latest.json()["score"]["label"] == "undervalued"

        invalid = client.get("/api/symbols/ABC/fundamentals")
        assert invalid.status_code == 400

        collected = client.post(
            "/api/symbols/fundamentals/collect",
            json={"symbols": ["005930", "000660"], "force": True},
            headers=_admin_headers(monkeypatch),
        )
        assert collected.status_code == 200
        assert collected.json()["target_count"] == 2
        assert fake_service.collected_symbols == ["005930", "000660"]

        status = client.get("/api/reports/status")
        assert status.status_code == 200
        assert status.json()["fundamentals"]["total_snapshots"] == 1


def test_market_pulse_api_routes(monkeypatch) -> None:
    class FakeMarketPulseService:
        def latest(self) -> dict:
            return {"status": "ok", "regime": "risk_on", "score": 72}

        def history(self, *, limit: int = 20) -> dict:
            return {"status": "ok", "items": [{"id": 1}], "limit": limit}

        async def collect(self, **kwargs) -> dict:
            return {"status": "ok", "regime": "rotation", "clock": kwargs.get("clock")}

        def status(self) -> dict:
            return {"status": "ok", "snapshot_count": 1}

    monkeypatch.setattr(
        tradecraft_main,
        "market_pulse_service",
        FakeMarketPulseService(),
    )

    with TestClient(app) as client:
        headers = _admin_headers(monkeypatch)
        latest = client.get("/api/market/pulse/latest", headers=headers)
        assert latest.status_code == 200
        assert latest.json()["regime"] == "risk_on"

        history = client.get("/api/market/pulse/history?limit=5", headers=headers)
        assert history.status_code == 200
        assert history.json()["items"][0]["id"] == 1

        run_once = client.post(
            "/api/market/pulse/run-once",
            headers=headers,
        )
        assert run_once.status_code == 200
        assert run_once.json()["regime"] == "rotation"


def test_market_judgment_api_routes(monkeypatch) -> None:
    class FakeMarketJudgmentEngine:
        def clock(self) -> dict:
            return {"status": "ok", "session": "regular", "is_market_open": True}

        def latest_quotes(self, limit: int = 100, symbols=None) -> dict:
            return {
                "status": "ok",
                "count": 1,
                "limit": limit,
                "symbols": symbols or [],
                "items": [{"symbol": "005930", "price": 76000}],
            }

        def latest_account(self) -> dict:
            return {
                "status": "ok",
                "account_label": "국장1",
                "cash_krw": 1_000_000,
                "positions": [{"symbol": "005930"}],
            }

        def latest_judgment(self) -> dict:
            return {
                "status": "ok",
                "run": {"mode": "llm"},
                "judgments": [{"symbol": "005930", "account_action": "hold"}],
            }

        def schedule(self) -> dict:
            return {
                "status": "ok",
                "judge_interval_sec": 1800,
                "clock": {"next_open_at": "2026-05-11T09:00:00+09:00"},
            }

        async def run_once(self, *, use_llm: bool = True) -> dict:
            return {
                "status": "ok",
                "use_llm": use_llm,
                "judgments": [{"symbol": "005930"}],
            }

        def status(self) -> dict:
            return {"status": "ok", "run_count": 1}

    monkeypatch.setattr(
        tradecraft_main,
        "market_judgment_engine",
        FakeMarketJudgmentEngine(),
    )
    monkeypatch.setattr(settings, "kis_primary_app_key", "app")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "secret")
    monkeypatch.setattr(settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(settings, "kis_primary_product_code", "01")

    with TestClient(app) as client:
        clock = client.get("/api/market/clock")
        assert clock.status_code == 200
        assert clock.json()["session"] == "regular"

        headers = _admin_headers(monkeypatch)
        account = client.get("/api/market/account", headers=headers)
        assert account.status_code == 200
        assert account.json()["account_label"] == "국장1"

        latest = client.get("/api/market/judgments/latest", headers=headers)
        assert latest.status_code == 200
        assert latest.json()["judgments"][0]["symbol"] == "005930"

        schedule = client.get("/api/market/judgments/schedule", headers=headers)
        assert schedule.status_code == 200
        assert schedule.json()["judge_interval_sec"] == 1800

        run = client.post("/api/market/judgments/run-once", headers=headers)
        assert run.status_code == 200
        assert run.json()["use_llm"] is True


def test_dashboard_uses_upbit_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 10_000.0,
                "available": 10_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 10_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.1,
                "available": 0.1,
                "locked": 0.0,
                "avg_price": 100_000_000.0,
                "mark_price": 110_000_000.0,
                "value_krw": 11_000_000.0,
                "pnl_krw": 1_000_000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "dummy")
    monkeypatch.setattr(settings, "upbit_secret_key", "dummy")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(upbit, "fetch_balance_assets", fake_fetch_balance_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        upbit_venue = next(v for v in payload["venues"] if v["id"] == "upbit")
        assert upbit_venue["cash_krw"] == 10_000.0
        assert upbit_venue["invested_krw"] == 11_000_000.0
        assert upbit_venue["unrealized_pnl_krw"] == 1_000_000.0
        assert any(event["type"] == "upbit" for event in payload["events"])


def test_dashboard_uses_bithumb_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 400_000.0,
                "available": 400_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 400_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "XRP",
                "kind": "position",
                "qty": 300.0,
                "available": 300.0,
                "locked": 0.0,
                "avg_price": 850.0,
                "mark_price": 920.0,
                "value_krw": 276_000.0,
                "pnl_krw": 21_000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "dummy")
    monkeypatch.setattr(settings, "bithumb_secret_key", "dummy")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    monkeypatch.setattr(bithumb, "fetch_balance_assets", fake_fetch_balance_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        venue = next(v for v in payload["venues"] if v["id"] == "bithumb")
        assert venue["cash_krw"] == 400_000.0
        assert venue["invested_krw"] == 276_000.0
        assert venue["unrealized_pnl_krw"] == 21_000.0
        assert any(
            "빗썸" in str(event.get("message") or "") for event in payload["events"]
        )


def test_dashboard_uses_binance_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_spot_assets(usdt_krw_rate: float | None = None) -> list[dict]:
        assert usdt_krw_rate == pytest.approx(1400.0)
        return [
            {
                "asset": "USDT",
                "kind": "cash",
                "qty": 1000.0,
                "available": 1000.0,
                "locked": 0.0,
                "avg_price": 1380.0,
                "mark_price": 1380.0,
                "value_krw": 1_380_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.01,
                "available": 0.01,
                "locked": 0.0,
                "avg_price": 0.0,
                "mark_price": 140_000_000.0,
                "value_krw": 1_400_000.0,
                "pnl_krw": 0.0,
            },
        ]

    async def fake_fetch_futures_assets(
        usdt_krw_rate: float | None = None,
    ) -> list[dict]:
        assert usdt_krw_rate == pytest.approx(1400.0)
        return [
            {
                "asset": "USDT-FUT",
                "kind": "cash",
                "qty": 200.0,
                "available": 200.0,
                "locked": 0.0,
                "avg_price": 1380.0,
                "mark_price": 1380.0,
                "value_krw": 276_000.0,
                "pnl_krw": 0.0,
            }
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "dummy")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "dummy")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(binance, "fetch_spot_assets", fake_fetch_spot_assets)
    monkeypatch.setattr(binance, "fetch_futures_assets", fake_fetch_futures_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        spot_venue = next(v for v in payload["venues"] if v["id"] == "binance")
        futures_venue = next(
            v for v in payload["venues"] if v["id"] == "binance_futures"
        )
        assert spot_venue["cash_krw"] == 1_380_000.0
        assert spot_venue["invested_krw"] == 1_400_000.0
        assert futures_venue["cash_krw"] == 276_000.0
        assert futures_venue["invested_krw"] == 0.0
        assert any(
            "바이낸스 Spot" in str(event.get("message") or "")
            for event in payload["events"]
        )
        assert any(
            "바이낸스 Futures" in str(event.get("message") or "")
            for event in payload["events"]
        )


def test_dashboard_uses_kis_primary_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "available": 500_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "005930",
                "kind": "position",
                "qty": 10.0,
                "available": 10.0,
                "locked": 0.0,
                "avg_price": 70_000.0,
                "mark_price": 75_000.0,
                "value_krw": 750_000.0,
                "pnl_krw": 50_000.0,
            },
        ]

    async def fake_fetch_us_balance_assets(
        usd_krw_rate: float | None = None,
    ) -> list[dict]:
        assert usd_krw_rate == pytest.approx(1350.0)
        return [
            {
                "asset": "USD",
                "kind": "cash",
                "qty": 100.0,
                "available": 100.0,
                "locked": 0.0,
                "avg_price": 1300.0,
                "mark_price": 1300.0,
                "value_krw": 130_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "AAPL",
                "kind": "position",
                "qty": 2.0,
                "available": 2.0,
                "locked": 0.0,
                "avg_price": 260_000.0,
                "mark_price": 270_000.0,
                "value_krw": 540_000.0,
                "pnl_krw": 20_000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "dummy")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "dummy")
    monkeypatch.setattr(settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(settings, "kis_primary_product_code", "01")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    monkeypatch.setattr(kis_primary, "fetch_balance_assets", fake_fetch_balance_assets)
    monkeypatch.setattr(
        kis_primary, "fetch_us_balance_assets", fake_fetch_us_balance_assets
    )

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        kr_venue = next(v for v in payload["venues"] if v["id"] == "kr_stock")
        us_venue = next(v for v in payload["venues"] if v["id"] == "us_stock")
        assert kr_venue["label"] == "국장1"
        assert kr_venue["cash_krw"] == 500_000.0
        assert kr_venue["invested_krw"] == 750_000.0
        assert kr_venue["unrealized_pnl_krw"] == 50_000.0
        assert us_venue["cash_krw"] == 130_000.0
        assert us_venue["invested_krw"] == 540_000.0
        assert us_venue["unrealized_pnl_krw"] == 20_000.0
        assert any(
            "KIS 1번" in str(event.get("message") or "") for event in payload["events"]
        )


def test_dashboard_adds_kis_secondary_venue_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 120_000.0,
                "available": 120_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 120_000.0,
                "pnl_krw": 0.0,
            }
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "dummy")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "dummy")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "12345678")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "01")
    monkeypatch.setattr(
        kis_secondary, "fetch_balance_assets", fake_fetch_balance_assets
    )

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        secondary = next(v for v in payload["venues"] if v["id"] == "kr_stock_2")
        assert secondary["label"] == "국장2"
        assert secondary["cash_krw"] == 120_000.0
        assert any(
            "KIS 2번" in str(event.get("message") or "") for event in payload["events"]
        )


def test_binance_quant_signals_api_requires_admin(monkeypatch, tmp_path) -> None:
    from tradecraft.services.crypto_quant import CryptoQuantRepository

    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    repo.save_signal(
        {
            "symbol": "BTCUSDT",
            "horizon": "intraday",
            "long_score": 70,
            "short_score": 20,
            "no_trade_score": 25,
            "expected_r_long": 0.45,
            "expected_r_short": -0.1,
            "signal_json": {"bias": "long", "metrics": {"atr_pct": 1.2, "rsi": 58}},
            "updated_at": "2026-05-24T09:00:00+00:00",
        }
    )
    monkeypatch.setattr(settings, "crypto_quant_db_path", str(tmp_path / "quant.db"))

    with TestClient(app) as client:
        blocked = client.get("/api/binance/quant/signals")
        assert blocked.status_code == 401

        ok = client.get(
            "/api/binance/quant/signals",
            headers=_admin_headers(monkeypatch),
        )
        assert ok.status_code == 200
        payload = ok.json()

    assert payload["status"] == "ok"
    assert payload["items"][0]["symbol"] == "BTCUSDT"
    assert payload["history"]["items"][0]["symbol"] == "BTCUSDT"


def test_binance_pattern_context_api_requires_admin(monkeypatch, tmp_path) -> None:
    crypto_pattern_lab = pytest.importorskip("tradecraft.services.crypto_pattern_lab")
    repository_cls = crypto_pattern_lab.CryptoPatternLabRepository

    repo = repository_cls(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p1",
                "source_id": "s1",
                "name": "EMA trend long",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": [],
            }
        ]
    )
    monkeypatch.setattr(settings, "crypto_pattern_lab_db_path", str(tmp_path / "patterns.db"))

    with TestClient(app) as client:
        blocked = client.get("/api/binance/patterns/context")
        assert blocked.status_code == 401

        ok = client.get(
            "/api/binance/patterns/context",
            headers=_admin_headers(monkeypatch),
        )
        assert ok.status_code == 200
        payload = ok.json()

    assert payload["status"] == "ok"
    assert payload["patterns"][0]["family"] == "ema_trend"


def test_evidence_policy_status_api(monkeypatch, tmp_path) -> None:
    class FakeStatusService:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def status(self) -> dict:
            return self.payload

    class FakeQuantRepository:
        path = tmp_path / "quant.db"

        def latest_signals(self, *, limit: int = 1) -> list[dict]:
            return [{"symbol": "BTCUSDT"}][:limit]

    class FakeMemoryRepository:
        def status(self) -> dict:
            return {"status": "ok", "policy_rule_count": 1}

        def list_policy_rules(
            self,
            *,
            active_only: bool = False,
            limit: int = 30,
        ) -> list[dict]:
            return [{"policy_id": "rule-1", "active_only": active_only}][:limit]

        def list_policy_scorecards(self, *, limit: int = 30) -> list[dict]:
            return [{"policy_id": "scorecard-1"}][:limit]

    class FakeMemoryService:
        repository = FakeMemoryRepository()

        def status(self) -> dict:
            raise AssertionError("evidence-policy status must be read-only")

        def policy_rules(
            self,
            *,
            limit: int = 30,
            active_only: bool = False,
        ) -> dict:
            raise AssertionError("evidence-policy context must not sync policy rules")

        def policy_scorecards(self, *, limit: int = 30) -> dict:
            raise AssertionError("evidence-policy context must use repository reads")

        def sync_policy_rules(self) -> dict:
            raise AssertionError("evidence-policy GET must not sync policy rules")

    monkeypatch.setattr(
        tradecraft_main,
        "crypto_market_research_service",
        FakeStatusService({"status": "ok", "source": "research"}),
    )
    monkeypatch.setattr(
        tradecraft_main,
        "crypto_alpha_service",
        FakeStatusService({"status": "ok", "source": "alpha"}),
    )
    monkeypatch.setattr(
        tradecraft_main,
        "crypto_pattern_service",
        FakeStatusService({"status": "ok", "source": "patterns"}),
    )
    monkeypatch.setattr(tradecraft_main, "crypto_quant_repository", FakeQuantRepository())
    monkeypatch.setattr(tradecraft_main, "investment_memory_service", FakeMemoryService())

    with TestClient(app) as client:
        blocked = client.get("/api/evidence-policy/status")
        assert blocked.status_code == 401
        blocked_context = client.get("/api/evidence-policy/context")
        assert blocked_context.status_code == 401
        blocked_crypto_pattern = client.get("/api/crypto/pattern-lab/status")
        assert blocked_crypto_pattern.status_code == 401

        headers = _admin_headers(monkeypatch)
        ok = client.get("/api/evidence-policy/status", headers=headers)
        assert ok.status_code == 200
        payload = ok.json()

        context = client.get("/api/evidence-policy/context?limit=12", headers=headers)
        assert context.status_code == 200
        context_payload = context.json()
        crypto_pattern = client.get("/api/crypto/pattern-lab/status", headers=headers)
        assert crypto_pattern.status_code == 200
        crypto_pattern_payload = crypto_pattern.json()

    assert payload["status"] == "ok"
    assert set(payload["sources"]) == {
        "crypto_market_research",
        "crypto_alpha",
        "crypto_quant",
        "crypto_pattern_lab",
        "kr_equity_pattern_lab",
    }
    assert payload["source_count"] == len(payload["sources"])
    assert payload["policy"]["memory_status"]["status"] == "ok"
    assert payload["policy"]["memory_status"]["read_only"] is True
    assert payload["policy"]["loop"] == (
        "evidence -> scorecard -> policy_rule -> decision_packet -> block_outcome"
    )
    assert context_payload["status"] == "ok"
    assert isinstance(context_payload["policy_rules"], list)
    assert isinstance(context_payload["policy_scorecards"], list)
    assert crypto_pattern_payload == {"status": "ok", "source": "patterns"}


def test_telegram_status_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    with TestClient(app) as client:
        res = client.get("/api/telegram/status")
        assert res.status_code == 200
        assert "ready" in res.json()


def test_dashboard_includes_research_feed(monkeypatch, tmp_path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "openai",
            "query": "crypto",
            "items": [
                {
                    "title": "Daily brief",
                    "summary": "market sentiment remains positive",
                    "source": "openai",
                    "url": "https://example.com/research",
                }
            ],
        }
    )

    monkeypatch.setattr(
        "tradecraft.main.research_reader",
        type(research_reader)(str(path), max_age_sec=60),
    )
    monkeypatch.setattr(settings, "research_enabled", True)

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert payload["research"]["status"] == "ok"
        assert payload["research"]["query"] == "crypto"
        assert payload["research"]["source"] == "openai"
        assert payload["research"]["count"] == 1
        assert payload["research"]["items"][0]["title"] == "Daily brief"


def test_dashboard_includes_stale_research_cache(monkeypatch, tmp_path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "source": "openai",
            "query": "crypto",
            "items": [
                {
                    "title": "Old daily brief",
                    "summary": "old but visible as stale cache",
                    "source": "openai",
                }
            ],
        }
    )

    monkeypatch.setattr(
        "tradecraft.main.research_reader",
        type(research_reader)(str(path), max_age_sec=60),
    )
    monkeypatch.setattr(settings, "research_enabled", True)

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard", headers=_admin_headers(monkeypatch))
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert payload["research"]["status"] == "stale"
        assert payload["research"]["stale"] is True
        assert payload["research"]["count"] == 1
        assert payload["research"]["items"][0]["title"] == "Old daily brief"
        assert any(
            "리서치 스냅샷 오래됨" in str(event.get("message") or "")
            for event in payload.get("events") or []
        )
