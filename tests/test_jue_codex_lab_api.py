from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tradecraft.api.app_route_groups import (
    CoreAppRouteGroupDeps,
    build_core_app_route_specs,
)
from tradecraft.api.app_routes import register_app_routes
from tradecraft.api.jue_codex_lab_router import (
    JueCodexLabRouteDeps,
    build_jue_codex_lab_router,
)
from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.services.jue_codex_lab_models import RepairTask
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore


class _FakeLab:
    def __init__(self) -> None:
        self.run_once_calls: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "db_path": ".runtime/test_codex_lab.db",
            "queued_count": 2,
            "failed_count": 1,
        }

    def run_once(self, *, max_tasks: int = 1) -> dict[str, Any]:
        self.run_once_calls.append({"max_tasks": max_tasks})
        return {"status": "ok", "processed_count": max_tasks}


def _client(lab: _FakeLab, *, admin_ok: bool = True) -> TestClient:
    def require_admin_auth() -> None:
        if not admin_ok:
            raise HTTPException(status_code=401, detail="admin auth required")

    app = FastAPI()
    app.include_router(
        build_jue_codex_lab_router(
            JueCodexLabRouteDeps(
                require_admin_auth=require_admin_auth,
                lab_provider=lambda: lab,
            )
        )
    )
    return TestClient(app)


def test_codex_lab_status_returns_service_status() -> None:
    lab = _FakeLab()

    response = _client(lab).get("/api/jue/codex-lab/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "db_path": ".runtime/test_codex_lab.db",
        "queued_count": 2,
        "failed_count": 1,
    }


def test_codex_lab_run_once_passes_max_tasks() -> None:
    lab = _FakeLab()

    response = _client(lab).post("/api/jue/codex-lab/run-once?max_tasks=3")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "processed_count": 3}
    assert lab.run_once_calls == [{"max_tasks": 3}]


def test_codex_lab_routes_require_admin_auth() -> None:
    response = _client(_FakeLab(), admin_ok=False).get(
        "/api/jue/codex-lab/status"
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "admin auth required"


def test_core_route_group_does_not_register_retired_codex_lab_router() -> None:
    lab = _FakeLab()
    app = FastAPI()
    specs = build_core_app_route_specs(
        _core_deps(lab=lab)
    )
    register_app_routes(app, specs)

    response = TestClient(app).get("/api/jue/codex-lab/status")

    assert response.status_code == 404


def test_codex_lab_service_status_counts_queued_and_failed_tasks(tmp_path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    store.initialize()
    for task_id, status in (("task-queued", "queued"), ("task-failed", "failed")):
        store.upsert_task(
            RepairTask(
                task_id=task_id,
                venue="binance",
                discipline_id="cost_simulation",
                source_validation_run_id="validation-binance-1",
                status=status,
                priority=100,
                owner="cost_model",
                automation_hook="sync_live_performance_and_edges",
                failure_status="fail",
            ),
            now_iso="2026-07-02T00:00:00+09:00",
        )

    status = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "trading_validation.db",
    ).status()

    assert status == {
        "status": "ok",
        "db_path": str(tmp_path / "codex_lab.db"),
        "initialized": True,
        "queued_count": 1,
        "failed_count": 1,
    }


def test_ops_readiness_codex_lab_section_is_retired() -> None:
    tree = ast.parse(Path("src/tradecraft/main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_build_ops_readiness":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Constant):
                continue
            assert inner.value != "jue_codex_lab"


def _core_deps(*, lab: _FakeLab) -> CoreAppRouteGroupDeps:
    class _Service:
        def status(self) -> dict[str, Any]:
            return {"status": "ok"}

        def latest(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok"}

        def history(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "items": []}

        def collect(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok"}

        def run_once(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok"}

        def source_status(self) -> dict[str, Any]:
            return {"status": "ok"}

        def list_external_signals(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "items": []}

        def append_external_signals(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok"}

        def build_candidates(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "candidates": []}

        def build_brief(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok"}

        def special_watch(self) -> dict[str, Any]:
            return {"status": "ok", "watch": []}

        def should_run_for_day(self, trading_day: Any) -> bool:
            return False

        def clock(self) -> dict[str, Any]:
            return {"phase": "closed"}

        def latest_quotes(self, **kwargs: Any) -> dict[str, Any]:
            return {"quotes": []}

        def latest_account(self) -> dict[str, Any]:
            return {"cash_krw": 0}

        def latest_judgment(self) -> dict[str, Any]:
            return {"status": "ok"}

        def schedule(self) -> dict[str, Any]:
            return {"status": "ok"}

    service = _Service()
    return CoreAppRouteGroupDeps(
        require_admin_auth=lambda: None,
        strategy_engine=lambda: service,
        strategy_classify_intent=lambda query: "general",
        strategy_default_query=lambda query: "default",
        strategy_safe_limit=lambda value: 1,
        strategy_read_research_feed=lambda: None,
        strategy_collect_source_ids=lambda payload: None,
        strategy_safe_collect_sources=lambda source_ids=None: [],
        strategy_build_insight_collector=lambda sources=None: object(),
        now=lambda: None,
        fundamentals_service=lambda: service,
        analysis_service=lambda: service,
        symbols_from_csv=lambda raw: [],
        strategy_fundamental_targets=lambda: [],
        is_krx_symbol=lambda symbol: False,
        max_symbols_per_collect=lambda: 1,
        daily_discovery_service=lambda: service,
        discovery_today=lambda: None,
        discovery_config_payload=lambda: {},
        build_ops_readiness=lambda: {"status": "ok"},
        build_codex_native_status=lambda: {"status": "ok"},
        refresh_codex_native_checks=lambda force=False: {"status": "ok"},
        system_metrics_snapshot=lambda: {},
        watchdog_status=lambda: {"status": "ok"},
        restart_runner_processes=lambda keys, delay_sec=0.5: {"keys": keys},
        build_settings_catalog=lambda: {},
        update_settings_env=lambda updates, dry_run=True: {},
        live_authority_payload=lambda: {},
        trading_validation_status_payload=lambda venue: {},
        trading_validation_service=lambda venue: service,
        sync_live_performance_and_edges=lambda: {},
        backtest_manager=lambda: service,
        backtest_data_registry=lambda: service,
        backtest_scenarios=lambda: [],
        backtest_load_sessions=lambda: ([], ""),
        backtest_build_config=lambda payload: payload,
        backtest_emit_interval=lambda: 1,
        build_dashboard_payload=lambda **kwargs: {},
        market_judgment_engine=service,
        market_pulse_service=service,
        kis_primary_ready=lambda: False,
        memory_status=lambda **kwargs: {},
        memory_today=lambda **kwargs: {},
        memory_symbol=lambda symbol: {},
        memory_block=lambda block_id: {},
        memory_initialize=lambda **kwargs: {},
        memory_build_context=lambda: {},
        memory_run_ritual=lambda **kwargs: {},
        memory_run_update=lambda **kwargs: {},
        memory_seed_current=lambda **kwargs: {},
        memory_run_due_reflections=lambda **kwargs: {},
        memory_latest_period_review=lambda period: {},
        memory_period_reviews=lambda **kwargs: {},
        memory_run_period_review=lambda **kwargs: {},
        memory_latest_historical_replay=lambda venue: {},
        memory_historical_replays=lambda **kwargs: {},
        memory_run_historical_replay=lambda **kwargs: {},
        memory_policy_scorecards=lambda **kwargs: {},
        memory_policy_rules=lambda **kwargs: {},
        memory_policy_revisions=lambda **kwargs: {},
        memory_activate_policy_revision=lambda revision_id: {},
        memory_reject_policy_revision=lambda revision_id: {},
        wiki_service=service,
    )
