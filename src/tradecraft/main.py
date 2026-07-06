from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
from tradecraft.api.binance_blocks_payloads import build_binance_block_readiness_payload
from tradecraft.api.dashboard_payloads import (
    DashboardPayloadCache,
    DashboardPayloadDeps,
    build_dashboard_payload as build_dashboard_payload_from_deps,
)
from tradecraft.api.evidence_payloads import (
    build_crypto_quant_status as build_evidence_crypto_quant_status,
    build_kr_equity_pattern_lab_status as build_evidence_kr_equity_pattern_lab_status,
    build_memory_read_only_status as build_evidence_memory_read_only_status,
    build_service_status as build_evidence_service_status,
    build_unavailable_service_status as build_evidence_unavailable_service_status,
)
from tradecraft.api.etf import (
    etf_auto_collect_skipped as build_etf_auto_collect_skipped,
    etf_read_only_auto_collect as build_etf_read_only_auto_collect,
    etf_symbols_from_payload as build_etf_symbols_from_payload,
    etf_universe_item_payload as build_etf_universe_item_payload,
)
from tradecraft.api.etf_payloads import (
    build_etf_research_candidates_payload as build_etf_research_candidates_payload_from_deps,
    merge_etf_items_payload as build_merge_etf_items_payload,
)
from tradecraft.api.helper_payloads import (
    build_helper_source_draft_answer as _build_helper_source_draft_answer,
    clean_helper_text as _clean_helper_text,
    clean_report_title as _clean_report_title,
    format_citation as _format_citation,
    helper_query_keywords as _helper_query_keywords,
    helper_report_has_exact_symbol_match as _helper_report_has_exact_symbol_match,
    helper_report_sort_key as _helper_report_sort_key,
    normalize_helper_answer_contract as _normalize_helper_answer_contract,
    parse_helper_llm_content as _parse_helper_llm_content,
    safe_helper_limit as _safe_helper_limit,
)
from tradecraft.api.kis_rebalance_payloads import (
    build_kis_block_rebalance_status_payload,
)
from tradecraft.api.research_payloads import build_reports_status_payload
from tradecraft.api.llm_payloads import (
    build_llm_usage_semantic_check as build_llm_usage_semantic_check_payload,
    build_llm_usage_status_payload as build_llm_usage_status_payload_from_summary,
    enrich_llm_usage_component_recovery as enrich_llm_usage_component_recovery_payload,
)
from tradecraft.api.ops_payloads import (
    build_disk_space_status as build_disk_space_status_payload,
    build_llm_operational_status as build_llm_operational_status_payload,
    build_ops_binance_block_trader_payload as build_ops_binance_block_trader_payload_payload,
    build_ops_crypto_alpha_payload as build_ops_crypto_alpha_payload_payload,
    build_ops_crypto_market_research_payload as build_ops_crypto_market_research_payload_payload,
    build_ops_environment_signals as build_ops_environment_signals_payload,
    build_ops_jue_wiki_payload as build_ops_jue_wiki_payload_payload,
    build_ops_kis_block_trader_payload as build_ops_kis_block_trader_payload_payload,
    build_ops_live_evaluator_payload as build_ops_live_evaluator_payload_payload,
    build_ops_market_judge_payload as build_ops_market_judge_payload_payload,
    build_ops_market_pulse_payload as build_ops_market_pulse_payload_payload,
    build_ops_memory_payload as build_ops_memory_payload_payload,
    build_ops_reports_payload as build_ops_reports_payload_payload,
    build_ops_runner_liveness as build_ops_runner_liveness_payload,
    build_ops_trading_validation_payload as build_ops_trading_validation_payload_payload,
    build_ops_watchdog_payload as build_ops_watchdog_payload_payload,
    finalize_ops_readiness_signals as build_finalize_ops_readiness_signals_payload,
    merge_section_readiness_signals as merge_ops_section_readiness_signals_payload,
)
from tradecraft.api.ops_readiness import (
    build_core_runner_processes as build_core_runner_processes_payload,
    build_market_judgment_readiness_status,
    build_market_pulse_readiness_status,
    build_ops_readiness_payload,
    light_runner_process_status as build_light_runner_process_status,
    runner_status_with_cover as build_runner_status_with_cover,
)
from tradecraft.api.trading_validation_payloads import (
    aggregate_trading_validation_lane_authority as build_aggregate_trading_validation_lane_authority,
    aggregate_trading_validation_venue_payloads as build_aggregate_trading_validation_venue_payloads,
    annotate_trading_validation_freshness as build_annotate_trading_validation_freshness,
    promote_trading_validation_payload_fields as build_promote_trading_validation_payload_fields,
    summarize_trading_validation_bottlenecks as build_summarize_trading_validation_bottlenecks,
    summarize_trading_validation_next_actions as build_summarize_trading_validation_next_actions,
)
from tradecraft.backtest.data_registry import BacktestDataRegistry
from tradecraft.backtest.engine import BacktestConfig
from tradecraft.backtest.live_manager import BacktestLiveManager
from tradecraft.backtest.scenarios import apply_scenario, list_scenarios
from tradecraft.config import AppSettings
from tradecraft.runtime.live_evaluator_runner import (
    build_live_authority_payload as build_runner_live_authority_payload,
    sync_live_performance_and_edges,
)
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.binance_block_trader import (
    BinanceBlockTrader,
    BinanceBlockTraderConfig,
)
from tradecraft.services.binance_risk import BinanceRiskConfig, BinanceRiskSizer
try:
    from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService
except ImportError as exc:
    CryptoAlphaConfig = None  # type: ignore[assignment]
    CryptoAlphaService = None  # type: ignore[assignment]
    _crypto_alpha_import_error: ImportError | None = exc
else:
    _crypto_alpha_import_error = None
try:
    from tradecraft.services.crypto_market_research import (
        CryptoMarketResearchConfig,
        CryptoMarketResearchService,
    )
except ImportError as exc:
    CryptoMarketResearchConfig = None  # type: ignore[assignment]
    CryptoMarketResearchService = None  # type: ignore[assignment]
    _crypto_market_research_import_error: ImportError | None = exc
else:
    _crypto_market_research_import_error = None
from tradecraft.services.crypto_quant import CryptoQuantRepository
try:
    from tradecraft.services.crypto_pattern_lab import (
        CryptoPatternLabConfig,
        CryptoPatternLabRepository,
        CryptoPatternLabService,
        HermesKlineReader,
    )
except Exception as exc:
    CryptoPatternLabConfig = None  # type: ignore[assignment]
    CryptoPatternLabRepository = None  # type: ignore[assignment]
    CryptoPatternLabService = None  # type: ignore[assignment]
    HermesKlineReader = None  # type: ignore[assignment]
    _crypto_pattern_lab_import_error: Exception | None = exc
else:
    _crypto_pattern_lab_import_error = None
from tradecraft.services.bithumb import BithumbAdapter, BithumbConfig
from tradecraft.services.daily_discovery import DailyDiscoveryConfig, DailyDiscoveryService
from tradecraft.services.etf_research import (
    ConfiguredETFResearchProvider,
    ETFResearchRepository,
    ETFUniverseItem,
    collect_etf_research as collect_etf_research_snapshots,
    expand_default_etf_universe,
    fetch_naver_etf_universe,
    merge_etf_universe,
    parse_etf_universe_config,
    stale_etf_symbols,
)
from tradecraft.services.fx import FxRateConfig, FxRateService
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.kis_block_trader import (
    KISBlockTrader,
    KISBlockTraderConfig,
)
from tradecraft.services.kis_config_policy import (
    parse_etf_universe,
    parse_horizon_targets,
)
from tradecraft.services.kr_equity_pattern_lab import KREquityPatternLabRepository
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.jue_lifecycle import JueLifecycleRepository
from tradecraft.services.jue_skill_registry import JueSkillRegistry, JueSkillValidationError
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_selector import (
    JueWikiSelectionRequest,
    JueWikiSelector,
    resolve_jue_wiki_prompt_mode,
)
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
)
from tradecraft.services.codex_native_store import CodexNativeStore
from tradecraft.services.llm_usage import KST, LLMUsageRepository
from tradecraft.services.live_authority import (
    EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
)
from tradecraft.services.trading_validation import (
    TradingValidationConfig,
    TradingValidationService,
)
from tradecraft.services.intelligence import (
    build_report_intelligence_status,
    build_report_intelligence_stack,
    run_report_collection_cycle,
    sync_report_rag,
)
from tradecraft.services.portfolio_coach import PortfolioCoachStore
from tradecraft.services.market import (
    empty_dashboard_template,
    replace_venue_assets,
    upsert_venue_assets,
)
from tradecraft.services.market_judgment import (
    MarketJudgmentConfig,
    MarketJudgmentEngine,
    next_krx_decision_due_at,
)
from tradecraft.services.opportunity_scanner import rank_opportunities
from tradecraft.services.market_pulse import MarketPulseConfig, MarketPulseService
from tradecraft.services.runtime_bridge import (
    ResearchSnapshotReader,
    RuntimeSnapshotReader,
)
from tradecraft.services.runtime_maintenance import (
    RuntimeStoragePolicy,
    build_runtime_storage_report,
    cleanup_runtime_storage,
)
from tradecraft.services.settings_catalog import (
    build_settings_catalog,
    update_settings_env,
)
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
    StrategyInsightCollector,
    classify_strategy_intent,
)
from tradecraft.services.symbol_analysis import SymbolAnalysisService
from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsConfig,
    SymbolFundamentalsService,
    jue_wiki_repair_target_symbols,
    merge_fundamental_target_symbols,
)
from tradecraft.services.system_metrics import SystemMetricsService
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    restart_runner_processes,
    runner_process_status,
    write_current_runner_pid,
)
from tradecraft.runtime.watchdog_runner import watchdog_status
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.session_loader import load_runtime_sessions
from tradecraft.reports_api.auth import is_valid_request_token_any, is_valid_token_any
from tradecraft.services.telegram import TelegramBridge, TelegramConfig
from tradecraft.services.telegram_cli import TelegramCLI
from tradecraft.services.upbit import UpbitAdapter, UpbitConfig

settings = AppSettings()
_ETF_RESEARCH_AUTO_COLLECT_LAST_ATTEMPT_AT: datetime | None = None
system_metrics_service = SystemMetricsService(root_path=str(Path.cwd()))
backtest_live_manager = BacktestLiveManager(
    state_path=settings.backtest_state_path,
    result_path=settings.backtest_result_path,
    max_curve_points=settings.backtest_max_curve_points,
)
backtest_data_registry = BacktestDataRegistry(settings.backtest_data_registry_path)
JUE_WORKFLOW_IDS = (
    "kis_pre_open",
    "kis_intraday_manager",
    "kis_post_close",
    "block_reflection",
    "policy_revision",
    "crypto_research",
    "binance_cycle",
)


def _available_jue_workflow_ids(registry: JueSkillRegistry) -> list[str]:
    workflow_dir = registry.root / "workflows"
    discovered = sorted(
        path.stem
        for path in workflow_dir.glob("*.json")
        if path.is_file()
    )
    preferred = [workflow_id for workflow_id in JUE_WORKFLOW_IDS if workflow_id in discovered]
    preferred_set = set(preferred)
    return preferred + [workflow_id for workflow_id in discovered if workflow_id not in preferred_set]


def _require_configured_admin_tokens() -> list[str]:
    tokens = settings.admin_token_list
    if not tokens:
        raise HTTPException(status_code=401, detail="admin auth required")
    return tokens


def require_admin_auth(
    authorization: str | None = Header(default=None),
    admin_token: str | None = Header(default=None, alias="X-TradeCraft-Admin-Token"),
) -> None:
    tokens = _require_configured_admin_tokens()
    if not authorization and not admin_token:
        raise HTTPException(status_code=401, detail="admin auth required")
    if not is_valid_request_token_any(authorization, admin_token, tokens):
        raise HTTPException(status_code=401, detail="unauthorized")


def _is_allowed_telegram_chat(chat_id: str) -> bool:
    expected = str(telegram.config.chat_id or settings.telegram_chat_id or "").strip()
    provided = str(chat_id or "").strip()
    return bool(expected and provided and is_valid_token_any(provided, [expected]))


def _validate_telegram_webhook_secret(secret: str | None) -> None:
    expected = str(settings.telegram_webhook_secret or "").strip()
    if not expected:
        raise HTTPException(status_code=401, detail="telegram webhook secret required")
    if not is_valid_token_any(secret, [expected]):
        raise HTTPException(status_code=401, detail="unauthorized")


def _settings_symbols_from_csv(value: Any) -> list[str]:
    return [
        symbol
        for symbol in dict.fromkeys(
            item.strip()
            for item in re.split(r"[\s,;]+", str(value or ""))
            if item.strip()
        )
        if re.fullmatch(r"\d{6}", symbol)
    ]


def _to_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


class _UnavailableCryptoMarketResearchService:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "db_path": settings.crypto_market_research_db_path,
            "snapshot_count": 0,
            "candidate_count": 0,
            "reason": self.reason,
        }

    def latest_context(
        self,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "symbols": symbols or [],
            "limit": limit,
            "items": [],
            "market_regime": {"status": "missing", "regime": "unknown"},
            "observed_symbol_count": len(symbols or []),
            "focus_symbol_count": 0,
            "candidates": [],
            "symbol_notes": {},
            "features": {},
            "reason": self.reason,
        }

    async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "symbols": symbols,
            "reason": self.reason,
        }

    async def run_research_once(
        self,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "symbols": symbols or [],
            "reason": self.reason,
        }


class _UnavailableCryptoAlphaService:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "db_path": settings.crypto_alpha_db_path,
            "sources": 0,
            "snapshots": 0,
            "events": 0,
            "outcomes": 0,
            "hypotheses": 0,
            "reason": self.reason,
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "scope": "binance_crypto_alpha",
            "symbols": symbols or [],
            "limit": limit,
            "events": [],
            "similar_outcomes": [],
            "scorecards": [],
            "active_lessons": [],
            "contradictions": [],
            "data_gaps": ["crypto_alpha_unavailable"],
            "reason": self.reason,
        }

    async def collect_once(self) -> dict[str, Any]:
        return {"status": "skipped", "available": False, "reason": self.reason}

    async def label_due_outcomes(self) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "reason": self.reason,
            "labeled": 0,
        }


def _crypto_research_symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = [str(raw)]
    return [
        symbol
        for symbol in dict.fromkeys(item.strip().upper() for item in values)
        if symbol and re.fullmatch(r"[A-Z0-9:_-]{2,30}", symbol)
    ]


def _default_crypto_research_symbols() -> list[str]:
    return _crypto_research_symbols(settings.crypto_market_research_universe)


async def _await_if_needed(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _jue_wiki_arg_list(kwargs: dict[str, Any], name: str) -> list[str]:
    raw = kwargs.get(name)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = [raw]
    return [str(value).strip() for value in values if str(value).strip()]


def _jue_wiki_prompt_mode() -> str:
    mode = str(settings.jue_wiki_prompt_mode or "assist").strip().lower()
    return mode if mode in {"observe", "assist", "primary"} else "assist"


def _jue_wiki_context_provider(**kwargs: Any) -> dict[str, Any]:
    if not bool(settings.jue_wiki_enabled):
        return {
            "status": "disabled",
            "enabled": False,
            "reason": "jue_wiki_disabled",
            "target_scope": str(kwargs.get("target_scope") or ""),
            "symbols": _jue_wiki_arg_list(kwargs, "symbols"),
            "prompt_mode": _jue_wiki_prompt_mode(),
            "pages": [],
            "content": "",
            "budget_report": {
                "status": "disabled",
                "char_count": 0,
                "max_chars": 0,
                "selected_count": 0,
                "rejected_count": 0,
            },
        }
    default_max_chars = int(
        getattr(
            settings,
            "jue_wiki_full_prompt_max_chars",
            settings.jue_wiki_context_max_chars,
        )
    )
    result = JueWikiSelector(jue_wiki_service).select(
        JueWikiSelectionRequest(
            target_scope=str(kwargs.get("target_scope") or ""),
            symbols=_jue_wiki_arg_list(kwargs, "symbols"),
            page_types=_jue_wiki_arg_list(kwargs, "page_types"),
            lanes=_jue_wiki_arg_list(kwargs, "lanes"),
            regimes=_jue_wiki_arg_list(kwargs, "regimes"),
            block_ids=_jue_wiki_arg_list(kwargs, "block_ids"),
            horizons=_jue_wiki_arg_list(kwargs, "horizons"),
            max_chars=int(
                kwargs["max_chars"]
                if kwargs.get("max_chars") is not None
                else default_max_chars
            ),
            max_pages=int(settings.jue_wiki_selector_max_pages),
            min_confidence=float(settings.jue_wiki_selector_min_confidence),
            exclude_lint_warnings=bool(settings.jue_wiki_exclude_lint_warnings),
            effectiveness_weight=float(settings.jue_wiki_effectiveness_weight),
            effectiveness_max_adjustment=float(
                settings.jue_wiki_effectiveness_max_adjustment
            ),
        )
    )
    configured_prompt_mode = _jue_wiki_prompt_mode()
    prompt_mode_resolution = resolve_jue_wiki_prompt_mode(
        configured_prompt_mode,
        result.mode_recommendation,
    )
    return {
        "status": result.status,
        "selection_run_id": result.selection_run_id,
        "target_scope": result.target_scope,
        "prompt_mode": prompt_mode_resolution["prompt_mode"],
        "configured_prompt_mode": prompt_mode_resolution["configured_prompt_mode"],
        "mode_recommendation": prompt_mode_resolution["mode_recommendation"],
        "prompt_mode_policy": prompt_mode_resolution["prompt_mode_policy"],
        "content": result.content,
        "effectiveness_policy": result.effectiveness_policy,
        "repair_priorities": result.repair_priorities,
        "repair_action_batches": getattr(result, "repair_action_batches", []),
        "evidence_quality": getattr(result, "evidence_quality", {}),
        "requested_symbol_summaries": result.requested_symbol_summaries,
        "trust_profile_effectiveness": getattr(
            result,
            "trust_profile_effectiveness",
            {},
        ),
        "repair_priority_effectiveness": getattr(
            result,
            "repair_priority_effectiveness",
            {},
        ),
        "validation_repair_effectiveness": getattr(
            result,
            "validation_repair_effectiveness",
            {},
        ),
        "wiki_application_coverage": getattr(result, "wiki_application_coverage", {}),
        "pages": [
            {
                "page_id": page.page_id,
                "rank": page.rank,
                "score": page.score,
                "selection_reasons": page.reasons,
                "selection_penalties": page.penalties,
                "char_count": page.char_count,
                "source_refs": page.source_refs,
                "effectiveness": page.effectiveness,
                "evidence_quality": page.evidence_quality,
                "quality_status": page.quality_status,
                "quality_warnings": page.quality_warnings,
            }
            for page in result.pages
        ],
        "rejected_pages": result.rejected_pages,
        "budget_report": result.budget_report,
    }


def _build_crypto_market_research_service(
    codex_runtime: CodexNativeRuntime,
) -> Any:
    if CryptoMarketResearchService is None or CryptoMarketResearchConfig is None:
        reason = "crypto market research service is not importable"
        if _crypto_market_research_import_error is not None:
            reason = str(_crypto_market_research_import_error)
        return _UnavailableCryptoMarketResearchService(reason=reason)

    config = CryptoMarketResearchConfig(
        db_path=settings.crypto_market_research_db_path,
        max_symbols=settings.crypto_market_research_max_symbols,
        llm_model=settings.crypto_market_research_llm_model,
        llm_reasoning_effort=settings.crypto_market_research_llm_reasoning_effort,
        external_enabled=settings.crypto_market_research_external_enabled,
        external_sources=settings.crypto_market_research_external_sources,
        auto_universe_enabled=settings.crypto_market_research_auto_universe_enabled,
        auto_universe_limit=settings.crypto_market_research_auto_universe_limit,
        research_universe_limit=settings.crypto_market_research_research_universe_limit,
        llm_top_symbols=settings.crypto_market_research_llm_top_symbols,
        min_quote_volume_usdt=settings.crypto_market_research_min_quote_volume_usdt,
        kline_intervals=_parse_crypto_kline_intervals(
            settings.crypto_market_research_kline_intervals
        ),
        kline_hot_window_rows=settings.crypto_market_research_kline_hot_window_rows,
        market_hot_window_rows=settings.crypto_market_research_market_hot_window_rows,
        regime_enabled=settings.crypto_market_research_regime_enabled,
        squeeze_guard_enabled=settings.crypto_market_research_squeeze_guard_enabled,
    )
    return CryptoMarketResearchService(
        config=config,
        binance=binance,
        codex_runtime=codex_runtime,
        memory_provider=investment_memory_service.context_pack,
        quant_repository=crypto_quant_repository if settings.crypto_quant_enabled else None,
    )


def _build_crypto_alpha_service() -> Any:
    if CryptoAlphaService is None or CryptoAlphaConfig is None:
        reason = "crypto alpha service is not importable"
        if _crypto_alpha_import_error is not None:
            reason = str(_crypto_alpha_import_error)
        return _UnavailableCryptoAlphaService(reason=reason)
    return CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=settings.crypto_alpha_db_path,
            source_ids=settings.crypto_alpha_source_ids,
            rate_limit_sec=settings.crypto_alpha_rate_limit_sec,
            context_limit=settings.crypto_alpha_context_limit,
            llm_model=settings.crypto_alpha_llm_model,
            llm_reasoning_effort=settings.crypto_alpha_llm_reasoning_effort,
        ),
        binance=binance,
    )


def _build_crypto_pattern_service() -> Any | None:
    if not bool(_setting("crypto_pattern_lab_enabled", True)):
        return None
    if (
        CryptoPatternLabConfig is None
        or CryptoPatternLabService is None
        or HermesKlineReader is None
    ):
        return None
    return CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=str(_setting("crypto_pattern_lab_db_path", ".runtime/crypto_pattern_lab.db")),
            enabled=bool(_setting("crypto_pattern_lab_enabled", True)),
            strategy_paths=str(_setting("crypto_pattern_lab_strategy_paths", "")),
            freqtrade_data_paths=str(_setting("crypto_pattern_lab_freqtrade_data_paths", "")),
            max_symbols=int(_setting("crypto_pattern_lab_max_symbols", 30)),
            intervals=str(_setting("crypto_pattern_lab_intervals", "5m,15m,1h")),
            lookback_bars=int(_setting("crypto_pattern_lab_lookback_bars", 500)),
            context_limit=int(_setting("crypto_pattern_lab_context_limit", 12)),
            retention_days=int(_setting("crypto_pattern_lab_retention_days", 90)),
            backtests_per_tuple_retention=int(
                _setting("crypto_pattern_lab_backtests_per_tuple_retention", 4)
            ),
            optimizer_runs_per_tuple_retention=int(
                _setting("crypto_pattern_lab_optimizer_runs_per_tuple_retention", 4)
            ),
            optimizer_trials_per_run_retention=int(
                _setting("crypto_pattern_lab_optimizer_trials_per_run_retention", 8)
            ),
            max_backtest_rows=int(
                _setting("crypto_pattern_lab_max_backtest_rows", 80_000)
            ),
            max_optimizer_runs=int(
                _setting("crypto_pattern_lab_max_optimizer_runs", 2_500)
            ),
            max_optimizer_trials=int(
                _setting("crypto_pattern_lab_max_optimizer_trials", 24_000)
            ),
        ),
        kline_reader=HermesKlineReader(
            str(
                _setting(
                    "crypto_market_research_db_path",
                    ".runtime/crypto_market_research.db",
                )
            )
        ),
    )


def _parse_crypto_kline_intervals(value: Any) -> dict[str, int]:
    intervals: dict[str, int] = {}
    for part in re.split(r"[,;]+", str(value or "")):
        if ":" not in part:
            continue
        key, raw_limit = part.split(":", 1)
        interval = key.strip()
        try:
            limit = int(str(raw_limit).strip())
        except ValueError:
            continue
        if interval and limit > 0:
            intervals[interval] = limit
    return intervals or {"1m": 120, "5m": 96, "15m": 96, "1h": 168, "4h": 180}


telegram = TelegramBridge(
    TelegramConfig(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
)
upbit = UpbitAdapter(
    UpbitConfig(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
        base_url=settings.upbit_base_url,
    )
)
bithumb = BithumbAdapter(
    BithumbConfig(
        access_key=settings.bithumb_access_key,
        secret_key=settings.bithumb_secret_key,
        base_url=settings.bithumb_base_url,
    )
)
binance = BinanceAdapter(
    BinanceConfig(
        spot_api_key=settings.binance_spot_api_key,
        spot_api_secret=settings.binance_spot_api_secret,
        spot_base_url=settings.binance_spot_base_url,
        futures_api_key=settings.binance_futures_api_key,
        futures_api_secret=settings.binance_futures_api_secret,
        futures_base_url=settings.binance_futures_base_url,
        usdt_krw_rate=settings.binance_usdt_krw,
    )
)
kis_primary = KISAdapter(
    KISConfig(
        app_key=settings.kis_primary_app_key,
        app_secret=settings.kis_primary_app_secret,
        account_no=settings.kis_primary_account_no,
        product_code=settings.kis_primary_product_code,
        base_url=settings.kis_base_url,
        rate_limit_enabled=settings.kis_rate_limit_enabled,
        rest_rate_limit_per_sec=settings.kis_rest_rate_limit_per_sec,
        account_min_interval_sec=settings.kis_account_min_interval_sec,
        token_min_interval_sec=settings.kis_token_min_interval_sec,
        rate_limit_db_path=settings.kis_rate_limit_db_path,
    )
)
kis_secondary = KISAdapter(
    KISConfig(
        app_key=settings.kis_secondary_app_key,
        app_secret=settings.kis_secondary_app_secret,
        account_no=settings.kis_secondary_account_no,
        product_code=settings.kis_secondary_product_code,
        base_url=settings.kis_base_url,
        rate_limit_enabled=settings.kis_rate_limit_enabled,
        rest_rate_limit_per_sec=settings.kis_rest_rate_limit_per_sec,
        account_min_interval_sec=settings.kis_account_min_interval_sec,
        token_min_interval_sec=settings.kis_token_min_interval_sec,
        rate_limit_db_path=settings.kis_rate_limit_db_path,
    )
)
telegram_cli = TelegramCLI(empty_dashboard_template)
runtime_reader = RuntimeSnapshotReader(
    path=settings.runtime_state_path,
    max_age_sec=settings.runtime_max_age_sec,
)
research_reader = ResearchSnapshotReader(
    path=settings.research_state_path,
    max_age_sec=settings.research_max_age_sec,
)
portfolio_coach_store = PortfolioCoachStore(settings.portfolio_coach_db_path)
report_intelligence_stack = build_report_intelligence_stack(settings)
naver_report_repository = report_intelligence_stack.repository
naver_report_crawler = report_intelligence_stack.crawler
rag_store = report_intelligence_stack.rag_store
helper_codex_runtime = CodexNativeRuntime(
    CodexNativeConfig(
        mode=settings.codex_runtime_mode,
        sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
        timeout_ms=settings.codex_runtime_timeout_ms,
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        usage_enabled=settings.llm_usage_enabled,
        usage_db_path=settings.llm_usage_db_path,
        usage_component="research_ask",
        thread_mode=settings.codex_native_thread_mode,
        thread_db_path=settings.codex_native_thread_db_path,
        compact_after_turns=settings.codex_native_compact_after_turns,
        read_turns=settings.codex_native_read_turns,
        developer_instructions_enabled=settings.codex_native_developer_instructions_enabled,
    )
)
daily_discovery_codex_runtime = CodexNativeRuntime(
    CodexNativeConfig(
        mode=settings.codex_runtime_mode,
        sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
        timeout_ms=settings.codex_runtime_timeout_ms,
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        usage_enabled=settings.llm_usage_enabled,
        usage_db_path=settings.llm_usage_db_path,
        usage_component="daily_discovery",
        thread_mode=settings.codex_native_thread_mode,
        thread_db_path=settings.codex_native_thread_db_path,
        compact_after_turns=settings.codex_native_compact_after_turns,
        read_turns=settings.codex_native_read_turns,
        developer_instructions_enabled=settings.codex_native_developer_instructions_enabled,
    )
)
symbol_fundamentals_service = SymbolFundamentalsService(
    SymbolFundamentalsConfig(
        db_path=settings.valuation_db_path,
        timeout_sec=settings.valuation_timeout_sec,
        min_refresh_hours=settings.valuation_min_refresh_hours,
        max_symbols_per_collect=settings.valuation_max_symbols_per_collect,
        jue_wiki_db_path=settings.jue_wiki_db_path,
    )
)
investment_memory_service = InvestmentMemoryService(
    config=InvestmentMemoryConfig(
        root_path=settings.investment_memory_root_path,
        db_path=settings.investment_memory_db_path,
        strategy_md_path=settings.research_strategy_md_path,
        policy_mode=settings.investment_memory_policy_mode,
        persona_tone=settings.investment_memory_persona_tone,
        telegram_enabled=settings.investment_memory_send_telegram,
        context_max_chars=settings.investment_memory_context_max_chars,
        ops_summary_cache_ttl_sec=settings.investment_memory_ops_summary_cache_ttl_sec,
    ),
    codex_runtime=helper_codex_runtime,
    telegram=telegram,
    wiki_context_provider=_jue_wiki_context_provider,
)
binance_manager_codex_runtime = CodexNativeRuntime(
    CodexNativeConfig(
        mode=settings.codex_runtime_mode,
        sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
        timeout_ms=settings.codex_runtime_timeout_ms,
        model=settings.binance_block_trader_llm_model,
        reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
        usage_enabled=settings.llm_usage_enabled,
        usage_db_path=settings.llm_usage_db_path,
        usage_component="binance_block_manager",
        thread_mode=settings.codex_native_thread_mode,
        thread_db_path=settings.codex_native_thread_db_path,
        compact_after_turns=settings.codex_native_compact_after_turns,
        read_turns=settings.codex_native_read_turns,
        developer_instructions_enabled=settings.codex_native_developer_instructions_enabled,
    )
)
crypto_market_research_codex_runtime = CodexNativeRuntime(
    CodexNativeConfig(
        mode=settings.codex_runtime_mode,
        sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
        timeout_ms=settings.codex_runtime_timeout_ms,
        model=settings.crypto_market_research_llm_model,
        reasoning_effort=settings.crypto_market_research_llm_reasoning_effort,
        usage_enabled=settings.llm_usage_enabled,
        usage_db_path=settings.llm_usage_db_path,
        usage_component="crypto_market_research",
        thread_mode=settings.codex_native_thread_mode,
        thread_db_path=settings.codex_native_thread_db_path,
        compact_after_turns=settings.codex_native_compact_after_turns,
        read_turns=settings.codex_native_read_turns,
        developer_instructions_enabled=settings.codex_native_developer_instructions_enabled,
    )
)
crypto_quant_repository = CryptoQuantRepository(settings.crypto_quant_db_path)
crypto_market_research_service = _build_crypto_market_research_service(
    crypto_market_research_codex_runtime
)
crypto_alpha_service = _build_crypto_alpha_service()
crypto_pattern_service = _build_crypto_pattern_service()
binance_block_trader = BinanceBlockTrader(
    config=BinanceBlockTraderConfig(
        db_path=settings.binance_block_trader_db_path,
        state_path=settings.binance_block_trader_state_path,
        live_performance_db_path=settings.live_performance_db_path,
        enabled=settings.binance_block_trader_enabled,
        execute_spot_orders=settings.binance_block_trader_execute_spot_orders,
        execute_futures_orders=settings.binance_block_trader_execute_futures_orders,
        execute_upbit_orders=settings.binance_block_trader_execute_upbit_orders,
        quote_interval_sec=settings.binance_block_trader_quote_interval_sec,
        rule_interval_sec=settings.binance_block_trader_rule_interval_sec,
        manager_interval_sec=settings.binance_block_trader_manager_interval_sec,
        aggressive_limit_bps=settings.binance_block_trader_aggressive_limit_bps,
        failed_exit_retry_cooldown_sec=(
            settings.binance_block_trader_failed_exit_retry_cooldown_sec
        ),
        min_entry_confidence=settings.binance_block_trader_min_entry_confidence,
        min_entry_expected_r=settings.binance_block_trader_min_entry_expected_r,
        min_entry_directional_score=(
            settings.binance_block_trader_min_entry_directional_score
        ),
        min_candidate_stop_pct=settings.binance_block_trader_min_candidate_stop_pct,
        profit_lock_trigger_r=settings.binance_block_trader_profit_lock_trigger_r,
        weak_lane_profit_lock_trigger_r=(
            settings.binance_block_trader_weak_lane_profit_lock_trigger_r
        ),
        distressed_lane_profit_lock_trigger_r=(
            settings.binance_block_trader_distressed_lane_profit_lock_trigger_r
        ),
        entry_quality_loss_tighten_trigger_r=(
            settings.binance_block_trader_entry_quality_loss_tighten_trigger_r
        ),
        distressed_lane_min_samples=(
            settings.binance_block_trader_distressed_lane_min_samples
        ),
        distressed_lane_max_win_rate_pct=(
            settings.binance_block_trader_distressed_lane_max_win_rate_pct
        ),
        distressed_lane_max_profit_factor=(
            settings.binance_block_trader_distressed_lane_max_profit_factor
        ),
        distressed_entry_quality_partial_profit_fraction=(
            settings.binance_block_trader_distressed_entry_quality_partial_profit_fraction
        ),
        profit_lock_stop_r=settings.binance_block_trader_profit_lock_stop_r,
        profit_lock_min_net_buffer_pct=(
            settings.binance_block_trader_profit_lock_min_net_buffer_pct
        ),
        spot_quote_budget_pct=settings.binance_block_trader_spot_quote_budget_pct,
        spot_min_quote_budget_usdt=(
            settings.binance_block_trader_spot_min_quote_budget_usdt
        ),
        spot_max_quote_budget_usdt=(
            settings.binance_block_trader_spot_max_quote_budget_usdt
        ),
        futures_quote_budget_pct=(
            settings.binance_block_trader_futures_quote_budget_pct
        ),
        futures_min_quote_budget_usdt=(
            settings.binance_block_trader_futures_min_quote_budget_usdt
        ),
        futures_max_quote_budget_usdt=(
            settings.binance_block_trader_futures_max_quote_budget_usdt
        ),
        volatile_attack_enabled=settings.binance_block_trader_volatile_attack_enabled,
        volatile_attack_candidate_limit=(
            settings.binance_block_trader_volatile_attack_candidate_limit
        ),
        volatile_attack_budget_multiplier=(
            settings.binance_block_trader_volatile_attack_budget_multiplier
        ),
        volatile_attack_min_change_pct=(
            settings.binance_block_trader_volatile_attack_min_change_pct
        ),
        volatile_attack_min_volume_expansion=(
            settings.binance_block_trader_volatile_attack_min_volume_expansion
        ),
        volatile_attack_min_reward_risk=(
            settings.binance_block_trader_volatile_attack_min_reward_risk
        ),
        volatile_attack_stop_multiplier=(
            settings.binance_block_trader_volatile_attack_stop_multiplier
        ),
        budget_performance_scale_enabled=(
            settings.binance_block_trader_budget_performance_scale_enabled
        ),
        budget_performance_scale_min_samples=(
            settings.binance_block_trader_budget_performance_scale_min_samples
        ),
        budget_performance_scale_win_rate_pct=(
            settings.binance_block_trader_budget_performance_scale_win_rate_pct
        ),
        budget_performance_scale_multiplier=(
            settings.binance_block_trader_budget_performance_scale_multiplier
        ),
        llm_timeout_ms=settings.binance_block_trader_llm_timeout_ms,
        max_manager_symbols=settings.binance_block_trader_max_manager_symbols,
        quant_context_limit=settings.crypto_quant_context_limit,
        prompt_target_chars=settings.binance_block_trader_prompt_target_chars,
        prompt_warn_chars=settings.binance_block_trader_prompt_warn_chars,
        prompt_max_chars=settings.binance_block_trader_prompt_max_chars,
        jue_wiki_context_max_chars=(
            settings.binance_block_trader_jue_wiki_context_max_chars
        ),
        max_futures_leverage=settings.binance_block_trader_max_futures_leverage,
        min_liquidation_distance_pct=(
            settings.binance_block_trader_min_liquidation_distance_pct
        ),
        spot_universe=settings.binance_block_trader_spot_universe,
        futures_universe=settings.binance_block_trader_futures_universe,
        upbit_universe=settings.binance_block_trader_upbit_universe,
        upbit_quote_budget_pct=settings.binance_block_trader_upbit_quote_budget_pct,
        upbit_min_quote_budget_krw=(
            settings.binance_block_trader_upbit_min_quote_budget_krw
        ),
        upbit_max_quote_budget_krw=(
            settings.binance_block_trader_upbit_max_quote_budget_krw
        ),
        upbit_usdt_krw_rate=settings.binance_usdt_krw,
        llm_model=settings.binance_block_trader_llm_model,
        llm_reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
    ),
    binance=binance,
    upbit=upbit,
    codex_runtime=binance_manager_codex_runtime,
    memory_context_provider=investment_memory_service.context_pack,
    wiki_context_provider=_jue_wiki_context_provider,
    crypto_research_provider=crypto_market_research_service,
    crypto_alpha_provider=crypto_alpha_service,
    quant_provider=crypto_quant_repository if settings.crypto_quant_enabled else None,
    crypto_pattern_provider=crypto_pattern_service,
    live_authority_provider=lambda: build_runner_live_authority_payload(settings)[
        "venues"
    ]["binance"],
    risk_sizer=BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=settings.binance_block_trader_account_risk_pct,
            max_total_exposure_usdt=(
                settings.binance_block_trader_max_total_exposure_usdt
            ),
            max_symbol_exposure_pct=(
                settings.binance_block_trader_max_symbol_exposure_pct
            ),
            min_reward_risk=settings.binance_block_trader_min_reward_risk,
        )
    ),
)
etf_research_provider = ConfiguredETFResearchProvider(
    repository_factory=lambda: ETFResearchRepository(settings.etf_research_db_path),
    universe_provider=lambda: parse_etf_universe_config(settings.etf_research_universe),
    symbol_directory_provider=lambda: _symbol_directory_etf_rows(limit=200),
)
jue_wiki_service = JueWikiService(
    config=JueWikiConfig(
        root_path=Path(settings.jue_wiki_root_path),
        db_path=Path(settings.jue_wiki_db_path),
        enabled=bool(settings.jue_wiki_enabled),
        context_max_chars=settings.jue_wiki_context_max_chars,
        page_max_chars=settings.jue_wiki_page_max_chars,
        context_page_limit=settings.jue_wiki_context_page_limit,
        kis_blocks_db_path=Path(settings.kis_block_trader_db_path),
        binance_blocks_db_path=Path(settings.binance_block_trader_db_path),
        investment_memory_db_path=Path(settings.investment_memory_db_path),
        daily_discovery_db_path=Path(settings.daily_discovery_db_path),
        trading_validation_db_path=Path(settings.trading_validation_db_path),
        naver_reports_db_path=Path(settings.naver_reports_db_path),
        crypto_market_research_db_path=Path(settings.crypto_market_research_db_path),
        market_pulse_db_path=Path(settings.market_pulse_db_path),
        etf_research_db_path=Path(settings.etf_research_db_path),
        strategy_insights_db_path=Path(settings.strategy_insight_db_path),
        crypto_quant_db_path=Path(settings.crypto_quant_db_path),
        crypto_pattern_lab_db_path=Path(settings.crypto_pattern_lab_db_path),
        crypto_alpha_db_path=Path(settings.crypto_alpha_db_path),
    ),
    rag_store=rag_store,
    etf_research_provider=etf_research_provider,
    crypto_market_research_provider=crypto_market_research_service,
)
strategy_intelligence = StrategyIntelligenceEngine(
    repository=naver_report_repository,
    rag_store=rag_store,
    codex_runtime=helper_codex_runtime,
    fundamentals_repository=symbol_fundamentals_service,
    etf_research_repository=etf_research_provider,
    config=StrategyIntelligenceConfig(
        insight_db_path=settings.strategy_insight_db_path,
        model_timeout_ms=settings.codex_runtime_timeout_ms,
    ),
)
market_pulse_service = MarketPulseService(
    config=MarketPulseConfig(
        db_path=settings.market_pulse_db_path,
        enabled=settings.market_pulse_enabled,
        timeout_sec=settings.market_pulse_timeout_sec,
        index_codes=settings.market_pulse_index_codes,
        sector_signal_limit=settings.market_pulse_sector_signal_limit,
        investor_flow_enabled=settings.market_pulse_investor_flow_enabled,
        investor_flow_markets=settings.market_pulse_investor_flow_markets,
        program_trading_enabled=settings.market_pulse_program_trading_enabled,
        program_trading_markets=settings.market_pulse_program_trading_markets,
        fx_enabled=settings.market_pulse_fx_enabled,
    ),
    strategy_signal_provider=strategy_intelligence,
)


def _market_opportunity_provider(
    *,
    limit: int,
    account: dict[str, Any] | None = None,
    strategy_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = strategy_payload if isinstance(strategy_payload, dict) else {}
    strategy_candidates = [
        row for row in list(payload.get("candidates") or []) if isinstance(row, dict)
    ]
    try:
        external_payload = strategy_intelligence.list_external_signals(limit=300)
        external_signals = [
            row
            for row in list(external_payload.get("items") or [])
            if isinstance(row, dict)
        ]
    except Exception:
        external_signals = []
    insights = [*strategy_candidates, *external_signals]
    try:
        symbol_rows = naver_report_repository.list_symbol_directory(limit=3_000)
    except Exception:
        symbol_rows = []
    try:
        report_rows = naver_report_repository.search(
            query="",
            category="company_analysis",
            limit=100,
        )
    except Exception:
        report_rows = []
    account_payload = account if isinstance(account, dict) else {}
    positions = [
        row for row in list(account_payload.get("positions") or []) if isinstance(row, dict)
    ]
    fundamental_symbols = [
        str(row.get("symbol") or "")
        for row in [*positions, *strategy_candidates, *report_rows]
        if isinstance(row, dict)
    ]
    fundamentals: list[dict[str, Any]] = []
    for symbol in dict.fromkeys(fundamental_symbols):
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        try:
            latest = symbol_fundamentals_service.latest(symbol)
        except Exception:
            latest = None
        if isinstance(latest, dict):
            fundamentals.append(latest)
        if len(fundamentals) >= max(int(limit) * 2, 30):
            break
    etf_rows: list[dict[str, Any]] = []
    try:
        for row in etf_research_provider.list_universe():
            symbol = str(row.get("symbol") or "")
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            latest_score = etf_research_provider.latest_score(symbol)
            etf_rows.append({**row, **(latest_score if isinstance(latest_score, dict) else {})})
    except Exception:
        etf_rows = []
    return rank_opportunities(
        symbols=symbol_rows,
        reports=report_rows,
        insights=insights,
        fundamentals=fundamentals,
        etfs=etf_rows,
        positions=positions,
        limit=max(int(limit), 1),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


market_judgment_engine = MarketJudgmentEngine(
    config=MarketJudgmentConfig(
        db_path=settings.market_judge_db_path,
        state_path=settings.market_judge_state_path,
        quote_interval_sec=settings.market_quote_interval_sec,
        judge_interval_sec=settings.market_judge_interval_sec,
        max_symbols=settings.market_judge_max_symbols,
        llm_max_symbols=settings.market_judge_llm_max_symbols,
        use_naver_fallback=settings.market_judge_use_naver_fallback,
        query=settings.market_judge_query,
    ),
    kis=kis_primary,
    codex_runtime=helper_codex_runtime,
    strategy_engine=strategy_intelligence,
    report_repository=naver_report_repository,
    fundamentals_repository=symbol_fundamentals_service,
    rag_store=rag_store,
    research_feed_provider=lambda: _read_strategy_research_feed(),
    market_pulse_provider=market_pulse_service.latest,
    memory_context_provider=investment_memory_service.context_pack,
    wiki_context_provider=_jue_wiki_context_provider,
    opportunity_provider=_market_opportunity_provider,
    watchlist=_settings_symbols_from_csv(settings.valuation_watchlist),
)
kis_block_trader = KISBlockTrader(
    config=KISBlockTraderConfig(
        db_path=settings.kis_block_trader_db_path,
        state_path=settings.kis_block_trader_state_path,
        enabled=settings.kis_block_trader_enabled,
        execute_orders=settings.kis_block_trader_execute_orders,
        rule_interval_sec=settings.kis_block_trader_rule_interval_sec,
        manager_interval_sec=settings.kis_block_trader_manager_interval_sec,
        aggressive_limit_bps=settings.kis_block_trader_aggressive_limit_bps,
        cost_buy_fee_rate=settings.kis_validation_buy_fee_rate,
        cost_sell_fee_rate=settings.kis_validation_sell_fee_rate,
        cost_sell_tax_rate=settings.kis_validation_sell_tax_rate,
        cost_slippage_bps=settings.kis_validation_slippage_bps,
        cost_spread_bps=settings.kis_validation_spread_bps,
        pending_reconcile_timeout_sec=(
            settings.kis_block_trader_pending_reconcile_timeout_sec
        ),
        max_manager_symbols=settings.kis_block_trader_max_manager_symbols,
        use_naver_fallback=settings.market_judge_use_naver_fallback,
        manager_query=settings.kis_block_trader_manager_query,
        telegram_enabled=settings.investment_memory_send_telegram,
        horizon_targets=parse_horizon_targets(settings.block_horizon_targets),
        etf_universe=parse_etf_universe(settings.kis_block_trader_etf_universe),
    ),
    kis=kis_primary,
    codex_runtime=helper_codex_runtime,
    strategy_engine=strategy_intelligence,
    etf_research_provider=etf_research_provider,
    market_judgment_provider=market_judgment_engine,
    research_feed_provider=lambda: _read_strategy_research_feed(),
    memory_context_provider=investment_memory_service.context_pack,
    wiki_context_provider=_jue_wiki_context_provider,
    market_pulse_provider=market_pulse_service.context_for_blocks,
    live_authority_provider=lambda: build_runner_live_authority_payload(settings)[
        "venues"
    ]["kis"],
    kr_pattern_lab_provider=lambda: KREquityPatternLabRepository(
        settings.kr_equity_pattern_lab_db_path
    ).context(limit=12),
    symbol_name_resolver=naver_report_repository.resolve_symbol_names,
    telegram=telegram,
)
symbol_analysis_service = SymbolAnalysisService(
    codex_runtime=helper_codex_runtime,
    memory_service=investment_memory_service,
    fundamentals=symbol_fundamentals_service,
    quote_provider=market_judgment_engine.quote_service,
    report_repository=naver_report_repository,
    rag_store=rag_store,
    block_provider=kis_block_trader,
    timeout_ms=settings.codex_runtime_timeout_ms,
)
kis_block_trader.symbol_analysis_runner = symbol_analysis_service.run
daily_discovery_symbol_analysis_service = SymbolAnalysisService(
    codex_runtime=daily_discovery_codex_runtime,
    memory_service=investment_memory_service,
    fundamentals=symbol_fundamentals_service,
    quote_provider=market_judgment_engine.quote_service,
    report_repository=naver_report_repository,
    rag_store=rag_store,
    block_provider=kis_block_trader,
    timeout_ms=settings.codex_runtime_timeout_ms,
)


def _daily_discovery_context_limit() -> int:
    return min(
        max(
            int(settings.daily_discovery_kospi_count)
            + int(settings.daily_discovery_kosdaq_count),
            30,
        ),
        120,
    )


daily_discovery_service = DailyDiscoveryService(
    config=DailyDiscoveryConfig(
        db_path=settings.daily_discovery_db_path,
        enabled=settings.daily_discovery_enabled,
        kospi_count=settings.daily_discovery_kospi_count,
        kosdaq_count=settings.daily_discovery_kosdaq_count,
        exclude_recent_days=settings.daily_discovery_exclude_recent_days,
        candidate_limit_per_market=(
            settings.daily_discovery_candidate_limit_per_market
        ),
        force_collect=True,
    ),
    directory_source=naver_report_repository,
    symbol_analysis=daily_discovery_symbol_analysis_service,
)
kis_block_trader.daily_discovery_provider = lambda: daily_discovery_service.latest_context(
    limit=_daily_discovery_context_limit()
)


_STRATEGY_COLLECT_ALLOWED_HOSTS = {
    "whale_insight": {"whale-insight.com", "www.whale-insight.com"},
    "after_close_330": {
        "api.lefthanders-new.xyz",
        "www.sesiban.site",
        "sesiban.site",
    },
}
_STRATEGY_COLLECT_DEFAULTS: dict[str, dict[str, str]] = {
    "whale_insight": {
        "url": "https://whale-insight.com/major_stock",
        "cache_path": ".runtime/cache/whale_insight_public_signals.json",
        "symbol_cache_path": ".runtime/cache/strategy_insight_symbol_cache.json",
        "symbol_search_url": "https://api.lefthanders-new.xyz/api/v1/assets",
    },
    "after_close_330": {
        "url": "https://api.lefthanders-new.xyz/api/v1/rankings/leading?market=KR",
        "cache_path": ".runtime/cache/sesiban_public_signals.json",
    },
}
_STRATEGY_COLLECT_SOURCE_ALIASES = {
    "whale": "whale_insight",
    "whale_insight": "whale_insight",
    "sesiban": "after_close_330",
    "after_close": "after_close_330",
    "after_close_330": "after_close_330",
}


def _is_allowed_collect_url(source_id: str, value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    host = (urlparse(raw).hostname or "").lower()
    return host in _STRATEGY_COLLECT_ALLOWED_HOSTS.get(source_id, set())


def _safe_runtime_cache_path(value: Any, default_value: str) -> str:
    raw = str(value or default_value).strip() or default_value
    candidate = Path(raw).expanduser()
    base = (Path.cwd() / ".runtime" / "cache").resolve()
    resolved = candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return default_value
    return raw


def _safe_strategy_collect_sources(source_ids: list[str] | None = None) -> list[dict[str, Any]]:
    wanted = {
        _STRATEGY_COLLECT_SOURCE_ALIASES.get(str(item or "").strip().lower(), "")
        for item in (source_ids or [])
        if str(item or "").strip()
    }
    wanted.discard("")
    safe_sources: list[dict[str, Any]] = []
    for source in settings.strategy_insight_source_list:
        source_id = _STRATEGY_COLLECT_SOURCE_ALIASES.get(
            str(source.get("source_id") or "").strip().lower(),
            "",
        )
        if source_id not in _STRATEGY_COLLECT_DEFAULTS:
            continue
        if wanted and source_id not in wanted:
            continue
        defaults = _STRATEGY_COLLECT_DEFAULTS[source_id]
        row = dict(source)
        row["source_id"] = source_id
        row["url"] = (
            str(source.get("url")).strip()
            if _is_allowed_collect_url(source_id, source.get("url"))
            else defaults["url"]
        )
        row["cache_path"] = _safe_runtime_cache_path(
            source.get("cache_path"),
            defaults["cache_path"],
        )
        if source_id == "whale_insight":
            row["symbol_cache_path"] = _safe_runtime_cache_path(
                source.get("symbol_cache_path"),
                defaults["symbol_cache_path"],
            )
            row["symbol_search_url"] = (
                str(source.get("symbol_search_url")).strip()
                if _is_allowed_collect_url("after_close_330", source.get("symbol_search_url"))
                else defaults["symbol_search_url"]
            )
        safe_sources.append(row)
    return safe_sources


def _strategy_collect_source_ids(payload: dict[str, Any] | None) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    forbidden_keys = {"sources", "url", "path", "cache_path", "symbol_cache_path"}
    if any(key in payload for key in forbidden_keys):
        raise HTTPException(
            status_code=400,
            detail="strategy insight collect accepts source_ids only",
        )
    raw = payload.get("source_ids") or payload.get("source_id")
    if raw is None:
        return None
    if isinstance(raw, str):
        return [item.strip() for item in re.split(r"[\s,;]+", raw) if item.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    raise HTTPException(status_code=400, detail="source_ids must be a string or list")


def _build_strategy_insight_collector(
    sources: list[dict[str, Any]] | None = None,
) -> StrategyInsightCollector:
    return StrategyInsightCollector(
        engine=strategy_intelligence,
        sources=_safe_strategy_collect_sources() if sources is None else sources,
        timeout_sec=settings.strategy_insight_request_timeout_sec,
    )


fx_rates = FxRateService(
    FxRateConfig(
        upbit_base_url=settings.upbit_base_url,
        bithumb_base_url=settings.bithumb_base_url,
        fallback_usdt_krw=settings.binance_usdt_krw,
        fallback_usd_krw=settings.usd_krw,
        cache_ttl_sec=settings.fx_cache_ttl_sec,
    )
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
RESEARCH_QUERY_LOG_PATH = Path(".runtime/research_query.log.jsonl")
telegram_poller_task: asyncio.Task[None] | None = None
telegram_update_offset: int | None = None
bithumb_cached_assets: list[dict[str, Any]] | None = None
kis_primary_cached_assets: list[dict[str, Any]] | None = None
kis_primary_us_cached_assets: list[dict[str, Any]] | None = None
kis_secondary_cached_assets: list[dict[str, Any]] | None = None
dashboard_payload_cache = DashboardPayloadCache()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_poller_task
    if telegram_poller_task is None and telegram.config.ready:
        await _prime_telegram_offset()
        telegram_poller_task = asyncio.create_task(_telegram_poll_worker())
    try:
        yield
    finally:
        if telegram_poller_task is not None:
            telegram_poller_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_poller_task
            telegram_poller_task = None


app = FastAPI(title="TradeCraft UI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _dashboard_reports_status() -> dict[str, Any]:
    repository_status_fn = getattr(naver_report_repository, "ops_status", None)
    if not callable(repository_status_fn):
        repository_status_fn = naver_report_repository.status
    rag_status = (
        rag_store.status()
        if settings.rag_enabled and rag_store is not None
        else {
            "available": False,
            "reason": "rag_disabled",
            "persist_path": settings.rag_persist_path,
            "collection_name": settings.rag_collection_name,
        }
    )
    return build_reports_status_payload(
        naver_reports_enabled=settings.naver_reports_enabled,
        repository_status=repository_status_fn(),
        intelligence_status=build_report_intelligence_status(settings),
        rag_status=rag_status,
        fundamentals_status=symbol_fundamentals_service.status(),
    )


async def _build_dashboard_payload(
    include_telegram: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    global \
        bithumb_cached_assets, \
        kis_primary_cached_assets, \
        kis_primary_us_cached_assets, \
        kis_secondary_cached_assets
    payload = await build_dashboard_payload_from_deps(
        DashboardPayloadDeps(
            settings=settings,
            fx_rates=fx_rates,
            upbit=upbit,
            bithumb=bithumb,
            binance=binance,
            kis_primary=kis_primary,
            kis_secondary=kis_secondary,
            runtime_reader=runtime_reader,
            research_reader=research_reader,
            telegram=telegram,
            dashboard_template=empty_dashboard_template,
            replace_venue_assets=replace_venue_assets,
            upsert_venue_assets=upsert_venue_assets,
            logger=logger,
            research_status_provider=_dashboard_reports_status,
        ),
        dashboard_payload_cache,
        include_telegram=include_telegram,
        force_refresh=force_refresh,
    )
    bithumb_cached_assets = dashboard_payload_cache.bithumb_assets
    kis_primary_cached_assets = dashboard_payload_cache.kis_primary_assets
    kis_primary_us_cached_assets = dashboard_payload_cache.kis_primary_us_assets
    kis_secondary_cached_assets = dashboard_payload_cache.kis_secondary_assets
    return payload


def _compact_llm_usage_for_memory(payload: dict[str, Any]) -> dict[str, Any]:
    total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
    rows = payload.get("by_component") if isinstance(payload.get("by_component"), list) else []
    return {
        "trading_day": payload.get("trading_day"),
        "total": {
            "call_count": total.get("call_count"),
            "total_tokens": total.get("total_tokens"),
            "prompt_tokens": total.get("prompt_tokens"),
            "completion_tokens": total.get("completion_tokens"),
            "estimated_token_count": total.get("estimated_token_count"),
            "error_count": total.get("error_count"),
        },
        "by_component": [
            {
                "component": row.get("component"),
                "call_count": row.get("call_count"),
                "total_tokens": row.get("total_tokens"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "error_count": row.get("error_count"),
            }
            for row in rows[:8]
            if isinstance(row, dict)
        ],
    }


async def _build_investment_memory_context() -> dict[str, Any]:
    context: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        block_snapshot = await kis_block_trader.snapshot()
        context["blocks"] = block_snapshot
        context["account"] = block_snapshot.get("account") or {}
        context["clock"] = (block_snapshot.get("summary") or {}).get("clock") or {}
    except Exception as exc:
        context["blocks"] = {"status": "error", "error_message": str(exc)}
        context["account"] = {}
    try:
        context["binance_blocks"] = await binance_block_trader.snapshot()
    except Exception as exc:
        context["binance_blocks"] = {"status": "error", "error_message": str(exc)}
    try:
        context["market_judgment"] = market_judgment_engine.latest_judgment()
        context["market_clock"] = market_judgment_engine.clock()
    except Exception as exc:
        context["market_judgment"] = {"status": "error", "error_message": str(exc)}
    try:
        research_feed = _read_strategy_research_feed()
        context["research"] = research_feed if isinstance(research_feed, dict) else {}
    except Exception as exc:
        context["research"] = {"status": "error", "error_message": str(exc)}
    try:
        context["reports_status"] = naver_report_repository.status()
    except Exception as exc:
        context["reports_status"] = {"status": "error", "error_message": str(exc)}
    try:
        strategy_payload = strategy_intelligence.build_candidates(
            query=settings.kis_block_trader_manager_query,
            research_feed=_read_strategy_research_feed(),
            limit=8,
        )
        context["strategy"] = strategy_payload
        context["strategy_source_status"] = list(strategy_payload.get("sources") or [])
    except Exception as exc:
        context["strategy"] = {"status": "error", "error_message": str(exc)}
        context["strategy_source_status"] = []
    try:
        context["valuation_status"] = symbol_fundamentals_service.status()
    except Exception as exc:
        context["valuation_status"] = {"status": "error", "error_message": str(exc)}
    try:
        context["llm_usage"] = _compact_llm_usage_for_memory(_llm_usage_summary())
    except Exception as exc:
        context["llm_usage"] = {"status": "error", "error_message": str(exc)}
    try:
        context["daily_discovery"] = daily_discovery_service.latest_context(
            limit=_daily_discovery_context_limit()
        )
    except Exception as exc:
        context["daily_discovery"] = {"status": "error", "error_message": str(exc)}
    return context


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _build_backtest_config(payload: dict[str, Any]) -> BacktestConfig:
    config = BacktestConfig(
        cycles=max(_as_int(payload.get("cycles"), settings.backtest_cycles), 1),
        step_sec=max(_as_int(payload.get("step_sec"), settings.backtest_step_sec), 1),
        speed=max(_as_float(payload.get("speed"), settings.backtest_speed), 0.01),
        initial_price=max(
            _as_float(payload.get("initial_price"), settings.backtest_initial_price),
            1.0,
        ),
        volatility_bps=max(
            _as_float(payload.get("volatility_bps"), settings.backtest_volatility_bps),
            0.0,
        ),
        drift_bps=_as_float(payload.get("drift_bps"), settings.backtest_drift_bps),
        fee_rate=max(
            _as_float(payload.get("fee_rate"), settings.backtest_fee_rate), 0.0
        ),
        slippage_bps=max(
            _as_float(payload.get("slippage_bps"), settings.backtest_slippage_bps),
            0.0,
        ),
        seed=_as_int(payload.get("seed"), settings.backtest_seed),
    )
    scenario = str(payload.get("scenario") or "baseline").strip().lower()
    return apply_scenario(config, scenario)


def _symbol_analysis_telegram_text(payload: dict[str, Any]) -> str:
    if str(payload.get("status") or "") == "invalid_symbol":
        return "사용법: /analyze 005930"
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    if not analysis:
        return "종목 분석 결과를 만들지 못했습니다."
    name = str(analysis.get("name") or payload.get("name") or "-").strip()
    symbol = str(analysis.get("symbol") or payload.get("symbol") or "-").strip()
    stance = str(analysis.get("stance") or "-").strip()
    confidence = analysis.get("confidence")
    summary = str(analysis.get("summary") or "-").strip()
    try:
        confidence_text = f"{float(confidence):.2f}"
    except (TypeError, ValueError):
        confidence_text = str(confidence or "-")
    return "\n".join(
        [
            f"{name}({symbol})",
            f"stance: {stance}",
            f"confidence: {confidence_text}",
            f"summary: {summary}",
        ]
    )


async def _process_telegram_text(text: str, chat_id: str) -> dict[str, Any]:
    if not _is_allowed_telegram_chat(chat_id):
        return {
            "handled": False,
            "sent": False,
            "reason": "chat_not_allowed",
        }
    if text:
        telegram.last_webhook_message = text
    command, args = telegram_cli.parse(text)
    if not command:
        return {"handled": False, "sent": False}

    if command in {"start", "help"}:
        handled, reply = telegram_cli.execute(text)
    elif command == "watchlist":
        query = telegram_cli.strategy_query_text("strategy", args)
        payload = await strategy_intelligence.build_brief(
            query=query,
            research_feed=_read_strategy_research_feed(),
            use_llm=False,
            limit=8,
        )
        handled, reply = True, telegram_cli.strategy_watchlist_text(payload)
    elif command == "why":
        symbol = str(args[0] if args else "").strip()
        query = f"{symbol} 왜 후보인지 근거와 반론을 설명해줘" if symbol else telegram_cli.strategy_query_text("strategy", [])
        payload = await strategy_intelligence.build_brief(
            query=query,
            research_feed=_read_strategy_research_feed(),
            use_llm=True,
            limit=8,
        )
        handled, reply = True, telegram_cli.strategy_why_text(payload, symbol)
    elif command == "analyze":
        symbol_or_name = str(args[0] if args else "").strip()
        if not symbol_or_name:
            handled, reply = True, "사용법: /analyze 005930"
        else:
            payload = await symbol_analysis_service.run(
                symbol_or_name,
                trigger="telegram",
                force_collect=True,
            )
            handled, reply = True, _symbol_analysis_telegram_text(payload)
    elif command == "market":
        payload = market_judgment_engine.latest_judgment()
        handled, reply = True, telegram_cli.market_judgment_text(payload)
    elif command == "judge":
        if not settings.kis_primary_ready:
            payload = {
                "status": "kis_primary_not_configured",
                "run": {
                    "mode": "unavailable",
                    "status": "kis_primary_not_configured",
                },
                "judgments": [],
            }
        else:
            payload = await market_judgment_engine.run_once(use_llm=True)
        handled, reply = True, telegram_cli.market_judgment_text(payload)
    elif command == "why-now":
        symbol = str(args[0] if args else "").strip()
        payload = market_judgment_engine.latest_judgment()
        handled, reply = True, telegram_cli.market_why_now_text(payload, symbol)
    elif command == "memory":
        payload = investment_memory_service.status()
        handled, reply = True, telegram_cli.memory_status_text(payload)
    elif command in {"llm-usage", "llm_usage"}:
        payload = await _llm_usage_summary_async()
        handled, reply = True, telegram_cli.llm_usage_text(payload)
    elif command in {"live", "authority"}:
        payload = await asyncio.to_thread(_build_live_authority_payload)
        handled, reply = True, telegram_cli.live_authority_text(payload)
    elif command == "mindset":
        today_payload = investment_memory_service.today()
        journal = next(
            (
                row
                for row in list(today_payload.get("journals") or [])
                if str(row.get("slot") or "") == "pre_open"
            ),
            None,
        )
        if journal is None:
            payload = await investment_memory_service.run_ritual(
                slot="pre_open",
                context=await _build_investment_memory_context(),
                send_telegram=False,
            )
            journal = payload.get("journal") if isinstance(payload, dict) else None
        handled, reply = True, telegram_cli.memory_journal_text(journal or {})
    elif command == "reflect":
        payload = investment_memory_service.run_due_reflections(
            context=await _build_investment_memory_context(),
            force=True,
        )
        handled, reply = True, telegram_cli.memory_journal_text(payload.get("journal") or {})
    elif command in {"weekly-review", "weekly_review"}:
        payload = await investment_memory_service.run_period_review(
            period_type="weekly",
            context=await _build_investment_memory_context(),
            force=True,
        )
        handled, reply = True, telegram_cli.memory_period_review_text(payload)
    elif command in {"monthly-review", "monthly_review"}:
        payload = await investment_memory_service.run_period_review(
            period_type="monthly",
            context=await _build_investment_memory_context(),
            force=True,
        )
        handled, reply = True, telegram_cli.memory_period_review_text(payload)
    elif command == "policy":
        payload = investment_memory_service.policy_revisions(limit=8)
        handled, reply = True, telegram_cli.memory_policy_revisions_text(payload)
    elif command == "journal":
        payload = investment_memory_service.today()
        handled, reply = True, telegram_cli.memory_today_text(payload)
    elif command == "why-block":
        block_id = str(args[0] if args else "").strip()
        payload = investment_memory_service.block_memory(block_id)
        handled, reply = True, telegram_cli.memory_block_text(payload)
    elif command in {"ask", "strategy", "bot"}:
        query = telegram_cli.strategy_query_text(command, args)
        payload = await strategy_intelligence.build_brief(
            query=query,
            research_feed=_read_strategy_research_feed(),
            use_llm=True,
            limit=5,
        )
        handled, reply = True, telegram_cli.strategy_brief_text(payload)
    else:
        dashboard_data = await _build_dashboard_payload(include_telegram=False)
        handled, reply = telegram_cli.execute_with_dashboard(text, dashboard_data)

    if not handled:
        return {"handled": False, "sent": False}

    parse_mode = "HTML" if reply.startswith("<pre>") else None
    target_chat_id = chat_id.strip() if chat_id else ""
    sent = await telegram.send_message(
        reply, parse_mode=parse_mode, chat_id=target_chat_id or None
    )
    return {
        "handled": True,
        "sent": bool(sent.get("ok")),
    }


async def _prime_telegram_offset() -> None:
    global telegram_update_offset
    updates = await telegram.get_updates(offset=None, timeout_sec=0)
    if not updates.get("ok"):
        return
    rows = list(updates.get("result") or [])
    if not rows:
        return

    latest = 0
    for item in rows:
        update_id = int(item.get("update_id") or 0)
        if update_id > latest:
            latest = update_id
    if latest:
        telegram_update_offset = latest + 1


async def _telegram_poll_worker() -> None:
    global telegram_update_offset
    while True:
        updates = await telegram.get_updates(
            offset=telegram_update_offset, timeout_sec=15
        )
        if not updates.get("ok"):
            await asyncio.sleep(2.0)
            continue

        rows = list(updates.get("result") or [])
        if not rows:
            await asyncio.sleep(0.2)
            continue

        for item in rows:
            update_id = int(item.get("update_id") or 0)
            if update_id:
                telegram_update_offset = update_id + 1

            message = item.get("message") or {}
            text = str(message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id") or "").strip()
            if not text:
                continue

            result = await _process_telegram_text(text, chat_id)
            if result.get("handled"):
                command = text.split(maxsplit=1)[0]
                logger.info("telegram command handled by polling: %s", command)


def _runner_status_with_cover(
    status: dict[str, Any],
    *,
    covered_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_runner_status_with_cover(status, covered_by=covered_by)


def _iso_to_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_code_mtime(paths: list[Path]) -> dict[str, Any]:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return {"code_mtime": "", "code_mtime_epoch": None}
    latest = max(path.stat().st_mtime for path in existing)
    return {
        "code_mtime": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(),
        "code_mtime_epoch": latest,
    }


def _process_with_code_staleness(
    process: dict[str, Any],
    *,
    code_paths: list[Path],
) -> dict[str, Any]:
    payload = dict(process)
    code = _max_code_mtime(code_paths)
    started_epoch = payload.get("started_at_epoch")
    code_epoch = code.get("code_mtime_epoch")
    stale = False
    if bool(payload.get("direct_alive") or payload.get("alive")):
        try:
            stale = bool(
                started_epoch is not None
                and code_epoch is not None
                and float(code_epoch) > float(started_epoch) + 1.0
            )
        except (TypeError, ValueError):
            stale = False
    payload.update(code)
    payload["stale_process"] = stale
    return payload


def _next_from_latest(latest_iso: Any, interval_sec: int) -> str:
    latest = _iso_to_utc(latest_iso)
    if latest is None:
        return ""
    return (latest + timedelta(seconds=max(int(interval_sec), 1))).isoformat()


def _next_from_latest_or_krx_clock(
    latest_iso: Any,
    interval_sec: int,
    *,
    clock: dict[str, Any] | None,
    include_post_close: bool,
) -> str:
    candidate = _next_from_latest(latest_iso, interval_sec)
    parsed_candidate = _iso_to_utc(candidate)
    if parsed_candidate is not None and parsed_candidate > datetime.now(timezone.utc):
        return candidate
    if isinstance(clock, dict):
        return next_krx_decision_due_at(
            clock,
            include_post_close=include_post_close,
        )
    return candidate


DUPLICATE_SCAN_RUNNER_KEYS = {
    "control",
    "kis_block_trader",
    "binance_block_trader",
    "market_judge",
    "market_pulse",
    "investment_memory",
    "live_evaluator",
}


def _runner_process_status_light(key: str) -> dict[str, Any]:
    return build_light_runner_process_status(
        key,
        runner_process_status,
        scan_alive_matches=key in DUPLICATE_SCAN_RUNNER_KEYS,
    )


def _build_core_runner_processes() -> dict[str, dict[str, Any]]:
    base = Path(__file__).resolve().parent
    processes = build_core_runner_processes_payload(
        base=base,
        runner_status=lambda key: _runner_status_with_cover(
            _runner_process_status_light(key)
        ),
        apply_code_staleness=_process_with_code_staleness,
    )
    processes["jue_wiki"] = _process_with_code_staleness(
        _runner_status_with_cover(_runner_process_status_light("jue_wiki")),
        code_paths=[
            base / "runtime" / "jue_wiki_runner.py",
            base / "services" / "jue_wiki.py",
        ],
    )
    # The legacy intelligence runner is superseded by naver_reports,
    # strategy_insights, investment_memory, and jue_wiki. Keeping it in the
    # live readiness process map makes the UI show a stopped core service even
    # when the active intelligence pipeline is healthy.
    processes.pop("intelligence", None)
    if not bool(settings.research_enabled):
        processes.pop("research", None)
    return processes


def _light_process_with_staleness(
    key: str,
    *,
    code_paths: list[Path],
) -> dict[str, Any]:
    return _process_with_code_staleness(
        _runner_status_with_cover(_runner_process_status_light(key)),
        code_paths=code_paths,
    )


def _build_kis_blocks_status_readiness() -> dict[str, Any]:
    base = Path(__file__).resolve().parent
    processes = {
        "kis_block_trader": _light_process_with_staleness(
            "kis_block_trader",
            code_paths=[
                base / "runtime" / "kis_block_trader_runner.py",
                base / "services" / "kis_block_trader.py",
            ],
        ),
        "market_judge": _light_process_with_staleness(
            "market_judge",
            code_paths=[
                base / "runtime" / "market_judge_runner.py",
                base / "services" / "market_judgment.py",
            ],
        ),
    }
    stale_processes = [
        key for key, process in processes.items() if bool(process.get("stale_process"))
    ]
    missing_processes = [
        key
        for key, process in processes.items()
        if not bool(process.get("effective_alive") or process.get("alive"))
    ]
    warnings: list[str] = []
    if stale_processes:
        warnings.append("restart_required")
    status = "yellow" if warnings or missing_processes else "green"
    try:
        block_status = kis_block_trader.status()
    except Exception as exc:
        block_status = {"status": "error", "error_message": str(exc)}
        status = "yellow"
        warnings.append("kis_status_unavailable")
    try:
        market_schedule = market_judgment_engine.schedule()
    except Exception as exc:
        market_schedule = {"status": "error", "error_message": str(exc)}
        status = "yellow"
        warnings.append("market_judge_schedule_unavailable")
    manager_interval = int(
        (block_status.get("config") or {}).get(
            "manager_interval_sec",
            settings.kis_block_trader_manager_interval_sec,
        )
    )
    return {
        "status": status,
        "warnings": warnings,
        "blockers": [],
        "stale_processes": stale_processes,
        "missing_processes": missing_processes,
        "kis_block_trader": {
            "next_manager_run_at": _next_from_latest_or_krx_clock(
                block_status.get("latest_manager_run_at"),
                manager_interval,
                clock=block_status.get("clock")
                if isinstance(block_status.get("clock"), dict)
                else None,
                include_post_close=False,
            ),
            "runner": processes.get("kis_block_trader", {}),
        },
        "market_judge": {
            "schedule": market_schedule,
            "runner": processes.get("market_judge", {}),
        },
    }


def _sqlite_one(path: str, sql: str) -> dict[str, Any]:
    db_path = Path(path)
    if not db_path.exists():
        return {"status": "missing", "db_path": str(db_path)}
    try:
        with sqlite3.connect(db_path, timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql).fetchone()
    except sqlite3.Error as exc:
        return {"status": "error", "db_path": str(db_path), "error_message": str(exc)}
    if row is None:
        return {"status": "missing", "db_path": str(db_path)}
    return {"status": "ok", "db_path": str(db_path), **dict(row)}


def _latest_terminal_block_status(db_path: str) -> dict[str, Any]:
    return _sqlite_one(
        db_path,
        """
        SELECT
          COUNT(*) AS terminal_count,
          MAX(COALESCE(NULLIF(closed_at, ''), NULLIF(updated_at, ''), NULLIF(created_at, '')))
            AS latest_terminal_at
        FROM blocks
        WHERE status IN ('closed', 'error')
        """,
    )


def _file_freshness(path: str, *, stale_after_sec: int) -> dict[str, Any]:
    value = Path(path)
    if not value.exists():
        return {
            "status": "missing",
            "path": str(value),
            "stale": True,
            "age_sec": None,
        }
    updated_at = datetime.fromtimestamp(value.stat().st_mtime, tz=timezone.utc)
    age_sec = max((datetime.now(timezone.utc) - updated_at).total_seconds(), 0.0)
    return {
        "status": "ok",
        "path": str(value),
        "updated_at": updated_at.isoformat(),
        "age_sec": int(age_sec),
        "stale": age_sec >= max(int(stale_after_sec), 1),
    }


def _runtime_state_freshness(
    path: str,
    *,
    stale_after_sec: int,
    running_timeout_sec: int,
) -> dict[str, Any]:
    payload = _file_freshness(path, stale_after_sec=stale_after_sec)
    value = Path(path)
    if not value.exists():
        return payload
    try:
        state = json.loads(value.read_text(encoding="utf-8"))
    except Exception as exc:
        payload["state_error"] = str(exc)[:200]
        return payload
    if not isinstance(state, dict):
        return payload
    runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
    if runtime:
        payload["runtime_execution_mode"] = str(
            runtime.get("execution_mode") or ""
        ).strip()
        payload["runtime_executes_orders"] = bool(runtime.get("executes_orders"))
        payload["runtime_session_source"] = str(
            runtime.get("session_source") or ""
        ).strip()
    state_status = str(state.get("status") or "").strip()
    state_updated_at = str(state.get("updated_at") or "")
    state_age = _iso_age_sec(state_updated_at)
    payload["state_status"] = state_status
    payload["state_updated_at"] = state_updated_at
    payload["state_age_sec"] = int(state_age) if state_age is not None else None
    if state_status.endswith("_running") or state_status == "running":
        timeout = max(
            int(state.get("reports_cycle_timeout_sec") or running_timeout_sec),
            1,
        )
        if state_age is None or state_age >= timeout + 300:
            payload["status"] = "stale_running"
            payload["stale"] = True
            payload["running_stale"] = True
            payload["running_timeout_sec"] = timeout
    return payload


def _runtime_session_source_warnings(runtime_state: dict[str, Any]) -> list[str]:
    source = str(runtime_state.get("runtime_session_source") or "").strip()
    if source.startswith("safe_default_no_orders (missing:"):
        return ["runtime_sessions_missing"]
    if source.startswith("safe_default_no_orders (invalid file:"):
        return ["runtime_sessions_invalid"]
    return []


def _reflection_lagging(
    *,
    latest_terminal_at: Any,
    latest_reflection_at: Any,
    grace_sec: int,
) -> bool:
    terminal_at = _iso_to_utc(latest_terminal_at)
    if terminal_at is None:
        return False
    now = datetime.now(timezone.utc)
    if (now - terminal_at).total_seconds() < max(int(grace_sec), 1):
        return False
    reflected_at = _iso_to_utc(latest_reflection_at)
    if reflected_at is None:
        return True
    return terminal_at > reflected_at + timedelta(seconds=max(int(grace_sec), 1))


def _build_jue_semantic_checks(
    *,
    memory_status: dict[str, Any],
    reports_status: dict[str, Any],
    llm_usage_payload: dict[str, Any],
    processes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    reflection_grace_sec = 900
    latest_reflection_at = str(memory_status.get("latest_reflection_at") or "")
    terminal_checks = {
        "kis": _latest_terminal_block_status(settings.kis_block_trader_db_path),
        "binance": _latest_terminal_block_status(settings.binance_block_trader_db_path),
    }
    lagging_venues: list[str] = []
    for venue, row in terminal_checks.items():
        if _reflection_lagging(
            latest_terminal_at=row.get("latest_terminal_at"),
            latest_reflection_at=latest_reflection_at,
            grace_sec=reflection_grace_sec,
        ):
            lagging_venues.append(venue)
    if lagging_venues:
        warnings.append("block_reflections_lagging")
    checks["block_reflections"] = {
        "latest_reflection_at": latest_reflection_at,
        "grace_sec": reflection_grace_sec,
        "lagging_venues": lagging_venues,
        "terminal_blocks": terminal_checks,
    }

    report_threshold_sec = max(int(settings.naver_reports_interval_sec) * 3, 24 * 3600)
    report_last_updated_at = str(reports_status.get("last_updated_at") or "")
    report_age = _iso_age_sec(report_last_updated_at)
    research_state = _runtime_state_freshness(
        settings.research_state_path,
        stale_after_sec=report_threshold_sec,
        running_timeout_sec=max(int(settings.naver_reports_cycle_timeout_sec), 1),
    )
    research_running_fresh = bool(
        str(research_state.get("state_status") or "").endswith("_running")
        and not bool(research_state.get("running_stale"))
        and not bool(research_state.get("stale"))
    )
    reports_stale = bool(
        settings.naver_reports_enabled
        and (report_age is None or report_age >= report_threshold_sec)
    )
    naver_reports_process = (
        (processes or {}).get("naver_reports")
        if isinstance(processes, dict)
        else {}
    )
    dedicated_reports_runner_alive = bool(
        isinstance(naver_reports_process, dict)
        and (
            naver_reports_process.get("effective_alive")
            or naver_reports_process.get("alive")
        )
    )
    legacy_research_state_authoritative = bool(
        settings.research_enabled or not dedicated_reports_runner_alive
    )
    if reports_stale and not research_running_fresh:
        warnings.append("reports_db_stale")
    if (
        settings.naver_reports_enabled
        and legacy_research_state_authoritative
        and bool(research_state.get("stale"))
    ):
        warnings.append("research_runner_state_stale")
    checks["reports"] = {
        "enabled": bool(settings.naver_reports_enabled),
        "last_updated_at": report_last_updated_at,
        "age_sec": int(report_age) if report_age is not None else None,
        "stale_after_sec": report_threshold_sec,
        "stale": reports_stale,
        "dedicated_reports_runner_alive": dedicated_reports_runner_alive,
        "legacy_research_state_authoritative": legacy_research_state_authoritative,
        "runner_state": research_state,
    }

    runtime_state = _runtime_state_freshness(
        settings.runtime_state_path,
        stale_after_sec=max(int(settings.runtime_write_interval_sec) * 12, 90),
        running_timeout_sec=max(int(settings.runtime_write_interval_sec) * 12, 90),
    )
    warnings.extend(_runtime_session_source_warnings(runtime_state))
    checks["runtime_state_writer"] = {
        "state_path": settings.runtime_state_path,
        "sessions_path": settings.runtime_sessions_path,
        "runner_state": runtime_state,
    }

    llm_semantic = build_llm_usage_semantic_check_payload(
        llm_usage_payload,
        processes=processes,
        component_enabled={
            "research_pipeline": bool(settings.research_enabled),
            "portfolio_coach": bool(
                settings.research_enabled and settings.portfolio_coach_enabled
            ),
            "kis_block_manager": bool(settings.kis_block_trader_enabled),
            "binance_block_manager": bool(settings.binance_block_trader_enabled),
            "market_judge": bool(settings.market_judge_enabled),
            "investment_memory": bool(settings.investment_memory_enabled),
            "crypto_market_research": bool(settings.crypto_market_research_enabled),
            "crypto_alpha": bool(settings.crypto_alpha_enabled),
            "research_reports": bool(settings.naver_reports_enabled),
            "daily_discovery": bool(settings.daily_discovery_enabled),
        },
    )
    warnings.extend(list(llm_semantic.get("warnings") or []))
    checks["llm_usage"] = llm_semantic.get("check") or {}
    return {"status": "ok", "warnings": warnings, "checks": checks}


def _llm_usage_today() -> str:
    return datetime.now(KST).date().isoformat()


def _llm_usage_summary(
    day: str | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    repo = LLMUsageRepository(settings.llm_usage_db_path)
    period_clean = str(period or "").strip().lower()
    if period_clean in {"all", "total", "history"}:
        return repo.period_summary(period="all")
    day_window_match = re.fullmatch(r"(\d{1,3})d", period_clean)
    if period_clean in {"7d", "week", "recent_7d"} or day_window_match:
        day_count = 7
        if day_window_match:
            day_count = max(min(int(day_window_match.group(1)), 365), 1)
            if day_count <= 1:
                trading_day = str(day or "").strip() or _llm_usage_today()
                return repo.daily_summary(trading_day)
        raw_day = str(day or "").strip()
        end_day = raw_day or _llm_usage_today()
        try:
            end_date = datetime.fromisoformat(end_day).date()
        except ValueError:
            end_day = _llm_usage_today()
            end_date = datetime.fromisoformat(end_day).date()
        start_day = (end_date - timedelta(days=day_count - 1)).isoformat()
        return repo.period_summary(
            period=f"{day_count}d",
            start_day=start_day,
            end_day=end_day,
        )
    trading_day = str(day or "").strip() or _llm_usage_today()
    return repo.daily_summary(trading_day)


def _enrich_llm_usage_component_recovery(summary: dict[str, Any]) -> dict[str, Any]:
    return enrich_llm_usage_component_recovery_payload(
        summary,
        settings.llm_usage_db_path,
    )


async def _llm_usage_summary_async(
    day: str | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(_llm_usage_summary, day, period)


def _build_llm_usage_status_payload() -> dict[str, Any]:
    summary = _enrich_llm_usage_component_recovery(_llm_usage_summary())
    return build_llm_usage_status_payload_from_summary(
        enabled=bool(settings.llm_usage_enabled),
        db_path=settings.llm_usage_db_path,
        summary=summary,
    )


async def _build_llm_usage_status_payload_async() -> dict[str, Any]:
    return await asyncio.to_thread(_build_llm_usage_status_payload)


def _build_live_authority_payload() -> dict[str, Any]:
    return build_runner_live_authority_payload(settings)


def _trading_validation_initial_equity(venue: str) -> float:
    default_initial_equity = 10_000.0
    clean_venue = venue.strip().lower()
    if clean_venue == "kis":
        initial_equity = getattr(settings, "kis_validation_initial_equity_krw", 4_000_000.0)
    elif clean_venue == "binance":
        initial_equity = getattr(settings, "binance_validation_initial_equity_usdt", 1_000.0)
    else:
        initial_equity = default_initial_equity
    try:
        return max(float(initial_equity), 1.0)
    except (TypeError, ValueError):
        return default_initial_equity


def _trading_validation_service(venue: str = "") -> TradingValidationService:
    return TradingValidationService(
        TradingValidationConfig(
            validation_db_path=settings.trading_validation_db_path,
            live_performance_db_path=settings.live_performance_db_path,
            crypto_pattern_lab_db_path=settings.crypto_pattern_lab_db_path,
            kr_equity_pattern_lab_db_path=settings.kr_equity_pattern_lab_db_path,
            strategy_revision_id=settings.jue_strategy_revision_id,
            initial_equity=_trading_validation_initial_equity(venue),
        )
    )


def _build_trading_validation_status_payload(venue: str = "") -> dict[str, Any]:
    clean_venue = venue.strip().lower()
    service = _trading_validation_service(clean_venue)
    requested_revision_id = str(service.config.strategy_revision_id or "").strip()
    latest = service.latest(venue=clean_venue)
    if (
        str(latest.get("status") or "").strip().lower() == "empty"
        and requested_revision_id
    ):
        fallback = service.repository.latest(venue=clean_venue)
        if str(fallback.get("status") or "").strip().lower() != "empty":
            latest = fallback
            latest["revision_mismatch"] = True
            latest["requested_strategy_revision_id"] = requested_revision_id
            latest["fallback_strategy_revision_id"] = (
                str(fallback.get("strategy_revision_id") or "").strip() or "legacy"
            )
    latest["config"] = {
        "db_path": settings.trading_validation_db_path,
        "live_performance_db_path": settings.live_performance_db_path,
        "max_age_sec": int(settings.trading_validation_max_age_sec),
        "strategy_revision_id": requested_revision_id,
    }
    return build_annotate_trading_validation_freshness(
        build_promote_trading_validation_payload_fields(
            latest,
            expected_discipline_count=EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
        ),
        max_age_sec=int(settings.trading_validation_max_age_sec),
    )


def _aggregate_trading_validation_venue_payloads(
    venues: dict[str, Any],
) -> dict[str, Any]:
    return build_aggregate_trading_validation_venue_payloads(
        venues,
        db_path=settings.trading_validation_db_path,
        live_performance_db_path=settings.live_performance_db_path,
        max_age_sec=int(settings.trading_validation_max_age_sec),
        expected_discipline_count=EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
        empty_payload=_build_trading_validation_status_payload,
    )


def _build_trading_validation_ops_status() -> dict[str, Any]:
    venues = {
        venue: _build_trading_validation_status_payload(venue)
        for venue in ("kis", "binance")
    }
    payload = _aggregate_trading_validation_venue_payloads(venues)
    payload["venues"] = venues
    payload["lane_authority_summary"] = build_aggregate_trading_validation_lane_authority(
        venues
    )
    payload["bottlenecks"] = build_summarize_trading_validation_bottlenecks(venues)
    payload["primary_next_actions"] = build_summarize_trading_validation_next_actions(
        venues
    )
    return payload


def _naver_reports_ops_status() -> dict[str, Any]:
    ops_status = getattr(naver_report_repository, "ops_status", None)
    if callable(ops_status):
        return ops_status()
    return naver_report_repository.status()


def _build_trading_validation_endpoint_payload(venue: str = "") -> dict[str, Any]:
    clean_venue = str(venue or "").strip().lower()
    if clean_venue:
        return _build_trading_validation_status_payload(clean_venue)
    return _build_trading_validation_ops_status()


def _codex_runtime_descriptor(
    component: str,
    runtime: CodexNativeRuntime,
    *,
    workflow: str = "",
    usage_component: str | None = None,
) -> dict[str, Any]:
    return {
        "component": component,
        "workflow": workflow,
        "mode": runtime.mode,
        "model": runtime.resolved_model,
        "reasoning_effort": runtime.resolved_reasoning_effort,
        "usage_component": usage_component or runtime.config.usage_component,
    }


def _codex_native_components() -> list[dict[str, Any]]:
    return [
        _codex_runtime_descriptor("research_ask", helper_codex_runtime),
        _codex_runtime_descriptor(
            "strategy_intelligence",
            helper_codex_runtime,
            usage_component="strategy_intelligence",
        ),
        _codex_runtime_descriptor(
            "symbol_analysis",
            helper_codex_runtime,
            usage_component="symbol_analysis",
        ),
        _codex_runtime_descriptor(
            "kis_block_manager",
            helper_codex_runtime,
            usage_component="kis_block_manager",
        ),
        _codex_runtime_descriptor(
            "market_judge",
            helper_codex_runtime,
            usage_component="market_judge",
        ),
        _codex_runtime_descriptor(
            "investment_memory",
            helper_codex_runtime,
            usage_component="investment_memory",
        ),
        _codex_runtime_descriptor("daily_discovery", daily_discovery_codex_runtime),
        _codex_runtime_descriptor("binance_block_manager", binance_manager_codex_runtime),
        _codex_runtime_descriptor(
            "crypto_market_research",
            crypto_market_research_codex_runtime,
        ),
        {
            "component": "crypto_alpha",
            "workflow": "",
            "mode": settings.codex_runtime_mode,
            "model": settings.crypto_alpha_llm_model,
            "reasoning_effort": settings.crypto_alpha_llm_reasoning_effort,
            "usage_component": "crypto_alpha",
        },
        {
            "component": "research_reports",
            "workflow": "",
            "mode": settings.codex_runtime_mode,
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "usage_component": "research_reports",
        },
        {
            "component": "research_pipeline",
            "workflow": "",
            "mode": settings.codex_runtime_mode,
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "usage_component": "research_pipeline",
        },
        {
            "component": "portfolio_coach",
            "workflow": "",
            "mode": settings.codex_runtime_mode,
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "usage_component": "portfolio_coach",
        },
    ]


def _iso_age_sec(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds(), 0.0)


def _is_stale_iso(value: Any, interval_sec: int) -> bool:
    age = _iso_age_sec(value)
    return age is None or age >= max(int(interval_sec), 1)


async def _refresh_codex_native_checks_if_due(*, force: bool = False) -> None:
    if not helper_codex_runtime.ready:
        return
    store = CodexNativeStore(settings.codex_native_thread_db_path)
    account = store.latest_account_check()
    if force or account is None or _is_stale_iso(
        account.get("checked_at"),
        settings.codex_native_account_check_interval_sec,
    ):
        await helper_codex_runtime.check_account()

    models = store.list_model_checks()
    component_models = {
        str(row.get("model") or "").strip()
        for row in _codex_native_components()
        if str(row.get("model") or "").strip()
    }
    active_model_rows = [
        row
        for row in models
        if str(row.get("model") or "").strip() in component_models
    ]
    stale_models = {
        str(row.get("model") or "").strip()
        for row in active_model_rows
        if _is_stale_iso(
            row.get("checked_at"),
            settings.codex_native_model_check_interval_sec,
        )
    }
    cached_models = {str(row.get("model") or "").strip() for row in active_model_rows}
    if force or not active_model_rows or bool(component_models - cached_models) or bool(stale_models):
        model_result = await helper_codex_runtime.list_models()
        if str(model_result.get("status") or "").lower() != "ok":
            return
        returned_models = {
            str(model or "").strip()
            for model in list(model_result.get("models") or [])
            if str(model or "").strip()
        }
        missing_models = component_models - returned_models
        for model in missing_models:
            await asyncio.to_thread(
                store.record_model_check,
                model=model,
                available=False,
                detail={"source": "component_config", "component_models": sorted(component_models)},
                error_message="configured model not returned by Codex SDK models()",
            )


def _build_codex_native_status() -> dict[str, Any]:
    store = CodexNativeStore(settings.codex_native_thread_db_path)
    account = store.latest_account_check()
    recent_turns = store.list_recent_turns(limit=50)
    recent_events = store.list_recent_runtime_events(limit=50)
    service_successes = _codex_native_component_service_success_rows()
    recent_errors = sorted(
        [
            *[
                row
                for row in recent_turns
                if str(row.get("status") or "").lower() not in {"ok", "success"}
            ],
            *[
                row
                for row in recent_events
                if str(row.get("status") or "").lower() not in {"ok", "success"}
            ],
        ],
        key=lambda row: str(row.get("finished_at") or row.get("created_at") or ""),
        reverse=True,
    )
    last_error, last_recovered_error = _split_codex_native_recovered_error(
        recent_errors,
        [*recent_turns, *recent_events, *service_successes],
    )
    recovery_rows = [*recent_turns, *recent_events, *service_successes]
    component_models = {
        str(row.get("model") or "").strip()
        for row in _codex_native_components()
        if str(row.get("model") or "").strip()
    }
    return {
        "status": "ok",
        "mode": helper_codex_runtime.mode,
        "model": helper_codex_runtime.resolved_model,
        "reasoning_effort": helper_codex_runtime.resolved_reasoning_effort,
        "thread_mode": settings.codex_native_thread_mode,
        "thread_db_path": settings.codex_native_thread_db_path,
        "compact_after_turns": int(settings.codex_native_compact_after_turns),
        "read_turns": bool(settings.codex_native_read_turns),
        "developer_instructions_enabled": bool(
            settings.codex_native_developer_instructions_enabled
        ),
        "latest_account_check": account,
        "account": account,
        "models": _codex_native_model_checks_with_successful_turns(
            store.list_model_checks(),
            recent_turns,
            active_models=component_models,
        ),
        "components": _codex_native_components(),
        "last_error": last_error,
        "last_recovered_error": last_recovered_error,
        "recent_runtime_events": _mark_codex_native_recovered_rows(
            recent_events[:8],
            recovery_rows,
        ),
        "check_intervals": {
            "account_sec": int(settings.codex_native_account_check_interval_sec),
            "model_sec": int(settings.codex_native_model_check_interval_sec),
        },
        "recent_turns": recent_turns[:8],
    }


def _codex_native_model_checks_with_successful_turns(
    models: list[dict[str, Any]],
    recent_turns: list[dict[str, Any]],
    *,
    active_models: set[str] | None = None,
) -> list[dict[str, Any]]:
    active = {str(model or "").strip() for model in (active_models or set()) if str(model or "").strip()}
    latest_success: dict[str, str] = {}
    for row in recent_turns:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() not in {"ok", "success"}:
            continue
        model = str(row.get("model") or "").strip()
        finished_at = str(row.get("finished_at") or row.get("created_at") or "").strip()
        if not model or not finished_at:
            continue
        if finished_at > latest_success.get(model, ""):
            latest_success[model] = finished_at

    normalized: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    for row in models:
        if not isinstance(row, dict):
            continue
        model = str(row.get("model") or "").strip()
        if active and model not in active:
            continue
        seen_models.add(model)
        success_at = latest_success.get(model, "")
        if not success_at:
            normalized.append(row)
            continue
        fixed = dict(row)
        fixed["available"] = True
        fixed["availability_source"] = "recent_successful_turn"
        fixed["last_successful_turn_at"] = success_at
        fixed.pop("error_message", None)
        normalized.append(fixed)

    for model in sorted(active - seen_models):
        success_at = latest_success.get(model, "")
        if not success_at:
            continue
        normalized.append(
            {
                "model": model,
                "available": True,
                "availability_source": "recent_successful_turn",
                "last_successful_turn_at": success_at,
                "check_source": "synthesized_from_recent_turn",
            }
        )
    return normalized


def _codex_native_component_service_success_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(
        _codex_native_manager_success_rows(
            service=binance_block_trader,
            component="binance_block_manager",
            workflow_id="binance_cycle",
        )
    )
    rows.extend(
        _codex_native_manager_success_rows(
            service=kis_block_trader,
            component="kis_block_manager",
            operation="manager_run",
            workflow_id="kis_intraday_manager",
        )
    )
    return rows


def _codex_native_manager_success_rows(
    *,
    service: Any,
    component: str,
    workflow_id: str,
    operation: str = "",
) -> list[dict[str, Any]]:
    status_fn = getattr(service, "status", None)
    if not callable(status_fn):
        return []
    try:
        payload = status_fn()
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    manager_status = str(payload.get("latest_manager_status") or "").lower().strip()
    manager_run_at = str(payload.get("latest_manager_run_at") or "").strip()
    if manager_status not in {"ok", "success"} or not manager_run_at:
        return []
    return [
        {
            "component": component,
            "operation": operation,
            "workflow_id": workflow_id,
            "status": "ok",
            "finished_at": manager_run_at,
            "recovery_reason": "component_service_status_ok",
        }
    ]


def _codex_native_row_time(row: dict[str, Any]) -> str:
    return str(row.get("finished_at") or row.get("created_at") or "").strip()


def _codex_native_row_scope(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("component") or "").strip(),
        str(row.get("operation") or "").strip(),
    )


def _split_codex_native_recovered_error(
    recent_errors: list[dict[str, Any]],
    recent_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    successes = [
        row
        for row in recent_rows
        if str(row.get("status") or "").lower() in {"ok", "success"}
    ]
    last_recovered: dict[str, Any] | None = None
    for error in recent_errors:
        recovery = _codex_native_recovery_metadata(error, successes)
        if recovery:
            if last_recovered is None:
                last_recovered = {
                    **error,
                    **recovery,
                }
            continue
        return error, last_recovered
    return None, last_recovered


def _mark_codex_native_recovered_rows(
    rows: list[dict[str, Any]],
    recent_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    successes = [
        row
        for row in recent_rows
        if str(row.get("status") or "").lower() in {"ok", "success"}
    ]
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("status") or "").lower() in {"ok", "success"}:
            out.append(row)
            continue
        recovery = _codex_native_recovery_metadata(row, successes)
        if recovery:
            out.append({**row, "status": "recovered", **recovery})
            continue
        out.append(row)
    return out


def _codex_native_recovery_metadata(
    error: dict[str, Any],
    successes: list[dict[str, Any]],
) -> dict[str, str]:
    error_time = _codex_native_row_time(error)
    error_scope = _codex_native_row_scope(error)
    recovered_at = max(
        (
            _codex_native_row_time(row)
            for row in successes
            if _codex_native_row_scope(row) == error_scope
            and _codex_native_row_time(row) > error_time
        ),
        default="",
    )
    if recovered_at:
        recovery_row = max(
            (
                row
                for row in successes
                if _codex_native_row_scope(row) == error_scope
                and _codex_native_row_time(row) == recovered_at
            ),
            key=lambda row: _codex_native_row_time(row),
            default={},
        )
        payload = {"recovered_at": recovered_at}
        if recovery_row.get("recovery_reason"):
            payload["recovery_reason"] = str(recovery_row["recovery_reason"])
        return payload
    if _codex_native_error_recovered_by_environment(error):
        return {
            "recovered_at": datetime.now(timezone.utc).isoformat(),
            "recovery_reason": "codex_sdk_import_available",
        }
    return {}


def _codex_native_error_recovered_by_environment(error: dict[str, Any]) -> bool:
    message = str(error.get("error_message") or "").strip().lower()
    if "openai-codex python sdk is not installed" not in message:
        return False
    return _codex_native_sdk_import_available()


def _codex_native_sdk_import_available() -> bool:
    return importlib.util.find_spec("openai_codex") is not None


_ops_readiness_cache_payload: dict[str, Any] | None = None
_ops_readiness_cache_expires_at = 0.0
_ops_readiness_cache_key: tuple[Any, ...] | None = None


def _callable_cache_identity(func: Any) -> tuple[Any, ...]:
    owner = getattr(func, "__self__", None)
    inner = getattr(func, "__func__", None)
    if owner is not None and inner is not None:
        return ("bound", id(owner), id(inner))
    return ("callable", id(func))


def _ops_readiness_dependency_cache_key() -> tuple[Any, ...]:
    return (
        bool(settings.admin_token_list),
        bool(settings.kis_primary_ready),
        bool(settings.binance_spot_ready),
        bool(settings.binance_futures_ready),
        bool(settings.upbit_ready),
        bool(settings.investment_memory_enabled),
        bool(settings.live_evaluator_enabled),
        bool(settings.market_judge_enabled),
        bool(settings.market_pulse_enabled),
        bool(settings.watchdog_enabled),
        bool(settings.crypto_market_research_enabled),
        bool(settings.crypto_alpha_enabled),
        bool(settings.jue_wiki_enabled),
        bool(settings.kis_block_trader_enabled),
        bool(settings.kis_block_trader_execute_orders),
        bool(settings.binance_block_trader_enabled),
        bool(settings.binance_block_trader_execute_spot_orders),
        bool(settings.binance_block_trader_execute_futures_orders),
        bool(settings.binance_block_trader_execute_upbit_orders),
        settings.llm_model,
        settings.llm_reasoning_effort,
        settings.codex_runtime_mode,
        settings.codex_native_thread_db_path,
        settings.binance_block_trader_llm_model,
        settings.binance_block_trader_llm_reasoning_effort,
        settings.crypto_alpha_llm_model,
        settings.crypto_alpha_llm_reasoning_effort,
        settings.crypto_alpha_context_limit,
        settings.crypto_alpha_source_ids,
        settings.crypto_alpha_crawl_interval_sec,
        settings.crypto_alpha_outcome_interval_sec,
        settings.crypto_market_research_llm_model,
        settings.crypto_market_research_llm_reasoning_effort,
        settings.crypto_market_research_max_symbols,
        settings.crypto_market_research_llm_top_symbols,
        settings.crypto_market_research_kline_intervals,
        settings.trading_validation_db_path,
        settings.trading_validation_max_age_sec,
        settings.live_evaluator_db_path,
        settings.live_performance_db_path,
        settings.naver_reports_enabled,
        _callable_cache_identity(_build_core_runner_processes),
        _callable_cache_identity(kis_block_trader.status),
        _callable_cache_identity(binance_block_trader.status),
        _callable_cache_identity(crypto_market_research_service.status),
        _callable_cache_identity(crypto_alpha_service.status),
        _callable_cache_identity(naver_report_repository.status),
        _callable_cache_identity(market_judgment_engine.status),
        _callable_cache_identity(market_judgment_engine.schedule),
    )


def _build_ops_readiness_cached(*, ttl_sec: float = 30.0) -> dict[str, Any]:
    global _ops_readiness_cache_payload
    global _ops_readiness_cache_expires_at
    global _ops_readiness_cache_key

    now = time.monotonic()
    cache_key = _ops_readiness_dependency_cache_key()
    if (
        ttl_sec > 0
        and _ops_readiness_cache_payload is not None
        and _ops_readiness_cache_key == cache_key
        and now < _ops_readiness_cache_expires_at
    ):
        return dict(_ops_readiness_cache_payload)

    payload = _build_ops_readiness()
    _ops_readiness_cache_payload = dict(payload)
    _ops_readiness_cache_key = cache_key
    _ops_readiness_cache_expires_at = time.monotonic() + max(float(ttl_sec), 0.0)
    return dict(payload)


def _build_ops_readiness() -> dict[str, Any]:
    processes = _build_core_runner_processes()
    try:
        memory_status = _investment_memory_read_only_status()
    except Exception as exc:
        memory_status = {"status": "error", "error_message": str(exc)}
    try:
        block_status = kis_block_trader.status()
    except Exception as exc:
        block_status = {"status": "error", "error_message": str(exc)}
    try:
        binance_block_status = binance_block_trader.status()
    except Exception as exc:
        binance_block_status = {"status": "error", "error_message": str(exc)}
    try:
        crypto_research_status = crypto_market_research_service.status()
    except Exception as exc:
        crypto_research_status = {"status": "error", "error_message": str(exc)}
    try:
        crypto_alpha_status = crypto_alpha_service.status()
    except Exception as exc:
        crypto_alpha_status = {"status": "error", "error_message": str(exc)}
    try:
        reports_repository_status = _naver_reports_ops_status()
    except Exception as exc:
        reports_repository_status = {"status": "error", "error_message": str(exc)}
    market_status = build_market_judgment_readiness_status(market_judgment_engine)
    try:
        market_schedule = market_judgment_engine.schedule()
    except Exception as exc:
        market_schedule = {"status": "error", "error_message": str(exc)}
    pulse_status = build_market_pulse_readiness_status(market_pulse_service)
    llm_usage_payload = _build_llm_usage_status_payload()
    semantic_checks = _build_jue_semantic_checks(
        memory_status=memory_status,
        reports_status=reports_repository_status,
        llm_usage_payload=llm_usage_payload,
        processes=processes,
    )
    try:
        trading_validation_status = _build_trading_validation_ops_status()
    except Exception as exc:
        trading_validation_status = {
            "status": "error",
            "db_path": settings.trading_validation_db_path,
            "error_message": str(exc),
        }
    disk_space_status = build_disk_space_status_payload(
        runtime_state_path=settings.runtime_state_path,
    )

    runner_liveness = build_ops_runner_liveness_payload(
        processes=processes,
        enabled={
            "kis_block_trader": bool(settings.kis_block_trader_enabled),
            "binance_block_trader": bool(settings.binance_block_trader_enabled),
            "crypto_market_research": bool(settings.crypto_market_research_enabled),
            "crypto_alpha": bool(settings.crypto_alpha_enabled),
            "investment_memory": bool(settings.investment_memory_enabled),
            "live_evaluator": bool(settings.live_evaluator_enabled),
            "naver_reports": bool(settings.naver_reports_enabled),
            "watchdog": bool(settings.watchdog_enabled),
            "market_judge": bool(settings.market_judge_enabled),
            "market_pulse": bool(settings.market_pulse_enabled),
            "jue_wiki": bool(settings.jue_wiki_enabled),
        },
    )
    environment_signals = build_ops_environment_signals_payload(
        admin_token_configured=bool(settings.admin_token_list),
        disk_space_status=disk_space_status,
        live_execution={
            "kis": bool(settings.kis_block_trader_execute_orders),
            "binance_spot": bool(settings.binance_block_trader_execute_spot_orders),
            "binance_futures": bool(
                settings.binance_block_trader_execute_futures_orders
            ),
            "upbit": bool(settings.binance_block_trader_execute_upbit_orders),
        },
        readiness={
            "kis_primary": bool(settings.kis_primary_ready),
            "binance_spot": bool(settings.binance_spot_ready),
            "binance_futures": bool(settings.binance_futures_ready),
            "upbit": bool(settings.upbit_ready),
        },
        kill_switch_enabled=bool((block_status.get("kill_switch") or {}).get("enabled")),
        binance_kill_switch_enabled=bool(
            (binance_block_status.get("kill_switch") or {}).get("enabled")
        ),
        memory_status=memory_status,
        feature_enabled={
            "investment_memory": bool(settings.investment_memory_enabled),
            "live_evaluator": bool(settings.live_evaluator_enabled),
            "market_judge": bool(settings.market_judge_enabled),
            "market_pulse": bool(settings.market_pulse_enabled),
            "watchdog": bool(settings.watchdog_enabled),
            "crypto_market_research": bool(settings.crypto_market_research_enabled),
            "crypto_alpha": bool(settings.crypto_alpha_enabled),
            "jue_wiki": bool(settings.jue_wiki_enabled),
        },
    )
    llm_operational = build_llm_operational_status_payload(
        block_status=block_status,
        market_schedule=market_schedule,
        processes=processes,
        configured=bool(settings.codex_runtime_ready),
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        native_mode=settings.codex_runtime_mode,
    )
    readiness_signals = build_finalize_ops_readiness_signals_payload(
        environment_signals=environment_signals,
        trading_validation_status=trading_validation_status,
        runner_liveness=runner_liveness,
        llm_operational=llm_operational,
        semantic_checks=semantic_checks,
    )
    manager_interval = int(
        (block_status.get("config") or {}).get(
            "manager_interval_sec",
            settings.kis_block_trader_manager_interval_sec,
        )
    )
    market_clock = (
        market_schedule.get("clock") if isinstance(market_schedule.get("clock"), dict) else {}
    )
    sections = {
        "memory": build_ops_memory_payload_payload(
            enabled=bool(settings.investment_memory_enabled),
            memory_status=memory_status,
        ),
        "reports": build_ops_reports_payload_payload(
            enabled=bool(settings.naver_reports_enabled),
            repository_status=reports_repository_status,
            runner=processes.get("naver_reports", {}),
            state_path=settings.research_state_path,
            interval_sec=settings.naver_reports_interval_sec,
        ),
        "live_evaluator": build_ops_live_evaluator_payload_payload(
            enabled=bool(settings.live_evaluator_enabled),
            state_path=settings.live_evaluator_state_path,
            edge_db_path=settings.live_evaluator_db_path,
            performance_db_path=settings.live_performance_db_path,
            interval_sec=settings.live_evaluator_interval_sec,
            runner=processes.get("live_evaluator", {}),
        ),
        "trading_validation": build_ops_trading_validation_payload_payload(
            status=trading_validation_status,
            db_path=settings.trading_validation_db_path,
        ),
        "watchdog": build_ops_watchdog_payload_payload(
            enabled=bool(settings.watchdog_enabled),
            state_path=settings.watchdog_state_path,
            db_path=settings.watchdog_db_path,
            interval_sec=settings.watchdog_interval_sec,
            runner=processes.get("watchdog", {}),
        ),
        "market_judge": build_ops_market_judge_payload_payload(
            enabled=bool(settings.market_judge_enabled),
            status=market_status,
            schedule=market_schedule,
        ),
        "market_pulse": build_ops_market_pulse_payload_payload(
            enabled=bool(settings.market_pulse_enabled),
            status=pulse_status,
        ),
        "kis_block_trader": build_ops_kis_block_trader_payload_payload(
            enabled=bool(settings.kis_block_trader_enabled),
            status=block_status,
            next_manager_run_at=_next_from_latest_or_krx_clock(
                block_status.get("latest_manager_run_at"),
                manager_interval,
                clock=block_status.get("clock")
                if isinstance(block_status.get("clock"), dict)
                else None,
                include_post_close=False,
            ),
        ),
        "binance_block_trader": build_ops_binance_block_trader_payload_payload(
            enabled=bool(settings.binance_block_trader_enabled),
            status=binance_block_status,
            runner=processes.get("binance_block_trader", {}),
            model=settings.binance_block_trader_llm_model,
            reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
            spot_live=bool(settings.binance_block_trader_execute_spot_orders),
            futures_live=bool(settings.binance_block_trader_execute_futures_orders),
            upbit_live=bool(settings.binance_block_trader_execute_upbit_orders),
            account_risk_pct=settings.binance_block_trader_account_risk_pct,
            max_total_exposure_usdt=(
                settings.binance_block_trader_max_total_exposure_usdt
            ),
            max_symbol_exposure_pct=(
                settings.binance_block_trader_max_symbol_exposure_pct
            ),
            min_reward_risk=settings.binance_block_trader_min_reward_risk,
            next_manager_run_at=_next_from_latest(
                binance_block_status.get("latest_manager_run_at"),
                int(settings.binance_block_trader_manager_interval_sec),
            ),
        ),
        "crypto_market_research": build_ops_crypto_market_research_payload_payload(
            enabled=bool(settings.crypto_market_research_enabled),
            status=crypto_research_status,
            runner=processes.get("crypto_market_research", {}),
            model=settings.crypto_market_research_llm_model,
            reasoning_effort=settings.crypto_market_research_llm_reasoning_effort,
            feature_interval_sec=settings.crypto_market_research_feature_interval_sec,
            llm_interval_sec=settings.crypto_market_research_llm_interval_sec,
            max_symbols=settings.crypto_market_research_max_symbols,
            llm_top_symbols=settings.crypto_market_research_llm_top_symbols,
            kline_intervals=_parse_crypto_kline_intervals(
                settings.crypto_market_research_kline_intervals
            ),
            regime_enabled=bool(settings.crypto_market_research_regime_enabled),
            squeeze_guard_enabled=bool(
                settings.crypto_market_research_squeeze_guard_enabled
            ),
            auto_universe_enabled=bool(
                settings.crypto_market_research_auto_universe_enabled
            ),
            auto_universe_limit=settings.crypto_market_research_auto_universe_limit,
        ),
        "crypto_alpha": build_ops_crypto_alpha_payload_payload(
            enabled=bool(settings.crypto_alpha_enabled),
            status=crypto_alpha_status,
            runner=processes.get("crypto_alpha", {}),
            model=settings.crypto_alpha_llm_model,
            reasoning_effort=settings.crypto_alpha_llm_reasoning_effort,
            crawl_interval_sec=settings.crypto_alpha_crawl_interval_sec,
            outcome_interval_sec=settings.crypto_alpha_outcome_interval_sec,
            context_limit=settings.crypto_alpha_context_limit,
            source_ids=settings.crypto_alpha_source_ids,
        ),
        "jue_wiki": build_ops_jue_wiki_payload_payload(
            enabled=bool(settings.jue_wiki_enabled),
            status=jue_wiki_service.status(),
            runner=processes.get("jue_wiki", {}),
            state_path=".runtime/jue_wiki_runner.json",
            interval_sec=settings.jue_wiki_runner_interval_sec,
        ),
    }
    for section_key in ("kis_block_trader", "binance_block_trader", "jue_wiki"):
        readiness_signals = merge_ops_section_readiness_signals_payload(
            readiness_signals,
            sections.get(section_key, {}),
        )
    readiness_payload = build_ops_readiness_payload(
        readiness_signals=readiness_signals,
        checked_at=datetime.now(timezone.utc).isoformat(),
        processes=processes,
        admin_token_configured=bool(settings.admin_token_list),
        kis_ready=bool(settings.kis_primary_ready),
        kis_rate_limit={
            "enabled": bool(settings.kis_rate_limit_enabled),
            "rest_rate_limit_per_sec": float(settings.kis_rest_rate_limit_per_sec),
            "account_min_interval_sec": float(settings.kis_account_min_interval_sec),
            "token_min_interval_sec": float(settings.kis_token_min_interval_sec),
            "db_path": settings.kis_rate_limit_db_path,
        },
        llm_ready=bool(settings.codex_runtime_ready),
        disk_space=disk_space_status,
        llm=llm_operational,
        llm_usage=llm_usage_payload,
        semantic_checks=semantic_checks,
        codex_native=_build_codex_native_status(),
        telegram_ready=bool(telegram.config.ready),
        live_trading_enabled=bool(environment_signals["live_trading_enabled"]),
        paper_mode=bool(environment_signals["paper_mode"]),
        kill_switch=block_status.get("kill_switch") or {"enabled": False},
        sections=sections,
        next_market_open_at=str(market_clock.get("next_open_at") or ""),
    )
    readiness_payload["jue_wiki"] = sections.get("jue_wiki", {})
    return readiness_payload


def _attach_block_memory(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    blocks = [
        dict(row)
        for row in list(result.get("blocks") or [])
        if isinstance(row, dict)
    ]
    block_ids = [str(row.get("block_id") or "") for row in blocks]
    reflection_status = investment_memory_service.block_reflection_statuses(block_ids)
    policies = investment_memory_service.active_policies(limit=6)
    for row in blocks:
        block_id = str(row.get("block_id") or "")
        status_row = reflection_status.get(block_id) or {
            "status": "pending"
            if str(row.get("status") or "") in {"closed", "error"}
            else "not_due"
        }
        row["reflection_status"] = status_row
        row["memory_links"] = {
            "block": f"/api/memory/blocks/{block_id}" if block_id else "",
            "symbol": f"/api/memory/symbols/{row.get('symbol')}" if row.get("symbol") else "",
        }
        row["policy_impacts"] = (
            row.get("policy_rule_impacts")
            if isinstance(row.get("policy_rule_impacts"), list)
            else []
        ) or policies[:3]
    result["blocks"] = blocks
    result["memory"] = {
        "reflection_status": reflection_status,
        "active_policies": policies,
    }
    return result


async def _build_kis_rebalance_status_payload() -> dict[str, Any]:
    updated_at = datetime.now(timezone.utc).isoformat()
    snapshot = await kis_block_trader.snapshot_compact(refresh_live=False)
    return build_kis_block_rebalance_status_payload(
        snapshot if isinstance(snapshot, dict) else {},
        updated_at=updated_at,
        primary_ready=bool(settings.kis_primary_ready),
    )


def _extract_symbol_hint(query: str) -> str:
    raw = str(query or "")

    match = re.search(r"(?<!\d)(\d{6})(?!\d)", raw)
    return match.group(1) if match else ""


def _append_research_query_log(payload: dict[str, Any]) -> None:
    try:
        RESEARCH_QUERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RESEARCH_QUERY_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _safe_strategy_limit(value: Any) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = 8
    return max(min(raw, 12), 3)


def _default_strategy_query(query: Any = "") -> str:
    text = str(query or "").strip()
    return text or "다음 거래일 관심 후보를 전략적으로 정리해줘"


def _read_strategy_research_feed() -> dict[str, Any] | None:
    feed, _ = read_active_research_feed(settings)
    merged: dict[str, Any] = dict(feed) if isinstance(feed, dict) else {}
    try:
        discovery = daily_discovery_service.latest_context(
            limit=_daily_discovery_context_limit()
        )
    except Exception as exc:
        if merged:
            merged["daily_discovery"] = {
                "status": "error",
                "error_message": str(exc),
            }
        return merged or None

    if isinstance(discovery, dict) and (
        discovery.get("items") or str(discovery.get("status") or "").lower() == "ok"
    ):
        merged["daily_discovery"] = discovery
    return merged or None


def _is_krx_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _symbols_from_csv(value: Any) -> list[str]:
    return [
        symbol
        for symbol in dict.fromkeys(
            item.strip()
            for item in re.split(r"[\s,;]+", str(value or ""))
            if item.strip()
        )
        if _is_krx_symbol(symbol)
    ]


def _strategy_fundamental_targets(limit: int | None = None) -> list[str]:
    max_items = max(int(limit or settings.valuation_max_symbols_per_collect), 1)
    discovered: list[str] = []

    watchlist = _symbols_from_csv(settings.valuation_watchlist)
    repair_targets = jue_wiki_repair_target_symbols(
        settings.jue_wiki_db_path,
        limit=max_items,
    )

    feed = _read_strategy_research_feed()
    if isinstance(feed, dict):
        for row in list(feed.get("items") or [])[:30]:
            if isinstance(row, dict):
                discovered.extend(str(item or "").strip() for item in list(row.get("picks") or []))

    try:
        strategy_payload = strategy_intelligence.build_candidates(
            query=_default_strategy_query(""),
            research_feed=feed,
            limit=12,
        )
    except Exception:
        strategy_payload = {}
    for row in list(strategy_payload.get("candidates") or []) + list(strategy_payload.get("exclusions") or []):
        if isinstance(row, dict):
            discovered.append(str(row.get("symbol") or "").strip())

    try:
        signal_payload = strategy_intelligence.list_external_signals(limit=300)
    except Exception:
        signal_payload = {}
    for row in list(signal_payload.get("items") or []):
        if isinstance(row, dict):
            discovered.append(str(row.get("symbol") or "").strip())

    try:
        report_rows = naver_report_repository.search(
            query="",
            category="company_analysis",
            limit=max_items,
        )
    except Exception:
        report_rows = []
    for row in report_rows:
        discovered.append(str(row.get("symbol") or "").strip())

    return merge_fundamental_target_symbols(
        watchlist=watchlist,
        repair_targets=repair_targets,
        discovered=discovered,
        limit=max_items,
    )


def _collect_helper_report_rows(
    *,
    query: str,
    symbol: str,
    broker: str,
    date_from: str,
    date_to: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = naver_report_repository.search(
        query=query,
        symbol=symbol,
        category="",
        limit=limit * 3,
    )

    def apply_filters(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in candidate_rows:
            if broker and str(row.get("broker") or "") != broker:
                continue
            published = str(row.get("published_at") or "")
            if date_from and published and published < date_from:
                continue
            if date_to and published and published > date_to:
                continue
            out.append(row)
        return out

    filtered = apply_filters(rows)
    for keyword in _helper_query_keywords(query):
        if filtered:
            break
        rows = naver_report_repository.search(
            query=keyword,
            symbol=symbol,
            category="",
            limit=100,
        )
        filtered = apply_filters(rows)

    if not filtered and symbol:
        rows = naver_report_repository.search(
            query="",
            symbol=symbol,
            category="",
            limit=limit * 3,
        )
        filtered = apply_filters(rows)

    if not filtered and not symbol:
        for keyword in _helper_query_keywords(query) or [query]:
            rows = naver_report_repository.search(
                query=keyword,
                symbol="",
                category="",
                limit=100,
            )
            filtered = apply_filters(rows)
            if filtered:
                break

    enriched_rows: list[dict[str, Any]] = []
    for row in filtered:
        payload = dict(row)
        report_id = int(payload.get("report_id") or 0)
        if report_id > 0:
            report = naver_report_repository.get_report(report_id)
            if report:
                payload["_sort_text"] = _clean_helper_text(
                    report.get("content"), limit=2200
                )
        enriched_rows.append(payload)
    filtered = enriched_rows

    exact_rows = [
        row
        for row in filtered
        if _helper_report_has_exact_symbol_match(row, query=query, symbol=symbol)
    ]
    if exact_rows:
        filtered = exact_rows

    filtered = sorted(
        filtered,
        key=lambda row: _helper_report_sort_key(row, query=query, symbol=symbol),
    )[:limit]
    facts_rows: list[dict[str, Any]] = []
    citations: list[str] = []
    for row in filtered:
        report_id = int(row.get("report_id") or 0)
        facts = naver_report_repository.get_report_facts(report_id)
        facts_payload = dict(row)
        facts_payload.pop("_sort_text", None)
        facts_payload["title"] = _clean_report_title(facts_payload)
        facts_payload["snippet"] = _clean_helper_text(
            facts_payload.get("snippet"), limit=700
        )
        if facts:
            facts_payload["facts"] = facts
            for quote in list(facts.get("evidence_quotes") or [])[:2]:
                page = str((quote or {}).get("page") or "?")
                citations.append(
                    _format_citation(
                        str(row.get("broker") or ""),
                        str(row.get("published_at") or ""),
                        page,
                    )
                )
        facts_rows.append(facts_payload)

    return facts_rows, citations


def _collect_helper_rag_rows(
    *,
    query: str,
    symbol: str,
    broker: str,
    date_from: str,
    date_to: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not settings.rag_enabled or rag_store is None:
        return []
    rows = rag_store.query(
        query=query,
        symbol=symbol,
        broker=broker,
        date_from=date_from,
        date_to=date_to,
        limit=min(limit, 8),
    )
    for keyword in _helper_query_keywords(query):
        if rows:
            break
        rows = rag_store.query(
            query=keyword,
            symbol=symbol,
            broker=broker,
            date_from=date_from,
            date_to=date_to,
            limit=min(limit, 8),
        )
    if not rows and symbol:
        for fallback_query in [*_helper_query_keywords(query), query]:
            rows = rag_store.query(
                query=fallback_query,
                symbol="",
                broker=broker,
                date_from=date_from,
                date_to=date_to,
                limit=min(limit, 8),
            )
            if rows:
                break
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["title"] = _clean_report_title(payload)
        payload["content"] = _clean_helper_text(payload.get("content"), limit=900)
        if not payload["content"] or "편집상의 공백페이지" in payload["content"]:
            continue
        out.append(payload)
    return out


def _collect_helper_strategy_context(query: str, limit: int) -> dict[str, Any]:
    try:
        payload = strategy_intelligence.build_candidates(
            query=_default_strategy_query(query),
            research_feed=_read_strategy_research_feed(),
            limit=_safe_strategy_limit(limit),
        )
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc)[:240],
            "candidates": [],
            "source_status": [],
        }

    candidates: list[dict[str, Any]] = []
    for row in list(payload.get("candidates") or [])[: max(min(int(limit), 8), 1)]:
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "name": str(row.get("name") or ""),
                "score": int(row.get("score") or 0),
                "confidence": int(row.get("confidence") or 0),
                "confidence_label": str(row.get("confidence_label") or ""),
                "stance": str(row.get("stance") or ""),
                "reasons": list(row.get("reasons") or [])[:5],
                "risks": list(row.get("risks") or [])[:4],
                "checks": list(row.get("checks") or [])[:4],
                "sources": list(row.get("sources") or [])[:6],
                "citations": list(row.get("citations") or [])[:5],
                "score_components": row.get("score_components") or {},
            }
        )

    return {
        "status": str(payload.get("status") or "ok"),
        "query": str(payload.get("query") or query),
        "candidates": candidates,
        "source_status": strategy_intelligence.source_status(),
    }


async def _build_helper_llm_answer(
    *,
    query: str,
    symbol: str,
    facts_rows: list[dict[str, Any]],
    rag_rows: list[dict[str, Any]],
    strategy_context: dict[str, Any] | None,
    source_draft_answer: str,
) -> dict[str, Any]:
    strategy_refs = list((strategy_context or {}).get("candidates") or [])[:8]
    if not helper_codex_runtime.ready:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "mode": "llm_unavailable",
                "error_message": "Codex native runtime unavailable",
            },
        )
    if not (facts_rows or rag_rows or strategy_refs):
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "mode": "no_evidence",
                "error_message": "helper evidence missing",
            },
        )

    report_refs = [
        {
            "report_id": int(row.get("report_id") or 0),
            "title": _clean_report_title(row),
            "broker": str(row.get("broker") or ""),
            "published_at": str(row.get("published_at") or ""),
            "symbol": str(row.get("symbol") or ""),
            "snippet": _clean_helper_text(row.get("snippet"), limit=500),
            "facts": row.get("facts") if isinstance(row.get("facts"), dict) else {},
        }
        for row in facts_rows[:8]
    ]
    rag_refs = [
        {
            "report_id": int(row.get("report_id") or 0),
            "chunk_index": int(row.get("chunk_index") or 0),
            "title": _clean_report_title(row),
            "broker": str(row.get("broker") or ""),
            "published_at": str(row.get("published_at") or ""),
            "symbol": str(row.get("symbol") or ""),
            "page_start": int(row.get("page_start") or 0),
            "content": _clean_helper_text(row.get("content"), limit=650),
        }
        for row in rag_rows[:8]
    ]
    prompt = {
        "question": query,
        "symbol_hint": symbol,
        "report_facts": report_refs,
        "rag_chunks": rag_refs,
        "strategy_candidates": strategy_refs,
        "strategy_source_status": list(
            (strategy_context or {}).get("source_status") or []
        )[:8],
        "source_draft_answer": source_draft_answer,
        "rules": [
            "Use report_facts, rag_chunks, and strategy_candidates as evidence.",
            "For open-ended candidate questions, provide a ranked watchlist, "
            "entry validation conditions, and invalidation checks instead of only "
            "saying evidence is weak.",
            "Treat Whale Insight and after-close sources as 참고 신호, not standalone proof.",
            "If evidence is weak, say so explicitly.",
            "When proposing action, express it as a HERMES block-trading candidate with validation, target/stop, and safety-gate assumptions.",
            "Use these section headings when applicable: 요약(3줄), 전략 후보/감시 리스트, "
            "핵심 근거(인용 포함), 리포트 간 차이/컨센서스, 리스크/반론 체크리스트, 근거 부족 시 안내.",
            "Write in Korean.",
        ],
        "output_schema": {
            "answer_md": "string",
            "confidence": "low|medium|high",
            "followups": ["string"],
            "limitations": ["string"],
        },
    }
    payload = {
        "model": helper_codex_runtime.resolved_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "telemetry": {"component": "research_ask", "operation": "helper_ask"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return only one JSON object. You are an evidence-bound "
                    "HERMES trading copilot. Produce actionable trading judgment "
                    "while respecting block rules and safety gates."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    timeout_ms = max(int(settings.codex_runtime_timeout_ms), 1000)
    result = await helper_codex_runtime.complete(payload, timeout_ms=timeout_ms)
    if not bool(result.get("ok")):
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "mode": "llm_error",
                "error_message": str(result.get("error") or "codex_runtime_failed")[:240],
            },
        )

    parsed = _parse_helper_llm_content(str(result.get("content") or ""))
    if not parsed:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "mode": "llm_parse_error",
                "error_message": "LLM response was empty",
            },
        )
    answer = str(parsed.get("answer_md") or parsed.get("answer") or "").strip()
    if not answer:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "mode": "llm_empty_answer",
                "error_message": "LLM response did not include answer_md",
            },
        )
    answer = _normalize_helper_answer_contract(answer)
    return {
        "answer": answer,
        "mode": "llm",
        "confidence": str(parsed.get("confidence") or "medium"),
        "followups": [
            str(item).strip()
            for item in list(parsed.get("followups") or [])
            if str(item).strip()
        ][:5],
        "limitations": [
            str(item).strip()
            for item in list(parsed.get("limitations") or [])
            if str(item).strip()
        ][:5],
        "usage": result.get("usage"),
    }


async def _helper_ask_impl(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    query = query[:600]
    limit = _safe_helper_limit(payload.get("limit"))
    symbol = str(payload.get("symbol") or "").strip() or _extract_symbol_hint(query)
    broker = str(payload.get("broker") or "").strip()
    date_from = str(payload.get("date_from") or "").strip()
    date_to = str(payload.get("date_to") or "").strip()

    facts_rows, citations = _collect_helper_report_rows(
        query=query,
        symbol=symbol,
        broker=broker,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    rag_rows = _collect_helper_rag_rows(
        query=query,
        symbol=symbol,
        broker=broker,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    for row in rag_rows[:8]:
        citations.append(
            _format_citation(
                str(row.get("broker") or ""),
                str(row.get("published_at") or ""),
                str(row.get("page_start") or "?"),
            )
        )

    strategy_context = _collect_helper_strategy_context(query, limit)
    for row in list(strategy_context.get("candidates") or [])[:5]:
        for citation in list(row.get("citations") or [])[:2]:
            citations.append(str(citation))

    source_draft_answer = _build_helper_source_draft_answer(
        query=query,
        facts_rows=facts_rows,
        rag_rows=rag_rows,
        strategy_context=strategy_context,
    )
    try:
        answer_payload = await _build_helper_llm_answer(
            query=query,
            symbol=symbol,
            facts_rows=facts_rows,
            rag_rows=rag_rows,
            strategy_context=strategy_context,
            source_draft_answer=source_draft_answer,
        )
    except HTTPException as exc:
        allow_source_only = bool(payload.get("allow_source_only_on_llm_error"))
        has_evidence = bool(facts_rows or rag_rows or strategy_context.get("candidates"))
        if not allow_source_only or not has_evidence or int(exc.status_code) < 500:
            raise
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        llm_error_mode = str(detail.get("mode") or "llm_error")
        llm_error_message = str(detail.get("error_message") or "llm_error")[:240]
        answer_payload = {
            "answer": source_draft_answer,
            "mode": f"{llm_error_mode}_source_draft",
            "confidence": "low",
            "source_draft": True,
            "llm_error": True,
            "llm_error_mode": llm_error_mode,
            "llm_error_message": llm_error_message,
            "followups": [],
            "limitations": [
                f"LLM 응답 실패: {llm_error_message}",
                "리포트/RAG/전략 후보 기반 초안이며 native 판단을 대체하지 않습니다.",
            ],
        }

    used_report_ids = sorted(
        {
            int(row.get("report_id") or 0)
            for row in [*facts_rows, *rag_rows]
            if int(row.get("report_id") or 0) > 0
        }
    )
    _append_research_query_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "helper_ask",
            "query": query,
            "symbol": symbol,
            "filters": {
                "broker": broker,
                "date_from": date_from,
                "date_to": date_to,
            },
            "top_k": limit,
            "answer_mode": answer_payload.get("mode"),
            "used_report_ids": used_report_ids,
            "citations": citations[:24],
            "strategy_candidate_count": len(strategy_context.get("candidates") or []),
        }
    )

    return {
        "status": "ok",
        "query": query,
        "intent": classify_strategy_intent(query),
        "symbol": symbol,
        "model": helper_codex_runtime.resolved_model,
        "mode": answer_payload.get("mode"),
        "confidence": answer_payload.get("confidence", "medium"),
        "answer": answer_payload.get("answer") or "",
        "citations": citations[:24],
        "followups": answer_payload.get("followups") or [],
        "limitations": answer_payload.get("limitations") or [],
        "source_draft": bool(answer_payload.get("source_draft")),
        "llm_error": bool(answer_payload.get("llm_error")),
        "llm_error_mode": answer_payload.get("llm_error_mode") or "",
        "llm_error_message": answer_payload.get("llm_error_message") or "",
        "count": len(facts_rows),
        "rag_count": len(rag_rows),
        "strategy": strategy_context,
        "items": facts_rows,
        "rag_items": rag_rows,
    }


register_app_routes(
    app,
    build_assistant_support_route_specs(
        AssistantSupportRouteGroupDeps(
            require_admin_auth=require_admin_auth,
            helper_ask=lambda payload: _helper_ask_impl(payload),
            static_dir=lambda: STATIC_DIR,
            jue_registry_factory=lambda: JueSkillRegistry(),
            jue_available_workflow_ids=lambda registry: _available_jue_workflow_ids(
                registry
            ),
            jue_validation_error_type=JueSkillValidationError,
            jue_lifecycle_repository_factory=lambda db_path: JueLifecycleRepository(
                db_path
            ),
            jue_investment_memory_db_path=lambda: settings.investment_memory_db_path,
            llm_usage_summary=lambda trading_day, period=None: _llm_usage_summary_async(
                trading_day,
                period,
            ),
            llm_usage_status=lambda: _build_llm_usage_status_payload_async(),
            llm_runtime=lambda: helper_codex_runtime,
            llm_timeout_ms=lambda: int(settings.codex_runtime_timeout_ms),
            llm_thread_mode=lambda: settings.codex_native_thread_mode,
            llm_now=lambda: datetime.now(timezone.utc),
        )
    ),
)

register_app_routes(
    app,
    build_core_app_route_specs(
        CoreAppRouteGroupDeps(
            require_admin_auth=require_admin_auth,
            strategy_engine=lambda: strategy_intelligence,
            strategy_classify_intent=classify_strategy_intent,
            strategy_default_query=lambda query: _default_strategy_query(query),
            strategy_safe_limit=lambda value: _safe_strategy_limit(value),
            strategy_read_research_feed=lambda: _read_strategy_research_feed(),
            strategy_collect_source_ids=lambda payload: _strategy_collect_source_ids(
                payload
            ),
            strategy_safe_collect_sources=lambda source_ids=None: (
                _safe_strategy_collect_sources(source_ids)
            ),
            strategy_build_insight_collector=lambda sources=None: (
                _build_strategy_insight_collector(sources)
            ),
            now=lambda: datetime.now(timezone.utc),
            fundamentals_service=lambda: symbol_fundamentals_service,
            analysis_service=lambda: symbol_analysis_service,
            symbols_from_csv=_symbols_from_csv,
            strategy_fundamental_targets=_strategy_fundamental_targets,
            is_krx_symbol=_is_krx_symbol,
            max_symbols_per_collect=lambda: int(
                settings.valuation_max_symbols_per_collect
            ),
            daily_discovery_service=lambda: daily_discovery_service,
            discovery_today=lambda: datetime.now(KST).date(),
            discovery_config_payload=lambda: {
                "enabled": bool(settings.daily_discovery_enabled),
                "kospi_count": int(settings.daily_discovery_kospi_count),
                "kosdaq_count": int(settings.daily_discovery_kosdaq_count),
                "exclude_recent_days": int(
                    settings.daily_discovery_exclude_recent_days
                ),
                "candidate_limit_per_market": int(
                    settings.daily_discovery_candidate_limit_per_market
                ),
                "db_path": settings.daily_discovery_db_path,
            },
            build_ops_readiness=lambda: _build_ops_readiness_cached(),
            build_ops_restart_readiness=lambda: _build_ops_readiness(),
            build_codex_native_status=lambda: _build_codex_native_status(),
            refresh_codex_native_checks=(
                lambda force=False: _refresh_codex_native_checks_if_due(force=force)
            ),
            system_metrics_snapshot=lambda: system_metrics_service.snapshot(),
            watchdog_status=lambda: watchdog_status(settings),
            restart_runner_processes=(
                lambda keys, delay_sec=0.5: restart_runner_processes(
                    keys,
                    delay_sec=delay_sec,
                )
            ),
            build_settings_catalog=lambda: build_settings_catalog(settings),
            update_settings_env=(
                lambda updates, confirm_high_risk=False: update_settings_env(
                    settings,
                    updates,
                    confirm_high_risk=confirm_high_risk,
                )
            ),
            live_authority_payload=lambda: _build_live_authority_payload(),
            trading_validation_status_payload=(
                lambda venue: _build_trading_validation_endpoint_payload(venue)
            ),
            trading_validation_service=lambda venue: _trading_validation_service(
                venue
            ),
            sync_live_performance_and_edges=lambda: sync_live_performance_and_edges(
                settings
            ),
            backtest_manager=lambda: backtest_live_manager,
            backtest_data_registry=lambda: backtest_data_registry,
            backtest_scenarios=list_scenarios,
            backtest_load_sessions=lambda: load_runtime_sessions(
                settings.runtime_sessions_path
            ),
            backtest_build_config=_build_backtest_config,
            backtest_emit_interval=lambda: int(settings.backtest_emit_interval),
            build_dashboard_payload=(
                lambda include_telegram=True, force_refresh=False: _build_dashboard_payload(
                    include_telegram=include_telegram,
                    force_refresh=force_refresh,
                )
            ),
            market_judgment_engine=lambda: market_judgment_engine,
            market_pulse_service=lambda: market_pulse_service,
            kis_primary_ready=lambda: bool(settings.kis_primary_ready),
            memory_status=lambda scope="", compact=False: investment_memory_service.status(
                scope=scope,
                compact=compact,
            ),
            memory_today=lambda scope="", compact=False: investment_memory_service.today(
                scope=scope,
                compact=compact,
            ),
            memory_symbol=lambda symbol: investment_memory_service.symbol_memory(
                symbol
            ),
            memory_block=lambda block_id: investment_memory_service.block_memory(
                block_id
            ),
            memory_initialize=lambda force=False: investment_memory_service.initialize(
                force=force
            ),
            memory_build_context=lambda: _build_investment_memory_context(),
            memory_run_ritual=(
                lambda slot, context, send_telegram=False, force=False: investment_memory_service.run_ritual(
                    slot=slot,
                    context=context,
                    send_telegram=send_telegram,
                    force=force,
                )
            ),
            memory_run_update=(
                lambda context, force=False: investment_memory_service.run_update(
                    context=context,
                    force=force,
                )
            ),
            memory_seed_current=(
                lambda context, force=False: investment_memory_service.seed_current(
                    context=context,
                    force=force,
                )
            ),
            memory_run_due_reflections=(
                lambda context, force=False: investment_memory_service.run_due_reflections(
                    context=context,
                    force=force,
                )
            ),
            memory_latest_period_review=(
                lambda period_type: investment_memory_service.latest_period_review(
                    period_type
                )
            ),
            memory_period_reviews=(
                lambda period_type="", limit=12: investment_memory_service.period_reviews(
                    period_type=period_type,
                    limit=limit,
                )
            ),
            memory_run_period_review=(
                lambda period_type, context, force=False: investment_memory_service.run_period_review(
                    period_type=period_type,
                    context=context,
                    force=force,
                )
            ),
            memory_latest_historical_replay=(
                lambda period_type: investment_memory_service.latest_historical_replay(
                    period_type
                )
            ),
            memory_historical_replays=(
                lambda period_type="", limit=12: investment_memory_service.historical_replays(
                    period_type=period_type,
                    limit=limit,
                )
            ),
            memory_run_historical_replay=(
                lambda period_type, context, force=False: investment_memory_service.run_historical_replay(
                    period_type=period_type,
                    context=context,
                    force=force,
                )
            ),
            memory_policy_scorecards=(
                lambda limit=30: investment_memory_service.policy_scorecards(
                    limit=limit
                )
            ),
            memory_policy_rules=(
                lambda active_only=False, limit=30: investment_memory_service.policy_rules(
                    active_only=active_only,
                    limit=limit,
                )
            ),
            memory_policy_revisions=(
                lambda status="", limit=30: investment_memory_service.policy_revisions(
                    status=status,
                    limit=limit,
                )
            ),
            memory_activate_policy_revision=(
                lambda revision_id: investment_memory_service.activate_policy_revision(
                    revision_id
                )
            ),
            memory_reject_policy_revision=(
                lambda revision_id: investment_memory_service.reject_policy_revision(
                    revision_id
                )
            ),
            wiki_service=jue_wiki_service,
        )
    ),
)


def _crypto_quant_status() -> dict[str, Any]:
    return build_evidence_crypto_quant_status(
        crypto_quant_repository,
        enabled=bool(settings.crypto_quant_enabled),
    )


async def _crypto_pattern_lab_status() -> dict[str, Any]:
    if crypto_pattern_service is None:
        return build_evidence_unavailable_service_status(
            enabled=bool(_setting("crypto_pattern_lab_enabled", True)),
            default_message="crypto pattern lab service is unavailable",
            import_error=_crypto_pattern_lab_import_error,
        )
    return await build_evidence_service_status(
        crypto_pattern_service,
        unavailable_message="crypto pattern lab service has no status method",
    )


def _kr_equity_pattern_lab_status() -> dict[str, Any]:
    return build_evidence_kr_equity_pattern_lab_status(
        KREquityPatternLabRepository,
        db_path=settings.kr_equity_pattern_lab_db_path,
    )


async def _service_status(service: Any) -> dict[str, Any]:
    return await build_evidence_service_status(service)


def _investment_memory_repository() -> Any:
    return investment_memory_service.repository


def _investment_memory_read_only_status() -> dict[str, Any]:
    return build_evidence_memory_read_only_status(_investment_memory_repository())


def _runtime_storage_policy() -> RuntimeStoragePolicy:
    runtime_dir = Path(settings.runtime_state_path).parent
    return RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir or Path(".runtime")),
        reports_db_path=settings.naver_reports_db_path,
        pdf_archive_dir=settings.naver_reports_pdf_archive_dir,
        rag_persist_path=settings.rag_persist_path,
        large_file_threshold_mb=settings.runtime_storage_large_file_threshold_mb,
        prune_unreferenced_pdfs=settings.runtime_storage_prune_unreferenced_pdfs,
        prune_extracted_report_pdfs=(
            settings.runtime_storage_prune_extracted_report_pdfs
        ),
        extracted_report_pdf_retention_days=(
            settings.runtime_storage_extracted_report_pdf_retention_days
        ),
        prune_rag_repair_artifacts=(
            settings.runtime_storage_prune_rag_repair_artifacts
        ),
        rag_repair_artifact_retention_days=(
            settings.runtime_storage_rag_repair_artifact_retention_days
        ),
        prune_rag_rebuild_backups=(
            settings.runtime_storage_prune_rag_rebuild_backups
        ),
        rag_rebuild_backup_retention_days=(
            settings.runtime_storage_rag_rebuild_backup_retention_days
        ),
        prune_old_runtime_logs=settings.runtime_storage_prune_old_runtime_logs,
        runtime_log_retention_days=settings.runtime_storage_log_retention_days,
        rotate_large_active_logs=settings.runtime_storage_rotate_large_active_logs,
        active_log_max_mb=settings.runtime_storage_active_log_max_mb,
        active_log_tail_kb=settings.runtime_storage_active_log_tail_kb,
        prune_scratch_artifacts=settings.runtime_storage_prune_scratch_artifacts,
        scratch_artifact_retention_days=(
            settings.runtime_storage_scratch_artifact_retention_days
        ),
        prune_old_backtest_artifacts=(
            settings.runtime_storage_prune_old_backtest_artifacts
        ),
        backtest_artifact_retention_days=(
            settings.runtime_storage_backtest_artifact_retention_days
        ),
        prune_old_ui_check_artifacts=(
            settings.runtime_storage_prune_old_ui_check_artifacts
        ),
        ui_check_artifact_retention_days=(
            settings.runtime_storage_ui_check_artifact_retention_days
        ),
        prune_zero_byte_runtime_markers=(
            settings.runtime_storage_prune_zero_byte_runtime_markers
        ),
        zero_byte_marker_retention_days=(
            settings.runtime_storage_zero_byte_marker_retention_days
        ),
        database_compact_min_free_mb=(
            settings.runtime_storage_database_compact_min_free_mb
        ),
        database_compact_min_free_ratio_pct=(
            settings.runtime_storage_database_compact_min_free_ratio_pct
        ),
        archive_retention_days_by_key={
            "kis_blocks": {
                "quote_snapshots_archive": (
                    settings.kis_block_trader_quote_retention_days
                    + settings.kis_block_trader_archive_retention_days
                ),
                "manager_runs_archive": (
                    settings.kis_block_trader_manager_run_retention_days
                    + settings.kis_block_trader_archive_retention_days
                ),
                "reconciliation_runs_archive": (
                    settings.kis_block_trader_reconciliation_retention_days
                    + settings.kis_block_trader_archive_retention_days
                ),
            },
            "binance_blocks": {
                "quote_snapshots_archive": (
                    settings.binance_block_trader_quote_retention_days
                    + settings.binance_block_trader_archive_retention_days
                ),
                "manager_runs_archive": (
                    settings.binance_block_trader_manager_run_retention_days
                    + settings.binance_block_trader_archive_retention_days
                ),
            },
            "market_judgment": {
                "quote_snapshots_archive": (
                    settings.market_judge_quote_archive_retention_days
                ),
                "judgment_runs_archive": (
                    settings.market_judge_judgment_retention_days
                    + settings.market_judge_judgment_archive_retention_days
                ),
                "symbol_judgments_archive": (
                    settings.market_judge_judgment_retention_days
                    + settings.market_judge_judgment_archive_retention_days
                ),
            },
            "market_pulse": settings.market_pulse_archive_retention_days,
            "crypto_market_research": (
                settings.crypto_market_research_archive_retention_days
            ),
            "crypto_quant": settings.crypto_quant_archive_retention_days,
            "etf_research": settings.etf_research_archive_retention_days,
        },
        operational_db_paths=(
            str(runtime_dir / "crypto_market_research.db"),
            str(runtime_dir / "crypto_quant.db"),
            str(runtime_dir / "crypto_pattern_lab.db"),
            str(runtime_dir / "binance_blocks.db"),
            str(runtime_dir / "kis_blocks.db"),
            str(runtime_dir / "market_judgment.db"),
            str(runtime_dir / "market_pulse.db"),
            str(runtime_dir / "investment_memory.db"),
            str(runtime_dir / "etf_research.db"),
            str(runtime_dir / "strategy_insights.db"),
            str(runtime_dir / "trading_validation.db"),
            str(runtime_dir / "live_performance.db"),
        ),
    )


register_app_routes(
    app,
    build_observability_route_specs(
        ObservabilityRouteGroupDeps(
            require_admin_auth=require_admin_auth,
            evidence_source_statuses={
                "crypto_market_research": lambda: _service_status(
                    crypto_market_research_service
                ),
                "crypto_alpha": lambda: _service_status(crypto_alpha_service),
                "crypto_quant": lambda: _crypto_quant_status(),
                "crypto_pattern_lab": lambda: _crypto_pattern_lab_status(),
                "kr_equity_pattern_lab": lambda: _kr_equity_pattern_lab_status(),
            },
            evidence_memory_repository=lambda: _investment_memory_repository(),
            telegram_status=lambda: telegram.status(),
            telegram_validate_webhook_secret=_validate_telegram_webhook_secret,
            telegram_process_text=(
                lambda text, chat_id: _process_telegram_text(text, chat_id)
            ),
            runtime_storage_policy=_runtime_storage_policy,
            build_runtime_storage_report=build_runtime_storage_report,
            cleanup_runtime_storage=cleanup_runtime_storage,
        )
    ),
)


def _configured_etf_universe() -> list[ETFUniverseItem]:
    return expand_default_etf_universe(
        parse_etf_universe_config(settings.etf_research_universe)
    )


def _symbol_directory_etf_rows(*, limit: int = 200) -> list[dict[str, Any]]:
    provider = getattr(naver_report_repository, "list_etf_symbol_directory", None)
    if callable(provider):
        try:
            rows = provider(limit=max(int(limit), 1))
            return [row for row in rows if isinstance(row, dict)]
        except Exception as exc:
            logger.warning("ETF symbol-directory provider failed: %s", exc)
            return []

    connect = getattr(naver_report_repository, "_connect", None)
    if not callable(connect):
        return []

    prefixes = (
        "KODEX",
        "TIGER",
        "ACE",
        "RISE",
        "SOL",
        "PLUS",
        "KBSTAR",
        "HANARO",
        "ARIRANG",
        "TIMEFOLIO",
    )
    try:
        with connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(symbol_directory)").fetchall()
            }
            required = {"symbol", "company_name"}
            if not required.issubset(columns):
                return []

            select_columns = ["symbol", "company_name"]
            if "market" in columns:
                select_columns.append("market")
            else:
                select_columns.append("'' AS market")

            where = [
                "TRIM(COALESCE(symbol, '')) <> ''",
                "TRIM(COALESCE(company_name, '')) <> ''",
            ]
            params: list[Any] = []
            prefix_clauses = []
            for prefix in prefixes:
                prefix_clauses.append("UPPER(company_name) LIKE ?")
                params.append(f"{prefix}%")
            etf_clause = " OR ".join(prefix_clauses)
            if "market" in columns:
                where.append(
                    "(UPPER(COALESCE(market, '')) IN ('ETF', 'ETN') OR "
                    f"{etf_clause})"
                )
            else:
                where.append(f"({etf_clause})")
            if "status" in columns:
                where.append(
                    """
                    LOWER(COALESCE(NULLIF(status, ''), 'active')) NOT IN (
                        'halted', 'managed', 'delisted', 'suspended',
                        'inactive', 'deleted'
                    )
                    """
                )
            query = f"""
                SELECT {', '.join(select_columns)}
                FROM symbol_directory
                WHERE {' AND '.join(where)}
                ORDER BY symbol ASC
                LIMIT ?
            """
            params.append(max(int(limit), 1))
            rows = conn.execute(query, tuple(params)).fetchall()
    except Exception as exc:
        logger.warning("ETF symbol-directory lookup failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "symbol": str(row["symbol"] or "").strip(),
                "company_name": str(row["company_name"] or "").strip(),
                "market": str(row["market"] or "").strip(),
            }
        )
    return out


def _expanded_etf_universe(
    configured: list[ETFUniverseItem] | None = None,
) -> list[ETFUniverseItem]:
    return merge_etf_universe(
        configured=configured if configured is not None else _configured_etf_universe(),
        symbol_directory_rows=_symbol_directory_etf_rows(limit=200),
        limit=200,
    )


def _discover_naver_etf_universe(limit: int = 200) -> list[ETFUniverseItem]:
    return fetch_naver_etf_universe(limit=limit, timeout_sec=8.0)


def _merge_etf_items(
    *groups: list[ETFUniverseItem],
    limit: int = 200,
) -> list[ETFUniverseItem]:
    return build_merge_etf_items_payload(*groups, limit=limit)


def _etf_research_repository() -> ETFResearchRepository:
    return ETFResearchRepository(settings.etf_research_db_path)


def _etf_universe_item_payload(item: ETFUniverseItem) -> dict[str, Any]:
    return build_etf_universe_item_payload(item)


def _seed_naver_report_etf_directory(items: list[ETFUniverseItem]) -> int:
    if not items:
        return 0
    return naver_report_repository.seed_symbol_directory(
        [
            {
                "symbol": item.symbol,
                "name": item.name,
                "market": "ETF",
                "source": "naver_etf"
                if item.category == "naver_etf" or "naver_etf" in item.tags
                else "configured_etf",
            }
            for item in items
        ]
    )


def _seed_etf_research_universe(
    repository: ETFResearchRepository,
) -> list[ETFUniverseItem]:
    configured = _configured_etf_universe()
    universe = _expanded_etf_universe(configured)
    discovered: list[ETFUniverseItem] = []
    if max(int(getattr(settings, "etf_research_max_symbols", 0)), 0) > len(configured):
        try:
            discovered = _discover_naver_etf_universe(limit=200)
        except Exception as exc:
            logger.warning("Naver ETF universe discovery failed: %s", exc)
    universe = _merge_etf_items(universe, discovered, limit=200)
    if universe:
        repository.upsert_universe(universe)
        _seed_naver_report_etf_directory(universe)
    return universe


def _etf_research_candidates(
    repository: ETFResearchRepository,
    configured: list[ETFUniverseItem],
) -> list[dict[str, Any]]:
    return build_etf_research_candidates_payload_from_deps(
        repository=repository,
        configured=configured,
        universe_item_payload=_etf_universe_item_payload,
    )


def _etf_symbols_from_payload(
    payload: dict[str, Any] | None,
    configured: list[ETFUniverseItem],
) -> list[str]:
    return build_etf_symbols_from_payload(
        payload,
        configured,
        max_symbols=settings.etf_research_max_symbols,
    )


def _etf_auto_collect_skipped(reason: str) -> dict[str, Any]:
    return build_etf_auto_collect_skipped(reason)


def _etf_read_only_auto_collect() -> dict[str, Any]:
    return build_etf_read_only_auto_collect()


async def _maybe_auto_collect_etf_research(
    repository: ETFResearchRepository,
    configured: list[ETFUniverseItem],
    *,
    trigger: str,
) -> dict[str, Any]:
    global _ETF_RESEARCH_AUTO_COLLECT_LAST_ATTEMPT_AT
    if not bool(getattr(settings, "etf_research_auto_collect", True)):
        return _etf_auto_collect_skipped("disabled")
    if not configured:
        return _etf_auto_collect_skipped("empty_universe")
    max_symbols = max(int(getattr(settings, "etf_research_max_symbols", 0)), 0)
    if max_symbols <= 0:
        return _etf_auto_collect_skipped("max_symbols_zero")
    symbols = stale_etf_symbols(
        repository,
        configured,
        stale_sec=int(getattr(settings, "etf_research_stale_sec", 1800)),
        max_symbols=max_symbols,
        rotation_key=datetime.now(timezone.utc).date().isoformat(),
    )
    if not symbols:
        return _etf_auto_collect_skipped("fresh")
    now = datetime.now(timezone.utc)
    min_interval = max(
        int(getattr(settings, "etf_research_auto_min_interval_sec", 300)),
        0,
    )
    if (
        _ETF_RESEARCH_AUTO_COLLECT_LAST_ATTEMPT_AT is not None
        and (
            now - _ETF_RESEARCH_AUTO_COLLECT_LAST_ATTEMPT_AT
        ).total_seconds()
        < min_interval
    ):
        return {
            **_etf_auto_collect_skipped("throttled"),
            "requested": symbols,
        }
    _ETF_RESEARCH_AUTO_COLLECT_LAST_ATTEMPT_AT = now
    result = await collect_etf_research_snapshots(
        repository=repository,
        configured=configured,
        fetch_quote=kis_primary.fetch_domestic_quote,
        symbols=symbols,
        force=False,
        retention_days=int(getattr(settings, "etf_research_retention_days", 7)),
        archive_retention_days=int(
            getattr(settings, "etf_research_archive_retention_days", 14)
        ),
    )
    return {**result, "auto": True, "trigger": trigger}


register_app_routes(
    app,
    build_research_route_specs(
        ResearchRouteGroupDeps(
            require_admin_auth=require_admin_auth,
            etf_repository_factory=lambda: _etf_research_repository(),
            etf_configured_universe=lambda: _configured_etf_universe(),
            etf_expanded_universe=lambda configured: _expanded_etf_universe(
                configured
            ),
            etf_universe_item_payload=lambda item: _etf_universe_item_payload(item),
            etf_settings_payload=lambda: {
                "db_path": settings.etf_research_db_path,
                "max_symbols": settings.etf_research_max_symbols,
            },
            etf_list_candidates=lambda repository, universe: _etf_research_candidates(
                repository,
                universe,
            ),
            etf_read_only_auto_collect=lambda: _etf_read_only_auto_collect(),
            etf_seed_universe=lambda repository: _seed_etf_research_universe(
                repository
            ),
            etf_symbols_from_payload=lambda payload, universe: _etf_symbols_from_payload(
                payload,
                universe,
            ),
            etf_collect_snapshots=collect_etf_research_snapshots,
            etf_fetch_quote=lambda symbol: kis_primary.fetch_domestic_quote(symbol),
            crypto_research_service=lambda: crypto_market_research_service,
            crypto_alpha_service=lambda: crypto_alpha_service,
            crypto_research_symbols=_crypto_research_symbols,
            default_crypto_research_symbols=_default_crypto_research_symbols,
            research_helper_ask=_helper_ask_impl,
            research_settings=settings,
            naver_report_repository=lambda: naver_report_repository,
            naver_report_crawler=lambda: naver_report_crawler,
            rag_store=lambda: rag_store,
            symbol_fundamentals_service=lambda: symbol_fundamentals_service,
            build_report_intelligence_status=build_report_intelligence_status,
            run_report_collection_cycle=run_report_collection_cycle,
            sync_report_rag=lambda **kwargs: sync_report_rag(**kwargs),
            seed_symbol_directory=lambda: _seed_naver_report_etf_directory(
                _configured_etf_universe()
            ),
            on_rag_resolve_error=lambda exc: logger.warning(
                "rag symbol auto-resolve failed: %s",
                exc,
            ),
        )
    ),
)


register_app_routes(
    app,
    build_trading_route_specs(
        TradingRouteGroupDeps(
            require_admin_auth=require_admin_auth,
            kis_rebalance_status=lambda: _build_kis_rebalance_status_payload(),
            kis_primary_ready=lambda: bool(settings.kis_primary_ready),
            kis_blocks_status=lambda: kis_block_trader.status(),
            kis_blocks_snapshot=lambda: kis_block_trader.snapshot(),
            kis_blocks_snapshot_compact=lambda: kis_block_trader.snapshot_compact(
                refresh_live=False
            ),
            attach_kis_block_memory=_attach_block_memory,
            kis_validation_repair_ops_summary=(
                lambda target_scope, limit: investment_memory_service.validation_repair_ops_summary(
                    target_scope=target_scope,
                    limit=limit,
                )
            ),
            ops_readiness=lambda: _build_ops_readiness_cached(),
            kis_blocks_status_readiness=lambda: _build_kis_blocks_status_readiness(),
            kis_manager_run_once=lambda: kis_block_trader.run_manager_once(),
            kis_adoption_run_once=lambda: kis_block_trader.run_adoption_once(),
            kis_executor_tick=lambda manual=False: kis_block_trader.executor_tick(
                manual=manual
            ),
            kis_set_kill_switch=(
                lambda enabled, reason: kis_block_trader.set_kill_switch(
                    bool(enabled),
                    reason=reason,
                )
            ),
            kis_cancel_order=lambda order_id, reason: kis_block_trader.cancel_order(
                order_id,
                reason=reason,
            ),
            kis_block_detail=lambda block_id: kis_block_trader.block_detail(block_id),
            kis_block_memory=lambda block_id: investment_memory_service.block_memory(
                block_id
            ),
            kis_add_user_directive=(
                lambda block_id, message, preferred_horizon, scope, source: kis_block_trader.add_user_directive(
                    block_id,
                    message=message,
                    preferred_horizon=preferred_horizon,
                    scope=scope,
                    source=source,
                )
            ),
            kis_pause_block=lambda block_id, reason: kis_block_trader.pause_block(
                block_id,
                reason=reason,
            ),
            kis_resume_block=lambda block_id, reason: kis_block_trader.resume_block(
                block_id,
                reason=reason,
            ),
            kis_close_block=lambda block_id, reason: kis_block_trader.close_block(
                block_id,
                reason=reason,
            ),
            binance_trader=binance_block_trader,
            binance_memory_service=investment_memory_service,
            build_binance_readiness=(
                lambda payload: build_binance_block_readiness_payload(
                    status_payload=payload,
                    runner=_runner_status_with_cover(
                        _runner_process_status_light("binance_block_trader")
                    ),
                    enabled=bool(settings.binance_block_trader_enabled),
                    spot_live=bool(settings.binance_block_trader_execute_spot_orders),
                    futures_live=bool(
                        settings.binance_block_trader_execute_futures_orders
                    ),
                    upbit_live=bool(settings.binance_block_trader_execute_upbit_orders),
                    model=settings.binance_block_trader_llm_model,
                    reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
                    account_risk_pct=settings.binance_block_trader_account_risk_pct,
                    max_total_exposure_usdt=(
                        settings.binance_block_trader_max_total_exposure_usdt
                    ),
                    max_symbol_exposure_pct=(
                        settings.binance_block_trader_max_symbol_exposure_pct
                    ),
                    min_reward_risk=settings.binance_block_trader_min_reward_risk,
                    manager_interval_sec=(
                        settings.binance_block_trader_manager_interval_sec
                    ),
                    next_from_latest=_next_from_latest,
                )
            ),
            binance_quant_repository_factory=lambda: CryptoQuantRepository(
                settings.crypto_quant_db_path
            ),
            binance_pattern_repository_cls=CryptoPatternLabRepository,
            binance_pattern_db_path=lambda: str(
                _setting(
                    "crypto_pattern_lab_db_path",
                    ".runtime/crypto_pattern_lab.db",
                )
            ),
            binance_pattern_import_error=_crypto_pattern_lab_import_error,
            portfolio_list_advice_messages=lambda **kwargs: (
                portfolio_coach_store.list_advice_messages(**kwargs)
            ),
            portfolio_get_advice_message=lambda message_id: (
                portfolio_coach_store.get_advice_message(message_id)
            ),
            portfolio_update_message_status=lambda **kwargs: (
                portfolio_coach_store.update_message_status(**kwargs)
            ),
            portfolio_send_message=lambda message: telegram.send_message(message),
        ),
    ),
)


def run() -> None:
    import uvicorn

    write_current_runner_pid("control")
    try:
        uvicorn.run(
            "tradecraft.main:app",
            host=settings.host,
            port=settings.port,
            reload=False,
        )
    finally:
        clear_current_runner_pid("control")
