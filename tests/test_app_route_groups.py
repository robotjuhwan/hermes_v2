from __future__ import annotations

import ast
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.app_route_groups import (
    AssistantSupportRouteGroupDeps,
    CoreAppRouteGroupDeps,
    ObservabilityRouteGroupDeps,
    ResearchRouteGroupDeps,
    TradingRouteGroupDeps,
    build_assistant_support_route_specs,
    build_core_app_route_specs,
    build_observability_route_specs,
    build_research_route_specs,
    build_trading_route_specs,
)
from tradecraft.api.app_routes import register_app_routes


def test_main_uses_assistant_support_route_group_instead_of_direct_support_routers() -> None:
    tree = ast.parse(Path("src/tradecraft/main.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")

    assert "tradecraft.api.app_route_groups.AssistantSupportRouteGroupDeps" in imported
    assert "tradecraft.api.app_route_groups.build_assistant_support_route_specs" in imported
    assert "tradecraft.api.helper.HelperRouteDeps" not in imported
    assert "tradecraft.api.helper.build_helper_router" not in imported
    assert "tradecraft.api.static.StaticRouteDeps" not in imported
    assert "tradecraft.api.static.build_static_router" not in imported
    assert "tradecraft.api.jue.JueRouteDeps" not in imported
    assert "tradecraft.api.jue.build_jue_router" not in imported
    assert "tradecraft.api.llm.LLMRouteDeps" not in imported
    assert "tradecraft.api.llm.build_llm_router" not in imported


def test_main_uses_core_app_route_group_instead_of_direct_core_routers() -> None:
    tree = ast.parse(Path("src/tradecraft/main.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")

    assert "tradecraft.api.app_route_groups.CoreAppRouteGroupDeps" in imported
    assert "tradecraft.api.app_route_groups.build_core_app_route_specs" in imported
    assert "tradecraft.api.strategy.StrategyRouteDeps" not in imported
    assert "tradecraft.api.strategy.build_strategy_router" not in imported
    assert "tradecraft.api.symbols.SymbolRouteDeps" not in imported
    assert "tradecraft.api.symbols.build_symbols_router" not in imported
    assert "tradecraft.api.discovery.DiscoveryRouteDeps" not in imported
    assert "tradecraft.api.discovery.build_discovery_router" not in imported
    assert "tradecraft.api.ops.OpsRouteDeps" not in imported
    assert "tradecraft.api.ops.build_ops_router" not in imported
    assert "tradecraft.api.trading.TradingRouteDeps" not in imported
    assert "tradecraft.api.trading.build_trading_router" not in imported
    assert "tradecraft.api.market.MarketRouteDeps" not in imported
    assert "tradecraft.api.market.build_market_router" not in imported
    assert "tradecraft.api.memory.MemoryRouteDeps" not in imported
    assert "tradecraft.api.memory.build_memory_router" not in imported


class _FakeMemoryRepository:
    def status(self) -> dict[str, Any]:
        return {"status": "ok", "policy_rule_count": 2}

    def list_policy_rules(
        self,
        *,
        active_only: bool = False,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        return [{"active_only": active_only, "limit": limit}]

    def list_policy_scorecards(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return [{"limit": limit}]


class _AssistantRegistry:
    def compile_prompt_pack(self, workflow_id: str) -> dict[str, Any]:
        return {"workflow_id": workflow_id, "model_policy": {"model": "gpt-5.5"}}

    def load_source_manifest(self, source_id: str) -> dict[str, Any]:
        return {"source_id": source_id, "repository_url": "", "mappings": []}


class _AssistantLifecycleRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def list_artifacts(self, **_: Any) -> list[dict[str, Any]]:
        return [{"artifact_id": "artifact-1", "db_path": self.db_path}]


class _AssistantRuntime:
    mode = "sdk"
    resolved_model = "gpt-5.5"
    resolved_reasoning_effort = "xhigh"

    async def complete(
        self,
        payload: dict[str, Any],
        *,
        timeout_ms: int,
    ) -> dict[str, Any]:
        return {"ok": True, "mode": "sdk", "content": "{}"}


class _CoreStrategyEngine:
    def source_status(self) -> dict[str, Any]:
        return {"source": "ok"}

    def list_external_signals(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "kwargs": kwargs}

    def append_external_signals(
        self,
        *,
        source_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {"source_id": source_id, "payload": payload}

    def build_candidates(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "kwargs": kwargs, "candidates": []}

    def build_brief(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "kwargs": kwargs}


class _CoreFundamentals:
    def latest(self, symbol: str) -> dict[str, Any]:
        return {"status": "ok", "symbol": symbol}

    def collect_symbols(
        self,
        symbols: list[str],
        *,
        force: bool,
    ) -> dict[str, Any]:
        return {"status": "ok", "symbols": symbols, "force": force}


class _CoreAnalysis:
    def special_watch(self) -> dict[str, Any]:
        return {"status": "ok", "watch": []}


class _CoreDiscovery:
    def latest_context(self, *, limit: int) -> dict[str, Any]:
        return {"limit": limit}

    def should_run_for_day(self, trading_day: date) -> bool:
        return trading_day.isoformat() == "2026-06-21"


class _CoreMarketJudgment:
    def clock(self) -> dict[str, Any]:
        return {"phase": "regular"}

    def latest_quotes(self, **kwargs: Any) -> dict[str, Any]:
        return {"quotes": [], "kwargs": kwargs}

    def latest_account(self) -> dict[str, Any]:
        return {"cash_krw": 1000}

    def latest_judgment(self) -> dict[str, Any]:
        return {"status": "ok"}

    def schedule(self) -> dict[str, Any]:
        return {"interval_sec": 1800}

    def run_once(self, *, use_llm: bool) -> dict[str, Any]:
        return {"status": "ok", "use_llm": use_llm}


class _CoreMarketPulse:
    def latest(self) -> dict[str, Any]:
        return {"pulse": "latest"}

    def history(self, *, limit: int) -> dict[str, Any]:
        return {"limit": limit}

    def collect(self, *, clock: dict[str, Any]) -> dict[str, Any]:
        return {"clock": clock}


class _CoreTradingValidation:
    def run_once(self, *, venue: str) -> dict[str, Any]:
        return {"status": "ok", "venue": venue}


class _CoreBacktestManager:
    def status(self) -> dict[str, Any]:
        return {"job": {"status": "idle"}}

    def start(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "running", "kwargs": kwargs}

    def stop(self) -> dict[str, Any]:
        return {"ok": True}


class _CoreBacktestDataRegistry:
    def status(self) -> dict[str, Any]:
        return {"symbol_count": 0}

    def observe_sessions(
        self,
        rows: list[dict[str, Any]],
        source: str,
    ) -> dict[str, Any]:
        return {"symbol_count": len(rows), "source": source}


def test_core_app_route_group_builds_strategy_symbols_discovery_ops_trading_market_and_memory_routes() -> None:
    app = FastAPI()
    specs = build_core_app_route_specs(
        CoreAppRouteGroupDeps(
            require_admin_auth=lambda: None,
            strategy_engine=lambda: _CoreStrategyEngine(),
            strategy_classify_intent=lambda query: f"intent:{query}",
            strategy_default_query=lambda query: str(query or "default"),
            strategy_safe_limit=lambda value: int(value or 8),
            strategy_read_research_feed=lambda: {"feed": []},
            strategy_collect_source_ids=lambda payload: None,
            strategy_safe_collect_sources=lambda source_ids=None: [],
            strategy_build_insight_collector=lambda sources=None: object(),
            now=lambda: datetime(2026, 6, 21, tzinfo=timezone.utc),
            fundamentals_service=lambda: _CoreFundamentals(),
            analysis_service=lambda: _CoreAnalysis(),
            symbols_from_csv=lambda raw: [item.strip() for item in raw.split(",")],
            strategy_fundamental_targets=lambda: ["005930"],
            is_krx_symbol=lambda symbol: str(symbol).isdigit() and len(str(symbol)) == 6,
            max_symbols_per_collect=lambda: 80,
            daily_discovery_service=lambda: _CoreDiscovery(),
            discovery_today=lambda: date(2026, 6, 21),
            discovery_config_payload=lambda: {
                "kospi_count": 5,
                "kosdaq_count": 5,
                "candidate_limit_per_market": 20,
            },
            build_ops_readiness=lambda: {"status": "ready"},
            build_codex_native_status=lambda: {"status": "ok"},
            refresh_codex_native_checks=lambda force=False: {"force": force},
            system_metrics_snapshot=lambda: {"cpu": 1},
            watchdog_status=lambda: {"status": "watching"},
            restart_runner_processes=lambda keys, delay_sec=0.5: {
                "keys": keys,
                "delay_sec": delay_sec,
            },
            build_settings_catalog=lambda: {"settings": []},
            update_settings_env=lambda updates, confirm_high_risk=False: {
                "updates": updates,
                "confirm_high_risk": confirm_high_risk,
            },
            live_authority_payload=lambda: {"status": "live"},
            trading_validation_status_payload=lambda venue: {"venue": venue},
            trading_validation_service=lambda venue: _CoreTradingValidation(),
            sync_live_performance_and_edges=lambda: {"synced": True},
            backtest_manager=lambda: _CoreBacktestManager(),
            backtest_data_registry=lambda: _CoreBacktestDataRegistry(),
            backtest_scenarios=lambda: [{"key": "baseline"}],
            backtest_load_sessions=lambda: (
                [
                    {
                        "session_id": "s1",
                        "venue_id": "kr_stock",
                        "strategy_id": "noop_balance",
                        "trade_symbol": "005930",
                    }
                ],
                "fixture_sessions.json",
            ),
            backtest_build_config=lambda payload: {"config": payload},
            backtest_emit_interval=lambda: 1,
            build_dashboard_payload=lambda include_telegram=True: {
                "include_telegram": include_telegram,
            },
            market_judgment_engine=lambda: _CoreMarketJudgment(),
            market_pulse_service=lambda: _CoreMarketPulse(),
            kis_primary_ready=lambda: True,
            memory_status=lambda scope="", compact=False: {
                "status": "memory_ok",
                "scope": scope,
                "compact": compact,
            },
            memory_today=lambda scope="", compact=False: {
                "day": "today",
                "scope": scope,
                "compact": compact,
            },
            memory_symbol=lambda symbol: {"symbol": symbol},
            memory_block=lambda block_id: {"block_id": block_id},
            memory_initialize=lambda force=False: {"force": force},
            memory_build_context=lambda: {"context": True},
            memory_run_ritual=lambda **kwargs: {"ritual": kwargs},
            memory_run_update=lambda **kwargs: {"update": kwargs},
            memory_seed_current=lambda **kwargs: {"seed": kwargs},
            memory_run_due_reflections=lambda **kwargs: {"reflections": kwargs},
            memory_latest_period_review=lambda period_type: {"period_type": period_type},
            memory_period_reviews=lambda **kwargs: {"reviews": kwargs},
            memory_run_period_review=lambda **kwargs: {"review": kwargs},
            memory_latest_historical_replay=lambda period_type: {
                "period_type": period_type
            },
            memory_historical_replays=lambda **kwargs: {"replays": kwargs},
            memory_run_historical_replay=lambda **kwargs: {"replay": kwargs},
            memory_policy_scorecards=lambda **kwargs: {"scorecards": kwargs},
            memory_policy_rules=lambda **kwargs: {"rules": kwargs},
            memory_policy_revisions=lambda **kwargs: {"revisions": kwargs},
            memory_activate_policy_revision=lambda revision_id: {
                "activated": revision_id
            },
            memory_reject_policy_revision=lambda revision_id: {"rejected": revision_id},
            wiki_service=type(
                "WikiService",
                (),
                {
                    "status": lambda self: {"status": "wiki_ok"},
                    "context_pack": lambda self, **kwargs: {"context": kwargs},
                    "read_page": lambda self, page_id: {
                        "page_id": page_id,
                        "content": "",
                    },
                    "rebuild": lambda self, **kwargs: {"rebuild": kwargs},
                    "lint": lambda self, **kwargs: {"lint": kwargs},
                },
            )(),
        )
    )
    register_app_routes(app, specs)

    assert [spec.name for spec in specs] == [
        "strategy",
        "symbols",
        "discovery",
        "ops",
        "backtest",
        "trading",
        "market",
        "memory",
        "wiki",
    ]

    with TestClient(app) as client:
        strategy = client.post("/api/strategy/intent", json={"query": "hello"})
        symbol = client.get("/api/symbols/005930/fundamentals")
        discovery = client.get("/api/discovery/status")
        health = client.get("/api/health")
        trading = client.get("/api/live/authority")
        backtest = client.get("/api/backtest/status")
        market = client.get("/api/market/clock")
        memory = client.get("/api/memory/status")
        wiki = client.get("/api/wiki/status")

    assert strategy.status_code == 200
    assert strategy.json()["intent"] == "intent:hello"
    assert symbol.status_code == 200
    assert symbol.json() == {"status": "ok", "symbol": "005930"}
    assert discovery.status_code == 200
    assert discovery.json()["due_today"] is True
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert trading.status_code == 200
    assert trading.json() == {
        "status": "live",
        "compact": True,
        "edge": {},
        "performance": {},
        "venues": {},
    }
    assert backtest.status_code == 200
    assert backtest.json() == {"job": {"status": "idle"}}
    assert market.status_code == 200
    assert market.json() == {"phase": "regular"}
    assert memory.status_code == 200
    assert memory.json() == {"status": "memory_ok", "scope": "", "compact": True}
    assert wiki.status_code == 200
    assert wiki.json() == {"status": "wiki_ok"}


def test_assistant_support_route_group_builds_helper_static_jue_and_llm_routes(
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><title>HERMES</title>",
        encoding="utf-8",
    )
    app = FastAPI()
    specs = build_assistant_support_route_specs(
        AssistantSupportRouteGroupDeps(
            require_admin_auth=lambda: None,
            helper_ask=lambda payload: {"status": "ok", "payload": payload},
            static_dir=lambda: static_dir,
            jue_registry_factory=_AssistantRegistry,
            jue_available_workflow_ids=lambda registry: ["workflow-1"],
            jue_validation_error_type=ValueError,
            jue_lifecycle_repository_factory=_AssistantLifecycleRepository,
            jue_investment_memory_db_path=lambda: "memory.db",
            llm_usage_summary=lambda trading_day: {"trading_day": trading_day},
            llm_usage_status=lambda: {"enabled": True},
            llm_runtime=lambda: _AssistantRuntime(),
            llm_timeout_ms=lambda: 1000,
            llm_thread_mode=lambda: "reuse",
            llm_now=lambda: datetime(2026, 6, 21, tzinfo=timezone.utc),
        )
    )
    register_app_routes(app, specs)

    assert [spec.name for spec in specs] == ["helper", "static", "jue", "llm"]

    with TestClient(app) as client:
        helper = client.post("/api/helper/ask", json={"query": "hello"})
        root = client.get("/")
        jue = client.get("/api/jue/workflows/status")
        llm = client.get("/api/llm/usage/status")

    assert helper.status_code == 200
    assert helper.json()["payload"] == {"query": "hello"}
    assert root.status_code == 200
    assert "HERMES" in root.text
    assert jue.status_code == 200
    assert jue.json()["workflow_count"] == 1
    assert llm.status_code == 200
    assert llm.json() == {"enabled": True}


def test_observability_route_group_builds_evidence_telegram_and_runtime_routes(
    tmp_path,
) -> None:
    app = FastAPI()
    specs = build_observability_route_specs(
        ObservabilityRouteGroupDeps(
            require_admin_auth=lambda: None,
            evidence_source_statuses={
                "crypto_quant": lambda: {"status": "ok", "source": "quant"},
            },
            evidence_memory_repository=lambda: _FakeMemoryRepository(),
            telegram_status=lambda: {"ready": True},
            telegram_validate_webhook_secret=lambda secret: None,
            telegram_process_text=lambda text, chat_id: {
                "handled": True,
                "text": text,
                "chat_id": chat_id,
            },
            runtime_storage_policy=lambda: {"runtime_dir": str(tmp_path)},
            build_runtime_storage_report=lambda policy: {
                "status": "ok",
                "policy": policy,
            },
            cleanup_runtime_storage=lambda policy, dry_run=True: {
                "status": "ok",
                "dry_run": dry_run,
            },
        )
    )
    register_app_routes(app, specs)

    assert [spec.name for spec in specs] == ["evidence", "telegram", "runtime"]

    with TestClient(app) as client:
        evidence = client.get("/api/evidence-policy/status")
        telegram = client.get("/api/telegram/status")
        runtime = client.get("/api/runtime/storage")

    assert evidence.status_code == 200
    assert evidence.json()["sources"]["crypto_quant"] == {
        "status": "ok",
        "source": "quant",
    }
    assert telegram.status_code == 200
    assert telegram.json() == {"ready": True}
    assert runtime.status_code == 200
    assert runtime.json() == {
        "status": "ok",
        "policy": {"runtime_dir": str(tmp_path)},
    }


def test_runtime_storage_route_caches_report_until_refresh_or_cleanup(tmp_path) -> None:
    app = FastAPI()
    calls = {"report": 0, "cleanup": 0}

    def build_report(policy: dict[str, Any]) -> dict[str, Any]:
        calls["report"] += 1
        return {
            "status": "ok",
            "policy": policy,
            "sequence": calls["report"],
        }

    def cleanup(policy: dict[str, Any], dry_run: bool = True, **_: Any) -> dict[str, Any]:
        calls["cleanup"] += 1
        return {
            "status": "ok",
            "dry_run": dry_run,
            "cleanup_sequence": calls["cleanup"],
        }

    specs = build_observability_route_specs(
        ObservabilityRouteGroupDeps(
            require_admin_auth=lambda: None,
            evidence_source_statuses={},
            evidence_memory_repository=lambda: _FakeMemoryRepository(),
            telegram_status=lambda: {"ready": True},
            telegram_validate_webhook_secret=lambda secret: None,
            telegram_process_text=lambda text, chat_id: {},
            runtime_storage_policy=lambda: {"runtime_dir": str(tmp_path)},
            build_runtime_storage_report=build_report,
            cleanup_runtime_storage=cleanup,
        )
    )
    register_app_routes(app, specs)

    with TestClient(app) as client:
        first = client.get("/api/runtime/storage")
        second = client.get("/api/runtime/storage")
        refreshed = client.get("/api/runtime/storage?refresh=true")
        cleanup_result = client.post("/api/runtime/storage/cleanup?dry_run=true")
        after_cleanup = client.get("/api/runtime/storage")

    assert first.status_code == 200
    assert second.status_code == 200
    assert refreshed.status_code == 200
    assert cleanup_result.status_code == 200
    assert after_cleanup.status_code == 200
    assert first.json()["sequence"] == 1
    assert second.json()["sequence"] == 1
    assert refreshed.json()["sequence"] == 2
    assert cleanup_result.json()["after"]["sequence"] == 3
    assert after_cleanup.json()["sequence"] == 3
    assert calls == {"report": 3, "cleanup": 1}


def test_observability_runtime_storage_file_cache_lasts_long_enough_for_ui(
    tmp_path,
) -> None:
    def build_app(build_report: Any) -> FastAPI:
        app = FastAPI()
        specs = build_observability_route_specs(
            ObservabilityRouteGroupDeps(
                require_admin_auth=lambda: None,
                evidence_source_statuses={},
                evidence_memory_repository=lambda: _FakeMemoryRepository(),
                telegram_status=lambda: {"ready": True},
                telegram_validate_webhook_secret=lambda secret: None,
                telegram_process_text=lambda text, chat_id: {},
                runtime_storage_policy=lambda: {"runtime_dir": str(tmp_path)},
                build_runtime_storage_report=build_report,
                cleanup_runtime_storage=lambda policy, dry_run=True: {"status": "ok"},
            )
        )
        register_app_routes(app, specs)
        return app

    first_app = build_app(
        lambda policy: {
            "status": "ok",
            "runtime_dir": policy["runtime_dir"],
            "marker": "persisted-storage-report",
        }
    )
    with TestClient(first_app) as client:
        first = client.get("/api/runtime/storage")

    assert first.status_code == 200
    cache_path = tmp_path / "runtime_storage_report_cache.json"
    payload = json.loads(cache_path.read_text())
    payload["cached_at_epoch"] = datetime.now(timezone.utc).timestamp() - 1200
    cache_path.write_text(json.dumps(payload))

    def unexpected_rebuild(policy: Any) -> dict[str, Any]:
        raise AssertionError(f"runtime storage UI cache should be reused: {policy}")

    second_app = build_app(unexpected_rebuild)
    with TestClient(second_app, raise_server_exceptions=False) as client:
        second = client.get("/api/runtime/storage?refresh=false")

    assert second.status_code == 200
    assert second.json()["marker"] == "persisted-storage-report"


class _ResearchSettings:
    naver_reports_enabled = True
    rag_enabled = True
    rag_sync_chunk_limit = 5
    rag_persist_path = ".runtime/rag"
    rag_collection_name = "reports"
    rag_query_top_k = 4


class _ETFRepository:
    def status(self) -> dict[str, Any]:
        return {"snapshot_count": 1}

    def list_universe(self) -> list[dict[str, Any]]:
        return [{"symbol": "069500", "name": "KODEX 200"}]


class _CryptoResearch:
    def status(self) -> dict[str, Any]:
        return {"status": "ok", "service": "crypto_research"}


class _CryptoAlpha:
    def status(self) -> dict[str, Any]:
        return {"status": "ok", "service": "crypto_alpha"}


class _ReportRepository:
    def status(self) -> dict[str, Any]:
        return {"status": "repository_ok"}

    def ops_status(self) -> dict[str, Any]:
        return {"status": "repository_ops_ok", "quality_mode": "lightweight"}

    def search(self, **_: Any) -> list[dict[str, Any]]:
        return []


class _RagStore:
    def status(self) -> dict[str, Any]:
        return {"available": True}

    def search(self, **_: Any) -> list[dict[str, Any]]:
        return []


class _Fundamentals:
    def status(self) -> dict[str, Any]:
        return {"status": "fundamentals_ok"}


def test_research_route_group_builds_etf_crypto_and_reports_routes() -> None:
    app = FastAPI()
    specs = build_research_route_specs(
        ResearchRouteGroupDeps(
            require_admin_auth=lambda: None,
            etf_repository_factory=lambda: _ETFRepository(),
            etf_configured_universe=lambda: [
                {"symbol": "069500", "name": "KODEX 200"}
            ],
            etf_expanded_universe=lambda configured: configured,
            etf_universe_item_payload=lambda item: dict(item),
            etf_settings_payload=lambda: {"db_path": "etf.db", "max_symbols": 10},
            etf_list_candidates=lambda repository, universe: [],
            etf_read_only_auto_collect=lambda: {
                "status": "skipped",
                "reason": "read_only_endpoint",
            },
            etf_seed_universe=lambda repository: [],
            etf_symbols_from_payload=lambda payload, universe: [],
            etf_collect_snapshots=lambda **kwargs: {"status": "ok"},
            etf_fetch_quote=lambda symbol: {"symbol": symbol},
            crypto_research_service=lambda: _CryptoResearch(),
            crypto_alpha_service=lambda: _CryptoAlpha(),
            crypto_research_symbols=lambda raw: [],
            default_crypto_research_symbols=lambda: ["BTCUSDT"],
            research_helper_ask=lambda payload: {"status": "ok", "payload": payload},
            research_settings=_ResearchSettings(),
            naver_report_repository=lambda: _ReportRepository(),
            naver_report_crawler=lambda: object(),
            rag_store=lambda: _RagStore(),
            symbol_fundamentals_service=lambda: _Fundamentals(),
            build_report_intelligence_status=lambda settings: {"enabled": True},
            run_report_collection_cycle=lambda **kwargs: {"snapshot": {}},
            sync_report_rag=lambda **kwargs: {"status": "synced"},
            seed_symbol_directory=lambda: None,
            on_rag_resolve_error=lambda exc: None,
        )
    )
    register_app_routes(app, specs)

    assert [spec.name for spec in specs] == ["etf", "crypto", "research"]

    with TestClient(app) as client:
        etf = client.get("/api/etf/research/status")
        crypto = client.get("/api/crypto/research/status")
        reports = client.get("/api/reports/status")

    assert etf.status_code == 200
    assert etf.json()["db_path"] == "etf.db"
    assert crypto.status_code == 200
    assert crypto.json() == {"status": "ok", "service": "crypto_research"}
    assert reports.status_code == 200
    assert reports.json()["repository"] == {"status": "repository_ok"}


def test_reports_status_compact_uses_lightweight_repository_ops_status() -> None:
    class _CountingReportRepository(_ReportRepository):
        def __init__(self) -> None:
            self.status_calls = 0
            self.ops_status_calls = 0

        def status(self) -> dict[str, Any]:
            self.status_calls += 1
            return {"status": "repository_full"}

        def ops_status(self) -> dict[str, Any]:
            self.ops_status_calls += 1
            return {"status": "repository_light", "quality_mode": "lightweight"}

    repo = _CountingReportRepository()
    app = FastAPI()
    specs = build_research_route_specs(
        ResearchRouteGroupDeps(
            require_admin_auth=lambda: None,
            etf_repository_factory=lambda: _ETFRepository(),
            etf_configured_universe=lambda: [],
            etf_expanded_universe=lambda configured: configured,
            etf_universe_item_payload=lambda item: dict(item),
            etf_settings_payload=lambda: {"db_path": "etf.db", "max_symbols": 10},
            etf_list_candidates=lambda repository, universe: [],
            etf_read_only_auto_collect=lambda: {"status": "skipped"},
            etf_seed_universe=lambda repository: [],
            etf_symbols_from_payload=lambda payload, universe: [],
            etf_collect_snapshots=lambda **kwargs: {"status": "ok"},
            etf_fetch_quote=lambda symbol: {"symbol": symbol},
            crypto_research_service=lambda: _CryptoResearch(),
            crypto_alpha_service=lambda: _CryptoAlpha(),
            crypto_research_symbols=lambda raw: [],
            default_crypto_research_symbols=lambda: ["BTCUSDT"],
            research_helper_ask=lambda payload: {"status": "ok", "payload": payload},
            research_settings=_ResearchSettings(),
            naver_report_repository=lambda: repo,
            naver_report_crawler=lambda: object(),
            rag_store=lambda: _RagStore(),
            symbol_fundamentals_service=lambda: _Fundamentals(),
            build_report_intelligence_status=lambda settings: {"enabled": True},
            run_report_collection_cycle=lambda **kwargs: {"snapshot": {}},
            sync_report_rag=lambda **kwargs: {"status": "synced"},
            seed_symbol_directory=lambda: None,
            on_rag_resolve_error=lambda exc: None,
        )
    )
    register_app_routes(app, specs)

    with TestClient(app) as client:
        compact = client.get("/api/reports/status?compact=true")
        full = client.get("/api/reports/status?compact=false")

    assert compact.status_code == 200
    assert full.status_code == 200
    assert compact.json()["compact"] is True
    assert full.json()["compact"] is False
    assert compact.json()["repository"]["status"] == "repository_light"
    assert compact.json()["repository"]["quality_mode"] == "lightweight"
    assert full.json()["repository"]["status"] == "repository_full"
    assert repo.ops_status_calls == 1
    assert repo.status_calls == 1


class _BinanceTrader:
    def snapshot(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "blocks": [],
            "manager_runs": [],
            "latest_manager_run_at": "",
        }

    def run_manager_once(self) -> dict[str, Any]:
        return {"status": "ok", "run": "manager"}

    def run_spot_adoption_once(self) -> dict[str, Any]:
        return {"status": "ok", "run": "spot_adoption"}

    def executor_tick(self) -> dict[str, Any]:
        return {"status": "ok", "run": "executor"}

    def set_kill_switch(self, enabled: bool, *, reason: str) -> dict[str, Any]:
        return {"enabled": enabled, "reason": reason}


class _BinanceMemory:
    def validation_repair_ops_summary(
        self,
        *,
        target_scope: str,
        limit: int,
    ) -> dict[str, Any]:
        return {"target_scope": target_scope, "limit": limit}


class _QuantRepository:
    def latest_signals(
        self,
        *,
        symbols: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return [{"symbols": symbols, "limit": limit}]

    def retrieval_context(
        self,
        *,
        symbols: list[str],
        horizon: str,
        points_per_symbol: int,
    ) -> dict[str, Any]:
        return {
            "symbols": symbols,
            "horizon": horizon,
            "points_per_symbol": points_per_symbol,
        }


class _PatternRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def pattern_context(
        self,
        *,
        symbols: list[str] | None,
        limit: int,
    ) -> dict[str, Any]:
        return {"db_path": self.db_path, "symbols": symbols, "limit": limit}


def test_trading_route_group_builds_kis_binance_and_portfolio_routes() -> None:
    app = FastAPI()
    specs = build_trading_route_specs(
        TradingRouteGroupDeps(
            require_admin_auth=lambda: None,
            kis_rebalance_status=lambda: {"status": "ok", "rebalance": True},
            kis_primary_ready=lambda: True,
            kis_blocks_status=lambda: {"status": "ok"},
            kis_blocks_snapshot=lambda: {"status": "ok", "blocks": []},
            kis_blocks_snapshot_compact=lambda: {
                "status": "ok",
                "compact": True,
                "active_blocks": [],
            },
            attach_kis_block_memory=lambda payload: payload,
            kis_validation_repair_ops_summary=(
                lambda target_scope, limit: {"target_scope": target_scope, "limit": limit}
            ),
            ops_readiness=lambda: {
                "kis_block_trader": {},
                "market_judge": {"schedule": {}},
                "stale_processes": [],
            },
            kis_manager_run_once=lambda: {"status": "ok"},
            kis_adoption_run_once=lambda: {"status": "ok"},
            kis_executor_tick=lambda manual=False: {"status": "ok", "manual": manual},
            kis_set_kill_switch=lambda enabled, reason: {
                "enabled": enabled,
                "reason": reason,
            },
            kis_cancel_order=lambda order_id, reason: {"status": "ok"},
            kis_block_detail=lambda block_id: {"status": "ok", "block_id": block_id},
            kis_block_memory=lambda block_id: {"block_id": block_id},
            kis_add_user_directive=lambda *args, **kwargs: {"status": "ok"},
            kis_pause_block=lambda block_id, reason: {"status": "ok"},
            kis_resume_block=lambda block_id, reason: {"status": "ok"},
            kis_close_block=lambda block_id, reason: {"status": "ok"},
            binance_trader=_BinanceTrader(),
            binance_memory_service=_BinanceMemory(),
            build_binance_readiness=lambda payload: {"ready": True},
            binance_quant_repository_factory=lambda: _QuantRepository(),
            binance_pattern_repository_cls=_PatternRepository,
            binance_pattern_db_path=lambda: "patterns.db",
            binance_pattern_import_error=None,
            portfolio_list_advice_messages=lambda **kwargs: [{"id": 1}],
            portfolio_get_advice_message=lambda message_id: {
                "id": message_id,
                "message_md": "hello",
            },
            portfolio_update_message_status=lambda **kwargs: True,
            portfolio_send_message=lambda message: {"ok": True},
        )
    )
    register_app_routes(app, specs)

    assert [spec.name for spec in specs] == [
        "rebalance",
        "kis_blocks",
        "binance_blocks",
        "portfolio_coach",
    ]

    with TestClient(app) as client:
        rebalance = client.get("/api/rebalance/kis-status")
        kis_blocks = client.get("/api/kis/blocks/status")
        binance = client.get("/api/binance/blocks/status")
        portfolio = client.get("/api/portfolio-coach/review-queue")

    assert rebalance.status_code == 200
    assert rebalance.json()["rebalance"] is True
    assert kis_blocks.status_code == 200
    assert kis_blocks.json()["validation_repair_ops"]["target_scope"] == "kis"
    assert binance.status_code == 200
    assert binance.json()["readiness"]["ready"] is True
    assert portfolio.status_code == 200
    assert portfolio.json()["items"] == [{"id": 1}]
