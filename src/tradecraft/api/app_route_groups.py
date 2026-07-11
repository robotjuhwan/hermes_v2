from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tradecraft.api.app_route_specs import RouteFactorySpec, build_route_specs
from tradecraft.api.app_routes import AppRouteSpec
from tradecraft.api.backtest import BacktestRouteDeps, build_backtest_router
from tradecraft.api.binance_blocks import build_binance_blocks_router
from tradecraft.api.binance_blocks_payloads import build_binance_block_route_deps
from tradecraft.api.crypto import CryptoRouteDeps, build_crypto_router
from tradecraft.api.discovery import DiscoveryRouteDeps, build_discovery_router
from tradecraft.api.evidence import EvidenceRouteDeps, build_evidence_router
from tradecraft.api.etf import ETFRouteDeps, build_etf_router
from tradecraft.api.helper import HelperRouteDeps, build_helper_router
from tradecraft.api.jue import JueRouteDeps, build_jue_router
from tradecraft.api.kis_blocks import KISBlockRouteDeps, build_kis_blocks_router
from tradecraft.api.llm import LLMRouteDeps, build_llm_router
from tradecraft.api.market import MarketRouteDeps, build_market_router
from tradecraft.api.memory import MemoryRouteDeps, build_memory_router
from tradecraft.api.ops import OpsRouteDeps, build_ops_router
from tradecraft.api.portfolio import (
    PortfolioCoachRouteDeps,
    build_portfolio_coach_router,
)
from tradecraft.api.rebalance import RebalanceRouteDeps, build_rebalance_router
from tradecraft.api.research import build_research_route_deps, build_research_router
from tradecraft.api.runtime import RuntimeRouteDeps, build_runtime_router
from tradecraft.api.static import StaticRouteDeps, build_static_router
from tradecraft.api.strategy import StrategyRouteDeps, build_strategy_router
from tradecraft.api.symbols import SymbolRouteDeps, build_symbols_router
from tradecraft.api.telegram import TelegramRouteDeps, build_telegram_router
from tradecraft.api.trading import TradingRouteDeps, build_trading_router
from tradecraft.api.wiki import WikiRouteDeps, build_wiki_router


@dataclass(frozen=True)
class AssistantSupportRouteGroupDeps:
    require_admin_auth: Callable[..., Any]
    helper_ask: Callable[[dict[str, Any]], Any]
    static_dir: Callable[[], Any]
    jue_registry_factory: Callable[[], Any]
    jue_available_workflow_ids: Callable[[Any], list[str]]
    jue_validation_error_type: type[Exception]
    jue_lifecycle_repository_factory: Callable[[str], Any]
    jue_investment_memory_db_path: Callable[[], str]
    llm_usage_summary: Callable[[str | None, str | None], Any]
    llm_usage_status: Callable[[], Any]
    llm_runtime: Callable[[], Any]
    llm_timeout_ms: Callable[[], int]
    llm_thread_mode: Callable[[], str]
    llm_now: Callable[[], Any]


@dataclass(frozen=True)
class CoreAppRouteGroupDeps:
    require_admin_auth: Callable[..., Any]
    strategy_engine: Callable[[], Any]
    strategy_classify_intent: Callable[[str], str]
    strategy_default_query: Callable[[Any], str]
    strategy_safe_limit: Callable[[Any], int]
    strategy_read_research_feed: Callable[[], dict[str, Any] | None]
    strategy_collect_source_ids: Callable[[dict[str, Any] | None], list[str] | None]
    strategy_safe_collect_sources: Callable[[list[str] | None], list[dict[str, Any]]]
    strategy_build_insight_collector: Callable[[list[dict[str, Any]] | None], Any]
    now: Callable[[], Any]
    fundamentals_service: Callable[[], Any]
    analysis_service: Callable[[], Any]
    symbols_from_csv: Callable[[Any], list[str]]
    strategy_fundamental_targets: Callable[[], list[str]]
    is_krx_symbol: Callable[[Any], bool]
    max_symbols_per_collect: Callable[[], int]
    daily_discovery_service: Callable[[], Any]
    discovery_today: Callable[[], Any]
    discovery_config_payload: Callable[[], dict[str, Any]]
    build_ops_readiness: Callable[[], dict[str, Any]]
    build_codex_native_status: Callable[[], dict[str, Any]]
    refresh_codex_native_checks: Callable[..., Any]
    system_metrics_snapshot: Callable[[], dict[str, Any]]
    watchdog_status: Callable[[], dict[str, Any]]
    restart_runner_processes: Callable[..., dict[str, Any]]
    build_settings_catalog: Callable[[], dict[str, Any]]
    update_settings_env: Callable[..., dict[str, Any]]
    live_authority_payload: Callable[[], dict[str, Any]]
    trading_validation_status_payload: Callable[[str], dict[str, Any]]
    trading_validation_service: Callable[[str], Any]
    sync_live_performance_and_edges: Callable[[], dict[str, Any]]
    backtest_manager: Callable[[], Any]
    backtest_data_registry: Callable[[], Any]
    backtest_scenarios: Callable[[], list[dict[str, Any]]]
    backtest_load_sessions: Callable[[], tuple[list[dict[str, Any]], str]]
    backtest_build_config: Callable[[dict[str, Any]], Any]
    backtest_emit_interval: Callable[[], int]
    build_dashboard_payload: Callable[..., Any]
    market_judgment_engine: Any
    market_pulse_service: Any
    kis_primary_ready: Callable[[], bool]
    memory_status: Callable[..., dict[str, Any]]
    memory_today: Callable[..., dict[str, Any]]
    memory_symbol: Callable[[str], dict[str, Any]]
    memory_block: Callable[[str], dict[str, Any]]
    memory_initialize: Callable[..., dict[str, Any]]
    memory_build_context: Callable[[], Any]
    memory_run_ritual: Callable[..., Any]
    memory_run_update: Callable[..., Any]
    memory_seed_current: Callable[..., dict[str, Any]]
    memory_run_due_reflections: Callable[..., dict[str, Any]]
    memory_latest_period_review: Callable[[str], dict[str, Any]]
    memory_period_reviews: Callable[..., dict[str, Any]]
    memory_run_period_review: Callable[..., Any]
    memory_latest_historical_replay: Callable[[str], dict[str, Any]]
    memory_historical_replays: Callable[..., dict[str, Any]]
    memory_run_historical_replay: Callable[..., Any]
    memory_policy_scorecards: Callable[..., dict[str, Any]]
    memory_policy_rules: Callable[..., dict[str, Any]]
    memory_policy_revisions: Callable[..., dict[str, Any]]
    memory_activate_policy_revision: Callable[[str], dict[str, Any]]
    memory_reject_policy_revision: Callable[[str], dict[str, Any]]
    build_ops_restart_readiness: Callable[[], dict[str, Any]] | None = None
    build_compact_ops_readiness: Callable[[], dict[str, Any]] | None = None
    wiki_service: Any | None = None


@dataclass(frozen=True)
class ObservabilityRouteGroupDeps:
    require_admin_auth: Callable[..., Any]
    evidence_source_statuses: dict[str, Callable[[], Any]]
    evidence_memory_repository: Callable[[], Any]
    telegram_status: Callable[[], dict[str, Any]]
    telegram_validate_webhook_secret: Callable[[str | None], None]
    telegram_process_text: Callable[[str, str], Any]
    runtime_storage_policy: Callable[[], Any]
    build_runtime_storage_report: Callable[[Any], dict[str, Any]]
    cleanup_runtime_storage: Callable[..., dict[str, Any]]
    refresh_cold_archive_status: Callable[[], dict[str, Any]] | None = None


@dataclass(frozen=True)
class ResearchRouteGroupDeps:
    require_admin_auth: Callable[..., Any]
    etf_repository_factory: Callable[[], Any]
    etf_configured_universe: Callable[[], list[Any]]
    etf_expanded_universe: Callable[[list[Any]], list[Any]]
    etf_universe_item_payload: Callable[[Any], dict[str, Any]]
    etf_settings_payload: Callable[[], dict[str, Any]]
    etf_list_candidates: Callable[[Any, list[Any]], list[dict[str, Any]]]
    etf_read_only_auto_collect: Callable[[], dict[str, Any]]
    etf_seed_universe: Callable[[Any], list[Any]]
    etf_symbols_from_payload: Callable[[dict[str, Any] | None, list[Any]], list[str]]
    etf_collect_snapshots: Callable[..., Any]
    etf_fetch_quote: Callable[..., Any]
    crypto_research_service: Any
    crypto_alpha_service: Any
    crypto_research_symbols: Callable[[Any], list[str]]
    default_crypto_research_symbols: Callable[[], list[str]]
    research_helper_ask: Callable[[dict[str, Any]], Any]
    research_settings: Any
    naver_report_repository: Any
    naver_report_crawler: Any
    rag_store: Any | None
    symbol_fundamentals_service: Any
    build_report_intelligence_status: Callable[[Any], dict[str, Any]]
    run_report_collection_cycle: Callable[..., Any]
    sync_report_rag: Callable[..., dict[str, Any] | None]
    seed_symbol_directory: Callable[[], Any]
    on_rag_resolve_error: Callable[[Exception], None] | None = None


@dataclass(frozen=True)
class TradingRouteGroupDeps:
    require_admin_auth: Callable[..., Any]
    kis_rebalance_status: Callable[[], Any]
    kis_primary_ready: Callable[[], bool]
    kis_blocks_status: Callable[[], dict[str, Any]]
    kis_blocks_snapshot: Callable[[], Any]
    kis_blocks_snapshot_compact: Callable[[], Any] | None
    attach_kis_block_memory: Callable[[dict[str, Any]], dict[str, Any]]
    kis_validation_repair_ops_summary: Callable[..., dict[str, Any]]
    ops_readiness: Callable[[], dict[str, Any]]
    kis_manager_run_once: Callable[[], Any]
    kis_adoption_run_once: Callable[[], Any]
    kis_executor_tick: Callable[..., Any]
    kis_set_kill_switch: Callable[[bool, str], dict[str, Any]]
    kis_cancel_order: Callable[..., Any]
    kis_block_detail: Callable[[str], dict[str, Any]]
    kis_block_memory: Callable[[str], dict[str, Any]]
    kis_add_user_directive: Callable[..., dict[str, Any]]
    kis_pause_block: Callable[[str, str], dict[str, Any]]
    kis_resume_block: Callable[[str, str], dict[str, Any]]
    kis_close_block: Callable[..., Any]
    binance_trader: Any
    binance_memory_service: Any
    build_binance_readiness: Callable[[dict[str, Any]], dict[str, Any]]
    binance_quant_repository_factory: Callable[[], Any]
    binance_pattern_repository_cls: type[Any] | None
    binance_pattern_db_path: Callable[[], str] | str
    portfolio_list_advice_messages: Callable[..., list[dict[str, Any]]]
    portfolio_get_advice_message: Callable[[int], dict[str, Any] | None]
    portfolio_update_message_status: Callable[..., bool]
    portfolio_send_message: Callable[[str], Any]
    binance_pattern_import_error: Exception | None = None
    kis_blocks_status_readiness: Callable[[], dict[str, Any]] | None = None


def build_observability_route_specs(
    deps: ObservabilityRouteGroupDeps,
) -> tuple[AppRouteSpec, ...]:
    return build_route_specs(
        (
            RouteFactorySpec(
                name="evidence",
                build_router=build_evidence_router,
                deps=EvidenceRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    source_statuses=deps.evidence_source_statuses,
                    memory_repository=deps.evidence_memory_repository,
                ),
            ),
            RouteFactorySpec(
                name="telegram",
                build_router=build_telegram_router,
                deps=TelegramRouteDeps(
                    status=deps.telegram_status,
                    validate_webhook_secret=deps.telegram_validate_webhook_secret,
                    process_text=deps.telegram_process_text,
                ),
            ),
            RouteFactorySpec(
                name="runtime",
                build_router=build_runtime_router,
                deps=RuntimeRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    runtime_storage_policy=deps.runtime_storage_policy,
                    build_runtime_storage_report=deps.build_runtime_storage_report,
                    cleanup_runtime_storage=deps.cleanup_runtime_storage,
                    refresh_cold_archive_status=(
                        deps.refresh_cold_archive_status
                    ),
                    storage_report_file_cache_enabled=True,
                    storage_report_file_cache_ttl_sec=1800,
                ),
            ),
        )
    )


def build_assistant_support_route_specs(
    deps: AssistantSupportRouteGroupDeps,
) -> tuple[AppRouteSpec, ...]:
    return build_route_specs(
        (
            RouteFactorySpec(
                name="helper",
                build_router=build_helper_router,
                deps=HelperRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    ask=deps.helper_ask,
                ),
            ),
            RouteFactorySpec(
                name="static",
                build_router=build_static_router,
                deps=StaticRouteDeps(static_dir=deps.static_dir),
            ),
            RouteFactorySpec(
                name="jue",
                build_router=build_jue_router,
                deps=JueRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    registry_factory=deps.jue_registry_factory,
                    available_workflow_ids=deps.jue_available_workflow_ids,
                    validation_error_type=deps.jue_validation_error_type,
                    lifecycle_repository_factory=(
                        deps.jue_lifecycle_repository_factory
                    ),
                    investment_memory_db_path=deps.jue_investment_memory_db_path,
                ),
            ),
            RouteFactorySpec(
                name="llm",
                build_router=build_llm_router,
                deps=LLMRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    usage_summary=deps.llm_usage_summary,
                    usage_status=deps.llm_usage_status,
                    runtime=deps.llm_runtime,
                    timeout_ms=deps.llm_timeout_ms,
                    thread_mode=deps.llm_thread_mode,
                    now=deps.llm_now,
                ),
            ),
        )
    )


def build_core_app_route_specs(
    deps: CoreAppRouteGroupDeps,
) -> tuple[AppRouteSpec, ...]:
    route_factories: list[RouteFactorySpec] = [
        RouteFactorySpec(
            name="strategy",
            build_router=build_strategy_router,
            deps=StrategyRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                strategy_engine=deps.strategy_engine,
                classify_intent=deps.strategy_classify_intent,
                default_query=deps.strategy_default_query,
                safe_limit=deps.strategy_safe_limit,
                read_research_feed=deps.strategy_read_research_feed,
                collect_source_ids=deps.strategy_collect_source_ids,
                safe_collect_sources=deps.strategy_safe_collect_sources,
                build_insight_collector=deps.strategy_build_insight_collector,
                now=deps.now,
            ),
        ),
        RouteFactorySpec(
            name="symbols",
            build_router=build_symbols_router,
            deps=SymbolRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                fundamentals_service=deps.fundamentals_service,
                analysis_service=deps.analysis_service,
                symbols_from_csv=deps.symbols_from_csv,
                strategy_fundamental_targets=(
                    deps.strategy_fundamental_targets
                ),
                is_krx_symbol=deps.is_krx_symbol,
                max_symbols_per_collect=deps.max_symbols_per_collect,
            ),
        ),
        RouteFactorySpec(
            name="discovery",
            build_router=build_discovery_router,
            deps=DiscoveryRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                service=deps.daily_discovery_service,
                today=deps.discovery_today,
                config_payload=deps.discovery_config_payload,
            ),
        ),
        RouteFactorySpec(
            name="ops",
            build_router=build_ops_router,
            deps=OpsRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                build_ops_readiness=deps.build_ops_readiness,
                build_codex_native_status=deps.build_codex_native_status,
                refresh_codex_native_checks=(
                    deps.refresh_codex_native_checks
                ),
                system_metrics_snapshot=deps.system_metrics_snapshot,
                watchdog_status=deps.watchdog_status,
                restart_runner_processes=deps.restart_runner_processes,
                build_settings_catalog=deps.build_settings_catalog,
                update_settings_env=deps.update_settings_env,
                build_ops_restart_readiness=deps.build_ops_restart_readiness,
                build_compact_ops_readiness=deps.build_compact_ops_readiness,
            ),
        ),
        RouteFactorySpec(
            name="backtest",
            build_router=build_backtest_router,
            deps=BacktestRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                manager=deps.backtest_manager,
                data_registry=deps.backtest_data_registry,
                list_scenarios=deps.backtest_scenarios,
                load_sessions=deps.backtest_load_sessions,
                build_config=deps.backtest_build_config,
                emit_interval=deps.backtest_emit_interval,
            ),
        ),
        RouteFactorySpec(
            name="trading",
            build_router=build_trading_router,
            deps=TradingRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                live_authority_payload=deps.live_authority_payload,
                trading_validation_status_payload=(
                    deps.trading_validation_status_payload
                ),
                trading_validation_service=deps.trading_validation_service,
                sync_live_performance_and_edges=(
                    deps.sync_live_performance_and_edges
                ),
            ),
        ),
        RouteFactorySpec(
            name="market",
            build_router=build_market_router,
            deps=MarketRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                build_dashboard_payload=deps.build_dashboard_payload,
                market_judgment_engine=deps.market_judgment_engine,
                market_pulse_service=deps.market_pulse_service,
                kis_primary_ready=deps.kis_primary_ready,
            ),
        ),
        RouteFactorySpec(
            name="memory",
            build_router=build_memory_router,
            deps=MemoryRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                status=deps.memory_status,
                today=deps.memory_today,
                symbol_memory=deps.memory_symbol,
                block_memory=deps.memory_block,
                initialize=deps.memory_initialize,
                build_context=deps.memory_build_context,
                run_ritual=deps.memory_run_ritual,
                run_update=deps.memory_run_update,
                seed_current=deps.memory_seed_current,
                run_due_reflections=deps.memory_run_due_reflections,
                latest_period_review=(
                    deps.memory_latest_period_review
                ),
                period_reviews=deps.memory_period_reviews,
                run_period_review=deps.memory_run_period_review,
                latest_historical_replay=(
                    deps.memory_latest_historical_replay
                ),
                historical_replays=deps.memory_historical_replays,
                run_historical_replay=deps.memory_run_historical_replay,
                policy_scorecards=deps.memory_policy_scorecards,
                policy_rules=deps.memory_policy_rules,
                policy_revisions=deps.memory_policy_revisions,
                activate_policy_revision=(
                    deps.memory_activate_policy_revision
                ),
                reject_policy_revision=deps.memory_reject_policy_revision,
            ),
        ),
    ]
    route_factories.append(
        RouteFactorySpec(
            name="wiki",
            build_router=build_wiki_router,
            deps=WikiRouteDeps(
                require_admin_auth=deps.require_admin_auth,
                service=deps.wiki_service,
            ),
        )
    )
    return build_route_specs(route_factories)


def build_research_route_specs(
    deps: ResearchRouteGroupDeps,
) -> tuple[AppRouteSpec, ...]:
    return build_route_specs(
        (
            RouteFactorySpec(
                name="etf",
                build_router=build_etf_router,
                deps=ETFRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    repository_factory=deps.etf_repository_factory,
                    configured_universe=deps.etf_configured_universe,
                    expanded_universe=deps.etf_expanded_universe,
                    universe_item_payload=deps.etf_universe_item_payload,
                    settings_payload=deps.etf_settings_payload,
                    list_candidates=deps.etf_list_candidates,
                    read_only_auto_collect=deps.etf_read_only_auto_collect,
                    seed_universe=deps.etf_seed_universe,
                    symbols_from_payload=deps.etf_symbols_from_payload,
                    collect_snapshots=deps.etf_collect_snapshots,
                    fetch_quote=deps.etf_fetch_quote,
                ),
            ),
            RouteFactorySpec(
                name="crypto",
                build_router=build_crypto_router,
                deps=CryptoRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    crypto_research_service=deps.crypto_research_service,
                    crypto_alpha_service=deps.crypto_alpha_service,
                    crypto_research_symbols=deps.crypto_research_symbols,
                    default_crypto_research_symbols=deps.default_crypto_research_symbols,
                ),
            ),
            RouteFactorySpec(
                name="research",
                build_router=build_research_router,
                deps=build_research_route_deps(
                    require_admin_auth=deps.require_admin_auth,
                    helper_ask=deps.research_helper_ask,
                    settings=deps.research_settings,
                    naver_report_repository=deps.naver_report_repository,
                    naver_report_crawler=deps.naver_report_crawler,
                    rag_store=deps.rag_store,
                    symbol_fundamentals_service=deps.symbol_fundamentals_service,
                    build_report_intelligence_status=(
                        deps.build_report_intelligence_status
                    ),
                    run_report_collection_cycle=deps.run_report_collection_cycle,
                    sync_report_rag=deps.sync_report_rag,
                    seed_symbol_directory=deps.seed_symbol_directory,
                    on_rag_resolve_error=deps.on_rag_resolve_error,
                ),
            ),
        )
    )


def build_trading_route_specs(
    deps: TradingRouteGroupDeps,
) -> tuple[AppRouteSpec, ...]:
    return build_route_specs(
        (
            RouteFactorySpec(
                name="rebalance",
                build_router=build_rebalance_router,
                deps=RebalanceRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    kis_rebalance_status=deps.kis_rebalance_status,
                ),
            ),
            RouteFactorySpec(
                name="kis_blocks",
                build_router=build_kis_blocks_router,
                deps=KISBlockRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    primary_ready=deps.kis_primary_ready,
                    status=deps.kis_blocks_status,
                    snapshot=deps.kis_blocks_snapshot,
                    snapshot_compact=deps.kis_blocks_snapshot_compact,
                    attach_block_memory=deps.attach_kis_block_memory,
                    validation_repair_ops_summary=(
                        deps.kis_validation_repair_ops_summary
                    ),
                    ops_readiness=deps.ops_readiness,
                    status_readiness=deps.kis_blocks_status_readiness,
                    manager_run_once=deps.kis_manager_run_once,
                    adoption_run_once=deps.kis_adoption_run_once,
                    executor_tick=deps.kis_executor_tick,
                    set_kill_switch=deps.kis_set_kill_switch,
                    cancel_order=deps.kis_cancel_order,
                    block_detail=deps.kis_block_detail,
                    block_memory=deps.kis_block_memory,
                    add_user_directive=deps.kis_add_user_directive,
                    pause_block=deps.kis_pause_block,
                    resume_block=deps.kis_resume_block,
                    close_block=deps.kis_close_block,
                ),
            ),
            RouteFactorySpec(
                name="binance_blocks",
                build_router=build_binance_blocks_router,
                deps=build_binance_block_route_deps(
                    require_admin_auth=deps.require_admin_auth,
                    trader=deps.binance_trader,
                    memory_service=deps.binance_memory_service,
                    build_readiness=deps.build_binance_readiness,
                    quant_repository_factory=deps.binance_quant_repository_factory,
                    pattern_repository_cls=deps.binance_pattern_repository_cls,
                    pattern_db_path=deps.binance_pattern_db_path,
                    pattern_import_error=deps.binance_pattern_import_error,
                ),
            ),
            RouteFactorySpec(
                name="portfolio_coach",
                build_router=build_portfolio_coach_router,
                deps=PortfolioCoachRouteDeps(
                    require_admin_auth=deps.require_admin_auth,
                    list_advice_messages=deps.portfolio_list_advice_messages,
                    get_advice_message=deps.portfolio_get_advice_message,
                    update_message_status=deps.portfolio_update_message_status,
                    send_message=deps.portfolio_send_message,
                ),
            ),
        )
    )


__all__ = [
    "AssistantSupportRouteGroupDeps",
    "CoreAppRouteGroupDeps",
    "ObservabilityRouteGroupDeps",
    "ResearchRouteGroupDeps",
    "TradingRouteGroupDeps",
    "build_assistant_support_route_specs",
    "build_core_app_route_specs",
    "build_observability_route_specs",
    "build_research_route_specs",
    "build_trading_route_specs",
]
