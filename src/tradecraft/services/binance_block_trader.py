from __future__ import annotations

import inspect
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable

from tradecraft.services.binance_entry_gate import (
    entry_fill_price_update_fields as build_entry_fill_price_update_fields,
    entry_reference_inside_tolerance as build_entry_reference_inside_tolerance,
    entry_tolerance_price as build_entry_tolerance_price,
    entry_quality_gate_check as build_entry_quality_gate_check,
    entry_quality_label_from_payload as build_entry_quality_label_from_payload,
    entry_trigger_fired as build_entry_trigger_fired,
    is_waiting_entry_block as build_is_waiting_entry_block,
    normalize_entry_trigger_operator as build_normalize_entry_trigger_operator,
    normalize_entry_quality_label as build_normalize_entry_quality_label,
    shadow_only_entry_qualities as build_shadow_only_entry_qualities,
    volatile_attack_context as build_volatile_attack_context,
    waiting_entry_metadata as build_waiting_entry_metadata,
    wait_pullback_confirmation_rejection as build_wait_pullback_confirmation_rejection,
)
from tradecraft.services.binance_live_authority_gate import (
    LIVE_AUTHORITY_VALIDATION_WAIT_ONLY_STATUSES,
    active_revision_budget_multiplier as build_active_revision_budget_multiplier,
    active_revision_waiting_entry_reason as build_active_revision_waiting_entry_reason,
    lane_authority_evidence_fields as build_lane_authority_evidence_fields,
    live_authority_create_gate as build_live_authority_create_gate,
    live_authority_validation_gate as build_live_authority_validation_gate,
)
from tradecraft.services.binance_exit_gate import (
    binance_exit_order_side as build_binance_exit_order_side,
    binance_exit_reason as build_binance_exit_reason,
    entry_quality_loss_tighten_plan as build_entry_quality_loss_tighten_plan,
    exit_fill_update_plan as build_exit_fill_update_plan,
    exit_quantity_unavailable_plan as build_exit_quantity_unavailable_plan,
    exit_quantity_unavailable_result_plan as build_exit_quantity_unavailable_result_plan,
    exit_reconciliation_error_plan as build_exit_reconciliation_error_plan,
    exit_success_plan as build_exit_success_plan,
    favorable_r_multiple as build_favorable_r_multiple,
    partial_profit_block_update_plan as build_partial_profit_block_update_plan,
    partial_profit_quantity_unavailable_plan as build_partial_profit_quantity_unavailable_plan,
    partial_profit_quantity_plan as build_partial_profit_quantity_plan,
    partial_profit_success_plan as build_partial_profit_success_plan,
    partial_profit_trigger_plan as build_partial_profit_trigger_plan,
    partial_profit_unfilled_plan as build_partial_profit_unfilled_plan,
    profit_lock_stop_plan as build_profit_lock_stop_plan,
    profit_lock_stop_price as build_profit_lock_stop_price,
    remaining_exit_qty as build_remaining_exit_qty,
    spot_exit_retry_update_plan as build_spot_exit_retry_update_plan,
)
from tradecraft.services.binance_executor import (
    entry_not_filled_reason as build_entry_not_filled_reason,
    entry_order_side as build_entry_order_side,
    exit_order_execution as build_exit_order_execution,
    filled_order_price as build_filled_order_price,
    partial_profit_exit_order_execution as build_partial_profit_exit_order_execution,
    response_filled_qty as build_response_filled_qty,
    response_order_id as build_response_order_id,
)
from tradecraft.services.binance_execution_defects import (
    execution_defect_risk_from_rows as build_execution_defect_risk_from_rows,
    partition_performance_rows as build_partition_performance_rows,
)
from tradecraft.services.binance_lane import (
    BINANCE_HORIZON_COLORS,
    BINANCE_MANAGER_LANES,
    binance_market_side_lane,
    binance_performance_lane_from_payload,
    canonical_binance_performance_lane,
    normalize_binance_display_lane,
    normalize_binance_horizon,
    parse_universe,
)
from tradecraft.services.binance_manager_actions import (
    empty_manager_action_results as build_empty_manager_action_results,
    manager_close_has_adverse_evidence as build_manager_close_has_adverse_evidence,
    manager_closed_fields as build_manager_closed_fields,
    manager_exit_request_fields as build_manager_exit_request_fields,
    manager_block_action_result as build_manager_block_action_result,
    manager_create_block_metadata as build_manager_create_block_metadata,
    manager_created_block_result as build_manager_created_block_result,
    manager_create_policy_repair_rejection as build_manager_create_policy_repair_rejection,
    manager_growth_governor_create_rejection as build_manager_growth_governor_create_rejection,
    manager_market_horizon_conflict as build_manager_market_horizon_conflict,
    manager_pause_fields as build_manager_pause_fields,
    manager_update_fields as build_manager_update_fields,
    rejected_manager_action as build_rejected_manager_action,
    validation_repair_metadata_update as build_validation_repair_metadata_update,
)
from tradecraft.services.binance_ledger import (
    build_lane_allocation_summary,
    ledger_json_dumps as _json_dumps,
    ledger_json_loads as _json_loads,
    parse_iso_datetime as _parse_iso_datetime,
    row_order_payload as build_row_order_payload,
    row_to_block as build_row_to_block,
    row_to_event as build_row_to_event,
    row_to_manager_run as build_row_to_manager_run,
    row_to_order as build_row_to_order,
    row_to_performance_reflection as build_row_to_performance_reflection,
    safe_float as _safe_float,
    safe_int as _safe_int,
    utc_now_iso,
)
from tradecraft.services.binance_manager_prompt import (
    BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS as BUILD_BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS,
    apply_manager_latency_guard as build_apply_manager_latency_guard,
    build_binance_manager_prompt_payload,
    compact_manager_account_for_prompt as build_compact_manager_account_for_prompt,
    compact_manager_candidate_for_prompt as build_compact_manager_candidate_for_prompt,
    compact_validation_repair_prompt as build_compact_validation_repair_prompt,
    compact_manager_live_authority_for_prompt as build_compact_manager_live_authority_for_prompt,
    compact_manager_prompt_context as build_compact_manager_prompt_context,
    compact_manager_response_payload as build_compact_manager_response_payload,
    compact_manager_storage_payload as build_compact_manager_storage_payload,
    compact_prompt_value as build_compact_prompt_value,
    compact_prompt_value_bounded as build_compact_prompt_value_bounded,
    compact_validation_repair_for_storage as build_compact_validation_repair_for_storage,
    finalize_prompt_budget as build_finalize_prompt_budget,
    format_prompt_budget_alert_message as build_format_prompt_budget_alert_message,
    manager_action_candidate_keys as build_manager_action_candidate_keys,
    manager_latency_guard_from_runs as build_manager_latency_guard_from_runs,
    manager_response_contract_error as build_manager_response_contract_error,
    manager_run_diagnostics as build_manager_run_diagnostics,
    manager_run_workflow_provenance as build_manager_run_workflow_provenance,
    merge_manager_candidate_price_plan as build_merge_manager_candidate_price_plan,
    normalize_lane_review as build_normalize_lane_review,
    prompt_budget_error as build_prompt_budget_error,
    validation_evidence_plan_from_repair as build_validation_evidence_plan_from_repair,
    validation_repair_action_metadata as build_validation_repair_action_metadata,
    validation_repair_note as build_validation_repair_note,
)
from tradecraft.services.manager_prompt_budget import attach_jue_wiki_budget_report
from tradecraft.services.jue_wiki import normalize_jue_wiki_quality_status
from tradecraft.services.jue_wiki_application import (
    build_jue_wiki_quality_pressure_action_plan_for_prompt,
    summarize_jue_wiki_quality_pressure_for_prompt,
)
from tradecraft.services.jue_wiki_prompt_quality import (
    canonical_jue_wiki_evidence_quality,
    jue_wiki_quality_status_from_evidence,
)
from tradecraft.services.jue_wiki_selector import (
    build_jue_wiki_decision_adjustment_audit_contract_for_prompt,
    build_jue_wiki_decision_adjustments_for_prompt,
    build_jue_wiki_repair_contract_for_prompt,
    build_jue_wiki_trust_profile_for_prompt,
    build_jue_wiki_validation_repair_contract_for_prompt,
    compact_jue_wiki_application_coverage_for_prompt,
    compact_jue_wiki_repair_loop_effectiveness_for_prompt,
    compact_jue_wiki_validation_repair_effectiveness_for_prompt,
)
from tradecraft.services.binance_growth_governor import (
    growth_governor_row_lanes as build_growth_governor_row_lanes,
)
from tradecraft.services.binance_manager_candidates import (
    BinanceManagerCandidateFinalizeHooks,
    BinanceProvidedCandidateBuildHooks,
    BinanceResearchCandidateBuildHooks,
    build_provided_manager_candidates,
    build_research_manager_candidates,
    candidate_derivatives_available as build_candidate_derivatives_available,
    candidate_identity as build_candidate_identity,
    candidate_execution_blocker_context as build_candidate_execution_blocker_context,
    candidate_is_explicit_long_candidate as build_candidate_is_explicit_long_candidate,
    crypto_research_feature_index as build_crypto_research_feature_index,
    crypto_research_market_feature_index as build_crypto_research_market_feature_index,
    diversify_manager_candidates_by_lane as build_diversify_manager_candidates_by_lane,
    finalize_manager_candidates as build_finalize_manager_candidates,
    manager_candidate_packets as build_manager_candidate_packets,
    market_from_crypto_research_candidate as build_market_from_crypto_research_candidate,
    side_from_crypto_research_candidate as build_side_from_crypto_research_candidate,
)
from tradecraft.services.binance_manager_lane_context import (
    candidate_lane_authority_context as build_candidate_lane_authority_context,
    candidate_near_duplicate_active_block_context as build_candidate_near_duplicate_active_block_context,
    lane_authority_key_variants as build_lane_authority_key_variants,
    lane_distribution as build_binance_lane_distribution,
    manager_lane_balance_context as build_manager_lane_balance_context,
    near_duplicate_active_blocks_context as build_near_duplicate_active_blocks_context,
    prices_within_bps as build_prices_within_bps,
    row_pattern_live_crosscheck_status as build_row_pattern_live_crosscheck_status,
    row_price_value as build_row_price_value,
)
from tradecraft.services.binance_manager_contract import (
    apply_manager_contract_aliases as build_apply_manager_contract_aliases,
    clean_string_list as build_clean_string_list,
    infer_manager_contract_side as build_infer_manager_contract_side,
    manager_contract_candidate_matches_source_id as build_manager_contract_candidate_matches_source_id,
    manager_contract_candidate_price_match_score as build_manager_contract_candidate_price_match_score,
    normalize_manager_hold_decision as build_normalize_manager_hold_decision,
    raise_for_manager_llm_error_payload as build_raise_for_manager_llm_error_payload,
    validate_manager_actions as build_validate_manager_actions,
)
from tradecraft.services.binance_order_math import (
    candidate_last_price,
    min_notional_from_filters as build_min_notional_from_filters,
    normalize_order_for_filters as build_normalize_order_for_filters,
    round_candidate_price,
)
from tradecraft.services.binance_performance_cost import (
    FEE_CONVERSION_QUOTE_SYMBOLS,
    QUOTE_ASSET_SUFFIXES,
    asset_amount_to_usdt as build_asset_amount_to_usdt,
    first_float as build_first_float,
    iter_payload_dicts as build_iter_payload_dicts,
    symbol_base_quote as build_symbol_base_quote,
)
from tradecraft.services.binance_performance_scorecard import (
    performance_group_scorecards as build_performance_group_scorecards,
    performance_pattern_scorecards as build_performance_pattern_scorecards,
    performance_scorecard_from_reflections as build_performance_scorecard_from_reflections,
)
from tradecraft.services.binance_performance_policy import (
    budget_scope_from_scorecard_rows as build_budget_scope_from_scorecard_rows,
    candidate_quote_budget_usdt as build_candidate_quote_budget_usdt,
    candidate_pattern_performance_scorecard as build_candidate_pattern_performance_scorecard,
    candidate_upbit_quote_budget_details as build_candidate_upbit_quote_budget_details,
    lane_card_is_distressed as build_lane_card_is_distressed,
    manager_candidate_empirical_edge_score as build_manager_candidate_empirical_edge_score,
    manager_candidate_execution_blocker_penalty as build_manager_candidate_execution_blocker_penalty,
    manager_candidate_freshness_penalty as build_manager_candidate_freshness_penalty,
    manager_candidate_lane_authority_bonus as build_manager_candidate_lane_authority_bonus,
    manager_candidate_near_duplicate_penalty as build_manager_candidate_near_duplicate_penalty,
    manager_candidate_pattern_performance_bonus as build_manager_candidate_pattern_performance_bonus,
    manager_candidate_pattern_performance_penalty as build_manager_candidate_pattern_performance_penalty,
    manager_candidate_performance_budget_penalty as build_manager_candidate_performance_budget_penalty,
    manager_candidate_performance_cooldown_penalty as build_manager_candidate_performance_cooldown_penalty,
    performance_scope_for_budget as build_performance_scope_for_budget,
    rank_manager_candidates_by_edge as build_rank_manager_candidates_by_edge,
    quote_budget_details_from_amount as build_quote_budget_details_from_amount,
    scorecard_allows_budget_scale as build_scorecard_allows_budget_scale,
    weak_lane_profit_protection_trigger as build_weak_lane_profit_protection_trigger,
)
from tradecraft.services.binance_policy_effects import (
    POLICY_ENTRY_TRIGGER_PCT_KEYS,
    VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR,
    VALIDATION_REPAIR_MIN_USDT_NOTIONAL_FLOOR,
    apply_policy_relative_price_effects as build_apply_policy_relative_price_effects,
    crypto_reward_risk as build_crypto_reward_risk,
    effective_max_stop_risk_pct_for_row as build_effective_max_stop_risk_pct_for_row,
    policy_effect_derived_trigger_price as build_policy_effect_derived_trigger_price,
    policy_impacts_for_row as build_policy_impacts_for_row,
    policy_effect_qty_adjusted as build_policy_effect_qty_adjusted,
    policy_effect_trigger_price as build_policy_effect_trigger_price,
    policy_effect_waiting_required as build_policy_effect_waiting_required,
    policy_effects as build_policy_effects,
    policy_reference_entry_price as build_policy_reference_entry_price,
    policy_target_stop_quality_gate as build_policy_target_stop_quality_gate,
    reward_risk_meets_minimum as build_reward_risk_meets_minimum,
    truthy_gate_value as build_truthy_gate_value,
    validation_repair_notional_floor as build_validation_repair_notional_floor,
)
from tradecraft.services.binance_price_plan import (
    design_crypto_candidate_price_plan as build_design_crypto_candidate_price_plan,
)
from tradecraft.services.binance_reconciliation import (
    ALLOCATION_STATUSES as BINANCE_ALLOCATION_STATUSES,
    allocated_qty_by_symbol as build_allocated_qty_by_symbol,
    position_assets_for_market as build_position_assets_for_market,
    spot_position_assets as build_spot_position_assets,
    upbit_position_assets as build_upbit_position_assets,
)
from tradecraft.services.binance_retention import (
    build_binance_operational_retention_rules,
    summarize_retention_result,
)
from tradecraft.services.binance_risk import (
    LANE_RISK_MULTIPLIERS,
    block_notional_usdt as build_block_notional_usdt,
    cash_reference_usdt as build_cash_reference_usdt,
    current_symbol_exposure_usdt as build_current_symbol_exposure_usdt,
    current_total_exposure_usdt as build_current_total_exposure_usdt,
)
from tradecraft.services.binance_snapshot import (
    actionable_error_block_rows as build_actionable_error_block_rows,
    attach_performance_reflections as build_attach_performance_reflections,
    block_history_rows as build_block_history_rows,
    compact_history_block_rows as build_compact_history_block_rows,
    compact_snapshot_manager_run as build_compact_snapshot_manager_run,
    enrich_blocks_with_latest_quotes as build_enrich_blocks_with_latest_quotes,
    inactive_error_block_rows as build_inactive_error_block_rows,
    manager_run_with_decision_context as build_manager_run_with_decision_context,
    normalize_account_snapshot as build_normalize_account_snapshot,
    visible_block_rows as build_visible_block_rows,
)
from tradecraft.services.binance_symbol import (
    ALLOWED_MARKETS as BINANCE_ALLOWED_MARKETS,
    UPBIT_SPOT_MARKET,
    explicit_market_scope,
    is_upbit_market,
    normalize_market,
    normalize_position_side,
    upbit_market_symbol,
    upbit_market_to_usdt_symbol,
)
from tradecraft.services.crypto_alpha_score import score_crypto_candidate
from tradecraft.services.crypto_growth_target import CryptoGrowthTargetLedger
from tradecraft.services.db_retention import SQLiteRetentionPruner
from tradecraft.services.evidence_policy import (
    EvidenceItem,
    build_decision_packet as build_evidence_decision_packet,
)
from tradecraft.services.jue_decision_packet import (
    build_decision_packet as build_jue_decision_packet,
)
from tradecraft.services.jue_language_policy import jue_language_policy
from tradecraft.services.jue_skill_registry import JueSkillRegistry, JueSkillValidationError
from tradecraft.services.live_authority import (
    compact_live_authority_for_prompt,
    compact_live_authority_for_status,
)
from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
)

logger = logging.getLogger(__name__)

BLOCK_STATUSES = {
    "proposed",
    "entry_pending",
    "open",
    "exit_pending",
    "closed",
    "paused",
    "error",
}
ACTIVE_BLOCK_STATUSES = {"entry_pending", "open", "exit_pending"}
VISIBLE_BLOCK_STATUSES = {"proposed", "entry_pending", "open", "exit_pending", "paused", "error"}
ALLOWED_MARKETS = BINANCE_ALLOWED_MARKETS
ALLOWED_MANAGER_ACTIONS = {
    "adopt_existing_blocks",
    "create_blocks",
    "update_blocks",
    "close_blocks",
    "pause_blocks",
}
SPOT_ADOPTION_MIN_NOTIONAL_USDT = 5.0
BOOK_MARKET_FEATURES_KEY = "_book_features_by_market"
BOOK_FIELD_KEYS = {
    "bid_price",
    "bid",
    "ask_price",
    "ask",
    "spread_bps",
    "book_source",
    "book_fetched_at",
    "book_market",
    "book_fresh",
}
WAITING_ENTRY_PREFLIGHT_MAX_SPREAD_BPS = 35.0
UPBIT_PRICE_SCALE_MIN_RATIO = 0.05
UPBIT_PRICE_SCALE_MAX_RATIO = 20.0
MANAGER_COST_EDGE_REQUIRED_MULTIPLE = 3.0
FEE_CONVERSION_QUOTE_LOOKBACK_DAYS = 7
MANAGER_COST_EDGE_MIN_TARGET_MOVE_PCT = {
    "spot": 0.45,
    UPBIT_SPOT_MARKET: 0.45,
    "futures": 0.35,
}
MANAGER_COST_EDGE_BASE_ROUND_TRIP_PCT = {
    "spot": 0.20,
    UPBIT_SPOT_MARKET: 0.20,
    "futures": 0.14,
}
CRYPTO_MARKET_PULSE_MAJORS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
)
LANE_CONCENTRATION_MIN_SAMPLE = 8
LANE_CONCENTRATION_SHARE_PCT = 70.0
KST = timezone(timedelta(hours=9))
DEFAULT_UPBIT_USDT_KRW_RATE = 1387.0
RECENT_RECOVERY_LANE_AUTHORITY_BUDGET_FLOOR = 0.25
RECENT_RECOVERY_RISK_MULTIPLIER = 0.75


@dataclass
class BinanceBlockTraderConfig:
    db_path: str = ".runtime/binance_blocks.db"
    state_path: str = ".runtime/binance_block_trader.json"
    live_performance_db_path: str = ".runtime/live_performance.db"
    enabled: bool = False
    execute_spot_orders: bool = False
    execute_futures_orders: bool = False
    execute_upbit_orders: bool = False
    quote_interval_sec: int = 15
    rule_interval_sec: int = 15
    manager_interval_sec: int = 1800
    llm_model: str = "gpt-5.5"
    llm_reasoning_effort: str = "xhigh"
    llm_timeout_ms: int = 0
    max_manager_symbols: int = 36
    quant_context_limit: int = 18
    prompt_target_chars: int = 45_000
    prompt_warn_chars: int = 65_000
    prompt_max_chars: int = 190_000
    jue_wiki_context_max_chars: int = 18_000
    telegram_alerts_enabled: bool = True
    spot_universe: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
    futures_universe: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    upbit_universe: str = "KRW-BTC,KRW-ETH,KRW-SOL,KRW-XRP,KRW-DOGE"
    max_futures_leverage: int = 2
    min_liquidation_distance_pct: float = 12.0
    aggressive_limit_bps: float = 20.0
    failed_exit_retry_cooldown_sec: int = 60
    min_entry_confidence: float = 0.58
    min_entry_expected_r: float = 0.55
    min_entry_directional_score: float = 62.0
    min_candidate_stop_pct: float = 1.2
    profit_lock_trigger_r: float = 1.2
    weak_lane_profit_lock_trigger_r: float = 0.8
    distressed_lane_profit_lock_trigger_r: float = 0.55
    entry_quality_loss_tighten_trigger_r: float = 0.5
    distressed_lane_min_samples: int = 5
    distressed_lane_max_win_rate_pct: float = 20.0
    distressed_lane_max_profit_factor: float = 0.5
    weak_lane_partial_profit_fraction: float = 0.5
    distressed_entry_quality_partial_profit_fraction: float = 0.75
    profit_lock_stop_r: float = 0.25
    profit_lock_min_net_buffer_pct: float = 0.12
    spot_quote_budget_pct: float = 5.0
    spot_min_quote_budget_usdt: float = 50.0
    spot_max_quote_budget_usdt: float = 300.0
    upbit_quote_budget_pct: float = 5.0
    upbit_min_quote_budget_krw: float = 10_000.0
    upbit_max_quote_budget_krw: float = 150_000.0
    upbit_usdt_krw_rate: float = DEFAULT_UPBIT_USDT_KRW_RATE
    futures_quote_budget_pct: float = 10.0
    futures_min_quote_budget_usdt: float = 25.0
    futures_max_quote_budget_usdt: float = 150.0
    volatile_attack_enabled: bool = True
    volatile_attack_candidate_limit: int = 12
    volatile_attack_budget_multiplier: float = 0.35
    volatile_attack_min_change_pct: float = 8.0
    volatile_attack_min_volume_expansion: float = 1.8
    volatile_attack_min_reward_risk: float = 2.0
    volatile_attack_stop_multiplier: float = 1.35
    monthly_target_pct: float = 50.0
    budget_performance_scale_enabled: bool = True
    budget_performance_scale_min_samples: int = 10
    budget_performance_scale_win_rate_pct: float = 55.0
    budget_performance_scale_multiplier: float = 1.5
    performance_scorecard_feedback_limit: int = 120
    lane_performance_scale_enabled: bool = True
    lane_performance_min_samples: int = 3
    lane_performance_loss_multiplier: float = 0.5
    execution_defect_loss_multiplier: float = 0.5
    volatile_attack_max_performance_multiplier: float = 1.15
    daily_loss_stop_pct: float = 7.0
    monthly_loss_stop_pct: float = 20.0
    symbol_lane_cooldown_min_samples: int = 6
    symbol_lane_cooldown_max_win_rate_pct: float = 45.0
    near_duplicate_block_price_tolerance_bps: float = 75.0
    strategy_revision_id: str = "jue_edge_repair_v1"


def _jue_wiki_prompt_mode(jue_wiki: dict[str, Any] | None) -> str:
    if isinstance(jue_wiki, dict):
        mode = str(jue_wiki.get("prompt_mode") or "").strip().lower()
        if mode in {"observe", "assist", "primary"}:
            return mode
    return "assist"


def _sanitize_jue_wiki_observation(payload: dict[str, Any]) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    raw_pages = [
        page for page in list(payload.get("pages") or []) if isinstance(page, dict)
    ]
    for page in payload.get("pages") or []:
        if not isinstance(page, dict):
            continue
        pages.append(
            {
                "page_id": page.get("page_id"),
                "rank": page.get("rank"),
                "score": page.get("score"),
                "selection_reasons": list(page.get("selection_reasons") or []),
                "selection_penalties": list(page.get("selection_penalties") or []),
                "char_count": page.get("char_count"),
                "source_refs": list(page.get("source_refs") or []),
                "effectiveness": page.get("effectiveness")
                if isinstance(page.get("effectiveness"), dict)
                else {},
            }
        )
    rejected_pages = [
        {key: value for key, value in page.items() if key != "content"}
        for page in payload.get("rejected_pages") or []
        if isinstance(page, dict)
    ]
    effectiveness_attention_items = _compact_jue_wiki_effectiveness_attention_items(
        payload.get("effectiveness_attention_items")
    )
    if not effectiveness_attention_items:
        effectiveness_attention_items = _jue_wiki_effectiveness_attention_items_from_rows(
            raw_pages
        )
    return {
        "status": payload.get("status"),
        "selection_run_id": payload.get("selection_run_id"),
        "target_scope": payload.get("target_scope"),
        "prompt_mode": "observe",
        "configured_prompt_mode": payload.get("configured_prompt_mode"),
        "mode_recommendation": payload.get("mode_recommendation")
        if isinstance(payload.get("mode_recommendation"), dict)
        else {},
        "prompt_mode_policy": payload.get("prompt_mode_policy")
        if isinstance(payload.get("prompt_mode_policy"), dict)
        else {},
        "trust_profile_effectiveness": payload.get("trust_profile_effectiveness")
        if isinstance(payload.get("trust_profile_effectiveness"), dict)
        else {},
        "effectiveness_policy": payload.get("effectiveness_policy")
        if isinstance(payload.get("effectiveness_policy"), dict)
        else {},
        "repair_priorities": [
            dict(item)
            for item in list(payload.get("repair_priorities") or [])[:8]
            if isinstance(item, dict)
        ],
        "repair_action_batches": _compact_jue_wiki_repair_action_batches(
            payload.get("repair_action_batches")
        ),
        "repair_queue": _compact_jue_wiki_repair_queue(payload.get("repair_queue")),
        "evidence_quality": _sanitize_jue_wiki_evidence_quality_for_observation(
            payload.get("evidence_quality")
        ),
        "repair_priority_effectiveness": (
            compact_jue_wiki_repair_loop_effectiveness_for_prompt(
                payload.get("repair_priority_effectiveness")
            )
        ),
        "validation_repair_effectiveness": (
            compact_jue_wiki_validation_repair_effectiveness_for_prompt(
                payload.get("validation_repair_effectiveness")
            )
        ),
        "wiki_application_coverage": (
            compact_jue_wiki_application_coverage_for_prompt(
                payload.get("wiki_application_coverage")
            )
        ),
        "effectiveness_attention_items": effectiveness_attention_items,
        "pages": pages,
        "rejected_pages": rejected_pages,
        "budget_report": payload.get("budget_report") or {},
    }

def _compact_jue_wiki_repair_queue(raw_queue: Any) -> dict[str, Any]:
    if not isinstance(raw_queue, dict):
        return {}
    queue: dict[str, Any] = {}
    for key in ("open_count", "resolved_count"):
        count = _safe_int(raw_queue.get(key))
        if count > 0:
            queue[key] = count
    raw_symbols = raw_queue.get("open_symbols")
    if isinstance(raw_symbols, str):
        symbol_values = [raw_symbols]
    elif isinstance(raw_symbols, list):
        symbol_values = raw_symbols
    else:
        symbol_values = []
    open_symbols = [
        _trim_prompt_text(str(symbol).strip().upper(), limit=40)
        for symbol in symbol_values[:64]
        if str(symbol).strip()
    ]
    if open_symbols:
        queue["open_symbols"] = list(dict.fromkeys(open_symbols))
    action_batches = _compact_jue_wiki_repair_action_batches(
        raw_queue.get("open_action_batches")
    )
    if action_batches:
        queue["open_action_batches"] = action_batches
    return {
        key: value
        for key, value in queue.items()
        if value not in (None, "", [], {})
    }


def _compact_jue_wiki_repair_action_batches(
    raw_batches: Any,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    for item in list(raw_batches or [])[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        batch: dict[str, Any] = {}
        scope = str(item.get("scope") or "").strip().lower()
        if scope:
            batch["scope"] = _trim_prompt_text(scope, limit=40)
        action_type = str(item.get("action_type") or "").strip()
        if action_type:
            batch["action_type"] = _trim_prompt_text(action_type, limit=120)
        count = _safe_int(item.get("count"))
        if count > 0:
            batch["count"] = count
        symbols = [
            _trim_prompt_text(str(symbol).strip().upper(), limit=40)
            for symbol in list(item.get("symbols") or [])[:64]
            if str(symbol).strip()
        ]
        if symbols:
            batch["symbols"] = list(dict.fromkeys(symbols))
        warnings = [
            _trim_prompt_text(str(warning).strip(), limit=120)
            for warning in list(item.get("warnings") or [])[:16]
            if str(warning).strip()
        ]
        if warnings:
            batch["warnings"] = list(dict.fromkeys(warnings))
        warning_counts = item.get("warning_counts")
        if isinstance(warning_counts, dict):
            compact_counts = {
                _trim_prompt_text(str(key).strip(), limit=120): _safe_int(value)
                for key, value in warning_counts.items()
                if str(key).strip() and _safe_int(value) > 0
            }
            if compact_counts:
                batch["warning_counts"] = compact_counts
        severity_score = _safe_float(item.get("max_severity_score"))
        if severity_score > 0:
            batch["max_severity_score"] = severity_score
        if batch:
            batches.append(batch)
    return batches


def _sanitize_jue_wiki_evidence_quality_for_observation(value: Any) -> dict[str, Any]:
    quality = canonical_jue_wiki_evidence_quality(value)
    return {
        key: data
        for key, data in quality.items()
        if data not in (None, "", [], {})
        and not str(key).startswith("raw_")
        and str(key) not in {"debug", "raw_debug"}
    }


def _jue_wiki_selection_audit(pages: list[dict[str, Any]]) -> dict[str, Any]:
    if not pages:
        return {}
    reason_counts: dict[str, int] = {}
    penalty_counts: dict[str, int] = {}
    top_pages: list[dict[str, Any]] = []

    def add_count(target: dict[str, int], value: Any) -> None:
        text = _trim_prompt_text(value, limit=120)
        if not text:
            return
        target[text] = target.get(text, 0) + 1

    for page in pages:
        if not isinstance(page, dict):
            continue
        selection_reasons = [
            _trim_prompt_text(item, limit=120)
            for item in list(page.get("selection_reasons") or [])[:6]
            if str(item).strip()
        ]
        selection_penalties = [
            _trim_prompt_text(item, limit=120)
            for item in list(page.get("selection_penalties") or [])[:6]
            if str(item).strip()
        ]
        for reason in selection_reasons:
            add_count(reason_counts, reason)
        for penalty in selection_penalties:
            add_count(penalty_counts, penalty)
        if len(top_pages) >= 8 or not selection_reasons and not selection_penalties:
            continue
        row: dict[str, Any] = {
            "page_id": _trim_prompt_text(page.get("page_id"), limit=180),
        }
        rank = _safe_int(page.get("rank"))
        if rank > 0:
            row["rank"] = rank
        score = _safe_float(page.get("score"))
        if score:
            row["score"] = score
        if selection_reasons:
            row["selection_reasons"] = selection_reasons
        if selection_penalties:
            row["selection_penalties"] = selection_penalties
        row = {key: value for key, value in row.items() if value not in ("", [], {})}
        if row:
            top_pages.append(row)

    if not reason_counts and not penalty_counts and not top_pages:
        return {}
    return {
        key: value
        for key, value in {
            "selected_page_count": len(pages),
            "reason_counts": dict(
                sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            "penalty_counts": dict(
                sorted(penalty_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            "top_pages": top_pages,
        }.items()
        if value not in ({}, [])
    }


def _jue_wiki_application_metadata(jue_wiki: dict[str, Any]) -> dict[str, Any]:
    pages = jue_wiki.get("pages") if isinstance(jue_wiki.get("pages"), list) else []
    requested_summaries = (
        jue_wiki.get("requested_symbol_summaries")
        if isinstance(jue_wiki.get("requested_symbol_summaries"), list)
        else []
    )
    selected_page_ids = _jue_wiki_page_ids(pages)
    requested_symbol_summary_page_ids = _jue_wiki_page_ids(requested_summaries)
    applied_page_ids = list(
        dict.fromkeys([*selected_page_ids, *requested_symbol_summary_page_ids])
    )
    metadata = {
        "status": "ok" if jue_wiki.get("selection_run_id") else "missing",
        "selection_run_id": str(jue_wiki.get("selection_run_id") or ""),
        "prompt_mode": str(jue_wiki.get("prompt_mode") or ""),
        "selected_page_ids": selected_page_ids,
        "requested_symbol_summary_page_ids": requested_symbol_summary_page_ids,
        "applied_page_ids": applied_page_ids,
        "requested_symbol_summary_count": len(requested_symbol_summary_page_ids),
        "budget_report": jue_wiki.get("budget_report")
        if isinstance(jue_wiki.get("budget_report"), dict)
        else {},
    }
    configured_mode = str(jue_wiki.get("configured_prompt_mode") or "").strip()
    if configured_mode:
        metadata["configured_prompt_mode"] = configured_mode
    if isinstance(jue_wiki.get("mode_recommendation"), dict):
        metadata["mode_recommendation"] = jue_wiki["mode_recommendation"]
    if isinstance(jue_wiki.get("prompt_mode_policy"), dict):
        metadata["prompt_mode_policy"] = jue_wiki["prompt_mode_policy"]
    trust_profile = build_jue_wiki_trust_profile_for_prompt(jue_wiki)
    if trust_profile:
        metadata["trust_profile"] = trust_profile
        decision_adjustments = build_jue_wiki_decision_adjustments_for_prompt(
            trust_profile
        )
        if decision_adjustments:
            metadata["decision_adjustments"] = decision_adjustments
    if isinstance(jue_wiki.get("trust_profile_effectiveness"), dict):
        metadata["trust_profile_effectiveness"] = jue_wiki[
            "trust_profile_effectiveness"
        ]
    validation_repair_effectiveness = (
        compact_jue_wiki_validation_repair_effectiveness_for_prompt(
            jue_wiki.get("validation_repair_effectiveness")
        )
    )
    if validation_repair_effectiveness:
        metadata["validation_repair_effectiveness"] = (
            validation_repair_effectiveness
        )
    wiki_application_coverage = compact_jue_wiki_application_coverage_for_prompt(
        jue_wiki.get("wiki_application_coverage")
    )
    if wiki_application_coverage:
        metadata["wiki_application_coverage"] = wiki_application_coverage
    repair_queue = _compact_jue_wiki_repair_queue(jue_wiki.get("repair_queue"))
    if repair_queue:
        metadata["repair_queue"] = repair_queue
    effectiveness_attention_items = _compact_jue_wiki_effectiveness_attention_items(
        jue_wiki.get("effectiveness_attention_items")
    )
    if not effectiveness_attention_items:
        effectiveness_attention_items = _jue_wiki_effectiveness_attention_items_from_rows(
            [*pages, *requested_summaries]
        )
    if effectiveness_attention_items:
        metadata["effectiveness_attention_items"] = effectiveness_attention_items
    selection_audit = _jue_wiki_selection_audit(pages)
    if selection_audit:
        metadata["selection_audit"] = selection_audit
    quality_summary = summarize_jue_wiki_quality_pressure_for_prompt(
        [*pages, *requested_summaries]
    )
    if quality_summary:
        metadata["quality_summary"] = quality_summary
        quality_action_plan = build_jue_wiki_quality_pressure_action_plan_for_prompt(
            quality_summary
        )
        if quality_action_plan:
            metadata["quality_pressure_action_plan"] = quality_action_plan
    memory_card_quality_summary = _jue_wiki_memory_card_quality_summary(
        requested_summaries
    )
    if memory_card_quality_summary:
        metadata["memory_card_quality_summary"] = memory_card_quality_summary
        memory_card_quality_action_plan = _jue_wiki_memory_card_quality_action_plan(
            memory_card_quality_summary
        )
        if memory_card_quality_action_plan:
            metadata["memory_card_quality_action_plan"] = (
                memory_card_quality_action_plan
            )
    coverage_action_plan = _jue_wiki_requested_symbol_coverage_action_plan(
        metadata.get("budget_report") if isinstance(metadata.get("budget_report"), dict) else {}
    )
    if coverage_action_plan:
        metadata["requested_symbol_coverage_action_plan"] = coverage_action_plan
    return metadata


def _jue_wiki_requested_symbol_coverage_action_plan(
    budget_report: dict[str, Any],
) -> dict[str, Any]:
    status = str(
        budget_report.get("requested_symbol_summary_coverage_status") or ""
    ).strip()
    degraded_symbols = [
        str(symbol).strip().upper()
        for symbol in list(
            budget_report.get("requested_symbol_degraded_summary_symbols") or []
        )[:24]
        if str(symbol).strip()
    ]
    degraded_reasons = [
        {
            key: value
            for key, value in {
                "symbol": str(row.get("symbol") or "").strip().upper(),
                "freshness": str(row.get("freshness") or "").strip(),
                "freshness_status": str(
                    row.get("freshness_status") or ""
                ).strip(),
                "freshness_warnings": [
                    str(item).strip()
                    for item in list(row.get("freshness_warnings") or [])[:6]
                    if str(item).strip()
                ],
                "quality_status": normalize_jue_wiki_quality_status(
                    row.get("quality_status")
                ),
                "quality_warnings": [
                    str(item).strip()
                    for item in list(row.get("quality_warnings") or [])[:6]
                    if str(item).strip()
                ],
            }.items()
            if value not in ("", [], {}, None)
        }
        for row in list(
            budget_report.get("requested_symbol_degraded_summary_reasons") or []
        )[:24]
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    ]
    if status not in {"partial", "none"} and not degraded_symbols:
        return {}
    unsummarized_symbols = [
        str(symbol).strip().upper()
        for symbol in list(budget_report.get("requested_symbol_unsummarized_symbols") or [])[:24]
        if str(symbol).strip()
    ]
    if not unsummarized_symbols and not degraded_symbols:
        return {}
    has_missing_field = "requested_symbol_missing_summary_symbols" in budget_report
    has_prompt_omitted_field = (
        "requested_symbol_prompt_omitted_symbols" in budget_report
    )
    missing_symbols = [
        str(symbol).strip().upper()
        for symbol in list(
            budget_report.get("requested_symbol_missing_summary_symbols") or []
        )[:24]
        if str(symbol).strip()
    ]
    prompt_omitted_symbols = [
        str(symbol).strip().upper()
        for symbol in list(
            budget_report.get("requested_symbol_prompt_omitted_symbols") or []
        )[:24]
        if str(symbol).strip()
    ]
    requested_count = int(budget_report.get("requested_symbol_count") or 0)
    unsummarized_count = int(
        budget_report.get("requested_symbol_unsummarized_count")
        or len(unsummarized_symbols)
    )
    summarized_count = max(requested_count - unsummarized_count, 0)
    required_adjustments: list[dict[str, Any]] = []
    if has_missing_field or has_prompt_omitted_field:
        if missing_symbols:
            required_adjustments.append(
                {
                    "adjustment_type": "coverage_gap_follow_up",
                    "reason": "requested_symbols_missing_from_wiki_summary",
                    "symbols": missing_symbols,
                    "resolution": (
                        "collect_or_rebuild_summary_before_confident_decision"
                    ),
                }
            )
        if prompt_omitted_symbols:
            required_adjustments.append(
                {
                    "adjustment_type": "prompt_omission_follow_up",
                    "reason": "requested_symbols_omitted_from_prompt_summary",
                    "symbols": prompt_omitted_symbols,
                    "resolution": (
                        "treat_as_reviewed_but_lower_confidence_until_direct_summary_check"
                    ),
                }
            )
    elif unsummarized_symbols:
        required_adjustments.append(
            {
                "adjustment_type": "coverage_gap_follow_up",
                "reason": "requested_symbols_missing_from_wiki_summary",
                "symbols": unsummarized_symbols,
                "resolution": (
                    "defer_confident_decision_until_summary_or_live_cross_check"
                ),
            }
        )
    if degraded_symbols:
        required_adjustments.append(
            {
                "adjustment_type": "degraded_summary_cross_check",
                "reason": "requested_symbol_summary_stale_or_weak",
                "symbols": degraded_symbols,
                "resolution": (
                    "cross_check_live_research_and_lower_confidence_until_refreshed"
                ),
            }
        )
    plan = {
        "status": status,
        "hard_blocker": False,
        "decision_policy": (
            "do_not_assume_unsummarized_symbols_were_reviewed"
            if unsummarized_symbols
            else "do_not_overtrust_stale_or_weak_requested_symbol_summaries"
        ),
        "requested_symbol_count": requested_count,
        "summarized_symbol_count": summarized_count,
        "unsummarized_symbol_count": unsummarized_count,
        "unsummarized_symbols": unsummarized_symbols,
        "required_adjustments": required_adjustments,
    }
    if degraded_symbols:
        plan["degraded_summary_count"] = int(
            budget_report.get("requested_symbol_degraded_summary_count")
            or len(degraded_symbols)
        )
        plan["degraded_summary_symbols"] = degraded_symbols
        if degraded_reasons:
            plan["degraded_summary_reasons"] = degraded_reasons
        if not unsummarized_symbols:
            plan["required_action"] = (
                "before confident decisions on stale or weak requested-symbol "
                "summaries, cross-check live crypto research and treat the wiki "
                "memory as cautionary until refreshed"
            )
    if has_missing_field:
        plan["missing_summary_count"] = int(
            budget_report.get("requested_symbol_missing_summary_count")
            or len(missing_symbols)
        )
        plan["missing_summary_symbols"] = missing_symbols
    if has_prompt_omitted_field:
        plan["prompt_omitted_count"] = int(
            budget_report.get("requested_symbol_prompt_omitted_count")
            or len(prompt_omitted_symbols)
        )
        plan["prompt_omitted_symbols"] = prompt_omitted_symbols
    return plan


def _jue_wiki_page_ids(rows: list[Any]) -> list[str]:
    page_ids = [
        str(row.get("page_id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("page_id") or "").strip()
    ]
    return list(dict.fromkeys(page_ids))


def _attach_jue_wiki_repair_contract(
    prompt: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(payload)
    if not contract:
        prompt.pop("jue_wiki_repair_contract", None)
        return
    prompt["jue_wiki_repair_contract"] = contract
    decision_inputs = list(prompt.get("decision_inputs") or [])
    if "jue_wiki_repair_contract" not in decision_inputs:
        decision_inputs.append("jue_wiki_repair_contract")
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_validation_repair_effectiveness_input(
    prompt: dict[str, Any],
) -> None:
    marker = "jue_wiki_validation_repair_effectiveness"
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    validation = (
        application.get("validation_repair_effectiveness")
        if isinstance(application.get("validation_repair_effectiveness"), dict)
        else {}
    )
    decision_inputs = [
        item for item in list(prompt.get("decision_inputs") or []) if item != marker
    ]
    if not validation:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        elif "decision_inputs" in prompt:
            prompt.pop("decision_inputs", None)
        return
    prompt[marker] = validation
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_validation_repair_contract(prompt: dict[str, Any]) -> None:
    marker = "jue_wiki_validation_repair_contract"
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    contract = build_jue_wiki_validation_repair_contract_for_prompt(application)
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if not contract:
        prompt.pop(marker, None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = contract
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_contract_feedback_gap_input(prompt: dict[str, Any]) -> None:
    marker = "jue_wiki_contract_feedback_gap"
    contract = (
        prompt.get("jue_wiki_validation_repair_contract")
        if isinstance(prompt.get("jue_wiki_validation_repair_contract"), dict)
        else {}
    )
    gap = (
        contract.get("contract_feedback_gap")
        if isinstance(contract.get("contract_feedback_gap"), dict)
        else {}
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if not gap:
        prompt.pop(marker, None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = {
        **gap,
        "source_contract": "jue_wiki_validation_repair_contract",
    }
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _jue_wiki_outcome_horizon_gap(
    coverage_payload: dict[str, Any],
) -> dict[str, Any]:
    coverage = (
        coverage_payload.get("coverage")
        if isinstance(coverage_payload.get("coverage"), dict)
        else {}
    )
    missing_count = _safe_int(coverage.get("closed_block_outcomes_without_horizon"))
    missing_pct = _safe_float(
        coverage.get("closed_block_outcomes_without_horizon_pct")
    )
    if missing_count <= 0 and missing_pct <= 0:
        return {}
    return {
        "status": "warning",
        "closed_block_outcomes_without_horizon": missing_count,
        "closed_block_outcomes_without_horizon_pct": missing_pct,
        "required_response": (
            "treat wiki closed-block effectiveness as horizon-ambiguous until "
            "outcomes are reprojected with block horizon/lane"
        ),
        "source_contract": "jue_wiki_application_coverage",
    }


def _attach_jue_wiki_application_coverage_input(prompt: dict[str, Any]) -> None:
    marker = "jue_wiki_application_coverage"
    gap_marker = "jue_wiki_outcome_horizon_gap"
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    coverage = (
        application.get("wiki_application_coverage")
        if isinstance(application.get("wiki_application_coverage"), dict)
        else {}
    )
    decision_inputs = [
        item
        for item in list(prompt.get("decision_inputs") or [])
        if item not in {marker, gap_marker}
    ]
    if not coverage:
        prompt.pop(marker, None)
        prompt.pop(gap_marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        elif "decision_inputs" in prompt:
            prompt.pop("decision_inputs", None)
        return
    prompt[marker] = coverage
    decision_inputs.append(marker)
    gap = _jue_wiki_outcome_horizon_gap(coverage)
    if gap:
        prompt[gap_marker] = gap
        decision_inputs.append(gap_marker)
    else:
        prompt.pop(gap_marker, None)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_decision_adjustments_input(prompt: dict[str, Any]) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_decision_adjustments"
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    contract = _jue_wiki_decision_adjustments_contract(
        application.get("decision_adjustments")
    )
    if contract:
        prompt[marker] = contract
        decision_inputs.append(marker)
        prompt["decision_inputs"] = decision_inputs
    elif decision_inputs != existing_inputs:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        else:
            prompt.pop("decision_inputs", None)


def _jue_wiki_decision_adjustments_contract(
    adjustments: Any,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in _normalize_list(adjustments)[:6]:
        if not isinstance(row, dict):
            continue
        compact = {
            key: _clean_text(row.get(key), limit=180)
            for key in (
                "source",
                "action",
                "target_risk_posture",
                "reason",
                "current_risk_posture",
                "current_status",
            )
            if _clean_text(row.get(key), limit=180)
        }
        for key in ("recommended_allowed_uses", "deprioritized_allowed_uses"):
            values = [
                _clean_text(item, limit=120)
                for item in _normalize_list(row.get(key))[:8]
                if _clean_text(item, limit=120)
            ]
            if values:
                compact[key] = list(dict.fromkeys(values))
        for key in (
            "decision_adjustment_effectiveness",
            "decision_adjustment_audit_effectiveness",
            "decision_adjustment_audit_policy",
            "evidence_grade",
        ):
            value = row.get(key)
            if isinstance(value, dict) and value:
                compact[key] = {
                    item_key: item_value
                    for item_key, item_value in value.items()
                    if item_value not in (None, "", [], {})
                }
        if compact.get("action"):
            rows.append(compact)
    if not rows:
        return {}
    return {
        "version": "jue_wiki_decision_adjustments_v1",
        "status": "active",
        "source_contract": "jue_wiki_application.decision_adjustments",
        "instruction": (
            "Apply wiki-derived decision adjustments as crypto lane, sizing, "
            "entry timing, stop/target, leverage, and evidence-depth guidance. "
            "If the adjustment shifts toward stronger wiki use, still cross-check "
            "live orderbook depth, spread, funding, volatility, account exposure, "
            "execution_gate, and live_authority before creating or scaling blocks."
        ),
        "accepted_uses": [
            "move a candidate from watch to waiting-entry block when live checks agree",
            "downgrade an immediate futures idea to repair_probe or spot/waiting size",
            "adjust stop/target/leverage based on proven wiki posture",
            "record explicit rejection when live microstructure conflicts with wiki memory",
        ],
        "evidence_grade_policy": {
            "positive": "usable_with_live_cross_check",
            "negative": "audit_or_repair_probe_only",
            "thin_sample": "probe_only_until_more_samples",
            "unproven": "require_live_cross_check",
        },
        "adjustments": rows,
        "hard_filters": False,
        "safety_gates_still_override": True,
    }


def _attach_jue_wiki_requested_symbol_coverage_input(prompt: dict[str, Any]) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_requested_symbol_coverage"
    plan = (
        application.get("requested_symbol_coverage_action_plan")
        if isinstance(application.get("requested_symbol_coverage_action_plan"), dict)
        else {}
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if plan:
        prompt[marker] = _jue_wiki_requested_symbol_coverage_contract(plan)
        decision_inputs.append(marker)
        prompt["decision_inputs"] = decision_inputs
    elif decision_inputs != existing_inputs:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        else:
            prompt.pop("decision_inputs", None)


def _attach_jue_wiki_memory_card_quality_input(prompt: dict[str, Any]) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_memory_card_quality"
    summary = (
        application.get("memory_card_quality_summary")
        if isinstance(application.get("memory_card_quality_summary"), dict)
        else {}
    )
    action_plan = (
        application.get("memory_card_quality_action_plan")
        if isinstance(application.get("memory_card_quality_action_plan"), dict)
        else {}
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if action_plan:
        prompt[marker] = {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": summary,
            "action_plan": action_plan,
        }
        decision_inputs.append(marker)
        prompt["decision_inputs"] = decision_inputs
    elif decision_inputs != existing_inputs:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        else:
            prompt.pop("decision_inputs", None)


def _jue_wiki_requested_symbol_coverage_contract(
    action_plan: dict[str, Any],
) -> dict[str, Any]:
    contract = {
        "version": "jue_wiki_requested_symbol_coverage_v1",
        "status": str(action_plan.get("status") or ""),
        "hard_blocker": bool(action_plan.get("hard_blocker") or False),
        "decision_policy": str(action_plan.get("decision_policy") or ""),
        "required_action": str(
            action_plan.get("required_action")
            or (
                "before confident decisions on unsummarized symbols, perform live "
                "cross-check or request/record a fresh wiki summary"
            )
        ),
        "unsummarized_symbols": [
            str(symbol).strip().upper()
            for symbol in list(action_plan.get("unsummarized_symbols") or [])[:24]
            if str(symbol).strip()
        ],
        "required_adjustments": [
            dict(item)
            for item in list(action_plan.get("required_adjustments") or [])[:4]
            if isinstance(item, dict)
        ],
    }
    if "missing_summary_symbols" in action_plan:
        contract["missing_summary_symbols"] = [
            str(symbol).strip().upper()
            for symbol in list(action_plan.get("missing_summary_symbols") or [])[:24]
            if str(symbol).strip()
        ]
    if "prompt_omitted_symbols" in action_plan:
        contract["prompt_omitted_symbols"] = [
            str(symbol).strip().upper()
            for symbol in list(action_plan.get("prompt_omitted_symbols") or [])[:24]
            if str(symbol).strip()
        ]
    if "degraded_summary_symbols" in action_plan:
        contract["degraded_summary_symbols"] = [
            str(symbol).strip().upper()
            for symbol in list(action_plan.get("degraded_summary_symbols") or [])[:24]
            if str(symbol).strip()
        ]
    if "degraded_summary_reasons" in action_plan:
        contract["degraded_summary_reasons"] = [
            {
                key: value
                for key, value in {
                    "symbol": str(item.get("symbol") or "").strip().upper(),
                    "freshness": str(item.get("freshness") or "").strip(),
                    "freshness_status": str(
                        item.get("freshness_status") or ""
                    ).strip(),
                    "freshness_warnings": [
                        str(warning).strip()
                        for warning in list(item.get("freshness_warnings") or [])[:6]
                        if str(warning).strip()
                    ],
                    "quality_status": normalize_jue_wiki_quality_status(
                        item.get("quality_status")
                    ),
                    "quality_warnings": [
                        str(warning).strip()
                        for warning in list(item.get("quality_warnings") or [])[:6]
                        if str(warning).strip()
                    ],
                }.items()
                if value not in ("", [], {}, None)
            }
            for item in list(action_plan.get("degraded_summary_reasons") or [])[:8]
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        ]
    return contract


def _attach_jue_wiki_decision_adjustment_audit_contract(
    prompt: dict[str, Any],
) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_decision_adjustment_audit_contract"
    contract = build_jue_wiki_decision_adjustment_audit_contract_for_prompt(
        application
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if not contract:
        prompt.pop(marker, None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = contract
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _jue_wiki_action_pressure_page_ids(payload: dict[str, Any]) -> list[str]:
    pages = payload.get("pages") if isinstance(payload, dict) else []
    page_ids: list[str] = []
    for row in pages if isinstance(pages, list) else []:
        if not isinstance(row, dict):
            continue
        page_id = str(row.get("page_id") or "").strip()
        refs = row.get("source_refs")
        has_action_pressure_ref = any(
            isinstance(ref, dict)
            and str(ref.get("source_type") or "").strip().lower() == "action_pressure"
            for ref in (refs if isinstance(refs, list) else [])
        )
        if page_id.endswith(".ops.action_pressure") or has_action_pressure_ref:
            page_ids.append(page_id or "unknown.ops.action_pressure")
    return list(dict.fromkeys(page_ids))


def _attach_jue_wiki_action_pressure_contract(
    prompt: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    page_ids = _jue_wiki_action_pressure_page_ids(payload)
    if not page_ids:
        prompt.pop("jue_wiki_action_pressure_contract", None)
        return
    prompt["jue_wiki_action_pressure_contract"] = {
        "version": "jue_wiki_action_pressure_contract_v1",
        "status": "active",
        "page_ids": page_ids,
        "required_when": "selected Jue Wiki pages include *.ops.action_pressure",
        "core_rule": (
            "Operational memory says Jue has been too passive. Resolve the "
            "candidate backlog into at least one concrete action, a staged "
            "waiting/probe block, or explicit candidate-level rejection with "
            "the next price/depth/funding/data condition."
        ),
        "accepted_resolutions": [
            "create a small executable probe block",
            "create a waiting-entry block with entry, target, stop, and trigger",
            "update/close/pause an existing block when the backlog affects it",
            "reject top candidates with exact missing evidence and next trigger",
            "defer only because a server safety gate blocks execution",
        ],
        "hold_only_contract": (
            "If all action arrays are empty, hold_decision must name reviewed "
            "candidate symbols and include next_triggers or data_gaps precise "
            "enough for the next manager run to act."
        ),
        "hard_filters": False,
        "safety_gates_still_override": True,
    }
    decision_inputs = list(prompt.get("decision_inputs") or [])
    if "jue_wiki_action_pressure_contract" not in decision_inputs:
        decision_inputs.append("jue_wiki_action_pressure_contract")
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_prompt_context(
    prompt: dict[str, Any],
    jue_wiki: dict[str, Any] | None,
    *,
    max_chars: int,
) -> None:
    payload = jue_wiki if isinstance(jue_wiki, dict) else {"status": "missing"}
    mode = _jue_wiki_prompt_mode(payload)
    if mode == "observe":
        observation = _sanitize_jue_wiki_observation(payload)
        prompt["jue_wiki_selection_observation"] = observation
        prompt["jue_wiki_application"] = _jue_wiki_application_metadata(observation)
        _attach_jue_wiki_requested_symbol_coverage_input(prompt)
        _attach_jue_wiki_memory_card_quality_input(prompt)
        _attach_jue_wiki_validation_repair_effectiveness_input(prompt)
        _attach_jue_wiki_validation_repair_contract(prompt)
        _attach_jue_wiki_contract_feedback_gap_input(prompt)
        _attach_jue_wiki_application_coverage_input(prompt)
        _attach_jue_wiki_decision_adjustments_input(prompt)
        _attach_jue_wiki_decision_adjustment_audit_contract(prompt)
        _attach_jue_wiki_repair_contract(prompt, observation)
        _attach_jue_wiki_action_pressure_contract(prompt, observation)
        prompt.pop("jue_wiki", None)
        prompt.pop("jue_wiki_budget_report", None)
        return
    payload = _compact_jue_wiki_prompt_payload(payload, max_chars=max_chars)
    if mode == "primary":
        payload = {
            **payload,
            "prompt_mode": "primary",
            "primary_context": True,
            "raw_context_policy": "evidence_only",
        }
        prompt["jue_wiki_primary_context_policy"] = {
            "raw_context_policy": "evidence_only",
            "instruction": (
                "Treat raw memory, RAG, and research context as compact evidence "
                "summaries only; use selected Jue Wiki pages as the primary "
                "compiled knowledge context."
            ),
        }
    prompt["jue_wiki"] = payload
    prompt["jue_wiki_application"] = _jue_wiki_application_metadata(payload)
    _attach_jue_wiki_requested_symbol_coverage_input(prompt)
    _attach_jue_wiki_memory_card_quality_input(prompt)
    _attach_jue_wiki_validation_repair_effectiveness_input(prompt)
    _attach_jue_wiki_validation_repair_contract(prompt)
    _attach_jue_wiki_contract_feedback_gap_input(prompt)
    _attach_jue_wiki_application_coverage_input(prompt)
    _attach_jue_wiki_decision_adjustments_input(prompt)
    _attach_jue_wiki_decision_adjustment_audit_contract(prompt)
    _attach_jue_wiki_repair_contract(prompt, payload)
    _attach_jue_wiki_action_pressure_contract(prompt, payload)
    attach_jue_wiki_budget_report(prompt, max_chars=max_chars)


def _looks_like_signature_type_error(exc: TypeError) -> bool:
    message = str(exc)
    return any(
        marker in message
        for marker in (
            "unexpected keyword argument",
            "positional arguments but",
            "takes no keyword arguments",
            "takes 0 positional arguments",
            "required positional argument",
            "missing 1 required",
        )
    )


def _call_wiki_context_provider(
    provider: Callable[..., dict[str, Any]],
    *,
    target_scope: str,
    symbols: list[str],
    page_types: list[str] | None = None,
    lanes: list[str] | None = None,
    regimes: list[str] | None = None,
    block_ids: list[str] | None = None,
    horizons: list[str] | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    try:
        kwargs: dict[str, Any] = {"target_scope": target_scope, "symbols": symbols}
        if page_types is not None:
            kwargs["page_types"] = list(page_types)
        if lanes is not None:
            kwargs["lanes"] = list(lanes)
        if regimes is not None:
            kwargs["regimes"] = list(regimes)
        if block_ids is not None:
            kwargs["block_ids"] = list(block_ids)
        if horizons is not None:
            kwargs["horizons"] = list(horizons)
        if max_chars is not None:
            kwargs["max_chars"] = int(max_chars)
        try:
            signature = inspect.signature(provider)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            parameters = signature.parameters
            if not parameters:
                return provider()
            accepts_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in parameters.values()
            )
            if not accepts_kwargs:
                kwargs = {key: value for key, value in kwargs.items() if key in parameters}
        payload = provider(**kwargs)
    except TypeError as exc:
        if not _looks_like_signature_type_error(exc):
            raise
        try:
            signature = inspect.signature(provider)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and signature.parameters:
            raise
        payload = provider()
    return payload


def _extract_block_metadata_pattern_key(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return ""
    calculated = (
        metadata.get("calculated_price_plan")
        if isinstance(metadata.get("calculated_price_plan"), dict)
        else metadata.get("calculated")
        if isinstance(metadata.get("calculated"), dict)
        else {}
    )
    pattern_inputs = (
        calculated.get("pattern_inputs")
        if isinstance(calculated.get("pattern_inputs"), dict)
        else {}
    )
    pattern_prior = (
        metadata.get("pattern_prior")
        if isinstance(metadata.get("pattern_prior"), dict)
        else pattern_inputs.get("prior")
        if isinstance(pattern_inputs.get("prior"), dict)
        else {}
    )
    for value in (
        metadata.get("pattern_key"),
        calculated.get("pattern_key"),
        pattern_prior.get("pattern_key"),
    ):
        key = _clean_text(value, limit=160)
        if key:
            return key
    return ""


def _is_legacy_fallback_pattern_key(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    if ":rr_" in text:
        return True
    return text in {
        "spot:long",
        "spot:short",
        "futures:long",
        "futures:short",
    }


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _evidence_item_from_row(row: Any) -> EvidenceItem | None:
    if not isinstance(row, dict):
        return None
    try:
        evidence_id = str(row.get("evidence_id") or row.get("id") or "").strip()
        source = str(row.get("source") or "").strip()
        signal_type = str(row.get("signal_type") or row.get("type") or "").strip()
        symbol = str(row.get("symbol") or "").strip().upper()
        scope = str(row.get("scope") or "binance").strip().lower()
        captured_at = str(row.get("captured_at") or row.get("created_at") or "").strip()
        expires_at = str(row.get("expires_at") or "").strip()
        payload = (
            build_compact_prompt_value(row.get("payload"), string_limit=160, list_limit=6)
            if isinstance(row.get("payload"), dict)
            else {}
        )
        if not all([evidence_id, source, signal_type, symbol, captured_at, expires_at]):
            return None
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return EvidenceItem(
            evidence_id=evidence_id,
            source=source,
            signal_type=signal_type,
            symbol=symbol,
            scope=scope or "binance",
            confidence=_safe_float(row.get("confidence")),
            captured_at=captured_at,
            expires_at=expires_at,
            payload=payload,
            outcome_status=str(row.get("outcome_status") or "pending"),
            used_by_block_ids=list(row.get("used_by_block_ids") or []),
        )
    except (TypeError, ValueError):
        return None


def _evidence_items_from_contexts(*contexts: dict[str, Any]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    for context in contexts:
        if not isinstance(context, dict):
            continue
        evidence_rows = context.get("evidence")
        if not isinstance(evidence_rows, (list, tuple)):
            continue
        for row in evidence_rows:
            item = _evidence_item_from_row(row)
            if item is None or item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            items.append(item)
    return items


def _compile_jue_workflow_prompt_pack(workflow_id: str) -> dict[str, Any]:
    try:
        return JueSkillRegistry().compile_prompt_pack(workflow_id)
    except JueSkillValidationError as exc:
        return {
            "workflow_id": workflow_id,
            "status": "error",
            "error_message": str(exc),
        }


def _policy_scope(value: dict[str, Any]) -> str:
    for key in ("scope", "target_scope", "source_scope", "market_scope", "venue"):
        if key in value:
            return str(value.get(key) or "").strip().lower()
    return ""


def _generalized_binance_policies(memory_context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("policy_rules", "active_policies"):
        values = memory_context.get(key)
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, dict):
                continue
            scope = _policy_scope(row)
            if scope in {"", "global", "binance"}:
                rows.append(dict(row))
    return rows


def _binance_candidate_policy_impacts(
    memory_context: dict[str, Any],
    symbols: list[str],
) -> dict[str, list[dict[str, Any]]]:
    evaluation = (
        memory_context.get("policy_rule_evaluation")
        if isinstance(memory_context.get("policy_rule_evaluation"), dict)
        else {}
    )
    by_symbol = (
        evaluation.get("by_symbol")
        if isinstance(evaluation.get("by_symbol"), dict)
        else {}
    )
    global_impacts = (
        evaluation.get("global")
        if isinstance(evaluation.get("global"), list)
        else []
    )
    compact_global_impacts = build_compact_prompt_value(
        [row for row in global_impacts if isinstance(row, dict)][:4],
        list_limit=4,
        string_limit=140,
    )
    candidate_symbols = [
        str(raw_symbol or "").strip().upper()
        for raw_symbol in symbols[:80]
        if str(raw_symbol or "").strip()
    ]
    replicate_global = len(candidate_symbols) <= 12
    out: dict[str, list[dict[str, Any]]] = {}
    if compact_global_impacts and not replicate_global:
        out["_global"] = compact_global_impacts
    for symbol in candidate_symbols:
        rows = [
            *(
                row
                for row in global_impacts
                if replicate_global and isinstance(row, dict)
            ),
            *(
                row
                for row in list(by_symbol.get(symbol) or [])
                if isinstance(row, dict)
            ),
        ]
        if rows:
            out[symbol] = build_compact_prompt_value(
                rows[:4],
                list_limit=4,
                string_limit=140,
            )
    return out


def _memory_hint_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.startswith("KRW-"):
        return upbit_market_to_usdt_symbol(text)
    return text


def _append_compact_unique(
    rows: list[str],
    value: Any,
    *,
    limit: int,
    chars: int = 160,
) -> None:
    text = _clean_text(value, limit=chars)
    if text and text not in rows and len(rows) < limit:
        rows.append(text)


def _add_symbol_memory_hint(
    hints: dict[str, dict[str, Any]],
    symbol: Any,
    *,
    source: str,
    reason: Any = "",
    risks: Any = None,
    checks: Any = None,
    confidence: Any = None,
) -> None:
    clean_symbol = _memory_hint_symbol(symbol)
    if not clean_symbol:
        return
    hint = hints.setdefault(
        clean_symbol,
        {
            "status": "available",
            "sources": [],
            "source_count": 0,
            "reasons": [],
            "risks": [],
            "checks": [],
            "confidence": 0.0,
        },
    )
    if source and source not in hint["sources"]:
        hint["sources"].append(source)
    _append_compact_unique(hint["reasons"], reason, limit=4)
    if isinstance(risks, list):
        for risk in risks[:3]:
            _append_compact_unique(hint["risks"], risk, limit=3, chars=140)
    if isinstance(checks, list):
        for check in checks[:3]:
            _append_compact_unique(hint["checks"], check, limit=3, chars=140)
    hint["source_count"] = len(hint["sources"])
    hint["confidence"] = max(_safe_float(hint.get("confidence")), _safe_float(confidence))


def _binance_candidate_memory_hints(
    memory_context: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(memory_context, dict):
        return {}
    hints: dict[str, dict[str, Any]] = {}
    symbol_analyses = (
        memory_context.get("symbol_analyses")
        if isinstance(memory_context.get("symbol_analyses"), dict)
        else {}
    )
    for raw_symbol, rows in symbol_analyses.items():
        first = next(
            (row for row in list(rows or []) if isinstance(row, dict)),
            None,
        )
        if not first:
            continue
        _add_symbol_memory_hint(
            hints,
            raw_symbol,
            source="symbol_analysis_memory",
            reason=first.get("summary"),
            risks=first.get("risks"),
            checks=first.get("data_gaps"),
            confidence=first.get("confidence"),
        )

    scoped_memory = (
        memory_context.get("scoped_memory")
        if isinstance(memory_context.get("scoped_memory"), dict)
        else {}
    )
    for bucket, source in (
        ("local", "scoped_local_memory"),
        ("translated", "scoped_translated_memory"),
    ):
        for row in list(scoped_memory.get(bucket) or []):
            if not isinstance(row, dict):
                continue
            _add_symbol_memory_hint(
                hints,
                row.get("symbol") or row.get("key"),
                source=source,
                reason=row.get("summary_md") or row.get("summary"),
                risks=row.get("risks"),
                checks=row.get("checks"),
                confidence=row.get("confidence"),
            )
    return {
        symbol: {
            key: build_compact_prompt_value(value, list_limit=4, string_limit=140)
            if isinstance(value, (list, dict))
            else value
            for key, value in hint.items()
            if value not in ({}, [], "", None, 0.0)
        }
        for symbol, hint in hints.items()
    }


def _attach_binance_candidate_memory_hints(
    candidates: list[dict[str, Any]],
    memory_context: dict[str, Any],
) -> list[dict[str, Any]]:
    hints = _binance_candidate_memory_hints(memory_context)
    if not hints:
        return candidates
    enriched: list[dict[str, Any]] = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        hint = hints.get(_memory_hint_symbol(row.get("symbol")))
        enriched.append({**row, "memory_hint": hint} if hint else row)
    return enriched


MANAGER_PROMPT_STORAGE_LIMIT = 80_000
MANAGER_RESPONSE_STORAGE_LIMIT = 80_000
MANAGER_ACTIONS_STORAGE_LIMIT = 60_000
BINANCE_LIVE_AUTHORITY_STATUS_CACHE_TTL_SEC = 30.0
MANAGER_RUN_PAYLOAD_RETENTION_REASON = "binance_manager_run_payload_retention"
MIN_NOTIONAL_QTY_BUMP_MAX_SHORTFALL_PCT = 5.0
BLOCK_EVENT_PAYLOAD_STORAGE_LIMIT = 5_000
BLOCK_EVENT_TEXT_LIMIT = 360
BLOCK_EVENT_LIST_LIMIT = 24
BLOCK_EVENT_SCALAR_KEYS = {
    "block_id",
    "symbol",
    "market",
    "side",
    "qty",
    "qty_initial",
    "qty_open",
    "entry_price",
    "target_price",
    "stop_price",
    "trigger_price",
    "current_price",
    "price",
    "leverage",
    "margin_type",
    "liquidation_price",
    "manager_run_id",
    "status",
    "force_exit_requested",
    "created_by",
    "reason",
    "source",
    "order_id",
    "id",
    "message",
    "error",
    "created_at",
    "updated_at",
    "opened_at",
    "closed_at",
}

BINANCE_QUOTE_RAW_KEEP_KEYS = {
    "symbol",
    "lastPrice",
    "price",
    "priceChange",
    "priceChangePercent",
    "quoteVolume",
    "volume",
    "bidPrice",
    "bidQty",
    "askPrice",
    "askQty",
    "closeTime",
    "markPrice",
    "indexPrice",
    "lastFundingRate",
    "nextFundingTime",
}
UPBIT_QUOTE_RAW_KEEP_KEYS = {
    "market",
    "trade_price",
    "opening_price",
    "high_price",
    "low_price",
    "change",
    "change_price",
    "change_rate",
    "signed_change_price",
    "signed_change_rate",
    "acc_trade_price_24h",
    "acc_trade_volume_24h",
    "timestamp",
}


def _compact_exchange_quote_raw(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if bool(value.get("_raw_compacted")):
        return value
    try:
        raw_len = len(_json_dumps(value))
    except Exception:
        raw_len = 0
    keep_keys = set(BINANCE_QUOTE_RAW_KEEP_KEYS)
    if "market" in value and any(str(value.get("market") or "").split("-")):
        keep_keys.update(UPBIT_QUOTE_RAW_KEEP_KEYS)
    has_quote_keys = bool(keep_keys.intersection(value.keys()))
    if raw_len < 700 and not has_quote_keys:
        return value
    compact = {
        key: value.get(key)
        for key in sorted(keep_keys)
        if value.get(key) not in (None, "", [], {})
    }
    compact["_raw_compacted"] = True
    compact["_raw_key_count"] = len(value)
    compact["_raw_original_chars"] = raw_len
    return compact


def _compact_binance_quote_raw_for_storage(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if bool(value.get("_raw_compacted")):
        return value
    try:
        raw_len = len(_json_dumps(value))
    except Exception:
        raw_len = 0
    if raw_len < 700 and "raw" not in value:
        return value
    compact: dict[str, Any] = {}
    for key, item in value.items():
        if key == "raw":
            compact[key] = _compact_exchange_quote_raw(item)
        elif key == "error_message":
            compact[key] = str(item or "")[:240]
        else:
            compact[key] = item
    if raw_len >= 700:
        compact["_raw_compacted"] = True
        compact["_raw_key_count"] = len(value)
        compact["_raw_original_chars"] = raw_len
    return compact
BLOCK_EVENT_METADATA_KEYS = {
    "lane",
    "horizon",
    "entry_style",
    "entry_trigger_type",
    "entry_trigger_operator",
    "entry_trigger_price",
    "target_price",
    "stop_price",
    "selected_candidate_key",
    "candidate_key",
    "pattern_key",
    "confidence",
    "source_id",
    "live_authority_status",
}
BLOCK_EVENT_RAW_KEY_HINTS = (
    "raw",
    "packet",
    "prompt",
    "response",
    "candidate_context",
    "research_context",
    "evidence_context",
)
COMPACT_VALIDATION_REPAIR_FOR_STORAGE = partial(
    build_compact_validation_repair_for_storage,
    repair_metadata=build_validation_repair_action_metadata,
)


def _sanitize_block_metadata_for_storage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata = dict(value)
    validation_repair = metadata.get("validation_repair")
    if isinstance(validation_repair, dict):
        compacted_repair = COMPACT_VALIDATION_REPAIR_FOR_STORAGE(validation_repair)
        if compacted_repair:
            metadata["validation_repair"] = compacted_repair
    live_authority = metadata.get("live_authority")
    if live_authority not in ({}, [], "", None):
        metadata["live_authority"] = build_compact_manager_live_authority_for_prompt(
            live_authority,
            string_limit=180,
            list_limit=2,
            compact_value=build_compact_prompt_value,
            compact_value_bounded=build_compact_prompt_value_bounded,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
        )
    return metadata


def _block_event_json_chars(value: Any) -> int:
    try:
        return len(_json_dumps(value))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _compact_block_event_scalar(value: Any, *, limit: int = BLOCK_EVENT_TEXT_LIMIT) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean_text(value, limit=limit)


def _compact_block_event_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = _json_loads(value, {})
    if not isinstance(value, dict):
        return {"_metadata_compacted": True, "_metadata_type": type(value).__name__}

    out: dict[str, Any] = {
        "_metadata_compacted": True,
        "_metadata_key_count": len(value),
        "_metadata_original_chars": _block_event_json_chars(value),
    }
    omitted: list[str] = []
    for key, raw in value.items():
        key_str = str(key)
        lower_key = key_str.lower()
        if any(hint in lower_key for hint in BLOCK_EVENT_RAW_KEY_HINTS):
            omitted.append(key_str)
            continue
        if key_str == "applied_policy_versions" and isinstance(raw, (list, tuple)):
            rows = [_clean_text(item, limit=80) for item in list(raw)[:BLOCK_EVENT_LIST_LIMIT]]
            out[key_str] = rows
            out["applied_policy_version_count"] = len(raw)
            continue
        if key_str in BLOCK_EVENT_METADATA_KEYS:
            if isinstance(raw, (dict, list, tuple)):
                if _block_event_json_chars(raw) <= 900:
                    out[key_str] = build_compact_prompt_value(
                        raw,
                        list_limit=4,
                        string_limit=120,
                    )
                else:
                    omitted.append(key_str)
            else:
                out[key_str] = _compact_block_event_scalar(raw, limit=160)
            continue
        if _block_event_json_chars(raw) > 900:
            omitted.append(key_str)
            continue
        if isinstance(raw, (dict, list, tuple)):
            out[key_str] = build_compact_prompt_value(raw, list_limit=4, string_limit=120)
        else:
            out[key_str] = _compact_block_event_scalar(raw, limit=160)
    if omitted:
        out["_metadata_omitted_keys"] = omitted[:20]
        out["_metadata_omitted_key_count"] = len(omitted)
    return out


def _compact_block_event_payload(value: Any) -> Any:
    if isinstance(value, str):
        parsed = _json_loads(value, None)
        if parsed is not None:
            value = parsed
    if isinstance(value, (list, tuple)):
        rows = [_compact_block_event_payload(item) for item in list(value)[:8]]
        if len(value) > 8:
            rows.append({"_omitted_item_count": len(value) - 8})
        return rows
    if not isinstance(value, dict):
        return _compact_block_event_scalar(value)

    out: dict[str, Any] = {}
    omitted: list[str] = []
    for key, raw in value.items():
        key_str = str(key)
        lower_key = key_str.lower()
        if key_str == "metadata":
            out["metadata"] = _compact_block_event_metadata(raw)
            continue
        if key_str == "metadata_json":
            if "metadata" not in out:
                out["metadata"] = _compact_block_event_metadata(raw)
            continue
        if key_str == "block" and isinstance(raw, dict):
            compact_block = _compact_block_event_payload(raw)
            if isinstance(compact_block, dict):
                for block_key in ("block_id", "symbol", "market", "side", "status"):
                    if block_key in compact_block and block_key not in out:
                        out[block_key] = compact_block[block_key]
                out["block"] = {
                    block_key: compact_block[block_key]
                    for block_key in (
                        "block_id",
                        "symbol",
                        "market",
                        "side",
                        "qty_initial",
                        "qty_open",
                        "entry_price",
                        "target_price",
                        "stop_price",
                        "status",
                    )
                    if block_key in compact_block
                }
            continue
        if any(hint in lower_key for hint in BLOCK_EVENT_RAW_KEY_HINTS):
            omitted.append(key_str)
            continue
        if key_str in BLOCK_EVENT_SCALAR_KEYS:
            out[key_str] = _compact_block_event_scalar(raw)
            continue
        raw_chars = _block_event_json_chars(raw)
        if raw_chars > 900:
            omitted.append(key_str)
            continue
        if isinstance(raw, dict):
            out[key_str] = build_compact_prompt_value(raw, list_limit=4, string_limit=120)
        elif isinstance(raw, (list, tuple)):
            out[key_str] = build_compact_prompt_value(
                list(raw)[:8],
                list_limit=8,
                string_limit=120,
            )
            if len(raw) > 8:
                out[f"{key_str}_omitted_count"] = len(raw) - 8
        else:
            out[key_str] = _compact_block_event_scalar(raw)
    if omitted:
        out["_event_omitted_keys"] = omitted[:24]
        out["_event_omitted_key_count"] = len(omitted)
    if _block_event_json_chars(out) > BLOCK_EVENT_PAYLOAD_STORAGE_LIMIT:
        out = {
            key: out[key]
            for key in (
                "block_id",
                "symbol",
                "market",
                "side",
                "qty_initial",
                "qty_open",
                "entry_price",
                "target_price",
                "stop_price",
                "status",
                "reason",
                "message",
            )
            if key in out
        } | {
            "_event_payload_compacted": True,
            "_event_original_chars": _block_event_json_chars(value),
        }
    return out


def _prompt_priority_symbols(prompt: dict[str, Any], *, limit: int) -> list[str]:
    symbols: list[str] = []
    for row in prompt.get("candidates") or []:
        symbol = str(row.get("symbol") if isinstance(row, dict) else row).upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= limit:
            return symbols
    for row in prompt.get("blocks") or []:
        symbol = str(row.get("symbol") if isinstance(row, dict) else row).upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= limit:
            return symbols
    for raw in prompt.get("universe") or []:
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= limit:
            return symbols
    return symbols


BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS = (
    BUILD_BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS
)


def _context_list_count(context: dict[str, Any], key: str) -> int:
    value = context.get(key)
    return len(value) if isinstance(value, (list, tuple)) else 0


def _compact_raw_context_ref(context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {"status": "missing"}
    requested_symbols = context.get("requested_symbols", context.get("symbols"))
    if not isinstance(requested_symbols, (list, tuple)):
        requested_symbols = []
    history = context.get("history") if isinstance(context.get("history"), dict) else {}
    return {
        "status": _clean_text(context.get("status"), limit=40),
        "requested_symbols": [
            _clean_text(symbol, limit=32)
            for symbol in list(requested_symbols)[:20]
        ],
        "evidence_count": _context_list_count(context, "evidence"),
        "item_count": _context_list_count(context, "items"),
        "candidate_count": _context_list_count(context, "candidates"),
        "event_count": _context_list_count(context, "events"),
        "scorecard_count": _context_list_count(context, "scorecards"),
        "optimized_strategy_set_count": _context_list_count(
            context, "optimized_strategy_sets"
        ),
        "symbol_count": len(requested_symbols),
        "history_item_count": _context_list_count(history, "items"),
    }


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[: max(int(limit), 1)]


def _trim_prompt_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    clean_limit = max(int(limit), 0)
    if clean_limit <= 0:
        return ""
    if len(text) <= clean_limit:
        return text
    suffix = "...[trimmed_for_prompt_budget]"
    keep = max(clean_limit - len(suffix), 0)
    return f"{text[:keep].rstrip()}{suffix}"


def _compact_jue_wiki_source_ref(value: Any) -> dict[str, Any] | str:
    if not isinstance(value, dict):
        return _trim_prompt_text(value, limit=180)
    row: dict[str, Any] = {}
    for key in (
        "source_type",
        "source_id",
        "source_scope",
        "kind",
        "id",
        "status",
        "action_type",
        "repair_status",
        "decision_use",
        "observed_at",
        "as_of",
    ):
        child = value.get(key)
        if child not in (None, "", [], {}):
            row[key] = _trim_prompt_text(child, limit=180)
    symbols = [
        _trim_prompt_text(symbol, limit=40)
        for symbol in list(value.get("symbols") or [])[:6]
        if str(symbol).strip()
    ]
    if symbols:
        row["symbols"] = symbols
    evidence_quality = value.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        canonical_evidence_quality = canonical_jue_wiki_evidence_quality(
            evidence_quality
        )
        row["evidence_quality"] = {
            key: canonical_evidence_quality.get(key)
            for key in (
                "summary_line",
                "source_count",
                "status_counts",
                "warning_counts",
                "source_type_counts",
                "top_warnings",
            )
            if canonical_evidence_quality.get(key) not in (None, "", [], {})
        }
    quality_status = normalize_jue_wiki_quality_status(value.get("quality_status"))
    if not quality_status:
        quality_status = _jue_wiki_quality_status_from_evidence(
            row.get("evidence_quality")
        )
    if quality_status:
        row["quality_status"] = quality_status
    quality_warnings = [
        _trim_prompt_text(warning, limit=120)
        for warning in list(value.get("quality_warnings") or [])[:6]
        if str(warning).strip()
    ]
    if not quality_warnings:
        quality_warnings = _jue_wiki_quality_warnings_from_evidence(
            row.get("evidence_quality"),
            limit=6,
        )
    if quality_warnings:
        row["quality_warnings"] = quality_warnings
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_quality_warning_effectiveness(
    value: Any,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(value or [])[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        warning = _trim_prompt_text(item.get("warning"), limit=120)
        if not warning:
            continue
        row: dict[str, Any] = {"warning": warning}
        for key, max_len in (("page_id", 160), ("status", 80)):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                row[key] = _trim_prompt_text(raw, limit=max_len)
        if item.get("sample_count") not in (None, "", [], {}):
            row["sample_count"] = _safe_int(item.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _safe_float(item.get(key))
        reasons = [
            _trim_prompt_text(reason, limit=120)
            for reason in list(item.get("reasons") or [])[:4]
            if str(reason).strip()
        ]
        if reasons:
            row["reasons"] = reasons
        rows.append(row)
    return rows


def _compact_jue_wiki_effectiveness_bundle(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (("status", 80), ("decision_use", 180)):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _trim_prompt_text(raw, limit=max_len)
    metrics: list[dict[str, Any]] = []
    for item in list(value.get("metrics") or [])[:4]:
        if not isinstance(item, dict):
            continue
        metric: dict[str, Any] = {}
        for key, max_len in (
            ("warning", 120),
            ("page_id", 160),
            ("source_type", 80),
            ("source_id", 160),
            ("status", 80),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                metric[key] = _trim_prompt_text(raw, limit=max_len)
        if item.get("sample_count") not in (None, "", [], {}):
            metric["sample_count"] = _safe_int(item.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if item.get(key) not in (None, "", [], {}):
                metric[key] = _safe_float(item.get(key))
        reasons = [
            _trim_prompt_text(reason, limit=120)
            for reason in list(item.get("reasons") or [])[:4]
            if str(reason).strip()
        ]
        if reasons:
            metric["reasons"] = reasons
        if metric:
            metrics.append(metric)
    if metrics:
        row["metrics"] = metrics
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_usage_guidance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (
        ("trust_level", 40),
        ("risk_posture", 80),
        ("decision_use", 180),
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _trim_prompt_text(raw, limit=max_len)
    for key in ("allowed_uses", "required_cross_checks"):
        items = [
            _trim_prompt_text(item, limit=100)
            for item in list(value.get(key) or [])[:8]
            if str(item).strip()
        ]
        if items:
            row[key] = items
    if value.get("hard_blocker") not in (None, "", [], {}):
        row["hard_blocker"] = build_truthy_gate_value(value.get("hard_blocker"))
    if value.get("max_confidence_without_cross_check") not in (None, "", [], {}):
        row["max_confidence_without_cross_check"] = _safe_float(
            value.get("max_confidence_without_cross_check")
        )
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_status_list(value: Any, *, limit: int = 6) -> list[str]:
    statuses: list[str] = []
    for item in list(value or [])[: max(int(limit), 0)]:
        status = _trim_prompt_text(item, limit=80).lower()
        if status and status not in statuses:
            statuses.append(status)
    return statuses


def _compact_jue_wiki_effectiveness_attention_items(
    value: Any,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in list(value or [])[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key, max_len in (
            ("page_id", 160),
            ("kind", 80),
            ("status", 80),
            ("evidence_id", 180),
            ("warning", 160),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                row[key] = _trim_prompt_text(raw, limit=max_len)
        if row and row not in items:
            items.append(row)
    return items


def _jue_wiki_effectiveness_attention_items_from_rows(
    rows: list[Any],
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = _trim_prompt_text(row.get("page_id"), limit=160)
        if not page_id:
            continue
        for kind, key in (
            ("usage_guidance", "usage_guidance_effectiveness"),
            ("memory_card_quality", "memory_card_quality_effectiveness"),
            ("quality_warning_source", "quality_warning_source_effectiveness"),
            ("quality_warning", "quality_warning_effectiveness"),
        ):
            for item in _jue_wiki_effectiveness_attention_items_for_value(
                page_id=page_id,
                kind=kind,
                value=row.get(key),
            ):
                if item not in items:
                    items.append(item)
                if len(items) >= limit:
                    return _compact_jue_wiki_effectiveness_attention_items(
                        items,
                        limit=limit,
                    )
    return _compact_jue_wiki_effectiveness_attention_items(items, limit=limit)


def _jue_wiki_effectiveness_attention_items_for_value(
    *,
    page_id: str,
    kind: str,
    value: Any,
) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else [value]
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metrics = [
            metric for metric in list(row.get("metrics") or []) if isinstance(metric, dict)
        ]
        source_rows = metrics or [row]
        for source in source_rows:
            if not isinstance(source, dict):
                continue
            status = _trim_prompt_text(
                source.get("status") or row.get("status"),
                limit=80,
            ).lower()
            evidence_id = (
                ""
                if kind == "quality_warning"
                else _trim_prompt_text(
                    source.get("page_id")
                    or source.get("source_id")
                    or source.get("rule_id"),
                    limit=180,
                )
            )
            warning = _trim_prompt_text(
                source.get("warning") or row.get("warning"),
                limit=160,
            )
            if not status and not evidence_id and not warning:
                continue
            item: dict[str, Any] = {"page_id": page_id, "kind": kind}
            if status:
                item["status"] = status
            if evidence_id:
                item["evidence_id"] = evidence_id
            if warning:
                item["warning"] = warning
            if item not in items:
                items.append(item)
    return items


def _compact_jue_wiki_effectiveness_reasons(
    value: Any,
    *,
    limit: int = 8,
) -> list[str]:
    priority_prefixes = (
        "metric_source=",
        "page_id=",
        "raw_scope=",
        "raw_venue=",
        "base_playbook_id=",
        "raw_playbook_id=",
    )
    priority: list[str] = []
    regular: list[str] = []
    for item in list(value or []):
        text = _trim_prompt_text(item, limit=160)
        if not text:
            continue
        target = (
            priority
            if any(text.startswith(prefix) for prefix in priority_prefixes)
            else regular
        )
        if text not in priority and text not in regular:
            target.append(text)
    out: list[str] = []
    for group in (priority, regular):
        for text in group:
            if text not in out:
                out.append(text)
            if len(out) >= max(int(limit), 0):
                return out
    return out


def _compact_jue_wiki_page_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "page_id",
        "rank",
        "score",
        "char_count",
        "freshness",
        "freshness_status",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        child = value.get(key)
        if child not in (None, "", [], {}):
            row[key] = child
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    reason = _trim_prompt_text(value.get("reason"), limit=80)
    if reason:
        row["reason"] = reason
    for key in ("selection_reasons", "selection_penalties"):
        items = value.get(key)
        if isinstance(items, list):
            row[key] = [_trim_prompt_text(item, limit=80) for item in items[:3]]
    quality_warnings = value.get("quality_warnings")
    if isinstance(quality_warnings, list):
        row["quality_warnings"] = [
            _trim_prompt_text(item, limit=120)
            for item in quality_warnings[:3]
            if str(item).strip()
        ]
    freshness_warnings = value.get("freshness_warnings")
    if isinstance(freshness_warnings, list):
        row["freshness_warnings"] = [
            _trim_prompt_text(item, limit=120)
            for item in freshness_warnings[:3]
            if str(item).strip()
        ]
    source_refs = value.get("source_refs")
    if isinstance(source_refs, list):
        refs = [
            ref
            for ref in (_compact_jue_wiki_source_ref(item) for item in source_refs[:3])
            if ref not in (None, "", [], {})
        ]
        if refs:
            row["source_refs"] = refs
    evidence_quality = value.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        row["evidence_quality"] = canonical_jue_wiki_evidence_quality(
            evidence_quality
        )
        if "quality_status" not in row:
            status = _jue_wiki_quality_status_from_evidence(evidence_quality)
            if status:
                row["quality_status"] = status
        if not row.get("quality_warnings"):
            warnings = _jue_wiki_quality_warnings_from_evidence(evidence_quality)
            if warnings:
                row["quality_warnings"] = warnings
    effectiveness = value.get("effectiveness")
    if isinstance(effectiveness, dict):
        row["effectiveness"] = {
            str(key): effectiveness.get(key)
            for key in (
                "status",
                "sample_count",
                "win_rate",
                "expectancy",
                "avg_return_pct",
                "median_mae_pct",
                "drawdown_pressure",
                "helpful_score",
                "confidence",
            )
            if effectiveness.get(key) not in (None, "", [], {})
        }
        reasons = _compact_jue_wiki_effectiveness_reasons(
            effectiveness.get("reasons")
        )
        if reasons:
            row["effectiveness"]["reasons"] = reasons
    usage_guidance = _compact_jue_wiki_usage_guidance(value.get("usage_guidance"))
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for source_key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle(
            value.get(source_key)
        )
        if effectiveness_bundle:
            row[source_key] = effectiveness_bundle
    quality_warning_effectiveness = _compact_jue_wiki_quality_warning_effectiveness(
        value.get("quality_warning_effectiveness")
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    return row


def _compact_jue_wiki_memory_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) > limit and not re.search(r"\s", text):
        return ""
    sentence_match = re.match(r"^(.{1,%d}?[.!?。])(?:\s|$)" % max(limit, 1), text)
    if sentence_match:
        return sentence_match.group(1).strip()
    return _trim_prompt_text(text, limit=limit)


def _jue_wiki_quality_status_from_evidence(evidence_quality: Any) -> str:
    return jue_wiki_quality_status_from_evidence(evidence_quality)


def _jue_wiki_quality_warnings_from_evidence(
    evidence_quality: Any,
    *,
    limit: int = 3,
) -> list[str]:
    if not isinstance(evidence_quality, dict):
        return []
    warnings: list[str] = []
    for item in list(evidence_quality.get("top_warnings") or []):
        if isinstance(item, dict):
            warning = str(item.get("warning") or "").strip()
        else:
            warning = str(item).strip()
        if warning and warning not in warnings:
            warnings.append(_trim_prompt_text(warning, limit=120))
        if len(warnings) >= max(int(limit), 0):
            break
    return warnings


def _jue_wiki_memory_card_quality(card: Any) -> dict[str, Any]:
    if not isinstance(card, dict) or not card:
        return {}
    core_keys = ("stance", "durable_facts", "lessons", "open_questions")
    present_keys = [key for key in core_keys if str(card.get(key) or "").strip()]
    missing_keys = [key for key in core_keys if key not in present_keys]
    evidence_keys = [key for key in present_keys if key != "stance"]
    if "stance" in present_keys and len(evidence_keys) >= 2:
        status = "strong"
    elif len(present_keys) >= 2:
        status = "partial"
    else:
        status = "weak"
    quality: dict[str, Any] = {
        "status": status,
        "present_keys": present_keys,
        "missing_keys": missing_keys,
    }
    if status != "strong":
        quality["required_action"] = "cross_check_live_research_before_high_confidence"
    return quality


def _compact_jue_wiki_requested_symbol_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "symbol",
        "page_id",
        "title",
        "selected_as_page",
        "confidence",
        "freshness",
        "freshness_status",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        child = value.get(key)
        if child not in (None, "", [], {}):
            row[key] = child
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    quality_warnings = value.get("quality_warnings")
    if isinstance(quality_warnings, list):
        row["quality_warnings"] = [
            _trim_prompt_text(item, limit=120)
            for item in quality_warnings[:3]
            if str(item).strip()
        ]
    freshness_warnings = value.get("freshness_warnings")
    if isinstance(freshness_warnings, list):
        row["freshness_warnings"] = [
            _trim_prompt_text(item, limit=120)
            for item in freshness_warnings[:3]
            if str(item).strip()
        ]
    evidence_quality = value.get("evidence_quality")
    if "quality_status" not in row:
        status = _jue_wiki_quality_status_from_evidence(evidence_quality)
        if status:
            row["quality_status"] = status
    if not row.get("quality_warnings"):
        warnings = _jue_wiki_quality_warnings_from_evidence(evidence_quality)
        if warnings:
            row["quality_warnings"] = warnings
    summary = _compact_jue_wiki_memory_text(value.get("summary"), limit=260)
    if summary:
        row["summary"] = summary
    if isinstance(evidence_quality, dict):
        canonical_evidence_quality = canonical_jue_wiki_evidence_quality(
            evidence_quality
        )
        row["evidence_quality"] = {
            key: canonical_evidence_quality.get(key)
            for key in ("summary_line", "status_counts", "top_warnings")
            if canonical_evidence_quality.get(key) not in (None, "", [], {})
        }
    effectiveness = value.get("effectiveness")
    if isinstance(effectiveness, dict):
        row["effectiveness"] = {
            key: effectiveness.get(key)
            for key in (
                "status",
                "sample_count",
                "win_rate",
                "expectancy",
                "helpful_score",
                "confidence",
                "reasons",
            )
            if effectiveness.get(key) not in (None, "", [], {})
        }
    usage_guidance = _compact_jue_wiki_usage_guidance(value.get("usage_guidance"))
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle(value.get(key))
        if effectiveness_bundle:
            row[key] = effectiveness_bundle
    quality_warning_effectiveness = _compact_jue_wiki_quality_warning_effectiveness(
        value.get("quality_warning_effectiveness")
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    memory_card = value.get("memory_card")
    if isinstance(memory_card, dict):
        card: dict[str, str] = {}
        for key, limit in (
            ("stance", 260),
            ("durable_facts", 260),
            ("trading_history", 360),
            ("lessons", 320),
            ("contradictions", 180),
            ("open_questions", 320),
        ):
            text = _compact_jue_wiki_memory_text(memory_card.get(key), limit=limit)
            if text:
                card[key] = text
        if card:
            row["memory_card"] = card
            row["memory_card_quality"] = _jue_wiki_memory_card_quality(card)
    return row


def _jue_wiki_memory_card_quality_summary(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list):
        return {}
    counts = {"strong": 0, "partial": 0, "weak": 0}
    symbols_by_status: dict[str, list[str]] = {
        "strong": [],
        "partial": [],
        "weak": [],
    }
    missing_fields_by_symbol: list[dict[str, Any]] = []
    missing_field_counts: dict[str, int] = {}
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        quality = (
            row.get("memory_card_quality")
            if isinstance(row.get("memory_card_quality"), dict)
            else _jue_wiki_memory_card_quality(row.get("memory_card"))
        )
        if not quality:
            continue
        status = str(quality.get("status") or "").strip()
        if status not in counts:
            status = "weak"
        total += 1
        counts[status] += 1
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol and symbol not in symbols_by_status[status]:
            symbols_by_status[status].append(symbol)
        missing_fields = [
            str(item).strip()
            for item in list(quality.get("missing_keys") or [])[:8]
            if str(item).strip()
        ]
        if missing_fields and status != "strong":
            for field in missing_fields:
                missing_field_counts[field] = missing_field_counts.get(field, 0) + 1
            missing_fields_by_symbol.append(
                {
                    key: value
                    for key, value in {
                        "symbol": symbol,
                        "status": status,
                        "missing_fields": missing_fields,
                    }.items()
                    if value not in ("", [], {}, None)
                }
            )
    if total <= 0:
        return {}
    overall = "weak" if counts["weak"] else "partial" if counts["partial"] else "strong"
    summary: dict[str, Any] = {
        "version": "jue_wiki_memory_card_quality_v1",
        "requested_symbol_summary_count": total,
        "status": overall,
        "strong_count": counts["strong"],
        "partial_count": counts["partial"],
        "weak_count": counts["weak"],
    }
    for status in ("strong", "partial", "weak"):
        symbols = symbols_by_status[status]
        if symbols:
            summary[f"{status}_symbols"] = symbols[:12]
    if missing_fields_by_symbol:
        summary["missing_fields_by_symbol"] = missing_fields_by_symbol[:12]
    if missing_field_counts:
        summary["missing_field_counts"] = {
            key: missing_field_counts[key]
            for key in sorted(missing_field_counts)
        }
    return summary


def _jue_wiki_memory_card_required_checks(summary: dict[str, Any]) -> list[str]:
    if not isinstance(summary, dict):
        return []
    checks_by_field = {
        "stance": "write_current_stance_from_latest_evidence",
        "durable_facts": (
            "refresh_durable_facts_from_reports_fundamentals_and_market_context"
        ),
        "lessons": "review_block_history_and_reflections_for_lessons",
        "open_questions": "record_open_questions_and_data_gaps_before_confident_action",
    }
    required_checks: list[str] = []
    for row in list(summary.get("missing_fields_by_symbol") or [])[:12]:
        if not isinstance(row, dict):
            continue
        for field in list(row.get("missing_fields") or [])[:8]:
            check = checks_by_field.get(str(field).strip())
            if check and check not in required_checks:
                required_checks.append(check)
    return required_checks


def _jue_wiki_memory_card_quality_action_plan(
    summary: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(summary, dict) or not int(summary.get("weak_count") or 0):
        return {}
    plan: dict[str, Any] = {
        "status": "active",
        "hard_blocker": False,
        "decision_policy": "do_not_overtrust_thin_requested_symbol_memory_cards",
        "required_action": "cross_check_live_research_before_high_confidence",
        "reason": "requested_symbol_memory_cards_are_thin",
        "symbols": list(summary.get("weak_symbols") or [])[:12],
    }
    missing_fields = [
        row
        for row in list(summary.get("missing_fields_by_symbol") or [])[:12]
        if isinstance(row, dict)
    ]
    if missing_fields:
        plan["missing_fields_by_symbol"] = missing_fields
    required_checks = _jue_wiki_memory_card_required_checks(summary)
    if required_checks:
        plan["required_checks"] = required_checks
    return plan


def _compact_jue_wiki_prompt_payload(
    payload: dict[str, Any],
    *,
    max_chars: int,
) -> dict[str, Any]:
    budget = max(int(max_chars), 1_000)
    original_chars = len(_json_dumps(payload))
    compact = dict(payload)
    repair_action_batches = compact.get("repair_action_batches")
    if isinstance(repair_action_batches, list):
        compact["repair_action_batches"] = _compact_jue_wiki_repair_action_batches(
            repair_action_batches
        )
    repair_queue = _compact_jue_wiki_repair_queue(compact.get("repair_queue"))
    if repair_queue:
        compact["repair_queue"] = repair_queue
    evidence_quality = compact.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        compact["evidence_quality"] = (
            _sanitize_jue_wiki_evidence_quality_for_observation(evidence_quality)
        )
    freshness_summary = compact.get("freshness_summary")
    if isinstance(freshness_summary, dict):
        compact_freshness_summary = {
            key: value
            for key, value in {
                "page_count": freshness_summary.get("page_count"),
                "status_counts": freshness_summary.get("status_counts"),
                "warning_counts": freshness_summary.get("warning_counts"),
            }.items()
            if value not in (None, "", [], {})
        }
        for key in ("stale_page_ids", "unknown_page_ids"):
            compact_freshness_summary[key] = [
                _trim_prompt_text(item, limit=120)
                for item in list(freshness_summary.get(key) or [])[:12]
                if str(item).strip()
            ]
        compact["freshness_summary"] = compact_freshness_summary
    repair_priority_effectiveness = compact.get("repair_priority_effectiveness")
    if isinstance(repair_priority_effectiveness, dict):
        compact["repair_priority_effectiveness"] = (
            compact_jue_wiki_repair_loop_effectiveness_for_prompt(
                repair_priority_effectiveness
            )
        )
    validation_repair_effectiveness = compact.get(
        "validation_repair_effectiveness"
    )
    if isinstance(validation_repair_effectiveness, dict):
        compact["validation_repair_effectiveness"] = (
            compact_jue_wiki_validation_repair_effectiveness_for_prompt(
                validation_repair_effectiveness
            )
        )
    wiki_application_coverage = compact.get("wiki_application_coverage")
    if isinstance(wiki_application_coverage, dict):
        compact["wiki_application_coverage"] = (
            compact_jue_wiki_application_coverage_for_prompt(
                wiki_application_coverage
            )
        )
    effectiveness_attention_items = compact.get("effectiveness_attention_items")
    if isinstance(effectiveness_attention_items, list):
        compact["effectiveness_attention_items"] = (
            _compact_jue_wiki_effectiveness_attention_items(
                effectiveness_attention_items
            )
        )

    pages = compact.get("pages")
    if isinstance(pages, list):
        compact["pages"] = [
            row
            for row in (_compact_jue_wiki_page_row(page) for page in pages[:12])
            if row
        ]

    rejected_pages = compact.get("rejected_pages")
    if isinstance(rejected_pages, list):
        compact["rejected_pages"] = [
            row
            for row in (
                _compact_jue_wiki_page_row(page) for page in rejected_pages[:20]
            )
            if row
        ]
        omitted = max(len(rejected_pages) - len(compact["rejected_pages"]), 0)
        if omitted:
            compact["rejected_pages_omitted_count"] = omitted

    requested_symbol_summaries = compact.get("requested_symbol_summaries")
    if isinstance(requested_symbol_summaries, list):
        compact["requested_symbol_summaries"] = [
            row
            for row in (
                _compact_jue_wiki_requested_symbol_summary(item)
                for item in requested_symbol_summaries[:8]
            )
            if row
        ]
        omitted = max(
            len(requested_symbol_summaries)
            - len(compact["requested_symbol_summaries"]),
            0,
        )
        if omitted:
            compact["requested_symbol_summaries_omitted_count"] = omitted

    if not compact.get("effectiveness_attention_items"):
        compact_pages = (
            compact.get("pages") if isinstance(compact.get("pages"), list) else []
        )
        compact_requested = (
            compact.get("requested_symbol_summaries")
            if isinstance(compact.get("requested_symbol_summaries"), list)
            else []
        )
        derived_attention_items = _jue_wiki_effectiveness_attention_items_from_rows(
            [*compact_pages, *compact_requested]
        )
        if derived_attention_items:
            compact["effectiveness_attention_items"] = derived_attention_items

    content = str(compact.get("content") or "")
    if content:
        content_limit = max(min(int(budget * 0.78), budget - 4_000), 1_000)
        compact["content"] = _trim_prompt_text(content, limit=content_limit)

    while len(_json_dumps(compact)) > budget and compact.get("content"):
        overflow = len(_json_dumps(compact)) - budget
        current = str(compact.get("content") or "")
        next_limit = max(len(current) - overflow - 512, 0)
        compact["content"] = _trim_prompt_text(current, limit=next_limit)
        if next_limit <= 0:
            break

    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("rejected_pages"), list
    ):
        compact["rejected_pages"] = list(compact["rejected_pages"][:8])

    if len(_json_dumps(compact)) > budget and isinstance(compact.get("pages"), list):
        compact["pages"] = list(compact["pages"][:6])

    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ):
        compact["requested_symbol_summaries"] = list(
            compact["requested_symbol_summaries"][:4]
        )

    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("rejected_pages"), list
    ):
        compact["rejected_pages"] = list(compact["rejected_pages"][:3])

    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ):
        for row in compact["requested_symbol_summaries"]:
            if isinstance(row, dict):
                row.pop("summary", None)

    while len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ) and len(compact["requested_symbol_summaries"]) > 1:
        compact["requested_symbol_summaries"].pop()
        compact["requested_symbol_summaries_omitted_count"] = int(
            compact.get("requested_symbol_summaries_omitted_count") or 0
        ) + 1

    if len(_json_dumps(compact)) > budget:
        compact.pop("rejected_pages", None)
        compact["rejected_pages_omitted_by_budget"] = True

    if len(_json_dumps(compact)) > budget:
        compact.pop("content", None)
        compact["content_omitted_by_budget"] = True

    final_chars = len(_json_dumps(compact))
    if final_chars < original_chars or original_chars > budget:
        report = (
            dict(compact.get("budget_report"))
            if isinstance(compact.get("budget_report"), dict)
            else {}
        )
        report.update(
            {
                "prompt_payload_original_chars": original_chars,
                "prompt_payload_chars": final_chars,
                "prompt_payload_max_chars": budget,
                "prompt_payload_status": (
                    "compacted" if final_chars < original_chars else "ok"
                ),
            }
        )
        compact["budget_report"] = report
    return compact


def _extract_labeled_float(value: Any, labels: tuple[str, ...]) -> float:
    if not isinstance(value, str):
        return 0.0
    text = value.strip()
    if not text:
        return 0.0
    for label in labels:
        match = re.search(
            rf"(?:^|[;,\s]){re.escape(label)}\s*[:=]\s*([-+]?[0-9][0-9,]*(?:\.[0-9]+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return _safe_float(match.group(1).replace(",", ""))
    return 0.0


def _normalize_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _manager_action_item_count(actions: Any) -> int:
    if not isinstance(actions, dict):
        return 0
    return sum(
        len(_normalize_list(actions.get(key)))
        for key in ALLOWED_MANAGER_ACTIONS
    )


def _compact_latest_wiki_attention_item(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact = {
        "component": _clean_text(row.get("component"), limit=120),
        "action_type": _clean_text(row.get("action_type"), limit=120),
        "recommended_resolution": _clean_text(
            row.get("recommended_resolution"),
            limit=180,
        ),
        "quality_warnings": [
            item
            for item in (
                _clean_text(value, limit=120)
                for value in _normalize_list(row.get("quality_warnings"))
            )
            if item
        ][:6],
        "impacted_symbols": [
            item
            for item in (
                _clean_text(value, limit=40)
                for value in _normalize_list(row.get("impacted_symbols"))
            )
            if item
        ][:8],
        "missing_fields": [
            item
            for item in (
                _clean_text(value, limit=80)
                for value in _normalize_list(row.get("missing_fields"))
            )
            if item
        ][:8],
        "required_checks": [
            item
            for item in (
                _clean_text(value, limit=160)
                for value in _normalize_list(row.get("required_checks"))
            )
            if item
        ][:8],
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _latest_wiki_attention_summary(
    *,
    prompt: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    repair_contract = (
        prompt.get("jue_wiki_repair_contract") if isinstance(prompt, dict) else {}
    )
    repair_contract = repair_contract if isinstance(repair_contract, dict) else {}
    contract = repair_contract.get("attention_plan_response_contract")
    contract = contract if isinstance(contract, dict) else {}
    if str(contract.get("status") or "").strip().lower() != "active":
        return {}
    compact = {
        "status": "active",
        "must_address": [
            item
            for item in (
                _clean_text(value, limit=80)
                for value in _normalize_list(contract.get("must_address"))
            )
            if item
        ],
        "repair_now": _compact_latest_wiki_attention_item(contract.get("repair_now")),
        "probe_next": _compact_latest_wiki_attention_item(contract.get("probe_next")),
        "additional_attention": [
            item
            for item in (
                _compact_latest_wiki_attention_item(row)
                for row in _normalize_list(contract.get("additional_attention"))[:4]
            )
            if item
        ],
        "resolution_status": diagnostics.get("jue_wiki_attention_resolution_status"),
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _latest_memory_card_quality_summary(
    *,
    prompt: dict[str, Any],
    diagnostics: dict[str, Any],
    response: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
    hold_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = (
        prompt.get("jue_wiki_memory_card_quality")
        if isinstance(prompt, dict)
        else {}
    )
    quality = quality if isinstance(quality, dict) else {}
    summary = (
        quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    )
    action_plan = (
        quality.get("action_plan")
        if isinstance(quality.get("action_plan"), dict)
        else {}
    )
    weak_symbols = [
        symbol
        for symbol in (
            _clean_text(value, limit=40).upper()
            for value in _normalize_list(
                summary.get("weak_symbols")
                or action_plan.get("symbols")
                or diagnostics.get("jue_wiki_weak_memory_card_symbols")
            )
        )
        if symbol
    ][:12]
    status = _clean_text(
        diagnostics.get("jue_wiki_memory_card_quality_status")
        or action_plan.get("status"),
        limit=80,
    ).lower()
    if status != "active" and not weak_symbols:
        return {}
    resolution_status = _clean_text(
        diagnostics.get("jue_wiki_memory_card_quality_resolution_status"),
        limit=80,
    )
    if resolution_status in {"", "unresolved"}:
        response_dict = response if isinstance(response, dict) else {}
        hold = (
            hold_decision
            if isinstance(hold_decision, dict)
            else response_dict.get("hold_decision")
            if isinstance(response_dict.get("hold_decision"), dict)
            else {}
        )
        actions_dict = actions if isinstance(actions, dict) else {}
        if _summary_actions_have_wiki_memory_card_quality_resolution(
            actions_dict,
            target_symbols=set(weak_symbols),
        ):
            resolution_status = "action_metadata"
        elif _summary_hold_decision_has_concrete_step(
            hold,
            target_symbols=set(weak_symbols),
        ):
            resolution_status = "hold_trigger"
        elif not resolution_status:
            resolution_status = "unresolved"

    compact = {
        "status": status or "active",
        "weak_symbols": weak_symbols,
        "required_action": _clean_text(
            action_plan.get("required_action"),
            limit=180,
        ),
        "decision_policy": _clean_text(
            action_plan.get("decision_policy"),
            limit=180,
        ),
        "reason": _clean_text(action_plan.get("reason"), limit=180),
        "missing_fields_by_symbol": [
            {
                key: value
                for key, value in {
                    "symbol": _clean_text(row.get("symbol"), limit=40).upper(),
                    "status": _clean_text(row.get("status"), limit=40),
                    "missing_fields": [
                        field
                        for field in (
                            _clean_text(item, limit=80)
                            for item in _normalize_list(row.get("missing_fields"))
                        )
                        if field
                    ][:8],
                }.items()
                if value not in ("", [], {}, None)
            }
            for row in _normalize_list(action_plan.get("missing_fields_by_symbol"))[:12]
            if isinstance(row, dict)
        ],
        "required_checks": [
            check
            for check in (
                _clean_text(item, limit=140)
                for item in _normalize_list(action_plan.get("required_checks"))
            )
            if check
        ][:8],
        "resolution_status": resolution_status,
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _latest_candidate_memory_contract_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    error = _clean_text(
        response.get("contract_error") or run.get("error_message"),
        limit=160,
    )
    policy = (
        prompt.get("candidate_memory_hint_policy")
        if isinstance(prompt.get("candidate_memory_hint_policy"), dict)
        else {}
    )
    if (
        policy.get("action_contract") != "cite_or_reject_candidate_memory_hint"
        and error != "candidate_memory_hint_resolution_missing_from_model"
    ):
        return {}
    memory_candidates: list[dict[str, Any]] = []
    impacted_symbols: list[str] = []
    for row in _normalize_list(prompt.get("candidates")):
        if not isinstance(row, dict) or not isinstance(row.get("memory_hint"), dict):
            continue
        memory_candidates.append(row)
        symbol = _clean_text(row.get("symbol"), limit=40).upper()
        if symbol and symbol not in impacted_symbols:
            impacted_symbols.append(symbol)
    if not memory_candidates and not error:
        return {}
    return {
        key: value
        for key, value in {
            "status": "error" if error else "active",
            "contract": "cite_or_reject_candidate_memory_hint",
            "error": error,
            "memory_packet_count": len(memory_candidates),
            "impacted_symbols": impacted_symbols[:12],
            "resolution_status": "missing" if error else "available",
        }.items()
        if value not in (None, "", [], {})
    }


def _latest_validation_repair_memory_contract_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    repair = (
        prompt.get("validation_repair")
        if isinstance(prompt.get("validation_repair"), dict)
        else {}
    )
    if not repair:
        return {}
    error = _clean_text(
        response.get("contract_error") or run.get("error_message"),
        limit=160,
    )
    contracts: list[str] = []
    contract_errors: list[str] = []
    impacted_symbols: list[str] = []
    resolved_candidates: list[dict[str, Any]] = []

    def add_unique(target: list[str], value: Any, *, limit: int = 160) -> None:
        text = _clean_text(value, limit=limit)
        if text and text not in target:
            target.append(text)

    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response.get("validation_repair_resolution"), dict)
        else {}
    )
    for row in _normalize_list(resolution.get("resolved_candidates")):
        if not isinstance(row, dict):
            continue
        memory_resolution = _clean_text(
            row.get("memory_contract_resolution"),
            limit=320,
        )
        memory_contract = _clean_text(row.get("memory_contract"), limit=160)
        memory_error = _clean_text(row.get("memory_contract_error"), limit=160)
        if not (memory_resolution or memory_contract or memory_error):
            continue
        symbol = _clean_text(row.get("symbol"), limit=40).upper()
        item = {
            key: value
            for key, value in {
                "symbol": symbol,
                "market": _clean_text(row.get("market"), limit=40),
                "resolution": _clean_text(row.get("resolution"), limit=120),
                "memory_contract": memory_contract,
                "memory_contract_error": memory_error,
                "memory_contract_resolution": memory_resolution,
            }.items()
            if value not in (None, "", [], {})
        }
        if item:
            resolved_candidates.append(item)
        add_unique(contracts, memory_contract)
        add_unique(contract_errors, memory_error)
        add_unique(impacted_symbols, symbol, limit=40)

    for value in _normalize_list(repair.get("memory_contracts")):
        add_unique(contracts, value)
    for value in _normalize_list(repair.get("memory_contract_errors")):
        add_unique(contract_errors, value)
    for value in _normalize_list(repair.get("impacted_symbols")):
        symbol = _clean_text(value, limit=40).upper()
        if symbol:
            add_unique(impacted_symbols, symbol, limit=40)
    memory_rows = 0
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            if (
                row.get("memory_contract") in (None, "", [], {})
                and row.get("memory_contract_error") in (None, "", [], {})
            ):
                continue
            memory_rows += 1
            add_unique(contracts, row.get("memory_contract"))
            add_unique(contract_errors, row.get("memory_contract_error"))
            for value in _normalize_list(row.get("impacted_symbols")):
                symbol = _clean_text(value, limit=40).upper()
                if symbol:
                    add_unique(impacted_symbols, symbol, limit=40)
    required_checks = {
        _clean_text(value, limit=80)
        for value in _normalize_list(repair.get("required_checks"))
    }
    if (
        not contracts
        and not contract_errors
        and not impacted_symbols
        and "require_memory_contract_resolution" not in required_checks
        and error != "memory_contract_resolution_missing_from_model"
    ):
        return {}
    status = "error" if error else "resolved" if resolved_candidates else "active"
    return {
        key: value
        for key, value in {
            "status": status,
            "contract": contracts[0] if contracts else "",
            "error": error,
            "memory_contract_errors": contract_errors[:8],
            "memory_packet_count": max(
                memory_rows,
                len(impacted_symbols),
                len(resolved_candidates),
                0,
            ),
            "impacted_symbols": impacted_symbols[:12],
            "resolution_status": (
                "missing" if error else "resolved" if resolved_candidates else "available"
            ),
            "resolved_candidates": resolved_candidates[:8],
            "source": "validation_repair",
        }.items()
        if value not in (None, "", [], {})
    }


def _summary_action_row_identity_symbols(row: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            symbol = _clean_text(item, limit=40).upper()
            if symbol:
                symbols.add(symbol)

    add(row.get("symbol"))
    add(row.get("code"))
    add(row.get("ticker"))
    add(row.get("symbols"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    add(metadata.get("symbol"))
    add(metadata.get("code"))
    add(metadata.get("ticker"))
    add(metadata.get("symbols"))
    return symbols


def _summary_actions_have_wiki_memory_card_quality_resolution(
    actions: dict[str, Any],
    *,
    target_symbols: set[str] | None = None,
) -> bool:
    if not isinstance(actions, dict):
        return False
    target_symbols = target_symbols or set()
    for key in ALLOWED_MANAGER_ACTIONS:
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            if target_symbols and _summary_action_row_identity_symbols(row).isdisjoint(
                target_symbols
            ):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_memory_card_quality",
                "jue_wiki_memory_card_cross_check",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if isinstance(note, dict):
                    if any(_clean_text(value, limit=120) for value in note.values()):
                        return True
                elif _clean_text(note, limit=120):
                    return True
    return False


def _prompt_jue_wiki_decision_adjustments(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    rows = (
        application.get("decision_adjustments")
        if isinstance(application.get("decision_adjustments"), list)
        else []
    )
    return [dict(row) for row in rows[:4] if isinstance(row, dict)]


def _attach_prompt_jue_wiki_decision_adjustments_to_actions(
    actions: dict[str, Any],
    *,
    prompt: dict[str, Any],
) -> dict[str, Any]:
    adjustments = _prompt_jue_wiki_decision_adjustments(prompt)
    if not adjustments:
        return actions
    adjusted = dict(actions)
    for key in ALLOWED_MANAGER_ACTIONS:
        rows = adjusted.get(key)
        if not isinstance(rows, list):
            continue
        next_rows: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                next_rows.append(row)
                continue
            next_row = dict(row)
            metadata = (
                dict(next_row.get("metadata"))
                if isinstance(next_row.get("metadata"), dict)
                else {}
            )
            if (
                "jue_wiki_decision_adjustments" not in metadata
                and "jue_wiki_decision_adjustments" not in next_row
            ):
                metadata["jue_wiki_decision_adjustments"] = adjustments
            next_row["metadata"] = metadata
            next_rows.append(next_row)
        adjusted[key] = next_rows
    return adjusted


def _summary_hold_decision_has_concrete_step(
    hold_decision: dict[str, Any],
    *,
    target_symbols: set[str] | None = None,
) -> bool:
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    target_symbols = target_symbols or set()

    def extract(value: Any) -> set[str]:
        values = value if isinstance(value, list) else [value]
        symbols: set[str] = set()
        for item in values:
            symbol = _clean_text(item, limit=40).upper()
            if symbol:
                symbols.add(symbol)
        return symbols

    for row in _normalize_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        if target_symbols:
            row_symbols = (
                extract(row.get("symbol"))
                | extract(row.get("code"))
                | extract(row.get("ticker"))
                | extract(row.get("symbols"))
            )
            if row_symbols.isdisjoint(target_symbols):
                continue
        if _clean_text(row.get("symbol"), limit=40) and _clean_text(
            row.get("condition") or row.get("reason"),
            limit=300,
        ):
            return True
        if _safe_float(row.get("price")) > 0 and _clean_text(
            row.get("condition"),
            limit=300,
        ):
            return True
    if target_symbols:
        return bool(
            extract(hold.get("watch_symbols")).intersection(target_symbols)
            and _normalize_list(hold.get("data_gaps"))
        )
    return bool(
        _normalize_list(hold.get("data_gaps"))
        and _normalize_list(hold.get("watch_symbols"))
    )


def _is_insufficient_balance_error(value: Any) -> bool:
    text = str(value or "").lower()
    return (
        "insufficient balance" in text
        or "code': -2010" in text
        or '"code": -2010' in text
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class BinanceBlockRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    block_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    side TEXT NOT NULL DEFAULT 'long',
                    qty_initial REAL NOT NULL,
                    qty_open REAL NOT NULL DEFAULT 0,
                    entry_price REAL,
                    target_price REAL,
                    stop_price REAL,
                    leverage INTEGER NOT NULL DEFAULT 1,
                    margin_type TEXT NOT NULL DEFAULT '',
                    liquidation_price REAL,
                    thesis TEXT NOT NULL DEFAULT '',
                    llm_reason TEXT NOT NULL DEFAULT '',
                    risk_note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'llm',
                    manager_run_id INTEGER,
                    status TEXT NOT NULL,
                    force_exit_requested INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    opened_at TEXT NOT NULL DEFAULT '',
                    closed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS block_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binance_block_events_block
                    ON block_events(block_id, id DESC);

                CREATE TABLE IF NOT EXISTS block_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    order_type TEXT NOT NULL DEFAULT 'MARKET',
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manager_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    actions_json TEXT NOT NULL DEFAULT '{}',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    workflow_version INTEGER NOT NULL DEFAULT 0,
                    skill_ids_json TEXT NOT NULL DEFAULT '[]',
                    contract_ids_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS quote_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    price REAL,
                    source TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error_message TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS block_performance_reflections (
                    block_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    side TEXT NOT NULL DEFAULT 'long',
                    lane TEXT NOT NULL DEFAULT '',
                    entry_price REAL NOT NULL DEFAULT 0,
                    exit_price REAL NOT NULL DEFAULT 0,
                    stop_price REAL NOT NULL DEFAULT 0,
                    target_price REAL NOT NULL DEFAULT 0,
                    pnl_usdt REAL NOT NULL DEFAULT 0,
                    gross_pnl_usdt REAL NOT NULL DEFAULT 0,
                    net_pnl_usdt REAL NOT NULL DEFAULT 0,
                    fee_usdt REAL NOT NULL DEFAULT 0,
                    funding_usdt REAL NOT NULL DEFAULT 0,
                    slippage_usdt REAL NOT NULL DEFAULT 0,
                    spread_usdt REAL NOT NULL DEFAULT 0,
                    cost_source TEXT NOT NULL DEFAULT '',
                    r_multiple REAL NOT NULL DEFAULT 0,
                    mfe_r_multiple REAL NOT NULL DEFAULT 0,
                    mae_r_multiple REAL NOT NULL DEFAULT 0,
                    pattern_key TEXT NOT NULL DEFAULT '',
                    lesson_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binance_block_perf_created
                    ON block_performance_reflections(created_at DESC);

                CREATE TABLE IF NOT EXISTS growth_target_months (
                    month_key TEXT PRIMARY KEY,
                    start_equity_usdt REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS growth_target_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month_key TEXT NOT NULL,
                    start_equity_usdt REAL NOT NULL DEFAULT 0,
                    current_equity_usdt REAL NOT NULL DEFAULT 0,
                    target_equity_usdt REAL NOT NULL DEFAULT 0,
                    current_return_pct REAL NOT NULL DEFAULT 0,
                    remaining_return_pct REAL NOT NULL DEFAULT 0,
                    required_daily_return_pct REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binance_growth_snapshots_month
                    ON growth_target_snapshots(month_key, id DESC);

                CREATE TABLE IF NOT EXISTS equity_baselines (
                    period_key TEXT PRIMARY KEY,
                    start_equity_usdt REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kill_switch (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO kill_switch (id, enabled, reason, updated_at)
                VALUES (1, 0, '', datetime('now'));
                """
            )
            for statement in (
                "ALTER TABLE blocks "
                "ADD COLUMN market TEXT NOT NULL DEFAULT 'spot'",
                "ALTER TABLE blocks "
                "ADD COLUMN side TEXT NOT NULL DEFAULT 'long'",
                "ALTER TABLE blocks "
                "ADD COLUMN leverage INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE blocks "
                "ADD COLUMN margin_type TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE blocks "
                "ADD COLUMN liquidation_price REAL",
                "ALTER TABLE block_orders "
                "ADD COLUMN market TEXT NOT NULL DEFAULT 'spot'",
                "ALTER TABLE quote_snapshots "
                "ADD COLUMN market TEXT NOT NULL DEFAULT 'spot'",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN lane TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN mfe_r_multiple REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN mae_r_multiple REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN pattern_key TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN gross_pnl_usdt REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN net_pnl_usdt REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN fee_usdt REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN funding_usdt REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN slippage_usdt REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN spread_usdt REAL NOT NULL DEFAULT 0",
                "ALTER TABLE block_performance_reflections "
                "ADD COLUMN cost_source TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE manager_runs "
                "ADD COLUMN workflow_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE manager_runs "
                "ADD COLUMN workflow_version INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE manager_runs "
                "ADD COLUMN skill_ids_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE manager_runs "
                "ADD COLUMN contract_ids_json TEXT NOT NULL DEFAULT '[]'",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_binance_blocks_status_symbol
                    ON blocks(status, market, symbol);
                CREATE INDEX IF NOT EXISTS idx_binance_block_orders_block
                    ON block_orders(block_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_binance_block_quotes_symbol
                    ON quote_snapshots(market, symbol, fetched_at DESC);
                """
            )
            self._repair_legacy_performance_lanes(conn)
            self._repair_legacy_performance_pattern_keys(conn)

    @classmethod
    def _repair_legacy_performance_lanes(cls, conn: sqlite3.Connection) -> int:
        rows = conn.execute(
            """
            SELECT block_id, market, side, lane
            FROM block_performance_reflections
            """
        ).fetchall()
        repaired = 0
        for row in rows:
            raw_lane = str(row["lane"] or "").strip().lower()
            canonical = canonical_binance_performance_lane(
                raw_lane=raw_lane,
                market=row["market"],
                side=row["side"],
            )
            if canonical == raw_lane:
                continue
            conn.execute(
                """
                UPDATE block_performance_reflections
                SET lane = ?
                WHERE block_id = ?
                """,
                (canonical, row["block_id"]),
            )
            repaired += 1
        return repaired

    @staticmethod
    def _repair_legacy_performance_pattern_keys(conn: sqlite3.Connection) -> int:
        rows = conn.execute(
            """
            SELECT r.block_id, r.pattern_key, b.metadata_json
            FROM block_performance_reflections r
            JOIN blocks b ON b.block_id = r.block_id
            WHERE b.metadata_json IS NOT NULL
              AND b.metadata_json != ''
            """
        ).fetchall()
        repaired = 0
        for row in rows:
            stored = str(row["pattern_key"] or "").strip()
            if stored and not _is_legacy_fallback_pattern_key(stored):
                continue
            metadata = _json_loads(row["metadata_json"], {})
            repaired_key = _extract_block_metadata_pattern_key(metadata)
            if not repaired_key or repaired_key == stored:
                continue
            conn.execute(
                """
                UPDATE block_performance_reflections
                SET pattern_key = ?
                WHERE block_id = ?
                """,
                (repaired_key, row["block_id"]),
            )
            repaired += 1
        return repaired

    def create_block(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        symbol = str(payload.get("symbol") or "").strip().upper()
        market = normalize_market(payload.get("market"))
        qty_initial = _safe_float(payload.get("qty_initial") or payload.get("qty"))
        qty_open = _safe_float(payload.get("qty_open"))
        status = str(payload.get("status") or "proposed").strip().lower()
        if status not in BLOCK_STATUSES:
            status = "proposed"
        if status == "open" and qty_open <= 0:
            qty_open = qty_initial
        if qty_open <= 0 and status in {"entry_pending", "exit_pending"}:
            qty_open = qty_initial
        metadata = _sanitize_block_metadata_for_storage(payload.get("metadata") or {})
        row = {
            "block_id": str(payload.get("block_id") or self._new_block_id(symbol, market)),
            "symbol": symbol,
            "market": market,
            "side": normalize_position_side(payload.get("side")),
            "qty_initial": max(qty_initial, 0.0),
            "qty_open": max(qty_open, 0.0),
            "entry_price": _safe_float(payload.get("entry_price")) or None,
            "target_price": _safe_float(payload.get("target_price")) or None,
            "stop_price": _safe_float(payload.get("stop_price")) or None,
            "leverage": max(_safe_int(payload.get("leverage") or 1), 1),
            "margin_type": str(payload.get("margin_type") or "").strip().lower(),
            "liquidation_price": _safe_float(payload.get("liquidation_price")) or None,
            "thesis": _clean_text(payload.get("thesis"), limit=2000),
            "llm_reason": _clean_text(payload.get("llm_reason") or payload.get("reason"), limit=2000),
            "risk_note": _clean_text(payload.get("risk_note"), limit=2000),
            "created_by": str(payload.get("created_by") or "llm"),
            "manager_run_id": payload.get("manager_run_id"),
            "status": status,
            "force_exit_requested": 1 if payload.get("force_exit_requested") else 0,
            "metadata_json": _json_dumps(metadata),
            "created_at": now,
            "updated_at": now,
            "opened_at": str(payload.get("opened_at") or (now if status == "open" else "")),
            "closed_at": str(payload.get("closed_at") or ""),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, symbol, market, side, qty_initial, qty_open,
                    entry_price, target_price, stop_price, leverage, margin_type,
                    liquidation_price, thesis, llm_reason, risk_note, created_by,
                    manager_run_id, status, force_exit_requested, metadata_json,
                    created_at, updated_at, opened_at, closed_at
                )
                VALUES (
                    :block_id, :symbol, :market, :side, :qty_initial, :qty_open,
                    :entry_price, :target_price, :stop_price, :leverage,
                    :margin_type, :liquidation_price, :thesis, :llm_reason,
                    :risk_note, :created_by, :manager_run_id, :status,
                    :force_exit_requested, :metadata_json, :created_at,
                    :updated_at, :opened_at, :closed_at
                )
                """,
                row,
            )
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["block_id"],
                    "created",
                    f"{market} block created: {symbol} x{row['qty_initial']}",
                    _json_dumps(_compact_block_event_payload({**row, "metadata": metadata})),
                    now,
                ),
            )
        return self.get_block(row["block_id"]) or row

    def update_block(self, block_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "qty_open",
            "entry_price",
            "target_price",
            "stop_price",
            "thesis",
            "llm_reason",
            "risk_note",
            "status",
            "force_exit_requested",
            "opened_at",
            "closed_at",
            "metadata",
        }
        updates: dict[str, Any] = {}
        event_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "status" and str(value) not in BLOCK_STATUSES:
                continue
            if key == "metadata":
                metadata = _sanitize_block_metadata_for_storage(value)
                updates["metadata_json"] = _json_dumps(metadata)
                event_fields[key] = metadata
                continue
            updates[key] = value
            event_fields[key] = value
        if not updates:
            return self.get_block(block_id)
        updates["updated_at"] = utc_now_iso()
        updates["block_id"] = str(block_id)
        set_clause = ", ".join(f"{key} = :{key}" for key in updates)
        with self._connect() as conn:
            conn.execute(f"UPDATE blocks SET {set_clause} WHERE block_id = :block_id", updates)
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(block_id),
                    "updated",
                    "block updated",
                    _json_dumps(_compact_block_event_payload(event_fields)),
                    utc_now_iso(),
                ),
            )
        return self.get_block(block_id)

    def claim_entry_pending(
        self,
        block_id: str,
        *,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE blocks
                SET status = 'entry_pending',
                    qty_open = qty_initial,
                    llm_reason = ?,
                    updated_at = ?
                WHERE block_id = ? AND status = 'proposed'
                """,
                (str(reason or ""), now, str(block_id)),
            )
            if int(cursor.rowcount or 0) <= 0:
                return None
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(block_id),
                    "entry_claimed",
                    str(reason or "entry_triggered"),
                    _json_dumps(_compact_block_event_payload(payload or {})),
                    now,
                ),
            )
        return self.get_block(block_id)

    def claim_exit_pending(
        self,
        block_id: str,
        *,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE blocks
                SET status = 'exit_pending',
                    llm_reason = ?,
                    updated_at = ?
                WHERE block_id = ? AND status IN ('open', 'exit_pending')
                """,
                (str(reason or ""), now, str(block_id)),
            )
            if int(cursor.rowcount or 0) <= 0:
                return False
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(block_id),
                    "exit_claimed",
                    str(reason or "exit_claimed"),
                    _json_dumps(_compact_block_event_payload(payload or {})),
                    now,
                ),
            )
        return True

    def add_event(
        self,
        block_id: str,
        event_type: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(block_id),
                    str(event_type),
                    str(message or ""),
                    _json_dumps(_compact_block_event_payload(payload or {})),
                    utc_now_iso(),
                ),
            )

    def add_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        row = {
            "block_id": str(payload.get("block_id") or ""),
            "symbol": str(payload.get("symbol") or "").upper(),
            "market": normalize_market(payload.get("market")),
            "side": str(payload.get("side") or "").lower(),
            "qty": max(_safe_float(payload.get("qty") or payload.get("quantity")), 0.0),
            "order_type": str(payload.get("order_type") or "MARKET"),
            "status": str(payload.get("status") or "planned"),
            "reason": str(payload.get("reason") or ""),
            "response_json": _json_dumps(payload.get("response") or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, market, side, qty, order_type, status,
                    reason, response_json, created_at, updated_at
                )
                VALUES (
                    :block_id, :symbol, :market, :side, :qty, :order_type, :status,
                    :reason, :response_json, :created_at, :updated_at
                )
                """,
                row,
            )
            order_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["block_id"],
                    "order",
                    f"{row['market']} {row['side']} {row['qty']} {row['status']}",
                    _json_dumps(_compact_block_event_payload({"id": order_id, **row})),
                    now,
                ),
            )
        return {"id": order_id, **build_row_order_payload(row)}

    def update_order_response(self, order_id: int, response: dict[str, Any]) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM block_orders WHERE id = ? LIMIT 1",
                (int(order_id),),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE block_orders
                SET response_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_json_dumps(response), now, int(order_id)),
            )
            updated = conn.execute(
                "SELECT * FROM block_orders WHERE id = ? LIMIT 1",
                (int(order_id),),
            ).fetchone()
        return build_row_to_order(updated) if updated else None

    def save_quotes(self, quotes: list[dict[str, Any]]) -> None:
        if not quotes:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO quote_snapshots (
                    symbol, market, price, source, fetched_at, status, error_message, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row.get("symbol") or "").upper(),
                        normalize_market(row.get("market")),
                        row.get("price"),
                        str(row.get("source") or ""),
                        str(row.get("fetched_at") or utc_now_iso()),
                        str(row.get("status") or "ok"),
                        str(row.get("error_message") or ""),
                        _json_dumps(
                            _compact_binance_quote_raw_for_storage(row.get("raw") or {})
                        ),
                    )
                    for row in quotes
                ],
            )

    def compact_verbose_quote_raw_payloads(
        self,
        *,
        batch_size: int = 5_000,
        vacuum: bool = False,
    ) -> dict[str, Any]:
        limit = max(int(batch_size), 1)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, raw_json
                FROM quote_snapshots
                WHERE raw_json NOT LIKE '%"_raw_compacted"%'
                  AND (
                    raw_json LIKE '%"raw"%'
                    OR LENGTH(raw_json) >= 700
                  )
                ORDER BY id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            changed = 0
            for row in rows:
                payload = _json_loads(row["raw_json"], default={})
                if not isinstance(payload, dict):
                    continue
                compact_json = _json_dumps(_compact_binance_quote_raw_for_storage(payload))
                if compact_json == str(row["raw_json"] or ""):
                    continue
                conn.execute(
                    "UPDATE quote_snapshots SET raw_json = ? WHERE id = ?",
                    (compact_json, row["id"]),
                )
                changed += 1
            remaining = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM quote_snapshots
                    WHERE raw_json NOT LIKE '%"_raw_compacted"%'
                      AND (
                        raw_json LIKE '%"raw"%'
                        OR LENGTH(raw_json) >= 700
                      )
                    """
                ).fetchone()[0]
                or 0
            )
        vacuumed = False
        if vacuum and changed:
            with self._connect() as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "batch_size": limit,
            "updated": changed,
            "remaining": remaining,
            "vacuumed": vacuumed,
        }

    def prune_operational_history(
        self,
        *,
        quote_retention_days: int = 14,
        manager_run_retention_days: int = 30,
        archive_retention_days: int = 14,
        manager_run_recent_count: int = 96,
        manager_run_payload_min_chars: int = 20_000,
        growth_snapshot_retention_days: int = 14,
    ) -> dict[str, Any]:
        rules = build_binance_operational_retention_rules(
            quote_retention_days=quote_retention_days,
            manager_run_retention_days=manager_run_retention_days,
            archive_retention_days=archive_retention_days,
        )
        if not rules:
            summary = {"status": "ok", "deleted": {}, "archived": {}}
        else:
            retention = SQLiteRetentionPruner(self.path).prune(rules)
            summary = summarize_retention_result(retention)
        if int(manager_run_retention_days) > 0:
            summary["active_manager_compaction"] = self.compact_active_manager_runs(
                recent_count=manager_run_recent_count,
                min_chars=manager_run_payload_min_chars,
            )
        else:
            summary["active_manager_compaction"] = {
                "status": "skipped",
                "reason": "manager_run_retention_disabled",
                "manager_runs": 0,
            }
        growth_snapshot_result = self.prune_growth_target_snapshots(
            retention_days=growth_snapshot_retention_days
        )
        summary["growth_target_snapshots_deleted"] = int(
            growth_snapshot_result.get("deleted") or 0
        )
        summary["growth_target_snapshot_retention"] = growth_snapshot_result
        return summary

    def prune_growth_target_snapshots(
        self,
        *,
        retention_days: int = 14,
    ) -> dict[str, Any]:
        days = int(retention_days)
        if days <= 0:
            return {
                "status": "skipped",
                "reason": "growth_snapshot_retention_disabled",
                "deleted": 0,
                "retention_days": days,
            }
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            deleted = int(
                conn.execute(
                    """
                    DELETE FROM growth_target_snapshots
                    WHERE created_at < ?
                      AND id NOT IN (
                        SELECT MAX(id)
                        FROM growth_target_snapshots
                        GROUP BY month_key
                      )
                    """,
                    (cutoff,),
                ).rowcount
                or 0
            )
        if deleted:
            with sqlite3.connect(self.path, isolation_level=None) as conn:
                conn.execute("VACUUM")
        return {
            "status": "ok",
            "deleted": deleted,
            "cutoff": cutoff,
            "retention_days": days,
            "keeps_latest_per_month": True,
            "vacuumed": bool(deleted),
        }

    def compact_active_manager_runs(
        self,
        *,
        recent_count: int = 96,
        min_chars: int = 20_000,
    ) -> dict[str, Any]:
        keep_recent = max(int(recent_count or 0), 0)
        threshold = max(int(min_chars or 0), 0)
        if threshold <= 0:
            return {
                "status": "skipped",
                "reason": "payload_compaction_disabled",
                "manager_runs": 0,
                "recent_count": keep_recent,
                "min_chars": threshold,
            }
        compacted = 0
        skipped_recent = 0
        skipped_small = 0
        skipped_already_compacted = 0
        compacted_at = utc_now_iso()
        with self._connect() as conn:
            recent_ids: set[int] = set()
            if keep_recent > 0:
                recent_rows = conn.execute(
                    """
                    SELECT id
                    FROM manager_runs
                    ORDER BY run_at DESC, id DESC
                    LIMIT ?
                    """,
                    (keep_recent,),
                ).fetchall()
                recent_ids = {int(row["id"]) for row in recent_rows}
            rows = conn.execute(
                """
                SELECT id, run_at, status, mode, model,
                       prompt_json, response_json, actions_json
                FROM manager_runs
                ORDER BY run_at ASC, id ASC
                """
            ).fetchall()
            for row in rows:
                run_id = int(row["id"])
                if run_id in recent_ids:
                    skipped_recent += 1
                    continue
                values = {
                    "prompt_json": str(row["prompt_json"] or "{}"),
                    "response_json": str(row["response_json"] or "{}"),
                    "actions_json": str(row["actions_json"] or "{}"),
                }
                if sum(len(value) for value in values.values()) < threshold:
                    skipped_small += 1
                    continue
                compacted_fields = {
                    field: self._is_manager_run_payload_compacted(value)
                    for field, value in values.items()
                }
                if all(compacted_fields.values()):
                    skipped_already_compacted += 1
                    continue
                next_values = {
                    field: (
                        value
                        if compacted_fields[field]
                        else _json_dumps(
                            self._manager_run_compaction_marker(
                                row,
                                field=field,
                                original_chars=len(value),
                                compacted_at=compacted_at,
                                recent_count=keep_recent,
                            )
                        )
                    )
                    for field, value in values.items()
                }
                conn.execute(
                    """
                    UPDATE manager_runs
                    SET prompt_json = ?, response_json = ?, actions_json = ?
                    WHERE id = ?
                    """,
                    (
                        next_values["prompt_json"],
                        next_values["response_json"],
                        next_values["actions_json"],
                        run_id,
                    ),
                )
                compacted += 1
        if compacted:
            with sqlite3.connect(self.path, isolation_level=None) as conn:
                conn.execute("VACUUM")
        return {
            "status": "ok",
            "manager_runs": compacted,
            "recent_count": keep_recent,
            "min_chars": threshold,
            "skipped_recent": skipped_recent,
            "skipped_small": skipped_small,
            "skipped_already_compacted": skipped_already_compacted,
            "vacuumed": bool(compacted),
        }

    @staticmethod
    def _is_manager_run_payload_compacted(value: str) -> bool:
        payload = _json_loads(value, {})
        return (
            isinstance(payload, dict)
            and payload.get("compacted") is True
            and payload.get("reason") == MANAGER_RUN_PAYLOAD_RETENTION_REASON
        )

    @staticmethod
    def _manager_run_compaction_marker(
        row: sqlite3.Row,
        *,
        field: str,
        original_chars: int,
        compacted_at: str,
        recent_count: int,
    ) -> dict[str, Any]:
        return {
            "compacted": True,
            "reason": MANAGER_RUN_PAYLOAD_RETENTION_REASON,
            "field": field,
            "run_id": int(row["id"]),
            "run_at": str(row["run_at"] or ""),
            "status": str(row["status"] or ""),
            "mode": str(row["mode"] or ""),
            "model": str(row["model"] or ""),
            "original_chars": int(original_chars),
            "compacted_at": compacted_at,
            "recent_run_count": int(recent_count),
        }

    def compact_legacy_archives(
        self,
        *,
        batch_size: int = 1000,
        vacuum: bool = True,
    ) -> dict[str, Any]:
        pruner = SQLiteRetentionPruner(self.path)
        tables = {
            "quote_snapshots_archive": pruner.compact_archive_columns(
                table="quote_snapshots_archive",
                columns=("raw_json",),
                batch_size=batch_size,
                vacuum=False,
            ),
            "manager_runs_archive": pruner.compact_archive_columns(
                table="manager_runs_archive",
                columns=("prompt_json", "response_json", "actions_json"),
                batch_size=batch_size,
                vacuum=False,
            ),
        }
        compacted = sum(int(row.get("compacted") or 0) for row in tables.values())
        vacuumed = False
        if vacuum and compacted:
            with sqlite3.connect(self.path, isolation_level=None) as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "tables": tables,
            "compacted": compacted,
            "vacuumed": vacuumed,
        }

    def save_manager_run(
        self,
        *,
        prompt: dict[str, Any],
        response: dict[str, Any],
        actions: dict[str, Any],
        status: str = "ok",
        mode: str = "llm",
        model: str = "",
        error_message: str = "",
    ) -> int:
        if isinstance(prompt, dict):
            response_payload = response if isinstance(response, dict) else {}
            action_payload = actions if isinstance(actions, dict) else {}
            hold_decision = (
                response_payload.get("hold_decision")
                if isinstance(response_payload.get("hold_decision"), dict)
                else {}
            )
            compact_context = build_compact_manager_prompt_context(
                prompt,
                response=response_payload,
                actions=action_payload,
                hold_decision=hold_decision,
            )
            if compact_context:
                prompt = dict(prompt)
                prompt["compact_manager_context"] = compact_context
                diagnostics = compact_context.get("diagnostics")
                if isinstance(diagnostics, dict) and diagnostics:
                    prompt["diagnostics"] = diagnostics
        provenance = build_manager_run_workflow_provenance(prompt)
        priority_candidate_keys = build_manager_action_candidate_keys(actions)
        stored_prompt = build_compact_manager_storage_payload(
            prompt,
            limit=MANAGER_PROMPT_STORAGE_LIMIT,
            label="binance_manager_prompt",
            priority_candidate_keys=priority_candidate_keys,
        )
        stored_response = build_compact_manager_storage_payload(
            response,
            limit=MANAGER_RESPONSE_STORAGE_LIMIT,
            label="binance_manager_response",
        )
        stored_actions = build_compact_manager_storage_payload(
            actions,
            limit=MANAGER_ACTIONS_STORAGE_LIMIT,
            label="binance_manager_actions",
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO manager_runs (
                    run_at, status, mode, model, error_message,
                    prompt_json, response_json, actions_json,
                    workflow_id, workflow_version, skill_ids_json, contract_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    str(status),
                    str(mode),
                    str(model),
                    str(error_message),
                    _json_dumps(stored_prompt),
                    _json_dumps(stored_response),
                    _json_dumps(stored_actions),
                    provenance["workflow_id"],
                    provenance["workflow_version"],
                    provenance["skill_ids_json"],
                    provenance["contract_ids_json"],
                ),
            )
            return int(cursor.lastrowid)

    def update_manager_run_response(
        self,
        manager_run_id: int,
        response: dict[str, Any],
    ) -> None:
        stored_response = build_compact_manager_storage_payload(
            response,
            limit=MANAGER_RESPONSE_STORAGE_LIMIT,
            label="binance_manager_response",
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE manager_runs SET response_json = ? WHERE id = ?",
                (_json_dumps(stored_response), int(manager_run_id)),
            )

    def list_blocks(
        self,
        *,
        include_closed: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM blocks"
        params: tuple[Any, ...] = ()
        if not include_closed:
            query += " WHERE status != 'closed'"
        query += " ORDER BY created_at DESC, block_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params = (max(int(limit), 1),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            build_row_to_block(
                row,
                compact_validation_repair=COMPACT_VALIDATION_REPAIR_FOR_STORAGE,
            )
            for row in rows
        ]

    def list_block_summaries(
        self,
        *,
        include_closed: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                block_id, symbol, market, side, qty_initial, qty_open,
                entry_price, target_price, stop_price, leverage, margin_type,
                liquidation_price, thesis, llm_reason, risk_note, created_by,
                manager_run_id, status, force_exit_requested, created_at,
                updated_at, opened_at, closed_at,
                json_extract(metadata_json, '$.horizon') AS metadata_horizon,
                json_extract(metadata_json, '$.block_color') AS metadata_block_color,
                json_extract(metadata_json, '$.lane') AS metadata_lane
            FROM blocks
        """
        params: tuple[Any, ...] = ()
        if not include_closed:
            query += " WHERE status != 'closed'"
        query += " ORDER BY created_at DESC, block_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params = (max(int(limit), 1),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            metadata = {
                key: row[source]
                for key, source in {
                    "horizon": "metadata_horizon",
                    "block_color": "metadata_block_color",
                    "lane": "metadata_lane",
                }.items()
                if row[source] not in (None, "")
            }
            row_payload = {
                key: row[key]
                for key in (
                    "block_id",
                    "symbol",
                    "market",
                    "side",
                    "qty_initial",
                    "qty_open",
                    "entry_price",
                    "target_price",
                    "stop_price",
                    "leverage",
                    "margin_type",
                    "liquidation_price",
                    "thesis",
                    "llm_reason",
                    "risk_note",
                    "created_by",
                    "manager_run_id",
                    "status",
                    "force_exit_requested",
                    "created_at",
                    "updated_at",
                    "opened_at",
                    "closed_at",
                )
            }
            row_payload["metadata_json"] = _json_dumps(metadata)
            summaries.append(
                build_row_to_block(
                    row_payload,
                    compact_validation_repair=COMPACT_VALIDATION_REPAIR_FOR_STORAGE,
                )
            )
        return summaries

    def list_recent_strategy_blocks(self, *, limit: int = 40) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM blocks
                WHERE COALESCE(created_by, '') NOT IN (
                    'wallet_adoption',
                    'existing_position'
                )
                ORDER BY created_at DESC, block_id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [
            build_row_to_block(
                row,
                compact_validation_repair=COMPACT_VALIDATION_REPAIR_FOR_STORAGE,
            )
            for row in rows
        ]

    def get_block(self, block_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM blocks WHERE block_id = ? LIMIT 1",
                (str(block_id),),
            ).fetchone()
        if row is None:
            return None
        return build_row_to_block(
            row,
            compact_validation_repair=COMPACT_VALIDATION_REPAIR_FOR_STORAGE,
        )

    def list_orders(self, block_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        if block_id:
            query = "SELECT * FROM block_orders WHERE block_id = ? ORDER BY id DESC LIMIT ?"
            params: tuple[Any, ...] = (str(block_id), max(int(limit), 1))
        else:
            query = "SELECT * FROM block_orders ORDER BY id DESC LIMIT ?"
            params = (max(int(limit), 1),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [build_row_to_order(row) for row in rows]

    def list_events(self, block_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        if block_id:
            query = "SELECT * FROM block_events WHERE block_id = ? ORDER BY id DESC LIMIT ?"
            params: tuple[Any, ...] = (str(block_id), max(int(limit), 1))
        else:
            query = "SELECT * FROM block_events ORDER BY id DESC LIMIT ?"
            params = (max(int(limit), 1),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [build_row_to_event(row) for row in rows]

    def quote_prices(
        self,
        *,
        symbol: str,
        market: str,
        start_at: str,
        end_at: str,
        limit: int = 2000,
    ) -> list[float]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT price FROM quote_snapshots
                WHERE symbol = ?
                  AND market = ?
                  AND fetched_at >= ?
                  AND fetched_at <= ?
                  AND price IS NOT NULL
                ORDER BY fetched_at ASC
                LIMIT ?
                """,
                (
                    str(symbol or "").upper(),
                    normalize_market(market),
                    str(start_at or ""),
                    str(end_at or utc_now_iso()),
                    max(int(limit), 1),
                ),
            ).fetchall()
        return [_safe_float(row["price"]) for row in rows if _safe_float(row["price"]) > 0]

    def latest_quotes_for_blocks(
        self,
        blocks: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        keys = sorted(
            {
                (
                    normalize_market(block.get("market")),
                    str(block.get("symbol") or "").upper(),
                )
                for block in blocks
                if str(block.get("symbol") or "").strip()
            }
        )
        if not keys:
            return {}
        quotes: dict[tuple[str, str], dict[str, Any]] = {}
        with self._connect() as conn:
            for market, symbol in keys:
                row = conn.execute(
                    """
                    SELECT symbol, market, price, source, fetched_at, status, error_message
                    FROM quote_snapshots
                    WHERE market = ?
                      AND symbol = ?
                    ORDER BY fetched_at DESC, id DESC
                    LIMIT 1
                    """,
                    (market, symbol),
                ).fetchone()
                if row is None:
                    continue
                quotes[(market, symbol)] = {
                    "symbol": row["symbol"],
                    "market": row["market"],
                    "price": float(row["price"] or 0.0),
                    "source": row["source"],
                    "fetched_at": row["fetched_at"],
                    "status": row["status"],
                    "error_message": row["error_message"],
                }
        return quotes

    def save_performance_reflection(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        created_at = str(
            payload.get("created_at")
            or payload.get("closed_at")
            or now
        ).strip()
        lesson = (
            dict(payload.get("lesson"))
            if isinstance(payload.get("lesson"), dict)
            else {}
        )
        present_cost_components = [
            str(component)
            for component in list(payload.get("present_cost_components") or [])
            if str(component).strip()
        ]
        if present_cost_components and "present_cost_components" not in lesson:
            lesson["present_cost_components"] = present_cost_components
        row = {
            "block_id": str(payload.get("block_id") or ""),
            "symbol": str(payload.get("symbol") or "").upper(),
            "market": normalize_market(payload.get("market")),
            "side": normalize_position_side(payload.get("side")),
            "entry_price": _safe_float(payload.get("entry_price")),
            "exit_price": _safe_float(payload.get("exit_price")),
            "stop_price": _safe_float(payload.get("stop_price")),
            "target_price": _safe_float(payload.get("target_price")),
            "pnl_usdt": _safe_float(payload.get("pnl_usdt")),
            "gross_pnl_usdt": _safe_float(payload.get("gross_pnl_usdt") or payload.get("pnl_usdt")),
            "net_pnl_usdt": _safe_float(payload.get("net_pnl_usdt") or payload.get("pnl_usdt")),
            "fee_usdt": _safe_float(payload.get("fee_usdt")),
            "funding_usdt": _safe_float(payload.get("funding_usdt")),
            "slippage_usdt": _safe_float(payload.get("slippage_usdt")),
            "spread_usdt": _safe_float(payload.get("spread_usdt")),
            "cost_source": str(payload.get("cost_source") or "").strip(),
            "r_multiple": _safe_float(payload.get("r_multiple")),
            "mfe_r_multiple": _safe_float(payload.get("mfe_r_multiple")),
            "mae_r_multiple": _safe_float(payload.get("mae_r_multiple")),
            "pattern_key": str(payload.get("pattern_key") or "").strip(),
            "lesson_json": _json_dumps(lesson),
            "created_at": created_at or now,
        }
        row["lane"] = binance_performance_lane_from_payload(row, payload)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO block_performance_reflections (
                    block_id, symbol, market, side, lane, entry_price, exit_price,
                    stop_price, target_price, pnl_usdt, gross_pnl_usdt,
                    net_pnl_usdt, fee_usdt, funding_usdt, slippage_usdt,
                    spread_usdt,
                    cost_source, r_multiple,
                    mfe_r_multiple, mae_r_multiple, pattern_key, lesson_json,
                    created_at
                )
                VALUES (
                    :block_id, :symbol, :market, :side, :lane, :entry_price, :exit_price,
                    :stop_price, :target_price, :pnl_usdt, :gross_pnl_usdt,
                    :net_pnl_usdt, :fee_usdt, :funding_usdt, :slippage_usdt,
                    :spread_usdt,
                    :cost_source, :r_multiple,
                    :mfe_r_multiple, :mae_r_multiple, :pattern_key, :lesson_json,
                    :created_at
                )
                ON CONFLICT(block_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    side = excluded.side,
                    lane = excluded.lane,
                    entry_price = excluded.entry_price,
                    exit_price = excluded.exit_price,
                    stop_price = excluded.stop_price,
                    target_price = excluded.target_price,
                    pnl_usdt = excluded.pnl_usdt,
                    gross_pnl_usdt = excluded.gross_pnl_usdt,
                    net_pnl_usdt = excluded.net_pnl_usdt,
                    fee_usdt = excluded.fee_usdt,
                    funding_usdt = excluded.funding_usdt,
                    slippage_usdt = excluded.slippage_usdt,
                    spread_usdt = excluded.spread_usdt,
                    cost_source = excluded.cost_source,
                    r_multiple = excluded.r_multiple,
                    mfe_r_multiple = excluded.mfe_r_multiple,
                    mae_r_multiple = excluded.mae_r_multiple,
                    pattern_key = excluded.pattern_key,
                    lesson_json = excluded.lesson_json,
                    created_at = excluded.created_at
                """,
                row,
            )
        return self.get_performance_reflection(row["block_id"]) or row

    def get_performance_reflection(self, block_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM block_performance_reflections
                WHERE block_id = ?
                LIMIT 1
                """,
                (str(block_id),),
            ).fetchone()
        if row is None:
            return None
        return build_row_to_performance_reflection(
            row,
            canonical_performance_lane=canonical_binance_performance_lane,
            entry_quality_label=build_entry_quality_label_from_payload,
        )

    def performance_reflections_for_blocks(
        self,
        block_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        keys = [str(block_id or "").strip() for block_id in block_ids]
        keys = [block_id for block_id in dict.fromkeys(keys) if block_id]
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM block_performance_reflections
                WHERE block_id IN ({placeholders})
                """,
                keys,
            ).fetchall()
        return {
            str(row["block_id"]): build_row_to_performance_reflection(
                row,
                canonical_performance_lane=canonical_binance_performance_lane,
                entry_quality_label=build_entry_quality_label_from_payload,
            )
            for row in rows
        }

    def latest_performance_scorecard(self, *, limit: int = 20) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, b.metadata_json AS block_metadata_json,
                       b.market AS block_market,
                       b.thesis AS block_thesis,
                       b.llm_reason AS block_llm_reason
                FROM block_performance_reflections r
                LEFT JOIN blocks b ON b.block_id = r.block_id
                WHERE COALESCE(b.created_by, '') NOT IN (
                    'wallet_adoption',
                    'existing_position'
                )
                ORDER BY COALESCE(NULLIF(b.closed_at, ''), r.created_at) DESC,
                    r.created_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        clean_rows, excluded_rows = build_partition_performance_rows(rows)
        reflections = [
            build_row_to_performance_reflection(
                row,
                canonical_performance_lane=canonical_binance_performance_lane,
                entry_quality_label=build_entry_quality_label_from_payload,
            )
            for row in clean_rows
        ]
        scorecard = build_performance_scorecard_from_reflections(reflections)
        scorecard["window"] = {
            "kind": "latest_by_block_close",
            "limit": max(int(limit), 1),
            "excluded_created_by": ["wallet_adoption", "existing_position"],
            "excluded_execution_defect_count": len(excluded_rows),
            "excluded_execution_defect_pnl_usdt": sum(
                _safe_float(row["net_pnl_usdt"] or row["pnl_usdt"])
                for row in excluded_rows
            ),
        }
        scorecard["execution_defect_risk"] = build_execution_defect_risk_from_rows(
            excluded_rows
        )
        return scorecard

    def performance_scorecard_for_pattern_key(
        self,
        pattern_key: str,
        *,
        limit: int = 80,
    ) -> dict[str, Any] | None:
        key = str(pattern_key or "").strip()
        if not key:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, b.metadata_json AS block_metadata_json,
                       b.market AS block_market,
                       b.thesis AS block_thesis,
                       b.llm_reason AS block_llm_reason
                FROM block_performance_reflections r
                LEFT JOIN blocks b ON b.block_id = r.block_id
                WHERE lower(r.pattern_key) = lower(?)
                  AND COALESCE(b.created_by, '') NOT IN (
                    'wallet_adoption',
                    'existing_position'
                  )
                ORDER BY COALESCE(NULLIF(b.closed_at, ''), r.created_at) DESC,
                    r.created_at DESC
                LIMIT ?
                """,
                (key, max(int(limit), 1)),
            ).fetchall()
        clean_rows, _excluded_rows = build_partition_performance_rows(rows)
        if not clean_rows:
            return None
        reflections = [
            build_row_to_performance_reflection(
                row,
                canonical_performance_lane=canonical_binance_performance_lane,
                entry_quality_label=build_entry_quality_label_from_payload,
            )
            for row in clean_rows
        ]
        for card in build_performance_pattern_scorecards(reflections):
            if str(card.get("pattern_key") or "").strip().lower() == key.lower():
                return card
        return None

    def performance_scorecard_for_symbol(
        self,
        symbol: str,
        *,
        limit: int = 80,
    ) -> dict[str, Any] | None:
        key = str(symbol or "").upper().strip()
        if not key:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, b.metadata_json AS block_metadata_json,
                       b.market AS block_market,
                       b.thesis AS block_thesis,
                       b.llm_reason AS block_llm_reason
                FROM block_performance_reflections r
                LEFT JOIN blocks b ON b.block_id = r.block_id
                WHERE upper(r.symbol) = upper(?)
                  AND COALESCE(b.created_by, '') NOT IN (
                    'wallet_adoption',
                    'existing_position'
                  )
                ORDER BY COALESCE(NULLIF(b.closed_at, ''), r.created_at) DESC,
                    r.created_at DESC
                LIMIT ?
                """,
                (key, max(int(limit), 1)),
            ).fetchall()
        clean_rows, _excluded_rows = build_partition_performance_rows(rows)
        if not clean_rows:
            return None
        reflections = [
            build_row_to_performance_reflection(
                row,
                canonical_performance_lane=canonical_binance_performance_lane,
                entry_quality_label=build_entry_quality_label_from_payload,
            )
            for row in clean_rows
        ]
        for card in build_performance_group_scorecards(
            reflections,
            key_name="symbol",
            key_fn=lambda row: str(row.get("symbol") or "UNKNOWN").upper(),
        ):
            if str(card.get("symbol") or "").upper().strip() == key:
                return card
        return None

    def today_performance_scorecard(self) -> dict[str, Any]:
        now = datetime.now(KST)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        start_at = start.astimezone(timezone.utc).isoformat()
        end_at = end.astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, b.metadata_json AS block_metadata_json,
                       b.market AS block_market,
                       b.thesis AS block_thesis,
                       b.llm_reason AS block_llm_reason
                FROM block_performance_reflections r
                JOIN blocks b ON b.block_id = r.block_id
                WHERE b.closed_at >= ?
                  AND b.closed_at < ?
                  AND COALESCE(b.created_by, '') NOT IN (
                    'wallet_adoption',
                    'existing_position'
                  )
                ORDER BY b.closed_at DESC, r.created_at DESC
                """,
                (start_at, end_at),
            ).fetchall()
        clean_rows, excluded_rows = build_partition_performance_rows(rows)
        reflections = [
            build_row_to_performance_reflection(
                row,
                canonical_performance_lane=canonical_binance_performance_lane,
                entry_quality_label=build_entry_quality_label_from_payload,
            )
            for row in clean_rows
        ]
        scorecard = build_performance_scorecard_from_reflections(reflections)
        scorecard["window"] = {
            "kind": "kst_day_by_block_close",
            "start_at": start_at,
            "end_at": end_at,
            "excluded_created_by": ["wallet_adoption", "existing_position"],
            "excluded_execution_defect_count": len(excluded_rows),
            "excluded_execution_defect_pnl_usdt": sum(
                _safe_float(row["net_pnl_usdt"] or row["pnl_usdt"])
                for row in excluded_rows
            ),
        }
        scorecard["execution_defect_risk"] = build_execution_defect_risk_from_rows(
            excluded_rows
        )
        return scorecard

    def list_manager_runs(
        self,
        *,
        limit: int = 5,
        include_payload: bool = True,
        compact_payload: bool = False,
    ) -> list[dict[str, Any]]:
        if include_payload:
            select_clause = "*"
        elif compact_payload:
            select_clause = """
                id, run_at, status, mode, model, error_message,
                '{}' AS prompt_json, response_json, actions_json,
                workflow_id, workflow_version, skill_ids_json, contract_ids_json
            """
        else:
            select_clause = """
                id, run_at, status, mode, model, error_message,
                '{}' AS prompt_json, '{}' AS response_json, '{}' AS actions_json,
                workflow_id, workflow_version, skill_ids_json, contract_ids_json
            """
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {select_clause} FROM manager_runs
                ORDER BY run_at DESC, id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [build_row_to_manager_run(row) for row in rows]

    def latest_manager_run(self, *, include_payload: bool = True) -> dict[str, Any]:
        rows = self.list_manager_runs(limit=1, include_payload=include_payload)
        return rows[0] if rows else {"status": "missing"}

    def get_kill_switch(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled, reason, updated_at FROM kill_switch WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"enabled": False}
        return {
            "enabled": bool(row["enabled"]),
            "reason": row["reason"],
            "updated_at": row["updated_at"],
        }

    def set_kill_switch(self, enabled: bool, *, reason: str = "") -> dict[str, Any]:
        payload = {
            "enabled": 1 if enabled else 0,
            "reason": str(reason or ""),
            "updated_at": utc_now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kill_switch (id, enabled, reason, updated_at)
                VALUES (1, :enabled, :reason, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                payload,
            )
        return self.get_kill_switch()

    def growth_month_start_equity(
        self,
        *,
        month_key: str,
        current_equity_usdt: float,
    ) -> float:
        equity = max(_safe_float(current_equity_usdt), 0.0)
        if not month_key or equity <= 0:
            return 0.0
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO growth_target_months (
                    month_key, start_equity_usdt, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (month_key, equity, now, now),
            )
            row = conn.execute(
                """
                SELECT start_equity_usdt
                FROM growth_target_months
                WHERE month_key = ?
                """,
                (month_key,),
            ).fetchone()
        return _safe_float(row["start_equity_usdt"]) if row else equity

    def save_growth_target_snapshot(
        self,
        *,
        month_key: str,
        payload: dict[str, Any],
    ) -> None:
        if not month_key or not isinstance(payload, dict):
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO growth_target_snapshots (
                    month_key, start_equity_usdt, current_equity_usdt,
                    target_equity_usdt, current_return_pct, remaining_return_pct,
                    required_daily_return_pct, status, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    month_key,
                    _safe_float(payload.get("start_equity_usdt")),
                    _safe_float(payload.get("current_equity_usdt")),
                    _safe_float(payload.get("target_equity_usdt")),
                    _safe_float(payload.get("current_return_pct")),
                    _safe_float(payload.get("remaining_return_pct")),
                    _safe_float(payload.get("required_daily_return_pct")),
                    str(payload.get("status") or ""),
                    _json_dumps(payload),
                    utc_now_iso(),
                ),
            )

    def equity_baseline(
        self,
        period_key: str,
        *,
        current_equity_usdt: float,
    ) -> float:
        key = str(period_key or "").strip()
        if not key:
            return 0.0
        now = utc_now_iso()
        equity = max(_safe_float(current_equity_usdt), 0.0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO equity_baselines (
                    period_key, start_equity_usdt, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (key, equity, now, now),
            )
            row = conn.execute(
                """
                SELECT start_equity_usdt
                FROM equity_baselines
                WHERE period_key = ?
                """,
                (key,),
            ).fetchone()
        return float(row["start_equity_usdt"] or 0.0) if row else 0.0

    def get_equity_baseline(self, period_key: str) -> float:
        key = str(period_key or "").strip()
        if not key:
            return 0.0
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT start_equity_usdt
                FROM equity_baselines
                WHERE period_key = ?
                """,
                (key,),
            ).fetchone()
        return float(row["start_equity_usdt"] or 0.0) if row else 0.0

    def set_equity_baseline(
        self,
        period_key: str,
        *,
        start_equity_usdt: float,
    ) -> float:
        key = str(period_key or "").strip()
        if not key:
            return 0.0
        now = utc_now_iso()
        equity = max(_safe_float(start_equity_usdt), 0.0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO equity_baselines (
                    period_key, start_equity_usdt, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(period_key) DO UPDATE SET
                    start_equity_usdt = excluded.start_equity_usdt,
                    updated_at = excluded.updated_at
                """,
                (key, equity, now, now),
            )
        return equity

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            block_count = int(conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])
            open_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM blocks
                    WHERE status IN ('entry_pending','open','exit_pending')
                    """
                ).fetchone()[0]
            )
            proposed_count = int(
                conn.execute("SELECT COUNT(*) FROM blocks WHERE status = 'proposed'").fetchone()[0]
            )
            order_count = int(conn.execute("SELECT COUNT(*) FROM block_orders").fetchone()[0])
            manager_count = int(conn.execute("SELECT COUNT(*) FROM manager_runs").fetchone()[0])
            latest_run = conn.execute(
                "SELECT run_at, status, mode FROM manager_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
            latest_error_run = conn.execute(
                """
                SELECT run_at, status, mode, error_message
                FROM manager_runs
                WHERE status NOT IN ('ok', 'success') OR error_message != ''
                ORDER BY run_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        latest_manager_error = (
            {
                "run_at": str(latest_error_run["run_at"]),
                "status": str(latest_error_run["status"]),
                "mode": str(latest_error_run["mode"]),
                "error_message": str(latest_error_run["error_message"]),
            }
            if latest_error_run
            else {}
        )
        latest_manager_error_recovered = bool(
            latest_run
            and latest_error_run
            and str(latest_run["status"]).lower() in {"ok", "success"}
            and str(latest_run["run_at"]) > str(latest_error_run["run_at"])
        )
        latest_unresolved_manager_error = (
            {}
            if not latest_manager_error or latest_manager_error_recovered
            else latest_manager_error
        )
        latest_status = str(latest_run["status"]) if latest_run else "missing"
        if latest_status == "missing":
            manager_operational_status = "awaiting_first_manager_run"
        elif latest_status.lower() in {"ok", "success"}:
            manager_operational_status = "ok"
        elif latest_unresolved_manager_error:
            manager_operational_status = "manager_error_pending_next_run"
        else:
            manager_operational_status = "review_latest_manager_run"
        return {
            "status": "ok",
            "db_path": str(self.path),
            "block_count": block_count,
            "open_block_count": open_count,
            "proposed_block_count": proposed_count,
            "order_count": order_count,
            "manager_run_count": manager_count,
            "latest_manager_run_at": str(latest_run["run_at"]) if latest_run else "",
            "latest_manager_status": latest_status,
            "manager_operational_status": manager_operational_status,
            "latest_manager_mode": str(latest_run["mode"]) if latest_run else "",
            "latest_manager_error": latest_manager_error,
            "latest_manager_error_recovered": latest_manager_error_recovered,
            "latest_unresolved_manager_error": latest_unresolved_manager_error,
        }

    def _new_block_id(self, symbol: str, market: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        safe_symbol = symbol or "UNKNOWN"
        return f"bnb_{market}_{safe_symbol}_{stamp}"

class BinanceBlockTrader:
    def __init__(
        self,
        *,
        config: BinanceBlockTraderConfig,
        adapter: Any | None = None,
        binance: Any | None = None,
        upbit: Any | None = None,
        codex_runtime: Any | None = None,
        memory_provider: Callable[..., dict[str, Any] | None] | None = None,
        memory_context_provider: Callable[..., dict[str, Any] | None] | None = None,
        wiki_context_provider: Callable[..., dict[str, Any]] | None = None,
        crypto_research_provider: Any | None = None,
        crypto_alpha_provider: Any | None = None,
        quant_provider: Any | None = None,
        crypto_pattern_provider: Any | None = None,
        live_authority_provider: Callable[[], dict[str, Any] | None] | None = None,
        risk_sizer: Any | None = None,
        telegram: Any | None = None,
    ) -> None:
        self.config = config
        self.adapter = adapter or binance
        self.upbit = upbit
        self.codex_runtime = codex_runtime
        self.memory_provider = memory_provider or memory_context_provider
        self.wiki_context_provider = wiki_context_provider
        self.crypto_research_provider = crypto_research_provider
        self.crypto_alpha_provider = crypto_alpha_provider
        self.quant_provider = quant_provider
        self.crypto_pattern_provider = crypto_pattern_provider
        self.live_authority_provider = live_authority_provider
        self.risk_sizer = risk_sizer
        self.telegram = telegram
        self._last_account_snapshot: dict[str, Any] = {}
        self._runtime_market_universe: dict[str, list[str]] = self._default_market_universe()
        self._last_manager_candidate_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._last_manager_quant_context: dict[str, Any] = {}
        self._last_manager_entry_gate_policy: dict[str, Any] = {}
        self._last_manager_growth_governor: dict[str, Any] = {}
        self._last_manager_growth_unlock: dict[str, Any] = {}
        self._last_manager_policy_impacts: dict[str, list[dict[str, Any]]] = {}
        self._last_live_authority_context: dict[str, Any] = {}
        self._last_live_authority_context_expires_at = 0.0
        self.repository = BinanceBlockRepository(config.db_path)
        self.live_performance_repository = LivePerformanceRepository(
            config.live_performance_db_path
        )

    def _adapter_for_market(self, market: str) -> Any | None:
        if is_upbit_market(market):
            return self.upbit
        return self.adapter

    def _live_authority_context(self) -> dict[str, Any]:
        if self.live_authority_provider is None:
            return {"status": "missing", "reason": "provider_not_configured"}
        now = time.monotonic()
        if (
            self._last_live_authority_context
            and now < self._last_live_authority_context_expires_at
        ):
            return dict(self._last_live_authority_context)
        try:
            payload = self.live_authority_provider()
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
        if not isinstance(payload, dict):
            return {"status": "missing", "reason": "provider_returned_non_dict"}
        if str(payload.get("status") or "").strip().lower() != "error":
            self._last_live_authority_context = dict(payload)
            self._last_live_authority_context_expires_at = (
                time.monotonic() + BINANCE_LIVE_AUTHORITY_STATUS_CACHE_TTL_SEC
            )
        return payload

    def _live_authority_metadata(self) -> dict[str, Any]:
        payload = self._live_authority_context()
        prompt_payload = compact_live_authority_for_prompt(payload)
        prompt_gate = (
            prompt_payload.get("validation_gate")
            if isinstance(prompt_payload.get("validation_gate"), dict)
            else {}
        )
        validation_gate = (
            payload.get("validation_gate")
            if isinstance(payload.get("validation_gate"), dict)
            else {}
        )
        metadata = {
            "status": payload.get("status"),
            "live_grade": payload.get("live_grade"),
            "max_budget_multiplier": payload.get("max_budget_multiplier"),
            "allow_scale_up": bool(payload.get("allow_scale_up")),
            "scorecard_count": payload.get("scorecard_count"),
            "validation_gate_status": validation_gate.get("status"),
            "validation_readiness": validation_gate.get("readiness"),
            "validation_gate_reason": validation_gate.get("reason"),
            "risk_governor_action": validation_gate.get("risk_governor_action"),
            "risk_governor_source": validation_gate.get("risk_governor_source"),
        }
        if prompt_payload.get("lane_authority"):
            metadata["lane_authority"] = prompt_payload.get("lane_authority")
        if prompt_payload.get("active_revision_evidence"):
            metadata["active_revision_evidence"] = prompt_payload.get(
                "active_revision_evidence"
            )
        for key in (
            "discipline_matrix",
            "validation_passport",
            "validation_pressure",
            "cost_attribution",
            "failed_disciplines",
            "weak_disciplines",
            "capacity_bottleneck",
            "failure_attribution",
            "loss_cooldown",
            "validation_recovery_focus",
            "operator_guidance",
            "risk_governor_reasons",
            "remediation_plan",
        ):
            if prompt_gate.get(key):
                metadata[key] = prompt_gate.get(key)
            elif validation_gate.get(key):
                metadata[key] = validation_gate.get(key)
        return metadata

    @staticmethod
    def _live_authority_validation_gate(
        live_authority: dict[str, Any] | None,
    ) -> dict[str, str]:
        return build_live_authority_validation_gate(live_authority)

    @staticmethod
    def _active_revision_waiting_entry_reason(
        live_authority: dict[str, Any] | None,
    ) -> str:
        return build_active_revision_waiting_entry_reason(live_authority)

    @staticmethod
    def _active_revision_budget_multiplier(
        live_authority: dict[str, Any] | None,
    ) -> float | None:
        return build_active_revision_budget_multiplier(live_authority)

    @classmethod
    def _live_authority_create_gate(
        cls,
        live_authority: dict[str, Any] | None,
        *,
        waiting_entry: bool,
    ) -> dict[str, Any]:
        _ = cls
        return build_live_authority_create_gate(
            live_authority,
            waiting_entry=waiting_entry,
        )

    @staticmethod
    def _lane_authority_evidence_fields(
        lane_detail: dict[str, Any],
        risk_budget_passport: dict[str, Any],
    ) -> dict[str, Any]:
        return build_lane_authority_evidence_fields(
            lane_detail,
            risk_budget_passport,
        )

    @classmethod
    def _live_authority_lane_gate(
        cls,
        live_authority: dict[str, Any] | None,
        row: dict[str, Any],
        *,
        waiting_entry: bool,
    ) -> dict[str, Any]:
        payload = live_authority if isinstance(live_authority, dict) else {}
        lane_authority = (
            payload.get("lane_authority")
            if isinstance(payload.get("lane_authority"), dict)
            else {}
        )
        row_lanes = cls._growth_governor_row_lanes(row)
        if not lane_authority:
            performance_gate = cls._live_authority_performance_lane_gate(
                payload,
                row_lanes,
                row=row,
                waiting_entry=waiting_entry,
            )
            if performance_gate:
                return performance_gate
            return {"ok": True, "matched_lanes": [], "lane_authority": {}}
        weak_lanes = {
            str(item or "").strip().lower()
            for item in lane_authority.get("weak_lanes") or []
            if str(item or "").strip()
        }
        weak_lane_groups: dict[str, set[str]] = {}
        for key in (
            "cost_weak_lanes",
            "cost_evidence_weak_lanes",
            "entry_quality_weak_lanes",
            "validation_evidence_weak_lanes",
            "validation_repair_weak_lanes",
        ):
            weak_lane_groups[key] = {
                str(item or "").strip().lower()
                for item in lane_authority.get(key) or []
                if str(item or "").strip()
            }
            weak_lanes.update(weak_lane_groups[key])
        validation_evidence_weak_lanes = weak_lane_groups[
            "validation_evidence_weak_lanes"
        ]
        insufficient_lanes = {
            str(item or "").strip().lower()
            for item in lane_authority.get("insufficient_lanes") or []
            if str(item or "").strip()
        }
        scale_candidate_lanes = {
            str(item or "").strip().lower()
            for item in lane_authority.get("scale_candidate_lanes") or []
            if str(item or "").strip()
        }
        qualified_lanes = {
            str(item or "").strip().lower()
            for item in lane_authority.get("qualified_lanes") or []
            if str(item or "").strip()
        }
        matched_weak = sorted(row_lanes.intersection(weak_lanes))
        matched_insufficient = sorted(row_lanes.intersection(insufficient_lanes))
        matched_scale_candidate = sorted(row_lanes.intersection(scale_candidate_lanes))
        matched_qualified = sorted(row_lanes.intersection(qualified_lanes))
        matched_lanes = (
            matched_weak
            or matched_insufficient
            or matched_scale_candidate
            or matched_qualified
        )
        lane_context = cls._candidate_lane_authority_context(payload, row)
        context_lane = str(lane_context.get("lane") or "").strip().lower()
        if not matched_lanes and context_lane:
            matched_lanes = [context_lane]
            context_grade = str(lane_context.get("grade") or "").strip().lower()
            context_bias = str(
                lane_context.get("selection_bias") or ""
            ).strip().lower()
            context_is_weak = context_bias == "avoid_weak_lane" or context_grade in {
                "weak",
                "observe_only",
                "restricted",
            }
            matched_weak = [context_lane] if context_is_weak else []
            matched_insufficient = (
                [context_lane]
                if context_grade == "insufficient" and not context_is_weak
                else []
            )
            matched_scale_candidate = (
                [context_lane] if context_grade == "scale_candidate" else []
            )
            matched_qualified = [context_lane] if context_grade == "qualified" else []
        if not matched_lanes:
            performance_gate = cls._live_authority_performance_lane_gate(
                payload,
                row_lanes,
                row=row,
                waiting_entry=waiting_entry,
            )
            if performance_gate:
                return performance_gate
        lane_actions = (
            lane_authority.get("lane_actions")
            if isinstance(lane_authority.get("lane_actions"), dict)
            else {}
        )
        lane_detail: dict[str, Any] = {}
        matched_lane = matched_lanes[0] if matched_lanes else ""
        if matched_lane and isinstance(lane_actions.get(matched_lane), dict):
            lane_detail = dict(lane_actions.get(matched_lane) or {})
        elif context_lane and matched_lane == context_lane:
            lane_detail = dict(lane_context)
        applied_budget_multiplier = _safe_float(
            lane_detail.get("applied_max_budget_multiplier")
        )
        max_budget_multiplier = _safe_float(lane_detail.get("max_budget_multiplier"))
        risk_budget_passport = (
            lane_detail.get("risk_budget_passport")
            if isinstance(lane_detail.get("risk_budget_passport"), dict)
            else {}
        )
        passport_budget_multiplier = _safe_float(
            risk_budget_passport.get("effective_risk_budget_multiplier")
            or risk_budget_passport.get("applied_risk_budget_multiplier")
        )
        evidence_fields = cls._lane_authority_evidence_fields(
            lane_detail,
            risk_budget_passport,
        )
        weak_lane_sources = sorted(
            key
            for key, lanes in weak_lane_groups.items()
            if matched_lane in lanes
        )
        scale_blockers = {
            str(item)
            for item in list(evidence_fields.get("scale_blockers") or [])
            if str(item).strip()
        }
        if (
            "cost_evidence_repair" in scale_blockers
            or "cost_evidence_weak_lanes" in weak_lane_sources
        ):
            evidence_fields.setdefault("scale_blocked_by_cost_precision", True)
            evidence_fields.setdefault("scale_blocked_by_cost_evidence", True)
        if "verified_edge_sample_cap" in scale_blockers:
            evidence_fields.setdefault("scale_blocked_by_verified_edge_samples", True)
        if "verified_edge_net_pnl_cap" in scale_blockers:
            evidence_fields.setdefault("scale_blocked_by_verified_edge_net_pnl", True)
        budget_multiplier_sources = [
            ("applied_max_budget_multiplier", applied_budget_multiplier),
            ("max_budget_multiplier", max_budget_multiplier),
            ("risk_budget_passport", passport_budget_multiplier),
        ]
        valid_budget_multipliers = [
            (source, value)
            for source, value in budget_multiplier_sources
            if value > 0
        ]
        if valid_budget_multipliers:
            budget_multiplier_source, budget_multiplier = min(
                valid_budget_multipliers,
                key=lambda item: item[1],
            )
        else:
            budget_multiplier_source = ""
            budget_multiplier = (
                0.25
                if matched_weak
                else 0.5
                if matched_insufficient
                else 1.0
            )
        lane_action = str(lane_detail.get("action") or "").strip().lower()
        if not lane_action:
            lane_action = (
                "observe_or_waiting_entry"
                if matched_weak
                else "small_probe_until_sample_builds"
                if matched_insufficient
                else "eligible_to_press_when_validation_clear"
                if matched_scale_candidate
                else "normal_or_selective_press"
                if matched_qualified
                else ""
            )
        requires_waiting_entry = bool(
            matched_weak or lane_detail.get("requires_waiting_entry")
        )
        result = {
            **evidence_fields,
            "ok": True,
            "matched_lanes": matched_lanes,
            "weak_lanes": matched_weak,
            "insufficient_lanes": matched_insufficient,
            "row_lanes": sorted(row_lanes),
            "lane_action": lane_action,
            "requires_waiting_entry": requires_waiting_entry,
            "budget_multiplier": budget_multiplier,
            "max_budget_multiplier": max_budget_multiplier,
            "applied_max_budget_multiplier": applied_budget_multiplier,
            "risk_budget_passport_multiplier": passport_budget_multiplier,
            "budget_multiplier_source": budget_multiplier_source,
            "scale_up_allowed": bool(lane_detail.get("scale_up_allowed")),
            "selection_bias": str(
                lane_context.get("selection_bias") or ""
            ).strip(),
            "expectancy_pct": _safe_float(lane_context.get("expectancy_pct")),
            "profit_factor": _safe_float(lane_context.get("profit_factor")),
            "sample_count": _safe_int(lane_context.get("sample_count")),
            "validation_evidence_status": str(
                lane_detail.get("validation_evidence_status") or ""
            ).strip(),
            "validation_missing_dimensions": [
                str(item)
                for item in list(
                    lane_detail.get("validation_missing_dimensions") or []
                )[:4]
                if str(item).strip()
            ],
            "validation_failed_dimensions": [
                str(item)
                for item in list(
                    lane_detail.get("validation_failed_dimensions") or []
                )[:4]
                if str(item).strip()
            ],
            "scale_blocked_by_validation_evidence": bool(
                lane_detail.get("scale_blocked_by_validation_evidence")
                or risk_budget_passport.get("scale_blocked_by_validation_evidence")
                or matched_lane in validation_evidence_weak_lanes
            ),
            "weak_lane_sources": weak_lane_sources,
            "entry_quality_requirements": [
                str(item)
                for item in list(
                    lane_detail.get("entry_quality_requirements") or []
                )[:4]
                if str(item).strip()
            ],
            "lane_authority": {
                "version": lane_authority.get("version"),
                "max_budget_multiplier": lane_authority.get("max_budget_multiplier"),
                "global_scale_up_allowed": lane_authority.get(
                    "global_scale_up_allowed"
                ),
            },
        }
        if requires_waiting_entry and not waiting_entry:
            return {
                **result,
                "ok": False,
                "reason": "lane_authority_requires_waiting_entry",
            }
        return result

    @staticmethod
    def _live_authority_performance_lane_gate(
        live_authority: dict[str, Any],
        row_lanes: set[str],
        *,
        row: dict[str, Any],
        waiting_entry: bool,
    ) -> dict[str, Any]:
        rows = live_authority.get("performance_lanes")
        if not isinstance(rows, list):
            return {}

        def lane_variants(value: Any) -> set[str]:
            raw = str(value or "").strip().lower()
            if not raw:
                return set()
            compact = re.sub(r"[\s/]+", "_", raw)
            variants = {compact, compact.replace("_", ":"), compact.replace(":", "_")}
            if compact.startswith("futures_"):
                variants.add(compact.replace("futures_", "futures:", 1))
            if compact.startswith("spot:") or compact.startswith("spot_"):
                variants.add("spot")
            if compact.startswith("upbit_spot:") or compact.startswith("upbit_spot_"):
                variants.add("upbit_spot")
            return {item for item in variants if item}

        candidate_lanes: set[str] = set()
        for lane in row_lanes:
            candidate_lanes.update(lane_variants(lane))
        matches: list[tuple[int, dict[str, Any], str]] = []
        priority = {
            "weak_review": 0,
            "sample_building": 1,
            "no_alpha_samples": 2,
            "scale_candidate": 3,
            "qualified": 4,
        }
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            venue = str(raw.get("venue") or "").strip().lower()
            if venue and venue != "binance":
                continue
            lane_candidates = lane_variants(raw.get("lane"))
            quality_hint = str(raw.get("quality_hint") or "").strip().lower()
            if quality_hint not in priority:
                continue
            if not lane_candidates.intersection(candidate_lanes):
                continue
            matched_lane = sorted(lane_candidates.intersection(candidate_lanes))[0]
            matches.append((priority[quality_hint], raw, matched_lane))
        if not matches:
            return {}
        _, lane_row, matched_lane = sorted(matches, key=lambda item: item[0])[0]
        quality_hint = str(lane_row.get("quality_hint") or "").strip().lower()
        action_hint = str(lane_row.get("action_hint") or "").strip().lower()
        weak = quality_hint == "weak_review"
        scale_candidate = quality_hint == "scale_candidate"
        qualified = quality_hint == "qualified"
        validation_gate = (
            live_authority.get("validation_gate")
            if isinstance(live_authority.get("validation_gate"), dict)
            else {}
        )
        validation_status = str(validation_gate.get("status") or "").strip().lower()
        lane_authority = (
            live_authority.get("lane_authority")
            if isinstance(live_authority.get("lane_authority"), dict)
            else {}
        )
        validation_shadow_gate = (
            lane_authority.get("validation_shadow_gate")
            if isinstance(lane_authority.get("validation_shadow_gate"), dict)
            else {}
        )
        validation_exposure_gate = (
            lane_authority.get("validation_exposure_gate")
            if isinstance(lane_authority.get("validation_exposure_gate"), dict)
            else {}
        )
        shadow_blocks_scale = build_truthy_gate_value(
            validation_shadow_gate.get("blocks_scale_up")
        ) or build_truthy_gate_value(validation_shadow_gate.get("requires_waiting_entry"))
        exposure_blocks_scale = build_truthy_gate_value(
            validation_exposure_gate.get("blocks_scale_up")
        ) or build_truthy_gate_value(validation_exposure_gate.get("requires_waiting_entry"))
        validation_scale_blocked = shadow_blocks_scale or exposure_blocks_scale
        exposure_cap_multiplier = _safe_float(validation_exposure_gate.get("cap_multiplier"))
        global_max_multiplier = _safe_float(live_authority.get("max_budget_multiplier"))
        risk_budget_multiplier = _safe_float(lane_row.get("risk_budget_multiplier"))
        risk_profile_allows_scale = (
            risk_budget_multiplier <= 0.0
            or risk_budget_multiplier > 1.0
        )
        entry_quality_gate = build_entry_quality_gate_check(
            row,
            waiting_entry=waiting_entry,
        )
        pressure = [
            str(item)
            for item in list(entry_quality_gate.get("pressure") or [])
            if str(item)
        ]
        hard_pressure = [
            item
            for item in pressure
            if item not in {"wait_for_price", "wait_pullback"}
            and "wait_for_price" not in item
            and "wait_pullback" not in item
        ]
        reliefs = list(entry_quality_gate.get("reliefs") or [])
        confluence = list(entry_quality_gate.get("confluence") or [])
        scale_entry_quality = {
            "version": "binance_performance_scale_entry_quality_v1",
            "scale_up_allowed": bool(
                entry_quality_gate.get("has_signal")
                and not hard_pressure
                and (waiting_entry or reliefs or len(confluence) >= 2)
            ),
            "waiting_entry": waiting_entry,
            "pressure": pressure,
            "hard_pressure": hard_pressure,
            "reliefs": reliefs,
            "confluence": confluence,
        }
        performance_scale_allowed = (
            scale_candidate
            and bool(live_authority.get("allow_scale_up"))
            and validation_status in {"", "clear"}
            and global_max_multiplier > 1.0
            and risk_profile_allows_scale
            and not validation_scale_blocked
            and bool(scale_entry_quality.get("scale_up_allowed"))
        )
        if performance_scale_allowed:
            budget_multiplier = min(global_max_multiplier, 1.25)
        elif weak:
            budget_multiplier = 0.25
        elif qualified or scale_candidate:
            budget_multiplier = 1.0
        else:
            budget_multiplier = 0.5
        if risk_budget_multiplier > 0.0:
            budget_multiplier = min(budget_multiplier, risk_budget_multiplier)
        if shadow_blocks_scale:
            budget_multiplier = min(budget_multiplier, 1.0)
        if exposure_blocks_scale and exposure_cap_multiplier > 0.0:
            budget_multiplier = min(budget_multiplier, exposure_cap_multiplier)
        lane_action = action_hint or (
            "observe_or_waiting_entry"
            if weak
            else "eligible_to_review_for_sizing_increase"
            if performance_scale_allowed
            else "normal_or_selective_press"
            if qualified
            else "small_probe_until_sample_builds"
        )
        requires_waiting_entry = weak or "waiting" in lane_action
        effective_requires_waiting_entry = (
            requires_waiting_entry
            or shadow_blocks_scale
            or exposure_blocks_scale
        )
        result = {
            "ok": True,
            "matched_lanes": [matched_lane],
            "weak_lanes": [matched_lane] if weak else [],
            "insufficient_lanes": [] if weak else [matched_lane],
            "row_lanes": sorted(row_lanes),
            "lane_action": lane_action,
            "requires_waiting_entry": effective_requires_waiting_entry,
            "budget_multiplier": budget_multiplier,
            "max_budget_multiplier": budget_multiplier,
            "applied_max_budget_multiplier": budget_multiplier,
            "budget_multiplier_source": "performance_lanes",
            "scale_up_allowed": performance_scale_allowed,
            "validation_scale_blocked": validation_scale_blocked,
            "validation_shadow_gate_status": validation_shadow_gate.get("status"),
            "validation_exposure_gate_status": validation_exposure_gate.get("status"),
            "risk_budget_multiplier": (
                round(risk_budget_multiplier, 6)
                if risk_budget_multiplier > 0.0
                else None
            ),
            "risk_profile_allows_scale": risk_profile_allows_scale,
            "risk_of_ruin_pct": lane_row.get("risk_of_ruin_pct"),
            "lane_confidence_score": lane_row.get("lane_confidence_score"),
            "recommended_risk_fraction": lane_row.get("recommended_risk_fraction"),
            "entry_quality_requirements": [
                "respect_realized_performance_lane_action_hint",
                (
                    "scale_only_when_entry_quality_validation_and_live_evidence_agree"
                    if performance_scale_allowed
                    else "prefer_waiting_entry_until_lane_recovers"
                ),
            ],
            "lane_authority": {
                "version": "performance_lanes_v1",
                "source": "live_performance",
            },
            "performance_quality_hint": quality_hint,
            "performance_action_hint": action_hint,
            "scale_entry_quality": scale_entry_quality,
            "reason": f"{matched_lane}:{lane_action}",
        }
        if effective_requires_waiting_entry and not waiting_entry:
            return {
                **result,
                "ok": False,
                "reason": "performance_lane_requires_waiting_entry",
            }
        return result

    @staticmethod
    def _apply_lane_authority_budget_to_row(
        row: dict[str, Any],
        lane_gate: dict[str, Any],
    ) -> dict[str, Any]:
        multiplier = _safe_float(lane_gate.get("budget_multiplier"))
        if multiplier <= 0 or multiplier >= 1:
            return row
        adjusted = dict(row)
        metadata = dict(
            adjusted.get("metadata")
            if isinstance(adjusted.get("metadata"), dict)
            else {}
        )
        adjustment: dict[str, Any] = {
            "version": "binance_lane_authority_budget_adjustment_v1",
            "budget_multiplier": round(multiplier, 6),
            "matched_lanes": list(lane_gate.get("matched_lanes") or [])[:6],
            "reason": str(
                lane_gate.get("reason")
                or lane_gate.get("lane_action")
                or "lane_authority_budget_multiplier"
            ),
        }
        market = normalize_market(adjusted.get("market") or adjusted.get("venue"))
        reference_price = (
            _safe_float(adjusted.get("entry_trigger_price"))
            or _safe_float(adjusted.get("entry_price"))
            or _safe_float(adjusted.get("entry_price_usdt"))
        )

        def executable_notional_floor_for_quantity() -> float:
            if market == UPBIT_SPOT_MARKET:
                return VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR
            return VALIDATION_REPAIR_MIN_USDT_NOTIONAL_FLOOR

        def scale_field(
            field: str,
            *,
            alias: str | None = None,
            quantity: bool = False,
        ) -> None:
            original = _safe_float(adjusted.get(field))
            if original <= 0:
                return
            scaled = original * multiplier
            floor_reason = ""
            if quantity and reference_price > 0:
                floored = BinanceBlockTrader._minimum_executable_probe_qty_floor(
                    row=adjusted,
                    market=market,
                    reference_price=reference_price,
                    original_qty=original,
                    adjusted_qty=scaled,
                    notional_floor=executable_notional_floor_for_quantity(),
                )
                if floored > scaled:
                    scaled = floored
                    floor_reason = "minimum_executable_notional_floor"
            else:
                floored = build_validation_repair_notional_floor(
                    field=field,
                    market=market,
                    original=original,
                    adjusted=scaled,
                )
                if floored > scaled:
                    scaled = floored
                    floor_reason = "minimum_executable_notional_floor"
            adjusted[field] = scaled
            key = alias or field
            adjustment[f"from_{key}"] = original
            adjustment[f"to_{key}"] = scaled
            if floor_reason:
                adjustment[f"raw_scaled_to_{key}"] = original * multiplier
                adjustment[f"floor_reason_{key}"] = floor_reason

        for key in ("qty", "qty_initial", "quantity"):
            scale_field(key, quantity=True)
        for key in (
            "quote_budget_usdt",
            "quote_budget_krw",
            "quote_budget",
            "quantity_or_quote_budget",
        ):
            scale_field(key)
        metadata["lane_authority_budget_adjustment"] = adjustment
        adjusted["metadata"] = metadata
        return adjusted

    @staticmethod
    def _minimum_executable_probe_qty_floor(
        *,
        row: dict[str, Any],
        market: str,
        reference_price: float,
        original_qty: float,
        adjusted_qty: float,
        notional_floor: float,
    ) -> float:
        if reference_price <= 0 or original_qty <= 0 or adjusted_qty <= 0:
            return adjusted_qty
        floor = _safe_float(notional_floor)
        if floor <= 0:
            return adjusted_qty
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        validation = (
            metadata.get("validation_repair_enforcement")
            if isinstance(metadata.get("validation_repair_enforcement"), dict)
            else row.get("validation_repair_enforcement")
            if isinstance(row.get("validation_repair_enforcement"), dict)
            else {}
        )
        source_qty = original_qty
        cap_qty = original_qty
        for item in validation.get("adjustments") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("field") or "").strip().lower() != "qty":
                continue
            from_qty = _safe_float(item.get("from"))
            to_qty = _safe_float(item.get("to"))
            if from_qty > 0:
                source_qty = max(source_qty, from_qty)
            if to_qty > 0:
                cap_qty = max(cap_qty, to_qty)
        if source_qty * reference_price < floor:
            return adjusted_qty
        if adjusted_qty * reference_price >= floor:
            return adjusted_qty
        floor_qty = floor / reference_price
        if floor_qty <= cap_qty + 1e-12:
            return floor_qty
        _ = market
        return adjusted_qty

    def _lane_authority_gate_with_recent_recovery_floor(
        self,
        row: dict[str, Any],
        lane_gate: dict[str, Any],
    ) -> dict[str, Any]:
        multiplier = _safe_float(lane_gate.get("budget_multiplier"))
        floor = RECENT_RECOVERY_LANE_AUTHORITY_BUDGET_FLOOR
        if multiplier <= 0 or multiplier >= floor:
            return lane_gate
        if not self._lane_authority_gate_allows_recent_recovery_floor(lane_gate):
            return lane_gate
        market = normalize_market(row.get("market") or row.get("venue"))
        if is_upbit_market(market):
            return lane_gate
        side = normalize_position_side(row.get("side"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        horizon = normalize_binance_horizon(
            row.get("horizon") or metadata.get("horizon"),
            market=market,
        )
        lane = normalize_binance_display_lane(
            lane=row.get("lane") or metadata.get("lane") or calculated.get("lane"),
            market=market,
            horizon=horizon,
            side=side,
        )
        recovery = self._lane_performance_recent_recovery_card(
            market=market,
            side=side,
            lane=lane,
            min_samples=max(_safe_int(self.config.lane_performance_min_samples), 1),
        )
        if not recovery:
            return lane_gate
        adjusted = dict(lane_gate)
        source = str(adjusted.get("budget_multiplier_source") or "").strip()
        adjusted["budget_multiplier"] = floor
        adjusted["budget_multiplier_source"] = (
            f"{source}+recent_recovery_floor" if source else "recent_recovery_floor"
        )
        adjusted["budget_multiplier_floor"] = {
            "version": "binance_lane_authority_recent_recovery_floor_v1",
            "reason": "recent_lane_recovery",
            "from_budget_multiplier": round(multiplier, 6),
            "to_budget_multiplier": round(floor, 6),
            "market": market,
            "side": side,
            "lane": lane,
            "matched_lanes": list(adjusted.get("matched_lanes") or [])[:6],
            "sample_count": _safe_int(recovery.get("sample_count")),
            "pnl_usdt": _safe_float(recovery.get("pnl_usdt")),
            "win_rate_pct": _safe_float(recovery.get("win_rate_pct")),
            "avg_r_multiple": _safe_float(recovery.get("avg_r_multiple")),
            "profit_factor": _safe_float(recovery.get("profit_factor")),
            "instruction": (
                "Recent same-lane closed-block evidence recovered; keep the "
                "candidate in probe mode, but do not trap it at stale 0.1 "
                "validation-repair sizing."
            ),
        }
        return adjusted

    @staticmethod
    def _lane_authority_gate_allows_recent_recovery_floor(
        lane_gate: dict[str, Any],
    ) -> bool:
        if not bool(lane_gate.get("ok")):
            return False
        if bool(lane_gate.get("scale_up_allowed")):
            return False
        if _safe_float(lane_gate.get("risk_of_ruin_pct")) >= 20.0:
            return False
        failed_dimensions = [
            str(item).strip()
            for item in list(lane_gate.get("validation_failed_dimensions") or [])
            if str(item).strip()
        ]
        if failed_dimensions:
            return False
        lane_action = str(lane_gate.get("lane_action") or "").strip().lower()
        matched_lanes = [
            str(item).strip().lower()
            for item in list(lane_gate.get("matched_lanes") or [])
            if str(item).strip()
        ]
        weak_sources = [
            str(item).strip().lower()
            for item in list(lane_gate.get("weak_lane_sources") or [])
            if str(item).strip()
        ]
        scale_blockers = [
            str(item).strip().lower()
            for item in list(lane_gate.get("scale_blockers") or [])
            if str(item).strip()
        ]
        soft_repair_tokens = {
            "repair",
            "probe",
            "waiting",
            "validation",
            "evidence",
        }
        if any(token in lane_action for token in soft_repair_tokens):
            return True
        if any(":validation:" in lane for lane in matched_lanes):
            return True
        if any("validation" in source or "repair" in source for source in weak_sources):
            return True
        return any("validation" in blocker or "repair" in blocker for blocker in scale_blockers)

    @staticmethod
    def _row_nested_payloads(row: dict[str, Any]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = [row]
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        calculated_price_plan = (
            row.get("calculated_price_plan")
            if isinstance(row.get("calculated_price_plan"), dict)
            else metadata.get("calculated_price_plan")
            if isinstance(metadata.get("calculated_price_plan"), dict)
            else {}
        )
        payloads.extend([metadata, calculated, calculated_price_plan])
        for parent in (metadata, calculated, calculated_price_plan):
            for key in ("market_inputs", "technical_inputs", "features"):
                child = parent.get(key) if isinstance(parent.get(key), dict) else {}
                if child:
                    payloads.append(child)
        return payloads

    @classmethod
    def _row_first_float(cls, row: dict[str, Any], keys: tuple[str, ...]) -> float:
        for payload in cls._row_nested_payloads(row):
            for key in keys:
                if key in payload:
                    value = _safe_float(payload.get(key))
                    if value > 0:
                        return value
        return 0.0

    @classmethod
    def _manager_cost_edge_gate(cls, row: dict[str, Any]) -> dict[str, Any]:
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        entry = cls._row_first_float(
            row,
            ("entry_price", "entry_price_usdt", "entry_trigger_price", "trigger_price"),
        )
        target = cls._row_first_float(row, ("target_price", "target_price_usdt"))
        stop = cls._row_first_float(row, ("stop_price", "stop_price_usdt"))
        if entry <= 0 or target <= 0 or stop <= 0:
            return {"ok": True, "status": "missing_price_inputs"}
        target_move_pct = (
            (target - entry) / entry * 100.0
            if side == "long"
            else (entry - target) / entry * 100.0
        )
        stop_move_pct = abs(entry - stop) / entry * 100.0
        spread_bps = cls._row_first_float(row, ("spread_bps",))
        funding_rate = cls._row_first_float(row, ("funding_rate",))
        spread_cost_pct = max(spread_bps, 0.0) / 100.0
        funding_buffer_pct = abs(funding_rate) * 100.0 if market == "futures" else 0.0
        base_cost_pct = MANAGER_COST_EDGE_BASE_ROUND_TRIP_PCT.get(market, 0.20)
        estimated_cost_pct = base_cost_pct + spread_cost_pct + funding_buffer_pct
        required_target_move_pct = max(
            estimated_cost_pct * MANAGER_COST_EDGE_REQUIRED_MULTIPLE,
            MANAGER_COST_EDGE_MIN_TARGET_MOVE_PCT.get(market, 0.45),
        )
        gate = {
            "ok": True,
            "version": "manager_cost_edge_gate_v1",
            "market": market,
            "side": side,
            "entry_price": entry,
            "target_price": target,
            "stop_price": stop,
            "target_move_pct": round(target_move_pct, 8),
            "stop_move_pct": round(stop_move_pct, 8),
            "spread_bps": round(spread_bps, 8),
            "funding_rate": funding_rate,
            "base_round_trip_cost_pct": round(base_cost_pct, 8),
            "estimated_round_trip_cost_pct": round(estimated_cost_pct, 8),
            "required_target_move_pct": round(required_target_move_pct, 8),
            "required_multiple": MANAGER_COST_EDGE_REQUIRED_MULTIPLE,
        }
        if target_move_pct <= 0:
            return {
                **gate,
                "ok": False,
                "reason": "cost_edge_invalid_target_direction",
            }
        if target_move_pct < required_target_move_pct:
            return {
                **gate,
                "ok": False,
                "reason": "cost_edge_too_thin",
            }
        return gate

    async def _notify_prompt_budget_error(
        self,
        *,
        run_id: int,
        error_message: str,
        prompt: dict[str, Any],
        venue: str = "Binance",
    ) -> None:
        if "prompt_budget_exceeded" not in str(error_message or ""):
            return
        if not self.config.telegram_alerts_enabled or self.telegram is None:
            self.repository.add_event(
                "__system__",
                "telegram_manager_error_skipped",
                "telegram disabled for prompt budget error",
                {
                    "run_id": run_id,
                    "venue": venue,
                    "error_message": error_message,
                },
            )
            return
        message = build_format_prompt_budget_alert_message(
            venue=venue,
            run_id=run_id,
            error_message=error_message,
            prompt=prompt,
        )
        try:
            result = await self.telegram.send_message(message)
        except Exception as exc:
            self.repository.add_event(
                "__system__",
                "telegram_manager_error_notify_error",
                str(exc),
                {
                    "run_id": run_id,
                    "venue": venue,
                    "error_message": error_message,
                },
            )
            return
        result_ok = bool(result.get("ok")) if isinstance(result, dict) else False
        self.repository.add_event(
            "__system__",
            "telegram_manager_error_notified"
            if result_ok
            else "telegram_manager_error_notify_error",
            "prompt budget error telegram notification handled",
            {
                "run_id": run_id,
                "venue": venue,
                "error_message": error_message,
                "telegram_result": result,
            },
        )

    def status(self) -> dict[str, Any]:
        account = self._status_account_snapshot()
        performance = self.repository.latest_performance_scorecard(limit=20)
        feedback_performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        risk_guard = self._risk_guard_snapshot(account)
        growth_target = self._growth_target_snapshot(account, persist=False)
        growth_governor = self._growth_governor_status_for_account(
            account,
            performance=performance,
            long_performance=feedback_performance,
            risk_guard=risk_guard,
            growth_target=growth_target,
        )
        live_authority = self._live_authority_context()
        repository_status = self.repository.status()
        non_closed_blocks = self.repository.list_blocks(include_closed=False)
        total_error_blocks = [
            row for row in non_closed_blocks if str(row.get("status") or "") == "error"
        ]
        error_blocks = build_actionable_error_block_rows(non_closed_blocks)
        inactive_error_blocks = build_inactive_error_block_rows(non_closed_blocks)
        dangling_error_qty_blocks = error_blocks
        repository_status.update(
            {
                "error_block_count": len(error_blocks),
                "inactive_error_block_count": len(inactive_error_blocks),
                "total_error_block_count": len(total_error_blocks),
                "dangling_error_qty_block_count": len(dangling_error_qty_blocks),
                "dangling_error_qty_notional_usdt": sum(
                    self._block_notional_usdt(row) for row in dangling_error_qty_blocks
                ),
            }
        )
        return {
            **repository_status,
            "enabled": bool(self.config.enabled),
            "execution_mode": (
                "live"
                if self.config.execute_spot_orders or self.config.execute_futures_orders
                else "paper"
            ),
            "execute_spot_orders": bool(self.config.execute_spot_orders),
            "execute_futures_orders": bool(self.config.execute_futures_orders),
            "execution": self._execution_status(),
            "kill_switch": self.kill_switch(),
            "risk": self._risk_status(performance=feedback_performance),
            "risk_guard": risk_guard,
            "growth_target": growth_target,
            "growth_governor": growth_governor,
            "growth_unlock": self._growth_unlock_context(
                growth_governor=growth_governor,
                performance=performance,
                live_authority=live_authority,
            ),
            "performance": performance,
            "performance_today": self.repository.today_performance_scorecard(),
            "live_authority": live_authority,
            "adapter_ready": bool(self.adapter is not None),
            "llm_ready": bool(getattr(self.codex_runtime, "ready", False)),
            "model": str(getattr(self.codex_runtime, "resolved_model", self.config.llm_model)),
            "reasoning_effort": str(self.config.llm_reasoning_effort),
            "latest_decision_input": self._latest_decision_input_summary(
                self.repository.latest_manager_run(include_payload=True),
            ),
            "config": {
                "quote_interval_sec": int(self.config.quote_interval_sec),
                "rule_interval_sec": int(self.config.rule_interval_sec),
                "manager_interval_sec": int(self.config.manager_interval_sec),
                "failed_exit_retry_cooldown_sec": int(
                    self.config.failed_exit_retry_cooldown_sec
                ),
                "llm_timeout_ms": int(self.config.llm_timeout_ms),
                "max_manager_symbols": int(self.config.max_manager_symbols),
                "quant_context_limit": int(self.config.quant_context_limit),
                "prompt_target_chars": int(self.config.prompt_target_chars),
                "prompt_warn_chars": int(self.config.prompt_warn_chars),
                "prompt_max_chars": int(self.config.prompt_max_chars),
                "jue_wiki_context_max_chars": int(
                    self.config.jue_wiki_context_max_chars
                ),
                "max_futures_leverage": int(self.config.max_futures_leverage),
                "min_liquidation_distance_pct": float(
                    self.config.min_liquidation_distance_pct
                ),
                "min_entry_confidence": float(self.config.min_entry_confidence),
                "min_entry_expected_r": float(self.config.min_entry_expected_r),
                "min_entry_directional_score": float(
                    self.config.min_entry_directional_score
                ),
                "min_candidate_stop_pct": float(self.config.min_candidate_stop_pct),
                "profit_lock_trigger_r": float(self.config.profit_lock_trigger_r),
                "profit_lock_stop_r": float(self.config.profit_lock_stop_r),
                "spot_quote_budget_pct": float(self.config.spot_quote_budget_pct),
                "spot_min_quote_budget_usdt": float(
                    self.config.spot_min_quote_budget_usdt
                ),
                "spot_max_quote_budget_usdt": float(
                    self.config.spot_max_quote_budget_usdt
                ),
                "upbit_quote_budget_pct": float(self.config.upbit_quote_budget_pct),
                "upbit_min_quote_budget_krw": float(
                    self.config.upbit_min_quote_budget_krw
                ),
                "upbit_max_quote_budget_krw": float(
                    self.config.upbit_max_quote_budget_krw
                ),
                "upbit_usdt_krw_rate": float(self.config.upbit_usdt_krw_rate),
                "futures_quote_budget_pct": float(
                    self.config.futures_quote_budget_pct
                ),
                "futures_min_quote_budget_usdt": float(
                    self.config.futures_min_quote_budget_usdt
                ),
                "futures_max_quote_budget_usdt": float(
                    self.config.futures_max_quote_budget_usdt
                ),
                "volatile_attack_enabled": bool(self.config.volatile_attack_enabled),
                "volatile_attack_candidate_limit": int(
                    self.config.volatile_attack_candidate_limit
                ),
                "volatile_attack_budget_multiplier": float(
                    self.config.volatile_attack_budget_multiplier
                ),
                "volatile_attack_min_change_pct": float(
                    self.config.volatile_attack_min_change_pct
                ),
                "volatile_attack_min_volume_expansion": float(
                    self.config.volatile_attack_min_volume_expansion
                ),
                "volatile_attack_min_reward_risk": float(
                    self.config.volatile_attack_min_reward_risk
                ),
                "volatile_attack_stop_multiplier": float(
                    self.config.volatile_attack_stop_multiplier
                ),
                "budget_performance_scale_enabled": bool(
                    self.config.budget_performance_scale_enabled
                ),
                "budget_performance_scale_min_samples": int(
                    self.config.budget_performance_scale_min_samples
                ),
                "budget_performance_scale_win_rate_pct": float(
                    self.config.budget_performance_scale_win_rate_pct
                ),
                "budget_performance_scale_multiplier": float(
                    self.config.budget_performance_scale_multiplier
                ),
                "execution_defect_loss_multiplier": float(
                    self.config.execution_defect_loss_multiplier
                ),
                "daily_loss_stop_pct": float(self.config.daily_loss_stop_pct),
                "monthly_loss_stop_pct": float(self.config.monthly_loss_stop_pct),
                "symbol_lane_cooldown_min_samples": int(
                    self.config.symbol_lane_cooldown_min_samples
                ),
                "symbol_lane_cooldown_max_win_rate_pct": float(
                    self.config.symbol_lane_cooldown_max_win_rate_pct
                ),
            },
        }

    def _latest_decision_input_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(run, dict) or run.get("status") == "missing":
            return {"status": "missing"}
        prompt = run.get("prompt") if isinstance(run.get("prompt"), dict) else {}
        response = run.get("response") if isinstance(run.get("response"), dict) else {}
        actions = run.get("actions") if isinstance(run.get("actions"), dict) else {}
        proactive_pressure = (
            prompt.get("proactive_decision_pressure")
            if isinstance(prompt.get("proactive_decision_pressure"), dict)
            else {}
        )
        execution_gate = (
            prompt.get("execution_gate")
            if isinstance(prompt.get("execution_gate"), dict)
            else {}
        )
        prompt_budget = (
            prompt.get("prompt_budget")
            if isinstance(prompt.get("prompt_budget"), dict)
            else {}
        )
        hold_decision = (
            response.get("hold_decision")
            if isinstance(response.get("hold_decision"), dict)
            else {}
        )
        diagnostics = self._manager_run_diagnostics(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
        watch_symbols = _normalize_list(hold_decision.get("watch_symbols"))[:12]
        return {
            key: value
            for key, value in {
                "status": "ok",
                "run_id": run.get("id"),
                "run_at": run.get("run_at"),
                "mode": run.get("mode"),
                "model": run.get("model"),
                "action_count": _manager_action_item_count(actions),
                "decision_inputs": list(prompt.get("decision_inputs") or [])[:24],
                "proactive_pressure_status": proactive_pressure.get("status"),
                "proactive_pressure_level": proactive_pressure.get("pressure_level"),
                "proactive_zero_action_streak": proactive_pressure.get(
                    "zero_action_streak"
                ),
                "proactive_candidate_count": proactive_pressure.get(
                    "candidate_count"
                ),
                "proactive_strong_candidate_count": proactive_pressure.get(
                    "strong_candidate_count"
                ),
                "execution_gate_status": execution_gate.get("status"),
                "live_venues": _normalize_list(execution_gate.get("live_venues"))[:6],
                "kill_switch_enabled": (
                    execution_gate.get("kill_switch", {}).get("enabled")
                    if isinstance(execution_gate.get("kill_switch"), dict)
                    else None
                ),
                "total_equity_usdt": (
                    execution_gate.get("cash_available", {}).get("total_equity_usdt")
                    if isinstance(execution_gate.get("cash_available"), dict)
                    else None
                ),
                "spot_cash_usdt": (
                    execution_gate.get("cash_available", {}).get("spot_cash_usdt")
                    if isinstance(execution_gate.get("cash_available"), dict)
                    else None
                ),
                "futures_cash_usdt": (
                    execution_gate.get("cash_available", {}).get("futures_cash_usdt")
                    if isinstance(execution_gate.get("cash_available"), dict)
                    else None
                ),
                "upbit_cash_krw": (
                    execution_gate.get("cash_available", {}).get("upbit_cash_krw")
                    if isinstance(execution_gate.get("cash_available"), dict)
                    else None
                ),
                "active_block_count": execution_gate.get("active_block_count"),
                "waiting_entry_block_count": execution_gate.get(
                    "waiting_entry_block_count"
                ),
                "pending_order_block_count": execution_gate.get(
                    "pending_order_block_count"
                ),
                "hold_summary": str(hold_decision.get("summary") or "")[:500],
                "hold_watch_symbols": watch_symbols,
                "jue_wiki_attention": _latest_wiki_attention_summary(
                    prompt=prompt,
                    diagnostics=diagnostics,
                ),
                "jue_wiki_memory_card_quality": (
                    _latest_memory_card_quality_summary(
                        prompt=prompt,
                        diagnostics=diagnostics,
                        response=response,
                        actions=actions,
                        hold_decision=hold_decision,
                    )
                ),
                "memory_contract": _latest_candidate_memory_contract_summary(
                    prompt=prompt,
                    response=response,
                    run=run,
                )
                or _latest_validation_repair_memory_contract_summary(
                    prompt=prompt,
                    response=response,
                    run=run,
                ),
                "prompt_budget": {
                    sub_key: sub_value
                    for sub_key, sub_value in prompt_budget.items()
                    if sub_key
                    in {"status", "total_chars", "target_chars", "warn_chars", "max_chars"}
                },
            }.items()
            if value not in (None, "", [], {})
        }

    async def snapshot(self) -> dict[str, Any]:
        account = build_normalize_account_snapshot(
            await self._collect_account_snapshot(),
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        self._last_account_snapshot = account
        status = self.status()
        growth_target = self._growth_target_snapshot(account, persist=False)
        risk_guard = status.get("risk_guard") or self._risk_guard_snapshot(account)
        performance = (
            status.get("performance")
            if isinstance(status.get("performance"), dict)
            else self.repository.latest_performance_scorecard(limit=20)
        )
        growth_governor = (
            status.get("growth_governor")
            if isinstance(status.get("growth_governor"), dict)
            else self._growth_governor_context(
                growth_target=growth_target,
                performance=performance,
                risk_guard=risk_guard,
                long_performance=self.repository.latest_performance_scorecard(
                    limit=self._performance_scorecard_feedback_limit()
                ),
            )
        )
        live_authority = (
            status.get("live_authority")
            if isinstance(status.get("live_authority"), dict)
            else self._live_authority_context()
        )
        growth_unlock = (
            status.get("growth_unlock")
            if isinstance(status.get("growth_unlock"), dict)
            else self._growth_unlock_context(
                growth_governor=growth_governor,
                performance=performance,
                live_authority=live_authority,
            )
        )
        blocks = self.repository.list_blocks(include_closed=True)
        active_blocks = build_visible_block_rows(
            self.repository.list_blocks(include_closed=False),
            visible_statuses=VISIBLE_BLOCK_STATUSES,
        )
        enriched_blocks = self._enrich_blocks_with_latest_quotes(blocks)
        enriched_blocks = self._attach_performance_reflections(enriched_blocks)
        enriched_active = self._enrich_blocks_with_latest_quotes(active_blocks)
        return {
            **status,
            "account": account,
            "growth_target": growth_target,
            "risk_guard": risk_guard,
            "growth_governor": growth_governor,
            "growth_unlock": growth_unlock,
            "live_authority": live_authority,
            "blocks": enriched_blocks,
            "active_blocks": enriched_active,
            "block_history": build_block_history_rows(enriched_blocks),
            "lane_allocation": build_lane_allocation_summary(
                enriched_active,
                active_statuses=ACTIVE_BLOCK_STATUSES,
                upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            ),
            "orders": self.repository.list_orders(limit=50),
            "events": self.repository.list_events(limit=80),
            "manager_runs": self._manager_runs_with_decision_context(limit=5),
            "performance": performance,
            "performance_today": self.repository.today_performance_scorecard(),
            "updated_at": utc_now_iso(),
        }

    async def snapshot_compact(self) -> dict[str, Any]:
        def stage(name: str, fn: Callable[[], Any]) -> Any:
            try:
                return fn()
            except Exception as exc:
                raise RuntimeError(f"snapshot_compact:{name}: {exc}") from exc

        account = stage("account_snapshot", self._status_account_snapshot)
        if stage("account_equity", lambda: self._account_equity_usdt(account)) <= 0:
            local_account = getattr(self.adapter, "account", None)
            if isinstance(local_account, dict):
                account = stage(
                    "adapter_cached_account",
                    lambda: build_normalize_account_snapshot(
                        local_account,
                        default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
                    ),
                )
                if stage("adapter_cached_equity", lambda: self._account_equity_usdt(account)) > 0:
                    self._last_account_snapshot = account
        status_payload = stage("status", lambda: dict(self.status()))
        live_authority = status_payload.get("live_authority")
        if isinstance(live_authority, dict):
            status_payload["live_authority"] = stage(
                "live_authority_compaction",
                lambda: compact_live_authority_for_status(live_authority),
            )
        account = stage("account_after_status", self._status_account_snapshot)
        active_blocks = stage(
            "active_block_summaries",
            lambda: build_visible_block_rows(
                self.repository.list_block_summaries(include_closed=False),
                visible_statuses=VISIBLE_BLOCK_STATUSES,
            ),
        )
        history_blocks = stage(
            "history_block_summaries",
            lambda: self.repository.list_block_summaries(
                include_closed=True,
                limit=30,
            ),
        )
        enriched_active = stage(
            "active_quote_enrichment",
            lambda: self._enrich_blocks_with_latest_quotes(active_blocks),
        )
        enriched_history = stage(
            "history_quote_enrichment",
            lambda: self._enrich_blocks_with_latest_quotes(history_blocks),
        )
        enriched_history = stage(
            "history_performance_reflections",
            lambda: self._attach_performance_reflections(enriched_history),
        )
        manager_runs = stage(
            "manager_runs",
            lambda: [
                self._compact_manager_run(row)
                for row in self.repository.list_manager_runs(
                    limit=3,
                    include_payload=False,
                    compact_payload=True,
                )
            ],
        )
        return {
            **status_payload,
            "account": account,
            "growth_target": status_payload.get("growth_target"),
            "growth_governor": status_payload.get("growth_governor"),
            "growth_unlock": status_payload.get("growth_unlock"),
            "active_blocks": enriched_active,
            "block_history": stage(
                "compact_block_history",
                lambda: build_compact_history_block_rows(
                    enriched_history,
                    limit=30,
                ),
            ),
            "lane_allocation": stage(
                "lane_allocation",
                lambda: build_lane_allocation_summary(
                    enriched_active,
                    active_statuses=ACTIVE_BLOCK_STATUSES,
                    upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
                ),
            ),
            "orders": stage("orders", lambda: self.repository.list_orders(limit=12)),
            "events": stage("events", lambda: self.repository.list_events(limit=12)),
            "manager_runs": manager_runs,
            "performance": stage(
                "performance_scorecard",
                lambda: self.repository.latest_performance_scorecard(limit=20),
            ),
            "performance_today": stage(
                "today_performance_scorecard",
                self.repository.today_performance_scorecard,
            ),
            "updated_at": utc_now_iso(),
            "compact": True,
        }

    def snapshot_sync(self) -> dict[str, Any]:
        status_payload = self.status()
        blocks = self.repository.list_blocks(include_closed=True)
        active_blocks = build_visible_block_rows(
            self.repository.list_blocks(include_closed=False),
            visible_statuses=VISIBLE_BLOCK_STATUSES,
        )
        enriched_blocks = self._enrich_blocks_with_latest_quotes(blocks)
        enriched_blocks = self._attach_performance_reflections(enriched_blocks)
        enriched_active = self._enrich_blocks_with_latest_quotes(active_blocks)
        return {
            **status_payload,
            "account": self._status_account_snapshot(),
            "growth_target": status_payload.get("growth_target"),
            "growth_governor": status_payload.get("growth_governor"),
            "growth_unlock": status_payload.get("growth_unlock"),
            "blocks": enriched_blocks,
            "active_blocks": enriched_active,
            "block_history": build_block_history_rows(enriched_blocks),
            "lane_allocation": build_lane_allocation_summary(
                enriched_active,
                active_statuses=ACTIVE_BLOCK_STATUSES,
                upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            ),
            "orders": self.repository.list_orders(limit=50),
            "events": self.repository.list_events(limit=80),
            "manager_runs": self._manager_runs_with_decision_context(limit=5),
            "performance": self.repository.latest_performance_scorecard(limit=20),
            "performance_today": self.repository.today_performance_scorecard(),
            "updated_at": utc_now_iso(),
        }

    def _enrich_blocks_with_latest_quotes(
        self,
        blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        quotes = self.repository.latest_quotes_for_blocks(blocks)
        return build_enrich_blocks_with_latest_quotes(blocks, quotes)

    def _attach_performance_reflections(
        self,
        blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        reflections = self.repository.performance_reflections_for_blocks(
            [str(block.get("block_id") or "") for block in blocks]
        )
        return build_attach_performance_reflections(blocks, reflections)

    @classmethod
    def _manager_run_with_decision_context(cls, row: dict[str, Any]) -> dict[str, Any]:
        return build_manager_run_with_decision_context(
            row,
            compact_prompt_context=cls._compact_manager_prompt_context,
        )

    def _manager_runs_with_decision_context(self, *, limit: int = 5) -> list[dict[str, Any]]:
        return [
            self._manager_run_with_decision_context(row)
            for row in self.repository.list_manager_runs(
                limit=limit,
                include_payload=False,
                compact_payload=True,
            )
        ]

    @classmethod
    def _compact_manager_run(cls, row: dict[str, Any]) -> dict[str, Any]:
        return build_compact_snapshot_manager_run(
            row,
            normalize_hold_decision=lambda response, actions: build_normalize_manager_hold_decision(
                response=response,
                actions=actions,
                symbols=[],
                allowed_actions=ALLOWED_MANAGER_ACTIONS,
            ),
            compact_response_payload=cls._compact_manager_response_payload,
            compact_prompt_context=cls._compact_manager_prompt_context,
        )

    @staticmethod
    def _compact_manager_response_payload(response: dict[str, Any]) -> dict[str, Any]:
        return build_compact_manager_response_payload(response)

    @classmethod
    def _compact_manager_prompt_context(
        cls,
        prompt: dict[str, Any],
        *,
        response: dict[str, Any] | None = None,
        actions: dict[str, Any] | None = None,
        hold_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = cls
        return build_compact_manager_prompt_context(
            prompt=prompt,
            response=response or {},
            actions=actions or {},
            hold_decision=hold_decision or {},
        )

    @staticmethod
    def _manager_run_diagnostics(
        *,
        prompt: dict[str, Any],
        response: dict[str, Any],
        actions: dict[str, Any],
        hold_decision: dict[str, Any],
    ) -> dict[str, Any]:
        return build_manager_run_diagnostics(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )

    def _proactive_decision_pressure(
        self,
        *,
        previous_manager_runs: list[dict[str, Any]],
        executable_candidates: list[dict[str, Any]],
        growth_governor: dict[str, Any],
        live_authority: dict[str, Any],
    ) -> dict[str, Any]:
        zero_action_streak = 0
        latest_hold: dict[str, Any] = {}
        for run in previous_manager_runs:
            if not isinstance(run, dict):
                continue
            if str(run.get("status") or "").lower() == "error":
                break
            if _manager_action_item_count(run.get("actions")) > 0:
                break
            zero_action_streak += 1
            response = (
                run.get("response")
                if isinstance(run.get("response"), dict)
                else {}
            )
            hold = (
                response.get("hold_decision")
                if isinstance(response.get("hold_decision"), dict)
                else {}
            )
            if hold:
                latest_hold = hold

        candidates = self._compact_pressure_candidates(executable_candidates, limit=8)
        strong_candidates = [
            row
            for row in candidates
            if _safe_float(row.get("score")) >= 55.0
            or _safe_float(row.get("confidence")) >= 0.58
        ]
        allow_new = bool(growth_governor.get("allow_new_blocks", True))
        live_grade = _clean_text(live_authority.get("live_grade"), limit=40)
        status = "idle"
        pressure_level = "none"
        if zero_action_streak >= 2 and candidates and allow_new:
            status = "action_required"
            pressure_level = "high"
        elif zero_action_streak >= 1 and candidates:
            status = "watch"
            pressure_level = "medium"
        elif candidates:
            status = "candidate_present"
            pressure_level = "low"
        if status == "idle":
            return {
                "version": "binance_proactive_decision_pressure_v1",
                "status": "idle",
                "zero_action_streak": zero_action_streak,
            }
        return {
            "version": "binance_proactive_decision_pressure_v1",
            "status": status,
            "pressure_level": pressure_level,
            "zero_action_streak": zero_action_streak,
            "candidate_count": len(candidates),
            "strong_candidate_count": len(strong_candidates),
            "growth_governor_mode": _clean_text(
                growth_governor.get("mode") or growth_governor.get("status"),
                limit=80,
            ),
            "growth_governor_allows_new_blocks": allow_new,
            "live_grade": live_grade,
            "previous_hold_summary": _clean_text(
                latest_hold.get("summary"),
                limit=260,
            ),
            "top_candidates": candidates,
            "required_resolution": (
                "When candidates exist and safety gates do not block risk, resolve "
                "at least one candidate as create_wait_block, small_probe, or "
                "explicit_reject_with_price_reason. Do not return generic hold text."
            ),
            "response_contract": {
                "action_required": (
                    "If status is action_required, the manager must either create "
                    "a small executable waiting/probe block or name the exact "
                    "candidate-level execution gate that prevents every top "
                    "candidate. Generic market caution is not a valid resolution."
                ),
                "hold_only_requires": [
                    "top rejected candidate symbols",
                    "specific missing price/depth/funding/risk condition",
                    "next trigger price or condition",
                ],
            },
            "allowed_resolutions": [
                "create_wait_block",
                "small_probe",
                "explicit_reject_with_price_reason",
                "defer_due_to_safety_gate",
            ],
        }

    @staticmethod
    def _compact_pressure_candidates(
        candidates: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate in candidates[: max(int(limit), 0)]:
            if not isinstance(candidate, dict):
                continue
            calculated = (
                candidate.get("calculated")
                if isinstance(candidate.get("calculated"), dict)
                else {}
            )
            market = _clean_text(candidate.get("market"), limit=32)
            if normalize_market(market) == UPBIT_SPOT_MARKET:
                has_krw_price_context = any(
                    _safe_float(source.get(key)) > 0
                    for source in (candidate, calculated)
                    for key in (
                        "entry_price_krw",
                        "target_price_krw",
                        "stop_price_krw",
                        "quote_budget_krw",
                    )
                )
                if not has_krw_price_context:
                    continue
                entry_price = (
                    candidate.get("entry_price_krw")
                    or calculated.get("entry_price_krw")
                    or candidate.get("entry_price")
                    or calculated.get("entry_price")
                )
                target_price = (
                    candidate.get("target_price_krw")
                    or calculated.get("target_price_krw")
                    or candidate.get("target_price")
                    or calculated.get("target_price")
                )
                stop_price = (
                    candidate.get("stop_price_krw")
                    or calculated.get("stop_price_krw")
                    or candidate.get("stop_price")
                    or calculated.get("stop_price")
                )
            else:
                entry_price = (
                    candidate.get("entry_price")
                    or calculated.get("entry_price")
                    or calculated.get("entry_price_usdt")
                )
                target_price = (
                    candidate.get("target_price")
                    or calculated.get("target_price")
                    or calculated.get("target_price_usdt")
                )
                stop_price = (
                    candidate.get("stop_price")
                    or calculated.get("stop_price")
                    or calculated.get("stop_price_usdt")
                )
            payload = {
                "symbol": _clean_text(candidate.get("symbol"), limit=32),
                "market": market,
                "side": _clean_text(candidate.get("side"), limit=16),
                "lane": _clean_text(candidate.get("lane"), limit=40),
                "horizon": _clean_text(candidate.get("horizon"), limit=40),
                "score": candidate.get("score"),
                "confidence": candidate.get("confidence"),
                "entry_style": _clean_text(
                    candidate.get("entry_style")
                    or calculated.get("entry_style"),
                    limit=40,
                ),
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_price": stop_price,
                "reason": _clean_text(
                    candidate.get("reason")
                    or candidate.get("reason_md")
                    or candidate.get("summary"),
                    limit=220,
                ),
            }
            compact = {
                key: value
                for key, value in payload.items()
                if value not in (None, "", [], {})
            }
            if compact:
                rows.append(compact)
        return rows

    def _execution_status(self) -> dict[str, Any]:
        return {
            "spot_mode": "live" if self.config.execute_spot_orders else "paper",
            "futures_mode": "live" if self.config.execute_futures_orders else "paper",
            "upbit_spot_mode": "live" if self.config.execute_upbit_orders else "paper",
            "spot_orders_enabled": bool(self.config.execute_spot_orders),
            "futures_orders_enabled": bool(self.config.execute_futures_orders),
            "upbit_orders_enabled": bool(self.config.execute_upbit_orders),
        }

    def _manager_execution_gate_context(
        self,
        *,
        account: dict[str, Any],
        blocks: list[dict[str, Any]],
        lane_balance: dict[str, Any],
    ) -> dict[str, Any]:
        kill_switch = self.kill_switch()
        execution = self._execution_status()
        active_blocks = [
            row
            for row in blocks
            if str(row.get("status") or "") in ACTIVE_BLOCK_STATUSES
        ]
        waiting_blocks = [
            row for row in blocks if str(row.get("status") or "") == "proposed"
        ]
        pending_blocks = [
            row
            for row in blocks
            if str(row.get("status") or "") in {"entry_pending", "exit_pending"}
        ]
        duplicate_guard = (
            lane_balance.get("near_duplicate_active_blocks")
            if isinstance(lane_balance.get("near_duplicate_active_blocks"), dict)
            else self._near_duplicate_active_blocks_context(active_blocks)
        )
        cash = {
            "spot_cash_usdt": _safe_float(account.get("spot_cash_usdt")),
            "futures_cash_usdt": _safe_float(account.get("futures_cash_usdt")),
            "upbit_cash_krw": _safe_float(account.get("upbit_cash_krw")),
            "upbit_cash_usdt": _safe_float(account.get("upbit_cash_usdt")),
            "total_equity_usdt": self._account_equity_usdt(account),
        }
        live_venues = [
            venue
            for venue, enabled in {
                "spot": execution.get("spot_orders_enabled"),
                "futures": execution.get("futures_orders_enabled"),
                "upbit_spot": execution.get("upbit_orders_enabled"),
            }.items()
            if enabled
        ]
        return {
            "version": "binance_execution_gate_v1",
            "status": "blocked" if kill_switch.get("enabled") else "ok",
            "execution": execution,
            "live_venues": live_venues,
            "kill_switch": {
                "enabled": bool(kill_switch.get("enabled")),
                "reason": str(kill_switch.get("reason") or ""),
                "updated_at": str(kill_switch.get("updated_at") or ""),
            },
            "cash_available": cash,
            "active_block_count": len(active_blocks),
            "waiting_entry_block_count": len(waiting_blocks),
            "pending_order_block_count": len(pending_blocks),
            "duplicate_order_guard": duplicate_guard,
            "decision_instruction": (
                "Use this section as the explicit execution gate. If kill_switch "
                "is false, live venue is enabled, cash_available is positive, and "
                "duplicate_order_guard is ok, do not claim these fields are missing."
            ),
        }

    def _risk_status(self, *, performance: dict[str, Any] | None = None) -> dict[str, Any]:
        config = getattr(self.risk_sizer, "config", None)
        return {
            "account_risk_pct": _safe_float(getattr(config, "account_risk_pct", 0.0)),
            "max_total_exposure_usdt": _safe_float(
                getattr(config, "max_total_exposure_usdt", 0.0)
            ),
            "max_symbol_exposure_pct": _safe_float(
                getattr(config, "max_symbol_exposure_pct", 0.0)
            ),
            "min_reward_risk": _safe_float(getattr(config, "min_reward_risk", 0.0)),
            "lane_risk_multipliers": dict(LANE_RISK_MULTIPLIERS),
            "lane_performance_multipliers": self._lane_performance_multipliers_status(
                performance=performance,
            ),
        }

    def _lane_performance_multipliers_status(
        self,
        *,
        performance: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        lanes = (
            "short",
            "mid",
            "long",
            "futures",
            "spot:long",
            "futures:long",
            "futures:short",
            "upbit_spot:long",
            "volatile_attack",
        )
        if performance is None:
            try:
                performance = self.repository.latest_performance_scorecard(
                    limit=self._performance_scorecard_feedback_limit()
                )
            except Exception:
                logger.warning(
                    "failed to load Binance lane performance for status",
                    exc_info=True,
                )
                performance = {}
        return {
            lane: self._lane_performance_risk_multiplier(
                lane,
                performance=performance,
            )
            for lane in lanes
        }

    @staticmethod
    def _month_window(now: datetime) -> tuple[str, datetime, datetime, float, float]:
        local_now = now.astimezone(KST)
        month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        elapsed_days = max((local_now - month_start).total_seconds() / 86_400.0, 0.0)
        month_days = max((next_month - month_start).total_seconds() / 86_400.0, 1.0)
        return month_start.strftime("%Y-%m"), month_start, next_month, elapsed_days, month_days

    @staticmethod
    def _account_equity_usdt(account: dict[str, Any]) -> float:
        if not isinstance(account, dict):
            return 0.0
        explicit_equity = 0.0
        for key in ("total_value_usdt", "total_equity_usdt", "equity_usdt"):
            explicit_equity = _safe_float(account.get(key))
            if explicit_equity > 0:
                break
        total = _safe_float(account.get("spot_cash_usdt")) + _safe_float(
            account.get("futures_cash_usdt")
        )
        total += _safe_float(account.get("upbit_cash_usdt"))
        upbit_usdt_krw_rate = max(
            _safe_float(account.get("upbit_usdt_krw_rate"))
            or DEFAULT_UPBIT_USDT_KRW_RATE,
            1.0,
        )
        for rows in (
            account.get("spot_assets"),
            account.get("futures_assets"),
            account.get("upbit_spot_assets"),
        ):
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                kind = str(row.get("kind") or "").strip().lower()
                if kind == "cash":
                    continue
                value_usdt = _safe_float(row.get("value_usdt"))
                if value_usdt <= 0 and _safe_float(row.get("value_krw")) > 0:
                    value_usdt = _safe_float(row.get("value_krw")) / upbit_usdt_krw_rate
                total += value_usdt
        for row in account.get("futures_position_risk") or []:
            if isinstance(row, dict):
                total += _safe_float(
                    row.get("unrealized_profit")
                    or row.get("unRealizedProfit")
                    or row.get("unrealizedProfit")
                )
        if explicit_equity > 0:
            if total <= 0:
                return explicit_equity
            looks_krw_scaled = explicit_equity > 100_000 and explicit_equity > total * 10
            if not looks_krw_scaled:
                return explicit_equity
        return max(total, 0.0)

    def _growth_target_snapshot(
        self,
        account: dict[str, Any],
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        normalized = build_normalize_account_snapshot(
            account,
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        current_equity = self._account_equity_usdt(normalized)
        if current_equity <= 0:
            return {
                "status": "missing_equity",
                "monthly_target_pct": float(self.config.monthly_target_pct),
                "current_equity_usdt": 0.0,
            }
        month_key, _month_start, _next_month, elapsed_days, month_days = self._month_window(
            datetime.now(timezone.utc)
        )
        start_equity = self.repository.growth_month_start_equity(
            month_key=month_key,
            current_equity_usdt=current_equity,
        )
        snapshot = CryptoGrowthTargetLedger(
            monthly_target_pct=float(self.config.monthly_target_pct)
        ).snapshot(
            start_equity_usdt=start_equity,
            current_equity_usdt=current_equity,
            elapsed_days=elapsed_days,
            month_days=month_days,
        )
        payload = {**snapshot, "month_key": month_key}
        if persist:
            self.repository.save_growth_target_snapshot(month_key=month_key, payload=payload)
        return payload

    @staticmethod
    def _return_pct(current: float, start: float) -> float:
        if start <= 0:
            return 0.0
        return (current / start - 1.0) * 100.0

    def _risk_guard_period_keys(self, now: datetime | None = None) -> dict[str, str]:
        local_now = (now or datetime.now(timezone.utc)).astimezone(KST)
        return {
            "day": f"day:{local_now.strftime('%Y-%m-%d')}",
            "month": f"month:{local_now.strftime('%Y-%m')}",
        }

    def _risk_guard_snapshot(self, account: dict[str, Any]) -> dict[str, Any]:
        normalized = build_normalize_account_snapshot(
            account,
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        current_equity = self._account_equity_usdt(normalized)
        daily_limit = abs(_safe_float(self.config.daily_loss_stop_pct))
        monthly_limit = abs(_safe_float(self.config.monthly_loss_stop_pct))
        payload: dict[str, Any] = {
            "version": "binance_risk_guard_v1",
            "current_equity_usdt": current_equity,
            "daily_loss_stop_pct": daily_limit,
            "monthly_loss_stop_pct": monthly_limit,
            "allow_new_entries": False,
            "breaches": [],
        }
        if current_equity <= 0:
            return {**payload, "status": "missing_equity"}

        keys = self._risk_guard_period_keys()
        daily_start = self.repository.equity_baseline(
            keys["day"],
            current_equity_usdt=current_equity,
        )
        monthly_start = self.repository.equity_baseline(
            keys["month"],
            current_equity_usdt=current_equity,
        )
        daily_return = self._return_pct(current_equity, daily_start)
        monthly_return = self._return_pct(current_equity, monthly_start)
        breaches: list[dict[str, Any]] = []
        if daily_limit > 0 and daily_return <= -daily_limit:
            breaches.append(
                {
                    "scope": "day",
                    "period_key": keys["day"],
                    "return_pct": round(daily_return, 4),
                    "limit_pct": -daily_limit,
                }
            )
        if monthly_limit > 0 and monthly_return <= -monthly_limit:
            breaches.append(
                {
                    "scope": "month",
                    "period_key": keys["month"],
                    "return_pct": round(monthly_return, 4),
                    "limit_pct": -monthly_limit,
                }
            )
        return {
            **payload,
            "status": "halt_new_entries" if breaches else "ok",
            "allow_new_entries": not breaches,
            "breaches": breaches,
            "day": {
                "period_key": keys["day"],
                "start_equity_usdt": daily_start,
                "return_pct": round(daily_return, 4),
            },
            "month": {
                "period_key": keys["month"],
                "start_equity_usdt": monthly_start,
                "return_pct": round(monthly_return, 4),
            },
        }

    def _entry_risk_guard_allows_new_entries(self) -> bool:
        guard = self._risk_guard_snapshot(self._last_account_snapshot)
        return bool(guard.get("allow_new_entries"))

    def _current_growth_governor_status(self) -> dict[str, Any]:
        account = self._last_account_snapshot
        performance = self.repository.latest_performance_scorecard(limit=20)
        growth_target = self._growth_target_snapshot(account, persist=False)
        risk_guard = self._risk_guard_snapshot(account)
        return self._growth_governor_context(
            growth_target=growth_target,
            performance=performance,
            risk_guard=risk_guard,
            long_performance=self.repository.latest_performance_scorecard(
                limit=self._performance_scorecard_feedback_limit()
            ),
        )

    def _current_growth_unlock_status(self) -> dict[str, Any]:
        performance = self.repository.latest_performance_scorecard(limit=20)
        growth_governor = self._current_growth_governor_status()
        live_authority = self._live_authority_context()
        return self._growth_unlock_context(
            growth_governor=growth_governor,
            performance=performance,
            live_authority=live_authority,
        )

    def _status_account_snapshot(self) -> dict[str, Any]:
        current = build_normalize_account_snapshot(
            self._last_account_snapshot,
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        if self._account_equity_usdt(current) > 0:
            return current
        for latest in self.repository.list_manager_runs(limit=8, include_payload=True):
            prompt = latest.get("prompt") if isinstance(latest, dict) else None
            if not isinstance(prompt, dict):
                continue
            account = prompt.get("account")
            if isinstance(account, dict):
                normalized = build_normalize_account_snapshot(
                    account,
                    default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
                )
                if self._account_equity_usdt(normalized) > 0:
                    normalized.setdefault("account_snapshot_source", "manager_run_prompt")
                    normalized.setdefault("account_snapshot_run_id", latest.get("id"))
                    return normalized
            risk_guard = prompt.get("risk_guard")
            if isinstance(risk_guard, dict):
                equity = _safe_float(risk_guard.get("current_equity_usdt"))
                if equity > 0:
                    return {
                        "status": "ok",
                        "stale": True,
                        "account_snapshot_source": "manager_run_risk_guard",
                        "account_snapshot_run_id": latest.get("id"),
                        "total_equity_usdt": equity,
                        "spot_cash_usdt": 0.0,
                        "futures_cash_usdt": 0.0,
                        "upbit_cash_usdt": 0.0,
                    }
            growth_governor = prompt.get("growth_governor")
            metrics = (
                growth_governor.get("metrics")
                if isinstance(growth_governor, dict)
                and isinstance(growth_governor.get("metrics"), dict)
                else {}
            )
            if str(metrics.get("risk_guard_status") or "") == "ok":
                equity = self.repository.get_equity_baseline(
                    self._risk_guard_period_keys()["day"],
                )
                if equity > 0:
                    return {
                        "status": "ok",
                        "stale": True,
                        "account_snapshot_source": "daily_equity_baseline",
                        "account_snapshot_run_id": latest.get("id"),
                        "total_equity_usdt": equity,
                        "spot_cash_usdt": 0.0,
                        "futures_cash_usdt": 0.0,
                        "upbit_cash_usdt": 0.0,
                        "upbit_usdt_krw_rate": max(
                            _safe_float(self.config.upbit_usdt_krw_rate),
                            1.0,
                        ),
                    }
        return current

    def _growth_governor_status_for_account(
        self,
        account: dict[str, Any],
        *,
        performance: dict[str, Any] | None = None,
        long_performance: dict[str, Any] | None = None,
        risk_guard: dict[str, Any] | None = None,
        growth_target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        perf = performance or self.repository.latest_performance_scorecard(limit=20)
        guard = risk_guard or self._risk_guard_snapshot(account)
        target = growth_target or self._growth_target_snapshot(account, persist=False)
        return self._growth_governor_context(
            growth_target=target,
            performance=perf,
            risk_guard=guard,
            long_performance=(
                long_performance
                if isinstance(long_performance, dict)
                else self.repository.latest_performance_scorecard(
                    limit=self._performance_scorecard_feedback_limit()
                )
            ),
        )

    @staticmethod
    def _growth_unlock_context(
        *,
        growth_governor: dict[str, Any],
        performance: dict[str, Any],
        live_authority: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = growth_governor.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        mode = str(
            growth_governor.get("mode")
            or growth_governor.get("status")
            or "steady"
        )
        sample_count = _safe_int(performance.get("sample_count"))
        win_rate = _safe_float(performance.get("win_rate_pct"))
        avg_r = _safe_float(performance.get("avg_r_multiple"))
        realized_pnl = _safe_float(performance.get("realized_pnl_usdt"))
        risk_guard_ok = str(metrics.get("risk_guard_status") or "").lower() == "ok"
        live_grade = str(
            live_authority.get("live_grade")
            or live_authority.get("grade")
            or live_authority.get("status")
            or ""
        ).lower()
        grade_rank = {
            "observe_only": 0,
            "missing": 0,
            "error": 0,
            "insufficient": 1,
            "restricted": 1,
            "qualified": 2,
            "scale_candidate": 3,
            "ok": 2,
        }
        live_rank = grade_rank.get(live_grade, 0)
        probe_ready = (
            mode != "halt_new_entries"
            and risk_guard_ok
            and sample_count >= 3
            and win_rate >= 45.0
            and avg_r >= 0.0
            and realized_pnl >= 0.0
        )
        immediate_ready = (
            probe_ready
            and not bool(growth_governor.get("require_waiting_entry"))
            and live_rank >= grade_rank["qualified"]
        )
        scale_ready = (
            sample_count >= 10
            and win_rate >= 52.0
            and avg_r >= 0.2
            and realized_pnl > 0.0
            and bool(live_authority.get("allow_scale_up"))
            and live_rank >= grade_rank["scale_candidate"]
        )
        if mode == "halt_new_entries":
            phase = "halted"
        elif scale_ready:
            phase = "scale_ready"
        elif immediate_ready:
            phase = "immediate_ready"
        elif probe_ready:
            phase = "probe_ready"
        else:
            phase = "rebuilding"

        criteria = [
            {
                "id": "risk_guard",
                "label": "account risk guard clear",
                "current": metrics.get("risk_guard_status") or "unknown",
                "target": "ok",
                "passed": risk_guard_ok,
            },
            {
                "id": "sample_count",
                "label": "recent closed Jue blocks",
                "current": sample_count,
                "target": ">= 8 for stable attack, >= 10 for scale",
                "passed": sample_count >= 8,
            },
            {
                "id": "win_rate",
                "label": "recent win rate",
                "current": round(win_rate, 4),
                "target": ">= 48% probe, >= 52% scale",
                "passed": win_rate >= 48.0,
            },
            {
                "id": "avg_r",
                "label": "average R multiple",
                "current": round(avg_r, 4),
                "target": ">= 0.0 probe, >= 0.2 scale",
                "passed": avg_r >= 0.0,
            },
            {
                "id": "realized_pnl",
                "label": "recent realized PnL",
                "current": round(realized_pnl, 6),
                "target": ">= 0 USDT",
                "passed": realized_pnl >= 0.0,
            },
            {
                "id": "live_authority",
                "label": "live authority grade",
                "current": live_grade or "missing",
                "target": "qualified for immediate, scale_candidate for scale",
                "passed": live_rank >= grade_rank["qualified"],
            },
        ]

        missions: list[dict[str, Any]] = []
        if not risk_guard_ok:
            missions.append(
                {
                    "priority": 1,
                    "lane": "all",
                    "mission": "restore_risk_guard",
                    "success_condition": "risk_guard_status becomes ok",
                }
            )
        if sample_count < 8:
            missions.append(
                {
                    "priority": 2,
                    "lane": "all",
                    "mission": "collect_live_samples",
                    "success_condition": f"close {8 - sample_count} more auditable Jue blocks",
                }
            )
        if win_rate < 48.0 or avg_r < 0.0 or realized_pnl < 0.0:
            missions.append(
                {
                    "priority": 3,
                    "lane": "weak_edge",
                    "mission": "rebuild_edge_quality",
                    "success_condition": (
                        "use waiting entries with pattern/book/quant confluence until "
                        "win_rate >= 48%, avg_r >= 0, and realized_pnl >= 0"
                    ),
                }
            )
        if live_rank < grade_rank["qualified"]:
            missions.append(
                {
                    "priority": 4,
                    "lane": "all",
                    "mission": "upgrade_live_authority",
                    "success_condition": "live_authority reaches qualified or better",
                }
            )
        weak_lanes = growth_governor.get("weak_lanes")
        weak_lanes = weak_lanes if isinstance(weak_lanes, list) else []
        for lane in weak_lanes[:4]:
            missions.append(
                {
                    "priority": 5,
                    "lane": str(lane),
                    "mission": "lane_specific_rebuild",
                    "success_condition": (
                        "only create small waiting-entry blocks in this lane until "
                        "lane scorecard turns non-negative"
                    ),
                }
            )

        action_permissions = {
            "new_waiting_entry_probe": mode != "halt_new_entries" and risk_guard_ok,
            "immediate_entry": immediate_ready,
            "scale_up": scale_ready,
            "volatile_attack_probe": (
                mode != "halt_new_entries"
                and risk_guard_ok
                and bool(growth_governor.get("require_waiting_entry"))
            ),
        }
        return {
            "version": "binance_growth_unlock_v1",
            "phase": phase,
            "can_leave_edge_rebuild": probe_ready or immediate_ready or scale_ready,
            "criteria": criteria,
            "action_permissions": action_permissions,
            "next_missions": missions[:8],
        }

    @staticmethod
    def _growth_governor_context(
        *,
        growth_target: dict[str, Any],
        performance: dict[str, Any],
        risk_guard: dict[str, Any],
        long_performance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sample_count = _safe_int(performance.get("sample_count"))
        win_rate = _safe_float(performance.get("win_rate_pct"))
        avg_r = _safe_float(performance.get("avg_r_multiple"))
        realized_pnl = _safe_float(performance.get("realized_pnl_usdt"))
        required_daily = _safe_float(growth_target.get("required_daily_return_pct"))
        target_status = str(growth_target.get("status") or "").strip()
        allow_by_risk_guard = bool(risk_guard.get("allow_new_entries"))
        reasons: list[str] = []
        mode = "steady"
        allow_new_blocks = True
        max_new_blocks = 2
        require_waiting_entry = False
        aggression_multiplier = 1.0

        if not allow_by_risk_guard:
            mode = "halt_new_entries"
            allow_new_blocks = False
            max_new_blocks = 0
            require_waiting_entry = True
            aggression_multiplier = 0.0
            reasons.append("risk_guard_halt")
        elif sample_count >= 5 and (
            realized_pnl < 0 or avg_r < 0 or win_rate < 45.0
        ):
            mode = "edge_rebuild"
            max_new_blocks = 2
            require_waiting_entry = True
            aggression_multiplier = 0.65
            reasons.append("recent_live_edge_weak")
        elif (
            target_status == "behind_target"
            and required_daily >= 1.5
            and sample_count >= 10
            and realized_pnl > 0
            and avg_r > 0
            and win_rate >= 52.0
        ):
            mode = "press_verified_edges"
            max_new_blocks = 3
            aggression_multiplier = 1.15
            reasons.append("behind_target_with_positive_live_edge")
        else:
            reasons.append("steady_growth_control")

        def row_is_weak(row: dict[str, Any]) -> bool:
            lane_samples = _safe_int(row.get("sample_count"))
            lane_win_rate = _safe_float(row.get("win_rate_pct"))
            lane_avg_r = _safe_float(row.get("avg_r_multiple"))
            lane_pnl = _safe_float(row.get("pnl_usdt"))
            return lane_samples >= 3 and (
                lane_pnl < 0 or lane_avg_r < 0 or lane_win_rate < 45.0
            )

        def row_is_positive(row: dict[str, Any]) -> bool:
            lane_samples = _safe_int(row.get("sample_count"))
            lane_win_rate = _safe_float(row.get("win_rate_pct"))
            lane_avg_r = _safe_float(row.get("avg_r_multiple"))
            lane_pnl = _safe_float(row.get("pnl_usdt"))
            lane_profit_factor = _safe_float(row.get("profit_factor"))
            return (
                lane_samples >= 5
                and lane_pnl > 0
                and lane_avg_r > 0
                and lane_win_rate >= 50.0
                and lane_profit_factor >= 1.2
            )

        weak_side_lanes: list[str] = []
        positive_lanes: list[str] = []
        probation_lanes: list[str] = []

        def append_positive_lane(lane: str) -> None:
            lane = lane.strip().lower()
            if lane and lane not in positive_lanes:
                positive_lanes.append(lane)

        def append_probation_lane(lane: str) -> None:
            lane = lane.strip().lower()
            if lane and lane not in probation_lanes:
                probation_lanes.append(lane)

        def long_window_confirms_scale(kind: str, lane: str) -> bool:
            if not isinstance(long_performance, dict):
                return True
            table_name = "side_scorecards" if kind == "side" else "lane_scorecards"
            key_name = "side" if kind == "side" else "lane"
            rows = long_performance.get(table_name)
            if not isinstance(rows, list):
                return False
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get(key_name) or "").strip().lower() != lane:
                    continue
                return BinanceBlockTrader._scorecard_allows_budget_scale(
                    row,
                    min_samples=5,
                    win_rate_threshold=50.0,
                )
            return False

        side_rows = performance.get("side_scorecards")
        if isinstance(side_rows, list):
            for row in side_rows:
                if not isinstance(row, dict):
                    continue
                lane = str(row.get("side") or "").strip().lower()
                if row_is_positive(row):
                    if not long_window_confirms_scale("side", lane):
                        append_probation_lane(lane)
                        continue
                    append_positive_lane(lane)
                    continue
                if not row_is_weak(row):
                    continue
                if lane and lane not in weak_side_lanes:
                    weak_side_lanes.append(lane)

        weak_horizons: list[str] = []
        weak_explicit_lanes: list[str] = []
        lane_rows = performance.get("lane_scorecards")
        if isinstance(lane_rows, list):
            for row in lane_rows:
                if not isinstance(row, dict):
                    continue
                lane = str(row.get("lane") or "").strip().lower()
                if not lane or lane == "futures":
                    continue
                if row_is_positive(row):
                    if not long_window_confirms_scale("lane", lane):
                        append_probation_lane(lane)
                        continue
                    append_positive_lane(lane)
                    continue
                if not row_is_weak(row):
                    continue
                if lane in {"short", "mid", "long"}:
                    if lane not in weak_horizons:
                        weak_horizons.append(lane)
                    continue
                if lane not in weak_explicit_lanes:
                    weak_explicit_lanes.append(lane)

        weak_lanes: list[str] = []
        for side_lane in weak_side_lanes:
            market = side_lane.split(":", 1)[0]
            if market in {"spot", UPBIT_SPOT_MARKET} and weak_horizons:
                for horizon in weak_horizons:
                    combined = f"{side_lane}:{horizon}"
                    if combined not in weak_lanes:
                        weak_lanes.append(combined)
                continue
            if side_lane not in weak_lanes:
                weak_lanes.append(side_lane)
        for lane in weak_explicit_lanes:
            if lane not in weak_lanes:
                weak_lanes.append(lane)
        if positive_lanes:
            weak_lanes = [
                lane
                for lane in weak_lanes
                if lane not in positive_lanes and "all" not in positive_lanes
            ]
            if mode == "edge_rebuild":
                reasons.append("positive_lane_recovery")
        if probation_lanes and mode == "edge_rebuild":
            reasons.append("recent_lane_recovery_probation")
        if mode == "edge_rebuild" and not weak_lanes:
            weak_lanes.append("all")

        return {
            "version": "binance_growth_governor_v1",
            "status": mode,
            "mode": mode,
            "allow_new_blocks": allow_new_blocks,
            "max_new_blocks": max_new_blocks,
            "require_waiting_entry": require_waiting_entry,
            "aggression_multiplier": round(aggression_multiplier, 4),
            "reasons": reasons,
            "weak_lanes": weak_lanes,
            "positive_lanes": positive_lanes,
            "probation_lanes": probation_lanes,
            "positive_lane_count": len(positive_lanes),
            "probation_lane_count": len(probation_lanes),
            "scope": "lane_aware" if weak_lanes and weak_lanes != ["all"] else "global",
            "metrics": {
                "growth_target_status": target_status,
                "required_daily_return_pct": round(required_daily, 4),
                "sample_count": sample_count,
                "win_rate_pct": round(win_rate, 4),
                "avg_r_multiple": round(avg_r, 4),
                "realized_pnl_usdt": round(realized_pnl, 6),
                "risk_guard_status": risk_guard.get("status"),
            },
        }

    def _manager_prompt_account(self, account: dict[str, Any]) -> dict[str, Any]:
        return build_compact_manager_account_for_prompt(
            build_normalize_account_snapshot(
                account,
                default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            )
        )

    @staticmethod
    def _spot_adoption_entry_price(asset: dict[str, Any], *, quote_price: float) -> float:
        avg_price_usdt = _safe_float(
            asset.get("avg_price_usdt") or asset.get("entry_price_usdt")
        )
        if avg_price_usdt > 0:
            asset["entry_price_source"] = "avg_price_usdt"
            return avg_price_usdt

        avg_price = _safe_float(asset.get("avg_price"))
        mark_price = _safe_float(asset.get("mark_price"))
        if avg_price > 0 and mark_price > 0 and quote_price > 0:
            asset["entry_price_source"] = "avg_mark_ratio"
            return quote_price * (avg_price / mark_price)

        asset["entry_price_source"] = "current_quote"
        return quote_price

    def kill_switch(self) -> dict[str, Any]:
        return self.repository.get_kill_switch()

    def set_kill_switch(self, enabled: bool, *, reason: str = "") -> dict[str, Any]:
        return self.repository.set_kill_switch(enabled, reason=reason)

    def prune_operational_history(
        self,
        *,
        quote_retention_days: int = 14,
        manager_run_retention_days: int = 30,
        archive_retention_days: int = 14,
    ) -> dict[str, Any]:
        return self.repository.prune_operational_history(
            quote_retention_days=quote_retention_days,
            manager_run_retention_days=manager_run_retention_days,
            archive_retention_days=archive_retention_days,
        )

    def list_blocks(self, *, include_closed: bool = True) -> list[dict[str, Any]]:
        return self.repository.list_blocks(include_closed=include_closed)

    def get_block(self, block_id: str) -> dict[str, Any] | None:
        return self.repository.get_block(block_id)

    def block_detail(self, block_id: str) -> dict[str, Any] | None:
        block = self.repository.get_block(block_id)
        if block is None:
            return None
        return {
            **block,
            "events": self.repository.list_events(block_id=block_id, limit=100),
            "orders": self.repository.list_orders(block_id=block_id, limit=100),
            "performance_reflection": self.repository.get_performance_reflection(block_id),
        }

    def create_block(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._prepare_new_block_payload(payload)
        self._validate_block(normalized)
        return self.repository.create_block(normalized)

    def _prepare_new_block_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._normalize_block_payload(self._apply_risk_sizing(payload))

    async def run_spot_adoption_once(self) -> dict[str, Any]:
        account = build_normalize_account_snapshot(
            await self._collect_account_snapshot(),
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        self._last_account_snapshot = account
        assets = build_spot_position_assets(account)
        if not assets:
            return {
                "status": "ok",
                "adopted_count": 0,
                "adopted": [],
                "skipped": [{"reason": "no_spot_positions"}],
            }

        blocks = self.repository.list_blocks(include_closed=False)
        allocated = build_allocated_qty_by_symbol(blocks, market="spot")
        adopted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for asset in assets:
            symbol = str(asset.get("symbol") or "").upper()
            qty = _safe_float(asset.get("qty"))
            available = _safe_float(asset.get("available"))
            locked = _safe_float(asset.get("locked"))
            allocatable_qty = available if available > 0 else max(qty - locked, 0.0)
            allocated_qty = allocated.get(symbol, 0.0)
            unassigned_qty = max(allocatable_qty - allocated_qty, 0.0)
            if not symbol or unassigned_qty <= 0.00000001:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "already_allocated",
                        "qty": qty,
                        "allocatable_qty": allocatable_qty,
                        "allocated_qty": allocated_qty,
                    }
                )
                continue

            quote = await self._fetch_quote(symbol=symbol, market="spot")
            quote_price = _safe_float(quote.get("price"))
            if quote_price <= 0:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "quote_unavailable",
                        "error_message": quote.get("error_message") or "",
                    }
                )
                continue
            notional_usdt = unassigned_qty * quote_price
            if notional_usdt < SPOT_ADOPTION_MIN_NOTIONAL_USDT:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "dust_below_min_notional",
                        "qty": unassigned_qty,
                        "notional_usdt": notional_usdt,
                        "min_notional_usdt": SPOT_ADOPTION_MIN_NOTIONAL_USDT,
                    }
                )
                continue

            self._runtime_market_universe.setdefault("spot", [])
            self._runtime_market_universe["spot"] = parse_universe(
                ",".join([*self._runtime_market_universe["spot"], symbol])
            )
            entry_price = self._spot_adoption_entry_price(asset, quote_price=quote_price)
            block = self.create_block(
                {
                    "symbol": symbol,
                    "market": "spot",
                    "side": "long",
                    "qty": unassigned_qty,
                    "qty_open": unassigned_qty,
                    "entry_price": entry_price,
                    "target_price": quote_price * 1.08,
                    "stop_price": quote_price * 0.95,
                    "status": "open",
                    "created_by": "wallet_adoption",
                    "thesis": (
                        "Existing Binance spot wallet position adopted into "
                        "Jue's block ledger."
                    ),
                    "llm_reason": "spot_wallet_adoption",
                    "risk_note": (
                        "Initial bracket uses +8% target and -5% stop until "
                        "the manager revises it."
                    ),
                    "metadata": {
                        "adoption": {
                            "source": "binance_spot_wallet",
                            "asset": asset.get("asset"),
                            "wallet_qty": qty,
                            "available_qty": available,
                            "locked_qty": locked,
                            "allocated_qty_before": allocated_qty,
                            "unassigned_qty": unassigned_qty,
                            "notional_usdt": notional_usdt,
                            "quote_price": quote_price,
                            "entry_price_source": asset.get("entry_price_source"),
                            "adopted_at": utc_now_iso(),
                        }
                    },
                }
            )
            allocated[symbol] = allocated_qty + unassigned_qty
            adopted.append(
                {
                    "status": "adopted",
                    "block_id": block["block_id"],
                    "symbol": symbol,
                    "qty": unassigned_qty,
                    "entry_price": entry_price,
                }
            )
        return {
            "status": "ok",
            "adopted_count": len(adopted),
            "adopted": adopted,
            "skipped": skipped,
        }

    async def run_upbit_adoption_once(self) -> dict[str, Any]:
        account = build_normalize_account_snapshot(
            await self._collect_account_snapshot(),
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        self._last_account_snapshot = account
        assets = build_upbit_position_assets(account)
        if not assets:
            return {
                "status": "ok",
                "adopted_count": 0,
                "adopted": [],
                "skipped": [{"reason": "no_upbit_spot_positions"}],
            }

        blocks = self.repository.list_blocks(include_closed=False)
        allocated = build_allocated_qty_by_symbol(blocks, market=UPBIT_SPOT_MARKET)
        adopted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for asset in assets:
            symbol = upbit_market_symbol(asset.get("symbol") or asset.get("asset"))
            qty = _safe_float(asset.get("qty"))
            available = _safe_float(asset.get("available"))
            locked = _safe_float(asset.get("locked"))
            allocatable_qty = available if available > 0 else max(qty - locked, 0.0)
            allocated_qty = allocated.get(symbol, 0.0)
            unassigned_qty = max(allocatable_qty - allocated_qty, 0.0)
            if not symbol or unassigned_qty <= 0.00000001:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "already_allocated",
                        "qty": qty,
                        "allocatable_qty": allocatable_qty,
                        "allocated_qty": allocated_qty,
                    }
                )
                continue

            quote = await self._fetch_quote(symbol=symbol, market=UPBIT_SPOT_MARKET)
            quote_price = _safe_float(quote.get("price"))
            if quote_price <= 0:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "quote_unavailable",
                        "error_message": quote.get("error_message") or "",
                    }
                )
                continue
            notional_krw = unassigned_qty * quote_price
            min_notional_krw = max(_safe_float(self.config.upbit_min_quote_budget_krw), 5_000.0)
            if notional_krw < min_notional_krw:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "dust_below_min_notional",
                        "qty": unassigned_qty,
                        "notional_krw": notional_krw,
                        "min_notional_krw": min_notional_krw,
                    }
                )
                continue

            self._runtime_market_universe.setdefault(UPBIT_SPOT_MARKET, [])
            self._runtime_market_universe[UPBIT_SPOT_MARKET] = parse_universe(
                ",".join([*self._runtime_market_universe[UPBIT_SPOT_MARKET], symbol])
            )
            entry_price = self._spot_adoption_entry_price(asset, quote_price=quote_price)
            block = self.create_block(
                {
                    "symbol": symbol,
                    "market": UPBIT_SPOT_MARKET,
                    "side": "long",
                    "qty": unassigned_qty,
                    "qty_open": unassigned_qty,
                    "entry_price": entry_price,
                    "target_price": quote_price * 1.08,
                    "stop_price": quote_price * 0.95,
                    "status": "open",
                    "created_by": "wallet_adoption",
                    "thesis": "Existing Upbit spot wallet position adopted into Jue's block ledger.",
                    "llm_reason": "upbit_spot_wallet_adoption",
                    "risk_note": (
                        "Initial KRW bracket uses +8% target and -5% stop until "
                        "the manager revises it."
                    ),
                    "metadata": {
                        "horizon": "mid",
                        "block_color": "mid",
                        "lane": "upbit_spot:long",
                        "quote_currency": "KRW",
                        "adoption": {
                            "source": "upbit_spot_wallet",
                            "asset": asset.get("asset"),
                            "wallet_qty": qty,
                            "available_qty": available,
                            "locked_qty": locked,
                            "allocated_qty_before": allocated_qty,
                            "unassigned_qty": unassigned_qty,
                            "notional_krw": notional_krw,
                            "quote_price": quote_price,
                            "entry_price_source": asset.get("entry_price_source"),
                            "adopted_at": utc_now_iso(),
                        },
                    },
                }
            )
            allocated[symbol] = allocated_qty + unassigned_qty
            adopted.append(
                {
                    "status": "adopted",
                    "block_id": block["block_id"],
                    "symbol": symbol,
                    "qty": unassigned_qty,
                    "entry_price": entry_price,
                    "notional_krw": notional_krw,
                }
            )
        return {
            "status": "ok",
            "adopted_count": len(adopted),
            "adopted": adopted,
            "skipped": skipped,
        }

    async def executor_tick(self) -> dict[str, Any]:
        if self.kill_switch().get("enabled"):
            return {
                "status": "blocked",
                "reason": "kill_switch_enabled",
                "actions": [],
                "action_count": 0,
            }
        active_rows = self.repository.list_blocks(include_closed=False)
        reconciliation_blocks = [
            row for row in active_rows if self._is_missing_spot_asset_reconciliation_block(row)
        ]
        open_blocks = [
            row
            for row in active_rows
            if row.get("status") in {"open", "exit_pending"}
            and _safe_float(row.get("qty_open")) > 0
        ]
        proposed_waiting_blocks = [
            row
            for row in active_rows
            if str(row.get("status") or "") == "proposed"
            and build_is_waiting_entry_block(row)
        ]
        if proposed_waiting_blocks and self._account_equity_usdt(self._last_account_snapshot) <= 0:
            account = build_normalize_account_snapshot(
                await self._collect_account_snapshot(),
                default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            )
            self._last_account_snapshot = account
        waiting_blocks = [
            row
            for row in proposed_waiting_blocks
            if self._execution_enabled(normalize_market(row.get("market")), for_entry=True)
        ]
        quote_keys = {
            (
                normalize_market(row.get("market")),
                str(row.get("symbol") or "").upper(),
            )
            for row in [*open_blocks, *waiting_blocks]
            if str(row.get("symbol") or "").strip()
        }
        quotes = await self._collect_quotes([*open_blocks, *waiting_blocks])
        self.repository.save_quotes(list(quotes.values()))
        actions: list[dict[str, Any]] = []
        for block in reconciliation_blocks:
            action = await self._maybe_reconcile_missing_spot_asset_block(block)
            if action:
                actions.append(action)
        for block in waiting_blocks:
            action = await self._maybe_create_entry(block)
            if action:
                actions.append(action)
        for block in open_blocks:
            action = await self._maybe_create_exit(block, quotes)
            if action:
                actions.append(action)
        return {
            "status": "ok",
            "actions": actions,
            "action_count": len(actions),
            "quote_count": len(quote_keys),
            "aux_quote_count": max(len(quotes) - len(quote_keys), 0),
        }

    @staticmethod
    def _is_missing_spot_asset_reconciliation_block(block: dict[str, Any]) -> bool:
        if str(block.get("status") or "") != "error":
            return False
        if _safe_float(block.get("qty_open")) <= 0:
            return False
        if normalize_market(block.get("market")) not in {"spot", UPBIT_SPOT_MARKET}:
            return False
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        error = metadata.get("exit_reconciliation_error")
        error_payload = error if isinstance(error, dict) else {}
        quantity_context = error_payload.get("quantity_context")
        context = quantity_context if isinstance(quantity_context, dict) else {}
        balance_source = str(context.get("balance_source") or "").strip().lower()
        message = " ".join(
            str(value or "")
            for value in (
                block.get("risk_note"),
                error_payload.get("message"),
                context.get("error_message"),
            )
        ).lower()
        return balance_source == "missing_asset" or "spot asset missing" in message

    async def _maybe_reconcile_missing_spot_asset_block(
        self,
        block: dict[str, Any],
    ) -> dict[str, Any] | None:
        market = normalize_market(block.get("market"))
        symbol = str(block.get("symbol") or "").upper().strip()
        block_id = str(block.get("block_id") or "")
        if not block_id or not symbol or market not in {"spot", UPBIT_SPOT_MARKET}:
            return None
        account = await self._spot_exit_account_snapshot()
        if not account:
            return None
        asset = self._spot_asset_for_symbol(account, symbol, market=market)
        if asset is not None:
            return None
        previous_qty = _safe_float(block.get("qty_open"))
        now = utc_now_iso()
        metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
        reconciliation = {
            "status": "missing_asset_confirmed",
            "symbol": symbol,
            "market": market,
            "previous_qty_open": previous_qty,
            "confirmed_at": now,
            "account_status": str(account.get("status") or ""),
        }
        metadata["exit_reconciled_missing_asset"] = reconciliation
        message = (
            f"reconciled missing spot asset for {symbol}; account snapshot has no "
            "matching sellable asset"
        )
        updated = self.repository.update_block(
            block_id,
            {
                "status": "closed",
                "qty_open": 0.0,
                "force_exit_requested": 0,
                "closed_at": now,
                "risk_note": message,
                "metadata": metadata,
            },
        )
        self.repository.add_event(
            block_id,
            "exit_reconciled_missing_asset",
            message,
            reconciliation,
        )
        return {
            "status": "reconciled_missing_asset",
            "block_id": block_id,
            "symbol": symbol,
            "market": market,
            "side": build_binance_exit_order_side(block),
            "qty": 0.0,
            "requested_qty": previous_qty,
            "quantity_context": {
                "balance_checked": True,
                "balance_source": "missing_asset",
                "reconciled": True,
            },
            "reason": "missing_spot_asset_reconciliation",
            "block": updated,
        }

    @staticmethod
    def _candidate_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return build_candidate_identity(row)

    @classmethod
    def _diversify_manager_candidates_by_lane(
        cls,
        candidates: list[dict[str, Any]],
        *,
        max_items: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        _ = cls
        return build_diversify_manager_candidates_by_lane(
            candidates,
            max_items=max_items,
        )

    @classmethod
    def _rank_manager_candidates_by_edge(
        cls,
        candidates: list[dict[str, Any]],
        *,
        entry_gate_policy: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        _ = cls
        return build_rank_manager_candidates_by_edge(
            candidates,
            entry_gate_policy=entry_gate_policy,
        )

    @staticmethod
    def _manager_candidate_empirical_edge_score(
        row: dict[str, Any],
        *,
        entry_gate_policy: dict[str, Any] | None = None,
    ) -> float:
        return build_manager_candidate_empirical_edge_score(
            row,
            entry_gate_policy=entry_gate_policy,
        )

    @staticmethod
    def _manager_candidate_lane_authority_bonus(row: dict[str, Any]) -> float:
        return build_manager_candidate_lane_authority_bonus(row)

    @staticmethod
    def _manager_candidate_near_duplicate_penalty(row: dict[str, Any]) -> float:
        return build_manager_candidate_near_duplicate_penalty(row)

    @staticmethod
    def _manager_candidate_freshness_penalty(row: dict[str, Any]) -> float:
        return build_manager_candidate_freshness_penalty(row)

    @staticmethod
    def _manager_candidate_performance_cooldown_penalty(
        row: dict[str, Any],
        *,
        entry_gate_policy: dict[str, Any] | None = None,
    ) -> float:
        return build_manager_candidate_performance_cooldown_penalty(
            row,
            entry_gate_policy=entry_gate_policy,
        )

    @staticmethod
    def _manager_candidate_performance_budget_penalty(row: dict[str, Any]) -> float:
        return build_manager_candidate_performance_budget_penalty(row)

    @staticmethod
    def _candidate_pattern_performance_scorecard(
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return build_candidate_pattern_performance_scorecard(row)

    @staticmethod
    def _manager_candidate_pattern_performance_penalty(
        row: dict[str, Any],
    ) -> float:
        return build_manager_candidate_pattern_performance_penalty(row)

    @staticmethod
    def _manager_candidate_pattern_performance_bonus(row: dict[str, Any]) -> float:
        return build_manager_candidate_pattern_performance_bonus(row)

    @staticmethod
    def _manager_candidate_execution_blocker_penalty(row: dict[str, Any]) -> float:
        return build_manager_candidate_execution_blocker_penalty(row)

    @staticmethod
    def _lane_authority_key_variants(value: Any) -> set[str]:
        return build_lane_authority_key_variants(value)

    @classmethod
    def _candidate_lane_authority_context(
        cls,
        live_authority: dict[str, Any] | None,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        _ = cls
        return build_candidate_lane_authority_context(live_authority, row)

    def _candidate_execution_blocker_context(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return build_candidate_execution_blocker_context(
            row,
            checks=(
                (
                    "entry_quality_performance_cooldown",
                    self._entry_quality_performance_cooldown_rejection,
                ),
                (
                    "symbol_performance_cooldown",
                    self._symbol_performance_cooldown_rejection,
                ),
                ("symbol_lane_cooldown", self._symbol_lane_cooldown_rejection),
                ("lane_performance_cooldown", self._lane_performance_cooldown_rejection),
            ),
        )

    @staticmethod
    def _lane_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return build_binance_lane_distribution(rows)

    def _near_duplicate_active_blocks_context(
        self,
        active_blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return build_near_duplicate_active_blocks_context(
            active_blocks,
            tolerance_bps=self.config.near_duplicate_block_price_tolerance_bps,
        )

    def _candidate_near_duplicate_active_block_context(
        self,
        row: dict[str, Any],
        active_blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return build_candidate_near_duplicate_active_block_context(
            row,
            active_blocks,
            tolerance_bps=self.config.near_duplicate_block_price_tolerance_bps,
        )

    def _manager_lane_balance_context(
        self,
        *,
        active_blocks: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        performance: dict[str, Any],
    ) -> dict[str, Any]:
        recent_blocks = self.repository.list_recent_strategy_blocks(limit=40)
        return build_manager_lane_balance_context(
            recent_blocks=recent_blocks,
            active_blocks=active_blocks,
            candidates=candidates,
            performance=performance,
            tolerance_bps=self.config.near_duplicate_block_price_tolerance_bps,
        )

    @staticmethod
    def _normalize_lane_review(
        *,
        response: dict[str, Any],
        lane_balance: dict[str, Any],
    ) -> dict[str, Any]:
        return build_normalize_lane_review(
            response=response,
            lane_balance=lane_balance,
        )

    async def run_manager_once(
        self,
        *,
        candidates: list[dict[str, Any]] | None = None,
        universe: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.kill_switch().get("enabled"):
            return {
                "status": "blocked",
                "reason": "kill_switch_enabled",
                "applied": {"created": [], "updated": [], "closed": [], "paused": []},
            }
        blocks = self.repository.list_blocks(include_closed=False)
        base_symbols = self._manager_symbols(
            blocks=blocks,
            candidates=candidates,
            universe=universe,
        )
        crypto_research = self._crypto_research_context(symbols=base_symbols)
        market_universe = self._build_runtime_market_universe(
            blocks=blocks,
            candidates=candidates or [],
            crypto_research=crypto_research,
        )
        crypto_research, book_generation = await self._enrich_crypto_research_with_live_books(
            crypto_research=crypto_research,
            market_universe=market_universe,
            max_items=max(int(self.config.quant_context_limit), 8),
            provided_candidates=candidates or [],
        )
        market_universe = self._build_runtime_market_universe(
            blocks=blocks,
            candidates=candidates or [],
            crypto_research=crypto_research,
        )
        self._runtime_market_universe = market_universe
        crypto_market_pulse = self._crypto_market_pulse_from_research(crypto_research)
        symbols = self._manager_symbols(
            blocks=blocks,
            candidates=candidates,
            universe=[
                *market_universe.get("spot", []),
                *market_universe.get("futures", []),
                *(upbit_market_to_usdt_symbol(symbol) for symbol in market_universe.get(UPBIT_SPOT_MARKET, [])),
            ],
        )
        crypto_alpha = self._crypto_alpha_context(symbols=symbols)
        crypto_quant = self._crypto_quant_context(symbols=symbols)
        crypto_patterns = self._crypto_pattern_context(symbols=symbols)
        memory_context = self._memory_context(symbols=symbols, blocks=blocks)
        active_block_ids = [
            str(row.get("block_id") or "")
            for row in blocks
            if str(row.get("block_id") or "")
        ]
        jue_wiki = self._wiki_context(
            target_scope="binance",
            symbols=symbols,
            page_types=[
                "risk",
                "ops",
                "research",
                "performance",
                "playbook",
                "lesson",
                "symbol",
                "regime",
            ],
            lanes=[
                "spot:long",
                "futures:long",
                "futures:short",
                "upbit_spot:long",
                "volatile_attack",
                "waiting_entry",
                "breakout_confirmed",
                "pullback_reclaim",
            ],
            regimes=[
                "crypto",
                "risk_on",
                "risk_off",
                "funding",
                "squeeze",
                "alt_rotation",
            ],
            block_ids=active_block_ids,
            horizons=["spot", "futures", "volatile_attack"],
        )
        evidence = _evidence_items_from_contexts(
            crypto_research,
            crypto_alpha,
            crypto_quant,
            crypto_patterns,
        )
        decision_packet = build_evidence_decision_packet(
            target_scope="binance",
            symbols=symbols,
            evidence=evidence,
            scorecards=list(memory_context.get("policy_scorecards") or []),
            active_policies=_generalized_binance_policies(memory_context),
            max_items=max(int(self.config.quant_context_limit), 8),
        )
        account = build_normalize_account_snapshot(
            await self._collect_account_snapshot(),
            default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )
        self._last_account_snapshot = account
        latest_quote_map = self.repository.latest_quotes_for_blocks(blocks)
        recent_events = self.repository.list_events(limit=80)
        previous_manager_runs = self.repository.list_manager_runs(
            limit=5,
            include_payload=False,
        )
        previous_manager_runs_with_payload = self.repository.list_manager_runs(
            limit=5,
            include_payload=True,
        )
        decision_packet_v2 = build_jue_decision_packet(
            account=account,
            blocks=blocks,
            quotes=list(latest_quote_map.values()),
            recent_events=recent_events,
            previous_manager_runs=previous_manager_runs,
            market_pulse=crypto_market_pulse,
            target_scope="binance",
            source_context={
                "crypto_research": crypto_research,
                "crypto_alpha": crypto_alpha,
                "crypto_quant": crypto_quant,
                "crypto_patterns": crypto_patterns,
                "crypto_market_pulse": crypto_market_pulse,
            },
        )
        growth_target = self._growth_target_snapshot(account, persist=True)
        risk_guard = self._risk_guard_snapshot(account)
        live_authority = self._live_authority_context()
        self._last_live_authority_context = dict(live_authority)
        live_authority_prompt = compact_live_authority_for_prompt(live_authority)
        recent_performance = self.repository.latest_performance_scorecard(limit=20)
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        entry_gate_policy = self._entry_gate_policy(
            performance=recent_performance,
            memory_context=memory_context,
        )
        entry_gate_policy = self._augment_entry_gate_policy_candidate_symbol_cooldowns(
            entry_gate_policy,
            symbols=symbols,
        )
        executable_candidates, candidate_generation = self._manager_executable_candidates(
            provided_candidates=candidates or [],
            crypto_research=crypto_research,
            crypto_patterns=crypto_patterns,
            live_authority=live_authority,
            market_universe=market_universe,
            account=account,
            active_blocks=blocks,
            entry_gate_policy=entry_gate_policy,
        )
        executable_candidates = _attach_binance_candidate_memory_hints(
            executable_candidates,
            memory_context,
        )
        candidate_generation = {**candidate_generation, **book_generation}
        growth_governor = self._growth_governor_context(
            growth_target=growth_target,
            performance=recent_performance,
            risk_guard=risk_guard,
            long_performance=performance,
        )
        growth_unlock = self._growth_unlock_context(
            growth_governor=growth_governor,
            performance=recent_performance,
            live_authority=live_authority,
        )
        lane_balance = self._manager_lane_balance_context(
            active_blocks=blocks,
            candidates=executable_candidates,
            performance=performance,
        )
        candidate_policy_impacts = _binance_candidate_policy_impacts(
            memory_context,
            symbols,
        )
        validation_repair = build_compact_validation_repair_prompt(
            memory_context,
            scope="binance",
            compact_value=build_compact_prompt_value,
        )
        prompt_candidates = [
            self._compact_manager_candidate_for_prompt(row)
            for row in executable_candidates
            if isinstance(row, dict)
        ]
        self._last_manager_candidate_index = self._manager_candidate_index(executable_candidates)
        self._last_manager_quant_context = crypto_quant
        self._last_manager_entry_gate_policy = entry_gate_policy
        self._last_manager_growth_governor = growth_governor
        self._last_manager_growth_unlock = growth_unlock
        self._last_manager_policy_impacts = candidate_policy_impacts
        language_policy = jue_language_policy()
        prompt = build_binance_manager_prompt_payload(
            allowed_actions=list(ALLOWED_MANAGER_ACTIONS),
            language_policy=language_policy,
            manager_lanes=list(BINANCE_MANAGER_LANES),
            account=self._manager_prompt_account(account),
            growth_target=growth_target,
            growth_governor=growth_governor,
            growth_unlock=growth_unlock,
            risk_guard=risk_guard,
            execution_gate=self._manager_execution_gate_context(
                account=account,
                blocks=blocks,
                lane_balance=lane_balance,
            ),
            memory_context=memory_context,
            decision_packet_v2=decision_packet_v2,
            decision_packet=decision_packet,
            candidate_policy_impacts=candidate_policy_impacts,
            validation_repair=validation_repair,
            crypto_market_pulse=crypto_market_pulse,
            raw_context_refs={
                "crypto_research": _compact_raw_context_ref(crypto_research),
                "crypto_alpha": _compact_raw_context_ref(crypto_alpha),
                "crypto_quant": _compact_raw_context_ref(crypto_quant),
                "crypto_patterns": _compact_raw_context_ref(crypto_patterns),
            },
            recent_performance=recent_performance,
            performance=performance,
            entry_gate_policy=entry_gate_policy,
            live_authority=live_authority_prompt,
            lane_balance=lane_balance,
            candidate_generation=candidate_generation,
            candidates=prompt_candidates,
            universe=symbols,
            market_universe=market_universe,
            blocks=blocks,
        )
        jue_workflow = _compile_jue_workflow_prompt_pack("binance_cycle")
        if jue_workflow.get("status") != "error":
            cadence = dict(jue_workflow.get("cadence") if isinstance(jue_workflow.get("cadence"), dict) else {})
            cadence["interval_sec"] = int(self.config.manager_interval_sec)
            cadence["runtime_interval_sec"] = int(self.config.manager_interval_sec)
            jue_workflow = {**jue_workflow, "cadence": cadence}
        prompt["jue_workflow"] = jue_workflow
        proactive_pressure = self._proactive_decision_pressure(
            previous_manager_runs=previous_manager_runs_with_payload,
            executable_candidates=executable_candidates,
            growth_governor=growth_governor,
            live_authority=live_authority,
        )
        if str(proactive_pressure.get("status") or "") != "idle":
            prompt["proactive_decision_pressure"] = proactive_pressure
            decision_inputs = list(prompt.get("decision_inputs") or [])
            if "proactive_decision_pressure" not in decision_inputs:
                decision_inputs.append("proactive_decision_pressure")
            prompt["decision_inputs"] = decision_inputs
        _attach_jue_wiki_prompt_context(
            prompt,
            jue_wiki,
            max_chars=self.config.prompt_max_chars,
        )
        prompt["native_thread_mode"] = "ephemeral"
        latency_guard = build_manager_latency_guard_from_runs(
            self.repository.list_manager_runs(limit=6, include_payload=False),
            target_chars=self.config.prompt_target_chars,
        )
        build_apply_manager_latency_guard(
            prompt,
            latency_guard=latency_guard,
            target_chars=self.config.prompt_target_chars,
        )
        build_finalize_prompt_budget(
            prompt,
            target_chars=self.config.prompt_target_chars,
            warn_chars=self.config.prompt_warn_chars,
            max_chars=self.config.prompt_max_chars,
        )
        response: dict[str, Any] = {}
        try:
            budget_error = build_prompt_budget_error(prompt)
            if budget_error:
                raise RuntimeError(budget_error)
            response = await self._complete_manager_json(prompt)
            actions = build_validate_manager_actions(
                response,
                normalize_create_payload=self._normalize_manager_contract_create_payload,
                allowed_actions=ALLOWED_MANAGER_ACTIONS,
            )
            actions = self._apply_validation_repair_to_actions(
                actions,
                validation_repair=validation_repair,
            )
            actions = _attach_prompt_jue_wiki_decision_adjustments_to_actions(
                actions,
                prompt=prompt,
            )
            hold_decision = build_normalize_manager_hold_decision(
                response=response,
                actions=actions,
                symbols=symbols,
                allowed_actions=ALLOWED_MANAGER_ACTIONS,
            )
            lane_review = self._normalize_lane_review(
                response=response,
                lane_balance=lane_balance,
            )
            if str(lane_review.get("status") or "") != "provided":
                raise RuntimeError("lane_review_missing_from_model")
            response = {**response, "hold_decision": hold_decision, "lane_review": lane_review}
            contract_error = build_manager_response_contract_error(
                prompt=prompt,
                response=response,
                actions=actions,
                hold_decision=hold_decision,
            )
            if contract_error:
                raise RuntimeError(contract_error)
            manager_run_id = self.repository.save_manager_run(
                prompt=prompt,
                response=response,
                actions=actions,
                model=str(getattr(self.codex_runtime, "resolved_model", self.config.llm_model)),
            )
            applied = await self._apply_manager_actions(
                actions,
                manager_run_id=manager_run_id,
            )
            response = {**response, "applied": applied}
            self.repository.update_manager_run_response(manager_run_id, response)
            return {
                "status": "ok",
                "manager_run_id": manager_run_id,
                "actions": actions,
                "hold_decision": hold_decision,
                "lane_review": lane_review,
                "applied": applied,
            }
        except Exception as exc:
            logger.warning("binance block manager failed: %s", exc)
            manager_run_id = self.repository.save_manager_run(
                prompt=prompt,
                response=response,
                actions={key: [] for key in sorted(ALLOWED_MANAGER_ACTIONS)},
                status="error",
                error_message=str(exc),
                model=str(getattr(self.codex_runtime, "resolved_model", self.config.llm_model)),
            )
            await self._notify_prompt_budget_error(
                run_id=manager_run_id,
                error_message=str(exc),
                prompt=prompt,
                venue="Binance",
            )
            return {
                "status": "error",
                "manager_run_id": manager_run_id,
                "error_message": str(exc),
                "applied": {
                    "adopted": [],
                    "created": [],
                    "updated": [],
                    "closed": [],
                    "paused": [],
                },
            }

    def run_performance_feedback_once(
        self,
        *,
        refresh_existing: bool = False,
    ) -> dict[str, Any]:
        closed_blocks = [
            block
            for block in self.repository.list_blocks(include_closed=True)
            if block.get("status") == "closed"
        ]
        count = 0
        live_performance_count = 0
        for block in closed_blocks:
            block_id = str(block.get("block_id") or "")
            if not block_id:
                continue
            if self._block_is_reconciliation_only_close(block):
                existing = self.repository.get_performance_reflection(block_id)
                if existing:
                    self._save_live_performance(
                        block=block,
                        reflection=existing,
                        execution_defect_reason="reconciliation_only_close",
                    )
                    live_performance_count += 1
                continue
            existing = self.repository.get_performance_reflection(block_id)
            if existing and not refresh_existing:
                if self._performance_reflection_needs_cost_refresh(existing):
                    refreshed_reflection = self._build_performance_reflection(block)
                    if (
                        refreshed_reflection is not None
                        and self._performance_reflection_has_stronger_cost_evidence(
                            refreshed_reflection,
                            existing,
                        )
                    ):
                        self.repository.save_performance_reflection(refreshed_reflection)
                        self._save_live_performance(
                            block=block,
                            reflection=refreshed_reflection,
                        )
                        live_performance_count += 1
                        count += 1
                        continue
                self._save_live_performance(block=block, reflection=existing)
                live_performance_count += 1
                continue
            reflection = self._build_performance_reflection(block)
            if reflection is None:
                continue
            self.repository.save_performance_reflection(reflection)
            self._save_live_performance(block=block, reflection=reflection)
            live_performance_count += 1
            if not existing:
                self._save_quant_outcome(reflection)
            count += 1
        return {
            "status": "ok",
            "reflection_count": count,
            "live_performance_count": live_performance_count,
        }

    @staticmethod
    def _block_is_reconciliation_only_close(block: dict[str, Any]) -> bool:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        reconciliation = metadata.get("exit_reconciled_missing_asset")
        return isinstance(reconciliation, dict) and bool(reconciliation)

    @staticmethod
    def _performance_reflection_cost_rank(reflection: dict[str, Any]) -> int:
        source = str(reflection.get("cost_source") or "").strip().lower()
        if source == "explicit":
            return 4
        if source == "partial_unconverted_fee":
            return 3
        if source == "unconverted_fee":
            return 2
        if (
            _safe_float(reflection.get("total_cost_usdt")) > 0
            or _safe_float(reflection.get("fee_usdt")) > 0
            or _safe_float(reflection.get("funding_usdt")) > 0
            or _safe_float(reflection.get("slippage_usdt")) > 0
            or _safe_float(reflection.get("spread_usdt")) > 0
            or list(reflection.get("present_cost_components") or [])
        ):
            return 1
        return 0

    @classmethod
    def _performance_reflection_needs_cost_refresh(
        cls,
        reflection: dict[str, Any],
    ) -> bool:
        return cls._performance_reflection_cost_rank(reflection) < 4

    @classmethod
    def _performance_reflection_has_stronger_cost_evidence(
        cls,
        candidate: dict[str, Any],
        existing: dict[str, Any],
    ) -> bool:
        candidate_rank = cls._performance_reflection_cost_rank(candidate)
        existing_rank = cls._performance_reflection_cost_rank(existing)
        if candidate_rank > existing_rank:
            return True
        if candidate_rank < existing_rank:
            return False
        candidate_components = {
            str(component)
            for component in list(candidate.get("present_cost_components") or [])
            if str(component).strip()
        }
        existing_components = {
            str(component)
            for component in list(existing.get("present_cost_components") or [])
            if str(component).strip()
        }
        if candidate_components > existing_components:
            return True
        candidate_cost = _safe_float(candidate.get("total_cost_usdt"))
        existing_cost = _safe_float(existing.get("total_cost_usdt"))
        return candidate_rank > 0 and candidate_cost > existing_cost

    def _save_quant_outcome(self, reflection: dict[str, Any]) -> None:
        if self.quant_provider is None:
            return
        save_outcome = getattr(self.quant_provider, "save_outcome", None)
        if save_outcome is None:
            return
        r_multiple = _safe_float(reflection.get("r_multiple"))
        try:
            save_outcome(
                {
                    "symbol": reflection.get("symbol"),
                    "side": reflection.get("side"),
                    "horizon": "block_lifecycle",
                    "source_id": reflection.get("block_id"),
                    "outcome": "closed_positive" if r_multiple > 0 else "closed_negative",
                    "r_multiple": r_multiple,
                    "mfe_r": _safe_float(reflection.get("mfe_r_multiple")),
                    "mae_r": _safe_float(reflection.get("mae_r_multiple")),
                    "entry_price": _safe_float(reflection.get("entry_price")),
                    "exit_price": _safe_float(reflection.get("exit_price")),
                    "target_price": _safe_float(reflection.get("target_price")),
                    "stop_price": _safe_float(reflection.get("stop_price")),
                    "pattern_key": reflection.get("pattern_key"),
                    "labeled_at": utc_now_iso(),
                }
            )
        except Exception as exc:
            logger.warning("binance quant outcome save failed: %s", exc)

    def _fee_asset_usdt_conversion_price(self, asset: str) -> float:
        symbol = FEE_CONVERSION_QUOTE_SYMBOLS.get(str(asset or "").upper().strip())
        if not symbol:
            return 0.0
        end_at = utc_now_iso()
        start_at = (
            datetime.now(timezone.utc)
            - timedelta(days=FEE_CONVERSION_QUOTE_LOOKBACK_DAYS)
        ).isoformat()
        prices = self.repository.quote_prices(
            symbol=symbol,
            market="spot",
            start_at=start_at,
            end_at=end_at,
            limit=2000,
        )
        if not prices:
            prices = self.repository.quote_prices(
                symbol=symbol,
                market="spot",
                start_at="",
                end_at=end_at,
                limit=2000,
            )
        return prices[-1] if prices else 0.0

    def _performance_cost_components_from_payload(
        self,
        payload: Any,
        *,
        symbol: str,
        market: str,
        fallback_price: float,
    ) -> dict[str, Any]:
        base_asset, quote_asset = build_symbol_base_quote(symbol, market=market)
        totals = {
            "fee_usdt": 0.0,
            "funding_usdt": 0.0,
            "slippage_usdt": 0.0,
            "spread_usdt": 0.0,
        }
        present_components: set[str] = set()
        unconverted: list[dict[str, Any]] = []
        source_count = 0
        for row in build_iter_payload_dicts(payload):
            asset = str(
                row.get("commissionAsset")
                or row.get("commission_asset")
                or row.get("feeAsset")
                or row.get("fee_asset")
                or row.get("asset")
                or row.get("currency")
                or ""
            ).upper()
            price = (
                build_first_float(
                    row,
                    (
                        "price",
                        "avgPrice",
                        "avg_price",
                        "avg_fill_price",
                        "lastPrice",
                        "last_price",
                    ),
                )
                or fallback_price
            )
            structured_fee = False
            for key in (
                "commission",
                "commissionAmount",
                "commission_amount",
                "feeAmount",
                "fee_amount",
            ):
                if key not in row:
                    continue
                amount = _safe_float(row.get(key))
                present_components.add("fees")
                if amount == 0:
                    continue
                totals["fee_usdt"] += build_asset_amount_to_usdt(
                    amount=amount,
                    asset=asset,
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    price=price,
                    upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
                    conversion_price_provider=self._fee_asset_usdt_conversion_price,
                    unconverted=unconverted,
                )
                structured_fee = True
                source_count += 1
            fee_payload = row.get("fee")
            if isinstance(fee_payload, dict):
                amount = build_first_float(fee_payload, ("cost", "amount", "fee"))
                fee_asset = str(
                    fee_payload.get("currency")
                    or fee_payload.get("asset")
                    or asset
                    or ""
                )
                present_components.add("fees")
                if amount != 0:
                    totals["fee_usdt"] += build_asset_amount_to_usdt(
                        amount=amount,
                        asset=fee_asset,
                        base_asset=base_asset,
                        quote_asset=quote_asset,
                        price=price,
                        upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
                        conversion_price_provider=self._fee_asset_usdt_conversion_price,
                        unconverted=unconverted,
                    )
                    structured_fee = True
                    source_count += 1
            for key in ("fee_usdt", "fees_usdt", "commission_usdt"):
                if key in row:
                    present_components.add("fees")
                    totals["fee_usdt"] += _safe_float(row.get(key))
                    source_count += 1
            if not structured_fee and "fees" in row and not isinstance(row.get("fees"), list):
                present_components.add("fees")
                value = _safe_float(row.get("fees"))
                if value != 0:
                    totals["fee_usdt"] += value
                    source_count += 1

            for key in ("slippage_usdt", "slippage", "slippage_cost_usdt"):
                if key in row:
                    present_components.add("slippage")
                    totals["slippage_usdt"] += _safe_float(row.get(key))
                    source_count += 1
            for key in ("spread_usdt", "spread_cost_usdt", "spread_cost"):
                if key in row:
                    present_components.add("spread")
                    totals["spread_usdt"] += _safe_float(row.get(key))
                    source_count += 1

            income_type = str(row.get("incomeType") or row.get("income_type") or "").upper()
            if "FUNDING" in income_type and "income" in row:
                present_components.add("funding")
                income = _safe_float(row.get("income"))
                income_asset = str(row.get("asset") or row.get("incomeAsset") or "USDT")
                funding_cost = build_asset_amount_to_usdt(
                    amount=-income,
                    asset=income_asset,
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    price=price,
                    upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
                    conversion_price_provider=self._fee_asset_usdt_conversion_price,
                    unconverted=unconverted,
                )
                totals["funding_usdt"] += funding_cost
                source_count += 1
            for key in ("funding_usdt", "funding_fee_usdt", "funding_fee", "funding"):
                if key in row:
                    present_components.add("funding")
                    totals["funding_usdt"] += _safe_float(row.get(key))
                    source_count += 1
        return {
            **totals,
            "source_count": source_count,
            "present_cost_components": sorted(present_components),
            "unconverted_fee_assets": unconverted[:8],
        }

    def _estimated_missing_performance_cost_model(
        self,
        *,
        block: dict[str, Any],
        market: str,
        entry_price: float,
    ) -> dict[str, Any]:
        qty = _safe_float(block.get("qty_initial") or block.get("qty_open") or block.get("qty"))
        if entry_price <= 0 or qty <= 0:
            return {}
        round_trip_cost_pct = MANAGER_COST_EDGE_BASE_ROUND_TRIP_PCT.get(market, 0.0)
        if round_trip_cost_pct <= 0:
            return {}
        notional = entry_price * qty
        if is_upbit_market(market):
            notional = notional / max(_safe_float(self.config.upbit_usdt_krw_rate), 1.0)
        estimated_cost = notional * round_trip_cost_pct / 100.0
        if estimated_cost <= 0:
            return {}
        return {
            "version": "binance_missing_cost_notional_estimate_v1",
            "market": market,
            "notional_usdt": notional,
            "round_trip_cost_pct": round_trip_cost_pct,
            "estimated_cost_usdt": estimated_cost,
        }

    def _performance_costs(self, block: dict[str, Any]) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        symbol = str(block.get("symbol") or "").upper()
        market = normalize_market(block.get("market"))
        fallback_price = _safe_float(block.get("entry_price"))
        fee = funding = slippage = spread = 0.0
        source_count = 0
        present_components: set[str] = set()
        unconverted: list[dict[str, Any]] = []
        initial = self._performance_cost_components_from_payload(
            metadata,
            symbol=symbol,
            market=market,
            fallback_price=fallback_price,
        )
        fee += _safe_float(initial.get("fee_usdt"))
        funding += _safe_float(initial.get("funding_usdt"))
        slippage += _safe_float(initial.get("slippage_usdt"))
        spread += _safe_float(initial.get("spread_usdt"))
        source_count += _safe_int(initial.get("source_count"))
        present_components.update(list(initial.get("present_cost_components") or []))
        unconverted.extend(list(initial.get("unconverted_fee_assets") or []))
        for order in self.repository.list_orders(
            block_id=str(block.get("block_id") or ""),
            limit=100,
        ):
            response = order.get("response") if isinstance(order.get("response"), dict) else {}
            order_without_response = {
                key: value for key, value in order.items() if key != "response"
            }
            for payload in (order_without_response, response):
                components = self._performance_cost_components_from_payload(
                    payload,
                    symbol=symbol,
                    market=market,
                    fallback_price=(
                        build_first_float(
                            response,
                            ("avg_fill_price", "avgPrice", "price"),
                        )
                        or fallback_price
                    ),
                )
                fee += _safe_float(components.get("fee_usdt"))
                funding += _safe_float(components.get("funding_usdt"))
                slippage += _safe_float(components.get("slippage_usdt"))
                spread += _safe_float(components.get("spread_usdt"))
                source_count += _safe_int(components.get("source_count"))
                present_components.update(
                    list(components.get("present_cost_components") or [])
                )
                unconverted.extend(list(components.get("unconverted_fee_assets") or []))
        total = fee + funding + slippage + spread
        cost_source = "missing"
        estimated_cost_model: dict[str, Any] = {}
        if source_count > 0:
            cost_source = "explicit"
        if unconverted:
            cost_source = "partial_unconverted_fee" if source_count > 0 else "unconverted_fee"
        if source_count <= 0 and not unconverted:
            estimated_cost_model = self._estimated_missing_performance_cost_model(
                block=block,
                market=market,
                entry_price=fallback_price,
            )
            estimated_cost = _safe_float(estimated_cost_model.get("estimated_cost_usdt"))
            if estimated_cost > 0:
                slippage += estimated_cost
                total = fee + funding + slippage + spread
                present_components.add("estimated_round_trip_cost")
                cost_source = "estimated_from_notional"
        return {
            "fee_usdt": fee,
            "funding_usdt": funding,
            "slippage_usdt": slippage,
            "spread_usdt": spread,
            "total_cost_usdt": total,
            "cost_source": cost_source,
            "present_cost_components": sorted(present_components),
            "unconverted_fee_assets": unconverted[:8],
            "estimated_cost_model": estimated_cost_model,
        }

    def _build_performance_reflection(self, block: dict[str, Any]) -> dict[str, Any] | None:
        if not self._block_has_effective_entry(block):
            return None
        entry = _safe_float(block.get("entry_price"))
        stop = _safe_float(block.get("stop_price"))
        risk_stop = self._performance_risk_stop_price(block)
        target = _safe_float(block.get("target_price"))
        exit_price = self._closed_exit_price(str(block.get("block_id") or ""))
        if entry <= 0 or stop <= 0 or risk_stop <= 0 or exit_price <= 0:
            return None
        risk = abs(entry - risk_stop)
        if risk <= 0:
            return None
        side = normalize_position_side(block.get("side"))
        qty = _safe_float(block.get("qty_initial"))
        pnl_per_unit = exit_price - entry if side == "long" else entry - exit_price
        gross_pnl_quote = pnl_per_unit * qty
        gross_pnl = (
            gross_pnl_quote / max(_safe_float(self.config.upbit_usdt_krw_rate), 1.0)
            if is_upbit_market(block.get("market"))
            else gross_pnl_quote
        )
        costs = self._performance_costs(block)
        net_pnl = gross_pnl - _safe_float(costs.get("total_cost_usdt"))
        r_multiple = pnl_per_unit / risk
        path = self._block_price_path(block=block, exit_price=exit_price)
        mfe_r, mae_r = self._mfe_mae_r(
            side=side,
            entry=entry,
            risk=risk,
            prices=path,
        )
        pattern_key = self._block_pattern_key(block)
        attribution = self._performance_attribution_for_block(block)
        return {
            "block_id": block.get("block_id"),
            "symbol": block.get("symbol"),
            "market": block.get("market"),
            "side": side,
            "lane": block.get("lane") or block.get("metadata", {}).get("lane", ""),
            "entry_price": entry,
            "exit_price": exit_price,
            "stop_price": stop,
            "target_price": target,
            "pnl_usdt": net_pnl,
            "gross_pnl_usdt": gross_pnl,
            "net_pnl_usdt": net_pnl,
            "fee_usdt": costs["fee_usdt"],
            "funding_usdt": costs["funding_usdt"],
            "slippage_usdt": costs["slippage_usdt"],
            "spread_usdt": costs["spread_usdt"],
            "total_cost_usdt": costs["total_cost_usdt"],
            "cost_source": costs["cost_source"],
            "present_cost_components": costs["present_cost_components"],
            "r_multiple": r_multiple,
            "mfe_r_multiple": mfe_r,
            "mae_r_multiple": mae_r,
            "pattern_key": pattern_key,
            "created_at": str(block.get("closed_at") or "") or utc_now_iso(),
            "lesson": {
                "thesis": block.get("thesis", ""),
                "result": "positive" if r_multiple > 0 else "negative",
                "r_multiple": r_multiple,
                "gross_pnl_usdt": gross_pnl,
                "net_pnl_usdt": net_pnl,
                "total_cost_usdt": costs["total_cost_usdt"],
                "fee_usdt": costs["fee_usdt"],
                "funding_usdt": costs["funding_usdt"],
                "slippage_usdt": costs["slippage_usdt"],
                "spread_usdt": costs["spread_usdt"],
                "cost_source": costs["cost_source"],
                "present_cost_components": costs["present_cost_components"],
                "unconverted_fee_assets": costs.get("unconverted_fee_assets") or [],
                "estimated_cost_model": costs.get("estimated_cost_model") or {},
                "risk_stop_price": risk_stop,
                "final_stop_price": stop,
                "mfe_r_multiple": mfe_r,
                "mae_r_multiple": mae_r,
                "pattern_key": pattern_key,
                "lane": block.get("lane") or block.get("metadata", {}).get("lane", ""),
                **attribution,
            },
        }

    @staticmethod
    def _performance_attribution_for_block(block: dict[str, Any]) -> dict[str, Any]:
        created_by = str(block.get("created_by") or "").strip().lower()
        if created_by == "existing_position":
            return {
                "created_by": "existing_position",
                "entry_attribution": "external_existing_position",
                "management_attribution": "jue_block_management",
                "scorecard_attribution": "risk_management_only",
                "include_in_entry_alpha_scorecard": False,
                "entry_alpha_exclusion_reason": "existing_position_adoption",
            }
        if created_by == "wallet_adoption":
            return {
                "created_by": "wallet_adoption",
                "entry_attribution": "external_wallet_position",
                "management_attribution": "jue_block_management",
                "scorecard_attribution": "risk_management_only",
                "include_in_entry_alpha_scorecard": False,
                "entry_alpha_exclusion_reason": "wallet_adoption",
            }
        return {
            "created_by": created_by or "llm",
            "entry_attribution": "jue_block_entry",
            "management_attribution": "jue_block_management",
            "scorecard_attribution": "entry_and_management",
            "include_in_entry_alpha_scorecard": True,
            "entry_alpha_exclusion_reason": "",
        }

    def _save_live_performance(
        self,
        *,
        block: dict[str, Any],
        reflection: dict[str, Any],
        execution_defect_reason: str = "",
    ) -> dict[str, Any]:
        market = normalize_market(block.get("market") or reflection.get("market"))
        venue = "upbit" if is_upbit_market(market) else "binance"
        side = normalize_position_side(block.get("side") or reflection.get("side"))
        lane = str(reflection.get("lane") or block.get("lane") or "").strip()
        if not lane:
            lane = binance_performance_lane_from_payload(
                {
                    "market": market,
                    "side": side,
                },
                {
                    "lane": lane,
                    "market": market,
                    "side": side,
                },
            )
        cost_source = str(reflection.get("cost_source") or "").strip() or "missing"
        cost_model_status = (
            "recorded"
            if cost_source == "explicit"
            else cost_source
            if cost_source
            else "missing"
        )
        present_components = [
            str(component)
            for component in list(reflection.get("present_cost_components") or [])
            if str(component).strip()
        ]
        attribution = self._performance_attribution_for_block(block)
        metadata = {
            "version": "binance_live_performance_sync_v1",
            "market": market,
            "side": side,
            "lane": lane,
            "cost_model_status": cost_model_status,
            "cost_source": f"binance_block_reflection:{cost_source}",
            "present_cost_components": present_components,
            "cost_components": {
                key: value
                for key, value in {
                    "fees": _safe_float(reflection.get("fee_usdt")),
                    "funding": _safe_float(reflection.get("funding_usdt")),
                    "slippage": _safe_float(reflection.get("slippage_usdt")),
                    "spread": _safe_float(reflection.get("spread_usdt")),
                }.items()
                if key in present_components and value != 0
            },
            "block_closed_at": str(block.get("closed_at") or ""),
            "pattern_key": str(reflection.get("pattern_key") or ""),
            **attribution,
        }
        strategy_revision_id = self._performance_strategy_revision_id(
            block=block,
            reflection=reflection,
        )
        if strategy_revision_id:
            metadata["strategy_revision_id"] = strategy_revision_id
        fill_evidence = self._live_performance_fill_evidence(
            block=block,
            reflection=reflection,
        )
        metadata.update(
            {
                "fill_evidence_status": str(fill_evidence.get("status") or ""),
                "fill_evidence_reason": str(fill_evidence.get("reason") or ""),
                "entry_order_filled": bool(fill_evidence.get("entry_order_filled")),
                "exit_order_filled": bool(fill_evidence.get("exit_order_filled")),
                "entry_price_source": str(
                    fill_evidence.get("entry_price_source") or "block.entry_price"
                ),
                "exit_price_source": str(
                    fill_evidence.get("exit_price_source") or "reflection_or_order"
                ),
            }
        )
        if execution_defect_reason:
            metadata["execution_defect"] = True
            metadata["execution_defect_reason"] = str(execution_defect_reason)
        return self.live_performance_repository.upsert_performance(
            BlockPerformanceInput(
                venue=venue,
                block_id=str(block.get("block_id") or reflection.get("block_id") or ""),
                symbol=str(block.get("symbol") or reflection.get("symbol") or "").upper(),
                created_by=str(block.get("created_by") or ""),
                status=str(block.get("status") or "closed"),
                entry_price=_safe_float(reflection.get("entry_price")),
                exit_price=_safe_float(reflection.get("exit_price")),
                qty=_safe_float(block.get("qty_initial") or block.get("qty_open")),
                fees=_safe_float(reflection.get("fee_usdt")),
                taxes=0.0,
                funding=_safe_float(reflection.get("funding_usdt")),
                slippage=_safe_float(reflection.get("slippage_usdt")),
                spread=_safe_float(reflection.get("spread_usdt")),
                filled=bool(fill_evidence.get("filled")),
                metadata=metadata,
            ),
            source={
                "block": block,
                "metadata": metadata,
                "reflection": reflection,
            },
        )

    @staticmethod
    def _performance_strategy_revision_id(
        *,
        block: dict[str, Any],
        reflection: dict[str, Any],
    ) -> str:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        lesson = (
            reflection.get("lesson")
            if isinstance(reflection.get("lesson"), dict)
            else {}
        )
        for source in (metadata, reflection, lesson):
            for key in ("strategy_revision_id", "jue_strategy_revision_id", "revision_id"):
                value = str(source.get(key) or "").strip()
                if value:
                    return value[:120]
        return ""

    def _live_performance_fill_evidence(
        self,
        *,
        block: dict[str, Any],
        reflection: dict[str, Any],
    ) -> dict[str, Any]:
        if str(block.get("status") or "").strip().lower() != "closed":
            return {
                "filled": False,
                "status": "not_closed_round_trip",
                "reason": f"status={block.get('status') or ''}",
            }
        entry_side = build_entry_order_side(block)
        exit_side = "sell" if entry_side == "buy" else "buy"
        orders = self.repository.list_orders(
            block_id=str(block.get("block_id") or ""),
            limit=100,
        )
        opened_at = str(block.get("opened_at") or "").strip()
        entry_filled = bool(opened_at)
        entry_price_source = "block.opened_at" if opened_at else ""
        exit_filled = False
        exit_price_source = ""
        for order in orders:
            reason = str(order.get("reason") or "")
            side = str(order.get("side") or "").strip().lower()
            filled = self._order_has_effective_fill(order)
            if reason == "entry_order" and (not side or side == entry_side) and filled:
                entry_filled = True
                entry_price_source = "entry_order_fill"
            if reason != "entry_order" and (not side or side == exit_side) and filled:
                exit_filled = True
                exit_price_source = "exit_order_fill"
        reflection_exit = _safe_float(reflection.get("exit_price"))
        if reflection_exit > 0:
            exit_filled = True
            if not exit_price_source:
                exit_price_source = "performance_reflection"
        if not entry_filled:
            return {
                "filled": False,
                "status": "missing_entry_evidence",
                "reason": "no_opened_at_or_entry_fill",
                "entry_order_filled": False,
                "exit_order_filled": exit_filled,
                "entry_price_source": entry_price_source,
                "exit_price_source": exit_price_source,
            }
        if not exit_filled:
            return {
                "filled": False,
                "status": "missing_exit_evidence",
                "reason": "no_exit_fill_or_reflection_exit",
                "entry_order_filled": entry_filled,
                "exit_order_filled": False,
                "entry_price_source": entry_price_source,
                "exit_price_source": exit_price_source,
            }
        return {
            "filled": True,
            "status": (
                "reflection_round_trip_recorded"
                if reflection_exit > 0
                else "order_round_trip_filled"
            ),
            "reason": "",
            "entry_order_filled": entry_filled,
            "exit_order_filled": exit_filled,
            "entry_price_source": entry_price_source or "block.entry_price",
            "exit_price_source": exit_price_source or "closed_order_or_event",
        }

    @staticmethod
    def _order_has_effective_fill(order: dict[str, Any]) -> bool:
        status = str(order.get("status") or "").strip().lower()
        response = order.get("response") if isinstance(order.get("response"), dict) else {}
        response_status = str(response.get("status") or "").strip().upper()
        return (
            status == "paper"
            or response_status in {"FILLED", "PARTIALLY_FILLED"}
            or build_response_filled_qty(response, requested_qty=0.0) > 0
            or build_filled_order_price(order) > 0
        )

    @staticmethod
    def _performance_risk_stop_price(block: dict[str, Any]) -> float:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        for value in (
            metadata.get("initial_stop_price"),
            metadata.get("risk_stop_price"),
            metadata.get("profit_lock_original_stop_price"),
        ):
            stop = _safe_float(value)
            if stop > 0:
                return stop
        calculated = (
            metadata.get("calculated_price_plan")
            if isinstance(metadata.get("calculated_price_plan"), dict)
            else {}
        )
        stop = _safe_float(calculated.get("stop_price"))
        if stop > 0:
            return stop
        return _safe_float(block.get("stop_price"))

    def _block_has_effective_entry(self, block: dict[str, Any]) -> bool:
        if str(block.get("opened_at") or "").strip():
            return True
        block_id = str(block.get("block_id") or "")
        for order in self.repository.list_orders(block_id=block_id, limit=100):
            if str(order.get("reason") or "") != "entry_order":
                continue
            status = str(order.get("status") or "").lower()
            response = order.get("response") if isinstance(order.get("response"), dict) else {}
            response_status = str(response.get("status") or "").upper()
            if status == "paper" or response_status in {"FILLED", "PARTIALLY_FILLED"}:
                return True
            if build_response_filled_qty(response, requested_qty=0.0) > 0:
                return True
        return False

    def _block_price_path(self, *, block: dict[str, Any], exit_price: float) -> list[float]:
        prices = self.repository.quote_prices(
            symbol=str(block.get("symbol") or ""),
            market=str(block.get("market") or "spot"),
            start_at=str(block.get("opened_at") or block.get("created_at") or ""),
            end_at=str(block.get("closed_at") or utc_now_iso()),
        )
        entry = _safe_float(block.get("entry_price"))
        return [price for price in [entry, *prices, exit_price] if price > 0]

    @staticmethod
    def _mfe_mae_r(
        *,
        side: str,
        entry: float,
        risk: float,
        prices: list[float],
    ) -> tuple[float, float]:
        if risk <= 0 or not prices:
            return 0.0, 0.0
        if side == "short":
            favorable = (entry - min(prices)) / risk
            adverse = (entry - max(prices)) / risk
            return favorable, adverse
        favorable = (max(prices) - entry) / risk
        adverse = (min(prices) - entry) / risk
        return favorable, adverse

    @staticmethod
    def _block_pattern_key(block: dict[str, Any]) -> str:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        key = _extract_block_metadata_pattern_key(metadata)
        if key:
            return key
        risk_sizing = metadata.get("risk_sizing") if isinstance(metadata, dict) else {}
        rr = _safe_float(risk_sizing.get("reward_risk") if isinstance(risk_sizing, dict) else 0)
        rr_bucket = "rr_high" if rr >= 2 else "rr_standard"
        return ":".join(
            [
                str(block.get("market") or "spot"),
                normalize_position_side(block.get("side")),
                rr_bucket,
            ]
        )

    def _closed_exit_price(self, block_id: str) -> float:
        for order in self.repository.list_orders(block_id=block_id, limit=100):
            if str(order.get("reason") or "") == "entry_order":
                continue
            price = build_filled_order_price(order)
            if price > 0:
                return price
        for event in self.repository.list_events(block_id=block_id, limit=100):
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            exit_price = _safe_float(payload.get("exit_price") or payload.get("price"))
            if exit_price > 0:
                return exit_price
        return 0.0

    async def _collect_quotes(
        self,
        blocks: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        quotes: dict[tuple[str, str], dict[str, Any]] = {}
        needs_binance_fee_conversion_quote = False
        for block in blocks:
            symbol = str(block.get("symbol") or "").upper()
            market = normalize_market(block.get("market"))
            if not symbol:
                continue
            if market in {"spot", "futures"}:
                needs_binance_fee_conversion_quote = True
            if (market, symbol) in quotes:
                continue
            quotes[(market, symbol)] = await self._fetch_quote(symbol=symbol, market=market)
        if needs_binance_fee_conversion_quote and ("spot", "BNBUSDT") not in quotes:
            quotes[("spot", "BNBUSDT")] = await self._fetch_quote(
                symbol="BNBUSDT",
                market="spot",
            )
        return quotes

    async def _fetch_quote(self, *, symbol: str, market: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        adapter = self._adapter_for_market(normalized_market)
        if adapter is None:
            return {
                "symbol": symbol,
                "market": normalized_market,
                "price": None,
                "source": "missing",
                "fetched_at": utc_now_iso(),
                "status": "error",
                "error_message": "market_adapter_unavailable",
                "raw": {},
            }
        method_names = [
            "fetch_quote",
            "fetch_futures_quote" if normalized_market == "futures" else "fetch_spot_quote",
            "get_quote",
        ]
        last_error = ""
        for name in method_names:
            method = getattr(adapter, name, None)
            if method is None:
                continue
            try:
                try:
                    if name == "fetch_quote":
                        payload = await _maybe_await(method(symbol, market=normalized_market))
                    else:
                        payload = await _maybe_await(method(symbol))
                except TypeError:
                    payload = await _maybe_await(method(symbol))
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                continue
            if isinstance(payload, dict):
                price = _safe_float(
                    payload.get("price")
                    or payload.get("mark_price")
                    or payload.get("lastPrice")
                    or payload.get("last_price")
                )
                if price > 0:
                    return {
                        "symbol": symbol,
                        "market": normalized_market,
                        "price": price,
                        "source": str(payload.get("source") or name),
                        "fetched_at": utc_now_iso(),
                        "status": "ok",
                        "raw": payload,
                    }
        prices = getattr(adapter, "prices", {})
        if isinstance(prices, dict):
            price = _safe_float(prices.get(symbol))
            if price > 0:
                return {
                    "symbol": symbol,
                    "market": normalized_market,
                    "price": price,
                    "source": "adapter.prices",
                    "fetched_at": utc_now_iso(),
                    "status": "ok",
                    "raw": {},
                }
        return {
            "symbol": symbol,
            "market": normalized_market,
            "price": None,
            "source": "missing",
            "fetched_at": utc_now_iso(),
            "status": "error",
            "error_message": last_error or "quote_unavailable",
            "raw": {},
        }

    async def _fetch_book_ticker(self, *, symbol: str, market: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        adapter = self._adapter_for_market(normalized_market)
        if adapter is None:
            return {
                "symbol": symbol,
                "market": normalized_market,
                "bid": None,
                "ask": None,
                "price": None,
                "source": "missing",
                "fetched_at": utc_now_iso(),
                "status": "error",
                "error_message": "market_adapter_unavailable",
                "raw": {},
            }
        method_names = [
            "fetch_book_ticker",
            "fetch_futures_book_ticker" if normalized_market == "futures" else "fetch_spot_book_ticker",
            "get_book_ticker",
        ]
        last_error = ""
        for name in method_names:
            method = getattr(adapter, name, None)
            if method is None:
                continue
            try:
                try:
                    if name == "fetch_book_ticker":
                        payload = await _maybe_await(method(symbol, market=normalized_market))
                    else:
                        payload = await _maybe_await(method(symbol))
                except TypeError:
                    payload = await _maybe_await(method(symbol))
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                continue
            if isinstance(payload, dict):
                bid = _safe_float(
                    payload.get("bid")
                    or payload.get("bid_price")
                    or payload.get("bidPrice")
                    or payload.get("best_bid")
                    or payload.get("bestBid")
                )
                ask = _safe_float(
                    payload.get("ask")
                    or payload.get("ask_price")
                    or payload.get("askPrice")
                    or payload.get("best_ask")
                    or payload.get("bestAsk")
                )
                if bid > 0 and ask > 0 and ask <= bid:
                    last_error = "book_crossed"
                    continue
                if bid > 0 or ask > 0:
                    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else max(bid, ask)
                    spread_bps = _safe_float(payload.get("spread_bps"))
                    if spread_bps <= 0 and bid > 0 and ask > 0:
                        spread_bps = max((ask - bid) / mid * 10_000.0, 0.0)
                    return {
                        "symbol": symbol,
                        "market": normalized_market,
                        "bid": bid,
                        "bid_price": bid,
                        "ask": ask,
                        "ask_price": ask,
                        "price": mid,
                        "spread_bps": spread_bps,
                        "source": str(payload.get("source") or name),
                        "fetched_at": str(payload.get("fetched_at") or utc_now_iso()),
                        "status": "ok",
                        "raw": payload,
                    }
        return {
            "symbol": symbol,
            "market": normalized_market,
            "bid": None,
            "ask": None,
            "price": None,
            "source": "missing",
            "fetched_at": utc_now_iso(),
            "status": "error",
            "error_message": last_error or "book_ticker_unavailable",
            "raw": {},
        }

    async def _entry_execution_reference(
        self,
        *,
        market: str,
        symbol: str,
        side: str,
    ) -> dict[str, Any]:
        book = await self._fetch_book_ticker(symbol=symbol, market=market)
        bid = _safe_float(book.get("bid"))
        ask = _safe_float(book.get("ask"))
        execution_price = ask if side == "buy" else bid
        if execution_price > 0:
            return {
                **book,
                "execution_price": execution_price,
                "execution_side": side,
                "execution_source": "book_ticker",
            }
        quote = await self._fetch_quote(symbol=symbol, market=market)
        price = _safe_float(quote.get("price"))
        return {
            **quote,
            "bid": bid,
            "ask": ask,
            "execution_price": price,
            "execution_side": side,
            "execution_source": "quote_fallback",
        }

    async def _maybe_create_exit(
        self,
        block: dict[str, Any],
        quotes: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any] | None:
        market = normalize_market(block.get("market"))
        symbol = str(block.get("symbol") or "").upper()
        quote = quotes.get((market, symbol)) or {}
        price = _safe_float(quote.get("price"))
        if price <= 0:
            return None
        reason = build_binance_exit_reason(block, price)
        if reason is None:
            tightened = self._maybe_tighten_entry_quality_loss_stop(block, price)
            if tightened is not None:
                return tightened
            partial = await self._maybe_take_partial_profit(block, price)
            if partial is not None:
                return partial
            return self._maybe_lock_profit(block, price)
        block_id = str(block.get("block_id") or "")
        if not self._exit_retry_due(block):
            return None
        qty = _safe_float(block.get("qty_open"))
        order_side = build_binance_exit_order_side(block)
        requested_qty = qty
        quantity_context: dict[str, Any] = {}
        if self._execution_enabled(market):
            try:
                quantity_context = await self._exit_quantity_context(
                    market=market,
                    symbol=symbol,
                    side=order_side,
                    requested_qty=requested_qty,
                    price=price,
                )
            except Exception as exc:
                quantity_context = {
                    "order_qty": 0.0,
                    "error_message": str(exc) or exc.__class__.__name__,
                    "balance_checked": True,
                }
            if "order_qty" in quantity_context:
                qty = _safe_float(quantity_context.get("order_qty"))
            if qty <= 0:
                message = quantity_context.get("error_message") or "exit quantity unavailable"
                retry_cooldown = max(int(self.config.failed_exit_retry_cooldown_sec), 0)
                plan = build_exit_quantity_unavailable_plan(
                    metadata=dict(
                        block.get("metadata")
                        if isinstance(block.get("metadata"), dict)
                        else {}
                    ),
                    message=str(message),
                    price=price,
                    side=order_side,
                    requested_qty=requested_qty,
                    quantity_context=quantity_context,
                    reason=reason,
                    now_iso=utc_now_iso(),
                    retry_cooldown_sec=retry_cooldown,
                    retry_after_ts=time.time() + retry_cooldown
                    if retry_cooldown > 0
                    else None,
                )
                updated = self.repository.update_block(
                    block_id,
                    plan["update_fields"],
                )
                event = plan.get("event") if isinstance(plan.get("event"), dict) else {}
                self.repository.add_event(
                    block_id,
                    str(event.get("type") or "exit_skipped"),
                    str(event.get("message") or message),
                    event.get("payload") if isinstance(event.get("payload"), dict) else {},
                )
                return build_exit_quantity_unavailable_result_plan(
                    status=str(plan.get("status") or "skipped"),
                    block_id=block_id,
                    symbol=symbol,
                    market=market,
                    order_side=order_side,
                    requested_qty=requested_qty,
                    quantity_context=quantity_context,
                    price=price,
                    reason=reason,
                    block=updated,
                )
        if not self.repository.claim_exit_pending(
            block_id,
            reason=reason,
            payload={
                "price": price,
                "side": order_side,
                "qty": qty,
                "requested_qty": requested_qty,
                "quantity_context": quantity_context,
            },
        ):
            return None
        execution_enabled = self._execution_enabled(market)
        execution_result = await build_exit_order_execution(
            execution_enabled=execution_enabled,
            submit_exit_order=self._submit_exit_order,
            enrich_order_response_for_costs=(
                self._enrich_order_response_for_costs if execution_enabled else None
            ),
            block=block,
            market=market,
            symbol=symbol,
            order_side=order_side,
            qty=qty,
            price=price,
            allow_reduce_only_below_min_notional=market == "futures",
        )
        response = (
            execution_result.get("response")
            if isinstance(execution_result.get("response"), dict)
            else {}
        )
        status = str(execution_result.get("status") or "paper")
        order = self.repository.add_order(
            {
                "block_id": block_id,
                "symbol": symbol,
                "market": market,
                "side": order_side,
                "qty": qty,
                "order_type": "LIMIT_IOC",
                "status": status,
                "reason": reason,
                "response": response,
            }
        )
        update_fields: dict[str, Any] = {"llm_reason": reason}
        reconciliation_error_event: dict[str, Any] | None = None
        response_status = str(response.get("status") or "").upper()
        if status == "paper" or response_status == "FILLED":
            filled_qty = build_response_filled_qty(response, requested_qty=qty)
            remaining_qty = build_remaining_exit_qty(
                requested_qty=requested_qty,
                filled_qty=filled_qty,
                price=price,
                min_notional=SPOT_ADOPTION_MIN_NOTIONAL_USDT,
            )
            retry_cooldown = max(int(self.config.failed_exit_retry_cooldown_sec), 0)
            update_fields = build_exit_fill_update_plan(
                metadata=dict(
                    block.get("metadata")
                    if isinstance(block.get("metadata"), dict)
                    else {}
                ),
                status=status,
                response_status=response_status,
                reason=reason,
                requested_qty=requested_qty,
                order_qty=qty,
                filled_qty=filled_qty,
                remaining_qty=remaining_qty,
                retry_reason="partial_fill",
                retry_cooldown_sec=retry_cooldown,
                retry_after_ts=time.time() + retry_cooldown
                if retry_cooldown > 0
                else None,
                now_iso=utc_now_iso(),
            )["update_fields"]
        elif status == "error":
            error_message = str(response.get("error_message") or "unknown")
            if (
                market in {"spot", UPBIT_SPOT_MARKET}
                and order_side == "sell"
                and _is_insufficient_balance_error(error_message)
            ):
                retry_action = await self._retry_spot_exit_after_insufficient_balance(
                    block=block,
                    market=market,
                    symbol=symbol,
                    side=order_side,
                    requested_qty=requested_qty,
                    failed_qty=qty,
                    price=price,
                    reason=reason,
                    error_message=error_message,
                    quantity_context=quantity_context,
                    initial_order=order,
                )
                if retry_action is not None:
                    return retry_action
                reconciliation_plan = build_exit_reconciliation_error_plan(
                    metadata=dict(
                        block.get("metadata")
                        if isinstance(block.get("metadata"), dict)
                        else {}
                    ),
                    error_message=error_message,
                    price=price,
                    side=order_side,
                    requested_qty=requested_qty,
                    order_qty=qty,
                    quantity_context=quantity_context,
                    order=order,
                    detected_at=utc_now_iso(),
                )
                update_fields.update(reconciliation_plan["update_fields"])
                status = "reconciliation_error"
                reconciliation_error_event = reconciliation_plan["event"]
            else:
                update_fields.update(
                    {
                        "status": "open",
                        "force_exit_requested": 1,
                        "risk_note": (
                            f"exit order submit failed; retry armed: "
                            f"{error_message}"
                        ),
                        "metadata": self._metadata_with_exit_retry(
                            block,
                            reason=error_message,
                        ),
                    }
                )
        else:
            filled_qty = build_response_filled_qty(response, requested_qty=0.0)
            remaining_qty = build_remaining_exit_qty(
                requested_qty=requested_qty,
                filled_qty=filled_qty,
                price=price,
                min_notional=SPOT_ADOPTION_MIN_NOTIONAL_USDT,
            )
            retry_reason = (
                "partial_fill"
                if response_status == "PARTIALLY_FILLED"
                else response_status or status
            )
            retry_cooldown = (
                max(int(self.config.failed_exit_retry_cooldown_sec), 0)
                if response_status == "PARTIALLY_FILLED"
                else self._failed_exit_retry_cooldown_sec(
                    market=market,
                    response_status=response_status or status,
                )
            )
            update_fields = build_exit_fill_update_plan(
                metadata=dict(
                    block.get("metadata")
                    if isinstance(block.get("metadata"), dict)
                    else {}
                ),
                status=status,
                response_status=response_status,
                reason=reason,
                requested_qty=requested_qty,
                order_qty=qty,
                filled_qty=filled_qty,
                remaining_qty=remaining_qty,
                retry_reason=retry_reason,
                retry_cooldown_sec=retry_cooldown,
                retry_after_ts=time.time() + retry_cooldown
                if retry_cooldown > 0
                else None,
                now_iso=utc_now_iso(),
            )["update_fields"]
        updated = self.repository.update_block(block_id, update_fields)
        if reconciliation_error_event is not None:
            self.repository.add_event(
                block_id,
                "exit_reconciliation_error",
                str(reconciliation_error_event.get("message") or ""),
                reconciliation_error_event.get("payload")
                if isinstance(reconciliation_error_event.get("payload"), dict)
                else {},
            )
        return build_exit_success_plan(
            status=status,
            block_id=block_id,
            symbol=symbol,
            market=market,
            order_side=order_side,
            qty=qty,
            requested_qty=requested_qty,
            quantity_context=quantity_context,
            price=price,
            reason=reason,
            order=order,
            block=updated,
        )

    async def _retry_spot_exit_after_insufficient_balance(
        self,
        *,
        block: dict[str, Any],
        market: str,
        symbol: str,
        side: str,
        requested_qty: float,
        failed_qty: float,
        price: float,
        reason: str,
        error_message: str,
        quantity_context: dict[str, Any],
        initial_order: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized_market = normalize_market(market)
        if normalized_market not in {"spot", UPBIT_SPOT_MARKET} or side != "sell":
            return None
        if requested_qty <= 0 or failed_qty <= 0:
            return None
        try:
            retry_context = await self._exit_quantity_context(
                market=normalized_market,
                symbol=symbol,
                side=side,
                requested_qty=requested_qty,
                price=price,
            )
        except Exception as exc:
            quantity_context["insufficient_balance_retry"] = False
            quantity_context["insufficient_balance_retry_error"] = (
                str(exc) or exc.__class__.__name__
            )
            logger.warning(
                "binance spot exit retry balance lookup failed for %s: %s",
                symbol,
                quantity_context["insufficient_balance_retry_error"],
            )
            return None
        retry_qty = _safe_float(retry_context.get("order_qty"))
        if retry_qty <= 0 or retry_qty >= failed_qty:
            quantity_context["insufficient_balance_retry"] = False
            quantity_context["insufficient_balance_retry_context"] = retry_context
            retry_error = str(retry_context.get("error_message") or "").strip()
            if retry_error:
                quantity_context["insufficient_balance_retry_error"] = retry_error
            return None
        retry_context = {
            **retry_context,
            "insufficient_balance_retry": True,
            "initial_order_qty": failed_qty,
            "initial_error_message": error_message,
            "initial_quantity_context": quantity_context,
        }
        response: dict[str, Any] = {}
        status = "error"
        try:
            response = await self._submit_exit_order(
                market=normalized_market,
                symbol=symbol,
                side=side,
                qty=retry_qty,
                price=price,
                allow_reduce_only_below_min_notional=False,
            )
            status = "sent"
        except Exception as exc:
            response = {"status": "error", "error_message": str(exc)}
        if status == "sent":
            response = await self._enrich_order_response_for_costs(
                block=block,
                market=normalized_market,
                symbol=symbol,
                response=response,
                include_funding=True,
            )
        retry_order = self.repository.add_order(
            {
                "block_id": str(block.get("block_id") or ""),
                "symbol": symbol,
                "market": normalized_market,
                "side": side,
                "qty": retry_qty,
                "order_type": "LIMIT_IOC",
                "status": status,
                "reason": reason,
                "response": response,
            }
        )
        response_status = str(response.get("status") or "").upper()
        filled_qty = 0.0
        if status == "sent" and response_status in {"FILLED", "PARTIALLY_FILLED"}:
            filled_qty = build_response_filled_qty(response, requested_qty=retry_qty)
        remaining_qty = build_remaining_exit_qty(
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            price=price,
            min_notional=SPOT_ADOPTION_MIN_NOTIONAL_USDT,
        )
        retry_cooldown = max(int(self.config.failed_exit_retry_cooldown_sec), 0)
        now_iso = utc_now_iso()
        retry_plan = build_spot_exit_retry_update_plan(
            metadata=dict(
                block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
            ),
            initial_error_message=error_message,
            price=price,
            side=side,
            requested_qty=requested_qty,
            failed_qty=failed_qty,
            retry_qty=retry_qty,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            status=status,
            response_status=response_status,
            response=response,
            retry_context=retry_context,
            initial_order=initial_order,
            retry_order=retry_order,
            reason=reason,
            now_iso=now_iso,
            retry_cooldown_sec=retry_cooldown,
            retry_after_ts=time.time() + retry_cooldown
            if retry_cooldown > 0
            else None,
        )
        update_fields = retry_plan["update_fields"]
        updated = self.repository.update_block(str(block.get("block_id") or ""), update_fields)
        event = retry_plan.get("event") if isinstance(retry_plan.get("event"), dict) else {}
        self.repository.add_event(
            str(block.get("block_id") or ""),
            str(event.get("type") or "insufficient_balance_exit_retry"),
            str(event.get("message") or "spot exit retried with fresh lower sellable balance"),
            event.get("payload") if isinstance(event.get("payload"), dict) else {},
        )
        return build_exit_success_plan(
            status=status,
            block_id=str(block.get("block_id") or ""),
            symbol=symbol,
            market=normalized_market,
            order_side=side,
            qty=retry_qty,
            requested_qty=requested_qty,
            quantity_context=retry_context,
            price=price,
            reason=reason,
            order=retry_order,
            block=updated,
            extra_fields={"initial_order": initial_order},
        )

    def _maybe_tighten_entry_quality_loss_stop(
        self,
        block: dict[str, Any],
        price: float,
    ) -> dict[str, Any] | None:
        if str(block.get("status") or "") != "open":
            return None
        if block.get("force_exit_requested"):
            return None
        trigger_r = max(
            _safe_float(self.config.entry_quality_loss_tighten_trigger_r),
            0.0,
        )
        if trigger_r <= 0:
            return None
        metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
        if isinstance(metadata.get("entry_quality_loss_tighten"), dict):
            return None
        calculated = (
            metadata.get("calculated_price_plan")
            if isinstance(metadata.get("calculated_price_plan"), dict)
            else {}
        )
        entry_quality = build_entry_quality_label_from_payload(block, metadata, calculated)
        if not entry_quality:
            return None
        entry = _safe_float(block.get("entry_price"))
        stop = _safe_float(block.get("stop_price"))
        original_stop = (
            _safe_float(metadata.get("entry_quality_loss_tighten_original_stop_price"))
            or _safe_float(metadata.get("profit_lock_original_stop_price"))
            or stop
        )
        if entry <= 0 or stop <= 0 or original_stop <= 0 or price <= 0:
            return None
        risk = abs(entry - original_stop)
        if risk <= 0:
            return None
        side = normalize_position_side(block.get("side"))
        unfavorable_r = (price - entry) / risk if side == "short" else (entry - price) / risk
        if unfavorable_r < trigger_r:
            return None
        market = normalize_market(block.get("market") or block.get("venue"))
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        quality_card = self._scorecard_for_entry_quality(
            performance,
            market=market,
            side=side,
            entry_quality=entry_quality,
        )
        if not quality_card:
            return None
        min_samples = max(_safe_int(self.config.lane_performance_min_samples), 1)
        if not self._lane_card_requires_cooldown(
            quality_card,
            min_samples=min_samples,
        ):
            return None
        if side == "short":
            new_stop = min(stop, price)
            if new_stop >= stop:
                return None
        else:
            new_stop = max(stop, price)
            if new_stop <= stop:
                return None
        new_stop = round_candidate_price(new_stop)
        entry_quality_lane = str(
            quality_card.get("entry_quality_lane")
            or f"{market}:{side}:{entry_quality}"
        ).strip()
        block_id = str(block.get("block_id") or "")
        tighten_plan = build_entry_quality_loss_tighten_plan(
            metadata=metadata,
            block_id=block_id,
            symbol=str(block.get("symbol") or ""),
            market=market,
            side=side,
            entry_quality=entry_quality,
            entry_quality_lane=entry_quality_lane,
            trigger_r=trigger_r,
            unfavorable_r=unfavorable_r,
            price=price,
            old_stop_price=stop,
            new_stop_price=new_stop,
            original_stop_price=original_stop,
            quality_card=quality_card,
            min_samples=min_samples,
            now_iso=utc_now_iso(),
        )
        updated = self.repository.update_block(block_id, tighten_plan["update_fields"])
        event = tighten_plan.get("event") if isinstance(tighten_plan.get("event"), dict) else {}
        self.repository.add_event(
            block_id,
            str(event.get("type") or "entry_quality_loss_tighten"),
            str(event.get("message") or "entry-quality loss stop tightened"),
            event.get("payload") if isinstance(event.get("payload"), dict) else {},
        )
        result_fields = (
            tighten_plan.get("result_fields")
            if isinstance(tighten_plan.get("result_fields"), dict)
            else {}
        )
        return {**result_fields, "block": updated}

    async def _maybe_take_partial_profit(
        self,
        block: dict[str, Any],
        price: float,
    ) -> dict[str, Any] | None:
        weak_lane_context = self._weak_performance_lane_context(block)
        weak_trigger_r = 0.0
        weak_trigger_source = "weak_performance_lane"
        entry_quality_repair_context: dict[str, Any] = {}
        global_repair_context: dict[str, Any] = {}
        if bool(weak_lane_context.get("matched")):
            weak_trigger_r, weak_trigger_source = self._weak_lane_profit_protection_trigger(
                weak_lane_context
            )
        else:
            entry_quality_repair_context = (
                self._entry_quality_mfe_surrender_profit_lock_context(block)
            )
            if not bool(entry_quality_repair_context.get("enabled")):
                global_repair_context = self._mfe_surrender_profit_lock_context()
        market = normalize_market(block.get("market"))
        side = normalize_position_side(block.get("side"))
        distressed_fraction_context = self._distressed_entry_quality_partial_profit_context(
            block=block,
            market=market,
            side=side,
        )
        trigger_plan = build_partial_profit_trigger_plan(
            block=block,
            price=price,
            weak_lane_context=weak_lane_context,
            weak_trigger_r=weak_trigger_r,
            weak_trigger_source=weak_trigger_source,
            entry_quality_repair_context=entry_quality_repair_context,
            global_repair_context=global_repair_context,
            base_fraction=self.config.weak_lane_partial_profit_fraction,
            distressed_fraction_context=distressed_fraction_context,
            distressed_fraction=self.config.distressed_entry_quality_partial_profit_fraction,
        )
        if str(trigger_plan.get("status") or "") != "ok":
            return None
        metadata = trigger_plan["metadata"]
        entry = _safe_float(trigger_plan.get("entry_price"))
        stop = _safe_float(trigger_plan.get("stop_price"))
        qty_open = _safe_float(trigger_plan.get("qty_open"))
        risk = _safe_float(trigger_plan.get("risk"))
        market = str(trigger_plan.get("market") or market)
        side = str(trigger_plan.get("side") or side)
        block_id = str(trigger_plan.get("block_id") or "")
        symbol = str(trigger_plan.get("symbol") or "").upper()
        favorable_r = _safe_float(trigger_plan.get("favorable_r"))
        trigger_r = _safe_float(trigger_plan.get("trigger_r"))
        trigger_source = str(trigger_plan.get("trigger_source") or "")
        weak_lane_context = (
            trigger_plan.get("weak_lane_context")
            if isinstance(trigger_plan.get("weak_lane_context"), dict)
            else {}
        )
        repair_context = (
            trigger_plan.get("repair_context")
            if isinstance(trigger_plan.get("repair_context"), dict)
            else {}
        )
        fraction = _safe_float(trigger_plan.get("fraction"))
        fraction_source = str(trigger_plan.get("fraction_source") or "")
        fraction_context = (
            trigger_plan.get("fraction_context")
            if isinstance(trigger_plan.get("fraction_context"), dict)
            else {}
        )
        min_notional = await self._exit_min_notional(market=market, symbol=symbol)
        qty_plan = build_partial_profit_quantity_plan(
            qty_open=qty_open,
            price=price,
            fraction=fraction,
            market=market,
            min_notional=min_notional,
        )
        if str(qty_plan.get("status") or "") != "ok":
            return None
        partial_qty = _safe_float(qty_plan.get("partial_qty"))
        original_partial_qty = _safe_float(qty_plan.get("original_partial_qty"))
        remaining_qty = _safe_float(qty_plan.get("remaining_qty"))
        full_exit_for_min_notional = bool(qty_plan.get("full_exit_for_min_notional"))
        partial_profit_exit_mode = str(qty_plan.get("exit_mode") or "partial")
        order_side = build_binance_exit_order_side(block)
        requested_qty = partial_qty
        quantity_context: dict[str, Any] = {}
        qty = partial_qty
        if self._execution_enabled(market):
            try:
                quantity_context = await self._exit_quantity_context(
                    market=market,
                    symbol=symbol,
                    side=order_side,
                    requested_qty=requested_qty,
                    price=price,
                )
            except Exception as exc:
                quantity_context = {
                    "order_qty": 0.0,
                    "error_message": str(exc) or exc.__class__.__name__,
                    "balance_checked": True,
                }
            if "order_qty" in quantity_context:
                qty = _safe_float(quantity_context.get("order_qty"))
            if qty <= 0:
                plan = build_partial_profit_quantity_unavailable_plan(
                    metadata=metadata,
                    message=str(
                        quantity_context.get("error_message")
                        or "partial exit quantity unavailable"
                    ),
                    block_id=block_id,
                    symbol=symbol,
                    market=market,
                    order_side=order_side,
                    requested_qty=requested_qty,
                    quantity_context=quantity_context,
                    price=price,
                    now_iso=utc_now_iso(),
                )
                updated = self.repository.update_block(block_id, plan["update_fields"])
                event = plan["event"]
                self.repository.add_event(
                    block_id,
                    str(event["type"]),
                    str(event["message"]),
                    event["payload"],
                )
                result = dict(plan["result"])
                result["block"] = updated
                return result
        execution_result = await build_partial_profit_exit_order_execution(
            execution_enabled=self._execution_enabled(market),
            submit_exit_order=self._submit_exit_order,
            market=market,
            symbol=symbol,
            order_side=order_side,
            qty=qty,
            requested_qty=requested_qty,
            qty_open=qty_open,
            remaining_qty=remaining_qty,
            price=price,
            full_exit_for_min_notional=full_exit_for_min_notional,
            exit_mode=partial_profit_exit_mode,
            metadata=metadata,
        )
        response = (
            execution_result.get("response")
            if isinstance(execution_result.get("response"), dict)
            else {}
        )
        status = str(execution_result.get("status") or "paper")
        qty = _safe_float(execution_result.get("qty"))
        remaining_qty = _safe_float(execution_result.get("remaining_qty"))
        full_exit_for_min_notional = bool(
            execution_result.get("full_exit_for_min_notional")
        )
        partial_profit_exit_mode = str(
            execution_result.get("exit_mode") or partial_profit_exit_mode
        )
        metadata = (
            execution_result.get("metadata")
            if isinstance(execution_result.get("metadata"), dict)
            else metadata
        )
        if status == "sent":
            response = await self._enrich_order_response_for_costs(
                block=block,
                market=market,
                symbol=symbol,
                response=response,
                include_funding=True,
            )
        order = self.repository.add_order(
            {
                "block_id": block_id,
                "symbol": symbol,
                "market": market,
                "side": order_side,
                "qty": qty,
                "order_type": "LIMIT_IOC",
                "status": status,
                "reason": "partial_profit_reached",
                "response": response,
            }
        )
        response_status = str(response.get("status") or "").upper()
        filled_qty = 0.0
        if status == "paper" or response_status == "FILLED":
            filled_qty = build_response_filled_qty(response, requested_qty=qty)
        elif response_status == "PARTIALLY_FILLED":
            filled_qty = build_response_filled_qty(response, requested_qty=0.0)
        if filled_qty <= 0 and status == "paper":
            filled_qty = qty
        if filled_qty <= 0:
            error_message = str(response.get("error_message") or response_status or status)
            plan = build_partial_profit_unfilled_plan(
                metadata=metadata,
                error_message=error_message,
                status=status,
                block_id=block_id,
                symbol=symbol,
                market=market,
                order_side=order_side,
                qty=qty,
                requested_qty=requested_qty,
                quantity_context=quantity_context,
                price=price,
                order=order,
                now_iso=utc_now_iso(),
            )
            updated = self.repository.update_block(block_id, plan["update_fields"])
            event = plan["event"]
            self.repository.add_event(
                block_id,
                str(event["type"]),
                str(event["message"]),
                event["payload"],
            )
            result = dict(plan["result"])
            result["block"] = updated
            return result
        remaining_qty = max(qty_open - min(filled_qty, qty_open), 0.0)
        new_stop = self._partial_profit_stop_price(
            side=side,
            entry=entry,
            stop=_safe_float(block.get("stop_price")),
            risk=risk,
            price=price,
            market=market,
            metadata=metadata,
        )
        partial_profit_update = build_partial_profit_block_update_plan(
            metadata=metadata,
            qty_open=qty_open,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            price=price,
            favorable_r=favorable_r,
            trigger_r=trigger_r,
            trigger_source=trigger_source,
            fraction=fraction,
            fraction_source=fraction_source,
            exit_mode=partial_profit_exit_mode,
            min_notional=min_notional,
            original_requested_qty=original_partial_qty,
            requested_qty=requested_qty,
            order_status=status,
            new_stop_price=new_stop,
            original_stop_price=stop,
            weak_lane_context=weak_lane_context,
            repair_context=repair_context,
            fraction_context=fraction_context,
            taken_at=utc_now_iso(),
        )
        metadata = partial_profit_update["metadata"]
        update_fields = partial_profit_update["update_fields"]
        updated = self.repository.update_block(block_id, update_fields)
        success_plan = build_partial_profit_success_plan(
            block_id=block_id,
            symbol=symbol,
            market=market,
            side=side,
            order_side=order_side,
            qty=qty,
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            quantity_context=quantity_context,
            price=price,
            favorable_r=favorable_r,
            trigger_r=trigger_r,
            trigger_source=trigger_source,
            new_stop_price=new_stop,
            order=order,
            block=updated,
        )
        event = success_plan["event"]
        self.repository.add_event(
            block_id,
            str(event["type"]),
            str(event["message"]),
            event["payload"],
        )
        return success_plan["result"]

    def _partial_profit_stop_price(
        self,
        *,
        side: str,
        entry: float,
        stop: float,
        risk: float,
        price: float,
        market: str,
        metadata: dict[str, Any],
    ) -> float | None:
        if entry <= 0 or stop <= 0 or risk <= 0 or price <= 0:
            return None
        lock_r = max(_safe_float(self.config.profit_lock_stop_r), 0.0)
        cost_gate = metadata.get("cost_edge_gate") if isinstance(metadata.get("cost_edge_gate"), dict) else {}
        estimated_cost_pct = _safe_float(cost_gate.get("estimated_round_trip_cost_pct"))
        if estimated_cost_pct <= 0:
            estimated_cost_pct = MANAGER_COST_EDGE_BASE_ROUND_TRIP_PCT.get(market, 0.20)
        new_stop = build_profit_lock_stop_price(
            side=side,
            entry_price=entry,
            stop_price=stop,
            risk=risk,
            price=price,
            lock_r=lock_r,
            estimated_cost_pct=estimated_cost_pct,
            min_net_buffer_pct=self.config.profit_lock_min_net_buffer_pct,
            default_cost_pct=MANAGER_COST_EDGE_BASE_ROUND_TRIP_PCT.get(market, 0.20),
        )
        if new_stop is None:
            return None
        return round_candidate_price(new_stop)

    async def _exit_min_notional(self, *, market: str, symbol: str) -> float:
        normalized_market = normalize_market(market)
        if normalized_market == UPBIT_SPOT_MARKET:
            return max(_safe_float(self.config.upbit_min_quote_budget_krw), 0.0)
        if normalized_market == "spot":
            return SPOT_ADOPTION_MIN_NOTIONAL_USDT
        if normalized_market != "futures":
            return 0.0
        try:
            filters = await self._exchange_filters_for_order(
                market=normalized_market,
                symbol=symbol,
            )
        except Exception as exc:
            logger.warning("binance exit min notional lookup failed: %s", exc)
            return 0.0
        return max(float(build_min_notional_from_filters(filters)), 0.0) if filters else 0.0

    def _maybe_lock_profit(self, block: dict[str, Any], price: float) -> dict[str, Any] | None:
        metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
        if metadata.get("profit_lock_triggered_at"):
            return None
        entry = _safe_float(block.get("entry_price"))
        stop = _safe_float(block.get("stop_price"))
        target = _safe_float(block.get("target_price"))
        if entry <= 0 or stop <= 0 or target <= 0 or price <= 0:
            return None
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        side = normalize_position_side(block.get("side"))
        favorable_r = build_favorable_r_multiple(
            entry_price=entry,
            stop_price=stop,
            price=price,
            side=side,
        )
        trigger_r = max(_safe_float(self.config.profit_lock_trigger_r), 0.0)
        trigger_source = "default"
        repair_context: dict[str, Any] = {}
        weak_lane_context = self._weak_performance_lane_context(block)
        if bool(weak_lane_context.get("matched")):
            weak_trigger_r, weak_trigger_source = self._weak_lane_profit_protection_trigger(
                weak_lane_context
            )
            if weak_trigger_r > 0 and (trigger_r <= 0 or weak_trigger_r < trigger_r):
                trigger_r = weak_trigger_r
                trigger_source = weak_trigger_source
        else:
            repair_context = self._entry_quality_mfe_surrender_profit_lock_context(block)
            repair_trigger_r = _safe_float(repair_context.get("trigger_r"))
            if (
                bool(repair_context.get("enabled"))
                and repair_trigger_r > 0
                and (trigger_r <= 0 or repair_trigger_r < trigger_r)
            ):
                trigger_r = repair_trigger_r
                trigger_source = "entry_quality_mfe_surrender_repair"
            if not bool(repair_context.get("enabled")):
                repair_context = self._mfe_surrender_profit_lock_context()
                repair_trigger_r = _safe_float(repair_context.get("trigger_r"))
                if (
                    bool(repair_context.get("enabled"))
                    and repair_trigger_r > 0
                    and (trigger_r <= 0 or repair_trigger_r < trigger_r)
                ):
                    trigger_r = repair_trigger_r
                    trigger_source = "mfe_surrender_repair"

        if trigger_source not in {
            "entry_quality_mfe_surrender_repair",
            "mfe_surrender_repair",
        }:
            repair_context = {}
        if favorable_r < trigger_r:
            return None
        lock_r = max(_safe_float(self.config.profit_lock_stop_r), 0.0)
        market = normalize_market(block.get("market"))
        cost_gate = metadata.get("cost_edge_gate") if isinstance(metadata.get("cost_edge_gate"), dict) else {}
        stop_plan = build_profit_lock_stop_plan(
            side=side,
            entry_price=entry,
            stop_price=stop,
            risk=risk,
            price=price,
            lock_r=lock_r,
            estimated_cost_pct=_safe_float(
                cost_gate.get("estimated_round_trip_cost_pct")
            ),
            min_net_buffer_pct=self.config.profit_lock_min_net_buffer_pct,
            default_cost_pct=MANAGER_COST_EDGE_BASE_ROUND_TRIP_PCT.get(market, 0.20),
        )
        if stop_plan.get("status") != "ok":
            return None
        new_stop = _safe_float(stop_plan.get("stop_price"))
        new_stop = round_candidate_price(new_stop)
        if side == "short" and price >= new_stop:
            return None
        if side != "short" and price <= new_stop:
            return None
        cost_floor_pct = _safe_float(stop_plan.get("cost_floor_pct"))
        estimated_cost_pct = _safe_float(
            stop_plan.get("estimated_round_trip_cost_pct")
        )
        min_net_buffer_pct = _safe_float(stop_plan.get("min_net_buffer_pct"))
        block_id = str(block.get("block_id") or "")
        metadata.update(
            {
                "profit_lock_triggered_at": utc_now_iso(),
                "profit_lock_trigger_r": trigger_r,
                "profit_lock_trigger_source": trigger_source,
                "profit_lock_stop_r": lock_r,
                "profit_lock_cost_floor_pct": cost_floor_pct,
                "profit_lock_estimated_round_trip_cost_pct": estimated_cost_pct,
                "profit_lock_min_net_buffer_pct": min_net_buffer_pct,
                "profit_lock_reference_price": price,
                "profit_lock_favorable_r": favorable_r,
                "profit_lock_original_stop_price": stop,
            }
        )
        if weak_lane_context.get("source") == "runtime_scorecard":
            metadata["runtime_weak_performance_lane"] = weak_lane_context
        if repair_context:
            metadata["profit_lock_repair_context"] = repair_context
        updated = self.repository.update_block(
            block_id,
            {
                "stop_price": new_stop,
                "llm_reason": "profit_lock_reached",
                "risk_note": (
                    f"profit lock: favorable_r={favorable_r:.3f}, "
                    f"cost_floor_pct={cost_floor_pct:.3f}, "
                    f"stop moved {stop:g}->{new_stop:g}"
                ),
                "metadata": metadata,
            },
        )
        self.repository.add_event(
            block_id,
            "profit_lock",
            "profit lock stop updated",
            {
                "price": price,
                "side": side,
                "entry_price": entry,
                "old_stop_price": stop,
                "new_stop_price": new_stop,
                "favorable_r": favorable_r,
                "trigger_r": trigger_r,
                "trigger_source": trigger_source,
                "lock_r": lock_r,
                "cost_floor_pct": cost_floor_pct,
                "estimated_round_trip_cost_pct": estimated_cost_pct,
            },
        )
        return {
            "status": "profit_locked",
            "block_id": block_id,
            "symbol": str(block.get("symbol") or "").upper(),
            "market": normalize_market(block.get("market")),
            "side": side,
            "price": price,
            "old_stop_price": stop,
            "new_stop_price": new_stop,
            "favorable_r": favorable_r,
            "trigger_r": trigger_r,
            "trigger_source": trigger_source,
            "reason": "profit_lock_reached",
            "block": updated,
        }

    def _mfe_surrender_profit_lock_context(self) -> dict[str, Any]:
        performance = self.repository.latest_performance_scorecard(limit=20)
        sample_count = _safe_int(performance.get("sample_count"))
        avg_mfe = _safe_float(performance.get("avg_mfe_r_multiple"))
        avg_r = _safe_float(performance.get("avg_r_multiple"))
        realized_pnl = _safe_float(performance.get("realized_pnl_usdt"))
        trigger_r = max(_safe_float(self.config.weak_lane_profit_lock_trigger_r), 0.0)
        enabled = (
            sample_count >= 5
            and avg_mfe >= 0.7
            and (avg_r < 0 or realized_pnl < 0)
            and trigger_r > 0
        )
        return {
            "version": "binance_mfe_surrender_profit_lock_v1",
            "enabled": enabled,
            "trigger_r": trigger_r,
            "sample_count": sample_count,
            "avg_mfe_r_multiple": round(avg_mfe, 6),
            "avg_r_multiple": round(avg_r, 6),
            "realized_pnl_usdt": round(realized_pnl, 6),
            "reason": (
                "recent_mfe_available_but_final_r_negative"
                if enabled
                else "recent_mfe_surrender_not_detected"
            ),
        }

    def _entry_quality_mfe_surrender_profit_lock_context(
        self,
        block: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        calculated = (
            metadata.get("calculated_price_plan")
            if isinstance(metadata.get("calculated_price_plan"), dict)
            else {}
        )
        entry_quality = build_entry_quality_label_from_payload(block, metadata, calculated)
        if not entry_quality:
            return {
                "version": "binance_entry_quality_mfe_surrender_profit_lock_v1",
                "enabled": False,
                "reason": "entry_quality_missing",
            }
        market = normalize_market(block.get("market") or block.get("venue"))
        side = normalize_position_side(block.get("side"))
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        card = self._scorecard_for_entry_quality(
            performance,
            market=market,
            side=side,
            entry_quality=entry_quality,
        )
        trigger_r = max(_safe_float(self.config.weak_lane_profit_lock_trigger_r), 0.0)
        if not card:
            return {
                "version": "binance_entry_quality_mfe_surrender_profit_lock_v1",
                "enabled": False,
                "trigger_r": trigger_r,
                "entry_quality_lane": f"{market}:{side}:{entry_quality}",
                "reason": "entry_quality_scorecard_missing",
            }
        sample_count = _safe_int(card.get("sample_count"))
        min_samples = max(_safe_int(self.config.lane_performance_min_samples), 1)
        avg_mfe = _safe_float(card.get("avg_mfe_r_multiple"))
        avg_r = _safe_float(card.get("avg_r_multiple"))
        realized_pnl = _safe_float(card.get("pnl_usdt"))
        enabled = (
            sample_count >= min_samples
            and avg_mfe >= 0.8
            and (avg_r < 0 or realized_pnl < 0)
            and trigger_r > 0
        )
        return {
            "version": "binance_entry_quality_mfe_surrender_profit_lock_v1",
            "enabled": enabled,
            "trigger_r": trigger_r,
            "entry_quality_lane": str(card.get("entry_quality_lane") or "").strip(),
            "sample_count": sample_count,
            "min_samples": min_samples,
            "avg_mfe_r_multiple": round(avg_mfe, 6),
            "avg_r_multiple": round(avg_r, 6),
            "realized_pnl_usdt": round(realized_pnl, 6),
            "profit_factor": _safe_float(card.get("profit_factor")),
            "reason": (
                "entry_quality_mfe_available_but_final_r_negative"
                if enabled
                else "entry_quality_mfe_surrender_not_detected"
            ),
        }

    def _distressed_entry_quality_partial_profit_context(
        self,
        *,
        block: dict[str, Any],
        market: str,
        side: str,
    ) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        calculated = (
            metadata.get("calculated_price_plan")
            if isinstance(metadata.get("calculated_price_plan"), dict)
            else {}
        )
        entry_quality = build_entry_quality_label_from_payload(block, metadata, calculated)
        if not entry_quality:
            return {
                "version": "binance_distressed_entry_quality_partial_profit_v1",
                "enabled": False,
                "reason": "entry_quality_missing",
            }
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        card = self._scorecard_for_entry_quality(
            performance,
            market=market,
            side=side,
            entry_quality=entry_quality,
        )
        entry_quality_lane = f"{market}:{side}:{entry_quality}"
        if not card:
            return {
                "version": "binance_distressed_entry_quality_partial_profit_v1",
                "enabled": False,
                "entry_quality_lane": entry_quality_lane,
                "reason": "entry_quality_scorecard_missing",
            }
        distressed = self._lane_card_is_distressed(card)
        return {
            "version": "binance_distressed_entry_quality_partial_profit_v1",
            "enabled": distressed,
            "entry_quality_lane": str(card.get("entry_quality_lane") or entry_quality_lane),
            "fraction": min(
                max(
                    _safe_float(
                        self.config.distressed_entry_quality_partial_profit_fraction
                    ),
                    0.0,
                ),
                0.95,
            ),
            "sample_count": _safe_int(card.get("sample_count")),
            "min_samples": max(
                _safe_int(self.config.distressed_lane_min_samples),
                _safe_int(self.config.lane_performance_min_samples),
                1,
            ),
            "pnl_usdt": _safe_float(card.get("pnl_usdt")),
            "win_rate_pct": _safe_float(card.get("win_rate_pct")),
            "avg_r_multiple": _safe_float(card.get("avg_r_multiple")),
            "avg_mfe_r_multiple": _safe_float(card.get("avg_mfe_r_multiple")),
            "profit_factor": _safe_float(card.get("profit_factor")),
            "reason": (
                "entry_quality_distressed_realized_profit_should_be_locked_harder"
                if distressed
                else "entry_quality_not_distressed"
            ),
        }

    def _weak_performance_lane_context(self, block: dict[str, Any]) -> dict[str, Any]:
        if self._block_metadata_has_weak_performance_lane(block):
            return {"matched": True, "source": "block_metadata"}
        runtime_context = self._runtime_weak_performance_lane_context(block)
        if runtime_context.get("matched"):
            return runtime_context
        return {"matched": False, "source": "none"}

    def _runtime_weak_performance_lane_context(self, block: dict[str, Any]) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        market = normalize_market(block.get("market") or block.get("venue"))
        side = normalize_position_side(block.get("side"))
        horizon = normalize_binance_horizon(block.get("horizon") or metadata.get("horizon"), market=market)
        lane = normalize_binance_display_lane(
            lane=block.get("lane") or metadata.get("lane"),
            market=market,
            horizon=horizon,
            side=side,
        )
        feedback_limit = self._performance_scorecard_feedback_limit()
        performance = self.repository.latest_performance_scorecard(limit=feedback_limit)
        lane_card = self._scorecard_for_lane(
            performance,
            market=market,
            side=side,
            lane=lane,
        )
        if not lane_card:
            return {"matched": False, "source": "runtime_scorecard"}
        min_samples = max(_safe_int(self.config.lane_performance_min_samples), 1)
        if not self._lane_card_requires_cooldown(lane_card, min_samples=min_samples):
            return {"matched": False, "source": "runtime_scorecard"}
        matched_card = {
            key: lane_card.get(key)
            for key in (
                "side",
                "lane",
                "sample_count",
                "pnl_usdt",
                "win_rate_pct",
                "avg_r_multiple",
                "profit_factor",
                "recovery_factor",
                "max_drawdown_r_multiple",
            )
            if lane_card.get(key) not in (None, "")
        }
        distressed = self._lane_card_is_distressed(lane_card)
        return {
            "matched": True,
            "source": "runtime_scorecard",
            "version": "runtime_weak_performance_lane_v1",
            "feedback_limit": feedback_limit,
            "min_samples": min_samples,
            "distressed": distressed,
            "row_lanes": sorted(self._growth_governor_row_lanes(block)),
            "matched_card": matched_card,
        }

    def _weak_lane_profit_protection_trigger(
        self,
        weak_lane_context: dict[str, Any],
    ) -> tuple[float, str]:
        context = dict(weak_lane_context or {})
        if "distressed" not in context:
            context["distressed"] = self._weak_lane_context_is_distressed(context)
        return build_weak_lane_profit_protection_trigger(
            context,
            weak_trigger_r=self.config.weak_lane_profit_lock_trigger_r,
            distressed_trigger_r=self.config.distressed_lane_profit_lock_trigger_r,
        )

    def _weak_lane_context_is_distressed(self, context: dict[str, Any]) -> bool:
        if not isinstance(context, dict) or not bool(context.get("matched")):
            return False
        if "distressed" in context:
            return bool(context.get("distressed"))
        matched_card = context.get("matched_card")
        if not isinstance(matched_card, dict):
            return False
        return self._lane_card_is_distressed(matched_card)

    def _lane_card_is_distressed(self, lane_card: dict[str, Any]) -> bool:
        return build_lane_card_is_distressed(
            lane_card,
            distressed_min_samples=self.config.distressed_lane_min_samples,
            lane_min_samples=self.config.lane_performance_min_samples,
            max_win_rate_pct=self.config.distressed_lane_max_win_rate_pct,
            max_profit_factor=self.config.distressed_lane_max_profit_factor,
        )

    @classmethod
    def _block_metadata_has_weak_performance_lane(cls, block: dict[str, Any]) -> bool:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        weak_lanes: set[str] = set()

        def collect(raw: Any) -> None:
            if isinstance(raw, (list, tuple, set)):
                for item in raw:
                    token = str(item or "").strip().lower()
                    if token:
                        weak_lanes.add(token)

        lane_authority = (
            metadata.get("lane_authority")
            if isinstance(metadata.get("lane_authority"), dict)
            else {}
        )
        collect(lane_authority.get("weak_lanes"))
        for key in (
            "cost_weak_lanes",
            "cost_evidence_weak_lanes",
            "entry_quality_weak_lanes",
            "validation_evidence_weak_lanes",
            "validation_repair_weak_lanes",
        ):
            collect(lane_authority.get(key))

        lane_gate = (
            metadata.get("lane_authority_gate")
            if isinstance(metadata.get("lane_authority_gate"), dict)
            else {}
        )
        collect(lane_gate.get("weak_lanes"))
        collect(lane_gate.get("insufficient_lanes"))
        collect(lane_gate.get("validation_evidence_weak_lanes"))
        collect(lane_gate.get("validation_repair_weak_lanes"))
        quality_hint = str(lane_gate.get("performance_quality_hint") or "").strip().lower()
        if quality_hint in {"weak", "weak_review", "repair_required"}:
            collect(lane_gate.get("matched_lanes"))
        lane_action = str(lane_gate.get("lane_action") or "").strip().lower()
        if "repair" in lane_action or "waiting" in lane_action or "probe" in lane_action:
            collect(lane_gate.get("matched_lanes"))
            collect(lane_gate.get("insufficient_lanes"))

        growth_governor = (
            metadata.get("growth_governor")
            if isinstance(metadata.get("growth_governor"), dict)
            else {}
        )
        collect(growth_governor.get("weak_lanes"))

        if not weak_lanes:
            return False
        if "all" in weak_lanes:
            return True
        row_lanes = cls._growth_governor_row_lanes(block)
        if weak_lanes.intersection(row_lanes):
            return True
        return any(
            weak_lane.startswith(f"{row_lane}:")
            for weak_lane in weak_lanes
            for row_lane in row_lanes
            if row_lane
        )

    def _exit_retry_due(self, block: dict[str, Any]) -> bool:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        retry_after_ts = _safe_float(metadata.get("exit_retry_after_ts"))
        return retry_after_ts <= 0 or time.time() >= retry_after_ts

    def _metadata_with_exit_retry(
        self,
        block: dict[str, Any],
        *,
        reason: str,
        cooldown_sec: int | None = None,
    ) -> dict[str, Any]:
        metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
        configured_cooldown = (
            int(self.config.failed_exit_retry_cooldown_sec)
            if cooldown_sec is None
            else int(cooldown_sec)
        )
        cooldown = max(configured_cooldown, 0)
        metadata["last_exit_retry_reason"] = _clean_text(reason, limit=300)
        metadata["last_exit_failure_at"] = utc_now_iso()
        metadata["exit_retry_cooldown_sec"] = cooldown
        if cooldown > 0:
            metadata["exit_retry_after_ts"] = time.time() + cooldown
        else:
            metadata.pop("exit_retry_after_ts", None)
        return metadata

    def _failed_exit_retry_cooldown_sec(
        self,
        *,
        market: str,
        response_status: str,
    ) -> int:
        normalized_market = normalize_market(market)
        status = str(response_status or "").upper()
        if normalized_market == "futures" and status in {
            "EXPIRED",
            "CANCELED",
            "CANCELLED",
        }:
            return 0
        return max(int(self.config.failed_exit_retry_cooldown_sec), 0)

    async def _exit_quantity_context(
        self,
        *,
        market: str,
        symbol: str,
        side: str,
        requested_qty: float,
        price: float,
    ) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        if normalized_market not in {"spot", UPBIT_SPOT_MARKET} or side != "sell" or requested_qty <= 0:
            return {"order_qty": requested_qty}
        account = await self._spot_exit_account_snapshot()
        asset = self._spot_asset_for_symbol(account, symbol, market=normalized_market)
        if asset is None:
            snapshot_error = (
                str(account.get("error_message") or "").strip()
                if str(account.get("status") or "").lower() == "error"
                else ""
            )
            checked = bool(account) and not snapshot_error
            return {
                "order_qty": 0.0,
                "requested_qty": requested_qty,
                "balance_checked": checked,
                "balance_source": (
                    "snapshot_error"
                    if snapshot_error
                    else "missing_asset"
                    if checked
                    else "missing_snapshot"
                ),
                "reconciliation_error": bool(snapshot_error) or checked,
                "error_message": snapshot_error
                or (
                    f"spot asset missing in account snapshot for {symbol}"
                    if checked
                    else f"spot account snapshot unavailable for {symbol}"
                ),
            }
        available_qty = _safe_float(asset.get("available"))
        total_qty = _safe_float(asset.get("qty"))
        locked_qty = _safe_float(asset.get("locked"))
        sellable_qty = available_qty if available_qty > 0 else max(total_qty - locked_qty, 0.0)
        if sellable_qty <= 0:
            raise ValueError(f"spot available quantity is zero for {symbol}")
        min_notional = (
            _safe_float(self.config.upbit_min_quote_budget_krw)
            if normalized_market == UPBIT_SPOT_MARKET
            else SPOT_ADOPTION_MIN_NOTIONAL_USDT
        )
        if price > 0 and sellable_qty * price < min_notional:
            return {
                "order_qty": 0.0,
                "requested_qty": requested_qty,
                "available_qty": available_qty,
                "locked_qty": locked_qty,
                "known_total_qty": total_qty,
                "balance_checked": True,
                "balance_source": "spot_account",
                "close_as_dust": True,
                "dust_notional_usdt": (
                    sellable_qty * price / max(_safe_float(self.config.upbit_usdt_krw_rate), 1.0)
                    if normalized_market == UPBIT_SPOT_MARKET
                    else sellable_qty * price
                ),
                "dust_notional": sellable_qty * price,
                "quote_currency": "KRW" if normalized_market == UPBIT_SPOT_MARKET else "USDT",
                "error_message": (
                    f"remaining spot balance is below minimum notional for {symbol}: "
                    f"{sellable_qty:g}"
                ),
            }
        order_qty = min(requested_qty, sellable_qty)
        return {
            "order_qty": order_qty,
            "requested_qty": requested_qty,
            "available_qty": available_qty,
            "locked_qty": locked_qty,
            "known_total_qty": total_qty,
            "balance_checked": True,
            "balance_source": "spot_account",
            "clamped": order_qty < requested_qty,
        }

    async def _spot_exit_account_snapshot(self) -> dict[str, Any]:
        try:
            account = build_normalize_account_snapshot(
                await self._collect_account_snapshot(),
                default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            )
        except Exception as exc:
            logger.warning("binance spot exit balance check failed: %s", exc)
            return {"status": "error", "error_message": str(exc) or exc.__class__.__name__}
        self._last_account_snapshot = account
        return account

    def _spot_asset_for_symbol(
        self,
        account: dict[str, Any],
        symbol: str,
        *,
        market: str = "spot",
    ) -> dict[str, Any] | None:
        if not isinstance(account, dict):
            return None
        target = str(symbol or "").upper()
        target_tokens = self._spot_asset_tokens_for_symbol(target, market=market)
        for asset in build_position_assets_for_market(account, market=market):
            asset_symbol = str(asset.get("symbol") or "").upper().strip()
            asset_code = str(asset.get("asset") or "").upper().strip()
            if asset_symbol == target or asset_symbol in target_tokens or asset_code in target_tokens:
                return asset
        return None

    @staticmethod
    def _spot_asset_tokens_for_symbol(symbol: str, *, market: str = "spot") -> set[str]:
        target = str(symbol or "").upper().strip()
        if not target:
            return set()
        tokens = {target}
        if normalize_market(market) == UPBIT_SPOT_MARKET and "-" in target:
            quote, _, base = target.partition("-")
            if quote and base:
                tokens.add(base)
        if "-" in target:
            _, _, base = target.partition("-")
            if base:
                tokens.add(base)
        for suffix in sorted(QUOTE_ASSET_SUFFIXES, key=len, reverse=True):
            if target.endswith(suffix) and len(target) > len(suffix):
                tokens.add(target[: -len(suffix)])
                break
        return {token for token in tokens if token}

    async def _maybe_create_entry(self, block: dict[str, Any]) -> dict[str, Any] | None:
        market = normalize_market(block.get("market"))
        symbol = str(block.get("symbol") or "").upper()
        side = build_entry_order_side(block)
        reference = await self._entry_execution_reference(
            market=market,
            symbol=symbol,
            side=side,
        )
        price = _safe_float(reference.get("execution_price"))
        if price <= 0:
            return None
        if not build_entry_trigger_fired(
            block,
            price=price,
            order_side=build_entry_order_side(block),
        ):
            return None
        block_id = str(block.get("block_id") or "")
        exchange_min_preflight = await self._exchange_min_order_preflight_rejection(block)
        if exchange_min_preflight is not None:
            reason = str(
                exchange_min_preflight.get("reason") or "exchange_min_order_rejected"
            )
            updated = self.repository.update_block(
                block_id,
                {
                    "status": "error",
                    "qty_open": 0.0,
                    "llm_reason": "entry_exchange_min_order_rejected",
                    "risk_note": reason,
                },
            )
            self.repository.add_event(
                block_id,
                "entry_preflight_blocked",
                reason,
                {
                    "stage": "exchange_min_order",
                    "price": price,
                    "side": side,
                    "reference": reference,
                },
            )
            return {
                "status": "ENTRY_PREFLIGHT_BLOCKED",
                "reason": reason,
                "preflight": {
                    "stage": "exchange_min_order",
                    "reason": reason,
                },
                "block": updated,
                "block_id": block_id,
                "symbol": symbol,
                "market": market,
                "side": side,
                "price": price,
            }
        claimed = self.repository.claim_entry_pending(
            block_id,
            reason="entry_triggered",
            payload={
                "price": price,
                "side": side,
                "reference": reference,
                "metadata": block.get("metadata") or {},
            },
        )
        if not claimed:
            return None
        preflight = await self._preflight_waiting_entry(claimed)
        if preflight is not None:
            return {
                **preflight,
                "block_id": block_id,
                "symbol": symbol,
                "market": market,
                "side": side,
                "price": price,
            }
        entry = await self._submit_entry_for_block(claimed)
        action = {
            **entry,
            "block_id": block_id,
            "symbol": symbol,
            "market": market,
            "side": side,
            "price": price,
        }
        action.setdefault("reason", "entry_triggered")
        return action

    async def _preflight_waiting_entry(
        self,
        block: dict[str, Any],
        *,
        reference: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        block_id = str(block.get("block_id") or "")
        market = normalize_market(block.get("market"))
        symbol = str(block.get("symbol") or "").upper()
        book = (
            reference
            if isinstance(reference, dict) and reference
            else await self._fetch_book_ticker(symbol=symbol, market=market)
        )
        bid = _safe_float(book.get("bid"))
        ask = _safe_float(book.get("ask"))
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        spread_bps = _safe_float(book.get("spread_bps"))
        if spread_bps <= 0 and mid > 0:
            spread_bps = max((ask - bid) / mid * 10_000.0, 0.0)
        max_spread_bps = WAITING_ENTRY_PREFLIGHT_MAX_SPREAD_BPS
        reason = ""
        if bid <= 0 or ask <= 0 or ask <= bid:
            reason = "preflight_book_invalid"
        elif spread_bps > max_spread_bps:
            reason = "preflight_spread_too_wide"
        if not reason:
            return None

        book_diagnostic = {
            "symbol": symbol,
            "market": market,
            "bid": bid,
            "ask": ask,
            "price": _safe_float(book.get("price")),
            "spread_bps": spread_bps,
            "source": str(book.get("source") or ""),
            "fetched_at": str(book.get("fetched_at") or ""),
            "status": str(book.get("status") or ""),
            "error_message": str(book.get("error_message") or ""),
        }
        diagnostic = {
            "reason": reason,
            "symbol": symbol,
            "market": market,
            "bid": bid,
            "ask": ask,
            "spread_bps": spread_bps,
            "max_spread_bps": max_spread_bps,
            "book": book_diagnostic,
        }
        metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
        metadata.update(
            {
                "entry_trigger_status": "blocked",
                "entry_trigger_reason": reason,
                "last_entry_preflight": diagnostic,
            }
        )
        note = f"{reason}: bid={bid:g} ask={ask:g} spread_bps={spread_bps:g}"
        updated = self.repository.update_block(
            block_id,
            {
                "status": "paused",
                "qty_open": 0.0,
                "llm_reason": reason,
                "risk_note": note,
                "metadata": metadata,
            },
        )
        self.repository.add_event(
            block_id,
            "entry_preflight_blocked",
            note,
            diagnostic,
        )
        return {
            "status": "blocked",
            "reason": reason,
            "preflight": diagnostic,
            "block": updated,
        }

    @staticmethod
    def _datetime_ms(value: Any) -> int:
        parsed = _parse_iso_datetime(value)
        if parsed is None:
            return 0
        return int(parsed.timestamp() * 1000)

    def _order_enrichment_window_ms(self, block: dict[str, Any]) -> tuple[int | None, int | None]:
        start_ms = (
            self._datetime_ms(block.get("opened_at"))
            or self._datetime_ms(block.get("created_at"))
        )
        if start_ms > 0:
            start_ms = max(start_ms - 60 * 60 * 1000, 0)
        end_ms = int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp() * 1000)
        return (start_ms or None, end_ms)

    async def _enrich_order_response_for_costs(
        self,
        *,
        block: dict[str, Any],
        market: str,
        symbol: str,
        response: dict[str, Any],
        include_funding: bool = False,
    ) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        if normalized_market not in {"spot", "futures"}:
            return response
        out = dict(response)
        order_id = build_response_order_id(out)
        if not order_id:
            return out
        if build_response_filled_qty(out, requested_qty=0.0) <= 0:
            return out
        adapter = self._adapter_for_market(normalized_market)
        if adapter is None:
            out["cost_enrichment"] = {
                "status": "error",
                "reason": "missing_adapter",
            }
            return out

        enrichment: dict[str, Any] = {
            "status": "ok",
            "order_id": order_id,
            "sources": [],
        }
        try:
            if normalized_market == "spot":
                method = getattr(adapter, "fetch_spot_my_trades", None)
                if method is None:
                    raise RuntimeError("missing fetch_spot_my_trades")
                trades = await _maybe_await(
                    method(symbol, order_id=order_id, limit=100)
                )
                out["trade_fills"] = trades
                enrichment["sources"].append("spot_my_trades")
            else:
                method = getattr(adapter, "fetch_futures_user_trades", None)
                if method is None:
                    raise RuntimeError("missing fetch_futures_user_trades")
                trades = await _maybe_await(
                    method(symbol, order_id=order_id, limit=100)
                )
                out["trade_fills"] = trades
                enrichment["sources"].append("futures_user_trades")
                if include_funding:
                    funding_method = getattr(adapter, "fetch_futures_income_history", None)
                    if funding_method is None:
                        raise RuntimeError("missing fetch_futures_income_history")
                    start_ms, end_ms = self._order_enrichment_window_ms(block)
                    funding = await _maybe_await(
                        funding_method(
                            symbol,
                            income_type="FUNDING_FEE",
                            start_time=start_ms,
                            end_time=end_ms,
                            limit=100,
                        )
                    )
                    out["funding_history"] = funding
                    enrichment["sources"].append("futures_income_funding")
        except Exception as exc:
            enrichment = {
                "status": "error",
                "order_id": order_id,
                "error_message": str(exc),
            }
        out["cost_enrichment"] = enrichment
        return out

    def _execution_enabled(self, market: str, *, for_entry: bool = False) -> bool:
        if self.kill_switch().get("enabled"):
            return False
        if not self.config.enabled:
            return False
        if for_entry and not self._entry_risk_guard_allows_new_entries():
            return False
        if market == "spot":
            return bool(self.config.execute_spot_orders)
        if market == UPBIT_SPOT_MARKET:
            return bool(self.config.execute_upbit_orders)
        return bool(self.config.execute_futures_orders)

    async def _submit_exit_order(
        self,
        *,
        market: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        allow_reduce_only_below_min_notional: bool = False,
    ) -> dict[str, Any]:
        adapter = self._adapter_for_market(market)
        method_name = "submit_futures_order" if market == "futures" else "submit_spot_order"
        method = getattr(adapter, method_name, None) if adapter is not None else None
        if method is None:
            venue = "Upbit" if is_upbit_market(market) else "Binance"
            raise RuntimeError(f"missing {venue} adapter method: {method_name}")
        bps = max(float(self.config.aggressive_limit_bps), 0.0)
        multiplier = 1.0 + (bps / 10_000.0) if side == "buy" else 1.0 - (bps / 10_000.0)
        limit_price = max(price * multiplier, 0.0)
        normalized = await self._normalize_order_for_exchange(
            market=market,
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            allow_reduce_only_below_min_notional=(
                bool(allow_reduce_only_below_min_notional) and market == "futures"
            ),
        )
        client_order_id = self._client_order_id(market=market, symbol=symbol, side=side)
        kwargs = {
            "symbol": symbol,
            "side": side,
            "quantity": normalized["quantity"],
            "limit_price": normalized["limit_price"],
            "client_order_id": client_order_id,
        }
        if market == "futures":
            kwargs["reduce_only"] = True
        return await _maybe_await(method(**kwargs))

    def _client_order_id(self, *, market: str, symbol: str, side: str) -> str:
        stamp = int(time.time() * 1000)
        raw = f"ju{market[0]}{side[0]}{symbol}{stamp}"
        return re.sub(r"[^A-Za-z0-9_-]", "", raw)[:36]

    async def _augment_snapshot_with_upbit(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        out = dict(snapshot)
        out.setdefault("upbit_spot_assets", [])
        out.setdefault("upbit_cash_krw", 0.0)
        out.setdefault("upbit_cash_usdt", 0.0)
        out.setdefault("upbit_usdt_krw_rate", max(_safe_float(self.config.upbit_usdt_krw_rate), 1.0))
        upbit_method = getattr(self.upbit, "fetch_balance_assets", None) if self.upbit is not None else None
        if upbit_method is None:
            return out
        errors = list(out.get("errors") or []) if isinstance(out.get("errors"), list) else []
        try:
            upbit_assets = await _maybe_await(upbit_method())
            if isinstance(upbit_assets, list):
                out["upbit_spot_assets"] = upbit_assets
                upbit_cash = sum(
                    _safe_float(row.get("qty"))
                    for row in upbit_assets
                    if isinstance(row, dict)
                    and str(row.get("kind") or "") == "cash"
                    and str(row.get("asset") or "").upper() == "KRW"
                )
                out["upbit_cash_krw"] = upbit_cash
                out["upbit_cash_usdt"] = upbit_cash / max(
                    _safe_float(self.config.upbit_usdt_krw_rate),
                    1.0,
                )
        except Exception as exc:
            errors.append({"source": "upbit_spot_assets", "error_message": str(exc)})
            out["errors"] = errors
            current_status = str(out.get("status") or "ok")
            out["status"] = "partial" if current_status == "ok" else current_status
        return out

    async def _collect_account_snapshot(self) -> dict[str, Any]:
        for name in (
            "fetch_account_snapshot",
            "account_snapshot",
            "fetch_account",
        ):
            method = getattr(self.adapter, name, None)
            if method is None:
                continue
            payload = await _maybe_await(method())
            if isinstance(payload, dict):
                return await self._augment_snapshot_with_upbit(payload)
        out: dict[str, Any] = {
            "status": "ok",
            "spot_assets": [],
            "upbit_spot_assets": [],
            "futures_assets": [],
            "futures_position_risk": [],
            "spot_cash_usdt": 0.0,
            "upbit_cash_krw": 0.0,
            "upbit_cash_usdt": 0.0,
            "upbit_usdt_krw_rate": max(_safe_float(self.config.upbit_usdt_krw_rate), 1.0),
            "futures_cash_usdt": 0.0,
            "errors": [],
        }
        spot_method = getattr(self.adapter, "fetch_spot_assets", None)
        if spot_method is not None:
            try:
                spot_assets = await _maybe_await(spot_method())
                if isinstance(spot_assets, list):
                    out["spot_assets"] = spot_assets
                    out["spot_cash_usdt"] = sum(
                        _safe_float(row.get("qty"))
                        for row in spot_assets
                        if isinstance(row, dict)
                        and str(row.get("kind") or "") == "cash"
                        and str(row.get("asset") or "").upper() in {"USDT", "USDC"}
                    )
            except Exception as exc:
                out["errors"].append({"source": "spot_assets", "error_message": str(exc)})
        out = await self._augment_snapshot_with_upbit(out)
        futures_method = getattr(self.adapter, "fetch_futures_assets", None)
        if futures_method is not None:
            try:
                futures_assets = await _maybe_await(futures_method())
                if isinstance(futures_assets, list):
                    out["futures_assets"] = futures_assets
                    out["futures_cash_usdt"] = sum(
                        _safe_float(row.get("qty"))
                        for row in futures_assets
                        if isinstance(row, dict) and str(row.get("kind") or "") == "cash"
                    )
            except Exception as exc:
                out["errors"].append({"source": "futures_assets", "error_message": str(exc)})
        risk_method = getattr(self.adapter, "fetch_futures_position_risk", None)
        if risk_method is not None:
            try:
                risk = await _maybe_await(risk_method())
                if isinstance(risk, list):
                    out["futures_position_risk"] = risk
            except Exception as exc:
                out["errors"].append({"source": "futures_position_risk", "error_message": str(exc)})
        if out["errors"] and not (
            out["spot_assets"] or out["upbit_spot_assets"] or out["futures_assets"]
        ):
            out["status"] = "error"
        elif out["errors"]:
            out["status"] = "partial"
        if out["spot_assets"] or out["upbit_spot_assets"] or out["futures_assets"] or out["errors"]:
            return out
        return {"status": "missing"}

    def _memory_context(
        self,
        *,
        symbols: list[str],
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.memory_provider is None:
            return {}
        try:
            payload = self.memory_provider(
                symbols=symbols,
                blocks=blocks,
                target_scope="binance",
                source_scope="binance",
            )
        except TypeError:
            try:
                payload = self.memory_provider(symbols=symbols, blocks=blocks)
            except TypeError:
                payload = self.memory_provider()
        return payload if isinstance(payload, dict) else {}

    def _wiki_context(
        self,
        *,
        target_scope: str,
        symbols: list[str],
        page_types: list[str] | None = None,
        lanes: list[str] | None = None,
        regimes: list[str] | None = None,
        block_ids: list[str] | None = None,
        horizons: list[str] | None = None,
    ) -> dict[str, Any]:
        provider = self.wiki_context_provider
        clean_symbols = [
            str(symbol).strip().upper()
            for symbol in symbols
            if str(symbol).strip()
        ]
        clean_horizons = [
            str(horizon).strip().lower()
            for horizon in list(horizons or [])
            if str(horizon).strip()
        ]
        if provider is None:
            return {
                "status": "missing",
                "reason": "wiki_context_provider_not_configured",
                "target_scope": target_scope,
                "symbols": clean_symbols,
                "horizons": clean_horizons,
            }
        try:
            payload = _call_wiki_context_provider(
                provider,
                target_scope=target_scope,
                symbols=clean_symbols,
                page_types=page_types,
                lanes=lanes,
                regimes=regimes,
                block_ids=block_ids,
                horizons=clean_horizons,
                max_chars=self.config.jue_wiki_context_max_chars,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error_message": str(exc),
                "target_scope": target_scope,
                "symbols": clean_symbols,
                "horizons": clean_horizons,
            }
        return (
            payload
            if isinstance(payload, dict)
            else {
                "status": "error",
                "error_message": "wiki_context_provider_returned_non_dict",
                "target_scope": target_scope,
                "symbols": clean_symbols,
                "horizons": clean_horizons,
            }
        )

    def _crypto_research_context(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.crypto_research_provider is None:
            return {"status": "missing"}
        context_limit = min(
            max(int(self.config.max_manager_symbols) * 3, 80),
            180,
        )
        try:
            payload = self.crypto_research_provider.latest_context(
                symbols=None,
                limit=context_limit,
            )
        except TypeError:
            payload = self.crypto_research_provider.latest_context(
                limit=context_limit
            )
        except Exception as exc:
            logger.warning("binance crypto research context failed: %s", exc)
            return {"status": "error", "error_message": str(exc)}
        if not isinstance(payload, dict):
            return {"status": "malformed"}
        payload = dict(payload)
        payload.setdefault("requested_symbols", symbols)
        return payload

    @staticmethod
    def _crypto_market_pulse_from_research(
        crypto_research: dict[str, Any],
    ) -> dict[str, Any]:
        pulse = {
            "status": "missing",
            "regime": "",
            "regime_brief": {},
            "market_direction": "",
            "risk_posture": "",
            "lane_bias": {},
            "horizon_bias": {},
            "operator_summary_ko": "",
            "external_notes": [],
            "derivatives_notes": [],
            "major_count": 0,
            "major_rows": [],
            "avg_major_change_pct_24h": 0.0,
            "avg_spread_bps": 0.0,
            "candidate_count": 0,
            "long_candidate_count": 0,
            "short_candidate_count": 0,
            "hold_candidate_count": 0,
            "data_gaps": [],
        }
        if not isinstance(crypto_research, dict):
            pulse["data_gaps"] = ["crypto_research_missing"]
            return pulse

        source_status = str(crypto_research.get("status") or "").strip().lower()
        regime = _clean_text(crypto_research.get("regime"), limit=80)
        market_regime = (
            crypto_research.get("market_regime")
            if isinstance(crypto_research.get("market_regime"), dict)
            else {}
        )
        regime_brief = (
            crypto_research.get("regime_brief")
            if isinstance(crypto_research.get("regime_brief"), dict)
            else {}
        )
        if not regime:
            regime = _clean_text(market_regime.get("regime"), limit=80)
        if not regime:
            regime = _clean_text(regime_brief.get("regime"), limit=80)
        pulse["regime"] = regime
        if regime_brief:
            pulse["regime_brief"] = {
                "version": _clean_text(regime_brief.get("version"), limit=80),
                "status": _clean_text(regime_brief.get("status"), limit=40),
                "regime": _clean_text(regime_brief.get("regime"), limit=80),
                "market_direction": _clean_text(
                    regime_brief.get("market_direction"),
                    limit=80,
                ),
                "risk_posture": _clean_text(regime_brief.get("risk_posture"), limit=80),
                "breadth": (
                    regime_brief.get("breadth")
                    if isinstance(regime_brief.get("breadth"), dict)
                    else {}
                ),
                "lane_bias": (
                    regime_brief.get("lane_bias")
                    if isinstance(regime_brief.get("lane_bias"), dict)
                    else {}
                ),
                "horizon_bias": (
                    regime_brief.get("horizon_bias")
                    if isinstance(regime_brief.get("horizon_bias"), dict)
                    else {}
                ),
                "rotation_notes": [
                    _clean_text(note, limit=160)
                    for note in list(regime_brief.get("rotation_notes") or [])[:6]
                ],
                "external_notes": [
                    note
                    for note in list(regime_brief.get("external_notes") or [])[:6]
                    if isinstance(note, dict)
                ],
                "derivatives_notes": [
                    note
                    for note in list(regime_brief.get("derivatives_notes") or [])[:6]
                    if isinstance(note, dict)
                ],
                "operator_summary_ko": _clean_text(
                    regime_brief.get("operator_summary_ko"),
                    limit=360,
                ),
                "data_gaps": [
                    _clean_text(gap, limit=100)
                    for gap in list(regime_brief.get("data_gaps") or [])[:8]
                ],
            }
            pulse["market_direction"] = pulse["regime_brief"]["market_direction"]
            pulse["risk_posture"] = pulse["regime_brief"]["risk_posture"]
            pulse["lane_bias"] = pulse["regime_brief"]["lane_bias"]
            pulse["horizon_bias"] = pulse["regime_brief"]["horizon_bias"]
            pulse["operator_summary_ko"] = pulse["regime_brief"]["operator_summary_ko"]
            pulse["external_notes"] = pulse["regime_brief"]["external_notes"]
            pulse["derivatives_notes"] = pulse["regime_brief"]["derivatives_notes"]

        def first_present(
            row: dict[str, Any],
            features: dict[str, Any],
            keys: tuple[str, ...],
        ) -> Any:
            for key in keys:
                for source in (row, features):
                    if key in source and source.get(key) not in (None, ""):
                        return source.get(key)
            return None

        raw_items = crypto_research.get("items")
        items = raw_items if isinstance(raw_items, (list, tuple)) else []
        raw_candidates = crypto_research.get("candidates")
        candidates = [
            row
            for row in (raw_candidates if isinstance(raw_candidates, (list, tuple)) else [])
            if isinstance(row, dict)
        ]
        candidate_market_by_symbol: dict[str, str] = {}
        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "").upper().strip()
            if symbol and symbol not in candidate_market_by_symbol:
                candidate_market_by_symbol[symbol] = normalize_market(
                    candidate.get("market") or candidate.get("venue")
                )
        market_feature_index = build_crypto_research_market_feature_index(crypto_research)

        def pulse_metric_sources(
            row: dict[str, Any],
            features: dict[str, Any],
            symbol: str,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            market = normalize_market(
                row.get("market")
                or row.get("venue")
                or candidate_market_by_symbol.get(symbol)
                or "spot"
            )
            scoped_features = market_feature_index.get((symbol, market))
            if not isinstance(scoped_features, dict) or not bool(scoped_features.get("book_fresh")):
                return row, features
            next_row = dict(row)
            for field in BOOK_FIELD_KEYS:
                next_row.pop(field, None)
            return next_row, dict(scoped_features)

        major_by_symbol: dict[str, dict[str, Any]] = {}
        for row in items:
            if not isinstance(row, dict):
                continue
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            symbol = str(row.get("symbol") or features.get("symbol") or "").upper().strip()
            if symbol not in CRYPTO_MARKET_PULSE_MAJORS or symbol in major_by_symbol:
                continue
            metric_row, metric_features = pulse_metric_sources(row, features, symbol)
            major_by_symbol[symbol] = {
                "symbol": symbol,
                "change_pct_24h": _safe_float(
                    first_present(
                        metric_row,
                        metric_features,
                        ("change_pct_24h", "price_change_pct_24h", "priceChangePercent"),
                    )
                ),
                "spread_bps": _safe_float(first_present(metric_row, metric_features, ("spread_bps",))),
                "entry_quality": _clean_text(
                    first_present(metric_row, metric_features, ("entry_quality", "entry_style")),
                    limit=80,
                ),
            }
        major_rows = [
            major_by_symbol[symbol]
            for symbol in CRYPTO_MARKET_PULSE_MAJORS
            if symbol in major_by_symbol
        ]
        pulse["major_rows"] = major_rows
        pulse["major_count"] = len(major_rows)
        if major_rows:
            pulse["avg_major_change_pct_24h"] = round(
                sum(_safe_float(row.get("change_pct_24h")) for row in major_rows)
                / len(major_rows),
                4,
            )
            pulse["avg_spread_bps"] = round(
                sum(_safe_float(row.get("spread_bps")) for row in major_rows)
                / len(major_rows),
                4,
            )

        pulse["candidate_count"] = len(candidates)
        for candidate in candidates:
            raw_stance = str(
                candidate.get("stance")
                or candidate.get("side")
                or candidate.get("direction")
                or candidate.get("signal")
                or ""
            ).strip().lower()
            if any(
                token in raw_stance
                for token in ("hold", "avoid", "neutral", "no_trade", "notrade")
            ):
                pulse["hold_candidate_count"] += 1
            elif "short" in raw_stance or raw_stance in {"sell", "bearish", "down"}:
                pulse["short_candidate_count"] += 1
            elif "long" in raw_stance or raw_stance in {"buy", "bullish", "up"}:
                pulse["long_candidate_count"] += 1
            else:
                pulse["hold_candidate_count"] += 1

        data_gaps: list[str] = []
        if source_status and source_status not in {"ok", "partial"}:
            data_gaps.append(f"crypto_research_{source_status}")
        if not major_rows:
            data_gaps.append("crypto_major_rows_missing")
        if not candidates:
            data_gaps.append("crypto_candidates_missing")
        if regime_brief:
            data_gaps.extend(
                _clean_text(gap, limit=100)
                for gap in list(regime_brief.get("data_gaps") or [])[:8]
            )
        pulse["data_gaps"] = data_gaps
        if source_status == "missing" or (not major_rows and not candidates):
            pulse["status"] = "missing"
        elif data_gaps or source_status == "partial":
            pulse["status"] = "partial"
        else:
            pulse["status"] = "ok"
        return pulse

    def _crypto_alpha_context(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.crypto_alpha_provider is None:
            return {
                "status": "missing",
                "events": [],
                "scorecards": [],
                "data_gaps": ["crypto_alpha_missing"],
            }
        try:
            payload = self.crypto_alpha_provider.context_pack(
                symbols=symbols,
                limit=12,
            )
        except TypeError:
            payload = self.crypto_alpha_provider.context_pack(symbols=symbols)
        except Exception as exc:
            logger.warning("binance crypto alpha context failed: %s", exc)
            return {
                "status": "error",
                "events": [],
                "scorecards": [],
                "error_message": str(exc),
            }
        return payload if isinstance(payload, dict) else {"status": "malformed"}

    def _crypto_quant_context(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.quant_provider is None:
            return {"status": "missing", "items": [], "history": {"items": []}}
        try:
            latest_signals = getattr(self.quant_provider, "latest_signals")
            items = latest_signals(
                symbols=symbols,
                limit=max(int(self.config.quant_context_limit), 1),
            )
            retrieval_context = getattr(self.quant_provider, "retrieval_context", None)
            history = (
                retrieval_context(
                    symbols=symbols,
                    horizon="intraday",
                    points_per_symbol=12,
                )
                if retrieval_context is not None
                else {"status": "missing", "items": []}
            )
            latest_evidence = getattr(self.quant_provider, "latest_evidence", None)
            evidence: list[dict[str, Any]] = []
            if latest_evidence is not None:
                try:
                    evidence_payload = latest_evidence(
                        symbols=symbols,
                        limit=max(int(self.config.quant_context_limit), 1),
                    )
                except TypeError:
                    evidence_payload = latest_evidence(
                        symbols=symbols,
                    )
                evidence = evidence_payload if isinstance(evidence_payload, list) else []
        except Exception as exc:
            logger.warning("binance crypto quant context failed: %s", exc)
            return {
                "status": "error",
                "error_message": str(exc),
                "items": [],
                "history": {"items": []},
                "evidence": [],
            }
        return {
            "status": "ok",
            "items": items,
            "history": history,
            "evidence": evidence if isinstance(evidence, list) else [],
            "policy": {
                "meaning": (
                    "Quant scores are directional evidence. They adjust conviction "
                    "and sizing, not deterministic safety gates."
                ),
                "biases": ["long", "short", "no_trade"],
                "preferred_use": (
                    "Compare latest scores and short history trends before creating blocks."
                ),
            },
        }

    def _crypto_pattern_context(self, *, symbols: list[str]) -> dict[str, Any]:
        provider = getattr(self, "crypto_pattern_provider", None)
        if provider is None:
            return {"status": "missing", "scorecards": []}
        try:
            context_pack = getattr(provider, "context_pack")
            payload = context_pack(
                symbols=symbols,
                limit=max(int(self.config.quant_context_limit), 1),
            )
        except Exception as exc:
            logger.warning("binance crypto pattern context failed: %s", exc)
            return {
                "status": "error",
                "error_message": str(exc),
                "scorecards": [],
            }
        return (
            payload
            if isinstance(payload, dict)
            else {"status": "malformed", "scorecards": []}
        )

    async def _complete_manager_json(self, prompt: dict[str, Any]) -> dict[str, Any]:
        if self.codex_runtime is None:
            raise RuntimeError("codex_runtime_unavailable")
        complete_json = getattr(self.codex_runtime, "complete_json", None)
        if complete_json is not None:
            try:
                payload = await _maybe_await(
                    complete_json(
                        prompt,
                        model=self.config.llm_model,
                        reasoning_effort=self.config.llm_reasoning_effort,
                    )
                )
            except TypeError:
                payload = await _maybe_await(complete_json(prompt))
            if isinstance(payload, dict):
                build_raise_for_manager_llm_error_payload(
                    payload,
                    allowed_actions=ALLOWED_MANAGER_ACTIONS,
                )
                return payload
            if isinstance(payload, str):
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    build_raise_for_manager_llm_error_payload(
                        parsed,
                        allowed_actions=ALLOWED_MANAGER_ACTIONS,
                    )
                    return parsed
        complete = getattr(self.codex_runtime, "complete", None)
        if complete is None:
            raise RuntimeError("codex_runtime missing completion method")
        try:
            timeout_ms = (
                int(self.config.llm_timeout_ms)
                if int(self.config.llm_timeout_ms) > 0
                else None
            )
            response = await _maybe_await(complete(prompt, timeout_ms=timeout_ms))
        except TypeError:
            response = await _maybe_await(complete(prompt))
        if isinstance(response, dict):
            build_raise_for_manager_llm_error_payload(
                response,
                allowed_actions=ALLOWED_MANAGER_ACTIONS,
            )
            content = response.get("content") or response.get("output_text") or response.get("text")
            if isinstance(content, dict):
                build_raise_for_manager_llm_error_payload(
                    content,
                    allowed_actions=ALLOWED_MANAGER_ACTIONS,
                )
                return content
            if isinstance(content, str):
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    build_raise_for_manager_llm_error_payload(
                        parsed,
                        allowed_actions=ALLOWED_MANAGER_ACTIONS,
                    )
                    return parsed
            return response
        if isinstance(response, str):
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                build_raise_for_manager_llm_error_payload(
                    parsed,
                    allowed_actions=ALLOWED_MANAGER_ACTIONS,
                )
                return parsed
        raise ValueError("LLM response did not contain a JSON object")

    @staticmethod
    def _apply_validation_repair_to_actions(
        actions: dict[str, list[dict[str, Any]]],
        *,
        validation_repair: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        metadata = build_validation_repair_action_metadata(validation_repair)
        repair = metadata.get("validation_repair") if metadata else None
        if not isinstance(repair, dict):
            return actions
        adjusted: dict[str, list[dict[str, Any]]] = {}
        note = build_validation_repair_note(repair)
        for key in sorted(ALLOWED_MANAGER_ACTIONS):
            rows: list[dict[str, Any]] = []
            for row in actions.get(key, []):
                if not isinstance(row, dict):
                    continue
                copied = dict(row)
                row_metadata = dict(
                    copied.get("metadata")
                    if isinstance(copied.get("metadata"), dict)
                    else {}
                )
                row_metadata["validation_repair"] = repair
                evidence_plan = build_validation_evidence_plan_from_repair(repair)
                existing_evidence = (
                    row_metadata.get("validation_evidence")
                    if isinstance(row_metadata.get("validation_evidence"), dict)
                    else {}
                )
                if evidence_plan or existing_evidence:
                    row_metadata["validation_evidence"] = {
                        **evidence_plan,
                        **existing_evidence,
                    }
                copied["metadata"] = row_metadata
                copied["validation_repair"] = repair
                if key == "create_blocks":
                    enforcement = BinanceBlockTrader._validation_repair_create_enforcement(
                        copied,
                        repair,
                    )
                    if enforcement:
                        copied["validation_repair_enforcement"] = enforcement
                        row_metadata["validation_repair_enforcement"] = enforcement
                if note and key == "create_blocks":
                    risk_note = _clean_text(copied.get("risk_note"), limit=1600)
                    copied["risk_note"] = (
                        _clean_text(f"{risk_note}\n{note}", limit=2000)
                        if risk_note
                        else note
                    )
                elif note and key in {"update_blocks", "close_blocks", "pause_blocks"}:
                    reason = _clean_text(copied.get("reason"), limit=1600)
                    copied["reason"] = (
                        _clean_text(f"{reason}\n{note}", limit=2000)
                        if reason
                        else note
                    )
                rows.append(copied)
            adjusted[key] = rows
        return adjusted

    @staticmethod
    def _validation_repair_create_enforcement(
        row: dict[str, Any],
        repair: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(repair, dict):
            return {}
        statuses = {
            str(value or "").strip().lower()
            for value in _as_list(repair.get("last_repair_statuses"))
            if str(value or "").strip()
        }
        entry_tokens = " ".join(
            str(value or "").strip().lower()
            for value in [
                *_as_list(repair.get("allowed_entry_postures")),
                *_as_list(repair.get("entry_biases")),
                *_as_list(repair.get("blocks_new_entries")),
            ]
            if str(value or "").strip()
        )
        sizing_tokens = " ".join(
            str(value or "").strip().lower()
            for value in [
                *_as_list(repair.get("sizing_policies")),
                *_as_list(repair.get("blocks_scaling")),
            ]
            if str(value or "").strip()
        )
        repair_pending = any(
            status.startswith("queued")
            or status in {"pending", "running", "active_caution", "error", "failed", "blocked"}
            for status in statuses
        )
        budget_multipliers = [
            value
            for value in (
                _safe_float(repair.get("risk_budget_multiplier")),
                _safe_float(repair.get("max_budget_multiplier")),
            )
            if 0 < value < 1
        ]
        budget_multiplier = min(budget_multipliers) if budget_multipliers else 0.0
        scale_blocked = (
            build_truthy_gate_value(repair.get("scale_up_blocked"))
            or repair_pending
            or budget_multiplier > 0
        )
        waiting_required = (
            build_truthy_gate_value(repair.get("live_shadow_required"))
            or any(
                token in entry_tokens
                for token in ("wait", "waiting", "probe", "verified", "shadow")
            )
            or repair_pending
        )
        enforcement: dict[str, Any] = {
            "version": "validation_repair_enforcement_v1",
            "repair_action_ids": list(repair.get("repair_action_ids") or [])[:6],
            "scale_up_blocked": scale_blocked,
            "waiting_entry_required": waiting_required,
            "last_repair_statuses": sorted(statuses)[:6],
            "budget_multiplier": (
                budget_multiplier
                if budget_multiplier > 0
                else 0.25
                if scale_blocked or "probe" in sizing_tokens
                else 1.0
            ),
            "adjustments": [],
        }
        if scale_blocked or "probe" in sizing_tokens:
            multiplier = _safe_float(enforcement.get("budget_multiplier")) or 0.25
            market = normalize_market(row.get("market") or row.get("venue"))
            for field in (
                "quote_budget_usdt",
                "risk_budget_usdt",
                "max_notional_usdt",
                "notional_usdt",
                "target_block_value_usdt",
                "quote_budget_krw",
                "risk_budget_krw",
                "notional_krw",
            ):
                original = _safe_float(row.get(field))
                if original <= 0:
                    continue
                raw_adjusted = round(original * multiplier, 8)
                adjusted = build_validation_repair_notional_floor(
                    field=field,
                    market=market,
                    original=original,
                    adjusted=raw_adjusted,
                )
                row[field] = adjusted
                adjustment = {
                    "field": field,
                    "from": original,
                    "to": adjusted,
                    "reason": "validation_repair_scale_up_blocked_probe_budget",
                }
                if adjusted > raw_adjusted:
                    adjustment["raw_scaled_to"] = raw_adjusted
                    adjustment["floor_reason"] = "minimum_executable_notional_floor"
                enforcement["adjustments"].append(adjustment)
            original_qty = _safe_float(row.get("qty"))
            if original_qty > 0:
                raw_adjusted_qty = round(original_qty * multiplier, 8)
                reference_price = (
                    _safe_float(row.get("entry_trigger_price"))
                    or _safe_float(row.get("entry_price"))
                    or _safe_float(row.get("entry_price_usdt"))
                )
                adjusted_qty = BinanceBlockTrader._minimum_executable_probe_qty_floor(
                    row=row,
                    market=market,
                    reference_price=reference_price,
                    original_qty=original_qty,
                    adjusted_qty=raw_adjusted_qty,
                    notional_floor=(
                        VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR
                        if normalize_market(market) == UPBIT_SPOT_MARKET
                        else VALIDATION_REPAIR_MIN_USDT_NOTIONAL_FLOOR
                    ),
                )
                row["qty"] = adjusted_qty
                adjustment = {
                    "field": "qty",
                    "from": original_qty,
                    "to": adjusted_qty,
                    "reason": "validation_repair_scale_up_blocked_probe_qty",
                }
                if adjusted_qty > raw_adjusted_qty:
                    adjustment["raw_scaled_to"] = raw_adjusted_qty
                    adjustment["floor_reason"] = "minimum_executable_notional_floor"
                enforcement["adjustments"].append(adjustment)
        current_style = str(row.get("entry_style") or "").strip().lower()
        if waiting_required and current_style not in {
            "wait_for_price",
            "waiting_entry",
            "triggered_entry",
        }:
            trigger_price = _safe_float(
                row.get("entry_trigger_price") or row.get("entry_price")
            )
            if trigger_price <= 0:
                enforcement["rejected"] = True
                enforcement["reason"] = (
                    "validation_repair_waiting_entry_requires_trigger_price"
                )
                return {
                    key: value
                    for key, value in enforcement.items()
                    if value not in (None, "", [], {})
                }
            side = normalize_position_side(row.get("side"))
            default_operator = ">=" if side == "short" else "<="
            row["entry_style"] = "wait_for_price"
            row["entry_trigger_price"] = trigger_price
            row["entry_trigger_operator"] = (
                build_normalize_entry_trigger_operator(
                    row.get("entry_trigger_operator"),
                    default=default_operator,
                )
            )
            enforcement["adjustments"].append(
                {
                    "field": "entry_style",
                    "from": current_style or "immediate",
                    "to": "wait_for_price",
                    "entry_trigger_price": trigger_price,
                    "entry_trigger_operator": row["entry_trigger_operator"],
                    "reason": "validation_repair_requires_waiting_entry",
                }
            )
        min_reward_risk = _safe_float(repair.get("min_reward_risk"))
        raw_max_stop_risk_pct = _safe_float(repair.get("max_stop_risk_pct"))
        max_stop_risk_pct, max_stop_override_reason = (
            build_effective_max_stop_risk_pct_for_row(row, raw_max_stop_risk_pct)
        )
        if min_reward_risk > 0 or max_stop_risk_pct > 0:
            side = normalize_position_side(row.get("side"))
            entry_price = (
                _safe_float(row.get("entry_trigger_price"))
                or _safe_float(row.get("entry_price"))
                or _safe_float(row.get("entry_price_usdt"))
            )
            structure = build_crypto_reward_risk(
                side=side,
                entry_price=entry_price,
                target_price=_safe_float(row.get("target_price") or row.get("target_price_usdt")),
                stop_price=_safe_float(row.get("stop_price") or row.get("stop_price_usdt")),
            )
            check = {
                "field": "target_stop",
                "side": side,
                "entry_price": round(entry_price, 8),
                "target_price": round(
                    _safe_float(row.get("target_price") or row.get("target_price_usdt")),
                    8,
                ),
                "stop_price": round(
                    _safe_float(row.get("stop_price") or row.get("stop_price_usdt")),
                    8,
                ),
                "min_reward_risk": round(min_reward_risk, 6)
                if min_reward_risk > 0
                else None,
                "max_stop_risk_pct": round(max_stop_risk_pct, 6)
                if max_stop_risk_pct > 0
                else None,
            }
            if max_stop_override_reason:
                check["raw_max_stop_risk_pct"] = round(raw_max_stop_risk_pct, 6)
                check["max_stop_risk_override_reason"] = max_stop_override_reason
            if structure.get("status") != "ok":
                check.update({
                    "status": "rejected",
                    "reason": "validation_repair_invalid_target_stop_structure",
                })
                enforcement["rejected"] = True
                enforcement["reason"] = "validation_repair_invalid_target_stop_structure"
                enforcement.setdefault("checks", []).append(check)
                return {
                    key: value
                    for key, value in enforcement.items()
                    if value not in (None, "", [], {})
                }
            reward_risk = _safe_float(structure.get("reward_risk"))
            stop_risk_pct = _safe_float(structure.get("stop_risk_pct"))
            check.update({
                "status": "ok",
                "reward_risk": round(reward_risk, 6),
                "stop_risk_pct": round(stop_risk_pct, 6),
            })
            if min_reward_risk > 0 and not build_reward_risk_meets_minimum(
                reward_risk,
                min_reward_risk,
            ):
                check.update({
                    "status": "rejected",
                    "reason": "validation_repair_min_reward_risk_not_met",
                })
                enforcement["rejected"] = True
                enforcement["reason"] = "validation_repair_min_reward_risk_not_met"
            if (
                max_stop_risk_pct > 0
                and stop_risk_pct - 1e-9 > max_stop_risk_pct
                and not enforcement.get("rejected")
            ):
                check.update({
                    "status": "rejected",
                    "reason": "validation_repair_max_stop_risk_pct_exceeded",
                })
                enforcement["rejected"] = True
                enforcement["reason"] = "validation_repair_max_stop_risk_pct_exceeded"
            enforcement.setdefault("checks", []).append(check)
            if enforcement.get("rejected"):
                return {
                    key: value
                    for key, value in enforcement.items()
                    if value not in (None, "", [], {})
                }
        return {
            key: value
            for key, value in enforcement.items()
            if value not in (None, "", [], {})
        }

    def _normalize_manager_contract_create_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        candidate = self._manager_contract_candidate_defaults(payload)
        row = dict(candidate) if candidate else {}
        for key, value in payload.items():
            if value is None or value == "":
                continue
            row[key] = value

        explicit_scope = explicit_market_scope(payload.get("market_or_account_scope"))
        market_scope = str(
            payload.get("market")
            or payload.get("venue")
            or row.get("market")
            or row.get("venue")
            or ""
        ).strip().lower()
        if explicit_scope:
            row["market"] = explicit_scope
            row["venue"] = explicit_scope
        elif market_scope:
            row["market"] = normalize_market(market_scope)
        side = self._infer_manager_contract_side(payload, candidate=candidate, row=row)
        if side:
            row["side"] = side
        raw_horizon = row.get("horizon")
        row["horizon"] = normalize_binance_horizon(
            raw_horizon,
            market=normalize_market(row.get("market") or row.get("venue")),
        )
        self._apply_manager_contract_aliases(row)
        metadata = dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {})
        metadata.update(
            {
                "manager_contract_decision": "create_blocks",
                "manager_contract_id": _clean_text(
                    payload.get("contract_id") or payload.get("selected_contract_id"),
                    limit=120,
                ),
                "manager_contract_source_id": _clean_text(payload.get("source_id"), limit=180),
                "manager_contract_defaults_used": bool(candidate),
            }
        )
        if payload.get("market_or_account_scope"):
            metadata["market_or_account_scope"] = _clean_text(
                payload.get("market_or_account_scope"),
                limit=80,
            )
        if raw_horizon not in (None, ""):
            metadata["manager_contract_raw_horizon"] = _clean_text(raw_horizon, limit=80)
        reasons = build_clean_string_list(payload.get("reasons"), limit=4)
        if reasons:
            metadata["manager_contract_reasons"] = reasons
        evidence = payload.get("evidence")
        if isinstance(evidence, list):
            metadata["manager_contract_evidence"] = build_compact_prompt_value(
                evidence,
                string_limit=180,
                list_limit=6,
            )
        row["metadata"] = metadata
        if not row.get("llm_reason"):
            row["llm_reason"] = _clean_text(
                payload.get("claim")
                or "; ".join(reasons)
                or payload.get("reason")
                or payload.get("thesis"),
                limit=1200,
            )
        return row

    def _manager_contract_candidate_defaults(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "").upper().strip()
        if not symbol:
            return {}
        explicit_scope = explicit_market_scope(payload.get("market_or_account_scope"))
        market_scope = str(
            payload.get("market") or payload.get("venue") or ""
        ).strip().lower()
        market = explicit_scope or (normalize_market(market_scope) if market_scope else "")
        side_hint = self._infer_manager_contract_side(payload, candidate={}, row={})
        horizon_hint = str(payload.get("horizon") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        candidates = self._unique_manager_candidates()
        best: dict[str, Any] = {}
        best_score = 0
        for candidate in candidates:
            candidate_symbol = str(candidate.get("symbol") or "").upper().strip()
            if candidate_symbol != symbol:
                continue
            candidate_market = normalize_market(candidate.get("market") or candidate.get("venue"))
            candidate_side = normalize_position_side(candidate.get("side"))
            candidate_horizon = normalize_binance_horizon(
                candidate.get("horizon"),
                market=candidate_market,
            )
            score = 100
            if market:
                if candidate_market != market:
                    continue
                score += 30
            if side_hint:
                if candidate_side != side_hint:
                    continue
                score += 30
            if horizon_hint and candidate_horizon == normalize_binance_horizon(
                horizon_hint,
                market=candidate_market,
            ):
                score += 10
            if source_id and self._candidate_matches_source_id(candidate, source_id):
                score += 8
            score += self._candidate_price_match_score(candidate, payload)
            if score > best_score:
                best = candidate
                best_score = score
        return dict(best) if best_score >= 100 else {}

    def _unique_manager_candidates(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        for candidate in self._last_manager_candidate_index.values():
            if not isinstance(candidate, dict):
                continue
            marker = id(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(candidate)
        return rows

    @staticmethod
    def _candidate_matches_source_id(candidate: dict[str, Any], source_id: str) -> bool:
        return build_manager_contract_candidate_matches_source_id(candidate, source_id)

    @staticmethod
    def _candidate_price_match_score(
        candidate: dict[str, Any],
        payload: dict[str, Any],
    ) -> int:
        return build_manager_contract_candidate_price_match_score(candidate, payload)

    @staticmethod
    def _infer_manager_contract_side(
        payload: dict[str, Any],
        *,
        candidate: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        return build_infer_manager_contract_side(payload, candidate=candidate, row=row)

    @staticmethod
    def _apply_manager_contract_aliases(row: dict[str, Any]) -> None:
        build_apply_manager_contract_aliases(row)

    def _entry_gate_policy(
        self,
        *,
        performance: dict[str, Any],
        memory_context: dict[str, Any],
    ) -> dict[str, Any]:
        min_confidence = max(min(float(self.config.min_entry_confidence), 1.0), 0.0)
        min_expected_r = max(float(self.config.min_entry_expected_r), 0.0)
        min_directional_score = max(float(self.config.min_entry_directional_score), 0.0)
        base_confidence = min_confidence
        base_expected_r = min_expected_r
        base_directional_score = min_directional_score
        sample_count = _safe_int(performance.get("sample_count"))
        win_rate = _safe_float(performance.get("win_rate_pct"))
        avg_r = _safe_float(performance.get("avg_r_multiple"))
        adjustment = "base"
        if sample_count >= 5 and (win_rate < 35.0 or avg_r < 0):
            min_confidence = min(min_confidence + 0.05, 0.78)
            min_expected_r = min_expected_r + 0.05
            min_directional_score = min(min_directional_score + 5.0, 78.0)
            adjustment = "tightened_by_recent_reflections"
        elif sample_count >= 8 and win_rate >= 50.0 and avg_r >= 0.2:
            min_confidence = max(min_confidence - 0.03, 0.5)
            min_expected_r = max(min_expected_r - 0.05, 0.35)
            min_directional_score = max(min_directional_score - 3.0, 55.0)
            adjustment = "relaxed_by_recent_reflections"

        policy_rules = memory_context.get("policy_rules")
        if isinstance(policy_rules, list):
            for row in policy_rules:
                if not isinstance(row, dict):
                    continue
                if str(row.get("scope") or "").lower() not in {"binance", "crypto"}:
                    continue
                if str(row.get("key") or row.get("policy_id") or "").lower().find("entry_gate") < 0:
                    continue
                strength = _safe_float(row.get("strength"))
                if strength > 0:
                    min_confidence = min(min_confidence + min(strength, 10.0) / 200.0, 0.82)
                    adjustment = "tightened_by_memory_policy"

        min_cooldown_samples = max(
            _safe_int(self.config.budget_performance_scale_min_samples),
            _safe_int(self.config.lane_performance_min_samples),
            1,
        )
        cooldown_lanes = self._entry_gate_cooldown_lanes(
            performance,
            min_samples=min_cooldown_samples,
        )
        entry_quality_cooldown_min_samples = max(
            _safe_int(self.config.lane_performance_min_samples),
            1,
        )
        entry_quality_cooldowns = self._entry_gate_cooldown_entry_qualities(
            performance,
            min_samples=entry_quality_cooldown_min_samples,
        )
        shadow_only_entry_qualities = (
            build_shadow_only_entry_qualities(entry_quality_cooldowns)
        )
        cooldown_symbols = self._entry_gate_cooldown_symbols(
            performance,
            min_samples=max(
                min(_safe_int(self.config.symbol_lane_cooldown_min_samples), 5),
                1,
            ),
            max_win_rate_pct=float(self.config.symbol_lane_cooldown_max_win_rate_pct),
        )
        return {
            "version": "binance_entry_gate_v1",
            "min_confidence": round(min_confidence, 4),
            "min_expected_r": round(min_expected_r, 4),
            "min_directional_score": round(min_directional_score, 4),
            "base_min_confidence": round(base_confidence, 4),
            "base_min_expected_r": round(base_expected_r, 4),
            "base_min_directional_score": round(base_directional_score, 4),
            "hold_stance_requires_explicit_upgrade": True,
            "watch_stance_requires_min_confidence": True,
            "adjustment": adjustment,
            "performance_sample_count": sample_count,
            "performance_win_rate_pct": win_rate,
            "performance_avg_r_multiple": avg_r,
            "lane_policy": (
                "Apply tightened performance gates to the matching market:side lane. "
                "Do not let futures losses fully freeze fresh spot exploration, and do "
                "not let spot losses fully freeze futures exploration."
            ),
            "lane_scorecards": self._entry_gate_lane_scorecards(performance),
            "cooldown_lanes": cooldown_lanes,
            "cooldown_lane_keys": sorted(cooldown_lanes.keys()),
            "entry_quality_cooldowns": entry_quality_cooldowns,
            "entry_quality_cooldown_keys": sorted(entry_quality_cooldowns.keys()),
            "entry_quality_cooldown_min_samples": entry_quality_cooldown_min_samples,
            "shadow_only_entry_qualities": shadow_only_entry_qualities,
            "shadow_only_entry_quality_keys": sorted(
                shadow_only_entry_qualities.keys()
            ),
            "cooldown_symbols": cooldown_symbols,
            "cooldown_symbol_keys": sorted(cooldown_symbols.keys()),
            "waiting_entry_policy": {
                "enabled": True,
                "mode": "small_exploratory_after_losses",
                "max_new_waiting_blocks_per_run": 1,
                "min_confidence": round(base_confidence, 4),
                "min_expected_r": round(base_expected_r, 4),
                "min_directional_score": round(base_directional_score, 4),
                "requires_price_trigger": True,
                "requires_executable_entry_target_stop": True,
                "intent": (
                    "When recent performance is weak, prefer one small waiting-entry "
                    "block with a concrete trigger over a blanket freeze."
                ),
            },
            "mutable_by": [
                "block_performance_reflections",
                "crypto_quant_outcomes",
                "binance memory policy scorecards",
            ],
        }

    def _augment_entry_gate_policy_candidate_symbol_cooldowns(
        self,
        policy: dict[str, Any],
        *,
        symbols: list[str],
    ) -> dict[str, Any]:
        if not isinstance(policy, dict):
            return policy
        unique_symbols = [
            symbol
            for symbol in dict.fromkeys(
                str(raw or "").upper().strip() for raw in symbols
            )
            if symbol
        ]
        if not unique_symbols:
            return policy
        cooldowns = (
            dict(policy.get("cooldown_symbols"))
            if isinstance(policy.get("cooldown_symbols"), dict)
            else {}
        )
        min_samples = max(
            min(_safe_int(self.config.symbol_lane_cooldown_min_samples), 5),
            1,
        )
        max_win_rate = float(self.config.symbol_lane_cooldown_max_win_rate_pct)
        added = 0
        for symbol in unique_symbols:
            if symbol in cooldowns:
                continue
            try:
                card = self.repository.performance_scorecard_for_symbol(
                    symbol,
                    limit=self._performance_scorecard_feedback_limit(),
                )
            except Exception:
                logger.warning(
                    "failed to load Binance candidate symbol cooldown performance",
                    exc_info=True,
                )
                continue
            if not card or not self._symbol_card_requires_cooldown(
                card,
                min_samples=min_samples,
                max_win_rate_pct=max_win_rate,
            ):
                continue
            cooldowns[symbol] = {
                "status": "cooldown",
                "sample_count": _safe_int(card.get("sample_count")),
                "pnl_usdt": _safe_float(card.get("pnl_usdt")),
                "win_rate_pct": _safe_float(card.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(card.get("avg_r_multiple")),
                "profit_factor": _safe_float(card.get("profit_factor")),
                "recovery_factor": _safe_float(card.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(
                    card.get("max_drawdown_r_multiple")
                ),
                "source": "candidate_symbol_lookup",
                "instruction": (
                    "Avoid new real blocks for this candidate symbol until fresh, "
                    "stronger edge offsets recent realized cost drag."
                ),
            }
            added += 1
        if added <= 0:
            return policy
        augmented = dict(policy)
        augmented["cooldown_symbols"] = cooldowns
        augmented["cooldown_symbol_keys"] = sorted(cooldowns.keys())
        augmented["candidate_symbol_cooldown_lookup"] = {
            "status": "augmented",
            "checked_count": len(unique_symbols),
            "added_count": added,
            "window_limit": self._performance_scorecard_feedback_limit(),
        }
        return augmented

    @staticmethod
    def _entry_gate_lane_scorecards(performance: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows: list[Any] = []
        side_rows = performance.get("side_scorecards")
        lane_rows = performance.get("lane_scorecards")
        if isinstance(side_rows, list):
            rows.extend(side_rows)
        if isinstance(lane_rows, list):
            rows.extend(lane_rows)
        cards: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            lane = str(row.get("lane") or row.get("side") or "").strip().lower()
            if not lane:
                continue
            cards[lane] = {
                "sample_count": _safe_int(row.get("sample_count")),
                "pnl_usdt": _safe_float(row.get("pnl_usdt")),
                "avg_r_multiple": _safe_float(row.get("avg_r_multiple")),
                "win_rate_pct": _safe_float(row.get("win_rate_pct")),
                "profit_factor": _safe_float(row.get("profit_factor")),
                "recovery_factor": _safe_float(row.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(row.get("max_drawdown_r_multiple")),
            }
        return cards

    @staticmethod
    def _lane_card_requires_cooldown(
        lane_card: dict[str, Any],
        *,
        min_samples: int,
    ) -> bool:
        sample_count = _safe_int(lane_card.get("sample_count"))
        if sample_count < max(min_samples, 1):
            return False
        pnl = _safe_float(lane_card.get("pnl_usdt"))
        win_rate = _safe_float(lane_card.get("win_rate_pct"))
        avg_r = _safe_float(lane_card.get("avg_r_multiple"))
        profit_factor = _safe_float(lane_card.get("profit_factor"))
        recovery_factor = _safe_float(lane_card.get("recovery_factor"))
        max_drawdown_r = _safe_float(lane_card.get("max_drawdown_r_multiple"))
        return (
            pnl < 0
            and profit_factor < 1.0
            and (
                win_rate < 35.0
                or avg_r < 0.0
                or recovery_factor <= 0.0
                or max_drawdown_r <= -3.0
            )
        )

    @staticmethod
    def _lane_card_requires_broad_cooldown(
        lane_card: dict[str, Any],
        *,
        min_samples: int,
    ) -> bool:
        sample_count = _safe_int(lane_card.get("sample_count"))
        severe_min_samples = max(min(max(int(min_samples), 1), 5) * 3, 12)
        if sample_count < severe_min_samples:
            return False
        pnl = _safe_float(lane_card.get("pnl_usdt"))
        win_rate = _safe_float(lane_card.get("win_rate_pct"))
        avg_r = _safe_float(lane_card.get("avg_r_multiple"))
        profit_factor = _safe_float(lane_card.get("profit_factor"))
        recovery_factor = _safe_float(lane_card.get("recovery_factor"))
        max_drawdown_r = _safe_float(lane_card.get("max_drawdown_r_multiple"))
        return (
            pnl < 0
            and profit_factor < 0.8
            and recovery_factor <= 0.0
            and max_drawdown_r <= -5.0
            and (avg_r < 0.05 or win_rate <= 55.0)
        )

    @staticmethod
    def _lane_card_shows_cooldown_recovery(
        lane_card: dict[str, Any],
        *,
        min_samples: int,
    ) -> bool:
        sample_count = _safe_int(lane_card.get("sample_count"))
        if sample_count < max(min_samples, 1):
            return False
        pnl = _safe_float(lane_card.get("pnl_usdt"))
        profit_factor = _safe_float(lane_card.get("profit_factor"))
        win_rate = _safe_float(lane_card.get("win_rate_pct"))
        avg_r = _safe_float(lane_card.get("avg_r_multiple"))
        if pnl < 0 or profit_factor < 1.0:
            return False
        return win_rate >= 45.0 or avg_r >= 0.0

    @classmethod
    def _entry_gate_cooldown_lanes(
        cls,
        performance: dict[str, Any],
        *,
        min_samples: int,
    ) -> dict[str, dict[str, Any]]:
        cooldowns: dict[str, dict[str, Any]] = {}
        weak_side_rows: list[tuple[str, dict[str, Any]]] = []
        side_rows = performance.get("side_scorecards")
        if isinstance(side_rows, list):
            for row in side_rows:
                if not isinstance(row, dict):
                    continue
                lane = str(row.get("side") or "").strip().lower()
                if not lane:
                    continue
                if not cls._lane_card_requires_cooldown(row, min_samples=min_samples):
                    continue
                weak_side_rows.append((lane, row))

        weak_horizons: list[str] = []
        weak_explicit_lanes: list[tuple[str, dict[str, Any]]] = []
        lane_rows = performance.get("lane_scorecards")
        if isinstance(lane_rows, list):
            for row in lane_rows:
                if not isinstance(row, dict):
                    continue
                lane = str(row.get("lane") or "").strip().lower()
                if not lane or lane == "futures":
                    continue
                if not cls._lane_card_requires_cooldown(row, min_samples=min_samples):
                    continue
                if lane in {"short", "mid", "long"}:
                    if lane not in weak_horizons:
                        weak_horizons.append(lane)
                    continue
                weak_explicit_lanes.append((lane, row))

        def cooldown_payload(row: dict[str, Any], *, scope: str = "lane") -> dict[str, Any]:
            payload = {
                "status": "cooldown",
                "scope": scope,
                "sample_count": _safe_int(row.get("sample_count")),
                "pnl_usdt": _safe_float(row.get("pnl_usdt")),
                "win_rate_pct": _safe_float(row.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(row.get("avg_r_multiple")),
                "profit_factor": _safe_float(row.get("profit_factor")),
                "recovery_factor": _safe_float(row.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(row.get("max_drawdown_r_multiple")),
                "instruction": "Do not add another real block to this lane until fresh evidence improves.",
            }
            if scope == "broad":
                payload["instruction"] = (
                    "Severe live drag detected for the whole market:side lane. "
                    "Do not add another real block to this side until recovery "
                    "evidence improves across closed-block outcomes."
                )
            return payload

        for lane, row in weak_side_rows:
            market = lane.split(":", 1)[0]
            if market in {"spot", UPBIT_SPOT_MARKET} and weak_horizons:
                if cls._lane_card_requires_broad_cooldown(row, min_samples=min_samples):
                    cooldowns[lane] = cooldown_payload(row, scope="broad")
                for horizon in weak_horizons:
                    cooldowns[f"{lane}:{horizon}"] = cooldown_payload(row)
                continue
            cooldowns[lane] = cooldown_payload(row)

        for lane, row in weak_explicit_lanes:
            cooldowns[lane] = cooldown_payload(row)

        return cooldowns

    @classmethod
    def _entry_gate_cooldown_entry_qualities(
        cls,
        performance: dict[str, Any],
        *,
        min_samples: int,
    ) -> dict[str, dict[str, Any]]:
        rows = performance.get("entry_quality_scorecards")
        if not isinstance(rows, list):
            return {}
        cooldowns: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("entry_quality_lane") or "").strip().lower()
            if not key:
                continue
            if not cls._lane_card_requires_cooldown(row, min_samples=min_samples):
                continue
            cooldowns[key] = {
                "status": "cooldown",
                "sample_count": _safe_int(row.get("sample_count")),
                "min_samples": max(int(min_samples), 1),
                "pnl_usdt": _safe_float(row.get("pnl_usdt")),
                "win_rate_pct": _safe_float(row.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(row.get("avg_r_multiple")),
                "profit_factor": _safe_float(row.get("profit_factor")),
                "recovery_factor": _safe_float(row.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(row.get("max_drawdown_r_multiple")),
                "recovery_required": (
                    "Do not repeat this market:side:entry-quality structure until "
                    "fresh evidence, cleaner live book/quant confirmation, or a "
                    "different executable entry structure offsets the realized losses."
                ),
                "instruction": (
                    "Prefer an alternate entry_quality, a smaller waiting-entry probe "
                    "with stronger confluence, or no new block."
                ),
            }
        return cooldowns

    @staticmethod
    def _symbol_card_requires_cooldown(
        symbol_card: dict[str, Any],
        *,
        min_samples: int,
        max_win_rate_pct: float,
    ) -> bool:
        sample_count = _safe_int(symbol_card.get("sample_count"))
        if sample_count < max(min_samples, 1):
            return False
        pnl = _safe_float(symbol_card.get("pnl_usdt"))
        profit_factor = _safe_float(symbol_card.get("profit_factor"))
        win_rate = _safe_float(symbol_card.get("win_rate_pct"))
        avg_r = _safe_float(symbol_card.get("avg_r_multiple"))
        recovery_factor = _safe_float(symbol_card.get("recovery_factor"))
        max_drawdown_r = _safe_float(symbol_card.get("max_drawdown_r_multiple"))
        return (
            pnl < 0
            and profit_factor < 1.0
            and (
                win_rate <= max_win_rate_pct
                or avg_r < 0.0
                or recovery_factor <= 0.0
                or max_drawdown_r <= -2.5
            )
        )

    @classmethod
    def _entry_gate_cooldown_symbols(
        cls,
        performance: dict[str, Any],
        *,
        min_samples: int,
        max_win_rate_pct: float,
    ) -> dict[str, dict[str, Any]]:
        rows = performance.get("symbol_scorecards")
        if not isinstance(rows, list):
            return {}
        cooldowns: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            if not cls._symbol_card_requires_cooldown(
                row,
                min_samples=min_samples,
                max_win_rate_pct=max_win_rate_pct,
            ):
                continue
            cooldowns[symbol] = {
                "status": "cooldown",
                "sample_count": _safe_int(row.get("sample_count")),
                "pnl_usdt": _safe_float(row.get("pnl_usdt")),
                "win_rate_pct": _safe_float(row.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(row.get("avg_r_multiple")),
                "profit_factor": _safe_float(row.get("profit_factor")),
                "recovery_factor": _safe_float(row.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(
                    row.get("max_drawdown_r_multiple")
                ),
                "instruction": (
                    "Avoid new real blocks for this symbol until a fresh, "
                    "stronger edge offsets recent realized cost drag."
                ),
            }
        return cooldowns

    @classmethod
    def _manager_candidate_index(
        cls,
        candidates: list[dict[str, Any]],
    ) -> dict[tuple[str, str, str, str], dict[str, Any]]:
        index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in candidates:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            market = normalize_market(row.get("market") or row.get("venue"))
            side = normalize_position_side(row.get("side") or row.get("stance"))
            horizon = normalize_binance_horizon(row.get("horizon"), market=market)
            for key in (
                (symbol, market, side, horizon),
                (symbol, market, side, ""),
                (symbol, market, "", horizon),
                (symbol, market, "", ""),
            ):
                index.setdefault(key, row)
        return index

    def _manager_entry_gate(self, row: dict[str, Any]) -> dict[str, Any]:
        policy = self._last_manager_entry_gate_policy or self._entry_gate_policy(
            performance=self.repository.latest_performance_scorecard(limit=20),
            memory_context={},
        )
        symbol = str(row.get("symbol") or "").upper().strip()
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        horizon = normalize_binance_horizon(row.get("horizon"), market=market)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        lane = normalize_binance_display_lane(
            lane=row.get("lane") or metadata.get("lane") or calculated.get("lane"),
            market=market,
            horizon=horizon,
            side=side,
        )
        waiting_entry = self._is_waiting_entry_payload(row)
        effective_policy = self._entry_gate_effective_policy(
            policy,
            market=market,
            side=side,
            lane=lane,
            waiting_entry=waiting_entry,
        )
        reasons: list[str] = []
        diagnostics: dict[str, Any] = {
            "symbol": symbol,
            "market": market,
            "side": side,
            "horizon": horizon,
            "lane": lane,
            "policy": policy,
            "effective_policy": effective_policy,
            "waiting_entry": waiting_entry,
        }
        if str(effective_policy.get("effective_adjustment") or "") in {
            "waiting_entry_uses_base_gate",
            "spot_waiting_entry_exploration",
        }:
            diagnostics["waiting_entry_relaxed"] = True

        entry_quality_check = build_entry_quality_gate_check(
            row,
            waiting_entry=waiting_entry,
        )
        if entry_quality_check.get("has_signal"):
            diagnostics["entry_quality"] = entry_quality_check
        reasons.extend(entry_quality_check.get("reasons") or [])

        candidate = self._entry_gate_candidate(
            symbol=symbol,
            market=market,
            side=side,
            horizon=horizon,
        )
        if candidate:
            candidate_check = self._entry_gate_candidate_check(candidate, row, effective_policy)
            diagnostics["candidate"] = candidate_check
            reasons.extend(candidate_check.get("reasons") or [])

        quant = self._entry_gate_quant_signal(
            symbol=symbol,
            side=side,
            horizon=horizon,
        )
        if quant:
            quant_check = self._entry_gate_quant_check(quant, side, effective_policy)
            diagnostics["quant"] = quant_check
            reasons.extend(quant_check.get("reasons") or [])

        diagnostics["ok"] = not reasons
        diagnostics["reasons"] = reasons
        return diagnostics

    def _apply_explicit_policy_effects_to_create_row(
        self,
        row: dict[str, Any],
        impacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        adjustments: list[dict[str, Any]] = []
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for impact, effect in build_policy_effects(impacts):
            rule_id = str(impact.get("rule_id") or impact.get("policy_id") or "")
            if build_policy_effect_waiting_required(effect):
                reference_price = build_policy_reference_entry_price(row)
                trigger_price = build_policy_effect_trigger_price(effect)
                trigger_effect_key = "entry_trigger_price"
                derived_operator = ""
                if trigger_price <= 0:
                    trigger_price, trigger_effect_key, derived_operator = build_policy_effect_derived_trigger_price(
                        effect,
                        reference_entry_price=reference_price,
                        side=str(row.get("side") or "long"),
                    )
                if trigger_price <= 0 and reference_price > 0:
                    trigger_price = reference_price
                    trigger_effect_key = "reference_entry_price"
                    derived_operator = (
                        ">=" if normalize_position_side(row.get("side")) == "short" else "<="
                    )
                if trigger_price > 0:
                    original_style = str(
                        row.get("entry_style") or metadata.get("entry_style") or "immediate"
                    )
                    original_trigger_price = _safe_float(row.get("entry_trigger_price"))
                    row["entry_style"] = "wait_for_price"
                    row["entry_trigger_price"] = trigger_price
                    row["entry_trigger_operator"] = build_normalize_entry_trigger_operator(
                        effect.get("entry_trigger_operator")
                        or effect.get("trigger_operator"),
                        default=derived_operator or "<=",
                    )
                    adjustments.append(
                        {
                            "rule_id": rule_id,
                            "field": "entry_style",
                            "from": original_style,
                            "to": "wait_for_price",
                            "entry_trigger_price": trigger_price,
                            "entry_trigger_price_from": original_trigger_price,
                            "method": "derived_price"
                            if trigger_effect_key in POLICY_ENTRY_TRIGGER_PCT_KEYS
                            else "explicit_price",
                            "effect_key": trigger_effect_key,
                        }
                    )
                elif not self._is_waiting_entry_payload(row):
                    return {
                        "version": "policy_effect_enforcement_v1",
                        "rejected": True,
                        "reason": "policy_requires_waiting_entry",
                        "rule_id": rule_id,
                        "adjustments": adjustments,
                    }

            original_qty = _safe_float(row.get("qty") or row.get("qty_initial"))
            adjusted_qty = build_policy_effect_qty_adjusted(original_qty, effect)
            if adjusted_qty > 0 and original_qty > adjusted_qty:
                row["qty"] = adjusted_qty
                row["qty_initial"] = adjusted_qty
                row["policy_adjusted_qty_from"] = original_qty
                adjustments.append(
                    {
                        "rule_id": rule_id,
                        "field": "qty",
                        "from": original_qty,
                        "to": adjusted_qty,
                    }
                )

            build_apply_policy_relative_price_effects(
                row,
                effect,
                rule_id=rule_id,
                adjustments=adjustments,
            )

            for effect_key, row_key in (
                ("target_price", "target_price"),
                ("stop_price", "stop_price"),
            ):
                price = _safe_float(effect.get(effect_key))
                if price <= 0:
                    continue
                original_price = _safe_float(row.get(row_key))
                if original_price == price:
                    continue
                row[row_key] = price
                adjustments.append(
                    {
                        "rule_id": rule_id,
                        "field": row_key,
                        "from": original_price,
                        "to": price,
                    }
                )

        quality_gate = build_policy_target_stop_quality_gate(row, impacts)
        if quality_gate.get("rejected"):
            return {
                "version": "policy_effect_enforcement_v1",
                "rejected": True,
                "reason": str(quality_gate.get("reason") or "policy_target_stop_rejected"),
                "rule_id": str(quality_gate.get("rule_id") or ""),
                "adjustments": adjustments,
                "checks": quality_gate.get("checks") or [],
            }

        return {
            "version": "policy_effect_enforcement_v1",
            "rejected": False,
            "adjustments": adjustments,
            "checks": quality_gate.get("checks") or [],
        }

    def _entry_gate_effective_policy(
        self,
        policy: dict[str, Any],
        *,
        market: str,
        side: str,
        lane: str = "",
        waiting_entry: bool,
    ) -> dict[str, Any]:
        base_policy = {
            "min_confidence": round(
                _safe_float(policy.get("base_min_confidence"))
                or max(min(float(self.config.min_entry_confidence), 1.0), 0.0),
                4,
            ),
            "min_expected_r": round(
                _safe_float(policy.get("base_min_expected_r"))
                or max(float(self.config.min_entry_expected_r), 0.0),
                4,
            ),
            "min_directional_score": round(
                _safe_float(policy.get("base_min_directional_score"))
                or max(float(self.config.min_entry_directional_score), 0.0),
                4,
            ),
            "min_direction_margin": round(_safe_float(policy.get("min_direction_margin")) or 8.0, 4),
        }
        adjustment = str(policy.get("adjustment") or "")
        lane_key = str(lane or "").strip().lower()
        if lane_key in {"short", "mid", "long", "futures"}:
            lane_key = f"{market}:{side}"
        if not lane_key:
            lane_key = f"{market}:{side}"
        lane_scorecards = policy.get("lane_scorecards")
        lane_card = (
            lane_scorecards.get(lane_key)
            if isinstance(lane_scorecards, dict)
            and isinstance(lane_scorecards.get(lane_key), dict)
            else None
        )
        if market == "spot" and side == "long" and waiting_entry:
            sample_count = _safe_int(lane_card.get("sample_count")) if lane_card else 0
            win_rate = _safe_float(lane_card.get("win_rate_pct")) if lane_card else 0.0
            avg_r = _safe_float(lane_card.get("avg_r_multiple")) if lane_card else 0.0
            hard_cooldown = sample_count >= 8 and win_rate < 30.0 and avg_r < -0.15
            if not hard_cooldown:
                return {
                    **policy,
                    **base_policy,
                    "min_expected_r": round(max(base_policy["min_expected_r"] - 0.15, 0.35), 4),
                    "min_directional_score": round(
                        max(base_policy["min_directional_score"] - 7.0, 55.0),
                        4,
                    ),
                    "min_direction_margin": 4.0,
                    "effective_adjustment": "spot_waiting_entry_exploration",
                    "matched_lane": lane_key,
                    "matched_lane_scorecard": lane_card or {},
                    "spot_exploration": True,
                }
        if lane_key == "volatile_attack" and lane_card:
            sample_count = _safe_int(lane_card.get("sample_count"))
            win_rate = _safe_float(lane_card.get("win_rate_pct"))
            avg_r = _safe_float(lane_card.get("avg_r_multiple"))
            pnl = _safe_float(lane_card.get("pnl_usdt"))
            if sample_count >= max(_safe_int(self.config.lane_performance_min_samples), 1) and (
                pnl < 0 or win_rate < 35.0 or avg_r < 0
            ):
                return {
                    **policy,
                    "min_confidence": round(min(_safe_float(policy.get("min_confidence")) + 0.05, 0.82), 4),
                    "min_expected_r": round(_safe_float(policy.get("min_expected_r")) + 0.15, 4),
                    "min_directional_score": round(
                        min(_safe_float(policy.get("min_directional_score")) + 6.0, 82.0),
                        4,
                    ),
                    "effective_adjustment": "tightened_by_volatile_attack_lane",
                    "matched_lane": lane_key,
                    "matched_lane_scorecard": lane_card,
                }
        if waiting_entry and adjustment.startswith("tightened_by_"):
            return {
                **policy,
                **base_policy,
                "effective_adjustment": "waiting_entry_uses_base_gate",
            }
        if adjustment != "tightened_by_recent_reflections":
            return policy

        if not lane_card:
            return {
                **policy,
                **base_policy,
                "effective_adjustment": "base_gate_for_fresh_lane",
                "matched_lane": lane_key,
            }
        sample_count = _safe_int(lane_card.get("sample_count"))
        win_rate = _safe_float(lane_card.get("win_rate_pct"))
        avg_r = _safe_float(lane_card.get("avg_r_multiple"))
        if sample_count >= 3 and (win_rate < 35.0 or avg_r < 0):
            return {
                **policy,
                "effective_adjustment": "tightened_by_matching_lane",
                "matched_lane": lane_key,
                "matched_lane_scorecard": lane_card,
            }
        return {
            **policy,
            **base_policy,
            "effective_adjustment": "base_gate_for_healthy_lane",
            "matched_lane": lane_key,
            "matched_lane_scorecard": lane_card,
        }

    def _entry_gate_candidate(
        self,
        *,
        symbol: str,
        market: str,
        side: str,
        horizon: str,
    ) -> dict[str, Any]:
        for key in (
            (symbol, market, side, horizon),
            (symbol, market, side, ""),
            (symbol, market, "", horizon),
            (symbol, market, "", ""),
        ):
            candidate = self._last_manager_candidate_index.get(key)
            if isinstance(candidate, dict):
                return candidate
        return {}

    @staticmethod
    def _entry_gate_candidate_check(
        candidate: dict[str, Any],
        row: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        raw_stance = str(
            candidate.get("stance")
            or candidate.get("side")
            or candidate.get("direction")
            or ""
        ).strip().lower()
        candidate_confidence = _safe_float(candidate.get("confidence"))
        if candidate_confidence > 1.0:
            candidate_confidence = candidate_confidence / 100.0
        manager_confidence = _safe_float(row.get("confidence"))
        if manager_confidence > 1.0:
            manager_confidence = manager_confidence / 100.0
        confidence = candidate_confidence
        confidence_source = "candidate"
        min_confidence = _safe_float(policy.get("min_confidence"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        waiting_entry = str(
            row.get("entry_style") or metadata.get("entry_style") or ""
        ).strip().lower() in {"wait_for_price", "waiting_entry", "triggered_entry"}
        if not waiting_entry:
            waiting_entry = _safe_float(
                row.get("entry_trigger_price")
                or row.get("trigger_price")
                or metadata.get("entry_trigger_price")
            ) > 0
        has_output_evidence = bool(row.get("evidence_refs")) or bool(
            row.get("conviction_upgrade_reason")
        )
        if waiting_entry and has_output_evidence and manager_confidence > confidence:
            confidence = manager_confidence
            confidence_source = "manager_output"
        explicit_upgrade = bool(
            row.get("conviction_upgrade_reason")
            or row.get("override_reason")
            or row.get("jue_price_override")
        )
        hold_like = any(
            token in raw_stance
            for token in ("hold", "avoid", "neutral", "no_trade", "notrade")
        )
        watch_like = "watch" in raw_stance
        if hold_like and not explicit_upgrade:
            reasons.append("candidate_stance_is_hold_without_upgrade")
        if confidence > 0 and confidence < min_confidence:
            reasons.append("candidate_confidence_too_low")
        if watch_like and confidence > 0 and confidence < min_confidence:
            reasons.append("watch_candidate_below_confidence_gate")
        return {
            "stance": raw_stance,
            "confidence": confidence,
            "candidate_confidence": candidate_confidence,
            "manager_confidence": manager_confidence,
            "confidence_source": confidence_source,
            "min_confidence": min_confidence,
            "explicit_upgrade": explicit_upgrade,
            "reasons": reasons,
        }

    def _entry_gate_quant_signal(
        self,
        *,
        symbol: str,
        side: str,
        horizon: str,
    ) -> dict[str, Any]:
        quant_context = self._last_manager_quant_context if isinstance(self._last_manager_quant_context, dict) else {}
        items = quant_context.get("items") if isinstance(quant_context.get("items"), list) else []
        preferred_horizons = [
            horizon,
            "intraday" if horizon == "short" else "",
            "scalp" if horizon == "short" else "",
            "swing" if horizon in {"mid", "long"} else "",
        ]
        clean_horizons = [value for value in dict.fromkeys(preferred_horizons) if value]
        fallback: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol") or "").upper().strip() != symbol:
                continue
            if not fallback:
                fallback = item
            item_horizon = str(item.get("horizon") or "").strip().lower()
            if item_horizon in clean_horizons:
                return item
        _ = side
        return fallback

    @staticmethod
    def _entry_gate_quant_check(
        quant: dict[str, Any],
        side: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        signal = quant.get("signal") if isinstance(quant.get("signal"), dict) else {}
        long_score = _safe_float(quant.get("long_score") or signal.get("long_score"))
        short_score = _safe_float(quant.get("short_score") or signal.get("short_score"))
        no_trade_score = _safe_float(quant.get("no_trade_score") or signal.get("no_trade_score"))
        expected_r_long = _safe_float(
            quant.get("expected_r_long") or signal.get("expected_r_long")
        )
        expected_r_short = _safe_float(
            quant.get("expected_r_short") or signal.get("expected_r_short")
        )
        directional_score = long_score if side == "long" else short_score
        opposite_score = short_score if side == "long" else long_score
        expected_r = expected_r_long if side == "long" else expected_r_short
        min_expected_r = _safe_float(policy.get("min_expected_r"))
        min_directional_score = _safe_float(policy.get("min_directional_score"))
        min_direction_margin = _safe_float(policy.get("min_direction_margin")) or 8.0
        reasons: list[str] = []
        if expected_r < min_expected_r:
            reasons.append("quant_expected_r_too_low")
        if directional_score > 0 and directional_score < min_directional_score:
            reasons.append("quant_directional_score_too_low")
        if no_trade_score > 0 and no_trade_score >= directional_score:
            reasons.append("quant_no_trade_dominates")
        if (
            directional_score > 0
            and opposite_score > 0
            and directional_score - opposite_score < min_direction_margin
        ):
            reasons.append("quant_direction_margin_too_thin")
        return {
            "horizon": quant.get("horizon"),
            "side": side,
            "directional_score": directional_score,
            "opposite_score": opposite_score,
            "no_trade_score": no_trade_score,
            "expected_r": expected_r,
            "min_expected_r": min_expected_r,
            "min_directional_score": min_directional_score,
            "min_direction_margin": min_direction_margin,
            "reasons": reasons,
        }

    @staticmethod
    def _futures_position_amt(row: dict[str, Any]) -> float:
        return build_first_float(
            row,
            (
                "position_amt",
                "positionAmt",
                "position_amount",
                "qty",
                "quantity",
            ),
        )

    @staticmethod
    def _futures_position_side(row: dict[str, Any]) -> str:
        amount = BinanceBlockTrader._futures_position_amt(row)
        if amount < 0:
            return "short"
        return "long"

    def _allocated_futures_qty_by_symbol(self, *, side: str) -> dict[str, float]:
        allocated: dict[str, float] = {}
        normalized_side = normalize_position_side(side)
        for block in self.repository.list_blocks(include_closed=False):
            if normalize_market(block.get("market")) != "futures":
                continue
            if normalize_position_side(block.get("side")) != normalized_side:
                continue
            if str(block.get("status") or "") not in BINANCE_ALLOCATION_STATUSES:
                continue
            symbol = str(block.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            qty = _safe_float(
                block.get("qty_open")
                or block.get("qty_initial")
                or block.get("qty")
            )
            allocated[symbol] = allocated.get(symbol, 0.0) + max(qty, 0.0)
        return allocated

    @staticmethod
    def _futures_position_for_symbol(
        account: dict[str, Any],
        *,
        symbol: str,
    ) -> dict[str, Any] | None:
        target = str(symbol or "").upper().strip()
        for row in account.get("futures_position_risk") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper().strip() != target:
                continue
            if abs(BinanceBlockTrader._futures_position_amt(row)) <= 0:
                continue
            return row
        return None

    async def _apply_manager_adopt_existing_block(
        self,
        row: dict[str, Any],
        *,
        manager_run_id: int,
        allocated_by_market: dict[str, dict[str, float]],
    ) -> dict[str, Any]:
        market = normalize_market(row.get("market") or row.get("venue") or "spot")
        side = normalize_position_side(row.get("side"))
        if market not in {"spot", "futures", UPBIT_SPOT_MARKET}:
            return build_rejected_manager_action(
                f"unsupported_existing_position_adoption_market:{market}",
                input_row=row,
            )

        symbol = str(row.get("symbol") or "").upper().strip()
        if market == UPBIT_SPOT_MARKET:
            symbol = upbit_market_symbol(symbol)
        if not symbol:
            return build_rejected_manager_action(
                "existing_position_adoption_missing_symbol",
                input_row=row,
            )

        account = self._last_account_snapshot
        if not account:
            account = build_normalize_account_snapshot(
                await self._collect_account_snapshot(),
                default_upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            )
            self._last_account_snapshot = account
        asset: dict[str, Any] | None = None
        position_amt = 0.0
        if market == "futures":
            asset = self._futures_position_for_symbol(account, symbol=symbol)
            if not asset:
                return build_rejected_manager_action(
                    "existing_futures_position_not_found_in_account",
                    input_row=row,
                    symbol=symbol,
                    market=market,
                )
            position_amt = self._futures_position_amt(asset)
            position_side = self._futures_position_side(asset)
            if side != position_side:
                return build_rejected_manager_action(
                    "existing_futures_position_side_mismatch",
                    input_row=row,
                    symbol=symbol,
                    market=market,
                    requested_side=side,
                    position_side=position_side,
                )
            allocation_key = f"{market}:{side}"
            if allocation_key not in allocated_by_market:
                allocated_by_market[allocation_key] = (
                    self._allocated_futures_qty_by_symbol(side=side)
                )
            allocated = allocated_by_market[allocation_key]
            qty = abs(position_amt)
            available = qty
            locked = 0.0
            allocatable_qty = qty
        else:
            if side != "long":
                return build_rejected_manager_action(
                    "existing_position_adoption_requires_long_side",
                    input_row=row,
                )
            assets = build_position_assets_for_market(account, market=market)
            asset = next(
                (
                    item
                    for item in assets
                    if str(item.get("symbol") or "").upper().strip() == symbol
                ),
                None,
            )
            if not asset:
                return build_rejected_manager_action(
                    "existing_position_not_found_in_account",
                    input_row=row,
                    symbol=symbol,
                    market=market,
                )
            if market not in allocated_by_market:
                allocated_by_market[market] = build_allocated_qty_by_symbol(
                    self.repository.list_blocks(include_closed=False),
                    market=market,
                )
            allocated = allocated_by_market[market]
            qty = _safe_float(asset.get("qty"))
            available = _safe_float(asset.get("available"))
            locked = _safe_float(asset.get("locked"))
            allocatable_qty = available if available > 0 else max(qty - locked, 0.0)
        allocated_qty = allocated.get(symbol, 0.0)
        unassigned_qty = max(allocatable_qty - allocated_qty, 0.0)
        requested_qty = _safe_float(row.get("qty") or row.get("quantity"))
        if requested_qty <= 0:
            return build_rejected_manager_action(
                "existing_position_adoption_qty_required",
                input_row=row,
                symbol=symbol,
                market=market,
                unassigned_qty=unassigned_qty,
            )
        if requested_qty > unassigned_qty + 1e-12:
            return build_rejected_manager_action(
                "existing_position_adoption_exceeds_unassigned_qty",
                input_row=row,
                symbol=symbol,
                market=market,
                requested_qty=requested_qty,
                unassigned_qty=unassigned_qty,
                allocated_qty=allocated_qty,
            )

        quote = await self._fetch_quote(symbol=symbol, market=market)
        quote_price = _safe_float(quote.get("price"))
        if quote_price <= 0:
            return build_rejected_manager_action(
                "existing_position_adoption_quote_unavailable",
                input_row=row,
                symbol=symbol,
                market=market,
                error_message=quote.get("error_message") or "",
            )
        entry_price = (
            _safe_float(row.get("entry_price") or row.get("entry_price_usdt"))
            or build_first_float(asset, ("entry_price", "entryPrice", "avg_price"))
            or self._spot_adoption_entry_price(asset, quote_price=quote_price)
        )
        target_price = _safe_float(row.get("target_price") or row.get("target_price_usdt"))
        stop_price = _safe_float(row.get("stop_price") or row.get("stop_price_usdt"))
        if min(entry_price, target_price, stop_price) <= 0:
            return build_rejected_manager_action(
                "existing_position_adoption_requires_entry_target_stop",
                input_row=row,
                symbol=symbol,
                market=market,
            )

        self._runtime_market_universe.setdefault(market, [])
        self._runtime_market_universe[market] = parse_universe(
            ",".join([*self._runtime_market_universe[market], symbol])
        )
        metadata = dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {})
        metadata["adoption"] = {
            "source": "manager_existing_position",
            "asset": asset.get("asset"),
            "position_amt": position_amt if market == "futures" else None,
            "wallet_qty": qty,
            "available_qty": available,
            "locked_qty": locked,
            "allocated_qty_before": allocated_qty,
            "unassigned_qty_before": unassigned_qty,
            "adopted_qty": requested_qty,
            "quote_price": quote_price,
            "entry_price_source": (
                "manager"
                if _safe_float(row.get("entry_price") or row.get("entry_price_usdt")) > 0
                else "futures_position"
                if market == "futures"
                else asset.get("entry_price_source")
            ),
            "margin_type": str(
                row.get("margin_type")
                or asset.get("margin_type")
                or asset.get("marginType")
                or ""
            ).strip().lower(),
            "leverage": max(_safe_int(row.get("leverage") or asset.get("leverage") or 1), 1),
            "liquidation_price": _safe_float(
                row.get("liquidation_price")
                or row.get("liquidationPrice")
                or asset.get("liquidation_price")
                or asset.get("liquidationPrice")
            ),
            "adoption_note": _clean_text(row.get("adoption_note"), limit=600),
            "adopted_at": utc_now_iso(),
        }
        for key in (
            "jue_wiki_repair_pressure",
            "jue_wiki_repair_resolution",
            "jue_wiki_memory_card_quality",
            "jue_wiki_memory_card_cross_check",
            "period_memory_coverage_gap",
            "period_memory_override_reason",
        ):
            if row.get(key) not in (None, "", [], {}):
                metadata[key] = row.get(key)

        block = self.create_block(
            {
                **row,
                "symbol": symbol,
                "market": market,
                "side": side,
                "qty": requested_qty,
                "qty_open": requested_qty,
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_price": stop_price,
                "margin_type": (
                    str(
                        row.get("margin_type")
                        or asset.get("margin_type")
                        or asset.get("marginType")
                        or "isolated"
                    )
                    .strip()
                    .lower()
                ),
                "leverage": max(
                    _safe_int(row.get("leverage") or asset.get("leverage") or 1),
                    1,
                ),
                "liquidation_price": _safe_float(
                    row.get("liquidation_price")
                    or row.get("liquidationPrice")
                    or asset.get("liquidation_price")
                    or asset.get("liquidationPrice")
                ),
                "status": "open",
                "created_by": "existing_position",
                "manager_run_id": manager_run_id,
                "llm_reason": row.get("adoption_note")
                or row.get("thesis")
                or "manager_existing_position_adoption",
                "metadata": metadata,
            }
        )
        allocated[symbol] = allocated_qty + requested_qty
        self.repository.add_event(
            str(block["block_id"]),
            "adopted_existing_position",
            "manager adopted existing wallet position into block ledger",
            {
                "manager_run_id": manager_run_id,
                "symbol": symbol,
                "market": market,
                "qty": requested_qty,
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_price": stop_price,
            },
        )
        return {
            "status": "adopted",
            "block_id": block["block_id"],
            "symbol": symbol,
            "market": market,
            "qty": requested_qty,
            "entry_price": entry_price,
            "target_price": target_price,
            "stop_price": stop_price,
        }

    async def _apply_manager_actions(
        self,
        actions: dict[str, list[dict[str, Any]]],
        *,
        manager_run_id: int,
    ) -> dict[str, list[dict[str, Any]]]:
        applied = build_empty_manager_action_results()
        risk_guard = self._risk_guard_snapshot(self._last_account_snapshot)
        growth_governor = self._last_manager_growth_governor or self._growth_governor_context(
            growth_target=self._growth_target_snapshot(
                self._last_account_snapshot,
                persist=False,
            ),
            performance=self.repository.latest_performance_scorecard(limit=20),
            risk_guard=risk_guard,
            long_performance=self.repository.latest_performance_scorecard(
                limit=self._performance_scorecard_feedback_limit()
            ),
        )
        max_new_blocks = (
            _safe_int(growth_governor.get("max_new_blocks"))
            if "max_new_blocks" in growth_governor
            else 2
        )
        allocated_by_market: dict[str, dict[str, float]] = {}
        for row in actions.get("adopt_existing_blocks", []):
            try:
                applied["adopted"].append(
                    await self._apply_manager_adopt_existing_block(
                        row,
                        manager_run_id=manager_run_id,
                        allocated_by_market=allocated_by_market,
                    )
                )
            except ValueError as exc:
                applied["adopted"].append(
                    build_rejected_manager_action(str(exc), input_row=row)
                )
                continue
        governed_new_blocks = 0
        for row in actions.get("create_blocks", []):
            if not bool(risk_guard.get("allow_new_entries")):
                applied["created"].append(
                    build_rejected_manager_action(
                        "risk_guard_halt_new_entries",
                        input_row=row,
                        risk_guard=risk_guard,
                    )
                )
                continue
            try:
                market = normalize_market(row.get("market") or row.get("venue"))
                market_conflict = build_manager_market_horizon_conflict(row)
                if market_conflict is not None:
                    applied["created"].append(market_conflict)
                    continue
                policy_impacts = build_policy_impacts_for_row(
                    self._last_manager_policy_impacts,
                    row,
                )
                policy_enforcement: dict[str, Any] = {}
                if policy_impacts:
                    policy_enforcement = self._apply_explicit_policy_effects_to_create_row(
                        row,
                        policy_impacts,
                    )
                repair_enforcement = (
                    row.get("validation_repair_enforcement")
                    if isinstance(row.get("validation_repair_enforcement"), dict)
                    else {}
                )
                policy_repair_rejection = build_manager_create_policy_repair_rejection(
                    row,
                    policy_enforcement=policy_enforcement,
                    repair_enforcement=repair_enforcement,
                )
                if policy_repair_rejection is not None:
                    applied["created"].append(policy_repair_rejection)
                    continue
                row = self._apply_candidate_budget_cap_to_row(row)
                waiting_entry = self._is_waiting_entry_payload(row)
                live_authority_context = (
                    self._last_live_authority_context or self._live_authority_context()
                )
                live_authority_gate = self._live_authority_create_gate(
                    live_authority_context,
                    waiting_entry=waiting_entry,
                )
                if not bool(live_authority_gate.get("ok")):
                    applied["created"].append(
                        build_rejected_manager_action(
                            live_authority_gate.get("reason") or "live_authority_gate",
                            input_row=row,
                            live_authority_gate=live_authority_gate,
                        )
                    )
                    continue
                lane_authority_gate = self._live_authority_lane_gate(
                    live_authority_context,
                    row,
                    waiting_entry=waiting_entry,
                )
                if not bool(lane_authority_gate.get("ok")):
                    applied["created"].append(
                        build_rejected_manager_action(
                            lane_authority_gate.get("reason")
                            or "lane_authority_rejected",
                            input_row=row,
                            lane_authority_gate=lane_authority_gate,
                        )
                    )
                    continue
                lane_authority_gate = self._lane_authority_gate_with_recent_recovery_floor(
                    row,
                    lane_authority_gate,
                )
                row = self._apply_lane_authority_budget_to_row(
                    row,
                    lane_authority_gate,
                )
                near_duplicate = self._near_duplicate_block_rejection(row)
                if near_duplicate is not None:
                    applied["created"].append(near_duplicate)
                    continue
                wait_pullback_confirmation = (
                    build_wait_pullback_confirmation_rejection(row)
                )
                if wait_pullback_confirmation is not None:
                    applied["created"].append(wait_pullback_confirmation)
                    continue
                entry_quality_cooldown = (
                    self._entry_quality_performance_cooldown_rejection(row)
                )
                if entry_quality_cooldown is not None:
                    applied["created"].append(entry_quality_cooldown)
                    continue
                lane_performance_cooldown = self._lane_performance_cooldown_rejection(row)
                if lane_performance_cooldown is not None:
                    applied["created"].append(lane_performance_cooldown)
                    continue
                lane_cooldown = self._symbol_lane_cooldown_rejection(row)
                if lane_cooldown is not None:
                    applied["created"].append(lane_cooldown)
                    continue
                symbol_performance_cooldown = (
                    self._symbol_performance_cooldown_rejection(row)
                )
                if symbol_performance_cooldown is not None:
                    applied["created"].append(symbol_performance_cooldown)
                    continue
                entry_gate = self._manager_entry_gate(row)
                if not bool(entry_gate.get("ok")):
                    reason = ",".join(str(item) for item in entry_gate.get("reasons") or [])
                    applied["created"].append(
                        build_rejected_manager_action(
                            f"entry_gate_rejected:{reason or 'weak_evidence'}",
                            input_row=row,
                            gate=entry_gate,
                        )
                    )
                    continue
                cost_edge_gate = self._manager_cost_edge_gate(row)
                if not bool(cost_edge_gate.get("ok")):
                    applied["created"].append(
                        build_rejected_manager_action(
                            cost_edge_gate.get("reason") or "cost_edge_rejected",
                            input_row=row,
                            cost_edge_gate=cost_edge_gate,
                        )
                    )
                    continue
                growth_governor_applies = self._growth_governor_applies_to_row(
                    growth_governor,
                    row,
                )
                growth_governor_rejection = build_manager_growth_governor_create_rejection(
                    row,
                    applies=growth_governor_applies,
                    growth_governor=growth_governor,
                    growth_unlock=self._last_manager_growth_unlock,
                    governed_new_blocks=governed_new_blocks,
                    max_new_blocks=max_new_blocks,
                    waiting_entry=waiting_entry,
                )
                if growth_governor_rejection is not None:
                    applied["created"].append(growth_governor_rejection)
                    continue
                live_entry = self._execution_enabled(market, for_entry=True)
                metadata = build_manager_create_block_metadata(
                    row,
                    entry_gate=entry_gate,
                    live_authority_gate=live_authority_gate,
                    lane_authority_gate=lane_authority_gate,
                    cost_edge_gate=cost_edge_gate,
                    growth_governor=growth_governor,
                    growth_governor_applies=growth_governor_applies,
                    policy_impacts=policy_impacts,
                    policy_enforcement=policy_enforcement,
                )
                payload = {
                    **row,
                    "metadata": metadata,
                    "manager_run_id": manager_run_id,
                    "created_by": "llm",
                    "status": (
                        "proposed"
                        if waiting_entry
                        else "entry_pending"
                        if live_entry
                        else row.get("status") or "proposed"
                    ),
                }
                normalized = self._prepare_new_block_payload(payload)
                self._validate_block(normalized)
                preflight = await self._exchange_min_order_preflight_rejection(normalized)
                if preflight is not None:
                    applied["created"].append(preflight)
                    continue
                block = self.repository.create_block(normalized)
                validation_repair_payload = (
                    metadata.get("validation_repair")
                    if isinstance(metadata.get("validation_repair"), dict)
                    else {}
                )
                if validation_repair_payload:
                    self.repository.add_event(
                        str(block["block_id"]),
                        "validation_repair_applied",
                        "19-test validation repair constraints applied during block entry",
                        {
                            "manager_run_id": manager_run_id,
                            "validation_repair": validation_repair_payload,
                        },
                    )
            except ValueError as exc:
                applied["created"].append(
                    build_rejected_manager_action(str(exc), input_row=row)
                )
                continue
            if self._growth_governor_applies_to_row(growth_governor, row):
                governed_new_blocks += 1
            if live_entry and not waiting_entry:
                entry = await self._submit_entry_for_block(block)
                applied["created"].append(
                    build_manager_created_block_result(
                        block,
                        live_entry=live_entry,
                        waiting_entry=waiting_entry,
                        entry=entry,
                    )
                )
                continue
            applied["created"].append(
                build_manager_created_block_result(
                    block,
                    live_entry=live_entry,
                    waiting_entry=waiting_entry,
                )
            )
        for row in actions.get("update_blocks", []):
            block_id = str(row.get("block_id") or "")
            if not block_id:
                continue
            block = self.repository.get_block(block_id)
            if not block:
                continue
            fields = build_manager_update_fields(row, block)
            if not fields:
                applied["updated"].append(
                    build_rejected_manager_action(
                        "no_allowed_fields",
                        block_id=block_id,
                    )
                )
                continue
            try:
                self._validate_price_direction({**block, **fields})
            except ValueError as exc:
                applied["updated"].append(
                    build_rejected_manager_action(str(exc), block_id=block_id)
                )
                continue
            updated = self.repository.update_block(block_id, fields)
            if updated:
                applied["updated"].append(
                    build_manager_block_action_result("updated", block_id)
                )
        for row in actions.get("close_blocks", []):
            block_id = str(row.get("block_id") or "")
            if not block_id:
                continue
            block = self.repository.get_block(block_id)
            if not block:
                continue
            if str(block.get("status") or "") in {"open", "entry_pending"}:
                if not build_manager_close_has_adverse_evidence(row, block):
                    self.repository.add_event(
                        block_id,
                        "manager_close_rejected",
                        "manager close rejected: missing adverse evidence",
                        {
                            "reason": row.get("reason", "manager_close_requested"),
                            "status": block.get("status"),
                            "symbol": block.get("symbol"),
                            "market": block.get("market"),
                        },
                    )
                    applied["closed"].append(
                        build_rejected_manager_action(
                            "manager_close_requires_adverse_evidence",
                            block_id=block_id,
                        )
                    )
                    continue
                metadata_update = build_validation_repair_metadata_update(row, block)
                if metadata_update:
                    self.repository.update_block(block_id, {"metadata": metadata_update})
                updated = self.repository.update_block(
                    block_id,
                    build_manager_exit_request_fields(row),
                )
                if updated:
                    applied["closed"].append(
                        build_manager_block_action_result("exit_requested", block_id)
                    )
                continue
            metadata_update = build_validation_repair_metadata_update(row, block)
            if metadata_update:
                self.repository.update_block(block_id, {"metadata": metadata_update})
            updated = self.repository.update_block(
                block_id,
                build_manager_closed_fields(row, closed_at=utc_now_iso()),
            )
            if updated:
                applied["closed"].append(
                    build_manager_block_action_result("closed", block_id)
                )
        for row in actions.get("pause_blocks", []):
            block_id = str(row.get("block_id") or "")
            if not block_id:
                continue
            block = self.repository.get_block(block_id)
            if block:
                metadata_update = build_validation_repair_metadata_update(row, block)
                if metadata_update:
                    self.repository.update_block(block_id, {"metadata": metadata_update})
            updated = self.repository.update_block(
                block_id,
                build_manager_pause_fields(row),
            )
            if updated:
                applied["paused"].append(
                    build_manager_block_action_result("paused", block_id)
                )
        return applied

    def _apply_candidate_budget_cap_to_row(self, row: dict[str, Any]) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "").upper().strip()
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        horizon = normalize_binance_horizon(row.get("horizon"), market=market)
        candidate = self._entry_gate_candidate(
            symbol=symbol,
            market=market,
            side=side,
            horizon=horizon,
        )
        if not candidate:
            return row
        calculated = (
            candidate.get("calculated")
            if isinstance(candidate.get("calculated"), dict)
            else candidate.get("calculated_price_plan")
            if isinstance(candidate.get("calculated_price_plan"), dict)
            else {}
        )
        sizing_inputs = (
            calculated.get("sizing_inputs")
            if isinstance(calculated.get("sizing_inputs"), dict)
            else {}
        )
        cap_usdt = (
            _safe_float(candidate.get("quote_budget_usdt"))
            or _safe_float(calculated.get("quote_budget_usdt"))
            or _safe_float(sizing_inputs.get("quote_budget_usdt"))
        )
        cap_krw = (
            _safe_float(candidate.get("quote_budget_krw"))
            or _safe_float(calculated.get("quote_budget_krw"))
            or _safe_float(sizing_inputs.get("quote_budget_krw"))
        )
        if market == UPBIT_SPOT_MARKET:
            cap = cap_krw
            cap_field = "quote_budget_krw"
        else:
            cap = cap_usdt
            cap_field = "quote_budget_usdt"
        if cap <= 0:
            return row

        adjusted = dict(row)
        metadata = dict(adjusted.get("metadata") if isinstance(adjusted.get("metadata"), dict) else {})
        reference_price = (
            _safe_float(adjusted.get("entry_trigger_price"))
            or _safe_float(adjusted.get("entry_price"))
            or _safe_float(adjusted.get("entry_price_usdt"))
        )
        performance_budget_multiplier = (
            _safe_float(candidate.get("performance_budget_multiplier"))
            or _safe_float(calculated.get("performance_budget_multiplier"))
            or _safe_float(sizing_inputs.get("performance_budget_multiplier"))
        )
        adjustment: dict[str, Any] = {
            "version": "binance_candidate_budget_cap_v1",
            "symbol": symbol,
            "market": market,
            "side": side,
            "horizon": horizon,
            "cap_field": cap_field,
        }
        if performance_budget_multiplier > 0:
            adjustment["performance_budget_multiplier"] = round(
                performance_budget_multiplier,
                6,
            )

        changed = False

        def cap_money_field(field: str, *, cap_value: float, alias: str | None = None) -> None:
            nonlocal changed
            original = _safe_float(adjusted.get(field))
            if original <= cap_value or original <= 0:
                return
            adjusted[field] = cap_value
            key = alias or field
            adjustment[f"from_{key}"] = original
            adjustment[f"to_{key}"] = cap_value
            changed = True

        if market == UPBIT_SPOT_MARKET:
            cap_money_field("quote_budget_krw", cap_value=cap_krw)
            cap_money_field("quote_budget", cap_value=cap_krw)
            cap_money_field("quantity_or_quote_budget", cap_value=cap_krw)
        else:
            cap_money_field("quote_budget_usdt", cap_value=cap_usdt)
            quote_currency = str(adjusted.get("quote_currency") or "").upper().strip()
            if quote_currency in {"", "USDT", "USDC", "BUSD"}:
                cap_money_field("quote_budget", cap_value=cap_usdt)
                cap_money_field("quantity_or_quote_budget", cap_value=cap_usdt)

        if reference_price > 0:
            cap_qty = cap / reference_price
            for field in ("qty", "qty_initial", "quantity"):
                original_qty = _safe_float(adjusted.get(field))
                if original_qty <= 0 or original_qty <= cap_qty:
                    continue
                adjusted[field] = cap_qty
                adjustment[f"from_{field}"] = original_qty
                adjustment[f"to_{field}"] = cap_qty
                changed = True

        if not changed:
            return row
        metadata["candidate_budget_cap"] = adjustment
        adjusted["metadata"] = metadata
        return adjusted

    def _near_duplicate_block_rejection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            return None
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        horizon = normalize_binance_horizon(row.get("horizon"), market=market)
        entry = build_row_price_value(row, "entry_price")
        target = build_row_price_value(row, "target_price")
        stop = build_row_price_value(row, "stop_price")
        if min(entry, target, stop) <= 0:
            return None
        tolerance_bps = max(
            _safe_float(self.config.near_duplicate_block_price_tolerance_bps),
            0.0,
        )
        for block in self.repository.list_blocks(include_closed=False):
            status = str(block.get("status") or "").strip().lower()
            if status not in {"proposed", "entry_pending", "open", "exit_pending"}:
                continue
            if str(block.get("symbol") or "").upper().strip() != symbol:
                continue
            if normalize_market(block.get("market")) != market:
                continue
            if normalize_position_side(block.get("side")) != side:
                continue
            block_metadata = (
                block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
            )
            block_horizon = normalize_binance_horizon(
                block.get("horizon") or block_metadata.get("horizon"),
                market=market,
            )
            if block_horizon != horizon:
                continue
            block_entry = _safe_float(block.get("entry_price"))
            block_target = _safe_float(block.get("target_price"))
            block_stop = _safe_float(block.get("stop_price"))
            if min(block_entry, block_target, block_stop) <= 0:
                continue
            if not (
                build_prices_within_bps(entry, block_entry, tolerance_bps)
                and build_prices_within_bps(target, block_target, tolerance_bps)
                and build_prices_within_bps(stop, block_stop, tolerance_bps)
            ):
                continue
            return {
                "status": "rejected",
                "reason": f"near_duplicate_block:{symbol}:{market}:{side}:{horizon}",
                "input": row,
                "near_duplicate_block": {
                    "version": "binance_near_duplicate_block_guard_v1",
                    "existing_block_id": str(block.get("block_id") or ""),
                    "existing_status": status,
                    "symbol": symbol,
                    "market": market,
                    "side": side,
                    "horizon": horizon,
                    "tolerance_bps": tolerance_bps,
                    "candidate": {
                        "entry_price": entry,
                        "target_price": target,
                        "stop_price": stop,
                    },
                    "existing": {
                        "entry_price": block_entry,
                        "target_price": block_target,
                        "stop_price": block_stop,
                    },
                },
            }
        return None

    @classmethod
    def _growth_governor_applies_to_row(
        cls,
        growth_governor: dict[str, Any],
        row: dict[str, Any],
    ) -> bool:
        mode = str(growth_governor.get("mode") or growth_governor.get("status") or "")
        if mode in {"", "steady"}:
            return False
        if mode == "halt_new_entries":
            return True
        weak_lanes = {
            str(item or "").strip().lower()
            for item in growth_governor.get("weak_lanes") or []
            if str(item or "").strip()
        }
        row_lanes = cls._growth_governor_row_lanes(row)
        positive_lanes = {
            str(item or "").strip().lower()
            for item in growth_governor.get("positive_lanes") or []
            if str(item or "").strip()
        }
        probation_lanes = {
            str(item or "").strip().lower()
            for item in growth_governor.get("probation_lanes") or []
            if str(item or "").strip()
        }
        if mode == "edge_rebuild" and positive_lanes.intersection(row_lanes):
            return False
        if mode == "edge_rebuild" and probation_lanes.intersection(row_lanes):
            return True
        if not weak_lanes or "all" in weak_lanes:
            return True
        return bool(weak_lanes.intersection(row_lanes))

    @staticmethod
    def _growth_governor_row_lanes(row: dict[str, Any]) -> set[str]:
        return build_growth_governor_row_lanes(row)

    def _lane_performance_cooldown_rejection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        horizon = normalize_binance_horizon(row.get("horizon"), market=market)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        lane = normalize_binance_display_lane(
            lane=row.get("lane") or metadata.get("lane") or calculated.get("lane"),
            market=market,
            horizon=horizon,
            side=side,
        )
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        lane_card = self._scorecard_for_lane(
            performance,
            market=market,
            side=side,
            lane=lane,
        )
        if not lane_card:
            return None
        min_samples = max(
            _safe_int(self.config.budget_performance_scale_min_samples),
            _safe_int(self.config.lane_performance_min_samples),
            1,
        )
        recovery = self._lane_performance_cooldown_recovery(
            row,
            market=market,
            side=side,
            lane=lane,
            min_samples=max(_safe_int(self.config.lane_performance_min_samples), 1),
        )
        if recovery:
            self._attach_performance_cooldown_recovery(row, recovery)
            return None
        validation_probe_recovery = self._validation_probe_cooldown_recovery(
            row,
            kind="validation_probe",
            matched_lane=str(lane_card.get("lane") or f"{market}:{side}"),
            scorecard=lane_card,
        )
        if validation_probe_recovery:
            self._attach_performance_cooldown_recovery(row, validation_probe_recovery)
            return None
        sample_count = _safe_int(lane_card.get("sample_count"))
        if sample_count < min_samples:
            crosscheck_status = build_row_pattern_live_crosscheck_status(row)
            weak_crosscheck = crosscheck_status in {
                "wait",
                "no_pattern_prior",
                "contradicted",
            }
            if self._lane_card_is_distressed(lane_card) and weak_crosscheck:
                return {
                    "status": "rejected",
                    "reason": f"lane_performance_cooldown:{market}:{side}",
                    "input": row,
                    "lane_performance_cooldown": {
                        "version": "binance_lane_performance_cooldown_v2_distressed_crosscheck",
                        "matched_lane": str(lane_card.get("lane") or f"{market}:{side}"),
                        "sample_count": sample_count,
                        "pnl_usdt": _safe_float(lane_card.get("pnl_usdt")),
                        "win_rate_pct": _safe_float(lane_card.get("win_rate_pct")),
                        "avg_r_multiple": _safe_float(lane_card.get("avg_r_multiple")),
                        "profit_factor": _safe_float(lane_card.get("profit_factor")),
                        "recovery_factor": _safe_float(lane_card.get("recovery_factor")),
                        "max_drawdown_r_multiple": _safe_float(
                            lane_card.get("max_drawdown_r_multiple")
                        ),
                        "min_samples": min_samples,
                        "distressed_min_samples": max(
                            _safe_int(self.config.distressed_lane_min_samples),
                            _safe_int(self.config.lane_performance_min_samples),
                            1,
                        ),
                        "distressed": True,
                        "pattern_live_crosscheck_status": crosscheck_status,
                        "recovery_required": (
                            "Distressed recent lane performance cannot add another "
                            "weak-crosscheck waiting block; wait for aligned pattern, "
                            "fresh quant/book evidence, or improved live outcomes."
                        ),
                    },
                }
            return None
        if not self._lane_card_requires_cooldown(lane_card, min_samples=min_samples):
            return None
        return {
            "status": "rejected",
            "reason": f"lane_performance_cooldown:{market}:{side}",
            "input": row,
            "lane_performance_cooldown": {
                "version": "binance_lane_performance_cooldown_v1",
                "matched_lane": str(lane_card.get("lane") or f"{market}:{side}"),
                "sample_count": sample_count,
                "pnl_usdt": _safe_float(lane_card.get("pnl_usdt")),
                "win_rate_pct": _safe_float(lane_card.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(lane_card.get("avg_r_multiple")),
                "profit_factor": _safe_float(lane_card.get("profit_factor")),
                "recovery_factor": _safe_float(lane_card.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(
                    lane_card.get("max_drawdown_r_multiple")
                ),
                "min_samples": min_samples,
                "recovery_required": (
                    "Wait for fresh live evidence before adding another block "
                    "to this underperforming lane."
                ),
            },
        }

    def _entry_quality_performance_cooldown_rejection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        entry_quality = build_entry_quality_label_from_payload(row, metadata, calculated)
        if not entry_quality:
            return None
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        quality_card = self._scorecard_for_entry_quality(
            performance,
            market=market,
            side=side,
            entry_quality=entry_quality,
        )
        if not quality_card:
            return None
        min_samples = max(_safe_int(self.config.lane_performance_min_samples), 1)
        if not self._lane_card_requires_cooldown(quality_card, min_samples=min_samples):
            return None
        recovery = self._entry_quality_performance_cooldown_recovery(
            row,
            market=market,
            side=side,
            entry_quality=entry_quality,
            min_samples=min_samples,
        )
        if recovery:
            self._attach_performance_cooldown_recovery(row, recovery)
            return None
        return {
            "status": "rejected",
            "reason": (
                f"entry_quality_performance_cooldown:"
                f"{market}:{side}:{entry_quality}"
            ),
            "input": row,
            "entry_quality_performance_cooldown": {
                "version": "binance_entry_quality_performance_cooldown_v1",
                "matched_entry_quality_lane": str(
                    quality_card.get("entry_quality_lane")
                    or f"{market}:{side}:{entry_quality}"
                ),
                "entry_quality": entry_quality,
                "sample_count": _safe_int(quality_card.get("sample_count")),
                "pnl_usdt": _safe_float(quality_card.get("pnl_usdt")),
                "win_rate_pct": _safe_float(quality_card.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(quality_card.get("avg_r_multiple")),
                "profit_factor": _safe_float(quality_card.get("profit_factor")),
                "recovery_factor": _safe_float(quality_card.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(
                    quality_card.get("max_drawdown_r_multiple")
                ),
                "min_samples": min_samples,
                "recovery_required": (
                    "This entry-quality profile has negative recent expectancy; "
                    "wait for stronger live confirmation, better cost evidence, "
                    "or improved closed-block outcomes before adding another "
                    "matching block."
                ),
            },
        }

    def _performance_cooldown_recovery_limit(self) -> int:
        feedback_limit = self._performance_scorecard_feedback_limit()
        return max(min(feedback_limit, 20), 1)

    def _validation_probe_cooldown_recovery(
        self,
        row: dict[str, Any],
        *,
        kind: str,
        matched_lane: str,
        scorecard: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        repair = metadata.get("validation_repair")
        if not isinstance(repair, dict):
            repair = row.get("validation_repair") if isinstance(row.get("validation_repair"), dict) else {}
        enforcement = metadata.get("validation_repair_enforcement")
        if not isinstance(enforcement, dict):
            enforcement = (
                row.get("validation_repair_enforcement")
                if isinstance(row.get("validation_repair_enforcement"), dict)
                else {}
            )
        if build_truthy_gate_value(repair.get("hard_filter")):
            return {}
        tokens = " ".join(
            str(value or "").strip().lower()
            for value in (
                *_as_list(repair.get("allowed_entry_postures")),
                *_as_list(repair.get("entry_biases")),
                *_as_list(repair.get("sizing_policies")),
                *_as_list(repair.get("blocks_scaling")),
            )
            if str(value or "").strip()
        )
        probe_like = any(
            token in tokens
            for token in ("probe", "waiting", "wait", "cost_verified", "fractional_kelly")
        )
        scale_blocked = build_truthy_gate_value(
            enforcement.get("scale_up_blocked") or repair.get("scale_up_blocked")
        )
        waiting_required = build_truthy_gate_value(
            enforcement.get("waiting_entry_required") or repair.get("live_shadow_required")
        )
        if not (probe_like or scale_blocked or waiting_required):
            return {}
        if not self._is_waiting_entry_payload(row):
            return {}
        return {
            "version": "binance_validation_probe_cooldown_recovery_v1",
            "kind": kind,
            "matched_lane": matched_lane,
            "sample_count": _safe_int(scorecard.get("sample_count")),
            "pnl_usdt": _safe_float(scorecard.get("pnl_usdt")),
            "win_rate_pct": _safe_float(scorecard.get("win_rate_pct")),
            "profit_factor": _safe_float(scorecard.get("profit_factor")),
            "instruction": (
                "Allow this validation-repair waiting probe through performance "
                "cooldown so Jue can collect fresh executable evidence without "
                "scaling the lane."
            ),
        }

    def _lane_performance_cooldown_recovery(
        self,
        row: dict[str, Any],
        *,
        market: str,
        side: str,
        lane: str,
        min_samples: int,
    ) -> dict[str, Any] | None:
        _ = row
        recovery_limit = self._performance_cooldown_recovery_limit()
        recent_performance = self.repository.latest_performance_scorecard(
            limit=recovery_limit
        )
        recent_card = self._scorecard_for_lane(
            recent_performance,
            market=market,
            side=side,
            lane=lane,
        )
        if not recent_card:
            return None
        if not self._lane_card_shows_cooldown_recovery(
            recent_card,
            min_samples=min_samples,
        ):
            return None
        return self._performance_cooldown_recovery_payload(
            kind="lane",
            matched_key=str(recent_card.get("lane") or f"{market}:{side}"),
            recovery_limit=recovery_limit,
            recent_card=recent_card,
        )

    def _entry_quality_performance_cooldown_recovery(
        self,
        row: dict[str, Any],
        *,
        market: str,
        side: str,
        entry_quality: str,
        min_samples: int,
    ) -> dict[str, Any] | None:
        _ = row
        recovery_limit = self._performance_cooldown_recovery_limit()
        recent_performance = self.repository.latest_performance_scorecard(
            limit=recovery_limit
        )
        recent_card = self._scorecard_for_entry_quality(
            recent_performance,
            market=market,
            side=side,
            entry_quality=entry_quality,
        )
        if not recent_card:
            return None
        if not self._lane_card_shows_cooldown_recovery(
            recent_card,
            min_samples=min_samples,
        ):
            return None
        return self._performance_cooldown_recovery_payload(
            kind="entry_quality",
            matched_key=str(
                recent_card.get("entry_quality_lane")
                or f"{market}:{side}:{entry_quality}"
            ),
            recovery_limit=recovery_limit,
            recent_card=recent_card,
        )

    @staticmethod
    def _performance_cooldown_recovery_payload(
        *,
        kind: str,
        matched_key: str,
        recovery_limit: int,
        recent_card: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "version": "binance_performance_cooldown_recovery_v1",
            "kind": kind,
            "matched_key": matched_key,
            "recent_window_limit": int(recovery_limit),
            "sample_count": _safe_int(recent_card.get("sample_count")),
            "pnl_usdt": _safe_float(recent_card.get("pnl_usdt")),
            "win_rate_pct": _safe_float(recent_card.get("win_rate_pct")),
            "avg_r_multiple": _safe_float(recent_card.get("avg_r_multiple")),
            "profit_factor": _safe_float(recent_card.get("profit_factor")),
            "recovery_factor": _safe_float(recent_card.get("recovery_factor")),
            "instruction": (
                "Recent closed-block evidence recovered this cooldown key; allow "
                "the block candidate through normal live authority, entry, and "
                "cost gates instead of freezing it on stale long-window losses."
            ),
        }

    @staticmethod
    def _attach_performance_cooldown_recovery(
        row: dict[str, Any],
        recovery: dict[str, Any],
    ) -> None:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            row["metadata"] = metadata
        metadata["performance_cooldown_recovery"] = recovery

    def _symbol_performance_cooldown_rejection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            return None
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        symbol_card = self._scorecard_for_symbol(performance, symbol)
        if not symbol_card:
            try:
                symbol_card = self.repository.performance_scorecard_for_symbol(
                    symbol,
                    limit=self._performance_scorecard_feedback_limit(),
                )
            except Exception:
                logger.warning(
                    "failed to load Binance symbol-specific performance",
                    exc_info=True,
                )
                symbol_card = None
        if not symbol_card:
            return None
        min_samples = max(
            min(_safe_int(self.config.symbol_lane_cooldown_min_samples), 5),
            1,
        )
        sample_count = _safe_int(symbol_card.get("sample_count"))
        if sample_count < min_samples:
            return None
        max_win_rate = float(self.config.symbol_lane_cooldown_max_win_rate_pct)
        if not self._symbol_card_requires_cooldown(
            symbol_card,
            min_samples=min_samples,
            max_win_rate_pct=max_win_rate,
        ):
            return None
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        return {
            "status": "rejected",
            "reason": f"symbol_performance_cooldown:{symbol}:{market}:{side}",
            "input": row,
            "symbol_performance_cooldown": {
                "version": "binance_symbol_performance_cooldown_v1",
                "symbol": symbol,
                "sample_count": sample_count,
                "pnl_usdt": _safe_float(symbol_card.get("pnl_usdt")),
                "win_rate_pct": _safe_float(symbol_card.get("win_rate_pct")),
                "avg_r_multiple": _safe_float(symbol_card.get("avg_r_multiple")),
                "profit_factor": _safe_float(symbol_card.get("profit_factor")),
                "recovery_factor": _safe_float(symbol_card.get("recovery_factor")),
                "max_drawdown_r_multiple": _safe_float(
                    symbol_card.get("max_drawdown_r_multiple")
                ),
                "min_samples": min_samples,
                "recovery_required": (
                    "Skip this repeatedly losing symbol until fresh alpha or "
                    "improved live evidence offsets the realized cost drag."
                ),
            },
        }

    def _symbol_lane_cooldown_rejection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            return None
        market = normalize_market(row.get("market") or row.get("venue"))
        side = normalize_position_side(row.get("side"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        lane = str(
            row.get("lane")
            or metadata.get("lane")
            or calculated.get("lane")
            or ""
        ).strip().lower()
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        symbol_card = self._scorecard_for_symbol(performance, symbol)
        if not symbol_card:
            try:
                symbol_card = self.repository.performance_scorecard_for_symbol(
                    symbol,
                    limit=self._performance_scorecard_feedback_limit(),
                )
            except Exception:
                logger.warning(
                    "failed to load Binance symbol-lane symbol performance",
                    exc_info=True,
                )
                symbol_card = None
        lane_card = self._scorecard_for_lane(
            performance,
            market=market,
            side=side,
            lane=lane,
        )
        min_samples = max(int(self.config.symbol_lane_cooldown_min_samples), 1)
        max_win_rate = float(self.config.symbol_lane_cooldown_max_win_rate_pct)
        if not symbol_card or not lane_card:
            return None
        symbol_samples = _safe_int(symbol_card.get("sample_count"))
        lane_samples = _safe_int(lane_card.get("sample_count"))
        if min(symbol_samples, lane_samples) < min_samples:
            return None
        symbol_pnl = _safe_float(symbol_card.get("pnl_usdt"))
        lane_pnl = _safe_float(lane_card.get("pnl_usdt"))
        symbol_win = _safe_float(symbol_card.get("win_rate_pct"))
        lane_win = _safe_float(lane_card.get("win_rate_pct"))
        symbol_avg_r = _safe_float(symbol_card.get("avg_r_multiple"))
        lane_avg_r = _safe_float(lane_card.get("avg_r_multiple"))
        if (
            symbol_pnl < 0
            and lane_pnl < 0
            and (symbol_win <= max_win_rate or symbol_avg_r < 0)
            and (lane_win <= max_win_rate or lane_avg_r < 0)
        ):
            return {
                "status": "rejected",
                "reason": f"symbol_lane_cooldown:{symbol}:{market}:{side}",
                "input": row,
                "symbol_scorecard": symbol_card,
                "lane_scorecard": lane_card,
            }
        return None

    @staticmethod
    def _scorecard_for_symbol(
        performance: dict[str, Any],
        symbol: str,
    ) -> dict[str, Any] | None:
        rows = performance.get("symbol_scorecards")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and str(row.get("symbol") or "").upper() == symbol:
                return row
        return None

    @staticmethod
    def _scorecard_for_entry_quality(
        performance: dict[str, Any],
        *,
        market: str,
        side: str,
        entry_quality: str,
    ) -> dict[str, Any] | None:
        wanted = (
            f"{normalize_market(market)}:"
            f"{normalize_position_side(side)}:"
            f"{build_normalize_entry_quality_label(entry_quality)}"
        )
        rows = performance.get("entry_quality_scorecards")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("entry_quality_lane") or "").strip().lower() == wanted:
                return row
        return None

    @staticmethod
    def _scorecard_for_lane(
        performance: dict[str, Any],
        *,
        market: str,
        side: str,
        lane: str = "",
    ) -> dict[str, Any] | None:
        market = normalize_market(market)
        side = normalize_position_side(side)
        lane_key = str(lane or "").strip().lower()
        if lane_key in {"short", "mid", "long"} and market != "futures":
            for candidate in (f"{market}:{side}:{lane_key}", lane_key):
                lane_card = BinanceBlockTrader._scorecard_for_lane_key(
                    performance,
                    candidate,
                )
                if lane_card:
                    return lane_card
        if lane_key in {"short", "mid", "long", "futures"}:
            lane_key = f"{market}:{side}"
        if lane_key:
            lane_card = BinanceBlockTrader._scorecard_for_lane_key(
                performance,
                lane_key,
            )
            if lane_card:
                return lane_card
        wanted = f"{market}:{side}"
        rows = performance.get("side_scorecards")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and str(row.get("side") or "").lower() == wanted:
                return row
        return None

    @staticmethod
    def _scorecard_for_lane_key(
        performance: dict[str, Any],
        lane: str,
    ) -> dict[str, Any] | None:
        wanted = str(lane or "").strip().lower()
        if not wanted:
            return None
        rows = performance.get("lane_scorecards")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and str(row.get("lane") or "").lower() == wanted:
                    return row
        if ":" in wanted:
            rows = performance.get("side_scorecards")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and str(row.get("side") or "").lower() == wanted:
                        return row
        return None

    async def _exchange_min_order_preflight_rejection(
        self,
        block: dict[str, Any],
    ) -> dict[str, Any] | None:
        market = normalize_market(block.get("market"))
        if not self._execution_enabled(market, for_entry=True):
            return None
        symbol = str(block.get("symbol") or "").upper()
        side = build_entry_order_side(block)
        qty = _safe_float(block.get("qty_initial") or block.get("qty_open"))
        entry_price = _safe_float(block.get("entry_price"))
        if not symbol or qty <= 0 or entry_price <= 0:
            return None
        multiplier = (
            1.0 + float(self.config.aggressive_limit_bps) / 10_000.0
            if side == "buy"
            else 1.0 - float(self.config.aggressive_limit_bps) / 10_000.0
        )
        try:
            normalized = await self._normalize_order_for_exchange(
                market=market,
                symbol=symbol,
                side=side,
                qty=qty,
                limit_price=max(entry_price * multiplier, 0.0),
                allow_min_notional_qty_bump=self._allows_min_notional_qty_bump(block),
            )
            if _safe_float(normalized.get("quantity")) > qty:
                block["qty_initial"] = normalized["quantity"]
        except ValueError as exc:
            message = str(exc)
            if "below min" in message or "below minimum" in message:
                return {
                    "status": "rejected",
                    "reason": f"exchange_min_order_rejected:{message}",
                    "input": block,
                }
            raise
        return None

    @staticmethod
    def _allows_min_notional_qty_bump(block: dict[str, Any]) -> bool:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        for row in (block, metadata):
            for key in (
                "quote_budget_usdt",
                "quote_budget_krw",
                "quote_budget",
                "quantity_or_quote_budget",
            ):
                if _safe_float(row.get(key)) > 0:
                    return True
        return False

    async def _submit_entry_for_block(self, block: dict[str, Any]) -> dict[str, Any]:
        block_id = str(block.get("block_id") or "")
        market = normalize_market(block.get("market"))
        symbol = str(block.get("symbol") or "").upper()
        side = build_entry_order_side(block)
        qty = _safe_float(block.get("qty_initial") or block.get("qty_open"))
        price = _safe_float(block.get("entry_price"))
        if qty <= 0 or price <= 0:
            updated = self.repository.update_block(
                block_id,
                {
                    "status": "error",
                    "qty_open": 0.0,
                    "risk_note": "entry order skipped: missing qty or entry price",
                },
            )
            return {"status": "error", "reason": "missing_entry_inputs", "block": updated}

        response: dict[str, Any] = {}
        status = "sent"
        try:
            response = await self._submit_entry_order(
                block=block,
                market=market,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
            )
        except Exception as exc:
            response = {"status": "error", "error_message": str(exc)}
            status = "error"
        if status == "sent":
            response = await self._enrich_order_response_for_costs(
                block=block,
                market=market,
                symbol=symbol,
                response=response,
            )

        response_status = str(response.get("status") or "").upper()
        if response_status == "ENTRY_PREFLIGHT_BLOCKED":
            return {
                "status": "blocked",
                "reason": str(response.get("reason") or "preflight_book_invalid"),
                "preflight": response.get("preflight") or {},
                "block": response.get("block"),
            }

        order = self.repository.add_order(
            {
                "block_id": block_id,
                "symbol": symbol,
                "market": market,
                "side": side,
                "qty": qty,
                "order_type": "LIMIT_IOC",
                "status": status,
                "reason": "entry_order",
                "response": response,
            }
        )

        filled_qty = build_response_filled_qty(response, requested_qty=qty)
        if response_status in {"ENTRY_WAITING", "WAITING_ENTRY"}:
            updated = self.repository.update_block(
                block_id,
                {
                    "status": "proposed",
                    "qty_open": 0.0,
                    "risk_note": str(response.get("reason") or "entry waiting for trigger"),
                    "metadata": response.get("metadata") or block.get("metadata") or {},
                },
            )
            return {"status": "waiting_entry", "order": order, "block": updated}
        if status == "sent" and response_status == "EXPIRED" and filled_qty <= 0:
            trigger_price = build_entry_tolerance_price(
                entry_price=price,
                side=side,
                aggressive_limit_bps=self.config.aggressive_limit_bps,
            )
            operator = "<=" if side == "buy" else ">="
            reason = build_entry_not_filled_reason(
                response,
                fallback_status=response_status,
            )
            updated = self.repository.update_block(
                block_id,
                {
                    "status": "proposed",
                    "qty_open": 0.0,
                    "risk_note": reason,
                    "metadata": build_waiting_entry_metadata(
                        block=block,
                        trigger_price=trigger_price or price,
                        operator=operator,
                        reason=reason,
                    ),
                },
            )
            return {"status": "waiting_entry", "order": order, "block": updated}
        if status == "sent" and (response_status == "FILLED" or filled_qty > 0):
            fill_price_fields = build_entry_fill_price_update_fields(
                block,
                fill_price=build_filled_order_price(order),
                min_candidate_stop_pct=_safe_float(self.config.min_candidate_stop_pct),
            )
            risk_note = (
                f"partial entry fill: {filled_qty}/{qty}"
                if filled_qty < qty
                else str(block.get("risk_note") or "")
            )
            if fill_price_fields.get("target_price") or fill_price_fields.get("stop_price"):
                risk_note = (
                    f"{risk_note}; " if risk_note else ""
                ) + "target/stop rebased to actual entry fill"
            update_fields = {
                "status": "open",
                "qty_open": min(filled_qty, qty),
                "opened_at": utc_now_iso(),
                "risk_note": risk_note,
            }
            update_fields.update(fill_price_fields)
            updated = self.repository.update_block(
                block_id,
                update_fields,
            )
            return {"status": "opened", "order": order, "block": updated}

        updated = self.repository.update_block(
            block_id,
            {
                "status": "error",
                "qty_open": 0.0,
                "risk_note": (
                    f"entry order not filled: {response_status or status}; "
                    f"{response.get('error_message') or ''}"
                ).strip(),
            },
        )
        return {"status": "error", "order": order, "block": updated}

    async def _submit_entry_order(
        self,
        *,
        block: dict[str, Any],
        market: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> dict[str, Any]:
        adapter = self._adapter_for_market(market)
        method_name = "submit_futures_order" if market == "futures" else "submit_spot_order"
        method = getattr(adapter, method_name, None) if adapter is not None else None
        if method is None:
            venue = "Upbit" if is_upbit_market(market) else "Binance"
            raise RuntimeError(f"missing {venue} adapter method: {method_name}")
        bps = max(float(self.config.aggressive_limit_bps), 0.0)
        multiplier = 1.0 + (bps / 10_000.0) if side == "buy" else 1.0 - (bps / 10_000.0)
        client_order_id = self._client_order_id(market=market, symbol=symbol, side=side)
        reference = await self._entry_execution_reference(
            market=market,
            symbol=symbol,
            side=side,
        )
        if build_is_waiting_entry_block(block):
            preflight = await self._preflight_waiting_entry(block, reference=reference)
            if preflight is not None:
                return {
                    "status": "ENTRY_PREFLIGHT_BLOCKED",
                    "reason": preflight.get("reason") or "preflight_book_invalid",
                    "preflight": preflight.get("preflight") or {},
                    "block": preflight.get("block"),
                }
        reference_price = _safe_float(reference.get("execution_price"))
        if (
            reference_price > 0
            and price > 0
            and not build_entry_reference_inside_tolerance(
                entry_price=price,
                reference_price=reference_price,
                side=side,
                aggressive_limit_bps=self.config.aggressive_limit_bps,
            )
        ):
            trigger_price = build_entry_tolerance_price(
                entry_price=price,
                side=side,
                aggressive_limit_bps=self.config.aggressive_limit_bps,
            )
            operator = "<=" if side == "buy" else ">="
            reason = (
                f"entry reference out of tolerance: {reference_price:g} "
                f"{'>' if side == 'buy' else '<'} {trigger_price:g}"
            )
            return {
                "status": "ENTRY_WAITING",
                "reason": reason,
                "reference": reference,
                "metadata": build_waiting_entry_metadata(
                    block=block,
                    trigger_price=trigger_price,
                    operator=operator,
                    reason=reason,
                    reference=reference,
                ),
            }
        if market == "futures":
            await self._prepare_futures_entry(block=block, symbol=symbol)
        order_base_price = reference_price if reference_price > 0 else price
        limit_price = max(order_base_price * multiplier, 0.0)
        normalized = await self._normalize_order_for_exchange(
            market=market,
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            allow_min_notional_qty_bump=self._allows_min_notional_qty_bump(block),
        )
        return await _maybe_await(
            method(
                symbol=symbol,
                side=side,
                quantity=normalized["quantity"],
                limit_price=normalized["limit_price"],
                client_order_id=client_order_id,
                reduce_only=False,
            )
            if market == "futures"
            else method(
                symbol=symbol,
                side=side,
                quantity=normalized["quantity"],
                limit_price=normalized["limit_price"],
                client_order_id=client_order_id,
            )
        )

    async def _normalize_order_for_exchange(
        self,
        *,
        market: str,
        symbol: str,
        side: str,
        qty: float,
        limit_price: float,
        allow_min_notional_qty_bump: bool = False,
        allow_reduce_only_below_min_notional: bool = False,
    ) -> dict[str, float]:
        filters = await self._exchange_filters_for_order(market=market, symbol=symbol)
        return build_normalize_order_for_filters(
            filters,
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            allow_min_notional_qty_bump=allow_min_notional_qty_bump,
            allow_reduce_only_below_min_notional=allow_reduce_only_below_min_notional,
            max_notional_bump_shortfall_pct=MIN_NOTIONAL_QTY_BUMP_MAX_SHORTFALL_PCT,
        )

    async def _exchange_filters_for_order(
        self,
        *,
        market: str,
        symbol: str,
    ) -> dict[str, dict[str, Any]]:
        normalized_market = normalize_market(market)
        adapter = self._adapter_for_market(normalized_market)
        if adapter is None:
            return {}
        method_names = (
            ("fetch_spot_exchange_filters", "fetch_exchange_filters")
            if normalized_market in {"spot", UPBIT_SPOT_MARKET}
            else ("fetch_futures_exchange_filters", "fetch_exchange_filters")
        )
        for method_name in method_names:
            method = getattr(adapter, method_name, None)
            if method is None:
                continue
            try:
                if method_name == "fetch_exchange_filters":
                    payload = await _maybe_await(method(symbol, market=normalized_market))
                else:
                    payload = await _maybe_await(method(symbol))
            except Exception as exc:
                logger.warning(
                    "binance %s exchange filter fetch failed for %s: %s",
                    normalized_market,
                    symbol,
                    exc,
                )
                return {}
            if isinstance(payload, dict):
                return {
                    str(key).upper(): dict(value)
                    for key, value in payload.items()
                    if isinstance(value, dict)
                }
        return {}

    async def _prepare_futures_entry(self, *, block: dict[str, Any], symbol: str) -> None:
        margin_method = getattr(self.adapter, "set_futures_margin_type", None)
        if margin_method is not None:
            try:
                await _maybe_await(
                    margin_method(
                        symbol=symbol,
                        margin_type=str(block.get("margin_type") or "isolated"),
                    )
                )
            except Exception as exc:
                message = str(exc)
                if "-4046" not in message and "No need to change margin type" not in message:
                    raise
        leverage_method = getattr(self.adapter, "set_futures_leverage", None)
        if leverage_method is not None:
            await _maybe_await(
                leverage_method(
                    symbol=symbol,
                    leverage=max(_safe_int(block.get("leverage") or 1), 1),
                )
            )

    def _apply_risk_sizing(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.risk_sizer is None:
            return payload
        if not self._requires_risk_sizing(payload):
            return payload
        if is_upbit_market(payload.get("market") or payload.get("venue")):
            out = dict(payload)
            metadata = _json_loads(_json_dumps(out.get("metadata") or {}), {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["risk_sizing"] = {
                "status": "skipped",
                "reason": "upbit_krw_budget_sizing",
            }
            out["metadata"] = metadata
            return out
        entry = _safe_float(payload.get("entry_price") or payload.get("entry_price_usdt"))
        stop = _safe_float(payload.get("stop_price") or payload.get("stop_price_usdt"))
        target = _safe_float(payload.get("target_price") or payload.get("target_price_usdt"))
        if entry <= 0 or stop <= 0 or target <= 0:
            raise ValueError("risk_sizer_requires_entry_target_stop")
        symbol = str(payload.get("symbol") or "").upper()
        leverage = max(_safe_int(payload.get("leverage") or 1), 1)
        account = self._last_account_snapshot or {}
        raw_equity = _safe_float(account.get("spot_cash_usdt")) + _safe_float(
            account.get("futures_cash_usdt")
        )
        live_authority = self._last_live_authority_context or {}
        live_authority_budget_multiplier = _safe_float(
            live_authority.get("max_budget_multiplier")
        )
        if live_authority_budget_multiplier <= 0:
            live_authority_budget_multiplier = 1.0
        live_authority_budget_multiplier = min(live_authority_budget_multiplier, 1.0)
        active_revision_budget_multiplier = self._active_revision_budget_multiplier(
            live_authority
        )
        active_revision_gate_reason = self._active_revision_waiting_entry_reason(
            live_authority
        )
        if active_revision_budget_multiplier is not None:
            live_authority_budget_multiplier = min(
                live_authority_budget_multiplier,
                active_revision_budget_multiplier,
            )
        live_authority_adjusted_equity = raw_equity * max(
            live_authority_budget_multiplier,
            0.0,
        )
        proposed_qty = _safe_float(payload.get("qty"))
        if proposed_qty <= 0 and payload.get("quote_budget_usdt") is not None:
            proposed_qty = _safe_float(payload.get("quote_budget_usdt")) / entry
        metadata_for_lane = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        lane_authority_gate = (
            metadata_for_lane.get("lane_authority_gate")
            if isinstance(metadata_for_lane.get("lane_authority_gate"), dict)
            else {}
        )
        lane_authority_budget_multiplier = _safe_float(
            lane_authority_gate.get("budget_multiplier")
        )
        if lane_authority_budget_multiplier <= 0:
            lane_authority_budget_multiplier = 1.0
        lane_scale_allowed = bool(lane_authority_gate.get("scale_up_allowed"))
        lane_multiplier_cap = 1.25 if lane_scale_allowed else 1.0
        lane_authority_budget_multiplier = min(
            max(lane_authority_budget_multiplier, 0.05),
            lane_multiplier_cap,
        )
        market = normalize_market(payload.get("market"))
        side = normalize_position_side(payload.get("side"))
        lane = normalize_binance_display_lane(
            lane=payload.get("lane") or metadata_for_lane.get("lane"),
            market=market,
            horizon=str(payload.get("horizon") or ""),
            side=side,
        )
        (
            execution_defect_budget_multiplier,
            execution_defect_risk,
        ) = self._execution_defect_risk_budget_context(
            market=market,
            side=side,
            lane=lane,
        )
        pattern_key = _extract_block_metadata_pattern_key(metadata_for_lane)
        (
            pattern_performance_multiplier,
            pattern_performance_scorecard,
        ) = self._pattern_performance_risk_context(pattern_key)
        sizing_equity = (
            live_authority_adjusted_equity
            * lane_authority_budget_multiplier
            * execution_defect_budget_multiplier
            * pattern_performance_multiplier
        )
        result = self.risk_sizer.size_block(
            symbol=symbol,
            account_equity_usdt=sizing_equity,
            current_symbol_exposure_usdt=self._current_symbol_exposure_usdt(symbol),
            current_total_exposure_usdt=self._current_total_exposure_usdt(),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            side=str(payload.get("side") or "long"),
            proposed_qty=proposed_qty if proposed_qty > 0 else None,
            leverage=leverage,
            lane=lane,
            performance_multiplier=self._lane_performance_risk_multiplier(
                lane,
                market=market,
                side=side,
            ),
        )
        if result.get("status") != "ok":
            raise ValueError(f"risk_sizer_rejected:{result.get('reason')}")
        result = dict(result)
        result_qty = _safe_float(result.get("qty"))
        candidate_budget_cap = (
            metadata_for_lane.get("candidate_budget_cap")
            if isinstance(metadata_for_lane.get("candidate_budget_cap"), dict)
            else {}
        )
        budget_backed_request = bool(candidate_budget_cap) or any(
            payload.get(key) is not None
            for key in (
                "quote_budget_usdt",
                "quote_budget_krw",
                "quote_budget",
                "quantity_or_quote_budget",
            )
        )
        if budget_backed_request and proposed_qty > 0 and result_qty > proposed_qty:
            result["candidate_budget_qty_cap"] = {
                "version": "binance_candidate_budget_qty_cap_v1",
                "from_qty": result_qty,
                "to_qty": proposed_qty,
                "source": "proposed_qty_after_candidate_budget_cap",
            }
            if candidate_budget_cap:
                result["candidate_budget_qty_cap"]["candidate_budget_cap"] = (
                    candidate_budget_cap
                )
            result["qty"] = proposed_qty
            result_qty = proposed_qty
        floor_notional = (
            VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR
            if market == UPBIT_SPOT_MARKET
            else VALIDATION_REPAIR_MIN_USDT_NOTIONAL_FLOOR
        )
        floored_qty = self._minimum_executable_probe_qty_floor(
            row=payload,
            market=market,
            reference_price=entry,
            original_qty=proposed_qty if proposed_qty > 0 else result_qty,
            adjusted_qty=result_qty,
            notional_floor=floor_notional,
        )
        if floored_qty > result_qty:
            result["raw_risk_sizer_qty"] = result_qty
            result["qty"] = floored_qty
            result["minimum_executable_notional_floor"] = {
                "version": "binance_risk_sizing_minimum_executable_probe_floor_v1",
                "from_qty": result_qty,
                "to_qty": floored_qty,
                "entry_price": entry,
                "notional_floor": floor_notional,
                "reason": "validation_probe_must_remain_exchange_executable",
            }
        out = dict(payload)
        out["qty"] = result["qty"]
        for field in ("qty_initial", "quantity"):
            if field in out:
                out[field] = result["qty"]
        result = {
            **result,
            "raw_account_equity_usdt": raw_equity,
            "live_authority_budget_multiplier": live_authority_budget_multiplier,
            "live_authority_adjusted_equity_usdt": live_authority_adjusted_equity,
            "lane_authority_budget_multiplier": lane_authority_budget_multiplier,
            "execution_defect_budget_multiplier": execution_defect_budget_multiplier,
            "execution_defect_risk": execution_defect_risk,
            "pattern_key": pattern_key,
            "pattern_performance_multiplier": pattern_performance_multiplier,
            "pattern_performance_scorecard": pattern_performance_scorecard,
            "sizing_equity_usdt": sizing_equity,
        }
        if active_revision_budget_multiplier is not None:
            result["active_revision_budget_multiplier"] = (
                active_revision_budget_multiplier
            )
            result["active_revision_gate_reason"] = active_revision_gate_reason
        metadata = _json_loads(_json_dumps(out.get("metadata") or {}), {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["risk_sizing"] = result
        out["metadata"] = metadata
        return out

    def _performance_scorecard_feedback_limit(self) -> int:
        configured = _safe_int(self.config.performance_scorecard_feedback_limit)
        if configured <= 0:
            configured = 120
        return max(configured, 1)

    def _execution_defect_risk_budget_context(
        self,
        *,
        market: str = "",
        side: str = "",
        lane: str = "",
    ) -> tuple[float, dict[str, Any]]:
        performance = self.repository.latest_performance_scorecard(
            limit=self._performance_scorecard_feedback_limit()
        )
        risk = performance.get("execution_defect_risk")
        if not isinstance(risk, dict):
            risk = {
                "version": "binance_execution_defect_risk_v1",
                "status": "clear",
                "excluded_count": 0,
                "excluded_loss_usdt": 0.0,
            }
        risk = self._scope_execution_defect_risk(
            risk,
            market=market,
            side=side,
            lane=lane,
        )
        multiplier = 1.0
        if (
            str(risk.get("status") or "").strip().lower() == "elevated"
            and _safe_float(risk.get("excluded_loss_usdt")) > 0
        ):
            configured = _safe_float(self.config.execution_defect_loss_multiplier)
            multiplier = min(max(configured if configured > 0 else 1.0, 0.1), 1.0)
        return multiplier, risk

    @staticmethod
    def _scope_execution_defect_risk(
        risk: dict[str, Any],
        *,
        market: str,
        side: str,
        lane: str,
    ) -> dict[str, Any]:
        status = str(risk.get("status") or "").strip().lower()
        if status != "elevated":
            return risk
        scope_losses = risk.get("scope_loss_usdt")
        scope_counts = risk.get("scope_counts")
        if not isinstance(scope_losses, dict) or not isinstance(scope_counts, dict):
            return risk
        target_scopes = {
            f"{normalize_market(market)}:{normalize_position_side(side)}",
            str(lane or "").strip().lower(),
        }
        target_scopes = {scope for scope in target_scopes if scope and scope != ":"}
        if not target_scopes:
            return risk
        scoped_count = sum(_safe_int(scope_counts.get(scope)) for scope in target_scopes)
        scoped_loss = sum(_safe_float(scope_losses.get(scope)) for scope in target_scopes)
        scoped = dict(risk)
        scoped.update(
            {
                "global_status": risk.get("status"),
                "target_scopes": sorted(target_scopes),
                "scoped_excluded_count": scoped_count,
                "scoped_excluded_loss_usdt": round(scoped_loss, 8),
                "scope_filter": "market_side_or_lane",
            }
        )
        if scoped_count <= 0 or scoped_loss <= 0:
            scoped["status"] = "scoped_clear"
            scoped["instruction"] = (
                "Execution defects exist in other lanes, but this candidate's "
                "market/side/lane has no matching defect loss in the feedback "
                "window; do not shrink this lane's risk budget for unrelated "
                "execution defects."
            )
            return scoped
        scoped["excluded_loss_usdt"] = scoped_loss
        scoped["excluded_count"] = scoped_count
        return scoped

    def _lane_performance_risk_multiplier(
        self,
        lane: str,
        *,
        market: str = "",
        side: str = "",
        performance: dict[str, Any] | None = None,
    ) -> float:
        if not bool(self.config.lane_performance_scale_enabled):
            return 1.0
        normalized_lane = str(lane or "").strip().lower()
        if not normalized_lane:
            return 1.0
        if performance is None:
            try:
                performance = self.repository.latest_performance_scorecard(
                    limit=self._performance_scorecard_feedback_limit()
                )
            except Exception:
                logger.warning(
                    "failed to load Binance lane performance for sizing",
                    exc_info=True,
                )
                return 1.0
        lane_card = None
        normalized_market = normalize_market(market) if market else ""
        normalized_side = normalize_position_side(side) if side else ""
        if normalized_market and normalized_side:
            lane_card = self._scorecard_for_lane(
                performance,
                market=normalized_market,
                side=normalized_side,
                lane=normalized_lane,
            )
        if not lane_card:
            lane_card = self._scorecard_for_lane_key(performance, normalized_lane)
        if not lane_card:
            return 1.0
        sample_count = _safe_int(lane_card.get("sample_count"))
        min_samples = max(_safe_int(self.config.lane_performance_min_samples), 1)
        if sample_count < min_samples:
            return 1.0
        pnl = _safe_float(lane_card.get("pnl_usdt"))
        win_rate = _safe_float(lane_card.get("win_rate_pct"))
        avg_r = _safe_float(lane_card.get("avg_r_multiple"))
        profit_factor = _safe_float(lane_card.get("profit_factor"))
        max_drawdown_r = _safe_float(lane_card.get("max_drawdown_r_multiple"))
        recovery_factor = _safe_float(lane_card.get("recovery_factor"))
        if (
            pnl < 0
            or avg_r < 0
            or win_rate <= 35.0
            or profit_factor < 1.0
            or recovery_factor <= 0.0
            or max_drawdown_r <= -3.0
        ):
            recent_recovery = self._lane_performance_recent_recovery_card(
                market=normalized_market,
                side=normalized_side,
                lane=normalized_lane,
                min_samples=min_samples,
            )
            if recent_recovery:
                return min(max(RECENT_RECOVERY_RISK_MULTIPLIER, 0.2), 1.0)
            loss_multiplier = _safe_float(self.config.lane_performance_loss_multiplier)
            return min(max(loss_multiplier or 0.5, 0.2), 1.0)
        scale_samples = max(_safe_int(self.config.budget_performance_scale_min_samples), min_samples)
        threshold = _safe_float(self.config.budget_performance_scale_win_rate_pct)
        if not self._scorecard_allows_budget_scale(
            lane_card,
            min_samples=scale_samples,
            win_rate_threshold=threshold,
        ):
            return 1.0
        multiplier = min(max(_safe_float(self.config.budget_performance_scale_multiplier), 1.0), 3.0)
        if normalized_lane == "volatile_attack":
            cap = _safe_float(self.config.volatile_attack_max_performance_multiplier) or 1.15
            multiplier = min(multiplier, max(cap, 1.0))
        return multiplier

    def _lane_performance_recent_recovery_card(
        self,
        *,
        market: str,
        side: str,
        lane: str,
        min_samples: int,
    ) -> dict[str, Any] | None:
        if not market or not side:
            return None
        recent_performance = self.repository.latest_performance_scorecard(
            limit=self._performance_cooldown_recovery_limit()
        )
        recent_card = self._scorecard_for_lane(
            recent_performance,
            market=market,
            side=side,
            lane=lane,
        )
        if not recent_card:
            return None
        if not self._lane_card_shows_cooldown_recovery(
            recent_card,
            min_samples=min_samples,
        ):
            return None
        return recent_card

    @staticmethod
    def _scorecard_for_pattern_key(
        performance: dict[str, Any],
        pattern_key: str,
    ) -> dict[str, Any] | None:
        wanted = str(pattern_key or "").strip().lower()
        if not wanted:
            return None
        rows = performance.get("pattern_scorecards")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if (
                isinstance(row, dict)
                and str(row.get("pattern_key") or "").strip().lower() == wanted
            ):
                return row
        return None

    def _pattern_performance_risk_context(
        self,
        pattern_key: str,
    ) -> tuple[float, dict[str, Any]]:
        clean_key = str(pattern_key or "").strip()
        if not clean_key:
            return 1.0, {}
        try:
            performance = self.repository.latest_performance_scorecard(
                limit=self._performance_scorecard_feedback_limit()
            )
        except Exception:
            logger.warning("failed to load Binance pattern performance for sizing", exc_info=True)
            return 1.0, {"pattern_key": clean_key, "status": "scorecard_error"}
        card = self._scorecard_for_pattern_key(performance, clean_key)
        if not card:
            try:
                card = self.repository.performance_scorecard_for_pattern_key(
                    clean_key,
                    limit=max(
                        self._performance_scorecard_feedback_limit(),
                        _safe_int(self.config.lane_performance_min_samples),
                        20,
                    ),
                )
            except Exception:
                logger.warning(
                    "failed to load Binance pattern-specific performance",
                    exc_info=True,
                )
                card = None
        if not card:
            return 1.0, {"pattern_key": clean_key, "status": "missing"}

        multiplier = self._scorecard_budget_de_risk_multiplier(card)
        status = "de_risk" if multiplier < 1.0 else "neutral"
        if multiplier >= 1.0 and self._scorecard_allows_budget_scale(
            card,
            min_samples=max(
                _safe_int(self.config.budget_performance_scale_min_samples),
                _safe_int(self.config.lane_performance_min_samples),
                1,
            ),
            win_rate_threshold=_safe_float(
                self.config.budget_performance_scale_win_rate_pct
            ),
        ):
            configured = _safe_float(self.config.budget_performance_scale_multiplier)
            multiplier = min(max(configured, 1.0), 1.5)
            status = "scale_candidate" if multiplier > 1.0 else "neutral"
        compact_card = {
            key: card.get(key)
            for key in (
                "pattern_key",
                "sample_count",
                "pnl_usdt",
                "avg_r_multiple",
                "win_rate_pct",
                "profit_factor",
                "max_drawdown_r_multiple",
                "recovery_factor",
            )
            if card.get(key) not in ({}, [], "", None)
        }
        compact_card["status"] = status
        return min(max(multiplier, 0.05), 1.5), compact_card

    def _annotate_candidate_pattern_performance(
        self,
        row: dict[str, Any],
        *,
        entry_gate_policy: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        metadata = dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {})
        calculated = (
            dict(row.get("calculated"))
            if isinstance(row.get("calculated"), dict)
            else dict(row.get("calculated_price_plan"))
            if isinstance(row.get("calculated_price_plan"), dict)
            else {}
        )
        pattern_key = _extract_block_metadata_pattern_key(
            {
                **metadata,
                "calculated_price_plan": calculated
                or metadata.get("calculated_price_plan", {}),
            }
        )
        if not pattern_key:
            pattern_key = _clean_text(row.get("pattern_key"), limit=160)
        multiplier, scorecard = self._pattern_performance_risk_context(pattern_key)
        if not scorecard or scorecard.get("status") == "missing":
            return row, False

        annotated = dict(row)
        annotated["pattern_performance_scorecard"] = scorecard
        metadata["pattern_performance_scorecard"] = scorecard
        calculated["pattern_performance_scorecard"] = scorecard

        sizing_inputs = (
            dict(calculated.get("sizing_inputs"))
            if isinstance(calculated.get("sizing_inputs"), dict)
            else {}
        )
        existing_multiplier = (
            _safe_float(sizing_inputs.get("performance_budget_multiplier"))
            or _safe_float(calculated.get("performance_budget_multiplier"))
            or _safe_float(metadata.get("performance_budget_multiplier"))
            or _safe_float(row.get("performance_budget_multiplier"))
            or 1.0
        )
        if multiplier < 1.0:
            combined_multiplier = min(existing_multiplier, multiplier)
        elif existing_multiplier < 1.0:
            combined_multiplier = existing_multiplier
        else:
            combined_multiplier = multiplier
        sizing_inputs["pattern_performance_multiplier"] = round(multiplier, 4)
        sizing_inputs["performance_budget_multiplier"] = round(combined_multiplier, 4)
        calculated["sizing_inputs"] = sizing_inputs
        calculated["pattern_performance_multiplier"] = round(multiplier, 4)
        calculated["performance_budget_multiplier"] = round(combined_multiplier, 4)
        metadata["pattern_performance_multiplier"] = round(multiplier, 4)
        metadata["performance_budget_multiplier"] = round(combined_multiplier, 4)
        annotated["performance_budget_multiplier"] = round(combined_multiplier, 4)

        edge_score = self._manager_candidate_empirical_edge_score(
            {**annotated, "calculated": calculated, "metadata": metadata},
            entry_gate_policy=entry_gate_policy,
        )
        annotated["empirical_edge_score"] = edge_score
        calculated["empirical_edge_score"] = edge_score
        metadata["empirical_edge_score"] = edge_score
        annotated["calculated"] = calculated
        annotated["calculated_price_plan"] = calculated
        metadata["calculated_price_plan"] = calculated
        annotated["metadata"] = metadata
        return annotated, True

    @staticmethod
    def _requires_risk_sizing(payload: dict[str, Any]) -> bool:
        return (
            str(payload.get("created_by") or "").strip().lower() == "llm"
            or payload.get("manager_run_id") is not None
        )

    def _current_symbol_exposure_usdt(self, symbol: str) -> float:
        return build_current_symbol_exposure_usdt(
            self.repository.list_blocks(include_closed=False),
            symbol,
            upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            active_statuses=ACTIVE_BLOCK_STATUSES,
        )

    def _current_total_exposure_usdt(self) -> float:
        return build_current_total_exposure_usdt(
            self.repository.list_blocks(include_closed=False),
            upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
            active_statuses=ACTIVE_BLOCK_STATUSES,
        )

    def _block_notional_usdt(self, block: dict[str, Any]) -> float:
        return build_block_notional_usdt(
            block,
            upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )

    def _manager_symbols(
        self,
        *,
        blocks: list[dict[str, Any]],
        candidates: list[dict[str, Any]] | None,
        universe: list[str] | None,
    ) -> list[str]:
        symbols: list[str] = []
        symbols.extend(str(row.get("symbol") or "").upper() for row in blocks)
        symbols.extend(str(row.get("symbol") or "").upper() for row in (candidates or []))
        symbols.extend(str(symbol or "").upper() for symbol in (universe or []))
        symbols.extend(parse_universe(self.config.spot_universe))
        symbols.extend(parse_universe(self.config.futures_universe))
        symbols.extend(upbit_market_to_usdt_symbol(symbol) for symbol in parse_universe(self.config.upbit_universe))
        clean = [symbol for symbol in symbols if symbol]
        return list(dict.fromkeys(clean))[: max(int(self.config.max_manager_symbols), 1)]

    def _default_market_universe(self) -> dict[str, list[str]]:
        return {
            "spot": parse_universe(self.config.spot_universe),
            "futures": parse_universe(self.config.futures_universe),
            UPBIT_SPOT_MARKET: [upbit_market_symbol(symbol) for symbol in parse_universe(self.config.upbit_universe)],
        }

    def _build_runtime_market_universe(
        self,
        *,
        blocks: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        crypto_research: dict[str, Any],
    ) -> dict[str, list[str]]:
        protected: dict[str, list[str]] = {"spot": [], "futures": [], UPBIT_SPOT_MARKET: []}
        filler = self._default_market_universe()
        max_symbols = max(int(self.config.max_manager_symbols), 1)

        for block in blocks:
            symbol = str(block.get("symbol") or "").upper().strip()
            market = normalize_market(block.get("market"))
            if symbol:
                protected[market].append(upbit_market_symbol(symbol) if market == UPBIT_SPOT_MARKET else symbol)

        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "").upper().strip()
            market = normalize_market(candidate.get("market") or candidate.get("venue"))
            if symbol:
                protected[market].append(upbit_market_symbol(symbol) if market == UPBIT_SPOT_MARKET else symbol)

        for item in crypto_research.get("items") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            filler["spot"].append(symbol)
            filler[UPBIT_SPOT_MARKET].append(upbit_market_symbol(symbol))
            features = item.get("features") if isinstance(item.get("features"), dict) else {}
            if str(features.get("derivatives_status") or "").lower() == "available":
                filler["futures"].append(symbol)

        for candidate in crypto_research.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            symbol = str(candidate.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            market = normalize_market(candidate.get("market"))
            filler[market].append(upbit_market_symbol(symbol) if market == UPBIT_SPOT_MARKET else symbol)
            if market in {"spot", "futures"}:
                filler[UPBIT_SPOT_MARKET].append(upbit_market_symbol(symbol))

        universe: dict[str, list[str]] = {}
        for market in ("spot", "futures", UPBIT_SPOT_MARKET):
            protected_symbols = parse_universe(",".join(protected[market]))
            filler_symbols = [
                symbol
                for symbol in parse_universe(",".join(filler[market]))
                if symbol not in set(protected_symbols)
            ]
            remaining = max(max_symbols - len(protected_symbols), 0)
            universe[market] = protected_symbols + filler_symbols[:remaining]
        return universe

    async def _enrich_crypto_research_with_live_books(
        self,
        *,
        crypto_research: dict[str, Any],
        market_universe: dict[str, list[str]],
        max_items: int,
        provided_candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(crypto_research, dict):
            return crypto_research, {"book_enriched_count": 0, "book_errors": []}

        next_context = dict(crypto_research)
        items = [
            dict(row)
            for row in crypto_research.get("items") or []
            if isinstance(row, dict)
        ]
        candidates = [
            dict(row)
            for row in crypto_research.get("candidates") or []
            if isinstance(row, dict)
        ]
        feature_by_symbol: dict[str, dict[str, Any]] = {}
        for item in items:
            symbol = str(item.get("symbol") or "").upper().strip()
            features = item.get("features") if isinstance(item.get("features"), dict) else {}
            if symbol:
                feature_by_symbol[symbol] = dict(features)
        feature_by_key: dict[tuple[str, str], dict[str, Any]] = {}

        def feature_source_symbol(symbol: str, market: str) -> str:
            return upbit_market_to_usdt_symbol(symbol) if market == UPBIT_SPOT_MARKET else symbol

        def failed_book_features(symbol: str, market: str) -> dict[str, Any]:
            features = dict(feature_by_symbol.get(feature_source_symbol(symbol, market), {}))
            for field in BOOK_FIELD_KEYS:
                features.pop(field, None)
            features.update(
                {
                    "bid_price": 0.0,
                    "ask_price": 0.0,
                    "spread_bps": 0.0,
                    "book_source": "",
                    "book_fetched_at": "",
                    "book_market": market,
                    "book_fresh": False,
                }
            )
            return features

        targets: list[tuple[str, str]] = []
        provided_seen: set[tuple[str, str, str, str]] = set()
        max_provided_targets = max(int(max_items), 1)
        for row in provided_candidates:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            market = normalize_market(row.get("market") or row.get("venue"))
            if market == UPBIT_SPOT_MARKET:
                symbol = upbit_market_symbol(symbol)
            side = normalize_position_side(row.get("side") or row.get("stance"))
            horizon = normalize_binance_horizon(row.get("horizon"), market=market)
            key = (symbol, market, side, horizon)
            if key in provided_seen:
                continue
            provided_seen.add(key)
            targets.append((symbol, market))
            if len(provided_seen) >= max_provided_targets:
                break

        for row in candidates:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            features = feature_by_symbol.get(symbol, {})
            side = build_side_from_crypto_research_candidate(row)
            market = build_market_from_crypto_research_candidate(
                candidate=row,
                features=features,
                side=side,
            )
            if symbol in set(market_universe.get(market) or []):
                targets.append((symbol, market))
            upbit_symbol = upbit_market_symbol(symbol)
            if side == "long" and upbit_symbol in set(market_universe.get(UPBIT_SPOT_MARKET) or []):
                targets.append((upbit_symbol, UPBIT_SPOT_MARKET))
            if (
                market == "spot"
                and side == "long"
                and build_candidate_derivatives_available(features)
                and symbol in set(market_universe.get("futures") or [])
            ):
                targets.append((symbol, "futures"))
            if (
                market == "futures"
                and side == "long"
                and build_candidate_is_explicit_long_candidate(row)
                and symbol in set(market_universe.get("spot") or [])
            ):
                targets.append((symbol, "spot"))

        enriched = 0
        errors: list[dict[str, Any]] = []
        for symbol, market in list(dict.fromkeys(targets)):
            try:
                book = await self._fetch_book_ticker(symbol=symbol, market=market)
            except Exception as exc:
                feature_by_key[(symbol, market)] = failed_book_features(symbol, market)
                errors.append({"symbol": symbol, "market": market, "error": str(exc)})
                continue
            bid = _safe_float(book.get("bid_price") or book.get("bid"))
            ask = _safe_float(book.get("ask_price") or book.get("ask"))
            spread = _safe_float(book.get("spread_bps"))
            if bid <= 0 or ask <= 0 or ask <= bid:
                feature_by_key[(symbol, market)] = failed_book_features(symbol, market)
                error = (
                    "book_crossed"
                    if bid > 0 and ask > 0 and ask <= bid
                    else str(book.get("error_message") or "book_bid_ask_missing")
                )
                errors.append({"symbol": symbol, "market": market, "error": error})
                continue
            features = dict(feature_by_symbol.get(feature_source_symbol(symbol, market), {}))
            book_price = _safe_float(book.get("price") or book.get("last_price") or book.get("current_price"))
            if book_price <= 0:
                book_price = (bid + ask) / 2.0
            if candidate_last_price(candidate={}, features=features) <= 0 and book_price > 0:
                features.update(
                    {
                        "price": book_price,
                        "last_price": book_price,
                        "current_price": book_price,
                    }
                )
            features.update(
                {
                    "bid_price": bid,
                    "ask_price": ask,
                    "spread_bps": spread,
                    "book_source": book.get("source") or "book_ticker",
                    "book_fetched_at": book.get("fetched_at") or utc_now_iso(),
                    "book_market": market,
                    "book_fresh": True,
                }
            )
            feature_by_key[(symbol, market)] = features
            enriched += 1

        for item in items:
            symbol = str(item.get("symbol") or "").upper().strip()
            if symbol in feature_by_symbol:
                item["features"] = feature_by_symbol[symbol]
        next_context["items"] = items
        next_context["candidates"] = candidates
        next_context[BOOK_MARKET_FEATURES_KEY] = feature_by_key
        return next_context, {
            "book_enriched_count": enriched,
            "book_errors": errors[:8],
        }

    def _manager_executable_candidates(
        self,
        *,
        provided_candidates: list[dict[str, Any]],
        crypto_research: dict[str, Any],
        crypto_patterns: dict[str, Any],
        live_authority: dict[str, Any],
        market_universe: dict[str, list[str]],
        account: dict[str, Any],
        active_blocks: list[dict[str, Any]] | None = None,
        entry_gate_policy: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        max_items = max(
            int(self.config.max_manager_symbols),
            int(self.config.quant_context_limit),
            8,
        )
        spot_shadow_count = 0
        futures_shadow_count = 0
        upbit_shadow_count = 0

        feature_index = build_crypto_research_feature_index(crypto_research)
        market_feature_index = build_crypto_research_market_feature_index(crypto_research)
        candidates, skipped, seen = build_provided_manager_candidates(
            provided_candidates=provided_candidates,
            feature_index=feature_index,
            market_feature_index=market_feature_index,
            crypto_patterns=crypto_patterns,
            live_authority=live_authority,
            account=account,
            hooks=BinanceProvidedCandidateBuildHooks(
                pattern_prior_for_candidate=self._pattern_prior_for_candidate,
                design_price_plan=self._design_crypto_candidate_price_plan,
                merge_candidate_price_plan=self._merge_candidate_price_plan,
            ),
        )

        research_candidates, research_skipped, seen, shadow_counts = (
            build_research_manager_candidates(
                research_candidates=crypto_research.get("candidates") or [],
                candidate_packets=(
                    crypto_research.get("candidate_packets")
                    if isinstance(crypto_research.get("candidate_packets"), dict)
                    else {}
                ),
                feature_index=feature_index,
                market_feature_index=market_feature_index,
                crypto_patterns=crypto_patterns,
                live_authority=live_authority,
                market_universe=market_universe,
                account=account,
                seen=seen,
                max_items=max_items,
                current_candidate_count=len(candidates),
                hooks=BinanceResearchCandidateBuildHooks(
                    pattern_prior_for_candidate=self._pattern_prior_for_candidate,
                    design_price_plan=self._design_crypto_candidate_price_plan,
                    merge_candidate_price_plan=self._merge_candidate_price_plan,
                    cash_reference_usdt=self._cash_reference_usdt,
                    volatile_attack_context=self._volatile_attack_context,
                ),
            )
        )
        candidates.extend(research_candidates)
        skipped.extend(research_skipped)
        spot_shadow_count += _safe_int(shadow_counts.get("spot_shadow_count"))
        futures_shadow_count += _safe_int(shadow_counts.get("futures_shadow_count"))
        upbit_shadow_count += _safe_int(shadow_counts.get("upbit_shadow_count"))

        return build_finalize_manager_candidates(
            candidates=candidates,
            hooks=BinanceManagerCandidateFinalizeHooks(
                candidate_near_duplicate_active_block_context=(
                    self._candidate_near_duplicate_active_block_context
                ),
                candidate_lane_authority_context=self._candidate_lane_authority_context,
                manager_candidate_empirical_edge_score=(
                    self._manager_candidate_empirical_edge_score
                ),
                candidate_execution_blocker_context=(
                    self._candidate_execution_blocker_context
                ),
                annotate_candidate_pattern_performance=(
                    self._annotate_candidate_pattern_performance
                ),
                rank_manager_candidates_by_edge=self._rank_manager_candidates_by_edge,
                diversify_manager_candidates_by_lane=(
                    self._diversify_manager_candidates_by_lane
                ),
                lane_distribution=self._lane_distribution,
                manager_candidate_packets=self._manager_candidate_packets,
                manager_candidate_stage_counts=self._manager_candidate_stage_counts,
                market_side_lane=binance_market_side_lane,
            ),
            max_items=max_items,
            active_blocks=active_blocks,
            live_authority=live_authority,
            entry_gate_policy=entry_gate_policy,
            crypto_research=crypto_research,
            crypto_patterns=crypto_patterns,
            market_universe=market_universe,
            provided_candidate_count=len(provided_candidates),
            spot_shadow_count=spot_shadow_count,
            futures_shadow_count=futures_shadow_count,
            upbit_shadow_count=upbit_shadow_count,
            skipped=skipped,
        )

    def _manager_candidate_stage_counts(
        self,
        *,
        crypto_research: dict[str, Any],
        market_universe: dict[str, list[str]],
        selected_candidates: list[dict[str, Any]],
    ) -> dict[str, int]:
        observed = _safe_int(
            crypto_research.get("observed_symbol_count")
            or crypto_research.get("observe_universe_count")
        )
        if observed <= 0:
            observed = len(
                {
                    symbol
                    for symbols in market_universe.values()
                    for symbol in (symbols or [])
                    if symbol
                }
            )
        research_count = _safe_int(
            crypto_research.get("research_universe_count")
            or crypto_research.get("focus_symbol_count")
        )
        if research_count <= 0:
            research_count = max(
                _context_list_count(crypto_research, "items"),
                _context_list_count(crypto_research, "candidates"),
            )
        return {
            "observe_universe": observed,
            "research_universe": research_count,
            "manager_candidates": len(selected_candidates),
            "trade_candidates": sum(1 for row in selected_candidates if row.get("calculated")),
        }

    @staticmethod
    def _compact_manager_candidate_for_prompt(row: dict[str, Any]) -> dict[str, Any]:
        return build_compact_manager_candidate_for_prompt(
            row,
            compact_value=build_compact_prompt_value,
            clean_text=lambda value, limit: _clean_text(value, limit=limit),
            score_candidate=score_crypto_candidate,
        )

    def _manager_candidate_packets(
        self,
        *,
        crypto_research: dict[str, Any],
        selected_candidates: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        return build_manager_candidate_packets(
            crypto_research=crypto_research,
            selected_candidates=selected_candidates,
            compact_value=build_compact_prompt_value,
            volatile_attack_context=self._volatile_attack_context,
            volatile_candidate_limit=self.config.volatile_attack_candidate_limit,
        )

    @staticmethod
    def _pattern_prior_for_candidate(
        *,
        crypto_patterns: dict[str, Any],
        symbol: str,
        side: str,
    ) -> dict[str, Any]:
        rows = (
            crypto_patterns.get("optimized_strategy_sets")
            if isinstance(crypto_patterns, dict)
            else []
        )
        if not isinstance(rows, list):
            return {}
        normalized_symbol = str(symbol or "").upper().strip()
        normalized_side = normalize_position_side(side)
        matches: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper().strip() != normalized_symbol:
                continue
            direction = str(row.get("direction") or "").strip().lower()
            if not direction and row.get("pattern_key"):
                parts = str(row.get("pattern_key") or "").split(":")
                if len(parts) >= 2:
                    direction = parts[1].strip().lower()
            if normalize_position_side(direction or normalized_side) != normalized_side:
                continue
            matches.append(row)
        if not matches:
            return {}
        best = max(matches, key=lambda item: _safe_float(item.get("objective_score")))
        params = best.get("parameter_set") if isinstance(best.get("parameter_set"), dict) else {}
        return {
            "set_id": _clean_text(best.get("set_id"), limit=80),
            "symbol": normalized_symbol,
            "pattern_key": _clean_text(best.get("pattern_key"), limit=120),
            "direction": normalized_side,
            "interval": _clean_text(best.get("interval"), limit=20),
            "objective": _clean_text(best.get("objective"), limit=80),
            "objective_score": _safe_float(best.get("objective_score")),
            "trade_count": _safe_int(best.get("trade_count")),
            "win_rate": _safe_float(best.get("win_rate")),
            "expectancy_r": _safe_float(best.get("expectancy_r")),
            "profit_factor": _safe_float(best.get("profit_factor")),
            "max_loss_r": _safe_float(best.get("max_loss_r")),
            "walk_forward_quality": (
                best.get("walk_forward_quality")
                if isinstance(best.get("walk_forward_quality"), dict)
                else {}
            ),
            "out_of_sample_trade_count": _safe_int(best.get("out_of_sample_trade_count")),
            "out_of_sample_expectancy_r": _safe_float(best.get("out_of_sample_expectancy_r")),
            "out_of_sample_profit_factor": _safe_float(best.get("out_of_sample_profit_factor")),
            "overfit_risk": _clean_text(best.get("overfit_risk"), limit=40),
            "parameter_set": {
                "stop_pct": _safe_float(params.get("stop_pct")),
                "target_pct": _safe_float(params.get("target_pct")),
                "holding_bars": _safe_int(params.get("holding_bars")),
            },
            "promoted_at": _clean_text(best.get("promoted_at"), limit=80),
        }

    @staticmethod
    def _pattern_prior_quality(pattern_prior: dict[str, Any]) -> dict[str, Any]:
        if not pattern_prior:
            return {"passed": False, "failed": ["missing_pattern_prior"]}
        failed: list[str] = []
        if _safe_int(pattern_prior.get("trade_count")) < 8:
            failed.append("trade_count")
        if _safe_float(pattern_prior.get("win_rate")) < 0.50:
            failed.append("win_rate")
        if _safe_float(pattern_prior.get("expectancy_r")) <= 0:
            failed.append("expectancy_r")
        if _safe_float(pattern_prior.get("profit_factor")) < 1.05:
            failed.append("profit_factor")
        if _safe_float(pattern_prior.get("objective_score")) <= 0:
            failed.append("objective_score")
        walk_forward_quality = (
            pattern_prior.get("walk_forward_quality")
            if isinstance(pattern_prior.get("walk_forward_quality"), dict)
            else {}
        )
        if walk_forward_quality and not bool(walk_forward_quality.get("passed")):
            failed.append("walk_forward_failed")
        params = pattern_prior.get("parameter_set") if isinstance(pattern_prior.get("parameter_set"), dict) else {}
        if _safe_float(params.get("stop_pct")) <= 0 or _safe_float(params.get("target_pct")) <= 0:
            failed.append("price_geometry")
        return {"passed": not failed, "failed": failed}

    @staticmethod
    def _pattern_live_crosscheck(
        *,
        pattern_prior: dict[str, Any],
        prior_quality: dict[str, Any],
        features: dict[str, Any],
        market: str,
        side: str,
        live_authority: dict[str, Any],
    ) -> dict[str, Any]:
        if not pattern_prior:
            return {
                "status": "no_pattern_prior",
                "immediate_block_allowed": False,
                "recommended_entry_mode": "research_only",
                "checks": ["missing_pattern_prior"],
            }
        checks: list[str] = []
        waits: list[str] = []
        contradictions: list[str] = []
        if not bool(prior_quality.get("passed")):
            contradictions.extend(
                str(item) for item in prior_quality.get("failed", []) if item
            )

        book_fresh = bool(features.get("book_fresh"))
        spread_bps = _safe_float(features.get("spread_bps"))
        bid = _safe_float(features.get("bid_price") or features.get("bid"))
        ask = _safe_float(features.get("ask_price") or features.get("ask"))
        if spread_bps <= 0 and bid > 0 and ask > 0:
            spread_bps = max((ask - bid) / ((ask + bid) / 2) * 10_000, 0.0)
        max_spread_bps = WAITING_ENTRY_PREFLIGHT_MAX_SPREAD_BPS
        if not book_fresh:
            waits.append("book_not_fresh")
        elif spread_bps > max_spread_bps:
            waits.append("spread_too_wide")
        else:
            checks.append("book_spread_ok")

        funding_rate = _safe_float(features.get("funding_rate"))
        if market == "futures":
            if side == "long" and funding_rate > 0.0005:
                waits.append("funding_costly_for_long")
            elif side == "short" and funding_rate < -0.0005:
                waits.append("funding_costly_for_short")
            else:
                checks.append("funding_acceptable")

        authority_status = str(live_authority.get("status") or "").strip().lower()
        authority_grade = str(
            live_authority.get("live_grade") or live_authority.get("grade") or ""
        ).strip().lower()
        authority_label = authority_grade or authority_status
        if authority_status in {"missing", "error"}:
            waits.append(f"live_authority_{authority_status}")
        elif authority_label in {"observe_only", "restricted"}:
            waits.append(f"live_authority_{authority_label}")
        else:
            checks.append("live_authority_available")
        active_revision_reason = BinanceBlockTrader._active_revision_waiting_entry_reason(
            live_authority
        )
        if active_revision_reason:
            waits.append(active_revision_reason)
        if not bool(live_authority.get("allow_scale_up")):
            checks.append("scale_up_not_allowed")
        validation_gate = BinanceBlockTrader._live_authority_validation_gate(live_authority)
        gate_status = validation_gate["status"]
        if gate_status and gate_status != "clear":
            reason = f"live_authority_{gate_status}"
            if gate_status in LIVE_AUTHORITY_VALIDATION_WAIT_ONLY_STATUSES:
                waits.append(reason)
            else:
                contradictions.append(reason)

        if contradictions:
            status = "contradicted"
            recommended_entry_mode = "reject_or_hold"
        elif waits:
            status = "wait"
            recommended_entry_mode = "wait_for_price"
        else:
            status = "aligned"
            recommended_entry_mode = "immediate_or_waiting_entry"
        return {
            "status": status,
            "immediate_block_allowed": status == "aligned",
            "recommended_entry_mode": recommended_entry_mode,
            "checks": checks,
            "wait_reasons": waits,
            "contradictions": contradictions,
            "spread_bps": spread_bps,
            "max_spread_bps": max_spread_bps,
            "funding_rate": funding_rate,
            "live_authority": {
                "status": live_authority.get("status"),
                "live_grade": live_authority.get("live_grade"),
                "allow_scale_up": bool(live_authority.get("allow_scale_up")),
                "max_budget_multiplier": live_authority.get("max_budget_multiplier"),
                "scorecard_count": live_authority.get("scorecard_count"),
                "validation_gate_status": validation_gate["status"],
                "validation_readiness": validation_gate["readiness"],
                "validation_gate_reason": validation_gate["reason"],
            },
        }

    def _design_crypto_candidate_price_plan(
        self,
        *,
        candidate: dict[str, Any],
        features: dict[str, Any],
        market: str,
        side: str,
        horizon: str,
        account: dict[str, Any],
        pattern_prior: dict[str, Any] | None = None,
        live_authority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_design_crypto_candidate_price_plan(
            candidate=candidate,
            features=features,
            market=market,
            side=side,
            horizon=horizon,
            account=account,
            config=self.config,
            min_reward_risk_floor=_safe_float(
                getattr(getattr(self.risk_sizer, "config", None), "min_reward_risk", 0.0)
            ),
            pattern_prior=pattern_prior,
            live_authority=live_authority,
            volatile_context_builder=self._volatile_attack_context,
            pattern_prior_quality=self._pattern_prior_quality,
            pattern_live_crosscheck=self._pattern_live_crosscheck,
            live_authority_validation_gate=self._live_authority_validation_gate,
            quote_budget_details=self._candidate_quote_budget_details,
            quote_budget_details_from_amount=self._quote_budget_details_from_amount,
            cash_reference_usdt=self._cash_reference_usdt,
        )

    def _volatile_attack_context(
        self,
        *,
        candidate: dict[str, Any],
        features: dict[str, Any],
        spread_bps: float,
        change_pct_24h: float,
        market: str,
    ) -> dict[str, Any]:
        return build_volatile_attack_context(
            candidate=candidate,
            features=features,
            spread_bps=spread_bps,
            change_pct_24h=change_pct_24h,
            market=market,
            enabled=bool(self.config.volatile_attack_enabled),
            min_change_pct=_safe_float(self.config.volatile_attack_min_change_pct),
            min_volume_expansion=_safe_float(
                self.config.volatile_attack_min_volume_expansion
            ),
        )

    def _candidate_quote_budget_details(
        self,
        *,
        market: str,
        account: dict[str, Any],
        side: str = "",
        lane: str = "",
    ) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        if normalized_market == UPBIT_SPOT_MARKET:
            performance_multiplier = self._candidate_budget_performance_multiplier(
                market=normalized_market,
                side=side,
                lane=lane,
            )
            return build_candidate_upbit_quote_budget_details(
                cash_krw=_safe_float(account.get("upbit_cash_krw")),
                cash_usdt=_safe_float(account.get("upbit_cash_usdt")),
                upbit_usdt_krw_rate=_safe_float(self.config.upbit_usdt_krw_rate),
                quote_budget_pct=_safe_float(self.config.upbit_quote_budget_pct),
                min_quote_budget_krw=_safe_float(self.config.upbit_min_quote_budget_krw),
                max_quote_budget_krw=_safe_float(self.config.upbit_max_quote_budget_krw),
                performance_multiplier=performance_multiplier,
            )
        performance_multiplier = self._candidate_budget_performance_multiplier(
            market=normalized_market,
            side=side,
            lane=lane,
        )
        budget_usdt = self._candidate_quote_budget_usdt(
            market=normalized_market,
            account=account,
            side=side,
            lane=lane,
        )
        details = self._quote_budget_details_from_amount(
            market=normalized_market,
            quote_budget=budget_usdt,
        )
        details["performance_budget_multiplier"] = round(performance_multiplier, 4)
        return details

    def _quote_budget_details_from_amount(self, *, market: str, quote_budget: float) -> dict[str, Any]:
        return build_quote_budget_details_from_amount(
            market=market,
            quote_budget=quote_budget,
            upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )

    def _candidate_quote_budget_usdt(
        self,
        *,
        market: str,
        account: dict[str, Any],
        side: str = "",
        lane: str = "",
    ) -> float:
        cash = self._cash_reference_usdt(market=market, account=account)
        normalized_market = normalize_market(market)
        multiplier = self._candidate_budget_performance_multiplier(
            market=normalized_market,
            side=side,
            lane=lane,
        )
        return build_candidate_quote_budget_usdt(
            market=normalized_market,
            cash_usdt=cash,
            futures_quote_budget_pct=self.config.futures_quote_budget_pct,
            futures_min_quote_budget_usdt=self.config.futures_min_quote_budget_usdt,
            futures_max_quote_budget_usdt=self.config.futures_max_quote_budget_usdt,
            spot_quote_budget_pct=self.config.spot_quote_budget_pct,
            spot_min_quote_budget_usdt=self.config.spot_min_quote_budget_usdt,
            spot_max_quote_budget_usdt=self.config.spot_max_quote_budget_usdt,
            performance_multiplier=multiplier,
            min_notional_usdt=SPOT_ADOPTION_MIN_NOTIONAL_USDT,
        )

    def _candidate_budget_performance_multiplier(
        self,
        *,
        market: str,
        side: str = "",
        lane: str = "",
    ) -> float:
        if not bool(self.config.budget_performance_scale_enabled):
            return 1.0
        try:
            performance = self.repository.latest_performance_scorecard(limit=20)
        except Exception:
            logger.warning("failed to load Binance performance scorecard for sizing", exc_info=True)
            return 1.0
        scoped = self._performance_scope_for_budget(
            performance,
            market=market,
            side=side,
            lane=lane,
        )
        if not scoped:
            return 1.0
        sample_count = _safe_int(scoped.get("sample_count"))
        de_risk_multiplier = self._scorecard_budget_de_risk_multiplier(scoped)
        if de_risk_multiplier < 1.0:
            return de_risk_multiplier
        scale_min_samples = max(_safe_int(self.config.budget_performance_scale_min_samples), 1)
        if sample_count < scale_min_samples:
            return 1.0
        win_rate = _safe_float(scoped.get("win_rate_pct"))
        realized_pnl = _safe_float(scoped.get("realized_pnl_usdt"))
        avg_r = _safe_float(scoped.get("avg_r_multiple"))
        profit_factor = _safe_float(scoped.get("profit_factor"))
        max_drawdown_r = _safe_float(scoped.get("max_drawdown_r_multiple"))
        recovery_factor = _safe_float(scoped.get("recovery_factor"))
        threshold = _safe_float(self.config.budget_performance_scale_win_rate_pct)
        if win_rate < threshold or realized_pnl <= 0 or avg_r <= 0:
            return 1.0
        if profit_factor < 1.2:
            return 1.0
        if sample_count >= 3 and recovery_factor < 1.0:
            return 1.0
        if max_drawdown_r <= -2.0:
            return 1.0
        if not self._long_window_budget_scale_confirmed(
            market=market,
            side=side,
            lane=lane,
            min_samples=scale_min_samples,
            win_rate_threshold=threshold,
        ):
            return 1.0
        multiplier = _safe_float(self.config.budget_performance_scale_multiplier)
        return min(max(multiplier, 1.0), 3.0)

    def _scorecard_budget_de_risk_multiplier(self, scorecard: dict[str, Any]) -> float:
        sample_count = _safe_int(scorecard.get("sample_count"))
        min_samples = max(_safe_int(self.config.lane_performance_min_samples), 1)
        if sample_count < min_samples:
            return 1.0
        pnl = _safe_float(scorecard.get("pnl_usdt") or scorecard.get("realized_pnl_usdt"))
        win_rate = _safe_float(scorecard.get("win_rate_pct"))
        avg_r = _safe_float(scorecard.get("avg_r_multiple"))
        profit_factor = _safe_float(scorecard.get("profit_factor"))
        max_drawdown_r = _safe_float(scorecard.get("max_drawdown_r_multiple"))
        recovery_factor = _safe_float(scorecard.get("recovery_factor"))
        if not (
            pnl < 0
            or avg_r < 0
            or win_rate <= 35.0
            or profit_factor < 1.0
            or recovery_factor <= 0.0
            or max_drawdown_r <= -3.0
        ):
            return 1.0
        multiplier = _safe_float(self.config.lane_performance_loss_multiplier)
        return min(max(multiplier or 0.5, 0.05), 1.0)

    @staticmethod
    def _scorecard_allows_budget_scale(
        scorecard: dict[str, Any],
        *,
        min_samples: int,
        win_rate_threshold: float,
    ) -> bool:
        return build_scorecard_allows_budget_scale(
            scorecard,
            min_samples=min_samples,
            win_rate_threshold=win_rate_threshold,
        )

    def _long_window_budget_scale_confirmed(
        self,
        *,
        market: str,
        side: str = "",
        lane: str = "",
        min_samples: int,
        win_rate_threshold: float,
    ) -> bool:
        feedback_limit = self._performance_scorecard_feedback_limit()
        if feedback_limit <= 20:
            return True
        try:
            long_performance = self.repository.latest_performance_scorecard(
                limit=feedback_limit
            )
        except Exception:
            logger.warning(
                "failed to load Binance long-window performance for budget scale",
                exc_info=True,
            )
            return False
        scoped = self._performance_scope_for_budget(
            long_performance,
            market=market,
            side=side,
            lane=lane,
        )
        if not scoped:
            return False
        return self._scorecard_allows_budget_scale(
            scoped,
            min_samples=min_samples,
            win_rate_threshold=win_rate_threshold,
        )

    @staticmethod
    def _budget_scope_from_scorecard_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return build_budget_scope_from_scorecard_rows(rows)

    @staticmethod
    def _performance_scope_for_budget(
        performance: dict[str, Any],
        *,
        market: str,
        side: str = "",
        lane: str = "",
    ) -> dict[str, Any]:
        return build_performance_scope_for_budget(
            performance,
            market=market,
            side=side,
            lane=lane,
        )

    def _cash_reference_usdt(self, *, market: str, account: dict[str, Any]) -> float:
        return build_cash_reference_usdt(
            market=market,
            account=account,
            upbit_usdt_krw_rate=self.config.upbit_usdt_krw_rate,
        )

    @staticmethod
    def _merge_candidate_price_plan(
        *,
        candidate: dict[str, Any],
        symbol: str,
        market: str,
        side: str,
        horizon: str,
        price_plan: dict[str, Any],
    ) -> dict[str, Any]:
        return build_merge_manager_candidate_price_plan(
            candidate=candidate,
            symbol=symbol,
            market=market,
            side=side,
            horizon=horizon,
            price_plan=price_plan,
            score_candidate=BinanceBlockTrader._manager_candidate_empirical_edge_score,
        )

    def _normalize_block_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["symbol"] = str(payload.get("symbol") or "").strip().upper()
        normalized["market"] = normalize_market(payload.get("market") or payload.get("venue"))
        if normalized["market"] == UPBIT_SPOT_MARKET:
            normalized["symbol"] = upbit_market_symbol(normalized["symbol"])
        normalized["side"] = normalize_position_side(payload.get("side"))
        metadata = dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
        metadata["market"] = normalized["market"]
        metadata["side"] = normalized["side"]
        metadata.setdefault("live_authority", self._live_authority_metadata())
        metadata.setdefault("strategy_revision_id", self.config.strategy_revision_id)
        for key in (
            "calculated_price_plan",
            "jue_price_override",
            "override_reason",
            "quote_budget",
            "quote_currency",
            "quote_budget_usdt",
            "quote_budget_krw",
            "confidence",
            "evidence_refs",
            "pattern_live_crosscheck",
            "pattern_prior",
        ):
            if key in payload and key not in metadata:
                metadata[key] = payload.get(key)
        qty_hint = payload.get("qty")
        if isinstance(qty_hint, str) and _safe_float(qty_hint) <= 0:
            clean_hint = qty_hint.strip()
            if clean_hint:
                metadata.setdefault("quantity_hint", clean_hint)
            hinted_quote_budget_krw = _extract_labeled_float(
                qty_hint,
                ("quote_budget_krw", "krw_budget"),
            )
            hinted_quote_budget_usdt = _extract_labeled_float(
                qty_hint,
                ("quote_budget_usdt", "usdt_budget"),
            )
            hinted_quote_budget = _extract_labeled_float(
                qty_hint,
                ("quote_budget", "budget"),
            )
            hinted_qty = _extract_labeled_float(
                qty_hint,
                ("approx_qty", "quantity", "qty"),
            )
            if hinted_quote_budget_krw > 0:
                metadata.setdefault("quote_budget_krw", hinted_quote_budget_krw)
                normalized.setdefault("quote_budget_krw", hinted_quote_budget_krw)
            if hinted_quote_budget_usdt > 0:
                metadata.setdefault("quote_budget_usdt", hinted_quote_budget_usdt)
                normalized.setdefault("quote_budget_usdt", hinted_quote_budget_usdt)
            if hinted_quote_budget > 0:
                metadata.setdefault("quote_budget", hinted_quote_budget)
                normalized.setdefault("quote_budget", hinted_quote_budget)
            if hinted_qty > 0:
                metadata.setdefault("approx_qty", hinted_qty)
        horizon = normalize_binance_horizon(
            payload.get("horizon") or metadata.get("horizon"),
            market=normalized["market"],
        )
        metadata["horizon"] = horizon
        metadata["block_color"] = BINANCE_HORIZON_COLORS.get(horizon, horizon)
        metadata["lane"] = normalize_binance_display_lane(
            lane=payload.get("lane") or metadata.get("lane"),
            market=normalized["market"],
            horizon=horizon,
            side=normalized["side"],
        )
        entry_style = str(
            payload.get("entry_style") or metadata.get("entry_style") or ""
        ).strip().lower()
        raw_trigger_price = (
            payload.get("entry_trigger_price")
            or payload.get("trigger_price")
            or metadata.get("entry_trigger_price")
        )
        trigger_price = _safe_float(raw_trigger_price)
        if entry_style in {"wait_for_price", "waiting_entry", "triggered_entry"} or trigger_price > 0:
            metadata["entry_style"] = "wait_for_price"
            if trigger_price <= 0:
                trigger_price = _safe_float(payload.get("entry_price") or payload.get("entry_price_usdt"))
            if trigger_price > 0:
                metadata["entry_trigger_price"] = trigger_price
                normalized["entry_price"] = trigger_price
            metadata["entry_trigger_operator"] = build_normalize_entry_trigger_operator(
                payload.get("entry_trigger_operator")
                or payload.get("trigger_operator")
                or metadata.get("entry_trigger_operator"),
                default="<=" if normalized["side"] == "long" else ">=",
            )
            metadata.setdefault("entry_trigger_status", "waiting")
        normalized["metadata"] = metadata
        if "entry_price" not in normalized and "entry_price_usdt" in payload:
            normalized["entry_price"] = payload.get("entry_price_usdt")
        if "target_price" not in normalized and "target_price_usdt" in payload:
            normalized["target_price"] = payload.get("target_price_usdt")
        if "stop_price" not in normalized and "stop_price_usdt" in payload:
            normalized["stop_price"] = payload.get("stop_price_usdt")
        if "qty_initial" not in normalized and "qty" in normalized:
            qty_value = _safe_float(payload.get("qty"))
            if qty_value > 0:
                normalized["qty_initial"] = qty_value
        if "qty_initial" not in normalized and normalized["market"] == UPBIT_SPOT_MARKET:
            entry = _safe_float(normalized.get("entry_price"))
            budget = _safe_float(
                payload.get("quote_budget_krw")
                or payload.get("quote_budget")
                or metadata.get("quote_budget_krw")
                or metadata.get("quote_budget")
            )
            if entry > 0 and budget > 0:
                normalized["qty_initial"] = budget / entry
        if "qty_initial" not in normalized and _safe_float(metadata.get("approx_qty")) > 0:
            normalized["qty_initial"] = _safe_float(metadata.get("approx_qty"))
        if "qty_initial" not in normalized and (
            "quote_budget_usdt" in payload
            or "quote_budget_usdt" in normalized
            or "quote_budget_usdt" in metadata
        ):
            entry = _safe_float(normalized.get("entry_price"))
            budget = _safe_float(
                payload.get("quote_budget_usdt")
                or normalized.get("quote_budget_usdt")
                or metadata.get("quote_budget_usdt")
            )
            if entry > 0 and budget > 0:
                normalized["qty_initial"] = budget / entry
        if normalized.get("market") == "futures":
            normalized["margin_type"] = str(payload.get("margin_type") or "isolated").strip().lower()
            normalized["leverage"] = max(_safe_int(payload.get("leverage") or 1), 1)
        return normalized

    def _is_waiting_entry_payload(self, payload: dict[str, Any]) -> bool:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        style = str(payload.get("entry_style") or metadata.get("entry_style") or "").strip().lower()
        if style in {"wait_for_price", "waiting_entry", "triggered_entry"}:
            return True
        return _safe_float(
            payload.get("entry_trigger_price")
            or payload.get("trigger_price")
            or metadata.get("entry_trigger_price")
        ) > 0

    def _validate_block(self, payload: dict[str, Any]) -> None:
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            raise ValueError("symbol is required")
        market = normalize_market(payload.get("market"))
        self._validate_market_universe(symbol=symbol, market=market)
        qty = _safe_float(payload.get("qty_initial") or payload.get("qty"))
        if qty <= 0:
            raise ValueError("qty must be positive")
        self._validate_price_direction(payload)
        if market in {"spot", UPBIT_SPOT_MARKET}:
            if normalize_position_side(payload.get("side")) == "short":
                raise ValueError(f"{market} blocks only support long side")
            if market == UPBIT_SPOT_MARKET:
                self._validate_upbit_price_scale(payload)
            return
        margin_type = str(payload.get("margin_type") or "").strip().lower()
        if margin_type != "isolated":
            raise ValueError("futures blocks require isolated margin")
        leverage = max(_safe_int(payload.get("leverage") or 1), 1)
        if leverage > max(int(self.config.max_futures_leverage), 1):
            raise ValueError("futures leverage exceeds configured maximum")
        status = str(payload.get("status") or "proposed").strip().lower()
        if (
            status in {"open", "entry_pending", "exit_pending"}
            or self.config.execute_futures_orders
        ) and (
            _safe_float(payload.get("entry_price")) <= 0
            or _safe_float(payload.get("liquidation_price")) <= 0
        ):
            raise ValueError("active futures blocks require liquidation distance inputs")
        self._validate_liquidation_distance(payload)

    def _validate_market_universe(self, *, symbol: str, market: str) -> None:
        universe = self._runtime_market_universe.get(market) or self._default_market_universe().get(market, [])
        if universe and symbol.upper() not in set(universe):
            raise ValueError(f"{market}_symbol_not_in_universe:{symbol.upper()}")

    def _validate_price_direction(self, payload: dict[str, Any]) -> None:
        entry = _safe_float(payload.get("entry_price"))
        target = _safe_float(payload.get("target_price"))
        stop = _safe_float(payload.get("stop_price"))
        if entry <= 0 or target <= 0 or stop <= 0:
            return
        side = normalize_position_side(payload.get("side"))
        if side == "short":
            if not (target < entry < stop):
                raise ValueError("invalid price direction for short block")
            return
        if str(payload.get("created_by") or "").strip().lower() in {
            "wallet_adoption",
            "existing_position",
        }:
            if not (entry < target and stop < target):
                raise ValueError("invalid price direction for adopted long block")
            return
        if not (stop < entry < target):
            raise ValueError("invalid price direction for long block")

    def _validate_upbit_price_scale(self, payload: dict[str, Any]) -> None:
        reference = self._upbit_price_scale_reference(payload)
        if reference <= 0:
            return
        for field in ("entry_price", "target_price", "stop_price"):
            price = _safe_float(payload.get(field))
            if price <= 0:
                continue
            ratio = price / reference
            if (
                ratio < UPBIT_PRICE_SCALE_MIN_RATIO
                or ratio > UPBIT_PRICE_SCALE_MAX_RATIO
            ):
                raise ValueError(
                    "upbit_spot_price_scale_mismatch:"
                    f"{str(payload.get('symbol') or '').upper()}:{field}:"
                    f"{price:g}:reference:{reference:g}"
                )

    def _upbit_price_scale_reference(self, payload: dict[str, Any]) -> float:
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            return 0.0
        quotes = self.repository.latest_quotes_for_blocks(
            [{"symbol": symbol, "market": UPBIT_SPOT_MARKET}]
        )
        quote = quotes.get((UPBIT_SPOT_MARKET, symbol), {})
        reference = _safe_float(quote.get("price"))
        if reference > 0:
            return reference
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        calculated = (
            metadata.get("calculated_price_plan")
            if isinstance(metadata.get("calculated_price_plan"), dict)
            else payload.get("calculated_price_plan")
            if isinstance(payload.get("calculated_price_plan"), dict)
            else payload.get("calculated")
            if isinstance(payload.get("calculated"), dict)
            else {}
        )
        market_inputs = (
            calculated.get("market_inputs")
            if isinstance(calculated.get("market_inputs"), dict)
            else metadata.get("market_inputs")
            if isinstance(metadata.get("market_inputs"), dict)
            else {}
        )
        for key in ("price", "last_price", "current_price"):
            reference = _safe_float(market_inputs.get(key))
            if reference > 0:
                return reference
        bid = _safe_float(market_inputs.get("bid_price") or market_inputs.get("bid"))
        ask = _safe_float(market_inputs.get("ask_price") or market_inputs.get("ask"))
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return ask or bid

    def _validate_liquidation_distance(self, payload: dict[str, Any]) -> None:
        entry = _safe_float(payload.get("entry_price"))
        liquidation = _safe_float(payload.get("liquidation_price"))
        if entry <= 0 or liquidation <= 0:
            return
        side = normalize_position_side(payload.get("side"))
        if side == "short":
            distance_pct = ((liquidation - entry) / entry) * 100.0
        else:
            distance_pct = ((entry - liquidation) / entry) * 100.0
        if distance_pct < float(self.config.min_liquidation_distance_pct):
            raise ValueError("futures liquidation distance is below configured minimum")
