from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradecraft.services.trading_validation import DISCIPLINE_DEFINITIONS


GRADE_RANK = {
    "observe_only": 0,
    "insufficient": 1,
    "restricted": 2,
    "qualified": 3,
    "scale_candidate": 4,
}
WEAK_GRADE_ALIASES = {
    "weak",
    "loss_weak",
    "negative",
    "de_risk",
}
EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT = 19
LANE_COST_DRAG_WAITING_ENTRY_THRESHOLD_PCT = 60.0
LANE_COST_PRECISION_VERIFIED_MIN_PCT = 60.0
LANE_KELLY_FRACTION = 0.25
LANE_KELLY_REFERENCE_RISK_FRACTION = 0.02
LANE_COST_REPAIR_ENTRY_REQUIREMENTS = [
    "use_waiting_entry_until_cost_drag_below_60pct",
    "target_move_must_clear_estimated_round_trip_cost",
    "do_not_scale_until_profit_factor_and_recovery_repair",
]
LANE_COST_EVIDENCE_REPAIR_ENTRY_REQUIREMENTS = [
    "prefer_recorded_fill_cost_evidence_before_size_increase",
    "use_probe_or_waiting_entry_until_cost_precision_verified",
    "do_not_press_lane_until_recorded_cost_samples_reach_60pct",
]
LANE_PERFORMANCE_EVIDENCE_REQUIRED_METRICS = (
    "expectancy_pct",
    "win_rate_pct",
    "profit_factor",
    "max_drawdown_pct",
    "recovery_factor",
)
LANE_PERFORMANCE_SCALE_BLOCKING_WEAK_METRICS = {
    "expectancy_non_positive",
    "win_rate_below_45pct",
    "profit_factor_below_1",
}
LANE_PERFORMANCE_SCALE_BLOCKING_SEVERE_METRICS = {
    "negative_expectancy",
    "profit_factor_below_0_8",
}
LANE_PERFORMANCE_REPAIR_ENTRY_REQUIREMENTS = [
    "record_expectancy_win_rate_pf_mdd_recovery_before_pressing",
    "use_probe_or_waiting_entry_until_performance_evidence_is_complete",
    "do_not_scale_lane_without_positive_expectancy_pf_and_recovery",
]
LANE_EARLY_LOSS_ENTRY_REQUIREMENTS = [
    "use_waiting_entry_until_expectancy_and_win_rate_recover",
    "keep_probe_budget_until_min_sample_and_positive_expectancy",
    "require_price_improvement_or_setup_confirmation_before_entry",
]
LANE_ENTRY_QUALITY_REPAIR_ENTRY_REQUIREMENTS = [
    "prefer_pullback_reclaim_or_value_location_before_immediate_entry",
    "require_entry_quality_score_above_60_before_size_increase",
    "do_not_chase_extended_moves_until_live_entry_quality_repairs",
]
LANE_VALIDATION_REPAIR_ENTRY_REQUIREMENTS = [
    "respect_validation_repair_enforcement_until_repair_passes",
    "keep_probe_or_waiting_entry_when_repair_blocks_scale_up",
    "do_not_press_lane_until_repair_actions_clear_latest_validation",
]
COST_PRECISION_COUNT_KEYS = (
    "recorded",
    "hybrid",
    "estimated",
    "partial",
    "missing",
)
VALIDATION_DISCIPLINE_BLOCK_ACTIONS: dict[str, dict[str, str]] = {
    "data_validation": {
        "entry_constraint": "no_live_entry_until_fresh_clean_source_data",
        "sizing_constraint": "observe_only_until_data_pipeline_recovers",
        "repair_action": "repair_quote_fill_cost_and_metadata_quality_first",
        "block_design_focus": "fresh_quotes_verified_prices_valid_qty_and_cost_model",
    },
    "overfit_validation": {
        "entry_constraint": "prefer_shadow_or_probe_entry_for_unproven_sets",
        "sizing_constraint": "no_scale_up_from_optimized_in_sample_edge_only",
        "repair_action": "separate_train_test_edge_and_reject_high_overfit_sets",
        "block_design_focus": "simple_parameters_and_out_of_sample_consistency",
    },
    "walk_forward_analysis": {
        "entry_constraint": "use_waiting_entry_until_rolling_wfa_rebuilt",
        "sizing_constraint": "no_size_increase_without_window_pass_evidence",
        "repair_action": "rebuild_recent_rolling_walk_forward_windows",
        "block_design_focus": "entry_rules_that_survive_multiple_market_windows",
    },
    "out_of_sample_test": {
        "entry_constraint": "probe_or_live_shadow_until_unused_sample_passes",
        "sizing_constraint": "keep_small_until_oos_expectancy_is_positive",
        "repair_action": "collect_unused_period_or_forward_shadow_outcomes",
        "block_design_focus": "edge_that_works_beyond_training_period",
    },
    "monte_carlo": {
        "entry_constraint": "patient_entry_only_when_sequence_risk_is_reduced",
        "sizing_constraint": "fractional_small_until_loss_streak_risk_repairs",
        "repair_action": "reduce_loss_clustering_and_retest_sequence_tail_risk",
        "block_design_focus": "fewer_correlated_entries_and_larger_net_edge_buffer",
    },
    "stress_test": {
        "entry_constraint": "avoid_fragile_entries_under_crisis_replay_pressure",
        "sizing_constraint": "risk_off_or_probe_size_until_stress_passes",
        "repair_action": "verify_block_survives_liquidity_fee_and_reversal_shocks",
        "block_design_focus": "wider_margin_of_safety_and_lower_forced_exit_risk",
    },
    "cost_simulation": {
        "entry_constraint": "entry_must_clear_round_trip_cost_and_slippage",
        "sizing_constraint": "do_not_scale_until_recorded_cost_evidence_repairs",
        "repair_action": "record_fee_tax_spread_slippage_funding_and_cost_stress",
        "block_design_focus": "target_move_after_costs_and_limit_order_quality",
    },
    "capacity_analysis": {
        "entry_constraint": "entry_qty_must_fit_depth_turnover_and_exit_capacity",
        "sizing_constraint": "cap_qty_to_practical_capacity_until_verified",
        "repair_action": "attach_orderbook_depth_or_turnover_capacity_evidence",
        "block_design_focus": "liquid_symbols_and_exit_capacity_before_size",
    },
    "kelly_sizing": {
        "entry_constraint": "only_take_blocks_with_valid_fractional_kelly_budget",
        "sizing_constraint": "use_fractional_kelly_mdd_ruin_confidence_cap",
        "repair_action": "recompute_kelly_from_verified_win_payoff_and_drawdown",
        "block_design_focus": "risk_fraction_bound_to_lane_confidence",
    },
    "mdd_limit": {
        "entry_constraint": "recovery_mode_entry_only_after_drawdown_budget_check",
        "sizing_constraint": "de_risk_until_drawdown_budget_recovers",
        "repair_action": "reduce_exposure_and_require_recovery_to_peak_plan",
        "block_design_focus": "smaller_blocks_and_better_stop_distance",
    },
    "sharpe_ratio": {
        "entry_constraint": "avoid_frequency_increase_until_volatility_adjusted_edge",
        "sizing_constraint": "keep_probe_size_when_sharpe_is_weak",
        "repair_action": "improve_return_per_volatility_or_reduce_churn",
        "block_design_focus": "stable_returns_not_noisy_micro_wins",
    },
    "sortino_ratio": {
        "entry_constraint": "avoid_setups_with_unpaid_downside_tail",
        "sizing_constraint": "reduce_size_until_downside_deviation_repairs",
        "repair_action": "tighten_bad_tail_sources_without_over_tight_stops",
        "block_design_focus": "asymmetric_upside_with_controlled_loss_tail",
    },
    "calmar_ratio": {
        "entry_constraint": "require_return_to_drawdown_efficiency_before_pressing",
        "sizing_constraint": "no_scale_until_calmar_and_drawdown_efficiency_repair",
        "repair_action": "raise reward_per_drawdown_or_lower_peak_to_trough_loss",
        "block_design_focus": "blocks_with_clear_reward_over_drawdown_budget",
    },
    "profit_factor": {
        "entry_constraint": "require_positive_profit_factor_lane_before_more_frequency",
        "sizing_constraint": "do_not_press_lanes_with_weak_gross_profit_over_loss",
        "repair_action": "improve_win_loss_payoff_or_filter_low_pf_setups",
        "block_design_focus": "higher_payoff_trades_and_fewer_low_edge_retries",
    },
    "recovery_factor": {
        "entry_constraint": "require_recovery_evidence_before_repeated_reentry",
        "sizing_constraint": "keep_small_until_loss_recovery_speed_improves",
        "repair_action": "verify_drawdown_recovery_count_and_net_recovery_factor",
        "block_design_focus": "setups_that_recover_losses_without_overtrading",
    },
    "risk_of_ruin": {
        "entry_constraint": "no_new_aggressive_blocks_when_ruin_risk_is_high",
        "sizing_constraint": "survival_cap_overrides_llm_size_preference",
        "repair_action": "lower_risk_fraction_and_reduce_tail_loss_probability",
        "block_design_focus": "survival_first_budget_and_uncorrelated_edge",
    },
    "regime_test": {
        "entry_constraint": "trade_only_when_current_regime_matches_positive_edge",
        "sizing_constraint": "reduce_or_shadow_in_negative_or_unknown_regime",
        "repair_action": "attach_regime_labels_and_scorecards_to_live_outcomes",
        "block_design_focus": "regime_aligned_entry_and_exit_thesis",
    },
    "correlation": {
        "entry_constraint": "avoid_adding_same_cluster_risk_without_offsetting_edge",
        "sizing_constraint": "cap_correlated_positions_until_diversified",
        "repair_action": "measure_cluster_correlation_and_reduce_overlap",
        "block_design_focus": "diversified_lane_and_symbol_exposure",
    },
    "factor_exposure": {
        "entry_constraint": "avoid_concentrated_factor_bets_without_explicit_thesis",
        "sizing_constraint": "cap_dominant_factor_exposure_until_balanced",
        "repair_action": "label_factor_exposures_and_limit_crowded_factor_weight",
        "block_design_focus": "controlled_sector_theme_factor_concentration",
    },
}


def _validation_payload(validation: dict[str, Any]) -> dict[str, Any]:
    payload = validation.get("payload") if isinstance(validation.get("payload"), dict) else {}
    return payload or validation


def _compact_discipline(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or row.get("id") or ""),
        "status": str(row.get("status") or "missing"),
    }
    action = str(row.get("action") or row.get("purpose") or "").strip()
    if action:
        out["action"] = action
    return out


def _validation_disciplines(validation: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _validation_payload(validation)
    disciplines = payload.get("disciplines")
    if not isinstance(disciplines, list):
        return []
    return [
        _compact_discipline(row)
        for row in disciplines
        if isinstance(row, dict)
    ]


def _with_absent_prompt_disciplines(
    disciplines: list[dict[str, Any]],
    *,
    actual_count: int,
    expected_count: int,
) -> list[dict[str, Any]]:
    rows = list(disciplines)
    if not rows or int(actual_count or 0) >= int(expected_count or 0):
        return rows
    present_ids = {
        str(row.get("id") or "").strip()
        for row in rows
        if str(row.get("id") or "").strip()
    }
    if not present_ids:
        return rows
    for definition in DISCIPLINE_DEFINITIONS:
        discipline_id = definition["id"]
        if discipline_id in present_ids:
            continue
        rows.append(
            {
                "id": discipline_id,
                "label": definition["label"],
                "status": "missing",
                "action": (
                    f"{definition['label']} 검증 row가 없어 "
                    "쥬 판단 전에 재검증이 필요합니다."
                ),
            }
        )
    return rows[: int(expected_count or EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT)]


def _prompt_discipline_status_counts(
    disciplines: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "missing": 0}
    for row in disciplines:
        status = str(row.get("status") or "missing").strip().lower()
        if status in {"pass", "passed", "ok", "clear"}:
            counts["pass"] += 1
        elif status in {"warn", "warning", "stale", "weak"}:
            counts["warn"] += 1
        elif status in {"fail", "failed", "error", "blocked_by_validation"}:
            counts["fail"] += 1
        else:
            counts["missing"] += 1
    return counts


def _validation_operator_guidance(validation: dict[str, Any]) -> list[str]:
    payload = _validation_payload(validation)
    guidance = payload.get("operator_guidance")
    if not isinstance(guidance, list):
        return []
    return [str(row).strip() for row in guidance if str(row).strip()][:4]


def _validation_remediation_plan(validation: dict[str, Any]) -> dict[str, Any]:
    payload = _validation_payload(validation)
    plan = payload.get("remediation_plan")
    if not isinstance(plan, dict):
        return {}

    def compact_item(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in ("discipline_id", "label", "status", "action")
            if row.get(key) not in (None, "")
        }

    def compact_work(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "task_id",
                "repair_action_id",
                "discipline_id",
                "category_id",
                "status",
                "priority",
                "owner",
                "cadence",
                "automation_hook",
                "execution_weight",
                "validation_mode",
                "allowed_entry_posture",
                "live_shadow_required",
                "scale_up_blocked",
                "lane_policy_hint",
                "blocks_scaling",
                "blocks_new_entries",
                "runner_hint",
                "verification_artifact",
                "exit_criteria",
                "evidence_targets",
            )
            if row.get(key) not in (None, "")
        }

    categories = plan.get("categories")
    compact_categories: list[dict[str, Any]] = []
    if isinstance(categories, list):
        for raw_category in categories[:3]:
            if not isinstance(raw_category, dict):
                continue
            items = raw_category.get("items")
            compact_items = [
                compact_item(row)
                for row in list(items or [])[:3]
                if isinstance(row, dict)
            ]
            compact_categories.append(
                {
                    key: value
                    for key, value in {
                        "id": raw_category.get("id"),
                        "label": raw_category.get("label"),
                        "weak_count": raw_category.get("weak_count"),
                        "fail_count": raw_category.get("fail_count"),
                        "items": compact_items,
                    }.items()
                    if value not in (None, "", [])
                }
            )
    raw_work_queue = plan.get("work_queue")
    work_queue = [
        compact_work(row)
        for row in list(raw_work_queue or [])[:8]
        if isinstance(row, dict)
    ]
    lane_policy_hints = (
        plan.get("lane_policy_hints")
        if isinstance(plan.get("lane_policy_hints"), dict)
        else {}
    )

    compact = {
        "status": plan.get("status"),
        "primary_next_action": plan.get("primary_next_action"),
        "weak_count": plan.get("weak_count"),
        "failed_count": plan.get("failed_count"),
        "missing_count": plan.get("missing_count"),
        "lane_policy_hints": lane_policy_hints,
        "work_queue": work_queue,
        "categories": compact_categories,
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [])
    }


def _validation_capacity_bottleneck(validation: dict[str, Any]) -> dict[str, Any]:
    payload = _validation_payload(validation)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    capacity = metrics.get("capacity") if isinstance(metrics.get("capacity"), dict) else {}
    if not capacity:
        return {}
    examples = capacity.get("examples") if isinstance(capacity.get("examples"), list) else []
    first_example = examples[0] if examples and isinstance(examples[0], dict) else {}
    bottleneck = {
        "status": capacity.get("status"),
        "capacity_method": capacity.get("capacity_method"),
        "min_capacity_ratio": capacity.get("min_capacity_ratio"),
        "tightest_symbol": capacity.get("tightest_symbol") or first_example.get("symbol"),
        "tightest_block_id": capacity.get("tightest_block_id") or first_example.get("block_id"),
    }
    return {
        key: value
        for key, value in bottleneck.items()
        if value is not None and value != ""
    }


def _validation_failure_attribution(validation: dict[str, Any]) -> dict[str, Any]:
    payload = _validation_payload(validation)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    attribution = (
        metrics.get("failure_attribution")
        if isinstance(metrics.get("failure_attribution"), dict)
        else {}
    )
    if not attribution:
        return {}
    recovery_focus = attribution.get("recovery_focus")
    worst_groups = attribution.get("worst_groups")
    best_groups = attribution.get("best_groups")
    out: dict[str, Any] = {
        "status": attribution.get("status"),
        "sample_count": attribution.get("sample_count"),
        "group_count": attribution.get("group_count"),
    }
    if isinstance(recovery_focus, list):
        out["recovery_focus"] = [
            str(row).strip()
            for row in recovery_focus
            if str(row).strip()
        ][:4]
    if isinstance(worst_groups, list):
        out["worst_groups"] = [
            row
            for row in worst_groups
            if isinstance(row, dict)
        ][:4]
    if isinstance(best_groups, list):
        out["best_groups"] = [
            row
            for row in best_groups
            if isinstance(row, dict)
        ][:4]
    return {
        key: value
        for key, value in out.items()
        if value is not None and value != "" and value != []
    }


def _compact_prompt_failure_attribution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_group(row: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "group_type": _prompt_text(row.get("group_type"), limit=48),
            "group": _prompt_text(row.get("group"), limit=80),
            "sample_count": row.get("sample_count"),
            "total_net_pnl": row.get("total_net_pnl"),
            "profit_factor": row.get("profit_factor"),
            "expectancy_pct": row.get("expectancy_pct"),
            "risk_score": row.get("risk_score"),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    compact = {
        "status": _prompt_text(value.get("status"), limit=60),
        "sample_count": value.get("sample_count"),
        "group_count": value.get("group_count"),
        "recovery_focus": [
            _prompt_text(row, limit=180)
            for row in list(value.get("recovery_focus") or [])[:4]
            if _prompt_text(row, limit=180)
        ],
        "worst_groups": [
            compact_group(row)
            for row in list(value.get("worst_groups") or [])[:4]
            if isinstance(row, dict)
        ],
        "instruction": (
            "Failure attribution identifies where realized losses concentrate. "
            "Use it to reduce repeat exposure, demand stronger fresh evidence, "
            "or switch to waiting entries before increasing risk."
        ),
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _validation_lane_scorecards(validation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {}
    payload = _validation_payload(validation)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    lane_scorecards = (
        metrics.get("lane_scorecards")
        if isinstance(metrics.get("lane_scorecards"), dict)
        else {}
    )
    if not lane_scorecards:
        return {}

    def compact_action(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: child
            for key, child in {
                "grade": _prompt_text(row.get("grade"), limit=60),
                "action": _prompt_text(row.get("action"), limit=100),
                "sample_count": row.get("sample_count"),
                "expectancy_pct": row.get("expectancy_pct"),
                "win_rate_pct": (
                    row.get("win_rate_pct")
                    if row.get("win_rate_pct") not in (None, "", [], {})
                    else row.get("win_rate")
                ),
                "profit_factor": row.get("profit_factor"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "recovery_factor": row.get("recovery_factor"),
                "cumulative_return_pct": row.get("cumulative_return_pct"),
                "recovery_factor_cap_multiplier": row.get(
                    "recovery_factor_cap_multiplier"
                ),
                "performance_evidence_status": _prompt_text(
                    row.get("performance_evidence_status"),
                    limit=60,
                ),
                "performance_missing_metrics": [
                    _prompt_text(item, limit=80)
                    for item in list(row.get("performance_missing_metrics") or [])[:8]
                    if _prompt_text(item, limit=80)
                ],
                "performance_weak_metrics": [
                    _prompt_text(item, limit=80)
                    for item in list(row.get("performance_weak_metrics") or [])[:8]
                    if _prompt_text(item, limit=80)
                ],
                "performance_scale_blocking_metrics": [
                    _prompt_text(item, limit=80)
                    for item in list(
                        row.get("performance_scale_blocking_metrics") or []
                    )[:8]
                    if _prompt_text(item, limit=80)
                ],
                "scale_blocked_by_performance_evidence": row.get(
                    "scale_blocked_by_performance_evidence"
                ),
                "cost_drag_pct_of_gross_pnl": row.get("cost_drag_pct_of_gross_pnl"),
                "authority_multiplier": row.get("authority_multiplier"),
                "max_budget_multiplier": row.get("max_budget_multiplier"),
                "risk_budget_multiplier": row.get("risk_budget_multiplier"),
                "risk_budget_scale_decision": _prompt_text(
                    row.get("risk_budget_scale_decision"),
                    limit=80,
                ),
                "risk_budget_blockers": [
                    _prompt_text(item, limit=100)
                    for item in list(row.get("risk_budget_blockers") or [])[:10]
                    if _prompt_text(item, limit=100)
                ],
                "risk_budget_repair_targets": [
                    _prompt_text(item, limit=120)
                    for item in list(row.get("risk_budget_repair_targets") or [])[:10]
                    if _prompt_text(item, limit=120)
                ],
                "raw_kelly_fraction": row.get("raw_kelly_fraction"),
                "fractional_kelly_fraction": row.get("fractional_kelly_fraction"),
                "kelly_cap_multiplier": row.get("kelly_cap_multiplier"),
                "drawdown_cap_multiplier": row.get("drawdown_cap_multiplier"),
                "ruin_cap_multiplier": row.get("ruin_cap_multiplier"),
                "lane_confidence_score": row.get("lane_confidence_score"),
                "lane_confidence_cap_multiplier": row.get(
                    "lane_confidence_cap_multiplier"
                ),
                "cost_precision_verified_rate": row.get(
                    "cost_precision_verified_rate"
                )
                if row.get("cost_precision_verified_rate") not in (None, "", [], {})
                else row.get(
                    "cost_precision_verified_rate_pct"
                ),
                "cost_precision_counts": _compact_cost_precision_counts(
                    row.get("cost_precision_counts")
                ),
                "missing_cost_component_counts": _compact_label_counts(
                    row.get("missing_cost_component_counts")
                ),
                "present_cost_component_counts": _compact_label_counts(
                    row.get("present_cost_component_counts")
                ),
                "required_cost_component_counts": _compact_label_counts(
                    row.get("required_cost_component_counts")
                ),
                "cost_precision_reason_counts": _compact_label_counts(
                    row.get("cost_precision_reason_counts"),
                    limit=6,
                ),
                "cost_evidence_status": _prompt_text(
                    row.get("cost_evidence_status"),
                    limit=80,
                ),
                "cost_repair_targets": _cost_evidence_repair_targets(row),
                "cost_verified_alpha_count": row.get("cost_verified_alpha_count"),
                "cost_unverified_alpha_count": row.get(
                    "cost_unverified_alpha_count"
                ),
                "cost_verified_alpha_net_pnl": row.get(
                    "cost_verified_alpha_net_pnl"
                ),
                "cost_unverified_alpha_net_pnl": row.get(
                    "cost_unverified_alpha_net_pnl"
                ),
                "cost_hybrid_alpha_count": row.get("cost_hybrid_alpha_count"),
                "cost_hybrid_alpha_net_pnl": row.get("cost_hybrid_alpha_net_pnl"),
                "scale_blocked_by_cost_precision": row.get(
                    "scale_blocked_by_cost_precision"
                ),
                "scale_blocked_by_cost_evidence": row.get(
                    "scale_blocked_by_cost_evidence"
                ),
                "scale_blocked_by_verified_edge_samples": row.get(
                    "scale_blocked_by_verified_edge_samples"
                ),
                "scale_blocked_by_verified_edge_net_pnl": row.get(
                    "scale_blocked_by_verified_edge_net_pnl"
                ),
                "avg_entry_quality_score": row.get("avg_entry_quality_score"),
                "bad_entry_quality_rate_pct": row.get("bad_entry_quality_rate_pct"),
                "entry_quality_label_counts": _compact_label_counts(
                    row.get("entry_quality_label_counts")
                ),
                "bad_entry_quality_label_counts": _compact_label_counts(
                    row.get("bad_entry_quality_label_counts")
                ),
                "good_entry_quality_label_counts": _compact_label_counts(
                    row.get("good_entry_quality_label_counts")
                ),
                "dominant_bad_entry_quality_label": _prompt_text(
                    row.get("dominant_bad_entry_quality_label"),
                    limit=80,
                ),
                "dominant_good_entry_quality_label": _prompt_text(
                    row.get("dominant_good_entry_quality_label"),
                    limit=80,
                ),
                "scale_blocked_by_entry_quality": row.get(
                    "scale_blocked_by_entry_quality"
                ),
                "entry_repair_targets": _entry_quality_repair_targets(row),
                "validation_evidence_status": _prompt_text(
                    row.get("validation_evidence_status"),
                    limit=80,
                ),
                "validation_missing_dimensions": _compact_validation_dimensions(
                    row.get("validation_missing_dimensions")
                ),
                "validation_failed_dimensions": _compact_validation_dimensions(
                    row.get("validation_failed_dimensions")
                ),
                "validation_thin_dimensions": _compact_validation_dimensions(
                    row.get("validation_thin_dimensions")
                ),
                "validation_evidence_repair_hint": _validation_evidence_repair_hint(
                    row
                ),
                "validation_evidence_required_evidence": [
                    _prompt_text(item, limit=100)
                    for item in list(
                        row.get("validation_evidence_required_evidence") or []
                    )[:8]
                    if _prompt_text(item, limit=100)
                ],
                "validation_evidence_required_checks": [
                    _prompt_text(item, limit=100)
                    for item in list(
                        row.get("validation_evidence_required_checks") or []
                    )[:8]
                    if _prompt_text(item, limit=100)
                ],
                "validation_evidence_pass_collection_hooks": [
                    _prompt_text(item, limit=140)
                    for item in list(
                        row.get("validation_evidence_pass_collection_hooks") or []
                    )[:6]
                    if _prompt_text(item, limit=140)
                ],
                "validation_evidence_pass_current_gaps": [
                    _prompt_text(item, limit=140)
                    for item in list(
                        row.get("validation_evidence_pass_current_gaps") or []
                    )[:6]
                    if _prompt_text(item, limit=140)
                ],
                "validation_evidence_pass_criteria": [
                    _prompt_text(item, limit=160)
                    for item in list(
                        row.get("validation_evidence_pass_criteria") or []
                    )[:6]
                    if _prompt_text(item, limit=160)
                ],
                "validation_evidence_verification_artifacts": [
                    _prompt_text(item, limit=160)
                    for item in list(
                        row.get("validation_evidence_verification_artifacts") or []
                    )[:6]
                    if _prompt_text(item, limit=160)
                ],
                "scale_blocked_by_validation_evidence": (
                    _validation_evidence_is_weak(row)
                ),
                "validation_repair_enforced_count": row.get(
                    "validation_repair_enforced_count"
                ),
                "validation_repair_scale_up_blocked_count": row.get(
                    "validation_repair_scale_up_blocked_count"
                ),
                "validation_repair_waiting_entry_count": row.get(
                    "validation_repair_waiting_entry_count"
                ),
                "validation_repair_rejected_count": row.get(
                    "validation_repair_rejected_count"
                ),
                "validation_repair_avg_budget_multiplier": row.get(
                    "validation_repair_avg_budget_multiplier"
                ),
                "validation_repair_action_counts": _compact_label_counts(
                    row.get("validation_repair_action_counts")
                ),
                "validation_repair_adjustment_reason_counts": _compact_label_counts(
                    row.get("validation_repair_adjustment_reason_counts")
                ),
                "requires_waiting_entry": row.get("requires_waiting_entry"),
            }.items()
            if child not in (None, "", [], {})
        }

    raw_actions = (
        lane_scorecards.get("lane_actions")
        if isinstance(lane_scorecards.get("lane_actions"), dict)
        else {}
    )
    lane_actions: dict[str, dict[str, Any]] = {}
    raw_action_order = {key: index for index, key in enumerate(raw_actions)}
    action_items = list(raw_actions.items())
    action_items.sort(
        key=lambda item: (
            _lane_prompt_priority(str(item[0]), item[1]),
            raw_action_order.get(item[0], 0),
        )
    )
    for lane, raw_action in action_items[:6]:
        if not isinstance(raw_action, dict):
            continue
        lane_key = _prompt_text(lane, limit=80)
        if lane_key:
            lane_actions[lane_key] = compact_action(raw_action)

    compact = {
        "version": _prompt_text(lane_scorecards.get("version"), limit=60),
        "status": _prompt_text(lane_scorecards.get("status"), limit=40),
        "lane_count": lane_scorecards.get("lane_count"),
        "weak_lanes": _compact_lane_names(
            lane_scorecards.get("weak_lanes"),
            limit=8,
        ),
        "scale_candidate_lanes": _compact_lane_names(
            lane_scorecards.get("scale_candidate_lanes"),
            limit=8,
        ),
        "qualified_lanes": _compact_lane_names(
            lane_scorecards.get("qualified_lanes"),
            limit=8,
        ),
        "insufficient_lanes": _compact_lane_names(
            lane_scorecards.get("insufficient_lanes"),
            limit=8,
        ),
        "cost_evidence_weak_lanes": _compact_lane_names(
            lane_scorecards.get("cost_evidence_weak_lanes"),
            limit=8,
        ),
        "entry_quality_weak_lanes": _compact_lane_names(
            lane_scorecards.get("entry_quality_weak_lanes"),
            limit=8,
        ),
        "validation_evidence_weak_lanes": _compact_lane_names(
            lane_scorecards.get("validation_evidence_weak_lanes"),
            limit=8,
        ),
        "validation_repair_weak_lanes": _compact_lane_names(
            lane_scorecards.get("validation_repair_weak_lanes"),
            limit=8,
        ),
        "lane_actions": lane_actions,
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _validation_loss_cooldown(validation: dict[str, Any]) -> dict[str, Any]:
    attribution = _validation_failure_attribution(validation)
    worst_groups = attribution.get("worst_groups")
    if not isinstance(worst_groups, list):
        return {}
    symbols: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []

    def compact_common(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "total_net_pnl",
                "profit_factor",
                "expectancy_pct",
                "risk_score",
            )
            if row.get(key) is not None
        }

    for raw_row in worst_groups:
        if not isinstance(raw_row, dict):
            continue
        group_type = str(raw_row.get("group_type") or "").strip()
        group = str(raw_row.get("group") or "").strip()
        if not group:
            continue
        common = compact_common(raw_row)
        if group_type == "symbol":
            symbols.append(
                {
                    "symbol": group,
                    **common,
                    "action": "do_not_scale_or_create_live_entry_without_new_evidence",
                }
            )
            continue
        groups.append(
            {
                "group_type": group_type or "unknown",
                "group": group,
                **common,
                "action": "deprioritize_until_revalidated",
            }
        )
    out = {
        "symbols": symbols[:6],
        "groups": groups[:6],
        "instruction": (
            "Treat these symbols or groups as recent loss-cooldown context. "
            "Do not scale them or create fresh live entries unless new evidence "
            "explicitly repairs the failure attribution."
        ),
    }
    return {
        key: value
        for key, value in out.items()
        if value not in (None, "", [])
    }


def _prompt_text(value: Any, *, limit: int = 220) -> str:
    return str(value or "").strip()[: max(int(limit), 1)]


def _lane_prompt_priority(lane: str, action: Any = None) -> int:
    lane_key = str(lane or "").strip().lower()
    raw_action = action if isinstance(action, dict) else {}
    action_text = str(raw_action.get("action") or "").strip().lower()
    grade = str(raw_action.get("grade") or "").strip().lower()
    if ":validation:" in lane_key or lane_key.startswith("validation:"):
        return 0
    if raw_action.get("scale_blocked_by_cost_precision") or "cost" in action_text:
        return 1
    if raw_action.get("requires_waiting_entry") or grade in {
        "observe_only",
        "restricted",
    }:
        return 2
    if grade == "scale_candidate":
        return 3
    if grade == "qualified":
        return 4
    if grade == "insufficient":
        return 5
    return 6


def _compact_lane_names(values: Any, *, limit: int) -> list[str]:
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, raw_value in enumerate(list(values or [])):
        lane = _prompt_text(raw_value, limit=80)
        if not lane or lane in seen:
            continue
        seen.add(lane)
        rows.append((index, lane))
    rows.sort(key=lambda item: (_lane_prompt_priority(item[1]), item[0]))
    return [lane for _, lane in rows[: max(int(limit or 0), 0)]]


def _lane_action_allows_probe(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    action = _prompt_text(value.get("action"), limit=160).strip().lower()
    grade = _prompt_text(value.get("grade"), limit=80).strip().lower()
    reason = _prompt_text(value.get("reason") or value.get("summary"), limit=160).lower()
    blocked_tokens = (
        "halt_new_risk",
        "no_live_entry",
        "observe_only_until",
        "risk_off_only",
    )
    if any(token in action or token in reason for token in blocked_tokens):
        return False
    probe_tokens = (
        "probe",
        "waiting_entry",
        "wait_for_price",
        "sample_build",
        "shadow_or_waiting",
        "small_probe",
    )
    if any(token in action or token in reason for token in probe_tokens):
        return True
    if _prompt_bool(value.get("requires_waiting_entry")) and grade in {
        "insufficient",
        "restricted",
        "qualified",
        "scale_candidate",
    }:
        return True
    return grade == "insufficient"


def _lane_scale_blocked_names(value: dict[str, Any], *, limit: int) -> list[str]:
    rows: list[str] = []
    for key in (
        "shadow_blocked_lanes",
        "exposure_blocked_lanes",
        "remediation_blocked_lanes",
    ):
        rows.extend(_compact_lane_names(value.get(key), limit=limit))
    lane_actions = (
        value.get("lane_actions")
        if isinstance(value.get("lane_actions"), dict)
        else {}
    )
    for lane, action in lane_actions.items():
        if not isinstance(action, dict):
            continue
        if any(
            _prompt_bool(action.get(key))
            for key in (
                "scale_up_blocked",
                "blocks_scale_up",
                "scale_up_blocked_by_shadow_gate",
                "scale_up_blocked_by_exposure_gate",
                "scale_up_blocked_by_validation_remediation",
                "scale_up_blocked_by_active_revision",
                "scale_blocked_by_validation_evidence",
                "scale_blocked_by_validation_repair",
                "scale_blocked_by_cost_precision",
                "scale_blocked_by_cost_evidence",
                "scale_blocked_by_verified_edge_samples",
                "scale_blocked_by_verified_edge_net_pnl",
                "scale_blocked_by_entry_quality",
                "scale_blocked_by_performance_evidence",
            )
        ):
            rows.append(_prompt_text(lane, limit=80))
    return _compact_lane_names(rows, limit=limit)


def _lane_probe_names(value: dict[str, Any], *, limit: int) -> list[str]:
    rows = _compact_lane_names(value.get("insufficient_lanes"), limit=limit)
    lane_actions = (
        value.get("lane_actions")
        if isinstance(value.get("lane_actions"), dict)
        else {}
    )
    for lane, action in lane_actions.items():
        if _lane_action_allows_probe(action):
            rows.append(_prompt_text(lane, limit=80))
    return _compact_lane_names(rows, limit=limit)


def _lane_execution_posture(
    *,
    allow_scale_up: bool,
    probe_lanes: list[str],
    scale_blocked_lanes: list[str],
    weak_lanes: list[str],
) -> str:
    if allow_scale_up:
        return "scale_allowed"
    if probe_lanes and scale_blocked_lanes:
        return "probe_allowed_scale_blocked"
    if probe_lanes:
        return "probe_allowed_sample_building"
    if scale_blocked_lanes or weak_lanes:
        return "review_required_no_scale"
    return "normal_selective"


def _compact_cost_precision_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key in COST_PRECISION_COUNT_KEYS:
        raw_count = value.get(key)
        if raw_count in (None, "", [], {}):
            continue
        try:
            count = int(float(raw_count))
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        counts[key] = count
    return counts


def _compact_label_counts(value: Any, *, limit: int = 8) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    rows: list[tuple[str, int]] = []
    for raw_key, raw_count in value.items():
        label = _prompt_text(raw_key, limit=80)
        if not label:
            continue
        try:
            count = int(float(raw_count))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        rows.append((label, count))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return {
        label: count
        for label, count in rows[: max(int(limit or 0), 0)]
    }


def _cost_evidence_repair_hint(row: dict[str, Any]) -> str:
    status = _prompt_text(row.get("cost_evidence_status"), limit=80).lower()
    counts = _compact_cost_precision_counts(row.get("cost_precision_counts"))
    missing_components = _compact_label_counts(
        row.get("missing_cost_component_counts")
    )
    blocked = bool(row.get("scale_blocked_by_cost_precision")) or bool(
        row.get("scale_blocked_by_cost_evidence")
    )
    recorded_count = counts.get("recorded", 0)
    non_recorded_count = sum(
        counts.get(key, 0)
        for key in ("hybrid", "estimated", "partial", "missing")
    )
    if missing_components:
        missing = ",".join(list(missing_components.keys())[:4])
        return f"record_missing_cost_components_before_size_increase:{missing}"
    if status == "hybrid_needs_market_cost_repair":
        return "replace_hybrid_estimates_with_recorded_fill_book_cost_evidence"
    if blocked and recorded_count == 0 and non_recorded_count > 0:
        return "record_fill_fee_spread_slippage_before_size_increase"
    if blocked:
        return "increase_recorded_cost_precision_before_size_increase"
    return ""


def _cost_evidence_repair_targets(row: dict[str, Any]) -> list[str]:
    targets: list[str] = []

    def add(value: str) -> None:
        clean = _prompt_text(value, limit=120)
        if clean and clean not in targets:
            targets.append(clean)

    missing_components = _compact_label_counts(
        row.get("missing_cost_component_counts")
    )
    for component in missing_components:
        add(f"record_missing_cost_component:{component}")

    counts = _compact_cost_precision_counts(row.get("cost_precision_counts"))
    status = _prompt_text(row.get("cost_evidence_status"), limit=80).lower()
    if status == "hybrid_needs_market_cost_repair" or counts.get("hybrid", 0) > 0:
        add("replace_hybrid_cost_estimates_with_recorded_fill_book_evidence")
    if counts.get("estimated", 0) > 0:
        add("replace_estimated_costs_with_recorded_execution_costs")
    if counts.get("partial", 0) > 0:
        add("complete_partial_cost_components_before_size_increase")
    if counts.get("missing", 0) > 0:
        add("record_missing_round_trip_costs_before_size_increase")
    unverified_count = _prompt_count(row.get("cost_unverified_alpha_count")) or 0
    raw_unverified_net = row.get("cost_unverified_alpha_net_pnl")
    has_unverified_net = raw_unverified_net not in (None, "", [], {})
    if (
        unverified_count >= 3
        and has_unverified_net
        and _prompt_float(raw_unverified_net) <= 0.0
    ):
        add("repair_negative_unverified_cost_edge_before_new_size")

    hint = _cost_evidence_repair_hint(row)
    if not targets and hint:
        add(hint)
    return targets[:6]


def _cost_evidence_requires_repair(
    row: dict[str, Any],
    *,
    sample_count: int,
    min_samples_to_scale: int,
) -> bool:
    if _prompt_bool(row.get("scale_blocked_by_cost_precision")) or _prompt_bool(
        row.get("scale_blocked_by_cost_evidence")
    ):
        return True

    raw_cost_precision_rate = row.get("cost_precision_verified_rate")
    if raw_cost_precision_rate in (None, "", [], {}):
        raw_cost_precision_rate = row.get("cost_precision_verified_rate_pct")
    has_cost_precision = raw_cost_precision_rate not in (None, "", [], {})
    cost_precision_verified_rate = _prompt_float(raw_cost_precision_rate)
    counts = _compact_cost_precision_counts(row.get("cost_precision_counts"))
    cost_precision_sample_count = sum(counts.values())
    effective_sample_count = max(
        int(sample_count or 0),
        cost_precision_sample_count,
        _prompt_count(row.get("cost_verified_alpha_count")) or 0,
        _prompt_count(row.get("cost_unverified_alpha_count")) or 0,
    )
    weak_rate = bool(
        has_cost_precision
        and cost_precision_verified_rate < LANE_COST_PRECISION_VERIFIED_MIN_PCT
    )
    if effective_sample_count >= max(int(min_samples_to_scale or 1), 1) and weak_rate:
        return True
    if cost_precision_sample_count >= 3 and weak_rate:
        return True

    unverified_count = _prompt_count(row.get("cost_unverified_alpha_count")) or 0
    raw_unverified_net = row.get("cost_unverified_alpha_net_pnl")
    has_unverified_net = raw_unverified_net not in (None, "", [], {})
    return bool(
        unverified_count >= 3
        and has_unverified_net
        and _prompt_float(raw_unverified_net) <= 0.0
    )


def _entry_quality_repair_hint(row: dict[str, Any]) -> str:
    dominant_bad = _prompt_text(
        row.get("dominant_bad_entry_quality_label"),
        limit=80,
    ).lower()
    avg_score = _prompt_float(row.get("avg_entry_quality_score"))
    bad_rate = _prompt_float(row.get("bad_entry_quality_rate_pct"))
    blocked = bool(row.get("scale_blocked_by_entry_quality"))
    if dominant_bad and any(
        token in dominant_bad
        for token in ("chase", "extended", "high", "고점", "추격")
    ):
        return "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks"
    if blocked or (avg_score > 0 and avg_score < 55.0) or bad_rate >= 50.0:
        return "require_price_relief_regime_alignment_before_new_blocks"
    return ""


def _entry_quality_repair_targets(row: dict[str, Any]) -> list[str]:
    targets: list[str] = []

    def add(value: str) -> None:
        clean = _prompt_text(value, limit=120)
        if clean and clean not in targets:
            targets.append(clean)

    bad_labels = _compact_label_counts(row.get("bad_entry_quality_label_counts"))
    dominant_bad = _prompt_text(
        row.get("dominant_bad_entry_quality_label"),
        limit=80,
    ).lower()
    label_text = " ".join([dominant_bad, *bad_labels.keys()]).lower()
    avg_score = _prompt_float(row.get("avg_entry_quality_score"))
    bad_rate = _prompt_float(row.get("bad_entry_quality_rate_pct"))
    blocked = bool(row.get("scale_blocked_by_entry_quality"))

    if any(
        token in label_text
        for token in ("chase", "extended", "near_20d_high", "high", "고점", "추격")
    ):
        add("replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks")
    if any(token in label_text for token in ("wick", "tail", "reversal", "꼬리")):
        add("wait_for_wick_risk_compression_before_entry")
    if any(token in label_text for token in ("churn", "spread", "noise", "whipsaw")):
        add("wait_for_churn_to_settle_before_entry")
    if avg_score > 0 and avg_score < 60.0:
        add("require_entry_quality_score_above_60_before_size_increase")
    if bad_rate >= 50.0:
        add("reduce_frequency_until_bad_entry_rate_below_50pct")
    if blocked:
        add("use_waiting_entry_or_price_improvement_before_live_block")

    hint = _entry_quality_repair_hint(row)
    if not targets and hint:
        add(hint)
    return targets[:6]


def _compact_validation_dimensions(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for raw_value in value:
        dimension = _prompt_text(raw_value, limit=80)
        if dimension and dimension not in rows:
            rows.append(dimension)
    return rows[: max(int(limit or 0), 0)]


def _validation_evidence_is_weak(row: dict[str, Any]) -> bool:
    status = _prompt_text(row.get("validation_evidence_status"), limit=80).lower()
    return (
        bool(row.get("scale_blocked_by_validation_evidence"))
        or status in {"missing", "fail", "failed", "blocked", "blocked_by_validation"}
        or bool(_compact_validation_dimensions(row.get("validation_missing_dimensions")))
        or bool(_compact_validation_dimensions(row.get("validation_failed_dimensions")))
        or bool(_compact_validation_dimensions(row.get("validation_thin_dimensions")))
    )


def _validation_evidence_repair_hint(row: dict[str, Any]) -> str:
    missing = set(_compact_validation_dimensions(row.get("validation_missing_dimensions")))
    failed = set(_compact_validation_dimensions(row.get("validation_failed_dimensions")))
    thin = set(_compact_validation_dimensions(row.get("validation_thin_dimensions")))
    dimensions = {value.lower() for value in missing.union(failed).union(thin)}
    if dimensions.intersection(
        {"backtest", "walk_forward", "walk_forward_analysis", "out_of_sample", "oos"}
    ):
        return "rebuild_backtest_wfa_oos_before_scale_up"
    if "live_shadow" in dimensions or "shadow" in dimensions:
        return "collect_live_shadow_evidence_before_scale_up"
    if _validation_evidence_is_weak(row):
        return "complete_validation_evidence_before_scale_up"
    return ""


def _normalized_validation_dimensions(row: dict[str, Any]) -> list[str]:
    aliases = {
        "walk_forward_analysis": "walk_forward",
        "wfa": "walk_forward",
        "oos": "out_of_sample",
        "out_of_sample_test": "out_of_sample",
        "shadow": "live_shadow",
        "live_shadow_test": "live_shadow",
    }
    dimensions: list[str] = []
    for value in [
        *_compact_validation_dimensions(row.get("validation_missing_dimensions")),
        *_compact_validation_dimensions(row.get("validation_failed_dimensions")),
        *_compact_validation_dimensions(row.get("validation_thin_dimensions")),
    ]:
        normalized = aliases.get(value.lower(), value.lower())
        if normalized and normalized not in dimensions:
            dimensions.append(normalized)
    return dimensions


def _core_validation_evidence_gaps(row: dict[str, Any]) -> list[str]:
    dimensions = set(_normalized_validation_dimensions(row))
    return [
        dimension
        for dimension in ("backtest", "walk_forward", "out_of_sample", "live_shadow")
        if dimension in dimensions
    ]


def _validation_evidence_repair_targets(row: dict[str, Any]) -> list[str]:
    targets: list[str] = []

    def add(value: str) -> None:
        clean = _prompt_text(value, limit=140)
        if clean and clean not in targets:
            targets.append(clean)

    gaps = _core_validation_evidence_gaps(row)
    if "backtest" in gaps:
        add("rerun_backtest_before_scale_up")
    if "walk_forward" in gaps:
        add("rebuild_walk_forward_windows_before_scale_up")
    if "out_of_sample" in gaps:
        add("pass_out_of_sample_validation_before_scale_up")
    if "live_shadow" in gaps:
        add("collect_live_shadow_samples_before_scale_up")
    hint = _validation_evidence_repair_hint(row)
    if hint:
        add(hint)
    if not targets and _validation_evidence_is_weak(row):
        add("complete_validation_evidence_before_scale_up")
    return targets[:8]


def _compact_prompt_active_revision_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        "version": _prompt_text(value.get("version"), limit=60),
        "venue": _prompt_text(value.get("venue"), limit=40),
        "strategy_revision_id": _prompt_text(
            value.get("strategy_revision_id"),
            limit=80,
        ),
        "status": _prompt_text(value.get("status"), limit=80),
        "validation_sample_role": _prompt_text(
            value.get("validation_sample_role"),
            limit=80,
        ),
        "legacy_proxy_gate_mode": _prompt_text(
            value.get("legacy_proxy_gate_mode"),
            limit=80,
        ),
        "authority_posture": _prompt_text(
            value.get("authority_posture"),
            limit=80,
        ),
        "active_sample_count": _prompt_count(value.get("active_sample_count")),
        "effective_sample_count": _prompt_count(
            value.get("effective_sample_count")
        ),
        "validation_sample_count": _prompt_count(
            value.get("validation_sample_count")
        ),
        "legacy_proxy_sample_count": _prompt_count(
            value.get("legacy_proxy_sample_count")
        ),
        "lane_alpha_count": _prompt_count(value.get("lane_alpha_count")),
        "pending_block_count": _prompt_count(value.get("pending_block_count")),
        "pending_block_status_counts": (
            value.get("pending_block_status_counts")
            if isinstance(value.get("pending_block_status_counts"), dict)
            else {}
        ),
        "pending_block_lane_counts": (
            value.get("pending_block_lane_counts")
            if isinstance(value.get("pending_block_lane_counts"), dict)
            else {}
        ),
        "missing_revision_nonterminal_count": _prompt_count(
            value.get("missing_revision_nonterminal_count")
        ),
        "pending_evidence_role": _prompt_text(
            value.get("pending_evidence_role"),
            limit=100,
        ),
        "min_samples_to_scale": _prompt_count(
            value.get("min_samples_to_scale") or value.get("min_sample_count")
        ),
        "scorecard_count": _prompt_count(value.get("scorecard_count")),
        "performance_lane_count": _prompt_count(
            value.get("performance_lane_count")
        ),
        "validation_fail_count": _prompt_count(
            value.get("validation_fail_count")
        ),
        "validation_missing_count": _prompt_count(
            value.get("validation_missing_count")
        ),
        "hard_blocking_count": _prompt_count(value.get("hard_blocking_count")),
        "scale_up_allowed": _prompt_bool(value.get("scale_up_allowed")),
        "can_scale_from_proxy": (
            _prompt_bool(value.get("can_scale_from_proxy"))
            if "can_scale_from_proxy" in value
            else None
        ),
        "block_design_requirement": _prompt_text(
            value.get("block_design_requirement"),
            limit=180,
        ),
        "next_action": _prompt_text(value.get("next_action"), limit=160),
    }
    for key in (
        "legacy_proxy_failed_discipline_ids",
        "legacy_proxy_missing_core_discipline_ids",
        "active_revision_sample_building_failed_discipline_ids",
    ):
        values = value.get(key)
        if isinstance(values, list):
            compact[key] = [
                _prompt_text(item, limit=80)
                for item in values[:8]
                if _prompt_text(item, limit=80)
            ]
    for key in ("sample_building_gate_mode",):
        child = _prompt_text(value.get(key), limit=80)
        if child:
            compact[key] = child
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _prompt_count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _prompt_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    if out in (float("inf"), float("-inf")):
        return default
    return out


def _prompt_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _validation_summary_count(summary: dict[str, Any]) -> int | None:
    values = [
        _prompt_count(summary.get(key))
        for key in ("pass_count", "warn_count", "fail_count", "missing_count")
    ]
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(known)


def _validation_effective_discipline_count(
    validation: dict[str, Any],
    *,
    gate: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    disciplines: list[dict[str, Any]] | None = None,
) -> int:
    payload = _validation_payload(validation)
    source_summary = (
        summary
        if isinstance(summary, dict)
        else payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else validation.get("summary")
        if isinstance(validation.get("summary"), dict)
        else {}
    )
    source_disciplines = disciplines if disciplines is not None else _validation_disciplines(validation)
    row_count = len(source_disciplines)
    summary_count = _validation_summary_count(source_summary)
    if summary_count is not None:
        return max(row_count, summary_count)
    if row_count:
        return row_count
    source_gate = gate if isinstance(gate, dict) else {}
    for value in (
        source_gate.get("discipline_count"),
        payload.get("discipline_count"),
        validation.get("discipline_count"),
    ):
        count = _prompt_count(value)
        if count is not None:
            return count
    return 0


def _validation_hard_fail_count(summary: dict[str, Any]) -> int:
    if "hard_fail_count" in summary:
        return _prompt_count(summary.get("hard_fail_count")) or 0
    if "core_fail_count" in summary:
        return _prompt_count(summary.get("core_fail_count")) or 0
    return _prompt_count(summary.get("fail_count")) or 0


def _validation_hard_blocking_count(summary: dict[str, Any]) -> int:
    if "hard_blocking_count" in summary:
        return _prompt_count(summary.get("hard_blocking_count")) or 0
    if "hard_fail_count" in summary or "hard_missing_count" in summary:
        return (_prompt_count(summary.get("hard_fail_count")) or 0) + (
            _prompt_count(summary.get("hard_missing_count")) or 0
        )
    if "core_fail_count" in summary or "core_missing_count" in summary:
        return (_prompt_count(summary.get("core_fail_count")) or 0) + (
            _prompt_count(summary.get("core_missing_count")) or 0
        )
    return _prompt_count(summary.get("fail_count")) or 0


def _cost_metric_from_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {}
    payload = _validation_payload(validation)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    cost_metric = (
        metrics.get("cost_simulation")
        if isinstance(metrics.get("cost_simulation"), dict)
        else {}
    )
    if cost_metric:
        return cost_metric
    disciplines = payload.get("disciplines")
    if not isinstance(disciplines, list):
        return {}
    for row in disciplines:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "") != "cost_simulation":
            continue
        metric = row.get("metric")
        if isinstance(metric, dict):
            return metric
        return row
    return {}


def _compact_prompt_cost_attribution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_group(row: dict[str, Any]) -> dict[str, Any]:
        symbols = row.get("symbols")
        block_ids = row.get("block_ids")
        payload = {
            "group_type": _prompt_text(row.get("group_type"), limit=48),
            "group": _prompt_text(row.get("group"), limit=80),
            "sample_count": row.get("sample_count"),
            "total_gross_pnl": row.get("total_gross_pnl"),
            "total_net_pnl": row.get("total_net_pnl"),
            "total_cost": row.get("total_cost"),
            "cost_drag_pct_of_abs_gross_pnl": row.get(
                "cost_drag_pct_of_abs_gross_pnl"
            ),
            "net_negative_after_cost": row.get("net_negative_after_cost"),
            "symbols": [
                _prompt_text(symbol, limit=32)
                for symbol in list(symbols or [])[:6]
                if _prompt_text(symbol, limit=32)
            ],
            "block_ids": [
                _prompt_text(block_id, limit=80)
                for block_id in list(block_ids or [])[:4]
                if _prompt_text(block_id, limit=80)
            ],
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    def compact_row(row: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "block_id": _prompt_text(row.get("block_id"), limit=80),
            "symbol": _prompt_text(row.get("symbol"), limit=32),
            "horizon": _prompt_text(row.get("horizon"), limit=48),
            "strategy_family": _prompt_text(row.get("strategy_family"), limit=80),
            "gross_pnl": row.get("gross_pnl"),
            "net_pnl": row.get("net_pnl"),
            "cost_total": row.get("cost_total"),
            "cost_drag_pct_of_abs_gross_pnl": row.get(
                "cost_drag_pct_of_abs_gross_pnl"
            ),
            "net_negative_after_cost": row.get("net_negative_after_cost"),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    groups = [
        compact_group(row)
        for row in list(value.get("worst_cost_groups") or value.get("groups") or [])[:4]
        if isinstance(row, dict)
    ]
    rows = [
        compact_row(row)
        for row in list(value.get("worst_cost_rows") or value.get("rows") or [])[:4]
        if isinstance(row, dict)
    ]
    compact = {
        "status": _prompt_text(value.get("status"), limit=60),
        "sample_count": value.get("sample_count"),
        "total_cost": value.get("total_cost"),
        "cost_drag_pct_of_gross_pnl": value.get("cost_drag_pct_of_gross_pnl"),
        "groups": groups,
        "rows": rows,
        "instruction": (
            "Cost attribution shows where gross edge is being erased by fees, tax, "
            "spread, or slippage. Use it to demand wider expected move, better "
            "entry location, longer hold time, or smaller frequency before scaling."
        ),
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _validation_cost_attribution(validation: Any) -> dict[str, Any]:
    return _compact_prompt_cost_attribution(
        _cost_metric_from_validation(validation)
    )


def _compact_prompt_discipline(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": _prompt_text(row.get("id"), limit=80),
        "label": _prompt_text(row.get("label") or row.get("id"), limit=120),
        "status": _prompt_text(row.get("status") or "missing", limit=40),
    }
    action = _prompt_text(row.get("action") or row.get("purpose"), limit=220)
    if action:
        payload["action"] = action
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }


def _compact_prompt_loss_cooldown(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_row(row: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "symbol": _prompt_text(row.get("symbol"), limit=32),
            "group_type": _prompt_text(row.get("group_type"), limit=48),
            "group": _prompt_text(row.get("group"), limit=80),
            "risk_score": row.get("risk_score"),
            "total_net_pnl": row.get("total_net_pnl"),
            "profit_factor": row.get("profit_factor"),
            "expectancy_pct": row.get("expectancy_pct"),
            "action": _prompt_text(row.get("action"), limit=120),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    compact: dict[str, Any] = {}
    symbols = value.get("symbols")
    if isinstance(symbols, list):
        compact["symbols"] = [
            compact_row(row)
            for row in symbols[:4]
            if isinstance(row, dict)
        ]
    groups = value.get("groups")
    if isinstance(groups, list):
        compact["groups"] = [
            compact_row(row)
            for row in groups[:4]
            if isinstance(row, dict)
        ]
    instruction = _prompt_text(value.get("instruction"), limit=220)
    if instruction:
        compact["instruction"] = instruction
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _compact_prompt_remediation_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_item(row: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "discipline_id": _prompt_text(row.get("discipline_id"), limit=80),
            "label": _prompt_text(row.get("label"), limit=120),
            "status": _prompt_text(row.get("status"), limit=40),
            "action": _prompt_text(row.get("action"), limit=220),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    def compact_pass_path(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        behavior = (
            value.get("jue_behavior_until_pass")
            if isinstance(value.get("jue_behavior_until_pass"), dict)
            else {}
        )
        runtime = (
            value.get("m1_runtime_profile")
            if isinstance(value.get("m1_runtime_profile"), dict)
            else {}
        )
        required_evidence = (
            value.get("required_evidence")
            if isinstance(value.get("required_evidence"), dict)
            else {}
        )
        payload = {
            "version": _prompt_text(value.get("version"), limit=60),
            "current_gap": _prompt_text(value.get("current_gap"), limit=80),
            "collection_hook": _prompt_text(
                value.get("collection_hook"),
                limit=120,
            ),
            "collection_cadence": _prompt_text(
                value.get("collection_cadence"),
                limit=80,
            ),
            "pass_criteria": _prompt_text(value.get("pass_criteria"), limit=220),
            "required_evidence": required_evidence,
            "jue_behavior_until_pass": {
                "allowed_entry_posture": _prompt_text(
                    behavior.get("allowed_entry_posture"),
                    limit=120,
                ),
                "blocks_scaling": _prompt_text(
                    behavior.get("blocks_scaling"),
                    limit=140,
                ),
                "blocks_new_entries": _prompt_text(
                    behavior.get("blocks_new_entries"),
                    limit=160,
                ),
                "scale_up_blocked": behavior.get("scale_up_blocked"),
                "live_shadow_required": behavior.get("live_shadow_required"),
            },
            "m1_runtime_profile": {
                "execution_weight": _prompt_text(
                    runtime.get("execution_weight"),
                    limit=80,
                ),
                "prefer_incremental_refresh": runtime.get(
                    "prefer_incremental_refresh"
                ),
                "avoid_full_rebuild_in_manager_prompt": runtime.get(
                    "avoid_full_rebuild_in_manager_prompt"
                ),
            },
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    def compact_work(row: dict[str, Any]) -> dict[str, Any]:
        evidence_targets = (
            row.get("evidence_targets")
            if isinstance(row.get("evidence_targets"), dict)
            else {}
        )
        pass_path = compact_pass_path(row.get("pass_path"))
        payload = {
            "task_id": _prompt_text(row.get("task_id"), limit=120),
            "discipline_id": _prompt_text(row.get("discipline_id"), limit=80),
            "category_id": _prompt_text(row.get("category_id"), limit=80),
            "status": _prompt_text(row.get("status"), limit=40),
            "priority": _prompt_text(row.get("priority"), limit=20),
            "owner": _prompt_text(row.get("owner"), limit=80),
            "cadence": _prompt_text(row.get("cadence"), limit=80),
            "lane_policy_hint": _prompt_text(
                row.get("lane_policy_hint"),
                limit=140,
            ),
            "blocks_scaling": _prompt_text(row.get("blocks_scaling"), limit=140),
            "blocks_new_entries": _prompt_text(
                row.get("blocks_new_entries"),
                limit=160,
            ),
            "runner_hint": _prompt_text(row.get("runner_hint"), limit=220),
            "verification_artifact": _prompt_text(
                row.get("verification_artifact"),
                limit=260,
            ),
            "exit_criteria": _prompt_text(row.get("exit_criteria"), limit=220),
            "validation_mode": _prompt_text(row.get("validation_mode"), limit=100),
            "allowed_entry_posture": _prompt_text(
                row.get("allowed_entry_posture"),
                limit=120,
            ),
            "live_shadow_required": row.get("live_shadow_required"),
            "scale_up_blocked": row.get("scale_up_blocked"),
            "evidence_targets": evidence_targets,
            "pass_path": pass_path,
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    categories: list[dict[str, Any]] = []
    raw_categories = value.get("categories")
    if isinstance(raw_categories, list):
        for raw_category in raw_categories[:3]:
            if not isinstance(raw_category, dict):
                continue
            raw_items = raw_category.get("items")
            items = [
                compact_item(row)
                for row in list(raw_items or [])[:2]
                if isinstance(row, dict)
            ]
            category = {
                "id": _prompt_text(raw_category.get("id"), limit=80),
                "label": _prompt_text(raw_category.get("label"), limit=120),
                "weak_count": raw_category.get("weak_count"),
                "fail_count": raw_category.get("fail_count"),
                "items": items,
            }
            categories.append(
                {
                    key: child
                    for key, child in category.items()
                    if child not in (None, "", [], {})
                }
            )
    raw_work_queue = value.get("work_queue")
    work_queue = [
        compact_work(row)
        for row in list(raw_work_queue or [])[:4]
        if isinstance(row, dict)
    ]
    lane_policy_hints = (
        value.get("lane_policy_hints")
        if isinstance(value.get("lane_policy_hints"), dict)
        else {}
    )
    pass_path_summary = (
        value.get("pass_path_summary")
        if isinstance(value.get("pass_path_summary"), dict)
        else {}
    )

    compact = {
        "status": _prompt_text(value.get("status"), limit=60),
        "primary_next_action": _prompt_text(
            value.get("primary_next_action"),
            limit=260,
        ),
        "weak_count": value.get("weak_count"),
        "failed_count": value.get("failed_count"),
        "missing_count": value.get("missing_count"),
        "lane_policy_hints": lane_policy_hints,
        "pass_path_summary": pass_path_summary,
        "work_queue": work_queue,
        "categories": categories,
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _compact_prompt_validation_matrix(
    validation: Any,
    *,
    gate: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {}
    payload = _validation_payload(validation)
    summary = (
        payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else validation.get("summary")
        if isinstance(validation.get("summary"), dict)
        else {}
    )
    disciplines = _validation_disciplines(validation)
    if not disciplines and not summary:
        return {}
    score = (
        summary.get("total_score")
        if summary.get("total_score") is not None
        else summary.get("score")
    )
    compact_summary = {
        "score": score,
        "readiness": _prompt_text(summary.get("readiness"), limit=80),
        "pass_count": summary.get("pass_count"),
        "warn_count": summary.get("warn_count"),
        "fail_count": summary.get("fail_count"),
        "missing_count": summary.get("missing_count"),
    }
    actual_count = _validation_effective_discipline_count(
        validation,
        gate=gate,
        summary=summary,
        disciplines=disciplines,
    )
    expected_count = gate.get(
        "expected_discipline_count",
        EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
    )
    completed_statuses = _with_absent_prompt_disciplines(
        disciplines,
        actual_count=actual_count,
        expected_count=int(expected_count or EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT),
    )
    row_detail_count = len(disciplines)
    row_detail_complete = (
        bool(expected_count)
        and row_detail_count >= int(expected_count or 0)
    )
    if disciplines and int(actual_count or 0) < int(expected_count or 0):
        completed_counts = _prompt_discipline_status_counts(completed_statuses)
        compact_summary.update(
            {
                "pass_count": completed_counts["pass"],
                "warn_count": completed_counts["warn"],
                "fail_count": completed_counts["fail"],
                "missing_count": completed_counts["missing"],
            }
        )
    matrix = {
        "expected_count": expected_count,
        "actual_count": actual_count,
        "row_detail_count": row_detail_count,
        "row_detail_complete": row_detail_complete,
        "summary": {
            key: value
            for key, value in compact_summary.items()
            if value not in (None, "", [], {})
        },
        "statuses": [
            _compact_prompt_discipline(row)
            for row in completed_statuses[:EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT]
        ],
    }
    return {
        key: child
        for key, child in matrix.items()
        if child not in (None, "", [], {})
    }


def _compact_prompt_validation_passport(
    validation: Any,
    *,
    gate: dict[str, Any],
) -> dict[str, Any]:
    matrix = _compact_prompt_validation_matrix(validation, gate=gate)
    if not matrix:
        return {}
    statuses = matrix.get("statuses")
    if not isinstance(statuses, list):
        statuses = []
    summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    failed_ids = [
        _prompt_text(row.get("id"), limit=80)
        for row in statuses
        if isinstance(row, dict)
        and _prompt_text(row.get("status"), limit=40) == "fail"
        and _prompt_text(row.get("id"), limit=80)
    ]
    weak_ids = [
        _prompt_text(row.get("id"), limit=80)
        for row in statuses
        if isinstance(row, dict)
        and _prompt_text(row.get("status"), limit=40) in {"fail", "warn", "missing"}
        and _prompt_text(row.get("id"), limit=80)
    ]
    expected_count = matrix.get("expected_count")
    actual_count = matrix.get("actual_count")
    row_detail_count = matrix.get("row_detail_count")
    row_detail_complete = bool(matrix.get("row_detail_complete"))
    is_complete = (
        bool(expected_count)
        and bool(actual_count)
        and int(actual_count or 0) >= int(expected_count or 0)
    )
    fail_count = summary.get("fail_count")
    missing_count = summary.get("missing_count")
    passport = {
        "version": "trading_validation_passport_v1",
        "status": _prompt_text(gate.get("status"), limit=80),
        "readiness": _prompt_text(
            gate.get("readiness") or summary.get("readiness"),
            limit=80,
        ),
        "score": summary.get("score"),
        "expected_count": expected_count,
        "actual_count": actual_count,
        "row_detail_count": row_detail_count,
        "row_detail_complete": row_detail_complete,
        "is_complete": is_complete,
        "pass_count": summary.get("pass_count"),
        "warn_count": summary.get("warn_count"),
        "fail_count": fail_count,
        "missing_count": missing_count,
        "failed_ids": failed_ids[:8],
        "weak_ids": weak_ids[:10],
        "risk_governor_action": _prompt_text(
            gate.get("risk_governor_action"),
            limit=80,
        ),
        "risk_governor_source": _prompt_text(
            gate.get("risk_governor_source"),
            limit=80,
        ),
        "requires_revalidation": (
            not is_complete
            or int(fail_count or 0) > 0
            or int(missing_count or 0) > 0
        ),
    }
    return {
        key: child
        for key, child in passport.items()
        if child not in (None, "", [], {})
    }


def _append_unique(items: list[str], value: str) -> None:
    value = str(value or "").strip()
    if value and value not in items:
        items.append(value)


def _validation_discipline_block_actions(
    statuses: list[Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in statuses:
        if not isinstance(row, dict):
            continue
        status = _prompt_text(row.get("status"), limit=40)
        if status not in {"fail", "warn", "missing"}:
            continue
        discipline_id = _prompt_text(row.get("id"), limit=80)
        if not discipline_id:
            continue
        template = VALIDATION_DISCIPLINE_BLOCK_ACTIONS.get(
            discipline_id,
            {
                "entry_constraint": "prefer_waiting_entry_until_validation_repairs",
                "sizing_constraint": "no_scale_up_until_discipline_passes",
                "repair_action": "repair_validation_evidence_before_size_increase",
                "block_design_focus": "verified_edge_quality_before_execution",
            },
        )
        payload = {
            "id": discipline_id,
            "status": status,
            "entry_constraint": template["entry_constraint"],
            "sizing_constraint": template["sizing_constraint"],
            "repair_action": template["repair_action"],
            "block_design_focus": template["block_design_focus"],
        }
        diagnostic_action = _prompt_text(row.get("action"), limit=140)
        if diagnostic_action:
            payload["diagnostic_action"] = diagnostic_action
        actions.append(payload)
    actions.sort(
        key=lambda item: (
            {"fail": 0, "warn": 1, "missing": 2}.get(
                str(item.get("status") or ""),
                3,
            ),
            str(item.get("id") or ""),
        )
    )
    return actions[: max(int(limit), 0)]


def _merge_remediation_discipline_block_actions(
    actions: list[dict[str, Any]],
    remediation_plan: dict[str, Any],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not isinstance(remediation_plan, dict):
        return actions[: max(int(limit), 0)]
    merged = [dict(row) for row in actions if isinstance(row, dict)]
    seen = {
        str(row.get("id") or "").strip()
        for row in merged
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    rows: list[Any] = []
    work_queue = remediation_plan.get("work_queue")
    if isinstance(work_queue, list):
        rows.extend(work_queue)
    categories = remediation_plan.get("categories")
    if isinstance(categories, list):
        for category in categories:
            if isinstance(category, dict) and isinstance(category.get("items"), list):
                rows.extend(category["items"])
    for row in rows:
        if not isinstance(row, dict):
            continue
        discipline_id = _prompt_text(row.get("discipline_id") or row.get("id"), limit=80)
        if not discipline_id or discipline_id in seen:
            continue
        template = VALIDATION_DISCIPLINE_BLOCK_ACTIONS.get(
            discipline_id,
            {
                "entry_constraint": "prefer_waiting_entry_until_validation_repairs",
                "sizing_constraint": "no_scale_up_until_discipline_passes",
                "repair_action": "repair_validation_evidence_before_size_increase",
                "block_design_focus": "verified_edge_quality_before_execution",
            },
        )
        payload = {
            "id": discipline_id,
            "status": _prompt_text(row.get("status") or "missing", limit=40),
            "entry_constraint": template["entry_constraint"],
            "sizing_constraint": template["sizing_constraint"],
            "repair_action": template["repair_action"],
            "block_design_focus": template["block_design_focus"],
        }
        diagnostic_action = _prompt_text(
            row.get("action")
            or row.get("runner_hint")
            or row.get("lane_policy_hint")
            or row.get("exit_criteria"),
            limit=140,
        )
        if diagnostic_action:
            payload["diagnostic_action"] = diagnostic_action
        merged.append(payload)
        seen.add(discipline_id)
    return merged[: max(int(limit), 0)]


def _compact_prompt_validation_pressure(
    validation: Any,
    *,
    gate: dict[str, Any],
    allow_scale_up: Any,
) -> dict[str, Any]:
    matrix = _compact_prompt_validation_matrix(validation, gate=gate)
    if not matrix:
        return {}
    statuses = matrix.get("statuses")
    if not isinstance(statuses, list):
        statuses = []
    fail_ids = [
        _prompt_text(row.get("id"), limit=80)
        for row in statuses
        if isinstance(row, dict)
        and _prompt_text(row.get("status"), limit=40) == "fail"
        and _prompt_text(row.get("id"), limit=80)
    ]
    warn_ids = [
        _prompt_text(row.get("id"), limit=80)
        for row in statuses
        if isinstance(row, dict)
        and _prompt_text(row.get("status"), limit=40) == "warn"
        and _prompt_text(row.get("id"), limit=80)
    ]
    missing_ids = [
        _prompt_text(row.get("id"), limit=80)
        for row in statuses
        if isinstance(row, dict)
        and _prompt_text(row.get("status"), limit=40) == "missing"
        and _prompt_text(row.get("id"), limit=80)
    ]
    weak_ids = fail_ids + [
        row for row in warn_ids + missing_ids if row not in set(fail_ids)
    ]
    discipline_actions = _validation_discipline_block_actions(statuses, limit=8)
    gate_status = _prompt_text(gate.get("status"), limit=80)
    readiness = _prompt_text(gate.get("readiness"), limit=80)
    risk_action = _prompt_text(gate.get("risk_governor_action"), limit=80)
    hard_fail_count = _prompt_count(gate.get("hard_fail_count")) or 0
    hard_blocking_count = (
        _prompt_count(gate.get("hard_blocking_count"))
        if gate.get("hard_blocking_count") is not None
        else hard_fail_count
    ) or 0
    hard_block = bool(
        hard_blocking_count > 0
        or gate_status
        in {
            "blocked_by_validation",
            "validation_error",
            "validation_incomplete",
            "validation_missing",
            "validation_research_only",
            "validation_stale",
        }
    )
    requirements: list[str] = []
    weak_set = set(weak_ids)
    if weak_set:
        _append_unique(requirements, "prefer_waiting_entry_or_price_improvement")
    if weak_set.intersection({"cost_simulation"}):
        _append_unique(requirements, "require_positive_net_edge_after_all_costs")
        _append_unique(requirements, "require_wider_expected_move_than_cost_drag")
    if weak_set.intersection(
        {"kelly_sizing", "risk_of_ruin", "monte_carlo", "mdd_limit"}
    ):
        _append_unique(
            requirements,
            "use_fractional_kelly_with_drawdown_and_ruin_caps",
        )
    if weak_set.intersection(
        {
            "profit_factor",
            "recovery_factor",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
        }
    ):
        _append_unique(
            requirements,
            "require_positive_lane_expectancy_before_frequency_or_size_increase",
        )
    if weak_set.intersection(
        {
            "overfit_validation",
            "walk_forward_analysis",
            "out_of_sample_test",
            "regime_test",
            "stress_test",
        }
    ):
        _append_unique(
            requirements,
            "require_oos_wfa_or_live_shadow_before_scale_up",
        )
    if weak_set.intersection({"data_validation"}):
        _append_unique(requirements, "require_fresh_clean_data_before_live_entry")
    if weak_set.intersection({"capacity_analysis", "correlation", "factor_exposure"}):
        _append_unique(
            requirements,
            "cap_concentration_and_confirm_capacity_before_scaling",
        )

    remediation_plan = _compact_prompt_remediation_plan(
        gate.get("remediation_plan")
    )
    discipline_actions = _merge_remediation_discipline_block_actions(
        discipline_actions,
        remediation_plan,
    )
    lane_policy_hints = (
        remediation_plan.get("lane_policy_hints")
        if isinstance(remediation_plan.get("lane_policy_hints"), dict)
        else {}
    )
    remediation_entry_mode = _prompt_text(
        lane_policy_hints.get("entry_mode"),
        limit=80,
    ).strip().lower()
    remediation_risk_budget_mode = _prompt_text(
        lane_policy_hints.get("risk_budget_mode"),
        limit=80,
    ).strip().lower()
    remediation_requires_shadow = _prompt_bool(
        lane_policy_hints.get("requires_shadow_or_waiting_entry")
    )
    remediation_scale_hint_present = (
        "scale_up_allowed" in lane_policy_hints
    )
    remediation_scale_blocked = (
        remediation_scale_hint_present
        and not _prompt_bool(lane_policy_hints.get("scale_up_allowed"))
    )
    remediation_waiting_required = (
        remediation_entry_mode in {"verified_waiting_probe", "risk_off_recovery"}
        or remediation_requires_shadow
    )
    remediation_risk_off = (
        remediation_entry_mode == "risk_off_recovery"
        or remediation_risk_budget_mode == "probe"
    )
    if lane_policy_hints:
        _append_unique(
            requirements,
            "respect_validation_remediation_lane_policy_hints",
        )
    if remediation_waiting_required:
        _append_unique(requirements, "follow_remediation_waiting_probe_mode")
    if remediation_scale_blocked:
        _append_unique(requirements, "do_not_scale_until_remediation_clears")
    if not requirements:
        _append_unique(requirements, "maintain_current_evidence_standard")

    if hard_block or risk_action == "halt_new_risk":
        severity = "blocked"
        entry_posture = "no_new_entry"
        sizing_posture = "halt_new_risk"
    elif risk_action == "risk_off":
        severity = "risk_off"
        entry_posture = "patient_waiting_entry"
        sizing_posture = "fractional_small_only"
    elif remediation_risk_off:
        severity = "remediation_risk_off"
        entry_posture = "patient_waiting_entry"
        sizing_posture = "fractional_small_only"
    elif risk_action in {"de_risk", "reduced"}:
        severity = risk_action
        entry_posture = "patient_waiting_entry"
        sizing_posture = "reduced_probe_only"
    elif remediation_waiting_required:
        severity = "remediation_waiting_probe"
        entry_posture = "patient_waiting_entry"
        sizing_posture = "reduced_probe_only"
    elif fail_ids:
        severity = "diagnostic_de_risk"
        entry_posture = "patient_waiting_entry"
        sizing_posture = "fractional_small_only"
    elif warn_ids or missing_ids:
        severity = "validation_caution"
        entry_posture = "selective_waiting_entry"
        sizing_posture = "reduced_probe_only"
    else:
        severity = "clear"
        entry_posture = "strategy_dependent"
        sizing_posture = "normal"

    scale_allowed = (
        bool(allow_scale_up)
        and severity == "clear"
        and not weak_ids
        and not remediation_scale_blocked
    )
    pressure = {
        "version": "validation_pressure_v1",
        "severity": severity,
        "gate_status": gate_status,
        "readiness": readiness,
        "risk_governor_action": risk_action,
        "hard_block": hard_block,
        "hard_fail_count": hard_fail_count,
        "hard_blocking_count": hard_blocking_count,
        "scale_up_allowed": scale_allowed,
        "entry_posture": entry_posture,
        "sizing_posture": sizing_posture,
        "fail_ids": fail_ids[:8],
        "warn_ids": warn_ids[:8],
        "missing_ids": missing_ids[:8],
        "discipline_actions": discipline_actions,
        "remediation_entry_mode": remediation_entry_mode,
        "remediation_risk_budget_mode": remediation_risk_budget_mode,
        "block_design_requirements": requirements[:8],
        "instruction": (
            "Translate failed validation diagnostics into the next block design: "
            "better entry location, wider net edge after costs, lower fractional "
            "risk, lane revalidation, and no scale-up until live evidence repairs "
            "the weak tests."
        ),
    }
    return {
        key: child
        for key, child in pressure.items()
        if child not in (None, "", [], {})
    }


def _compact_prompt_lane_authority(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_risk_budget_passport(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        allowed_keys = (
            "version",
            "sample_confidence",
            "raw_kelly_fraction",
            "raw_fractional_kelly_fraction",
            "kelly_cap_multiplier",
            "drawdown_cap_multiplier",
            "recovery_factor_cap_multiplier",
            "ruin_cap_multiplier",
            "risk_of_ruin_pct",
            "lane_confidence_score",
            "sample_cap_multiplier",
            "lane_confidence_cap_multiplier",
            "applied_risk_budget_multiplier",
            "recommended_risk_fraction",
            "max_risk_cap_fraction",
            "risk_fraction_cap_multiplier",
            "cost_precision_verified_rate",
            "cost_precision_counts",
            "missing_cost_component_counts",
            "present_cost_component_counts",
            "required_cost_component_counts",
            "cost_precision_reason_counts",
            "cost_evidence_status",
            "cost_evidence_repair_hint",
            "cost_repair_targets",
            "cost_verified_alpha_count",
            "cost_unverified_alpha_count",
            "cost_verified_alpha_net_pnl",
            "cost_unverified_alpha_net_pnl",
            "cost_hybrid_alpha_count",
            "cost_hybrid_alpha_net_pnl",
            "scale_blocked_by_cost_precision",
            "scale_blocked_by_cost_evidence",
            "cost_precision_cap_multiplier",
            "verified_edge_sample_cap_multiplier",
            "verified_edge_net_cap_multiplier",
            "scale_blocked_by_verified_edge_samples",
            "scale_blocked_by_verified_edge_net_pnl",
                "avg_entry_quality_score",
                "bad_entry_quality_rate_pct",
                "entry_quality_label_counts",
                "bad_entry_quality_label_counts",
                "good_entry_quality_label_counts",
                "dominant_bad_entry_quality_label",
                "dominant_good_entry_quality_label",
                "entry_quality_repair_hint",
            "entry_repair_targets",
            "entry_quality_cap_multiplier",
            "validation_evidence_status",
            "validation_missing_dimensions",
            "validation_failed_dimensions",
            "validation_thin_dimensions",
            "validation_evidence_repair_hint",
            "validation_evidence_repair_targets",
            "core_validation_evidence_gaps",
            "validation_evidence_cap_multiplier",
            "scale_blocked_by_validation_evidence",
            "validation_repair_enforced_count",
            "validation_repair_scale_up_blocked_count",
            "validation_repair_waiting_entry_count",
            "validation_repair_rejected_count",
            "validation_repair_avg_budget_multiplier",
            "validation_repair_action_counts",
            "validation_repair_adjustment_reason_counts",
            "validation_repair_cap_multiplier",
            "scale_blocked_by_validation_repair",
            "performance_evidence_status",
            "performance_missing_metrics",
            "performance_weak_metrics",
            "performance_severe_metrics",
            "performance_scale_blocking_metrics",
            "performance_evidence_cap_multiplier",
            "performance_repair_targets",
            "scale_blocked_by_performance_evidence",
            "validation_governor_action",
            "validation_governor_source",
            "validation_governor_cap_multiplier",
            "validation_risk_of_ruin_pct",
            "validation_recommended_risk_fraction",
            "validation_max_risk_cap_fraction",
            "validation_kelly_cap_reason",
            "validation_drawdown_usage_ratio",
            "validation_shadow_gate_status",
            "validation_shadow_cap_multiplier",
            "validation_exposure_gate_status",
            "validation_exposure_cap_multiplier",
            "validation_remediation_gate_status",
            "validation_remediation_cap_multiplier",
            "validation_remediation_blocked_ids",
            "validation_remediation_p0_ids",
            "validation_remediation_modes",
            "scale_blocked_by_validation_remediation",
            "active_revision_gate_status",
            "active_revision_cap_multiplier",
            "effective_risk_budget_multiplier",
            "scale_decision",
            "scale_blockers",
            "scale_repair_targets",
        )
        return {
            key: raw.get(key)
            for key in allowed_keys
            if raw.get(key) not in (None, "", [], {})
        }

    def compact_shadow_gate(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        payload = {
            "status": _prompt_text(raw.get("status"), limit=80),
            "blocks_scale_up": raw.get("blocks_scale_up"),
            "requires_live_shadow": raw.get("requires_live_shadow"),
            "requires_waiting_entry": raw.get("requires_waiting_entry"),
            "cap_multiplier": raw.get("cap_multiplier"),
            "active_sample_count": raw.get("active_sample_count"),
            "effective_sample_count": raw.get("effective_sample_count"),
            "legacy_proxy_sample_count": raw.get("legacy_proxy_sample_count"),
            "pending_block_count": raw.get("pending_block_count"),
            "min_samples_to_scale": raw.get("min_samples_to_scale"),
            "weak_validation_ids": [
                _prompt_text(row, limit=80)
                for row in list(raw.get("weak_validation_ids") or [])[:6]
                if _prompt_text(row, limit=80)
            ],
            "scale_blocked_discipline_ids": [
                _prompt_text(row, limit=80)
                for row in list(raw.get("scale_blocked_discipline_ids") or [])[:6]
                if _prompt_text(row, limit=80)
            ],
            "p0_discipline_ids": [
                _prompt_text(row, limit=80)
                for row in list(raw.get("p0_discipline_ids") or [])[:6]
                if _prompt_text(row, limit=80)
            ],
            "validation_modes": [
                _prompt_text(row, limit=100)
                for row in list(raw.get("validation_modes") or [])[:6]
                if _prompt_text(row, limit=100)
            ],
            "focus_reasons": [
                _prompt_text(row, limit=100)
                for row in list(raw.get("focus_reasons") or [])[:4]
                if _prompt_text(row, limit=100)
            ],
            "entry_mode": _prompt_text(raw.get("entry_mode"), limit=80),
            "risk_budget_mode": _prompt_text(raw.get("risk_budget_mode"), limit=80),
            "entry_policy": _prompt_text(raw.get("entry_policy"), limit=120),
            "scale_policy": _prompt_text(raw.get("scale_policy"), limit=120),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    def compact_exposure_gate(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        payload = {
            "status": _prompt_text(raw.get("status"), limit=80),
            "blocks_scale_up": raw.get("blocks_scale_up"),
            "requires_waiting_entry": raw.get("requires_waiting_entry"),
            "weak_validation_ids": [
                _prompt_text(row, limit=80)
                for row in list(raw.get("weak_validation_ids") or [])[:6]
                if _prompt_text(row, limit=80)
            ],
            "cap_multiplier": raw.get("cap_multiplier"),
            "entry_policy": _prompt_text(raw.get("entry_policy"), limit=120),
            "scale_policy": _prompt_text(raw.get("scale_policy"), limit=120),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    def slim_lane_action_for_prompt(raw: dict[str, Any]) -> dict[str, Any]:
        def compact_text_list(key: str, *, limit: int = 4, text_limit: int = 90) -> list[str]:
            return [
                _prompt_text(row, limit=text_limit)
                for row in list(raw.get(key) or [])[:limit]
                if _prompt_text(row, limit=text_limit)
            ]

        def slim_risk_budget_passport(raw_passport: Any) -> dict[str, Any]:
            passport = compact_risk_budget_passport(raw_passport)
            if not passport:
                return {}
            keep_keys = (
                "raw_kelly_fraction",
                "raw_fractional_kelly_fraction",
                "kelly_cap_multiplier",
                "drawdown_cap_multiplier",
                "recovery_factor_cap_multiplier",
                "ruin_cap_multiplier",
                "risk_of_ruin_pct",
                "lane_confidence_score",
                "sample_cap_multiplier",
                "lane_confidence_cap_multiplier",
                "applied_risk_budget_multiplier",
                "recommended_risk_fraction",
                "max_risk_cap_fraction",
                "risk_fraction_cap_multiplier",
                "cost_precision_verified_rate",
                "cost_precision_counts",
                "cost_evidence_status",
                "cost_evidence_repair_hint",
                "cost_verified_alpha_count",
                "cost_unverified_alpha_count",
                "cost_verified_alpha_net_pnl",
                "cost_unverified_alpha_net_pnl",
                "cost_hybrid_alpha_count",
                "cost_hybrid_alpha_net_pnl",
                "scale_blocked_by_cost_precision",
                "scale_blocked_by_cost_evidence",
                "cost_precision_cap_multiplier",
                "verified_edge_sample_cap_multiplier",
                "verified_edge_net_cap_multiplier",
                "scale_blocked_by_verified_edge_samples",
                "scale_blocked_by_verified_edge_net_pnl",
                "avg_entry_quality_score",
                "bad_entry_quality_rate_pct",
                "entry_quality_label_counts",
                "bad_entry_quality_label_counts",
                "good_entry_quality_label_counts",
                "dominant_bad_entry_quality_label",
                "dominant_good_entry_quality_label",
                "entry_quality_repair_hint",
                "entry_quality_cap_multiplier",
                "validation_evidence_cap_multiplier",
                "scale_blocked_by_validation_evidence",
                "validation_repair_cap_multiplier",
                "scale_blocked_by_validation_repair",
                "performance_evidence_cap_multiplier",
                "scale_blocked_by_performance_evidence",
                "validation_shadow_gate_status",
                "validation_shadow_cap_multiplier",
                "validation_exposure_gate_status",
                "validation_exposure_cap_multiplier",
                "validation_remediation_gate_status",
                "validation_remediation_cap_multiplier",
                "active_revision_gate_status",
                "active_revision_cap_multiplier",
                "effective_risk_budget_multiplier",
                "scale_decision",
                "scale_blockers",
            )
            out = {
                key: passport.get(key)
                for key in keep_keys
                if passport.get(key) not in (None, "", [], {})
            }
            for list_key in (
                "cost_repair_targets",
                "entry_repair_targets",
                "validation_evidence_required_evidence",
                "validation_evidence_required_checks",
                "validation_evidence_pass_collection_hooks",
                "validation_evidence_pass_current_gaps",
                "validation_evidence_pass_criteria",
                "scale_repair_targets",
            ):
                rows = passport.get(list_key)
                if isinstance(rows, list):
                    out[list_key] = [
                        _prompt_text(row, limit=110)
                        for row in rows[:4]
                        if _prompt_text(row, limit=110)
                    ]
            return {
                key: child
                for key, child in out.items()
                if child not in (None, "", [], {})
            }

        payload = {
            "grade": _prompt_text(raw.get("grade"), limit=60),
            "action": _prompt_text(raw.get("action"), limit=90),
            "sample_count": raw.get("sample_count"),
            "max_budget_multiplier": raw.get("max_budget_multiplier"),
            "applied_max_budget_multiplier": raw.get(
                "applied_max_budget_multiplier"
            ),
            "scale_up_allowed": raw.get("scale_up_allowed"),
            "scale_decision": _prompt_text(raw.get("scale_decision"), limit=80),
            "scale_blockers": compact_text_list(
                "scale_blockers",
                limit=4,
                text_limit=90,
            ),
            "scale_repair_targets": compact_text_list(
                "scale_repair_targets",
                limit=4,
                text_limit=100,
            ),
            "expectancy_pct": raw.get("expectancy_pct"),
            "win_rate": raw.get("win_rate"),
            "max_drawdown_pct": raw.get("max_drawdown_pct"),
            "profit_factor": raw.get("profit_factor"),
            "recovery_factor": raw.get("recovery_factor"),
            "performance_evidence_status": _prompt_text(
                raw.get("performance_evidence_status"),
                limit=60,
            ),
            "performance_missing_metrics": compact_text_list(
                "performance_missing_metrics",
                limit=5,
                text_limit=60,
            ),
            "performance_weak_metrics": compact_text_list(
                "performance_weak_metrics",
                limit=5,
                text_limit=60,
            ),
            "performance_scale_blocking_metrics": compact_text_list(
                "performance_scale_blocking_metrics",
                limit=5,
                text_limit=70,
            ),
            "cost_drag_pct_of_gross_pnl": raw.get("cost_drag_pct_of_gross_pnl"),
            "cost_precision_verified_rate": raw.get("cost_precision_verified_rate"),
            "cost_precision_counts": raw.get("cost_precision_counts"),
            "cost_evidence_status": _prompt_text(
                raw.get("cost_evidence_status"),
                limit=60,
            ),
            "cost_evidence_repair_hint": _prompt_text(
                raw.get("cost_evidence_repair_hint"),
                limit=90,
            ),
            "cost_repair_targets": compact_text_list(
                "cost_repair_targets",
                limit=4,
                text_limit=100,
            ),
            "cost_hybrid_alpha_count": raw.get("cost_hybrid_alpha_count"),
            "cost_hybrid_alpha_net_pnl": raw.get("cost_hybrid_alpha_net_pnl"),
            "cost_verified_alpha_count": raw.get("cost_verified_alpha_count"),
            "cost_unverified_alpha_count": raw.get("cost_unverified_alpha_count"),
            "cost_verified_alpha_net_pnl": raw.get("cost_verified_alpha_net_pnl"),
            "cost_unverified_alpha_net_pnl": raw.get("cost_unverified_alpha_net_pnl"),
            "entry_quality_repair_hint": _prompt_text(
                raw.get("entry_quality_repair_hint"),
                limit=90,
            ),
            "entry_quality_requirements": compact_text_list(
                "entry_quality_requirements",
                limit=4,
                text_limit=100,
            ),
            "validation_evidence_status": _prompt_text(
                raw.get("validation_evidence_status"),
                limit=60,
            ),
            "validation_missing_dimensions": compact_text_list(
                "validation_missing_dimensions",
                limit=4,
                text_limit=60,
            ),
            "validation_failed_dimensions": compact_text_list(
                "validation_failed_dimensions",
                limit=4,
                text_limit=60,
            ),
            "validation_thin_dimensions": compact_text_list(
                "validation_thin_dimensions",
                limit=4,
                text_limit=60,
            ),
            "validation_evidence_repair_hint": _prompt_text(
                raw.get("validation_evidence_repair_hint"),
                limit=90,
            ),
            "validation_evidence_repair_targets": compact_text_list(
                "validation_evidence_repair_targets",
                limit=5,
                text_limit=100,
            ),
            "validation_evidence_required_evidence": compact_text_list(
                "validation_evidence_required_evidence",
                limit=4,
                text_limit=90,
            ),
            "validation_evidence_required_checks": compact_text_list(
                "validation_evidence_required_checks",
                limit=4,
                text_limit=90,
            ),
            "validation_evidence_pass_collection_hooks": compact_text_list(
                "validation_evidence_pass_collection_hooks",
                limit=3,
                text_limit=110,
            ),
            "validation_evidence_pass_current_gaps": compact_text_list(
                "validation_evidence_pass_current_gaps",
                limit=3,
                text_limit=110,
            ),
            "validation_evidence_pass_criteria": compact_text_list(
                "validation_evidence_pass_criteria",
                limit=3,
                text_limit=110,
            ),
            "core_validation_evidence_gaps": compact_text_list(
                "core_validation_evidence_gaps",
                limit=4,
                text_limit=70,
            ),
            "validation_repair_enforced_count": raw.get(
                "validation_repair_enforced_count"
            ),
            "validation_repair_scale_up_blocked_count": raw.get(
                "validation_repair_scale_up_blocked_count"
            ),
            "validation_repair_waiting_entry_count": raw.get(
                "validation_repair_waiting_entry_count"
            ),
            "scale_blocked_by_performance_evidence": raw.get(
                "scale_blocked_by_performance_evidence"
            ),
            "scale_blocked_by_cost_precision": raw.get(
                "scale_blocked_by_cost_precision"
            ),
            "scale_blocked_by_cost_evidence": raw.get(
                "scale_blocked_by_cost_evidence"
            ),
            "scale_blocked_by_verified_edge_samples": raw.get(
                "scale_blocked_by_verified_edge_samples"
            ),
            "scale_blocked_by_verified_edge_net_pnl": raw.get(
                "scale_blocked_by_verified_edge_net_pnl"
            ),
            "avg_entry_quality_score": raw.get("avg_entry_quality_score"),
            "bad_entry_quality_rate_pct": raw.get("bad_entry_quality_rate_pct"),
            "entry_quality_label_counts": raw.get("entry_quality_label_counts"),
            "bad_entry_quality_label_counts": raw.get(
                "bad_entry_quality_label_counts"
            ),
            "good_entry_quality_label_counts": raw.get(
                "good_entry_quality_label_counts"
            ),
            "dominant_bad_entry_quality_label": _prompt_text(
                raw.get("dominant_bad_entry_quality_label"),
                limit=70,
            ),
            "dominant_good_entry_quality_label": _prompt_text(
                raw.get("dominant_good_entry_quality_label"),
                limit=70,
            ),
            "entry_repair_targets": compact_text_list(
                "entry_repair_targets",
                limit=4,
                text_limit=100,
            ),
            "scale_blocked_by_entry_quality": raw.get(
                "scale_blocked_by_entry_quality"
            ),
            "scale_blocked_by_validation_evidence": raw.get(
                "scale_blocked_by_validation_evidence"
            ),
            "scale_blocked_by_validation_repair": raw.get(
                "scale_blocked_by_validation_repair"
            ),
            "requires_waiting_entry": raw.get("requires_waiting_entry"),
            "scale_up_blocked_by_shadow_gate": raw.get(
                "scale_up_blocked_by_shadow_gate"
            ),
            "scale_up_blocked_by_exposure_gate": raw.get(
                "scale_up_blocked_by_exposure_gate"
            ),
            "scale_up_blocked_by_validation_remediation": raw.get(
                "scale_up_blocked_by_validation_remediation"
            ),
            "scale_up_blocked_by_active_revision": raw.get(
                "scale_up_blocked_by_active_revision"
            ),
            "risk_budget_passport": slim_risk_budget_passport(
                raw.get("risk_budget_passport")
            ),
            "validation_shadow_gate": compact_shadow_gate(
                raw.get("validation_shadow_gate")
            ),
            "validation_exposure_gate": compact_exposure_gate(
                raw.get("validation_exposure_gate")
            ),
            "validation_remediation_gate": compact_shadow_gate(
                raw.get("validation_remediation_gate")
            ),
            "active_revision_gate": compact_shadow_gate(
                raw.get("active_revision_gate")
            ),
        }
        return {
            key: child
            for key, child in payload.items()
            if child not in (None, "", [], {})
        }

    lane_actions = value.get("lane_actions")
    compact_actions: dict[str, Any] = {}
    if isinstance(lane_actions, dict):
        lane_action_order = {key: index for index, key in enumerate(lane_actions)}
        action_items = list(lane_actions.items())
        action_items.sort(
            key=lambda item: (
                _lane_prompt_priority(str(item[0]), item[1]),
                lane_action_order.get(item[0], 0),
            )
        )
        for lane, raw_action in action_items[:4]:
            if not isinstance(raw_action, dict):
                continue
            lane_key = _prompt_text(lane, limit=80)
            if not lane_key:
                continue
            compact_actions[lane_key] = {
                key: child
                for key, child in {
                    "grade": _prompt_text(raw_action.get("grade"), limit=60),
                    "action": _prompt_text(raw_action.get("action"), limit=80),
                    "sample_count": raw_action.get("sample_count"),
                    "max_budget_multiplier": raw_action.get("max_budget_multiplier"),
                    "applied_max_budget_multiplier": raw_action.get(
                        "applied_max_budget_multiplier"
                    ),
                    "scale_up_allowed": raw_action.get("scale_up_allowed"),
                    "scale_decision": _prompt_text(
                        raw_action.get("scale_decision"),
                        limit=80,
                    ),
                    "scale_blockers": [
                        _prompt_text(row, limit=100)
                        for row in list(raw_action.get("scale_blockers") or [])[:10]
                        if _prompt_text(row, limit=100)
                    ],
                    "scale_repair_targets": [
                        _prompt_text(row, limit=120)
                        for row in list(raw_action.get("scale_repair_targets") or [])[:10]
                        if _prompt_text(row, limit=120)
                    ],
                    "expectancy_pct": raw_action.get("expectancy_pct"),
                    "win_rate": raw_action.get("win_rate"),
                    "max_drawdown_pct": raw_action.get("max_drawdown_pct"),
                    "profit_factor": raw_action.get("profit_factor"),
                    "recovery_factor": raw_action.get("recovery_factor"),
                    "performance_evidence_status": _prompt_text(
                        raw_action.get("performance_evidence_status"),
                        limit=60,
                    ),
                    "performance_missing_metrics": [
                        _prompt_text(row, limit=80)
                        for row in list(
                            raw_action.get("performance_missing_metrics") or []
                        )[:8]
                        if _prompt_text(row, limit=80)
                    ],
                    "performance_weak_metrics": [
                        _prompt_text(row, limit=80)
                        for row in list(
                            raw_action.get("performance_weak_metrics") or []
                        )[:8]
                        if _prompt_text(row, limit=80)
                    ],
                    "performance_scale_blocking_metrics": [
                        _prompt_text(row, limit=80)
                        for row in list(
                            raw_action.get("performance_scale_blocking_metrics") or []
                        )[:8]
                        if _prompt_text(row, limit=80)
                    ],
                    "scale_blocked_by_performance_evidence": raw_action.get(
                        "scale_blocked_by_performance_evidence"
                    ),
                    "cumulative_return_pct": raw_action.get("cumulative_return_pct"),
                    "cost_drag_pct_of_gross_pnl": raw_action.get(
                        "cost_drag_pct_of_gross_pnl"
                    ),
                    "cost_precision_verified_rate": raw_action.get(
                        "cost_precision_verified_rate"
                    )
                    if raw_action.get("cost_precision_verified_rate")
                    not in (None, "", [], {})
                    else raw_action.get(
                        "cost_precision_verified_rate_pct"
                    ),
                    "scale_blocked_by_cost_precision": raw_action.get(
                        "scale_blocked_by_cost_precision"
                    ),
                    "scale_blocked_by_cost_evidence": raw_action.get(
                        "scale_blocked_by_cost_evidence"
                    ),
                    "scale_blocked_by_verified_edge_samples": raw_action.get(
                        "scale_blocked_by_verified_edge_samples"
                    ),
                    "scale_blocked_by_verified_edge_net_pnl": raw_action.get(
                        "scale_blocked_by_verified_edge_net_pnl"
                    ),
                    "cost_precision_counts": _compact_cost_precision_counts(
                        raw_action.get("cost_precision_counts")
                    ),
                    "missing_cost_component_counts": _compact_label_counts(
                        raw_action.get("missing_cost_component_counts")
                    ),
                    "present_cost_component_counts": _compact_label_counts(
                        raw_action.get("present_cost_component_counts")
                    ),
                    "required_cost_component_counts": _compact_label_counts(
                        raw_action.get("required_cost_component_counts")
                    ),
                    "cost_precision_reason_counts": _compact_label_counts(
                        raw_action.get("cost_precision_reason_counts"),
                        limit=6,
                    ),
                    "cost_evidence_status": _prompt_text(
                        raw_action.get("cost_evidence_status"),
                        limit=80,
                    ),
                    "cost_evidence_repair_hint": _prompt_text(
                        raw_action.get("cost_evidence_repair_hint"),
                        limit=100,
                    ),
                    "cost_repair_targets": [
                        _prompt_text(row, limit=120)
                        for row in list(raw_action.get("cost_repair_targets") or [])[:6]
                        if _prompt_text(row, limit=120)
                    ],
                    "cost_verified_alpha_count": _prompt_count(
                        raw_action.get("cost_verified_alpha_count")
                    ),
                    "cost_unverified_alpha_count": _prompt_count(
                        raw_action.get("cost_unverified_alpha_count")
                    ),
                    "cost_verified_alpha_net_pnl": raw_action.get(
                        "cost_verified_alpha_net_pnl"
                    ),
                    "cost_unverified_alpha_net_pnl": raw_action.get(
                        "cost_unverified_alpha_net_pnl"
                    ),
                    "cost_hybrid_alpha_count": _prompt_count(
                        raw_action.get("cost_hybrid_alpha_count")
                    ),
                    "cost_hybrid_alpha_net_pnl": raw_action.get(
                        "cost_hybrid_alpha_net_pnl"
                    ),
                    "avg_entry_quality_score": raw_action.get(
                        "avg_entry_quality_score"
                    ),
                    "bad_entry_quality_rate_pct": raw_action.get(
                        "bad_entry_quality_rate_pct"
                    ),
                    "entry_quality_label_counts": _compact_label_counts(
                        raw_action.get("entry_quality_label_counts")
                    ),
                    "bad_entry_quality_label_counts": _compact_label_counts(
                        raw_action.get("bad_entry_quality_label_counts")
                    ),
                    "good_entry_quality_label_counts": _compact_label_counts(
                        raw_action.get("good_entry_quality_label_counts")
                    ),
                    "dominant_bad_entry_quality_label": _prompt_text(
                        raw_action.get("dominant_bad_entry_quality_label"),
                        limit=80,
                    ),
                    "dominant_good_entry_quality_label": _prompt_text(
                        raw_action.get("dominant_good_entry_quality_label"),
                        limit=80,
                    ),
                    "entry_quality_repair_hint": _prompt_text(
                        raw_action.get("entry_quality_repair_hint"),
                        limit=120,
                    ),
                    "entry_repair_targets": [
                        _prompt_text(row, limit=120)
                        for row in list(raw_action.get("entry_repair_targets") or [])[:6]
                        if _prompt_text(row, limit=120)
                    ],
                    "scale_blocked_by_entry_quality": raw_action.get(
                        "scale_blocked_by_entry_quality"
                    ),
                    "validation_evidence_status": _prompt_text(
                        raw_action.get("validation_evidence_status"),
                        limit=60,
                    ),
                    "validation_missing_dimensions": [
                        _prompt_text(row, limit=60)
                        for row in list(
                            raw_action.get("validation_missing_dimensions") or []
                        )[:4]
                        if _prompt_text(row, limit=60)
                    ],
                    "validation_failed_dimensions": [
                        _prompt_text(row, limit=60)
                        for row in list(
                            raw_action.get("validation_failed_dimensions") or []
                        )[:4]
                        if _prompt_text(row, limit=60)
                    ],
                    "validation_thin_dimensions": [
                        _prompt_text(row, limit=60)
                        for row in list(
                            raw_action.get("validation_thin_dimensions") or []
                        )[:4]
                        if _prompt_text(row, limit=60)
                    ],
                    "validation_evidence_repair_hint": _prompt_text(
                        raw_action.get("validation_evidence_repair_hint"),
                        limit=120,
                    ),
                    "validation_evidence_repair_targets": [
                        _prompt_text(row, limit=120)
                        for row in list(
                            raw_action.get("validation_evidence_repair_targets") or []
                        )[:8]
                        if _prompt_text(row, limit=120)
                    ],
                    "validation_evidence_required_evidence": [
                        _prompt_text(row, limit=100)
                        for row in list(
                            raw_action.get("validation_evidence_required_evidence")
                            or []
                        )[:8]
                        if _prompt_text(row, limit=100)
                    ],
                    "validation_evidence_required_checks": [
                        _prompt_text(row, limit=100)
                        for row in list(
                            raw_action.get("validation_evidence_required_checks")
                            or []
                        )[:8]
                        if _prompt_text(row, limit=100)
                    ],
                    "validation_evidence_pass_collection_hooks": [
                        _prompt_text(row, limit=140)
                        for row in list(
                            raw_action.get(
                                "validation_evidence_pass_collection_hooks"
                            )
                            or []
                        )[:6]
                        if _prompt_text(row, limit=140)
                    ],
                    "validation_evidence_pass_current_gaps": [
                        _prompt_text(row, limit=140)
                        for row in list(
                            raw_action.get("validation_evidence_pass_current_gaps")
                            or []
                        )[:6]
                        if _prompt_text(row, limit=140)
                    ],
                    "validation_evidence_pass_criteria": [
                        _prompt_text(row, limit=160)
                        for row in list(
                            raw_action.get("validation_evidence_pass_criteria")
                            or []
                        )[:6]
                        if _prompt_text(row, limit=160)
                    ],
                    "validation_evidence_verification_artifacts": [
                        _prompt_text(row, limit=160)
                        for row in list(
                            raw_action.get(
                                "validation_evidence_verification_artifacts"
                            )
                            or []
                        )[:6]
                        if _prompt_text(row, limit=160)
                    ],
                    "core_validation_evidence_gaps": [
                        _prompt_text(row, limit=80)
                        for row in list(
                            raw_action.get("core_validation_evidence_gaps") or []
                        )[:4]
                        if _prompt_text(row, limit=80)
                    ],
                    "scale_blocked_by_validation_evidence": raw_action.get(
                        "scale_blocked_by_validation_evidence"
                    ),
                    "validation_repair_enforced_count": raw_action.get(
                        "validation_repair_enforced_count"
                    ),
                    "validation_repair_scale_up_blocked_count": raw_action.get(
                        "validation_repair_scale_up_blocked_count"
                    ),
                    "validation_repair_waiting_entry_count": raw_action.get(
                        "validation_repair_waiting_entry_count"
                    ),
                    "validation_repair_rejected_count": raw_action.get(
                        "validation_repair_rejected_count"
                    ),
                    "validation_repair_avg_budget_multiplier": raw_action.get(
                        "validation_repair_avg_budget_multiplier"
                    ),
                    "validation_repair_action_counts": _compact_label_counts(
                        raw_action.get("validation_repair_action_counts")
                    ),
                    "validation_repair_adjustment_reason_counts": (
                        _compact_label_counts(
                            raw_action.get(
                                "validation_repair_adjustment_reason_counts"
                            )
                        )
                    ),
                    "scale_blocked_by_validation_repair": raw_action.get(
                        "scale_blocked_by_validation_repair"
                    ),
                    "requires_waiting_entry": raw_action.get(
                        "requires_waiting_entry"
                    ),
                    "scale_up_blocked_by_shadow_gate": raw_action.get(
                        "scale_up_blocked_by_shadow_gate"
                    ),
                    "scale_up_blocked_by_exposure_gate": raw_action.get(
                        "scale_up_blocked_by_exposure_gate"
                    ),
                    "scale_up_blocked_by_validation_remediation": raw_action.get(
                        "scale_up_blocked_by_validation_remediation"
                    ),
                    "scale_up_blocked_by_active_revision": raw_action.get(
                        "scale_up_blocked_by_active_revision"
                    ),
                    "entry_quality_requirements": [
                        _prompt_text(row, limit=120)
                        for row in list(
                            raw_action.get("entry_quality_requirements") or []
                        )[:4]
                        if _prompt_text(row, limit=120)
                    ],
                    "risk_budget_passport": compact_risk_budget_passport(
                        raw_action.get("risk_budget_passport")
                    ),
                    "validation_shadow_gate": compact_shadow_gate(
                        raw_action.get("validation_shadow_gate")
                    ),
                    "validation_exposure_gate": compact_exposure_gate(
                        raw_action.get("validation_exposure_gate")
                    ),
                    "validation_remediation_gate": compact_shadow_gate(
                        raw_action.get("validation_remediation_gate")
                    ),
                    "active_revision_gate": compact_shadow_gate(
                        raw_action.get("active_revision_gate")
                    ),
                }.items()
                if child not in (None, "", [], {})
            }
            compact_actions[lane_key] = slim_lane_action_for_prompt(
                compact_actions[lane_key]
            )
    weak_lanes = _compact_lane_names(value.get("weak_lanes"), limit=12)
    probe_lanes = _lane_probe_names(value, limit=12)
    scale_blocked_lanes = _lane_scale_blocked_names(value, limit=12)
    allow_scale_up = bool(value.get("global_scale_up_allowed"))
    compact = {
        "version": _prompt_text(value.get("version"), limit=60),
        "global_scale_up_allowed": allow_scale_up,
        "max_budget_multiplier": value.get("max_budget_multiplier"),
        "validation_gate_status": _prompt_text(
            value.get("validation_gate_status"),
            limit=80,
        ),
        "execution_posture": _lane_execution_posture(
            allow_scale_up=allow_scale_up,
            probe_lanes=probe_lanes,
            scale_blocked_lanes=scale_blocked_lanes,
            weak_lanes=weak_lanes,
        ),
        "probe_policy": (
            "scale-up is blocked, but small waiting-entry/probe blocks are allowed "
            "when price structure and safety gates agree"
        )
        if probe_lanes and not allow_scale_up
        else "",
        "probe_lane_count": len(probe_lanes),
        "probe_lane_names": probe_lanes,
        "scale_blocked_lane_count": len(scale_blocked_lanes),
        "scale_blocked_lanes": scale_blocked_lanes,
        "validation_shadow_gate": compact_shadow_gate(
            value.get("validation_shadow_gate")
        ),
        "validation_exposure_gate": compact_exposure_gate(
            value.get("validation_exposure_gate")
        ),
        "validation_remediation_gate": compact_shadow_gate(
            value.get("validation_remediation_gate")
        ),
        "active_revision_gate": compact_shadow_gate(
            value.get("active_revision_gate")
        ),
        "weak_lanes": weak_lanes,
        "scale_candidate_lanes": _compact_lane_names(
            value.get("scale_candidate_lanes"),
            limit=12,
        ),
        "qualified_lanes": _compact_lane_names(value.get("qualified_lanes"), limit=12),
        "insufficient_lanes": _compact_lane_names(
            value.get("insufficient_lanes"),
            limit=12,
        ),
        "cost_weak_lanes": _compact_lane_names(value.get("cost_weak_lanes"), limit=12),
        "cost_evidence_weak_lanes": _compact_lane_names(
            value.get("cost_evidence_weak_lanes"),
            limit=12,
        ),
        "entry_quality_weak_lanes": _compact_lane_names(
            value.get("entry_quality_weak_lanes"),
            limit=12,
        ),
        "validation_evidence_weak_lanes": _compact_lane_names(
            value.get("validation_evidence_weak_lanes"),
            limit=12,
        ),
        "validation_repair_weak_lanes": _compact_lane_names(
            value.get("validation_repair_weak_lanes"),
            limit=12,
        ),
        "shadow_blocked_lanes": _compact_lane_names(
            value.get("shadow_blocked_lanes"),
            limit=12,
        ),
        "exposure_blocked_lanes": _compact_lane_names(
            value.get("exposure_blocked_lanes"),
            limit=12,
        ),
        "remediation_blocked_lanes": _compact_lane_names(
            value.get("remediation_blocked_lanes"),
            limit=12,
        ),
        "lane_actions": compact_actions,
        "block_design_requirements": [
            _prompt_text(row, limit=120)
            for row in list(value.get("block_design_requirements") or [])[:8]
            if _prompt_text(row, limit=120)
        ],
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def performance_lanes_for_venue(
    summary: dict[str, Any],
    venue: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    clean_venue = _prompt_text(venue, limit=40).lower()
    if not clean_venue:
        return []
    lanes = summary.get("lanes")
    if not isinstance(lanes, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in lanes:
        if not isinstance(row, dict):
            continue
        row_venue = _prompt_text(row.get("venue"), limit=40).lower()
        if row_venue != clean_venue:
            continue
        lane = _prompt_text(row.get("lane"), limit=80)
        if not lane:
            continue
        rows.append(dict(row))
    rows.sort(
        key=lambda row: (
            _performance_lane_prompt_priority(row),
            _performance_lane_specificity_priority(row),
            str(row.get("lane") or ""),
        )
    )
    return rows[: max(int(limit or 0), 0)]


def _performance_lane_prompt_priority(row: dict[str, Any]) -> int:
    quality_hint = str(row.get("quality_hint") or "").strip().lower()
    action_hint = str(row.get("action_hint") or "").strip().lower()
    alpha_conversion_status = str(
        row.get("alpha_conversion_status") or ""
    ).strip().lower()
    if alpha_conversion_status == "blocked_by_fill_or_execution_evidence":
        return 0
    if quality_hint == "weak_review" or "reduce" in action_hint:
        return 0
    if "waiting" in action_hint or "build" in action_hint:
        return 1
    if quality_hint == "no_alpha_samples":
        return 2
    if quality_hint == "sample_building":
        return 3
    if quality_hint == "scale_candidate":
        return 4
    if quality_hint == "qualified":
        return 5
    return 6


def _performance_lane_specificity_priority(row: dict[str, Any]) -> int:
    lane = str(row.get("lane") or "")
    return 0 if ":" in lane else 1


def _compact_prompt_performance_lanes(
    value: Any,
    *,
    venue: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = value.get("lanes")
    else:
        rows = value
    if not isinstance(rows, list):
        return []
    clean_venue = _prompt_text(venue, limit=40).lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_venue = _prompt_text(row.get("venue"), limit=40).lower()
        if clean_venue and row_venue and row_venue != clean_venue:
            continue
        lane = _prompt_text(row.get("lane"), limit=80)
        if not lane:
            continue
        cost_counts_raw = row.get("cost_precision_counts")
        cost_counts = (
            {
                "recorded": _prompt_count(cost_counts_raw.get("recorded")),
                "hybrid": _prompt_count(cost_counts_raw.get("hybrid")),
                "estimated": _prompt_count(cost_counts_raw.get("estimated")),
                "partial": _prompt_count(cost_counts_raw.get("partial")),
                "missing": _prompt_count(cost_counts_raw.get("missing")),
            }
            if isinstance(cost_counts_raw, dict)
            else {}
        )
        cost_counts = {
            key: child
            for key, child in cost_counts.items()
            if child not in (None, "", [], {})
        }
        cost_drag = (
            row.get("cost_drag_pct_of_abs_gross_pnl")
            if row.get("cost_drag_pct_of_abs_gross_pnl") not in (None, "", [], {})
            else row.get("cost_drag_pct_of_gross_pnl")
        )
        compact = {
            "venue": row_venue or clean_venue,
            "lane": lane,
            "block_count": _prompt_count(row.get("block_count")),
            "alpha_count": _prompt_count(row.get("alpha_count")),
            "sample_count": _prompt_count(
                row.get("sample_count")
                if row.get("sample_count") not in (None, "", [], {})
                else row.get("alpha_count")
            ),
            "non_alpha_count": _prompt_count(row.get("non_alpha_count")),
            "unfilled_or_unrealized_count": _prompt_count(
                row.get("unfilled_or_unrealized_count")
            ),
            "operational_failure_pre_fill_count": _prompt_count(
                row.get("operational_failure_pre_fill_count")
            ),
            "adopted_position_count": _prompt_count(
                row.get("adopted_position_count")
            ),
            "execution_quality_count": _prompt_count(
                row.get("execution_quality_count")
            ),
            "alpha_conversion_status": _prompt_text(
                row.get("alpha_conversion_status"),
                limit=80,
            ),
            "alpha_conversion_repair_hint": _prompt_text(
                row.get("alpha_conversion_repair_hint"),
                limit=180,
            ),
            "expectancy_pct": _prompt_float(row.get("expectancy_pct")),
            "win_rate_pct": _prompt_float(row.get("win_rate_pct")),
            "profit_factor": _prompt_float(row.get("profit_factor")),
            "max_drawdown_pct": _prompt_float(row.get("max_drawdown_pct")),
            "recovery_factor": _prompt_float(row.get("recovery_factor")),
            "cumulative_return_pct": _prompt_float(row.get("cumulative_return_pct")),
            "cost_drag_pct_of_abs_gross_pnl": _prompt_float(cost_drag),
            "cost_drag_pct_of_gross_pnl": _prompt_float(
                row.get("cost_drag_pct_of_gross_pnl")
            ),
            "cost_precision_counts": cost_counts,
            "missing_cost_component_counts": _compact_label_counts(
                row.get("missing_cost_component_counts")
            ),
            "present_cost_component_counts": _compact_label_counts(
                row.get("present_cost_component_counts")
            ),
            "required_cost_component_counts": _compact_label_counts(
                row.get("required_cost_component_counts")
            ),
            "cost_precision_reason_counts": _compact_label_counts(
                row.get("cost_precision_reason_counts"),
                limit=6,
            ),
            "cost_repair_targets": _cost_evidence_repair_targets(row),
            "validation_pressure_severity_counts": _compact_label_counts(
                row.get("validation_pressure_severity_counts")
            ),
            "validation_pressure_entry_posture_counts": _compact_label_counts(
                row.get("validation_pressure_entry_posture_counts")
            ),
            "validation_pressure_sizing_posture_counts": _compact_label_counts(
                row.get("validation_pressure_sizing_posture_counts")
            ),
            "validation_pressure_fail_id_counts": _compact_label_counts(
                row.get("validation_pressure_fail_id_counts")
            ),
            "validation_pressure_warn_id_counts": _compact_label_counts(
                row.get("validation_pressure_warn_id_counts")
            ),
            "validation_pressure_missing_id_counts": _compact_label_counts(
                row.get("validation_pressure_missing_id_counts")
            ),
            "validation_pressure_discipline_action_counts": _compact_label_counts(
                row.get("validation_pressure_discipline_action_counts"),
                limit=12,
            ),
            "cost_precision_verified_rate": _prompt_float(
                row.get("cost_precision_verified_rate")
            ),
            "cost_evidence_status": _prompt_text(
                row.get("cost_evidence_status"),
                limit=80,
            ),
            "alpha_evidence_status": _prompt_text(
                row.get("alpha_evidence_status"),
                limit=80,
            ),
            "cost_verified_alpha_count": _prompt_count(
                row.get("cost_verified_alpha_count")
            ),
            "cost_hybrid_alpha_count": _prompt_count(
                row.get("cost_hybrid_alpha_count")
            ),
            "cost_unverified_alpha_count": _prompt_count(
                row.get("cost_unverified_alpha_count")
            ),
            "cost_verified_alpha_net_pnl": _prompt_float(
                row.get("cost_verified_alpha_net_pnl")
            ),
            "cost_hybrid_alpha_net_pnl": _prompt_float(
                row.get("cost_hybrid_alpha_net_pnl")
            ),
            "cost_unverified_alpha_net_pnl": _prompt_float(
                row.get("cost_unverified_alpha_net_pnl")
            ),
            "scale_blocked_by_cost_evidence": row.get(
                "scale_blocked_by_cost_evidence"
            ),
            "scale_blocked_by_cost_precision": row.get(
                "scale_blocked_by_cost_precision"
            ),
            "scale_blocked_by_verified_edge_net_pnl": row.get(
                "scale_blocked_by_verified_edge_net_pnl"
            ),
            "risk_model_status": _prompt_text(
                row.get("risk_model_status"),
                limit=80,
            ),
            "lane_confidence_score": _prompt_float(row.get("lane_confidence_score")),
            "risk_of_ruin_pct": _prompt_float(row.get("risk_of_ruin_pct")),
            "recovery_factor_cap_multiplier": _prompt_float(
                row.get("recovery_factor_cap_multiplier")
            ),
            "recommended_risk_fraction": _prompt_float(
                row.get("recommended_risk_fraction")
            ),
            "max_risk_cap_fraction": _prompt_float(row.get("max_risk_cap_fraction")),
            "risk_budget_multiplier": _prompt_float(row.get("risk_budget_multiplier")),
            "entry_quality_sample_count": _prompt_count(
                row.get("entry_quality_sample_count")
            ),
            "avg_entry_quality_score": _prompt_float(
                row.get("avg_entry_quality_score")
            ),
            "bad_entry_quality_rate_pct": _prompt_float(
                row.get("bad_entry_quality_rate_pct")
            ),
            "entry_quality_label_counts": _compact_label_counts(
                row.get("entry_quality_label_counts")
            ),
            "bad_entry_quality_label_counts": _compact_label_counts(
                row.get("bad_entry_quality_label_counts")
            ),
            "good_entry_quality_label_counts": _compact_label_counts(
                row.get("good_entry_quality_label_counts")
            ),
            "dominant_bad_entry_quality_label": _prompt_text(
                row.get("dominant_bad_entry_quality_label"),
                limit=80,
            ),
            "dominant_good_entry_quality_label": _prompt_text(
                row.get("dominant_good_entry_quality_label"),
                limit=80,
            ),
            "entry_repair_targets": _entry_quality_repair_targets(row),
            "scale_blocked_by_entry_quality": row.get(
                "scale_blocked_by_entry_quality"
            ),
            "quality_hint": _prompt_text(row.get("quality_hint"), limit=80),
            "action_hint": _prompt_text(row.get("action_hint"), limit=160),
        }
        out.append(
            {
                key: child
                for key, child in compact.items()
                if child not in (None, "", [], {})
            }
        )
    out.sort(
        key=lambda row: (
            _performance_lane_prompt_priority(row),
            _performance_lane_specificity_priority(row),
            row.get("lane", ""),
        )
    )
    return out[: max(int(limit or 0), 0)]


def _compact_prompt_repair_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_actions = value.get("actions") if isinstance(value.get("actions"), list) else []
    actions: list[dict[str, Any]] = []
    for row in raw_actions[:6]:
        if not isinstance(row, dict):
            continue
        action = {
            "discipline_id": _prompt_text(row.get("discipline_id"), limit=80),
            "priority": _prompt_text(row.get("priority"), limit=20),
            "status": _prompt_text(row.get("status"), limit=60),
            "validation_mode": _prompt_text(row.get("validation_mode"), limit=100),
            "scale_up_blocked": row.get("scale_up_blocked"),
            "live_shadow_required": row.get("live_shadow_required"),
            "artifact": _prompt_text(row.get("artifact"), limit=120),
            "reason": _prompt_text(row.get("reason"), limit=180),
            "runner_status": _prompt_text(row.get("runner_status"), limit=60),
            "discipline_status": _prompt_text(
                row.get("discipline_status"),
                limit=60,
            ),
            "active_optimized_set_count": _prompt_count(
                row.get("active_optimized_set_count")
            ),
            "evidence_status": _prompt_text(row.get("evidence_status"), limit=80),
            "evidence_reasons": [
                _prompt_text(reason, limit=100)
                for reason in list(row.get("evidence_reasons") or [])[:4]
                if _prompt_text(reason, limit=100)
            ],
            "recorded_cost_coverage_pct": _prompt_float(
                row.get("recorded_cost_coverage_pct")
            ),
            "cost_stress_2x_net_pnl": _prompt_float(
                row.get("cost_stress_2x_net_pnl")
            ),
            "recommended_risk_fraction": _prompt_float(
                row.get("recommended_risk_fraction")
            ),
            "risk_of_ruin_pct": _prompt_float(row.get("risk_of_ruin_pct")),
            "profit_factor": _prompt_float(row.get("profit_factor")),
            "recovery_factor": _prompt_float(row.get("recovery_factor")),
        }
        actions.append(
            {
                key: child
                for key, child in action.items()
                if child not in (None, "", [], {})
            }
        )
    compact = {
        "version": _prompt_text(value.get("version"), limit=80),
        "source_version": _prompt_text(value.get("source_version"), limit=80),
        "venue": _prompt_text(value.get("venue"), limit=40),
        "status": _prompt_text(value.get("status"), limit=60),
        "item_count": _prompt_count(value.get("item_count")),
        "executed_count": _prompt_count(value.get("executed_count")),
        "queued_count": _prompt_count(value.get("queued_count")),
        "m1_execution_posture": _prompt_text(
            value.get("m1_execution_posture"),
            limit=80,
        ),
        "actions": actions,
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def compact_live_authority_for_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    gate = (
        payload.get("validation_gate")
        if isinstance(payload.get("validation_gate"), dict)
        else {}
    )
    compact = {
        "status": _prompt_text(payload.get("status"), limit=60),
        "live_grade": _prompt_text(payload.get("live_grade"), limit=60),
        "allow_scale_up": payload.get("allow_scale_up"),
        "max_budget_multiplier": payload.get("max_budget_multiplier"),
        "scorecard_count": payload.get("scorecard_count"),
    }
    out = {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }
    lane_authority = _compact_prompt_lane_authority(payload.get("lane_authority"))
    if lane_authority:
        out["lane_authority"] = lane_authority
    active_revision_evidence = _compact_prompt_active_revision_evidence(
        payload.get("active_revision_evidence")
    )
    if active_revision_evidence:
        out["active_revision_evidence"] = active_revision_evidence
    performance_payload = (
        payload.get("performance")
        if isinstance(payload.get("performance"), dict)
        else {}
    )
    performance_lanes = _compact_prompt_performance_lanes(
        payload.get("performance_lanes") or performance_payload.get("lanes"),
        venue=_prompt_text(payload.get("venue"), limit=40),
    )
    if performance_lanes:
        out["performance_lanes"] = performance_lanes
        out["performance_lane_policy"] = {
            "role": "realized lane feedback",
            "instruction": (
                "Use quality_hint and action_hint as live evidence. Weak lanes "
                "should shrink or switch to patient waiting entries. Scale only "
                "when the lane has positive alpha samples, controlled drawdown, "
                "acceptable cost drag, and validation gates agree."
            ),
        }
    repair_execution = _compact_prompt_repair_execution(payload.get("repair_execution"))
    if repair_execution:
        out["repair_execution"] = repair_execution
    if not gate:
        return out
    gate_payload: dict[str, Any] = {
        "status": _prompt_text(gate.get("status"), limit=80),
        "readiness": _prompt_text(gate.get("readiness"), limit=80),
        "reason": _prompt_text(gate.get("reason"), limit=220),
        "fail_count": gate.get("fail_count"),
        "discipline_count": gate.get("discipline_count"),
        "expected_discipline_count": gate.get("expected_discipline_count"),
        "risk_governor_action": _prompt_text(
            gate.get("risk_governor_action"),
            limit=80,
        ),
        "risk_governor_source": _prompt_text(
            gate.get("risk_governor_source"),
            limit=80,
        ),
        "risk_governor_reasons": [
            _prompt_text(row, limit=160)
            for row in list(gate.get("risk_governor_reasons") or [])[:4]
            if _prompt_text(row, limit=160)
        ],
    }
    for key in ("failed_disciplines", "weak_disciplines"):
        rows = gate.get(key)
        if isinstance(rows, list):
            gate_payload[key] = [
                _compact_prompt_discipline(row)
                for row in rows[:6]
                if isinstance(row, dict)
            ]
    cooldown = _compact_prompt_loss_cooldown(gate.get("loss_cooldown"))
    if cooldown:
        gate_payload["loss_cooldown"] = cooldown
    recovery_focus = gate.get("validation_recovery_focus")
    if isinstance(recovery_focus, list):
        gate_payload["validation_recovery_focus"] = [
            {
                key: _prompt_text(row.get(key), limit=180)
                if key in {"source", "reason", "action"}
                else row.get(key)
                for key in ("source", "reason", "action", "active_set_count")
                if isinstance(row, dict)
                and row.get(key) not in (None, "", [], {})
            }
            for row in recovery_focus[:4]
            if isinstance(row, dict)
        ]
    guidance = gate.get("operator_guidance")
    if isinstance(guidance, list):
        gate_payload["operator_guidance"] = [
            _prompt_text(row, limit=240)
            for row in guidance[:4]
            if _prompt_text(row, limit=240)
        ]
    remediation_plan = _compact_prompt_remediation_plan(gate.get("remediation_plan"))
    if remediation_plan:
        gate_payload["remediation_plan"] = remediation_plan
    lane_scorecards = _validation_lane_scorecards(payload.get("trading_validation"))
    if not lane_scorecards:
        lane_scorecards = _validation_lane_scorecards(
            {"payload": {"metrics": {"lane_scorecards": gate.get("lane_scorecards")}}}
        )
    if lane_scorecards:
        gate_payload["lane_scorecards"] = lane_scorecards
    validation_matrix = _compact_prompt_validation_matrix(
        payload.get("trading_validation"),
        gate=gate,
    )
    if validation_matrix:
        if validation_matrix.get("actual_count") is not None:
            gate_payload["discipline_count"] = validation_matrix.get("actual_count")
        if validation_matrix.get("expected_count") is not None:
            gate_payload["expected_discipline_count"] = validation_matrix.get("expected_count")
        gate_payload["discipline_matrix"] = validation_matrix
    validation_passport = _compact_prompt_validation_passport(
        payload.get("trading_validation"),
        gate=gate,
    )
    if validation_passport:
        gate_payload["validation_passport"] = validation_passport
    validation_pressure = _compact_prompt_validation_pressure(
        payload.get("trading_validation"),
        gate=gate,
        allow_scale_up=payload.get("allow_scale_up"),
    )
    if validation_pressure:
        gate_payload["validation_pressure"] = validation_pressure
    cost_attribution = _validation_cost_attribution(payload.get("trading_validation"))
    if not cost_attribution:
        cost_attribution = _compact_prompt_cost_attribution(
            gate.get("cost_attribution")
        )
    if cost_attribution:
        gate_payload["cost_attribution"] = cost_attribution
    failure_attribution = _compact_prompt_failure_attribution(
        gate.get("failure_attribution")
    )
    if failure_attribution:
        gate_payload["failure_attribution"] = failure_attribution
    out["validation_gate"] = {
        key: child
        for key, child in gate_payload.items()
        if child not in (None, "", [], {})
    }
    return out


def compact_live_authority_for_status(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out = {
        key: child
        for key, child in {
            "status": _prompt_text(payload.get("status"), limit=60),
            "live_grade": _prompt_text(payload.get("live_grade"), limit=60),
            "allow_scale_up": payload.get("allow_scale_up"),
            "max_budget_multiplier": payload.get("max_budget_multiplier"),
            "scorecard_count": payload.get("scorecard_count"),
        }.items()
        if child not in (None, "", [], {})
    }
    lane_authority = (
        payload.get("lane_authority")
        if isinstance(payload.get("lane_authority"), dict)
        else {}
    )
    if lane_authority:
        blocked_lanes = set()
        for key in (
            "shadow_blocked_lanes",
            "exposure_blocked_lanes",
            "remediation_blocked_lanes",
        ):
            rows = lane_authority.get(key)
            if isinstance(rows, list):
                blocked_lanes.update(_prompt_text(row, limit=80) for row in rows)
        weak_lanes = (
            _compact_lane_names(lane_authority.get("weak_lanes"), limit=12)
            if isinstance(lane_authority.get("weak_lanes"), list)
            else []
        )
        probe_lanes = _lane_probe_names(lane_authority, limit=12)
        scale_blocked_lanes = _lane_scale_blocked_names(lane_authority, limit=12)
        lane_actions = (
            lane_authority.get("lane_actions")
            if isinstance(lane_authority.get("lane_actions"), dict)
            else {}
        )
        compact_lane_actions: dict[str, dict[str, Any]] = {}
        for lane, raw_action in list(lane_actions.items())[:4]:
            if not isinstance(raw_action, dict):
                continue
            lane_key = _prompt_text(lane, limit=80)
            if not lane_key:
                continue
            action = {
                key: child
                for key, child in {
                    "grade": _prompt_text(raw_action.get("grade"), limit=60),
                    "action": _prompt_text(raw_action.get("action"), limit=60),
                    "sample_count": raw_action.get("sample_count"),
                    "max_budget_multiplier": raw_action.get("max_budget_multiplier"),
                    "applied_max_budget_multiplier": raw_action.get(
                        "applied_max_budget_multiplier"
                    ),
                    "scale_up_allowed": raw_action.get("scale_up_allowed"),
                    "scale_decision": _prompt_text(
                        raw_action.get("scale_decision"),
                        limit=80,
                    ),
                    "requires_waiting_entry": raw_action.get(
                        "requires_waiting_entry"
                    ),
                    "scale_blockers": [
                        _prompt_text(row, limit=80)
                        for row in list(raw_action.get("scale_blockers") or [])[:1]
                        if _prompt_text(row, limit=80)
                    ],
                    "scale_repair_targets": [
                        _prompt_text(row, limit=80)
                        for row in list(
                            raw_action.get("scale_repair_targets") or []
                        )[:1]
                        if _prompt_text(row, limit=80)
                    ],
                }.items()
                if child not in (None, "", [], {})
            }
            if action:
                compact_lane_actions[lane_key] = action
        insufficient_lanes = lane_authority.get("insufficient_lanes")
        allow_scale_up = bool(
            lane_authority.get("global_scale_up_allowed")
            or payload.get("allow_scale_up")
        )
        out["lane_authority"] = {
            key: child
            for key, child in {
                "version": _prompt_text(lane_authority.get("version"), limit=80),
                "global_scale_up_allowed": lane_authority.get(
                    "global_scale_up_allowed"
                ),
                "max_budget_multiplier": lane_authority.get("max_budget_multiplier"),
                "validation_gate_status": _prompt_text(
                    lane_authority.get("validation_gate_status"),
                    limit=80,
                ),
                "execution_posture": _lane_execution_posture(
                    allow_scale_up=allow_scale_up,
                    probe_lanes=probe_lanes,
                    scale_blocked_lanes=scale_blocked_lanes,
                    weak_lanes=weak_lanes,
                ),
                "weak_lane_count": len(weak_lanes),
                "insufficient_lane_count": len(insufficient_lanes)
                if isinstance(insufficient_lanes, list)
                else 0,
                "blocked_lane_count": len([row for row in blocked_lanes if row]),
                "scale_blocked_lane_count": len(scale_blocked_lanes),
                "probe_lane_count": len(probe_lanes),
                "probe_lane_names": probe_lanes[:6],
                "scale_blocked_lanes": scale_blocked_lanes[:6],
                "lane_action_count": len(lane_actions),
                "lane_actions": compact_lane_actions,
            }.items()
            if child not in (None, "", [], {})
        }
    active_revision_evidence = _compact_prompt_active_revision_evidence(
        payload.get("active_revision_evidence")
    )
    if active_revision_evidence:
        out["active_revision_evidence"] = active_revision_evidence
    pending_active_revision_blocks = _compact_status_pending_active_revision_blocks(
        payload.get("pending_active_revision_blocks")
    )
    if pending_active_revision_blocks:
        out["pending_active_revision_blocks"] = pending_active_revision_blocks
    gate = (
        payload.get("validation_gate")
        if isinstance(payload.get("validation_gate"), dict)
        else {}
    )
    if gate:
        reasons = gate.get("risk_governor_reasons")
        gate_payload = {
            key: child
            for key, child in {
                "status": _prompt_text(gate.get("status"), limit=80),
                "readiness": _prompt_text(gate.get("readiness"), limit=80),
                "reason": _prompt_text(gate.get("reason"), limit=220),
                "fail_count": gate.get("fail_count"),
                "hard_fail_count": gate.get("hard_fail_count"),
                "hard_missing_count": gate.get("hard_missing_count"),
                "hard_blocking_count": gate.get("hard_blocking_count"),
                "diagnostic_fail_count": gate.get("diagnostic_fail_count"),
                "core_fail_count": gate.get("core_fail_count"),
                "core_missing_count": gate.get("core_missing_count"),
                "discipline_count": gate.get("discipline_count"),
                "expected_discipline_count": gate.get("expected_discipline_count"),
                "original_max_budget_multiplier": gate.get(
                    "original_max_budget_multiplier"
                ),
                "applied_max_budget_multiplier": gate.get(
                    "applied_max_budget_multiplier"
                ),
                "risk_governor_action": _prompt_text(
                    gate.get("risk_governor_action"),
                    limit=80,
                ),
                "risk_governor_source": _prompt_text(
                    gate.get("risk_governor_source"),
                    limit=80,
                ),
                "risk_governor_reasons": [
                    _prompt_text(row, limit=160)
                    for row in list(reasons or [])[:3]
                    if _prompt_text(row, limit=160)
                ],
            }.items()
            if child not in (None, "", [], {})
        }
        failure_attribution = _compact_prompt_failure_attribution(
            gate.get("failure_attribution")
        )
        if failure_attribution:
            gate_payload["failure_attribution"] = failure_attribution
        cost_attribution = _compact_status_cost_attribution(
            gate.get("cost_attribution")
        )
        if cost_attribution:
            gate_payload["cost_attribution"] = cost_attribution
        validation_matrix = _compact_status_validation_matrix(
            payload.get("trading_validation"),
            gate=gate,
        )
        if validation_matrix:
            if validation_matrix.get("actual_count") is not None:
                gate_payload["discipline_count"] = validation_matrix.get(
                    "actual_count"
                )
            if validation_matrix.get("expected_count") is not None:
                gate_payload["expected_discipline_count"] = validation_matrix.get(
                    "expected_count"
                )
            gate_payload["discipline_matrix"] = validation_matrix
        validation_passport = _compact_prompt_validation_passport(
            payload.get("trading_validation"),
            gate=gate,
        )
        if validation_passport:
            gate_payload["validation_passport"] = validation_passport
        out["validation_gate"] = gate_payload
    performance_lanes = payload.get("performance_lanes")
    if isinstance(performance_lanes, list):
        out["performance_lanes"] = [
            {
                key: child
                for key, child in {
                    "lane": _prompt_text(row.get("lane"), limit=80),
                    "quality_hint": _prompt_text(row.get("quality_hint"), limit=120),
                    "action_hint": _prompt_text(row.get("action_hint"), limit=120),
                    "sample_count": _prompt_count(row.get("sample_count")),
                    "win_rate_pct": row.get("win_rate_pct"),
                    "expectancy_pct": row.get("expectancy_pct"),
                    "profit_factor": row.get("profit_factor"),
                }.items()
                if isinstance(row, dict) and child not in (None, "", [], {})
            }
            for row in performance_lanes[:6]
            if isinstance(row, dict)
        ]
    repair_execution = (
        payload.get("repair_execution")
        if isinstance(payload.get("repair_execution"), dict)
        else {}
    )
    if repair_execution:
        compact_repair_execution = _compact_status_repair_execution(repair_execution)
        if compact_repair_execution:
            out["repair_execution"] = compact_repair_execution
    return out


def _compact_status_cost_attribution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_group(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: child
            for key, child in {
                "group_type": _prompt_text(row.get("group_type"), limit=40),
                "group": _prompt_text(row.get("group"), limit=60),
                "sample_count": row.get("sample_count"),
                "total_net_pnl": row.get("total_net_pnl"),
                "total_cost": row.get("total_cost"),
                "cost_drag_pct_of_abs_gross_pnl": row.get(
                    "cost_drag_pct_of_abs_gross_pnl"
                ),
                "net_negative_after_cost": row.get("net_negative_after_cost"),
                "symbols": [
                    _prompt_text(symbol, limit=24)
                    for symbol in list(row.get("symbols") or [])[:3]
                    if _prompt_text(symbol, limit=24)
                ],
                "block_ids": [
                    _prompt_text(block_id, limit=48)
                    for block_id in list(row.get("block_ids") or [])[:2]
                    if _prompt_text(block_id, limit=48)
                ],
            }.items()
            if child not in (None, "", [], {})
        }

    def compact_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: child
            for key, child in {
                "block_id": _prompt_text(row.get("block_id"), limit=48),
                "symbol": _prompt_text(row.get("symbol"), limit=24),
                "horizon": _prompt_text(row.get("horizon"), limit=32),
                "strategy_family": _prompt_text(
                    row.get("strategy_family"),
                    limit=48,
                ),
                "net_pnl": row.get("net_pnl"),
                "cost_total": row.get("cost_total"),
                "cost_drag_pct_of_abs_gross_pnl": row.get(
                    "cost_drag_pct_of_abs_gross_pnl"
                ),
                "net_negative_after_cost": row.get("net_negative_after_cost"),
            }.items()
            if child not in (None, "", [], {})
        }

    compact = {
        "status": _prompt_text(value.get("status"), limit=60),
        "sample_count": value.get("sample_count"),
        "total_cost": value.get("total_cost"),
        "cost_drag_pct_of_gross_pnl": value.get("cost_drag_pct_of_gross_pnl"),
        "groups": [
            compact_group(row)
            for row in list(value.get("worst_cost_groups") or value.get("groups") or [])[:2]
            if isinstance(row, dict)
        ],
        "rows": [
            compact_row(row)
            for row in list(value.get("worst_cost_rows") or value.get("rows") or [])[:2]
            if isinstance(row, dict)
        ],
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _compact_status_repair_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_actions = value.get("actions") if isinstance(value.get("actions"), list) else []
    actions: list[dict[str, Any]] = []
    for row in raw_actions[:3]:
        if not isinstance(row, dict):
            continue
        action = {
            "discipline_id": _prompt_text(row.get("discipline_id"), limit=60),
            "priority": _prompt_text(row.get("priority"), limit=20),
            "status": _prompt_text(row.get("status"), limit=50),
            "validation_mode": _prompt_text(row.get("validation_mode"), limit=70),
            "scale_up_blocked": row.get("scale_up_blocked"),
            "live_shadow_required": row.get("live_shadow_required"),
            "artifact": _prompt_text(row.get("artifact"), limit=80),
            "reason": _prompt_text(row.get("reason"), limit=80),
            "runner_status": _prompt_text(row.get("runner_status"), limit=50),
            "discipline_status": _prompt_text(row.get("discipline_status"), limit=50),
            "evidence_status": _prompt_text(row.get("evidence_status"), limit=60),
            "profit_factor": _prompt_float(row.get("profit_factor")),
        }
        actions.append(
            {
                key: child
                for key, child in action.items()
                if child not in (None, "", [], {})
            }
        )
    compact = {
        "version": _prompt_text(value.get("version"), limit=60),
        "source_version": _prompt_text(value.get("source_version"), limit=60),
        "venue": _prompt_text(value.get("venue"), limit=40),
        "status": _prompt_text(value.get("status"), limit=60),
        "item_count": _prompt_count(value.get("item_count")),
        "executed_count": _prompt_count(value.get("executed_count")),
        "queued_count": _prompt_count(value.get("queued_count")),
        "m1_execution_posture": _prompt_text(
            value.get("m1_execution_posture"),
            limit=70,
        ),
        "actions": actions,
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _compact_status_pending_active_revision_blocks(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        "version": _prompt_text(value.get("version"), limit=80),
        "venue": _prompt_text(value.get("venue"), limit=40),
        "strategy_revision_id": _prompt_text(
            value.get("strategy_revision_id"),
            limit=80,
        ),
        "status": _prompt_text(value.get("status"), limit=80),
        "pending_block_count": _prompt_count(value.get("pending_block_count")),
        "pending_block_status_counts": (
            value.get("pending_block_status_counts")
            if isinstance(value.get("pending_block_status_counts"), dict)
            else {}
        ),
        "pending_block_lane_counts": (
            value.get("pending_block_lane_counts")
            if isinstance(value.get("pending_block_lane_counts"), dict)
            else {}
        ),
    }
    rows = value.get("blocks") if isinstance(value.get("blocks"), list) else []
    blocks: list[dict[str, Any]] = []
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        block = {
            "block_id": _prompt_text(row.get("block_id"), limit=80),
            "symbol": _prompt_text(row.get("symbol"), limit=40),
            "name": _prompt_text(row.get("name"), limit=80),
            "status": _prompt_text(row.get("status"), limit=60),
            "horizon": _prompt_text(row.get("horizon"), limit=40),
            "created_by": _prompt_text(row.get("created_by"), limit=40),
        }
        blocks.append(
            {
                key: child
                for key, child in block.items()
                if child not in (None, "", [], {})
            }
        )
    if blocks:
        compact["blocks"] = blocks
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _compact_status_validation_matrix(
    validation: Any,
    *,
    gate: dict[str, Any],
) -> dict[str, Any]:
    matrix = _compact_prompt_validation_matrix(validation, gate=gate)
    if not matrix:
        return {}
    statuses = matrix.get("statuses")
    if isinstance(statuses, list):
        focused = [
            row
            for row in statuses
            if isinstance(row, dict)
            and _prompt_text(row.get("status"), limit=40) in {"fail", "warn", "missing"}
        ][:8]
        if focused:
            matrix["statuses"] = focused
        else:
            matrix.pop("statuses", None)
    return matrix


def _validation_recovery_focus(validation: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _validation_payload(validation)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    pattern_lab = (
        metrics.get("pattern_lab")
        if isinstance(metrics.get("pattern_lab"), dict)
        else {}
    )
    if not pattern_lab:
        return []
    reasons = pattern_lab.get("validation_reasons")
    if not isinstance(reasons, list):
        return []
    action_by_reason = {
        "active_walk_forward_windows_missing": (
            "Re-run rolling WFA windows for active optimized sets."
        ),
        "active_out_of_sample_missing": (
            "Rebuild out-of-sample evidence for active optimized sets."
        ),
        "active_overfit_unknown": (
            "Recompute overfit risk before allowing scale-up."
        ),
        "active_overfit_high": (
            "Demote high-overfit active sets and re-optimize."
        ),
    }
    focus: list[dict[str, Any]] = []
    for raw_reason in reasons[:4]:
        reason = str(raw_reason or "").strip()
        if not reason:
            continue
        item = {
            "source": "pattern_lab",
            "status": str(
                pattern_lab.get("validation_status")
                or pattern_lab.get("status")
                or ""
            ),
            "reason": reason,
            "action": action_by_reason.get(
                reason,
                "Repair pattern lab validation evidence before scale-up.",
            ),
            "source_scope": pattern_lab.get("source_scope"),
            "active_set_count": pattern_lab.get("active_set_count"),
            "active_oos_coverage_rate_pct": pattern_lab.get(
                "active_out_of_sample_coverage_rate_pct"
            ),
            "active_wfa_coverage_rate_pct": pattern_lab.get(
                "active_walk_forward_coverage_rate_pct"
            ),
        }
        focus.append(
            {
                key: value
                for key, value in item.items()
                if value is not None and value != ""
            }
        )
    return focus


GOVERNOR_RANK = {
    "normal": 0,
    "reduced": 1,
    "de_risk": 2,
    "risk_off": 3,
    "halt_new_risk": 4,
    "no_samples": 1,
}


def _validation_risk_governor(validation: dict[str, Any]) -> dict[str, Any]:
    payload = _validation_payload(validation)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    if not metrics:
        return {}
    summary = (
        payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else validation.get("summary")
        if isinstance(validation.get("summary"), dict)
        else {}
    )
    hard_fail_count = _validation_hard_fail_count(summary)
    sources = ("ruin_profile", "drawdown_budget", "kelly_sizing")
    actions: list[tuple[str, str]] = []
    for source in sources:
        packet = metrics.get(source)
        if not isinstance(packet, dict):
            continue
        action = str(packet.get("governor_action") or "").strip()
        if action:
            actions.append((source, action))
        elif source == "kelly_sizing":
            cap_reason = str(packet.get("cap_reason") or "").strip()
            if cap_reason in {
                "mdd_limit",
                "risk_of_ruin_limit",
                "no_positive_edge",
            }:
                actions.append((source, "halt_new_risk"))
            elif cap_reason in {
                "insufficient_sample_cap",
                "validation_quality_warning_cap",
                "validation_quality_missing_cap",
            }:
                actions.append((source, "de_risk"))
    if not actions:
        return {}
    selected_source, selected_action = max(
        actions,
        key=lambda item: GOVERNOR_RANK.get(item[1], 0),
    )
    demoted_diagnostic_halt = False
    if selected_action == "halt_new_risk" and hard_fail_count <= 0:
        selected_action = "de_risk"
        demoted_diagnostic_halt = True
    action_cap = {
        "halt_new_risk": 0.0,
        "risk_off": 0.25,
        "de_risk": 0.5,
        "reduced": 0.75,
        "no_samples": 0.5,
    }.get(selected_action, 1.0)
    if selected_source == "kelly_sizing" and not demoted_diagnostic_halt:
        kelly = metrics.get("kelly_sizing")
        if isinstance(kelly, dict):
            recommended = _prompt_float(kelly.get("recommended_risk_fraction"))
            max_cap = _prompt_float(kelly.get("max_risk_cap_fraction"))
            if max_cap > 0:
                action_cap = min(action_cap, max(recommended / max_cap, 0.0))
    reasons = [f"{source}:{action}" for source, action in actions]
    if demoted_diagnostic_halt:
        reasons.append("hard_gate_clear:diagnostic_halt_demoted_to_de_risk")
    ruin_profile = (
        metrics.get("ruin_profile")
        if isinstance(metrics.get("ruin_profile"), dict)
        else {}
    )
    kelly_sizing = (
        metrics.get("kelly_sizing")
        if isinstance(metrics.get("kelly_sizing"), dict)
        else {}
    )
    drawdown_budget = (
        metrics.get("drawdown_budget")
        if isinstance(metrics.get("drawdown_budget"), dict)
        else {}
    )
    metrics_payload = {
        "risk_of_ruin_pct": ruin_profile.get("risk_of_ruin_pct"),
        "recommended_risk_fraction": kelly_sizing.get("recommended_risk_fraction"),
        "max_risk_cap_fraction": kelly_sizing.get("max_risk_cap_fraction"),
        "kelly_cap_reason": kelly_sizing.get("cap_reason"),
        "drawdown_usage_ratio": drawdown_budget.get("drawdown_usage_ratio"),
    }
    return {
        "action": selected_action,
        "source": selected_source,
        "cap_multiplier": action_cap,
        "reasons": reasons,
        "metrics": {
            key: value
            for key, value in metrics_payload.items()
            if value not in (None, "", [], {})
        },
    }


@dataclass(frozen=True, slots=True)
class LiveAuthorityConfig:
    base_budget_multiplier: float = 1.0
    max_scale_multiplier: float = 1.5
    observe_only_multiplier: float = 0.5
    min_samples_to_scale: int = 10


def _lowest_effective_grade(scorecards: list[dict[str, Any]]) -> str:
    if not scorecards:
        return "observe_only"
    grades = [
        _normalize_authority_grade(row.get("grade"))
        for row in scorecards
    ]
    actionable = [
        grade
        for grade in grades
        if grade not in {"restricted", "observe_only"}
    ]
    if actionable:
        return max(actionable, key=lambda grade: GRADE_RANK.get(grade, 0))
    if "restricted" in grades:
        return "restricted"
    return "observe_only"


def _normalize_authority_grade(value: Any) -> str:
    grade = str(value or "observe_only").strip().lower()
    if grade in WEAK_GRADE_ALIASES:
        return "restricted"
    if grade in GRADE_RANK:
        return grade
    return "observe_only"


def _canonical_authority_lane_key(value: Any) -> str:
    lane = _prompt_text(value, limit=80).strip().lower()
    if not lane:
        return ""

    parts = [part for part in lane.split(":") if part]
    if len(parts) >= 4:
        if parts[:2] == parts[2:4]:
            return ":".join(parts[:2])
        if (
            parts[0] in {"spot", "upbit_spot"}
            and parts[1] == "long"
            and parts[2] in {"short", "mid", "long"}
            and parts[3] == parts[2]
        ):
            return ":".join(parts[:3])

    if (
        len(parts) == 3
        and parts[0] in {"futures", "perp", "perpetual"}
        and parts[1] in {"long", "short"}
        and parts[2] in {"futures", "perp", "perpetual"}
    ):
        return f"futures:{parts[1]}"

    return lane


def _lane_key_from_scorecard(row: dict[str, Any]) -> str:
    lane = _canonical_authority_lane_key(row.get("lane"))
    if lane and lane != "all":
        return lane

    family = _prompt_text(row.get("strategy_family"), limit=80).strip().lower()
    evidence_key = _prompt_text(row.get("evidence_key"), limit=80).strip().lower()
    if family and family != "all" and evidence_key and evidence_key != "all":
        return f"{family}:{evidence_key}"

    for value in (
        family,
        _prompt_text(row.get("horizon"), limit=80).strip().lower(),
        evidence_key,
    ):
        if value and value != "all":
            return value
    return "all"


def _scorecard_win_rate_pct(row: dict[str, Any]) -> float:
    if row.get("win_rate") not in (None, "", [], {}):
        return _prompt_float(row.get("win_rate"))
    return _prompt_float(row.get("win_rate_pct"))


def _lane_performance_evidence_profile(row: dict[str, Any]) -> dict[str, Any]:
    has_expectancy = row.get("expectancy_pct") not in (None, "", [], {})
    has_win_rate = (
        row.get("win_rate") not in (None, "", [], {})
        or row.get("win_rate_pct") not in (None, "", [], {})
    )
    has_profit_factor = row.get("profit_factor") not in (None, "", [], {})
    has_drawdown = row.get("max_drawdown_pct") not in (None, "", [], {})
    has_recovery = row.get("recovery_factor") not in (None, "", [], {})

    missing_metrics: list[str] = []
    if not has_expectancy:
        missing_metrics.append("expectancy_pct")
    if not has_win_rate:
        missing_metrics.append("win_rate_pct")
    if not has_profit_factor:
        missing_metrics.append("profit_factor")
    if not has_drawdown:
        missing_metrics.append("max_drawdown_pct")
    if not has_recovery:
        missing_metrics.append("recovery_factor")

    expectancy = _prompt_float(row.get("expectancy_pct"))
    win_rate = _scorecard_win_rate_pct(row)
    profit_factor = _prompt_float(row.get("profit_factor"))
    drawdown = _prompt_float(row.get("max_drawdown_pct"))
    recovery_factor = _prompt_float(row.get("recovery_factor"))

    weak_metrics: list[str] = []
    if has_expectancy and expectancy <= 0.0:
        weak_metrics.append("expectancy_non_positive")
    if has_win_rate and win_rate < 45.0:
        weak_metrics.append("win_rate_below_45pct")
    if has_profit_factor and profit_factor < 1.0:
        weak_metrics.append("profit_factor_below_1")
    if has_drawdown and drawdown <= -4.0:
        weak_metrics.append("drawdown_over_scale_threshold")
    if has_recovery and recovery_factor < 1.0:
        weak_metrics.append("recovery_factor_below_1")

    severe_metrics: list[str] = []
    if has_expectancy and expectancy < 0.0:
        severe_metrics.append("negative_expectancy")
    if has_profit_factor and profit_factor < 0.8:
        severe_metrics.append("profit_factor_below_0_8")
    if has_drawdown and drawdown <= -7.0:
        severe_metrics.append("drawdown_over_hard_limit")
    if has_recovery and recovery_factor <= 0.0:
        severe_metrics.append("non_positive_recovery_factor")

    if missing_metrics:
        status = "missing"
    elif weak_metrics:
        status = "weak"
    else:
        status = "complete"

    repair_targets: list[str] = []
    if missing_metrics:
        repair_targets.append("record_core_performance_metrics_before_scale_up")
    if "expectancy_non_positive" in weak_metrics:
        repair_targets.append("produce_positive_expectancy_before_size_increase")
    if "win_rate_below_45pct" in weak_metrics:
        repair_targets.append("raise_lane_win_rate_before_size_increase")
    if "profit_factor_below_1" in weak_metrics:
        repair_targets.append("raise_profit_factor_above_1_before_pressing")
    if "drawdown_over_scale_threshold" in weak_metrics:
        repair_targets.append("reduce_lane_drawdown_before_scale_up")
    if "recovery_factor_below_1" in weak_metrics:
        repair_targets.append("improve_recovery_factor_before_size_increase")

    cap_multiplier = 1.0
    if missing_metrics:
        cap_multiplier = 0.5
    if severe_metrics:
        cap_multiplier = min(cap_multiplier, 0.25)

    return {
        "status": status,
        "missing_metrics": missing_metrics,
        "weak_metrics": weak_metrics,
        "severe_metrics": severe_metrics,
        "cap_multiplier": cap_multiplier,
        "repair_targets": repair_targets,
    }


def _lane_performance_scale_blocking_metrics(profile: dict[str, Any]) -> list[str]:
    blocking: list[str] = []
    for item in list(profile.get("weak_metrics") or []):
        clean = _prompt_text(item, limit=80)
        if clean in LANE_PERFORMANCE_SCALE_BLOCKING_WEAK_METRICS:
            blocking.append(clean)
    for item in list(profile.get("severe_metrics") or []):
        clean = _prompt_text(item, limit=80)
        if clean in LANE_PERFORMANCE_SCALE_BLOCKING_SEVERE_METRICS:
            blocking.append(clean)
    return list(dict.fromkeys(blocking))


def _lane_performance_requires_repair(
    *,
    grade: str,
    sample_count: int,
    min_samples_to_scale: int,
    missing_metrics: list[str],
    scale_blocking_metrics: list[str],
    severe_metrics: list[str],
) -> bool:
    if grade == "scale_candidate" and (missing_metrics or scale_blocking_metrics):
        return True
    if int(sample_count or 0) <= 0:
        return False
    if severe_metrics and int(sample_count or 0) >= 3:
        return True
    weak_sample_threshold = min(max(int(min_samples_to_scale or 1), 1), 10)
    return bool(
        scale_blocking_metrics
        and int(sample_count or 0) >= weak_sample_threshold
    )


def _lane_performance_is_scale_ready(row: dict[str, Any]) -> bool:
    profile = _lane_performance_evidence_profile(row)
    return bool(
        not profile.get("missing_metrics")
        and not _lane_performance_scale_blocking_metrics(profile)
    )


def _add_scale_blocker(
    action: dict[str, Any],
    *,
    blocker: str,
    repair_target: str,
) -> None:
    clean_blocker = _prompt_text(blocker, limit=100)
    clean_repair = _prompt_text(repair_target, limit=140)
    blockers = [
        _prompt_text(row, limit=100)
        for row in list(action.get("scale_blockers") or [])
        if _prompt_text(row, limit=100)
    ]
    if clean_blocker and clean_blocker not in blockers:
        blockers.append(clean_blocker)
    repairs = [
        _prompt_text(row, limit=140)
        for row in list(action.get("scale_repair_targets") or [])
        if _prompt_text(row, limit=140)
    ]
    if clean_repair and clean_repair not in repairs:
        repairs.append(clean_repair)
    if blockers:
        action["scale_blockers"] = blockers[:10]
        action["scale_decision"] = "capped_until_repairs"
    if repairs:
        action["scale_repair_targets"] = repairs[:10]
    passport = (
        dict(action.get("risk_budget_passport"))
        if isinstance(action.get("risk_budget_passport"), dict)
        else {}
    )
    if not passport:
        return
    passport_blockers = [
        _prompt_text(row, limit=100)
        for row in list(passport.get("scale_blockers") or [])
        if _prompt_text(row, limit=100)
    ]
    if clean_blocker and clean_blocker not in passport_blockers:
        passport_blockers.append(clean_blocker)
    passport_repairs = [
        _prompt_text(row, limit=140)
        for row in list(passport.get("scale_repair_targets") or [])
        if _prompt_text(row, limit=140)
    ]
    if clean_repair and clean_repair not in passport_repairs:
        passport_repairs.append(clean_repair)
    if passport_blockers:
        passport["scale_blockers"] = passport_blockers[:10]
        passport["scale_decision"] = "capped_until_repairs"
    if passport_repairs:
        passport["scale_repair_targets"] = passport_repairs[:10]
    action["risk_budget_passport"] = passport


def _lane_risk_budget_passport(
    row: dict[str, Any],
    *,
    min_samples_to_scale: int,
) -> dict[str, Any]:
    grade = _normalize_authority_grade(row.get("grade") or "insufficient")
    sample_count = int(row.get("sample_count") or 0)
    min_samples = max(int(min_samples_to_scale or 1), 1)
    sample_confidence = min(sample_count / min_samples, 1.0)
    sample_cap = 1.0 if sample_count >= min_samples else max(sample_confidence, 0.25)
    lane_confidence = _prompt_float(row.get("lane_confidence_score"))
    if lane_confidence <= 0:
        lane_confidence = sample_confidence
    lane_confidence = min(max(lane_confidence, 0.0), 1.0)
    lane_confidence_cap = max(lane_confidence, 0.25)

    win_rate = _scorecard_win_rate_pct(row)
    profit_factor = _prompt_float(row.get("profit_factor"))
    raw_kelly = 0.0
    kelly_cap = 1.0
    has_kelly_inputs = win_rate > 0.0 and profit_factor > 0.0
    if has_kelly_inputs:
        win_probability = min(max(win_rate / 100.0, 0.0), 1.0)
        loss_probability = max(1.0 - win_probability, 0.0)
        payoff_ratio = (
            profit_factor * loss_probability / win_probability
            if win_probability > 0 and loss_probability > 0
            else 0.0
        )
        raw_kelly = (
            max(win_probability - (loss_probability / payoff_ratio), 0.0)
            if payoff_ratio > 0
            else 0.0
        )
        fractional_kelly = raw_kelly * LANE_KELLY_FRACTION
        kelly_cap = (
            min(
                fractional_kelly / LANE_KELLY_REFERENCE_RISK_FRACTION,
                1.25,
            )
            if fractional_kelly > 0
            else 0.25
        )
    else:
        fractional_kelly = 0.0

    drawdown = _prompt_float(row.get("max_drawdown_pct"))
    if drawdown <= -7.0:
        drawdown_cap = 0.5
    elif drawdown <= -4.0:
        drawdown_cap = 0.75
    else:
        drawdown_cap = 1.0
    has_recovery_factor = row.get("recovery_factor") not in (None, "", [], {})
    recovery_factor = _prompt_float(row.get("recovery_factor"))
    has_reported_recovery_cap = row.get("recovery_factor_cap_multiplier") not in (
        None,
        "",
        [],
        {},
    )
    reported_recovery_cap = _prompt_float(row.get("recovery_factor_cap_multiplier"))
    if reported_recovery_cap > 0:
        recovery_cap = min(max(reported_recovery_cap, 0.0), 1.25)
    elif has_reported_recovery_cap:
        recovery_cap = 0.25
    elif sample_count >= 3 and has_recovery_factor and recovery_factor <= 0.0:
        recovery_cap = 0.25
    elif sample_count >= 3 and has_recovery_factor and recovery_factor < 0.5:
        recovery_cap = 0.5
    elif sample_count >= 3 and has_recovery_factor and recovery_factor < 1.0:
        recovery_cap = 0.75
    else:
        recovery_cap = 1.0

    raw_ruin = row.get("risk_of_ruin_pct")
    has_ruin = raw_ruin is not None
    risk_of_ruin = _prompt_float(raw_ruin)
    if has_ruin and risk_of_ruin >= 20.0:
        ruin_cap = 0.25
    elif has_ruin and risk_of_ruin >= 10.0:
        ruin_cap = 0.5
    elif has_ruin and risk_of_ruin >= 5.0:
        ruin_cap = 0.75
    else:
        ruin_cap = 1.0

    recommended_risk = _prompt_float(row.get("recommended_risk_fraction"))
    max_risk_cap = _prompt_float(row.get("max_risk_cap_fraction"))
    risk_fraction_cap = 1.0
    if max_risk_cap > 0:
        risk_fraction_cap = min(
            risk_fraction_cap,
            max_risk_cap / LANE_KELLY_REFERENCE_RISK_FRACTION,
        )
    if recommended_risk > 0:
        risk_fraction_cap = min(
            risk_fraction_cap,
            recommended_risk / LANE_KELLY_REFERENCE_RISK_FRACTION,
        )
    elif row.get("recommended_risk_fraction") not in (None, "", [], {}) and (
        _prompt_float(row.get("expectancy_pct")) <= 0 or profit_factor < 1.0
    ):
        risk_fraction_cap = min(risk_fraction_cap, 0.25)
    risk_fraction_cap = min(max(risk_fraction_cap, 0.0), 1.25)

    raw_cost_precision_rate = row.get("cost_precision_verified_rate")
    if raw_cost_precision_rate in (None, "", [], {}):
        raw_cost_precision_rate = row.get("cost_precision_verified_rate_pct")
    has_cost_precision = raw_cost_precision_rate not in (
        None,
        "",
        [],
        {},
    )
    cost_precision_verified_rate = _prompt_float(raw_cost_precision_rate)
    cost_precision_weak = _cost_evidence_requires_repair(
        row,
        sample_count=sample_count,
        min_samples_to_scale=min_samples,
    )
    cost_precision_cap = 0.5 if cost_precision_weak else 1.0
    cost_precision_counts = _compact_cost_precision_counts(
        row.get("cost_precision_counts")
    )
    missing_cost_component_counts = _compact_label_counts(
        row.get("missing_cost_component_counts")
    )
    present_cost_component_counts = _compact_label_counts(
        row.get("present_cost_component_counts")
    )
    required_cost_component_counts = _compact_label_counts(
        row.get("required_cost_component_counts")
    )
    cost_precision_reason_counts = _compact_label_counts(
        row.get("cost_precision_reason_counts"),
        limit=6,
    )
    cost_evidence_status = _prompt_text(
        row.get("cost_evidence_status"),
        limit=80,
    )
    cost_evidence_repair_hint = _cost_evidence_repair_hint(row)
    cost_repair_targets = _cost_evidence_repair_targets(row)
    raw_cost_verified_alpha_count = row.get("cost_verified_alpha_count")
    has_cost_verified_alpha_count = raw_cost_verified_alpha_count not in (
        None,
        "",
        [],
        {},
    )
    cost_verified_alpha_count = _prompt_count(raw_cost_verified_alpha_count)
    cost_unverified_alpha_count = _prompt_count(row.get("cost_unverified_alpha_count"))
    raw_cost_verified_alpha_net = row.get("cost_verified_alpha_net_pnl")
    has_cost_verified_alpha_net = raw_cost_verified_alpha_net not in (
        None,
        "",
        [],
        {},
    )
    cost_verified_alpha_net = _prompt_float(raw_cost_verified_alpha_net)
    raw_cost_unverified_alpha_net = row.get("cost_unverified_alpha_net_pnl")
    has_cost_unverified_alpha_net = raw_cost_unverified_alpha_net not in (
        None,
        "",
        [],
        {},
    )
    cost_unverified_alpha_net = _prompt_float(raw_cost_unverified_alpha_net)
    cost_hybrid_alpha_count = _prompt_count(row.get("cost_hybrid_alpha_count"))
    raw_cost_hybrid_alpha_net = row.get("cost_hybrid_alpha_net_pnl")
    has_cost_hybrid_alpha_net = raw_cost_hybrid_alpha_net not in (None, "", [], {})
    cost_hybrid_alpha_net = _prompt_float(raw_cost_hybrid_alpha_net)
    if cost_precision_weak and not cost_evidence_repair_hint:
        cost_evidence_repair_hint = (
            "increase_recorded_cost_precision_before_size_increase"
        )
    verified_edge_sample_weak = bool(
        has_cost_verified_alpha_count
        and sample_count >= min_samples
        and cost_verified_alpha_count < min_samples
    )
    verified_edge_sample_cap = 0.5 if verified_edge_sample_weak else 1.0
    verified_edge_net_weak = bool(
        has_cost_verified_alpha_count
        and has_cost_verified_alpha_net
        and cost_verified_alpha_count >= min_samples
        and cost_verified_alpha_net <= 0.0
    )
    verified_edge_net_cap = 0.25 if verified_edge_net_weak else 1.0
    entry_quality_sample_count = int(row.get("entry_quality_sample_count") or 0)
    avg_entry_quality_score = _prompt_float(row.get("avg_entry_quality_score"))
    bad_entry_quality_rate = _prompt_float(row.get("bad_entry_quality_rate_pct"))
    entry_quality_label_counts = _compact_label_counts(
        row.get("entry_quality_label_counts")
    )
    bad_entry_quality_label_counts = _compact_label_counts(
        row.get("bad_entry_quality_label_counts")
    )
    good_entry_quality_label_counts = _compact_label_counts(
        row.get("good_entry_quality_label_counts")
    )
    dominant_bad_entry_quality_label = _prompt_text(
        row.get("dominant_bad_entry_quality_label"),
        limit=80,
    )
    dominant_good_entry_quality_label = _prompt_text(
        row.get("dominant_good_entry_quality_label"),
        limit=80,
    )
    entry_quality_repair_hint = _entry_quality_repair_hint(row)
    entry_repair_targets = _entry_quality_repair_targets(row)
    entry_quality_weak = bool(row.get("scale_blocked_by_entry_quality")) or (
        entry_quality_sample_count >= min_samples
        and (
            avg_entry_quality_score < 55.0
            or bad_entry_quality_rate >= 50.0
        )
    )
    entry_quality_cap = 0.5 if entry_quality_weak else 1.0
    if entry_quality_weak and not entry_quality_repair_hint:
        entry_quality_repair_hint = (
            "require_price_relief_regime_alignment_before_new_blocks"
        )
    validation_evidence_status = _prompt_text(
        row.get("validation_evidence_status"),
        limit=60,
    )
    validation_missing_dimensions = _compact_validation_dimensions(
        row.get("validation_missing_dimensions")
    )
    validation_failed_dimensions = _compact_validation_dimensions(
        row.get("validation_failed_dimensions")
    )
    validation_thin_dimensions = _compact_validation_dimensions(
        row.get("validation_thin_dimensions")
    )
    validation_evidence_repair_hint = _validation_evidence_repair_hint(row)
    validation_evidence_repair_targets = _validation_evidence_repair_targets(row)
    core_validation_evidence_gaps = _core_validation_evidence_gaps(row)
    validation_evidence_required_evidence = [
        _prompt_text(item, limit=100)
        for item in list(row.get("validation_evidence_required_evidence") or [])[:8]
        if _prompt_text(item, limit=100)
    ]
    validation_evidence_required_checks = [
        _prompt_text(item, limit=100)
        for item in list(row.get("validation_evidence_required_checks") or [])[:8]
        if _prompt_text(item, limit=100)
    ]
    validation_evidence_pass_collection_hooks = [
        _prompt_text(item, limit=140)
        for item in list(
            row.get("validation_evidence_pass_collection_hooks") or []
        )[:6]
        if _prompt_text(item, limit=140)
    ]
    validation_evidence_pass_current_gaps = [
        _prompt_text(item, limit=140)
        for item in list(row.get("validation_evidence_pass_current_gaps") or [])[:6]
        if _prompt_text(item, limit=140)
    ]
    validation_evidence_pass_criteria = [
        _prompt_text(item, limit=160)
        for item in list(row.get("validation_evidence_pass_criteria") or [])[:6]
        if _prompt_text(item, limit=160)
    ]
    validation_evidence_verification_artifacts = [
        _prompt_text(item, limit=160)
        for item in list(
            row.get("validation_evidence_verification_artifacts") or []
        )[:6]
        if _prompt_text(item, limit=160)
    ]
    validation_evidence_weak = _validation_evidence_is_weak(row)
    validation_evidence_cap = (
        1.0
        if not validation_evidence_weak
        else 0.5
        if core_validation_evidence_gaps
        else 0.75
    )
    validation_repair_enforced_count = _prompt_count(
        row.get("validation_repair_enforced_count")
    ) or 0
    validation_repair_scale_blocked_count = _prompt_count(
        row.get("validation_repair_scale_up_blocked_count")
    ) or 0
    validation_repair_waiting_entry_count = _prompt_count(
        row.get("validation_repair_waiting_entry_count")
    ) or 0
    validation_repair_rejected_count = _prompt_count(
        row.get("validation_repair_rejected_count")
    ) or 0
    validation_repair_avg_budget_multiplier = _prompt_float(
        row.get("validation_repair_avg_budget_multiplier")
    )
    validation_repair_action_counts = _compact_label_counts(
        row.get("validation_repair_action_counts")
    )
    validation_repair_adjustment_reason_counts = _compact_label_counts(
        row.get("validation_repair_adjustment_reason_counts")
    )
    validation_repair_weak = bool(
        validation_repair_enforced_count > 0
        and (
            validation_repair_scale_blocked_count > 0
            or validation_repair_waiting_entry_count > 0
            or validation_repair_rejected_count > 0
        )
    )
    validation_repair_cap = 1.0
    if validation_repair_weak:
        validation_repair_cap = (
            validation_repair_avg_budget_multiplier
            if 0 < validation_repair_avg_budget_multiplier < 1.0
            else 0.5
        )
        if validation_repair_rejected_count > 0:
            validation_repair_cap = min(validation_repair_cap, 0.25)
    performance_profile = _lane_performance_evidence_profile(row)
    performance_evidence_status = _prompt_text(
        performance_profile.get("status"),
        limit=60,
    )
    performance_missing_metrics = [
        _prompt_text(item, limit=80)
        for item in list(performance_profile.get("missing_metrics") or [])[:8]
        if _prompt_text(item, limit=80)
    ]
    performance_weak_metrics = [
        _prompt_text(item, limit=80)
        for item in list(performance_profile.get("weak_metrics") or [])[:8]
        if _prompt_text(item, limit=80)
    ]
    performance_severe_metrics = [
        _prompt_text(item, limit=80)
        for item in list(performance_profile.get("severe_metrics") or [])[:8]
        if _prompt_text(item, limit=80)
    ]
    performance_scale_blocking_metrics = _lane_performance_scale_blocking_metrics(
        performance_profile
    )
    performance_repair_targets = [
        _prompt_text(item, limit=140)
        for item in list(performance_profile.get("repair_targets") or [])[:8]
        if _prompt_text(item, limit=140)
    ]
    performance_evidence_cap = min(
        max(_prompt_float(performance_profile.get("cap_multiplier")), 0.0),
        1.0,
    )
    performance_evidence_weak = _lane_performance_requires_repair(
        grade=grade,
        sample_count=sample_count,
        min_samples_to_scale=min_samples,
        missing_metrics=performance_missing_metrics,
        scale_blocking_metrics=performance_scale_blocking_metrics,
        severe_metrics=performance_severe_metrics,
    )
    if not performance_evidence_weak:
        performance_evidence_cap = 1.0
    elif performance_scale_blocking_metrics and not performance_missing_metrics:
        performance_evidence_cap = min(
            performance_evidence_cap,
            0.25 if performance_severe_metrics else 0.5,
        )

    row_multiplier = max(_prompt_float(row.get("authority_multiplier")), 0.0) or 1.0
    applied = min(
        row_multiplier,
        kelly_cap,
        drawdown_cap,
        recovery_cap,
        ruin_cap,
        sample_cap,
        lane_confidence_cap,
        risk_fraction_cap,
        cost_precision_cap,
        verified_edge_sample_cap,
        verified_edge_net_cap,
        entry_quality_cap,
        validation_evidence_cap,
        validation_repair_cap,
        performance_evidence_cap,
    )
    scale_blockers: list[str] = []

    def add_blocker(value: str) -> None:
        clean = _prompt_text(value, limit=100)
        if clean and clean not in scale_blockers:
            scale_blockers.append(clean)

    if sample_count < min_samples:
        add_blocker("insufficient_closed_samples")
    if 0 <= kelly_cap < 1.0:
        add_blocker("fractional_kelly_cap")
    if drawdown_cap < 1.0:
        add_blocker("drawdown_cap")
    if recovery_cap < 1.0:
        add_blocker("recovery_factor_cap")
    if ruin_cap < 1.0:
        add_blocker("risk_of_ruin_cap")
    if lane_confidence_cap < 1.0:
        add_blocker("lane_confidence_cap")
    if risk_fraction_cap < 1.0:
        add_blocker("risk_fraction_cap")
    if cost_precision_weak:
        add_blocker("cost_evidence_repair")
    if verified_edge_sample_weak:
        add_blocker("verified_edge_sample_cap")
    if verified_edge_net_weak:
        add_blocker("verified_edge_net_pnl_cap")
    if entry_quality_weak:
        add_blocker("entry_quality_repair")
    if validation_evidence_weak:
        add_blocker("validation_evidence_repair")
    if core_validation_evidence_gaps:
        add_blocker("validation_backtest_wfa_oos_shadow_cap")
    if validation_repair_weak:
        add_blocker("validation_repair_enforced")
    if performance_evidence_weak:
        add_blocker("performance_evidence_repair")

    scale_repair_targets: list[str] = []

    def add_repair_target(value: str) -> None:
        clean = _prompt_text(value, limit=140)
        if clean and clean not in scale_repair_targets:
            scale_repair_targets.append(clean)

    if sample_count < min_samples:
        add_repair_target("close_more_verified_lane_samples_before_scale_up")
    if kelly_cap < 1.0:
        add_repair_target("improve_fractional_kelly_inputs_before_size_increase")
    if drawdown_cap < 1.0:
        add_repair_target("reduce_drawdown_usage_before_size_increase")
    if recovery_cap < 1.0:
        add_repair_target("improve_recovery_factor_before_size_increase")
    if ruin_cap < 1.0:
        add_repair_target("lower_risk_of_ruin_before_size_increase")
    if lane_confidence_cap < 1.0:
        add_repair_target("raise_lane_confidence_before_size_increase")
    if risk_fraction_cap < 1.0:
        add_repair_target("respect_recommended_risk_fraction_cap")
    for target in cost_repair_targets:
        add_repair_target(target)
    if verified_edge_sample_weak:
        add_repair_target("close_more_recorded_cost_alpha_samples_before_scale_up")
    if verified_edge_net_weak:
        add_repair_target("produce_positive_recorded_cost_alpha_net_pnl_before_scale_up")
    for target in entry_repair_targets:
        add_repair_target(target)
    for target in validation_evidence_repair_targets:
        add_repair_target(target)
    if validation_repair_weak:
        add_repair_target("clear_validation_repair_enforcement_before_scale_up")
    if performance_evidence_weak:
        for target in performance_repair_targets:
            add_repair_target(target)

    if applied <= 0:
        scale_decision = "blocked"
    elif scale_blockers:
        scale_decision = "capped_until_repairs"
    elif applied > 1.0:
        scale_decision = "eligible_to_scale"
    else:
        scale_decision = "normal_size_no_scale"
    passport = {
        "version": "lane_risk_budget_passport_v1",
        "sample_confidence": round(sample_confidence, 6),
        "lane_confidence_score": round(lane_confidence, 6),
        "raw_kelly_fraction": round(raw_kelly, 8),
        "raw_fractional_kelly_fraction": round(fractional_kelly, 8),
        "kelly_cap_multiplier": round(kelly_cap, 6),
        "drawdown_cap_multiplier": round(drawdown_cap, 6),
        "recovery_factor_cap_multiplier": round(recovery_cap, 6),
        "ruin_cap_multiplier": round(ruin_cap, 6),
        "risk_of_ruin_pct": round(risk_of_ruin, 6) if has_ruin else None,
        "sample_cap_multiplier": round(sample_cap, 6),
        "lane_confidence_cap_multiplier": round(lane_confidence_cap, 6),
        "recommended_risk_fraction": (
            round(recommended_risk, 8)
            if row.get("recommended_risk_fraction") not in (None, "", [], {})
            else None
        ),
        "max_risk_cap_fraction": (
            round(max_risk_cap, 8)
            if row.get("max_risk_cap_fraction") not in (None, "", [], {})
            else None
        ),
        "risk_fraction_cap_multiplier": round(risk_fraction_cap, 6),
        "cost_precision_verified_rate": (
            round(cost_precision_verified_rate, 6)
            if has_cost_precision
            else None
        ),
        "cost_precision_counts": cost_precision_counts,
        "missing_cost_component_counts": missing_cost_component_counts,
        "present_cost_component_counts": present_cost_component_counts,
        "required_cost_component_counts": required_cost_component_counts,
        "cost_precision_reason_counts": cost_precision_reason_counts,
        "cost_evidence_status": cost_evidence_status,
        "cost_evidence_repair_hint": cost_evidence_repair_hint,
        "cost_repair_targets": cost_repair_targets,
        "cost_verified_alpha_count": cost_verified_alpha_count,
        "cost_unverified_alpha_count": cost_unverified_alpha_count,
        "cost_verified_alpha_net_pnl": (
            round(cost_verified_alpha_net, 8)
            if has_cost_verified_alpha_net
            else None
        ),
        "cost_unverified_alpha_net_pnl": (
            round(cost_unverified_alpha_net, 8)
            if has_cost_unverified_alpha_net
            else None
        ),
        "cost_hybrid_alpha_count": cost_hybrid_alpha_count,
        "cost_hybrid_alpha_net_pnl": (
            round(cost_hybrid_alpha_net, 8)
            if has_cost_hybrid_alpha_net
            else None
        ),
        "scale_blocked_by_cost_precision": cost_precision_weak,
        "scale_blocked_by_cost_evidence": bool(
            row.get("scale_blocked_by_cost_evidence")
        )
        or cost_precision_weak
        or verified_edge_sample_weak,
        "cost_precision_cap_multiplier": round(cost_precision_cap, 6),
        "verified_edge_sample_cap_multiplier": round(
            verified_edge_sample_cap,
            6,
        ),
        "verified_edge_net_cap_multiplier": round(
            verified_edge_net_cap,
            6,
        ),
        "scale_blocked_by_verified_edge_samples": verified_edge_sample_weak,
        "scale_blocked_by_verified_edge_net_pnl": verified_edge_net_weak,
        "avg_entry_quality_score": (
            round(avg_entry_quality_score, 6)
            if entry_quality_sample_count > 0
            else None
        ),
        "bad_entry_quality_rate_pct": (
            round(bad_entry_quality_rate, 6)
            if entry_quality_sample_count > 0
            else None
        ),
        "entry_quality_label_counts": entry_quality_label_counts,
        "bad_entry_quality_label_counts": bad_entry_quality_label_counts,
        "good_entry_quality_label_counts": good_entry_quality_label_counts,
        "dominant_bad_entry_quality_label": dominant_bad_entry_quality_label,
        "dominant_good_entry_quality_label": dominant_good_entry_quality_label,
        "entry_quality_repair_hint": entry_quality_repair_hint,
        "entry_repair_targets": entry_repair_targets,
        "entry_quality_cap_multiplier": round(entry_quality_cap, 6),
        "validation_evidence_status": validation_evidence_status,
        "validation_missing_dimensions": validation_missing_dimensions,
        "validation_failed_dimensions": validation_failed_dimensions,
        "validation_thin_dimensions": validation_thin_dimensions,
        "validation_evidence_repair_hint": validation_evidence_repair_hint,
        "validation_evidence_repair_targets": validation_evidence_repair_targets,
        "core_validation_evidence_gaps": core_validation_evidence_gaps,
        "validation_evidence_required_evidence": (
            validation_evidence_required_evidence
        ),
        "validation_evidence_required_checks": validation_evidence_required_checks,
        "validation_evidence_pass_collection_hooks": (
            validation_evidence_pass_collection_hooks
        ),
        "validation_evidence_pass_current_gaps": (
            validation_evidence_pass_current_gaps
        ),
        "validation_evidence_pass_criteria": validation_evidence_pass_criteria,
        "validation_evidence_verification_artifacts": (
            validation_evidence_verification_artifacts
        ),
        "validation_evidence_cap_multiplier": round(validation_evidence_cap, 6),
        "scale_blocked_by_validation_evidence": validation_evidence_weak,
        "validation_repair_enforced_count": validation_repair_enforced_count,
        "validation_repair_scale_up_blocked_count": (
            validation_repair_scale_blocked_count
        ),
        "validation_repair_waiting_entry_count": (
            validation_repair_waiting_entry_count
        ),
        "validation_repair_rejected_count": validation_repair_rejected_count,
        "validation_repair_avg_budget_multiplier": (
            round(validation_repair_avg_budget_multiplier, 6)
            if validation_repair_avg_budget_multiplier > 0
            else None
        ),
        "validation_repair_action_counts": validation_repair_action_counts,
        "validation_repair_adjustment_reason_counts": (
            validation_repair_adjustment_reason_counts
        ),
        "validation_repair_cap_multiplier": round(validation_repair_cap, 6),
        "scale_blocked_by_validation_repair": validation_repair_weak,
        "performance_evidence_status": performance_evidence_status,
        "performance_missing_metrics": performance_missing_metrics,
        "performance_weak_metrics": performance_weak_metrics,
        "performance_severe_metrics": performance_severe_metrics,
        "performance_scale_blocking_metrics": performance_scale_blocking_metrics,
        "performance_evidence_cap_multiplier": round(
            performance_evidence_cap,
            6,
        ),
        "performance_repair_targets": performance_repair_targets,
        "scale_blocked_by_performance_evidence": performance_evidence_weak,
        "scale_decision": scale_decision,
        "scale_blockers": scale_blockers,
        "scale_repair_targets": scale_repair_targets[:10],
        "applied_risk_budget_multiplier": round(applied, 6),
    }
    return {
        key: value
        for key, value in passport.items()
        if value not in (None, "", [], {})
    }


def _lane_authority_from_scorecards(
    scorecards: list[dict[str, Any]],
    *,
    allow_scale_up: bool,
    max_budget_multiplier: float,
    min_samples_to_scale: int,
) -> dict[str, Any]:
    lane_actions: dict[str, dict[str, Any]] = {}
    weak_lanes: list[str] = []
    scale_candidate_lanes: list[str] = []
    qualified_lanes: list[str] = []
    insufficient_lanes: list[str] = []
    cost_weak_lanes: list[str] = []
    cost_evidence_weak_lanes: list[str] = []
    early_loss_lanes: list[str] = []
    entry_quality_weak_lanes: list[str] = []
    validation_evidence_weak_lanes: list[str] = []
    validation_repair_weak_lanes: list[str] = []
    performance_evidence_weak_lanes: list[str] = []

    def add_unique(rows: list[str], value: str) -> None:
        if value and value not in rows:
            rows.append(value)

    for row in scorecards[:50]:
        if not isinstance(row, dict):
            continue
        lane = _lane_key_from_scorecard(row)
        if not lane:
            continue
        grade = _normalize_authority_grade(row.get("grade") or "insufficient")
        sample_count = int(row.get("sample_count") or 0)
        expectancy = _prompt_float(row.get("expectancy_pct"))
        win_rate = _scorecard_win_rate_pct(row)
        drawdown = _prompt_float(row.get("max_drawdown_pct"))
        profit_factor = _prompt_float(row.get("profit_factor"))
        recovery_factor = _prompt_float(row.get("recovery_factor"))
        cumulative_return = _prompt_float(row.get("cumulative_return_pct"))
        cost_drag = _prompt_float(row.get("cost_drag_pct_of_gross_pnl"))
        raw_cost_precision_rate = row.get("cost_precision_verified_rate")
        if raw_cost_precision_rate in (None, "", [], {}):
            raw_cost_precision_rate = row.get("cost_precision_verified_rate_pct")
        has_cost_precision = raw_cost_precision_rate not in (
            None,
            "",
            [],
            {},
        )
        cost_precision_verified_rate = _prompt_float(raw_cost_precision_rate)
        cost_precision_counts = _compact_cost_precision_counts(
            row.get("cost_precision_counts")
        )
        missing_cost_component_counts = _compact_label_counts(
            row.get("missing_cost_component_counts")
        )
        present_cost_component_counts = _compact_label_counts(
            row.get("present_cost_component_counts")
        )
        required_cost_component_counts = _compact_label_counts(
            row.get("required_cost_component_counts")
        )
        cost_precision_reason_counts = _compact_label_counts(
            row.get("cost_precision_reason_counts"),
            limit=6,
        )
        cost_evidence_status = _prompt_text(
            row.get("cost_evidence_status"),
            limit=80,
        )
        cost_evidence_repair_hint = _cost_evidence_repair_hint(row)
        cost_repair_targets = _cost_evidence_repair_targets(row)
        raw_cost_verified_alpha_count = row.get("cost_verified_alpha_count")
        has_cost_verified_alpha_count = raw_cost_verified_alpha_count not in (
            None,
            "",
            [],
            {},
        )
        cost_verified_alpha_count = _prompt_count(raw_cost_verified_alpha_count)
        cost_unverified_alpha_count = _prompt_count(
            row.get("cost_unverified_alpha_count")
        )
        raw_cost_verified_alpha_net = row.get("cost_verified_alpha_net_pnl")
        has_cost_verified_alpha_net = raw_cost_verified_alpha_net not in (
            None,
            "",
            [],
            {},
        )
        cost_verified_alpha_net = _prompt_float(raw_cost_verified_alpha_net)
        raw_cost_unverified_alpha_net = row.get("cost_unverified_alpha_net_pnl")
        has_cost_unverified_alpha_net = raw_cost_unverified_alpha_net not in (
            None,
            "",
            [],
            {},
        )
        cost_unverified_alpha_net = _prompt_float(raw_cost_unverified_alpha_net)
        cost_hybrid_alpha_count = _prompt_count(row.get("cost_hybrid_alpha_count"))
        raw_cost_hybrid_alpha_net = row.get("cost_hybrid_alpha_net_pnl")
        has_cost_hybrid_alpha_net = raw_cost_hybrid_alpha_net not in (None, "", [], {})
        cost_hybrid_alpha_net = _prompt_float(raw_cost_hybrid_alpha_net)
        cost_precision_weak = _cost_evidence_requires_repair(
            row,
            sample_count=sample_count,
            min_samples_to_scale=max(int(min_samples_to_scale or 1), 1),
        )
        if cost_precision_weak and not cost_evidence_repair_hint:
            cost_evidence_repair_hint = (
                "increase_recorded_cost_precision_before_size_increase"
            )
        verified_edge_sample_weak = bool(
            has_cost_verified_alpha_count
            and sample_count >= max(int(min_samples_to_scale or 1), 1)
            and cost_verified_alpha_count < max(int(min_samples_to_scale or 1), 1)
        )
        verified_edge_net_weak = bool(
            has_cost_verified_alpha_count
            and has_cost_verified_alpha_net
            and cost_verified_alpha_count >= max(int(min_samples_to_scale or 1), 1)
            and cost_verified_alpha_net <= 0.0
        )
        entry_quality_sample_count = int(row.get("entry_quality_sample_count") or 0)
        avg_entry_quality_score = _prompt_float(row.get("avg_entry_quality_score"))
        bad_entry_quality_rate = _prompt_float(row.get("bad_entry_quality_rate_pct"))
        entry_quality_label_counts = _compact_label_counts(
            row.get("entry_quality_label_counts")
        )
        bad_entry_quality_label_counts = _compact_label_counts(
            row.get("bad_entry_quality_label_counts")
        )
        good_entry_quality_label_counts = _compact_label_counts(
            row.get("good_entry_quality_label_counts")
        )
        dominant_bad_entry_quality_label = _prompt_text(
            row.get("dominant_bad_entry_quality_label"),
            limit=80,
        )
        dominant_good_entry_quality_label = _prompt_text(
            row.get("dominant_good_entry_quality_label"),
            limit=80,
        )
        entry_quality_repair_hint = _entry_quality_repair_hint(row)
        entry_repair_targets = _entry_quality_repair_targets(row)
        entry_quality_weak = bool(row.get("scale_blocked_by_entry_quality")) or (
            entry_quality_sample_count >= max(int(min_samples_to_scale or 1), 1)
            and (
                avg_entry_quality_score < 55.0
                or bad_entry_quality_rate >= 50.0
            )
        )
        if entry_quality_weak and not entry_quality_repair_hint:
            entry_quality_repair_hint = (
                "require_price_relief_regime_alignment_before_new_blocks"
            )
        validation_evidence_status = _prompt_text(
            row.get("validation_evidence_status"),
            limit=60,
        )
        validation_missing_dimensions = _compact_validation_dimensions(
            row.get("validation_missing_dimensions")
        )
        validation_failed_dimensions = _compact_validation_dimensions(
            row.get("validation_failed_dimensions")
        )
        validation_thin_dimensions = _compact_validation_dimensions(
            row.get("validation_thin_dimensions")
        )
        validation_evidence_repair_hint = _validation_evidence_repair_hint(row)
        validation_evidence_repair_targets = _validation_evidence_repair_targets(row)
        core_validation_evidence_gaps = _core_validation_evidence_gaps(row)
        validation_evidence_weak = _validation_evidence_is_weak(row)
        validation_repair_enforced_count = _prompt_count(
            row.get("validation_repair_enforced_count")
        ) or 0
        validation_repair_scale_blocked_count = _prompt_count(
            row.get("validation_repair_scale_up_blocked_count")
        ) or 0
        validation_repair_waiting_entry_count = _prompt_count(
            row.get("validation_repair_waiting_entry_count")
        ) or 0
        validation_repair_rejected_count = _prompt_count(
            row.get("validation_repair_rejected_count")
        ) or 0
        validation_repair_avg_budget_multiplier = _prompt_float(
            row.get("validation_repair_avg_budget_multiplier")
        )
        validation_repair_action_counts = _compact_label_counts(
            row.get("validation_repair_action_counts")
        )
        validation_repair_adjustment_reason_counts = _compact_label_counts(
            row.get("validation_repair_adjustment_reason_counts")
        )
        validation_repair_weak = bool(
            validation_repair_enforced_count > 0
            and (
                validation_repair_scale_blocked_count > 0
                or validation_repair_waiting_entry_count > 0
                or validation_repair_rejected_count > 0
            )
        )
        performance_profile = _lane_performance_evidence_profile(row)
        performance_evidence_status = _prompt_text(
            performance_profile.get("status"),
            limit=60,
        )
        performance_missing_metrics = [
            _prompt_text(item, limit=80)
            for item in list(performance_profile.get("missing_metrics") or [])[:8]
            if _prompt_text(item, limit=80)
        ]
        performance_weak_metrics = [
            _prompt_text(item, limit=80)
            for item in list(performance_profile.get("weak_metrics") or [])[:8]
            if _prompt_text(item, limit=80)
        ]
        performance_severe_metrics = [
            _prompt_text(item, limit=80)
            for item in list(performance_profile.get("severe_metrics") or [])[:8]
            if _prompt_text(item, limit=80)
        ]
        performance_scale_blocking_metrics = _lane_performance_scale_blocking_metrics(
            performance_profile
        )
        performance_repair_targets = [
            _prompt_text(item, limit=140)
            for item in list(performance_profile.get("repair_targets") or [])[:8]
            if _prompt_text(item, limit=140)
        ]
        performance_evidence_weak = _lane_performance_requires_repair(
            grade=grade,
            sample_count=sample_count,
            min_samples_to_scale=min_samples_to_scale,
            missing_metrics=performance_missing_metrics,
            scale_blocking_metrics=performance_scale_blocking_metrics,
            severe_metrics=performance_severe_metrics,
        )
        cost_weak = (
            cost_drag >= LANE_COST_DRAG_WAITING_ENTRY_THRESHOLD_PCT
            and grade not in {"qualified", "scale_candidate"}
        )
        has_expectancy = row.get("expectancy_pct") not in (None, "", [], {})
        has_win_rate = (
            row.get("win_rate") not in (None, "", [], {})
            or row.get("win_rate_pct") not in (None, "", [], {})
        )
        has_profit_factor = row.get("profit_factor") not in (None, "", [], {})
        early_loss_weak = (
            grade == "insufficient"
            and sample_count > 0
            and (
                (has_expectancy and expectancy < 0)
                or (has_win_rate and win_rate < 45.0)
                or (has_profit_factor and profit_factor < 1.0)
            )
        )
        row_multiplier = max(_prompt_float(row.get("authority_multiplier")), 0.0)
        risk_budget_passport = _lane_risk_budget_passport(
            row,
            min_samples_to_scale=min_samples_to_scale,
        )
        risk_budget_cap = _prompt_float(
            risk_budget_passport.get("applied_risk_budget_multiplier")
        )
        applied_max_budget_multiplier = row_multiplier or 1.0
        if max_budget_multiplier > 0:
            applied_max_budget_multiplier = min(
                applied_max_budget_multiplier,
                float(max_budget_multiplier),
            )
        if risk_budget_cap > 0:
            applied_max_budget_multiplier = min(
                applied_max_budget_multiplier,
                risk_budget_cap,
            )
        action = "normal_review"
        sizing_posture = "normal"
        if grade == "scale_candidate":
            add_unique(scale_candidate_lanes, lane)
            action = "eligible_to_press_when_validation_clear"
            sizing_posture = "press_when_validation_clear"
        elif grade == "qualified":
            add_unique(qualified_lanes, lane)
            action = "normal_or_selective_press"
            sizing_posture = "normal_or_selective_press"
        elif grade == "insufficient":
            add_unique(insufficient_lanes, lane)
            if early_loss_weak:
                add_unique(early_loss_lanes, lane)
                add_unique(weak_lanes, lane)
                action = "early_loss_waiting_probe"
                sizing_posture = "early_loss_waiting_probe"
            else:
                action = "small_probe_until_sample_builds"
                sizing_posture = "small_probe"
        elif grade == "restricted":
            add_unique(weak_lanes, lane)
            action = "de_risk_or_waiting_entry"
            sizing_posture = "de_risk"
        elif grade == "observe_only":
            add_unique(weak_lanes, lane)
            action = "observe_or_waiting_entry"
            sizing_posture = "observe_or_waiting_entry"
        elif expectancy < 0 or win_rate < 45.0:
            add_unique(weak_lanes, lane)
            action = "de_risk_or_waiting_entry"
            sizing_posture = "de_risk"
        if cost_weak:
            add_unique(cost_weak_lanes, lane)
            add_unique(weak_lanes, lane)
            if grade == "insufficient":
                action = "cost_repair_waiting_probe"
                sizing_posture = "cost_repair_waiting_probe"
            elif action == "normal_review":
                action = "cost_repair_waiting_entry"
                sizing_posture = "cost_repair_waiting_entry"
        if cost_precision_weak or verified_edge_sample_weak or verified_edge_net_weak:
            add_unique(cost_evidence_weak_lanes, lane)
            add_unique(weak_lanes, lane)
            if lane in scale_candidate_lanes:
                scale_candidate_lanes.remove(lane)
            if grade in {"qualified", "scale_candidate"}:
                add_unique(qualified_lanes, lane)
            action = "cost_evidence_repair_waiting_probe"
            sizing_posture = "cost_evidence_repair_probe"
        if entry_quality_weak:
            add_unique(entry_quality_weak_lanes, lane)
            add_unique(weak_lanes, lane)
            if lane in scale_candidate_lanes:
                scale_candidate_lanes.remove(lane)
            if grade in {"qualified", "scale_candidate"}:
                add_unique(qualified_lanes, lane)
            action = "entry_quality_repair_waiting_entry"
            sizing_posture = "entry_quality_repair_probe"
        if validation_evidence_weak:
            add_unique(validation_evidence_weak_lanes, lane)
            add_unique(weak_lanes, lane)
            if lane in scale_candidate_lanes:
                scale_candidate_lanes.remove(lane)
            if grade in {"qualified", "scale_candidate"}:
                add_unique(qualified_lanes, lane)
            action = "validation_evidence_repair_waiting_probe"
            sizing_posture = "validation_evidence_repair_probe"
        if validation_repair_weak:
            add_unique(validation_repair_weak_lanes, lane)
            add_unique(weak_lanes, lane)
            if lane in scale_candidate_lanes:
                scale_candidate_lanes.remove(lane)
            if grade in {"qualified", "scale_candidate"}:
                add_unique(qualified_lanes, lane)
            action = "validation_repair_enforced_waiting_probe"
            sizing_posture = "validation_repair_probe"
        if performance_evidence_weak:
            add_unique(performance_evidence_weak_lanes, lane)
            add_unique(weak_lanes, lane)
            if lane in scale_candidate_lanes:
                scale_candidate_lanes.remove(lane)
            if grade in {"qualified", "scale_candidate"}:
                add_unique(qualified_lanes, lane)
            if action in {
                "normal_review",
                "eligible_to_press_when_validation_clear",
                "normal_or_selective_press",
                "small_probe_until_sample_builds",
            }:
                action = "performance_evidence_repair_waiting_probe"
                sizing_posture = "performance_evidence_repair_probe"
        entry_quality_requirements: list[str] = []
        if cost_weak:
            entry_quality_requirements.extend(LANE_COST_REPAIR_ENTRY_REQUIREMENTS)
        if cost_precision_weak or verified_edge_sample_weak or verified_edge_net_weak:
            for requirement in LANE_COST_EVIDENCE_REPAIR_ENTRY_REQUIREMENTS:
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        if verified_edge_sample_weak:
            requirement = "build_recorded_cost_alpha_sample_count_before_pressing"
            if requirement not in entry_quality_requirements:
                entry_quality_requirements.append(requirement)
        if verified_edge_net_weak:
            requirement = "require_positive_recorded_cost_alpha_net_pnl_before_pressing"
            if requirement not in entry_quality_requirements:
                entry_quality_requirements.append(requirement)
        if early_loss_weak:
            for requirement in LANE_EARLY_LOSS_ENTRY_REQUIREMENTS:
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        if entry_quality_weak:
            for requirement in LANE_ENTRY_QUALITY_REPAIR_ENTRY_REQUIREMENTS:
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        if validation_evidence_weak:
            for requirement in (
                "backtest_wfa_oos_live_shadow_before_scale_up",
                "use_waiting_or_shadow_entry_until_validation_evidence_is_complete",
            ):
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        if core_validation_evidence_gaps:
            for requirement in (
                "require_backtest_wfa_oos_live_shadow_pass_before_pressing",
                "block_scale_until_core_validation_evidence_is_complete",
            ):
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        if validation_repair_weak:
            for requirement in LANE_VALIDATION_REPAIR_ENTRY_REQUIREMENTS:
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        if performance_evidence_weak:
            for requirement in LANE_PERFORMANCE_REPAIR_ENTRY_REQUIREMENTS:
                if requirement not in entry_quality_requirements:
                    entry_quality_requirements.append(requirement)
        lane_actions[lane] = {
            "grade": grade,
            "action": action,
            "sizing_posture": sizing_posture,
            "max_budget_multiplier": round(row_multiplier, 4),
            "applied_max_budget_multiplier": round(
                applied_max_budget_multiplier,
                4,
            ),
            "scale_up_allowed": bool(
                allow_scale_up
                and grade == "scale_candidate"
                and not validation_repair_weak
                and not performance_evidence_weak
                and applied_max_budget_multiplier > 1.0
            ),
            "scale_decision": risk_budget_passport.get("scale_decision"),
            "scale_blockers": list(risk_budget_passport.get("scale_blockers") or [])[:10],
            "scale_repair_targets": list(
                risk_budget_passport.get("scale_repair_targets") or []
            )[:10],
            "sample_count": sample_count,
            "expectancy_pct": round(expectancy, 6),
            "win_rate": round(win_rate, 6),
            "max_drawdown_pct": round(drawdown, 6),
            "profit_factor": round(profit_factor, 6),
            "recovery_factor": round(recovery_factor, 6),
            "cumulative_return_pct": round(cumulative_return, 6),
            "cost_drag_pct_of_gross_pnl": round(cost_drag, 6),
            "cost_precision_verified_rate": (
                round(cost_precision_verified_rate, 6)
                if has_cost_precision
                else None
            ),
            "cost_precision_counts": cost_precision_counts,
            "missing_cost_component_counts": missing_cost_component_counts,
            "present_cost_component_counts": present_cost_component_counts,
            "required_cost_component_counts": required_cost_component_counts,
            "cost_precision_reason_counts": cost_precision_reason_counts,
            "cost_evidence_status": cost_evidence_status,
            "cost_evidence_repair_hint": cost_evidence_repair_hint,
            "cost_repair_targets": cost_repair_targets,
            "cost_verified_alpha_count": cost_verified_alpha_count,
            "cost_unverified_alpha_count": cost_unverified_alpha_count,
            "cost_verified_alpha_net_pnl": (
                round(cost_verified_alpha_net, 8)
                if has_cost_verified_alpha_net
                else None
            ),
            "cost_unverified_alpha_net_pnl": (
                round(cost_unverified_alpha_net, 8)
                if has_cost_unverified_alpha_net
                else None
            ),
            "cost_hybrid_alpha_count": cost_hybrid_alpha_count,
            "cost_hybrid_alpha_net_pnl": (
                round(cost_hybrid_alpha_net, 8)
                if has_cost_hybrid_alpha_net
                else None
            ),
            "scale_blocked_by_cost_precision": cost_precision_weak,
            "scale_blocked_by_cost_evidence": bool(
                row.get("scale_blocked_by_cost_evidence")
            )
            or cost_precision_weak
            or verified_edge_sample_weak
            or verified_edge_net_weak,
            "scale_blocked_by_verified_edge_samples": verified_edge_sample_weak,
            "scale_blocked_by_verified_edge_net_pnl": verified_edge_net_weak,
            "entry_quality_sample_count": entry_quality_sample_count,
            "avg_entry_quality_score": (
                round(avg_entry_quality_score, 6)
                if entry_quality_sample_count > 0
                else None
            ),
            "bad_entry_quality_rate_pct": (
                round(bad_entry_quality_rate, 6)
                if entry_quality_sample_count > 0
                else None
            ),
            "entry_quality_label_counts": entry_quality_label_counts,
            "bad_entry_quality_label_counts": bad_entry_quality_label_counts,
            "good_entry_quality_label_counts": good_entry_quality_label_counts,
            "dominant_bad_entry_quality_label": dominant_bad_entry_quality_label,
            "dominant_good_entry_quality_label": dominant_good_entry_quality_label,
            "entry_quality_repair_hint": entry_quality_repair_hint,
            "entry_repair_targets": entry_repair_targets,
            "scale_blocked_by_entry_quality": bool(
                row.get("scale_blocked_by_entry_quality")
            )
            or entry_quality_weak,
            "validation_evidence_status": validation_evidence_status,
            "validation_missing_dimensions": validation_missing_dimensions,
            "validation_failed_dimensions": validation_failed_dimensions,
            "validation_thin_dimensions": validation_thin_dimensions,
            "validation_evidence_repair_hint": validation_evidence_repair_hint,
            "validation_evidence_repair_targets": validation_evidence_repair_targets,
            "core_validation_evidence_gaps": core_validation_evidence_gaps,
            "validation_evidence_required_evidence": (
                risk_budget_passport.get("validation_evidence_required_evidence")
            ),
            "validation_evidence_required_checks": (
                risk_budget_passport.get("validation_evidence_required_checks")
            ),
            "validation_evidence_pass_collection_hooks": (
                risk_budget_passport.get("validation_evidence_pass_collection_hooks")
            ),
            "validation_evidence_pass_current_gaps": (
                risk_budget_passport.get("validation_evidence_pass_current_gaps")
            ),
            "validation_evidence_pass_criteria": (
                risk_budget_passport.get("validation_evidence_pass_criteria")
            ),
            "validation_evidence_verification_artifacts": (
                risk_budget_passport.get("validation_evidence_verification_artifacts")
            ),
            "scale_blocked_by_validation_evidence": validation_evidence_weak,
            "validation_repair_enforced_count": validation_repair_enforced_count,
            "validation_repair_scale_up_blocked_count": (
                validation_repair_scale_blocked_count
            ),
            "validation_repair_waiting_entry_count": (
                validation_repair_waiting_entry_count
            ),
            "validation_repair_rejected_count": validation_repair_rejected_count,
            "validation_repair_avg_budget_multiplier": (
                round(validation_repair_avg_budget_multiplier, 6)
                if validation_repair_avg_budget_multiplier > 0
                else None
            ),
            "validation_repair_action_counts": validation_repair_action_counts,
            "validation_repair_adjustment_reason_counts": (
                validation_repair_adjustment_reason_counts
            ),
            "scale_blocked_by_validation_repair": validation_repair_weak,
            "performance_evidence_status": performance_evidence_status,
            "performance_missing_metrics": performance_missing_metrics,
            "performance_weak_metrics": performance_weak_metrics,
            "performance_severe_metrics": performance_severe_metrics,
            "performance_scale_blocking_metrics": performance_scale_blocking_metrics,
            "performance_repair_targets": performance_repair_targets,
            "scale_blocked_by_performance_evidence": performance_evidence_weak,
            "requires_waiting_entry": bool(
                cost_weak
                or cost_precision_weak
                or verified_edge_sample_weak
                or verified_edge_net_weak
                or entry_quality_weak
                or validation_evidence_weak
                or validation_repair_weak
                or performance_evidence_weak
                or lane in weak_lanes
            ),
            "entry_quality_requirements": entry_quality_requirements,
            "risk_budget_passport": risk_budget_passport,
        }

    requirements = [
        "press_only_scale_candidate_or_qualified_lanes",
        "weak_lanes_use_observe_small_probe_or_waiting_entry",
        "insufficient_lanes_build_samples_before_size_increase",
    ]
    if weak_lanes:
        requirements.append("do_not_expand_negative_expectancy_lanes")
    if cost_weak_lanes:
        requirements.append("cost_weak_lanes_require_waiting_entry")
    if cost_evidence_weak_lanes:
        requirements.append("cost_evidence_weak_lanes_require_recorded_cost_repair")
    if early_loss_lanes:
        requirements.append("early_loss_lanes_require_waiting_entry")
    if entry_quality_weak_lanes:
        requirements.append("entry_quality_weak_lanes_require_pullback_or_waiting_entry")
    if validation_evidence_weak_lanes:
        requirements.append("validation_evidence_required_before_lane_scale_up")
    if validation_repair_weak_lanes:
        requirements.append("validation_repair_clearance_required_before_lane_scale_up")
    if performance_evidence_weak_lanes:
        requirements.append("complete_performance_metrics_required_before_lane_scale_up")
    return {
        "version": "lane_authority_v1",
        "global_scale_up_allowed": bool(allow_scale_up),
        "max_budget_multiplier": round(float(max_budget_multiplier), 4),
        "weak_lanes": weak_lanes[:12],
        "scale_candidate_lanes": scale_candidate_lanes[:12],
        "qualified_lanes": qualified_lanes[:12],
        "insufficient_lanes": insufficient_lanes[:12],
        "cost_weak_lanes": cost_weak_lanes[:12],
        "cost_evidence_weak_lanes": cost_evidence_weak_lanes[:12],
        "early_loss_lanes": early_loss_lanes[:12],
        "entry_quality_weak_lanes": entry_quality_weak_lanes[:12],
        "validation_evidence_weak_lanes": validation_evidence_weak_lanes[:12],
        "validation_repair_weak_lanes": validation_repair_weak_lanes[:12],
        "performance_evidence_weak_lanes": performance_evidence_weak_lanes[:12],
        "lane_actions": lane_actions,
        "block_design_requirements": requirements,
    }


REVALIDATION_DISCIPLINE_IDS = {
    "overfit_validation",
    "walk_forward_analysis",
    "out_of_sample_test",
    "regime_test",
    "stress_test",
}

EXPOSURE_DISCIPLINE_IDS = {
    "regime_test",
    "correlation",
    "factor_exposure",
}


def _validation_shadow_gate_from_gate(gate: dict[str, Any]) -> dict[str, Any]:
    pressure = (
        gate.get("validation_pressure")
        if isinstance(gate.get("validation_pressure"), dict)
        else {}
    )
    weak_ids: set[str] = set()
    for key in ("fail_ids", "warn_ids", "missing_ids"):
        values = pressure.get(key)
        if not isinstance(values, list):
            continue
        weak_ids.update(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    requirements = [
        str(row or "").strip()
        for row in list(pressure.get("block_design_requirements") or [])
        if str(row or "").strip()
    ]
    recovery_focus = (
        gate.get("validation_recovery_focus")
        if isinstance(gate.get("validation_recovery_focus"), list)
        else []
    )
    remediation_plan = (
        gate.get("remediation_plan")
        if isinstance(gate.get("remediation_plan"), dict)
        else {}
    )
    lane_policy_hints = (
        remediation_plan.get("lane_policy_hints")
        if isinstance(remediation_plan.get("lane_policy_hints"), dict)
        else {}
    )
    weak_revalidation_ids = sorted(
        item for item in weak_ids if item in REVALIDATION_DISCIPLINE_IDS
    )
    blocks_scale = (
        bool(weak_revalidation_ids)
        or "require_oos_wfa_or_live_shadow_before_scale_up" in requirements
        or bool(recovery_focus)
        or _prompt_bool(lane_policy_hints.get("requires_shadow_or_waiting_entry"))
    )
    if not blocks_scale:
        return {}
    focus_reasons: list[str] = []
    for row in recovery_focus[:4]:
        if not isinstance(row, dict):
            continue
        reason = _prompt_text(row.get("reason"), limit=100)
        if reason:
            focus_reasons.append(reason)
    return {
        "version": "validation_shadow_gate_v1",
        "status": "revalidation_required_before_scale_up",
        "blocks_scale_up": True,
        "requires_live_shadow": True,
        "requires_waiting_entry": True,
        "weak_validation_ids": weak_revalidation_ids[:8],
        "focus_reasons": focus_reasons[:4],
        "entry_policy": "shadow_or_waiting_entry_until_oos_wfa_rebuilt",
        "scale_policy": "no_size_increase_until_backtest_wfa_oos_live_shadow_clear",
    }


def _validation_exposure_gate_from_gate(gate: dict[str, Any]) -> dict[str, Any]:
    pressure = (
        gate.get("validation_pressure")
        if isinstance(gate.get("validation_pressure"), dict)
        else {}
    )
    weak_ids: set[str] = set()
    fail_ids: set[str] = set()
    for key in ("fail_ids", "warn_ids", "missing_ids"):
        values = pressure.get(key)
        if not isinstance(values, list):
            continue
        rows = {
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        }
        weak_ids.update(rows)
        if key == "fail_ids":
            fail_ids.update(rows)
    requirements = [
        str(row or "").strip()
        for row in list(pressure.get("block_design_requirements") or [])
        if str(row or "").strip()
    ]
    remediation_plan = (
        gate.get("remediation_plan")
        if isinstance(gate.get("remediation_plan"), dict)
        else {}
    )
    lane_policy_hints = (
        remediation_plan.get("lane_policy_hints")
        if isinstance(remediation_plan.get("lane_policy_hints"), dict)
        else {}
    )
    work_queue = (
        remediation_plan.get("work_queue")
        if isinstance(remediation_plan.get("work_queue"), list)
        else []
    )
    weak_exposure_ids = sorted(
        item for item in weak_ids if item in EXPOSURE_DISCIPLINE_IDS
    )
    for row in work_queue:
        if not isinstance(row, dict):
            continue
        discipline_id = _prompt_text(row.get("discipline_id"), limit=80)
        status = _prompt_text(row.get("status"), limit=40)
        if discipline_id in EXPOSURE_DISCIPLINE_IDS and status != "pass":
            _append_unique(weak_exposure_ids, discipline_id)
    hint_values = {
        _prompt_text(lane_policy_hints.get(key), limit=120).strip().lower()
        for key in (
            "entry_mode",
            "risk_budget_mode",
            "weak_lane_default",
            "concentration_policy",
            "regime_policy",
        )
    }
    for row in work_queue:
        if not isinstance(row, dict):
            continue
        hint_values.update(
            {
                _prompt_text(row.get("lane_policy_hint"), limit=140)
                .strip()
                .lower(),
                _prompt_text(row.get("blocks_scaling"), limit=140)
                .strip()
                .lower(),
            }
        )
    exposure_hint_present = bool(
        hint_values.intersection(
            {
                "avoid_unpriced_concentration",
                "cap_correlated_exposure",
                "regime_confirmed_only",
                "regime_mismatch_probe_only",
            }
        )
    )
    blocks_scale = (
        bool(weak_exposure_ids)
        or "cap_concentration_and_confirm_capacity_before_scaling" in requirements
        or exposure_hint_present
    )
    if not blocks_scale:
        return {}
    cap_multiplier = 0.5 if fail_ids.intersection(EXPOSURE_DISCIPLINE_IDS) else 0.75
    return {
        "version": "validation_exposure_gate_v1",
        "status": "regime_correlation_factor_review_required",
        "blocks_scale_up": True,
        "requires_waiting_entry": True,
        "weak_validation_ids": weak_exposure_ids[:8],
        "cap_multiplier": round(cap_multiplier, 6),
        "entry_policy": "regime_factor_confirmed_waiting_entry",
        "scale_policy": "cap_size_until_regime_correlation_factor_clear",
    }


def _sync_lane_authority_with_gate(packet: dict[str, Any]) -> None:
    lane_authority = (
        packet.get("lane_authority")
        if isinstance(packet.get("lane_authority"), dict)
        else {}
    )
    if not lane_authority:
        return
    synced = dict(lane_authority)
    requirements = [
        str(row)
        for row in list(synced.get("block_design_requirements") or [])
        if str(row).strip()
    ]
    allow_scale_up = bool(packet.get("allow_scale_up"))
    synced["global_scale_up_allowed"] = allow_scale_up
    synced["max_budget_multiplier"] = round(
        _prompt_float(packet.get("max_budget_multiplier")),
        4,
    )
    raw_lane_actions = (
        synced.get("lane_actions")
        if isinstance(synced.get("lane_actions"), dict)
        else {}
    )
    lane_actions: dict[str, dict[str, Any]] = {}
    for lane, raw_action in list(raw_lane_actions.items()):
        if not isinstance(raw_action, dict):
            continue
        lane_key = _canonical_authority_lane_key(lane)
        if not lane_key:
            continue
        action = dict(lane_actions.get(lane_key) or {})
        action.update(dict(raw_action))
        lane_actions[lane_key] = action
    gate = (
        packet.get("validation_gate")
        if isinstance(packet.get("validation_gate"), dict)
        else {}
    )
    validation_lane_scorecards = (
        gate.get("lane_scorecards")
        if isinstance(gate.get("lane_scorecards"), dict)
        else {}
    )
    validation_lane_actions = (
        validation_lane_scorecards.get("lane_actions")
        if isinstance(validation_lane_scorecards.get("lane_actions"), dict)
        else {}
    )

    def add_lane(list_key: str, lane: str) -> None:
        lane_key = _canonical_authority_lane_key(lane)
        rows = [
            _canonical_authority_lane_key(row)
            for row in list(synced.get(list_key) or [])
            if _canonical_authority_lane_key(row)
        ]
        if lane_key and lane_key not in rows:
            rows.append(lane_key)
        if rows:
            synced[list_key] = rows[:12]

    def remove_lane(list_key: str, lane: str) -> None:
        clean_lane = _canonical_authority_lane_key(lane)
        if not clean_lane:
            return
        rows = [
            _canonical_authority_lane_key(row)
            for row in list(synced.get(list_key) or [])
            if _canonical_authority_lane_key(row)
            and _canonical_authority_lane_key(row) != clean_lane
        ]
        synced[list_key] = rows[:12]

    validation_weak_lane_keys = {
        "weak_lanes",
        "cost_weak_lanes",
        "cost_evidence_weak_lanes",
        "entry_quality_weak_lanes",
        "validation_evidence_weak_lanes",
        "validation_repair_weak_lanes",
    }
    for list_key in (
        "weak_lanes",
        "scale_candidate_lanes",
        "qualified_lanes",
        "insufficient_lanes",
        "cost_weak_lanes",
        "cost_evidence_weak_lanes",
        "entry_quality_weak_lanes",
        "validation_evidence_weak_lanes",
        "validation_repair_weak_lanes",
    ):
        for lane in _compact_lane_names(
            validation_lane_scorecards.get(list_key),
            limit=12,
        ):
            add_lane(list_key, lane)
            if list_key in validation_weak_lane_keys:
                add_lane("weak_lanes", lane)

    for lane, raw_validation_action in list(validation_lane_actions.items()):
        if not isinstance(raw_validation_action, dict):
            continue
        lane_key = _canonical_authority_lane_key(lane)
        if not lane_key:
            continue
        action = (
            dict(lane_actions.get(lane_key))
            if isinstance(lane_actions.get(lane_key), dict)
            else {}
        )
        validation_grade = _normalize_authority_grade(
            raw_validation_action.get("grade")
        )
        validation_action = _prompt_text(
            raw_validation_action.get("action"),
            limit=120,
        )
        if not action:
            action["grade"] = validation_grade
            action["action"] = validation_action or "validation_lane_review"
        validation_cost_counts = _compact_cost_precision_counts(
            raw_validation_action.get("cost_precision_counts")
        )
        validation_missing_cost_counts = _compact_label_counts(
            raw_validation_action.get("missing_cost_component_counts")
        )
        validation_present_cost_counts = _compact_label_counts(
            raw_validation_action.get("present_cost_component_counts")
        )
        validation_required_cost_counts = _compact_label_counts(
            raw_validation_action.get("required_cost_component_counts")
        )
        validation_cost_reason_counts = _compact_label_counts(
            raw_validation_action.get("cost_precision_reason_counts"),
            limit=6,
        )
        validation_cost_status = _prompt_text(
            raw_validation_action.get("cost_evidence_status"),
            limit=80,
        )
        validation_cost_repair_hint = _cost_evidence_repair_hint(
            raw_validation_action
        )
        validation_cost_repair_targets = _cost_evidence_repair_targets(
            raw_validation_action
        )
        validation_evidence_repair_targets = _validation_evidence_repair_targets(
            raw_validation_action
        )
        core_validation_evidence_gaps = _core_validation_evidence_gaps(
            raw_validation_action
        )
        validation_evidence_required_evidence = [
            _prompt_text(item, limit=100)
            for item in list(
                raw_validation_action.get("validation_evidence_required_evidence")
                or []
            )[:8]
            if _prompt_text(item, limit=100)
        ]
        validation_evidence_required_checks = [
            _prompt_text(item, limit=100)
            for item in list(
                raw_validation_action.get("validation_evidence_required_checks")
                or []
            )[:8]
            if _prompt_text(item, limit=100)
        ]
        validation_evidence_pass_collection_hooks = [
            _prompt_text(item, limit=140)
            for item in list(
                raw_validation_action.get(
                    "validation_evidence_pass_collection_hooks"
                )
                or []
            )[:6]
            if _prompt_text(item, limit=140)
        ]
        validation_evidence_pass_current_gaps = [
            _prompt_text(item, limit=140)
            for item in list(
                raw_validation_action.get("validation_evidence_pass_current_gaps")
                or []
            )[:6]
            if _prompt_text(item, limit=140)
        ]
        validation_evidence_pass_criteria = [
            _prompt_text(item, limit=160)
            for item in list(
                raw_validation_action.get("validation_evidence_pass_criteria") or []
            )[:6]
            if _prompt_text(item, limit=160)
        ]
        validation_evidence_verification_artifacts = [
            _prompt_text(item, limit=160)
            for item in list(
                raw_validation_action.get(
                    "validation_evidence_verification_artifacts"
                )
                or []
            )[:6]
            if _prompt_text(item, limit=160)
        ]
        validation_cost_hybrid_count = _prompt_count(
            raw_validation_action.get("cost_hybrid_alpha_count")
        )
        validation_cost_hybrid_net = raw_validation_action.get(
            "cost_hybrid_alpha_net_pnl"
        )
        validation_cost_verified_count = _prompt_count(
            raw_validation_action.get("cost_verified_alpha_count")
        )
        validation_cost_unverified_count = _prompt_count(
            raw_validation_action.get("cost_unverified_alpha_count")
        )
        validation_cost_verified_net = raw_validation_action.get(
            "cost_verified_alpha_net_pnl"
        )
        validation_cost_unverified_net = raw_validation_action.get(
            "cost_unverified_alpha_net_pnl"
        )
        validation_cost_sample_count = (
            _prompt_count(raw_validation_action.get("sample_count"))
            or _prompt_count(action.get("sample_count"))
            or 0
        )
        validation_cost_precision_weak = _cost_evidence_requires_repair(
            raw_validation_action,
            sample_count=validation_cost_sample_count,
            min_samples_to_scale=(
                _prompt_count(raw_validation_action.get("min_samples_to_scale"))
                or 10
            ),
        )
        if validation_cost_precision_weak and not validation_cost_status:
            validation_cost_status = (
                "estimated_or_missing"
                if validation_cost_counts
                else "unverified_cost_alpha"
            )
        if validation_cost_precision_weak and not validation_cost_repair_hint:
            validation_cost_repair_hint = (
                "increase_recorded_cost_precision_before_size_increase"
            )
        validation_verified_edge_sample_weak = _prompt_bool(
            raw_validation_action.get("scale_blocked_by_verified_edge_samples")
        )
        validation_verified_edge_net_weak = _prompt_bool(
            raw_validation_action.get("scale_blocked_by_verified_edge_net_pnl")
        )
        validation_repair_enforced_count = _prompt_count(
            raw_validation_action.get("validation_repair_enforced_count")
        ) or 0
        validation_repair_scale_blocked_count = _prompt_count(
            raw_validation_action.get("validation_repair_scale_up_blocked_count")
        ) or 0
        validation_repair_waiting_entry_count = _prompt_count(
            raw_validation_action.get("validation_repair_waiting_entry_count")
        ) or 0
        validation_repair_rejected_count = _prompt_count(
            raw_validation_action.get("validation_repair_rejected_count")
        ) or 0
        validation_repair_action_counts = _compact_label_counts(
            raw_validation_action.get("validation_repair_action_counts")
        )
        validation_repair_adjustment_reason_counts = _compact_label_counts(
            raw_validation_action.get("validation_repair_adjustment_reason_counts")
        )
        validation_repair_weak = bool(
            validation_repair_enforced_count > 0
            and (
                validation_repair_scale_blocked_count > 0
                or validation_repair_waiting_entry_count > 0
                or validation_repair_rejected_count > 0
            )
        )
        action["validation_lane_scorecard_action"] = {
            key: value
            for key, value in {
                "grade": validation_grade,
                "action": validation_action,
                "sample_count": raw_validation_action.get("sample_count"),
                "expectancy_pct": raw_validation_action.get("expectancy_pct"),
                "profit_factor": raw_validation_action.get("profit_factor"),
                "recovery_factor": raw_validation_action.get("recovery_factor"),
                "risk_of_ruin_pct": raw_validation_action.get("risk_of_ruin_pct"),
                "cost_drag_pct_of_gross_pnl": raw_validation_action.get(
                    "cost_drag_pct_of_gross_pnl"
                ),
                "cost_precision_verified_rate": raw_validation_action.get(
                    "cost_precision_verified_rate"
                )
                if raw_validation_action.get("cost_precision_verified_rate")
                not in (None, "", [], {})
                else raw_validation_action.get("cost_precision_verified_rate_pct"),
                "cost_precision_counts": validation_cost_counts,
                "missing_cost_component_counts": validation_missing_cost_counts,
                "present_cost_component_counts": validation_present_cost_counts,
                "required_cost_component_counts": validation_required_cost_counts,
                "cost_precision_reason_counts": validation_cost_reason_counts,
                "cost_evidence_status": validation_cost_status,
                "cost_evidence_repair_hint": validation_cost_repair_hint,
                "cost_repair_targets": validation_cost_repair_targets,
                "cost_verified_alpha_count": validation_cost_verified_count,
                "cost_unverified_alpha_count": validation_cost_unverified_count,
                "cost_verified_alpha_net_pnl": validation_cost_verified_net,
                "cost_unverified_alpha_net_pnl": validation_cost_unverified_net,
                "cost_hybrid_alpha_count": validation_cost_hybrid_count,
                "cost_hybrid_alpha_net_pnl": validation_cost_hybrid_net,
                "scale_blocked_by_cost_precision": raw_validation_action.get(
                    "scale_blocked_by_cost_precision"
                )
                or validation_cost_precision_weak,
                "scale_blocked_by_cost_evidence": raw_validation_action.get(
                    "scale_blocked_by_cost_evidence"
                )
                or validation_cost_precision_weak,
                "scale_blocked_by_verified_edge_samples": (
                    validation_verified_edge_sample_weak
                ),
                "scale_blocked_by_verified_edge_net_pnl": (
                    validation_verified_edge_net_weak
                ),
                "avg_entry_quality_score": raw_validation_action.get(
                    "avg_entry_quality_score"
                ),
                "bad_entry_quality_rate_pct": raw_validation_action.get(
                    "bad_entry_quality_rate_pct"
                ),
                "entry_quality_label_counts": _compact_label_counts(
                    raw_validation_action.get("entry_quality_label_counts")
                ),
                "bad_entry_quality_label_counts": _compact_label_counts(
                    raw_validation_action.get("bad_entry_quality_label_counts")
                ),
                "good_entry_quality_label_counts": _compact_label_counts(
                    raw_validation_action.get("good_entry_quality_label_counts")
                ),
                "dominant_bad_entry_quality_label": raw_validation_action.get(
                    "dominant_bad_entry_quality_label"
                ),
                "dominant_good_entry_quality_label": raw_validation_action.get(
                    "dominant_good_entry_quality_label"
                ),
                "entry_quality_repair_hint": _entry_quality_repair_hint(
                    raw_validation_action
                ),
                "entry_repair_targets": _entry_quality_repair_targets(
                    raw_validation_action
                ),
                "validation_evidence_status": raw_validation_action.get(
                    "validation_evidence_status"
                ),
                "validation_missing_dimensions": _compact_validation_dimensions(
                    raw_validation_action.get("validation_missing_dimensions")
                ),
                "validation_failed_dimensions": _compact_validation_dimensions(
                    raw_validation_action.get("validation_failed_dimensions")
                ),
                "validation_evidence_repair_hint": (
                    _validation_evidence_repair_hint(raw_validation_action)
                ),
                "validation_evidence_repair_targets": (
                    validation_evidence_repair_targets
                ),
                "core_validation_evidence_gaps": core_validation_evidence_gaps,
                "validation_evidence_required_evidence": (
                    validation_evidence_required_evidence
                ),
                "validation_evidence_required_checks": (
                    validation_evidence_required_checks
                ),
                "validation_evidence_pass_collection_hooks": (
                    validation_evidence_pass_collection_hooks
                ),
                "validation_evidence_pass_current_gaps": (
                    validation_evidence_pass_current_gaps
                ),
                "validation_evidence_pass_criteria": (
                    validation_evidence_pass_criteria
                ),
                "validation_evidence_verification_artifacts": (
                    validation_evidence_verification_artifacts
                ),
                "scale_blocked_by_validation_evidence": raw_validation_action.get(
                    "scale_blocked_by_validation_evidence"
                )
                or _validation_evidence_is_weak(raw_validation_action),
                "validation_repair_enforced_count": (
                    validation_repair_enforced_count
                ),
                "validation_repair_scale_up_blocked_count": (
                    validation_repair_scale_blocked_count
                ),
                "validation_repair_waiting_entry_count": (
                    validation_repair_waiting_entry_count
                ),
                "validation_repair_rejected_count": (
                    validation_repair_rejected_count
                ),
                "validation_repair_avg_budget_multiplier": (
                    raw_validation_action.get(
                        "validation_repair_avg_budget_multiplier"
                    )
                ),
                "validation_repair_action_counts": (
                    validation_repair_action_counts
                ),
                "validation_repair_adjustment_reason_counts": (
                    validation_repair_adjustment_reason_counts
                ),
                "scale_blocked_by_validation_repair": validation_repair_weak,
            }.items()
            if value not in (None, "", [], {})
        }
        for key, value in (
            (
                "cost_precision_verified_rate",
                raw_validation_action.get("cost_precision_verified_rate")
                if raw_validation_action.get("cost_precision_verified_rate")
                not in (None, "", [], {})
                else raw_validation_action.get("cost_precision_verified_rate_pct"),
            ),
            ("cost_precision_counts", validation_cost_counts),
            ("missing_cost_component_counts", validation_missing_cost_counts),
            ("present_cost_component_counts", validation_present_cost_counts),
            ("required_cost_component_counts", validation_required_cost_counts),
            ("cost_precision_reason_counts", validation_cost_reason_counts),
            ("cost_evidence_status", validation_cost_status),
            ("cost_evidence_repair_hint", validation_cost_repair_hint),
            ("cost_repair_targets", validation_cost_repair_targets),
            ("cost_verified_alpha_count", validation_cost_verified_count),
            ("cost_unverified_alpha_count", validation_cost_unverified_count),
            ("cost_verified_alpha_net_pnl", validation_cost_verified_net),
            ("cost_unverified_alpha_net_pnl", validation_cost_unverified_net),
            ("cost_hybrid_alpha_count", validation_cost_hybrid_count),
            ("cost_hybrid_alpha_net_pnl", validation_cost_hybrid_net),
            (
                "scale_blocked_by_cost_precision",
                raw_validation_action.get("scale_blocked_by_cost_precision")
                or validation_cost_precision_weak,
            ),
            (
                "scale_blocked_by_cost_evidence",
                raw_validation_action.get("scale_blocked_by_cost_evidence")
                or validation_cost_precision_weak,
            ),
            (
                "scale_blocked_by_verified_edge_samples",
                validation_verified_edge_sample_weak,
            ),
            (
                "scale_blocked_by_verified_edge_net_pnl",
                validation_verified_edge_net_weak,
            ),
            (
                "entry_quality_label_counts",
                _compact_label_counts(
                    raw_validation_action.get("entry_quality_label_counts")
                ),
            ),
            (
                "bad_entry_quality_label_counts",
                _compact_label_counts(
                    raw_validation_action.get("bad_entry_quality_label_counts")
                ),
            ),
            (
                "good_entry_quality_label_counts",
                _compact_label_counts(
                    raw_validation_action.get("good_entry_quality_label_counts")
                ),
            ),
            (
                "dominant_bad_entry_quality_label",
                raw_validation_action.get("dominant_bad_entry_quality_label"),
            ),
            (
                "dominant_good_entry_quality_label",
                raw_validation_action.get("dominant_good_entry_quality_label"),
            ),
            (
                "entry_quality_repair_hint",
                _entry_quality_repair_hint(raw_validation_action),
            ),
            (
                "entry_repair_targets",
                _entry_quality_repair_targets(raw_validation_action),
            ),
            (
                "validation_evidence_repair_hint",
                _validation_evidence_repair_hint(raw_validation_action),
            ),
            (
                "scale_blocked_by_validation_evidence",
                _validation_evidence_is_weak(raw_validation_action),
            ),
            ("validation_repair_enforced_count", validation_repair_enforced_count),
            (
                "validation_repair_scale_up_blocked_count",
                validation_repair_scale_blocked_count,
            ),
            (
                "validation_repair_waiting_entry_count",
                validation_repair_waiting_entry_count,
            ),
            (
                "validation_repair_rejected_count",
                validation_repair_rejected_count,
            ),
            (
                "validation_repair_avg_budget_multiplier",
                raw_validation_action.get("validation_repair_avg_budget_multiplier"),
            ),
            ("validation_repair_action_counts", validation_repair_action_counts),
            (
                "validation_repair_adjustment_reason_counts",
                validation_repair_adjustment_reason_counts,
            ),
            ("scale_blocked_by_validation_repair", validation_repair_weak),
        ):
            if value not in (None, "", [], {}) and action.get(key) in (
                None,
                "",
                [],
                {},
            ):
                action[key] = value
        for key, value in (
            ("cost_verified_alpha_count", validation_cost_verified_count),
            ("cost_unverified_alpha_count", validation_cost_unverified_count),
            ("cost_verified_alpha_net_pnl", validation_cost_verified_net),
            ("cost_unverified_alpha_net_pnl", validation_cost_unverified_net),
        ):
            if value not in (None, "", [], {}):
                action[key] = value
        for key in (
            "scale_blocked_by_cost_precision",
            "scale_blocked_by_cost_evidence",
            "scale_blocked_by_verified_edge_samples",
            "scale_blocked_by_verified_edge_net_pnl",
            "scale_blocked_by_entry_quality",
        ):
            if _prompt_bool(raw_validation_action.get(key)):
                action[key] = True
        if validation_cost_precision_weak:
            action["scale_blocked_by_cost_precision"] = True
            action["scale_blocked_by_cost_evidence"] = True
        if validation_repair_weak:
            action["scale_blocked_by_validation_repair"] = True
            action["validation_repair_enforced_count"] = (
                validation_repair_enforced_count
            )
            action["validation_repair_scale_up_blocked_count"] = (
                validation_repair_scale_blocked_count
            )
            action["validation_repair_waiting_entry_count"] = (
                validation_repair_waiting_entry_count
            )
            action["validation_repair_rejected_count"] = (
                validation_repair_rejected_count
            )
            if raw_validation_action.get("validation_repair_avg_budget_multiplier") not in (
                None,
                "",
                [],
                {},
            ):
                action["validation_repair_avg_budget_multiplier"] = (
                    raw_validation_action.get(
                        "validation_repair_avg_budget_multiplier"
                    )
                )
            if validation_repair_action_counts:
                action["validation_repair_action_counts"] = (
                    validation_repair_action_counts
                )
            if validation_repair_adjustment_reason_counts:
                action["validation_repair_adjustment_reason_counts"] = (
                    validation_repair_adjustment_reason_counts
                )
        validation_max = _prompt_float(
            raw_validation_action.get("applied_max_budget_multiplier")
            or raw_validation_action.get("max_budget_multiplier")
            or raw_validation_action.get("authority_multiplier")
        )
        if validation_max > 0:
            existing_max = _prompt_float(action.get("max_budget_multiplier"))
            action["max_budget_multiplier"] = round(
                min(existing_max, validation_max) if existing_max > 0 else validation_max,
                6,
            )
            action["validation_lane_max_budget_multiplier"] = round(
                validation_max,
                6,
            )
        validation_requires_waiting = (
            bool(raw_validation_action.get("requires_waiting_entry"))
            or validation_grade in {"restricted", "observe_only", "insufficient"}
            or _prompt_bool(raw_validation_action.get("scale_blocked_by_cost_precision"))
            or _prompt_bool(raw_validation_action.get("scale_blocked_by_cost_evidence"))
            or validation_cost_precision_weak
            or validation_verified_edge_sample_weak
            or validation_verified_edge_net_weak
            or _prompt_bool(raw_validation_action.get("scale_blocked_by_entry_quality"))
            or _validation_evidence_is_weak(raw_validation_action)
            or validation_repair_weak
        )
        if validation_requires_waiting:
            action["requires_waiting_entry"] = True
            entry_requirements = [
                str(row)
                for row in list(action.get("entry_quality_requirements") or [])
                if str(row).strip()
            ]
            if (
                "respect_trading_validation_lane_scorecard_before_entry"
                not in entry_requirements
            ):
                entry_requirements.append(
                    "respect_trading_validation_lane_scorecard_before_entry"
                )
            if validation_repair_weak:
                for requirement in LANE_VALIDATION_REPAIR_ENTRY_REQUIREMENTS:
                    if requirement not in entry_requirements:
                        entry_requirements.append(requirement)
            action["entry_quality_requirements"] = entry_requirements
            if validation_grade in {"restricted", "observe_only"}:
                add_lane("weak_lanes", lane_key)
            elif validation_grade == "insufficient":
                add_lane("insufficient_lanes", lane_key)
            if validation_action and validation_action != "normal_or_selective_press":
                action["action"] = validation_action
        lane_actions[lane_key] = action

    risk_governor_action = _prompt_text(
        gate.get("risk_governor_action"),
        limit=80,
    )
    risk_governor_source = _prompt_text(
        gate.get("risk_governor_source"),
        limit=80,
    )
    risk_governor_metrics = (
        gate.get("risk_governor_metrics")
        if isinstance(gate.get("risk_governor_metrics"), dict)
        else {}
    )
    shadow_gate = _validation_shadow_gate_from_gate(gate)
    shadow_blocks_scale = bool(shadow_gate.get("blocks_scale_up"))
    if shadow_blocks_scale:
        synced["validation_shadow_gate"] = shadow_gate
        if "live_shadow_or_oos_wfa_required_before_lane_scale_up" not in requirements:
            requirements.append("live_shadow_or_oos_wfa_required_before_lane_scale_up")
    exposure_gate = _validation_exposure_gate_from_gate(gate)
    exposure_blocks_scale = bool(exposure_gate.get("blocks_scale_up"))
    if exposure_blocks_scale:
        synced["validation_exposure_gate"] = exposure_gate
        if "regime_correlation_factor_review_required_before_lane_scale_up" not in requirements:
            requirements.append(
                "regime_correlation_factor_review_required_before_lane_scale_up"
            )
    applied_global = _prompt_float(packet.get("max_budget_multiplier"))
    shadow_cap_multiplier = 1.0 if shadow_blocks_scale else 0.0
    exposure_cap_multiplier = (
        _prompt_float(exposure_gate.get("cap_multiplier"))
        if exposure_blocks_scale
        else 0.0
    )
    remediation_plan = (
        gate.get("remediation_plan")
        if isinstance(gate.get("remediation_plan"), dict)
        else {}
    )
    lane_policy_hints = (
        remediation_plan.get("lane_policy_hints")
        if isinstance(remediation_plan.get("lane_policy_hints"), dict)
        else {}
    )
    remediation_work_queue = (
        remediation_plan.get("work_queue")
        if isinstance(remediation_plan.get("work_queue"), list)
        else []
    )
    remediation_entry_mode = _prompt_text(
        lane_policy_hints.get("entry_mode"),
        limit=80,
    ).strip().lower()
    remediation_risk_budget_mode = _prompt_text(
        lane_policy_hints.get("risk_budget_mode"),
        limit=80,
    ).strip().lower()
    remediation_scale_hint_present = "scale_up_allowed" in lane_policy_hints
    remediation_scale_blocked = (
        remediation_scale_hint_present
        and not _prompt_bool(lane_policy_hints.get("scale_up_allowed"))
    )
    remediation_requires_waiting = bool(
        remediation_entry_mode in {"verified_waiting_probe", "risk_off_recovery"}
        or _prompt_bool(lane_policy_hints.get("requires_shadow_or_waiting_entry"))
    )
    remediation_scale_blocked_ids: list[str] = []
    remediation_p0_ids: list[str] = []
    remediation_modes: list[str] = []
    for row in remediation_work_queue:
        if not isinstance(row, dict):
            continue
        discipline_id = _prompt_text(row.get("discipline_id"), limit=80)
        if not discipline_id:
            continue
        if _prompt_bool(row.get("scale_up_blocked")):
            _append_unique(remediation_scale_blocked_ids, discipline_id)
        if _prompt_text(row.get("priority"), limit=20).strip().lower() == "p0":
            _append_unique(remediation_p0_ids, discipline_id)
        validation_mode = _prompt_text(row.get("validation_mode"), limit=100)
        if validation_mode:
            _append_unique(remediation_modes, validation_mode)
        allowed_posture = _prompt_text(
            row.get("allowed_entry_posture"),
            limit=120,
        ).strip().lower()
        if allowed_posture and allowed_posture not in {
            "normal",
            "normal_or_selective_press",
        }:
            remediation_requires_waiting = True
    remediation_blocks_scale = bool(
        remediation_scale_blocked
        or remediation_scale_blocked_ids
        or remediation_entry_mode == "risk_off_recovery"
        or remediation_risk_budget_mode == "probe"
    )
    remediation_cap_multiplier = 0.0
    if remediation_blocks_scale:
        remediation_cap_multiplier = (
            0.25
            if remediation_entry_mode == "risk_off_recovery"
            or remediation_risk_budget_mode == "probe"
            or bool(remediation_p0_ids)
            else 0.5
        )
    remediation_gate: dict[str, Any] = {}
    if remediation_blocks_scale or remediation_requires_waiting:
        remediation_gate = {
            "version": "validation_remediation_gate_v1",
            "status": "remediation_required_before_scale_up"
            if remediation_blocks_scale
            else "remediation_waiting_entry_required",
            "blocks_scale_up": remediation_blocks_scale,
            "requires_waiting_entry": remediation_requires_waiting,
            "cap_multiplier": round(remediation_cap_multiplier, 6)
            if remediation_cap_multiplier > 0
            else None,
            "entry_mode": remediation_entry_mode,
            "risk_budget_mode": remediation_risk_budget_mode,
            "scale_blocked_discipline_ids": remediation_scale_blocked_ids[:8],
            "p0_discipline_ids": remediation_p0_ids[:8],
            "validation_modes": remediation_modes[:8],
            "entry_policy": "follow_validation_remediation_waiting_probe_mode",
            "scale_policy": "no_size_increase_until_remediation_work_queue_clears",
        }
        remediation_gate = {
            key: value
            for key, value in remediation_gate.items()
            if value not in (None, "", [], {})
        }
        synced["validation_remediation_gate"] = remediation_gate
        for requirement in (
            "respect_validation_remediation_work_queue_before_lane_scale_up",
            "use_waiting_or_probe_entry_until_remediation_clears",
        ):
            if requirement not in requirements:
                requirements.append(requirement)
    shadow_blocked_lanes: list[str] = []
    exposure_blocked_lanes: list[str] = []
    remediation_blocked_lanes: list[str] = []
    for lane, raw_action in list(lane_actions.items()):
        if not isinstance(raw_action, dict):
            continue
        action = dict(raw_action)
        lane_max = _prompt_float(action.get("max_budget_multiplier"))
        passport = (
            action.get("risk_budget_passport")
            if isinstance(action.get("risk_budget_passport"), dict)
            else {}
        )
        risk_budget_cap = _prompt_float(
            passport.get("applied_risk_budget_multiplier")
        )
        applied_caps = [lane_max, applied_global]
        if risk_budget_cap > 0:
            applied_caps.append(risk_budget_cap)
        validation_repair_blocks_scale = bool(
            action.get("scale_blocked_by_validation_repair")
        )
        cost_evidence_blocks_scale = bool(
            _prompt_bool(action.get("scale_blocked_by_cost_precision"))
            or _prompt_bool(action.get("scale_blocked_by_cost_evidence"))
            or _prompt_bool(action.get("scale_blocked_by_verified_edge_samples"))
            or _prompt_bool(action.get("scale_blocked_by_verified_edge_net_pnl"))
        )
        cost_evidence_cap_multiplier = (
            0.25
            if _prompt_bool(action.get("scale_blocked_by_verified_edge_net_pnl"))
            else 0.5
        )
        entry_quality_blocks_scale = _prompt_bool(
            action.get("scale_blocked_by_entry_quality")
        )
        validation_evidence_blocks_scale = _prompt_bool(
            action.get("scale_blocked_by_validation_evidence")
        )
        validation_repair_cap = _prompt_float(
            action.get("validation_repair_avg_budget_multiplier")
        )
        if validation_repair_blocks_scale:
            if not (0 < validation_repair_cap < 1.0):
                validation_repair_cap = 0.5
            if (_prompt_count(action.get("validation_repair_rejected_count")) or 0) > 0:
                validation_repair_cap = min(validation_repair_cap, 0.25)
            applied_caps.append(validation_repair_cap)
        if cost_evidence_blocks_scale:
            applied_caps.append(cost_evidence_cap_multiplier)
        if entry_quality_blocks_scale:
            applied_caps.append(0.5)
        if validation_evidence_blocks_scale:
            applied_caps.append(0.5)
        applied_max = min(applied_caps) if applied_global > 0 else 0.0
        if shadow_blocks_scale:
            applied_max = min(applied_max, 1.0)
        if exposure_blocks_scale and exposure_cap_multiplier > 0:
            applied_max = min(applied_max, exposure_cap_multiplier)
        if remediation_blocks_scale and remediation_cap_multiplier > 0:
            applied_max = min(applied_max, remediation_cap_multiplier)
        if passport:
            passport = dict(passport)
            if risk_governor_action:
                passport["validation_governor_action"] = risk_governor_action
                if risk_governor_source:
                    passport["validation_governor_source"] = risk_governor_source
                passport["validation_governor_cap_multiplier"] = round(
                    applied_global,
                    6,
                )
                for source_key, target_key in (
                    ("risk_of_ruin_pct", "validation_risk_of_ruin_pct"),
                    (
                        "recommended_risk_fraction",
                        "validation_recommended_risk_fraction",
                    ),
                    (
                        "max_risk_cap_fraction",
                        "validation_max_risk_cap_fraction",
                    ),
                    ("kelly_cap_reason", "validation_kelly_cap_reason"),
                    ("drawdown_usage_ratio", "validation_drawdown_usage_ratio"),
                ):
                    value = risk_governor_metrics.get(source_key)
                    if value not in (None, "", [], {}):
                        passport[target_key] = value
            if shadow_blocks_scale:
                passport["validation_shadow_gate_status"] = shadow_gate.get("status")
                passport["validation_shadow_cap_multiplier"] = round(
                    shadow_cap_multiplier,
                    6,
                )
            if exposure_blocks_scale:
                passport["validation_exposure_gate_status"] = exposure_gate.get(
                    "status"
                )
                passport["validation_exposure_cap_multiplier"] = round(
                    exposure_cap_multiplier,
                    6,
                )
            if remediation_gate:
                passport["validation_remediation_gate_status"] = (
                    remediation_gate.get("status")
                )
                if remediation_cap_multiplier > 0:
                    passport["validation_remediation_cap_multiplier"] = round(
                        remediation_cap_multiplier,
                        6,
                    )
                if remediation_scale_blocked_ids:
                    passport["validation_remediation_blocked_ids"] = (
                        remediation_scale_blocked_ids[:8]
                    )
                if remediation_p0_ids:
                    passport["validation_remediation_p0_ids"] = (
                        remediation_p0_ids[:8]
                    )
                if remediation_modes:
                    passport["validation_remediation_modes"] = (
                        remediation_modes[:8]
                    )
            passport_base = risk_budget_cap if risk_budget_cap > 0 else lane_max
            effective_caps = [passport_base, applied_global]
            if shadow_blocks_scale:
                effective_caps.append(shadow_cap_multiplier)
            if exposure_blocks_scale and exposure_cap_multiplier > 0:
                effective_caps.append(exposure_cap_multiplier)
            if remediation_blocks_scale and remediation_cap_multiplier > 0:
                passport["scale_blocked_by_validation_remediation"] = True
                effective_caps.append(remediation_cap_multiplier)
            if validation_repair_blocks_scale and validation_repair_cap > 0:
                passport["validation_repair_cap_multiplier"] = round(
                    validation_repair_cap,
                    6,
                )
                passport["scale_blocked_by_validation_repair"] = True
                effective_caps.append(validation_repair_cap)
            if cost_evidence_blocks_scale:
                passport["cost_evidence_cap_multiplier"] = (
                    cost_evidence_cap_multiplier
                )
                passport["scale_blocked_by_cost_evidence"] = True
                for source_key in (
                    "cost_verified_alpha_count",
                    "cost_unverified_alpha_count",
                    "cost_verified_alpha_net_pnl",
                    "cost_unverified_alpha_net_pnl",
                ):
                    value = action.get(source_key)
                    if value not in (None, "", [], {}) and passport.get(source_key) in (
                        None,
                        "",
                        [],
                        {},
                    ):
                        passport[source_key] = value
                if _prompt_bool(action.get("scale_blocked_by_verified_edge_samples")):
                    passport["verified_edge_sample_cap_multiplier"] = 0.5
                    passport["scale_blocked_by_verified_edge_samples"] = True
                if _prompt_bool(action.get("scale_blocked_by_verified_edge_net_pnl")):
                    passport["verified_edge_net_cap_multiplier"] = 0.25
                    passport["scale_blocked_by_verified_edge_net_pnl"] = True
                effective_caps.append(cost_evidence_cap_multiplier)
            if entry_quality_blocks_scale:
                passport["entry_quality_cap_multiplier"] = 0.5
                passport["scale_blocked_by_entry_quality"] = True
                effective_caps.append(0.5)
            if validation_evidence_blocks_scale:
                passport["validation_evidence_cap_multiplier"] = min(
                    _prompt_float(passport.get("validation_evidence_cap_multiplier"))
                    or 0.5,
                    0.5,
                )
                passport["scale_blocked_by_validation_evidence"] = True
                effective_caps.append(0.5)
            passport["effective_risk_budget_multiplier"] = round(
                min(effective_caps) if applied_global > 0 else 0.0,
                6,
            )
            action["risk_budget_passport"] = passport
        grade = _prompt_text(action.get("grade"), limit=80).strip().lower()
        if not allow_scale_up:
            _add_scale_blocker(
                action,
                blocker="validation_gate_scale_cap",
                repair_target="clear_validation_gate_before_scale_up",
            )
        if shadow_blocks_scale:
            if str(lane) not in shadow_blocked_lanes:
                shadow_blocked_lanes.append(str(lane))
            action["validation_shadow_gate"] = shadow_gate
            action["scale_up_blocked_by_shadow_gate"] = True
            action["requires_waiting_entry"] = True
            entry_requirements = [
                str(row)
                for row in list(action.get("entry_quality_requirements") or [])
                if str(row).strip()
            ]
            if "require_oos_wfa_or_live_shadow_before_scale_up" not in entry_requirements:
                entry_requirements.append(
                    "require_oos_wfa_or_live_shadow_before_scale_up"
                )
            action["entry_quality_requirements"] = entry_requirements
            _add_scale_blocker(
                action,
                blocker="validation_shadow_gate",
                repair_target="collect_oos_wfa_or_live_shadow_before_scale_up",
            )
            if grade in {"scale_candidate", "qualified"}:
                action["action"] = "shadow_or_waiting_entry_until_validation_rebuilt"
                action["sizing_posture"] = "validation_shadow_probe"
        if exposure_blocks_scale:
            if str(lane) not in exposure_blocked_lanes:
                exposure_blocked_lanes.append(str(lane))
            action["validation_exposure_gate"] = exposure_gate
            action["scale_up_blocked_by_exposure_gate"] = True
            action["requires_waiting_entry"] = True
            entry_requirements = [
                str(row)
                for row in list(action.get("entry_quality_requirements") or [])
                if str(row).strip()
            ]
            if (
                "require_regime_correlation_factor_review_before_scale_up"
                not in entry_requirements
            ):
                entry_requirements.append(
                    "require_regime_correlation_factor_review_before_scale_up"
                )
            action["entry_quality_requirements"] = entry_requirements
            _add_scale_blocker(
                action,
                blocker="validation_exposure_gate",
                repair_target="repair_regime_correlation_factor_exposure_before_scale_up",
            )
            if grade in {"scale_candidate", "qualified"} and not shadow_blocks_scale:
                action["action"] = (
                    "exposure_review_waiting_entry_until_validation_clear"
                )
                action["sizing_posture"] = "concentration_capped_probe"
        if remediation_gate:
            if remediation_blocks_scale and str(lane) not in remediation_blocked_lanes:
                remediation_blocked_lanes.append(str(lane))
            action["validation_remediation_gate"] = remediation_gate
            action["scale_up_blocked_by_validation_remediation"] = (
                remediation_blocks_scale
            )
            if remediation_requires_waiting:
                action["requires_waiting_entry"] = True
            entry_requirements = [
                str(row)
                for row in list(action.get("entry_quality_requirements") or [])
                if str(row).strip()
            ]
            for requirement in (
                "respect_validation_remediation_work_queue_before_entry",
                "use_waiting_or_probe_entry_until_remediation_clears",
            ):
                if requirement not in entry_requirements:
                    entry_requirements.append(requirement)
            action["entry_quality_requirements"] = entry_requirements
            if remediation_blocks_scale:
                _add_scale_blocker(
                    action,
                    blocker="validation_remediation_gate",
                    repair_target="clear_validation_remediation_work_queue_before_scale_up",
                )
            if (
                grade in {"scale_candidate", "qualified"}
                and not shadow_blocks_scale
                and not exposure_blocks_scale
            ):
                action["action"] = (
                    "remediation_waiting_probe_until_validation_clears"
                )
                action["sizing_posture"] = "validation_remediation_probe"
        if cost_evidence_blocks_scale:
            add_lane("weak_lanes", str(lane))
            add_lane("cost_evidence_weak_lanes", str(lane))
            remove_lane("scale_candidate_lanes", str(lane))
            action["requires_waiting_entry"] = True
            _add_scale_blocker(
                action,
                blocker=(
                    "verified_edge_net_pnl_cap"
                    if _prompt_bool(action.get("scale_blocked_by_verified_edge_net_pnl"))
                    else "verified_edge_sample_cap"
                    if _prompt_bool(action.get("scale_blocked_by_verified_edge_samples"))
                    else "cost_evidence_repair"
                ),
                repair_target=(
                    "produce_positive_recorded_cost_alpha_net_pnl_before_scale_up"
                    if _prompt_bool(action.get("scale_blocked_by_verified_edge_net_pnl"))
                    else "close_more_recorded_cost_alpha_samples_before_scale_up"
                    if _prompt_bool(action.get("scale_blocked_by_verified_edge_samples"))
                    else "replace_estimated_costs_with_recorded_fill_evidence"
                ),
            )
            entry_requirements = [
                str(row)
                for row in list(action.get("entry_quality_requirements") or [])
                if str(row).strip()
            ]
            for requirement in LANE_COST_EVIDENCE_REPAIR_ENTRY_REQUIREMENTS:
                if requirement not in entry_requirements:
                    entry_requirements.append(requirement)
            if _prompt_bool(action.get("scale_blocked_by_verified_edge_samples")):
                requirement = "build_recorded_cost_alpha_sample_count_before_pressing"
                if requirement not in entry_requirements:
                    entry_requirements.append(requirement)
            if _prompt_bool(action.get("scale_blocked_by_verified_edge_net_pnl")):
                requirement = (
                    "require_positive_recorded_cost_alpha_net_pnl_before_pressing"
                )
                if requirement not in entry_requirements:
                    entry_requirements.append(requirement)
            action["entry_quality_requirements"] = entry_requirements
            if grade in {"scale_candidate", "qualified"}:
                action["action"] = "cost_evidence_repair_waiting_probe"
                action["sizing_posture"] = "cost_evidence_repair_probe"
        if entry_quality_blocks_scale:
            add_lane("weak_lanes", str(lane))
            add_lane("entry_quality_weak_lanes", str(lane))
            remove_lane("scale_candidate_lanes", str(lane))
            action["requires_waiting_entry"] = True
            _add_scale_blocker(
                action,
                blocker="entry_quality_repair",
                repair_target="require_price_relief_regime_alignment_before_new_blocks",
            )
        if validation_evidence_blocks_scale:
            add_lane("weak_lanes", str(lane))
            add_lane("validation_evidence_weak_lanes", str(lane))
            remove_lane("scale_candidate_lanes", str(lane))
            action["requires_waiting_entry"] = True
            _add_scale_blocker(
                action,
                blocker="validation_evidence_repair",
                repair_target="collect_backtest_wfa_oos_live_shadow_evidence_before_scale_up",
            )
        if validation_repair_blocks_scale:
            action["requires_waiting_entry"] = True
            _add_scale_blocker(
                action,
                blocker="validation_repair_enforced",
                repair_target="clear_validation_repair_enforcement_before_scale_up",
            )
            if grade in {"scale_candidate", "qualified"}:
                action["action"] = "validation_repair_waiting_probe_until_clear"
                action["sizing_posture"] = "validation_repair_probe"
        action["applied_max_budget_multiplier"] = round(applied_max, 4)
        action["scale_up_allowed"] = bool(
            allow_scale_up
            and not shadow_blocks_scale
            and not exposure_blocks_scale
            and not remediation_blocks_scale
            and not validation_repair_blocks_scale
            and not cost_evidence_blocks_scale
            and not entry_quality_blocks_scale
            and not validation_evidence_blocks_scale
            and grade == "scale_candidate"
            and applied_max > 1.0
        )
        lane_actions[str(lane)] = action
    if lane_actions:
        synced["lane_actions"] = lane_actions
    if shadow_blocked_lanes:
        synced["shadow_blocked_lanes"] = shadow_blocked_lanes[:12]
    if exposure_blocked_lanes:
        synced["exposure_blocked_lanes"] = exposure_blocked_lanes[:12]
    if remediation_blocked_lanes:
        synced["remediation_blocked_lanes"] = remediation_blocked_lanes[:12]
    gate_status = _prompt_text(gate.get("status"), limit=80)
    if gate_status:
        synced["validation_gate_status"] = gate_status
    if (
        not allow_scale_up
        and synced.get("scale_candidate_lanes")
        and "scale_up_blocked_by_validation_gate" not in requirements
    ):
        requirements.append("scale_up_blocked_by_validation_gate")
    synced["block_design_requirements"] = requirements
    packet["lane_authority"] = synced


def _active_revision_blocks_scale_up(evidence: dict[str, Any]) -> bool:
    if not isinstance(evidence, dict) or not evidence:
        return False
    status = _prompt_text(evidence.get("status"), limit=100).strip().lower()
    if status in {
        "no_active_revision_samples",
        "no_active_revision_samples_with_proxy",
        "active_revision_samples_pending_close",
        "active_revision_samples_pending_close_with_proxy",
        "active_revision_sample_building",
        "insufficient_active_revision_samples",
    }:
        return True
    active_sample_count = _prompt_count(evidence.get("active_sample_count")) or 0
    effective_sample_count = _prompt_count(evidence.get("effective_sample_count")) or 0
    min_samples_to_scale = _prompt_count(evidence.get("min_samples_to_scale")) or 0
    scale_up_allowed = _prompt_bool(evidence.get("scale_up_allowed"))
    validation_sample_role = _prompt_text(
        evidence.get("validation_sample_role"),
        limit=100,
    ).strip().lower()
    if validation_sample_role == "legacy_proxy_metrics_no_scale":
        return True
    if min_samples_to_scale > 0 and max(active_sample_count, effective_sample_count) < min_samples_to_scale:
        return True
    return not scale_up_allowed


def active_revision_probe_budget_multiplier(evidence: dict[str, Any]) -> float:
    if not isinstance(evidence, dict) or not evidence:
        return 0.25
    status = _prompt_text(evidence.get("status"), limit=100).strip().lower()
    if status in {
        "active_revision_sample_building",
        "insufficient_active_revision_samples",
    }:
        return 0.5
    return 0.25


def apply_active_revision_evidence_gate(
    packet: dict[str, Any],
    evidence: dict[str, Any],
    *,
    probe_cap_multiplier: float | None = None,
) -> dict[str, Any]:
    if not isinstance(packet, dict) or not isinstance(evidence, dict) or not evidence:
        return packet
    packet["active_revision_evidence"] = evidence
    if not _active_revision_blocks_scale_up(evidence):
        return packet

    raw_cap_multiplier = (
        active_revision_probe_budget_multiplier(evidence)
        if probe_cap_multiplier is None
        else float(probe_cap_multiplier)
    )
    cap_multiplier = max(min(float(raw_cap_multiplier), 1.0), 0.0)
    packet["allow_scale_up"] = False
    if "max_budget_multiplier" in packet:
        current_multiplier = _prompt_float(packet.get("max_budget_multiplier"))
        packet["max_budget_multiplier"] = round(
            min(current_multiplier, cap_multiplier)
            if current_multiplier > 0
            else cap_multiplier,
            4,
        )
    active_gate = {
        "status": _prompt_text(evidence.get("status"), limit=100),
        "blocks_scale_up": True,
        "requires_waiting_entry": True,
        "cap_multiplier": round(cap_multiplier, 6),
        "active_sample_count": _prompt_count(evidence.get("active_sample_count")) or 0,
        "effective_sample_count": _prompt_count(evidence.get("effective_sample_count")) or 0,
        "legacy_proxy_sample_count": _prompt_count(
            evidence.get("legacy_proxy_sample_count")
        ) or 0,
        "pending_block_count": _prompt_count(evidence.get("pending_block_count")) or 0,
        "min_samples_to_scale": _prompt_count(evidence.get("min_samples_to_scale")) or 0,
        "entry_policy": "waiting_entry_probe_only_until_active_revision_samples_close",
        "scale_policy": "no_lane_scale_up_until_active_revision_min_samples_clear",
    }
    lane_authority = (
        packet.get("lane_authority")
        if isinstance(packet.get("lane_authority"), dict)
        else {}
    )
    if not lane_authority:
        return packet
    synced = dict(lane_authority)
    synced["global_scale_up_allowed"] = False
    synced["max_budget_multiplier"] = packet.get("max_budget_multiplier")
    synced["active_revision_gate"] = active_gate
    requirements = [
        str(row)
        for row in list(synced.get("block_design_requirements") or [])
        if str(row).strip()
    ]
    for requirement in (
        "active_revision_closed_samples_required_before_lane_scale_up",
        "use_waiting_entry_probe_until_active_revision_min_samples",
    ):
        if requirement not in requirements:
            requirements.append(requirement)

    lane_actions = (
        synced.get("lane_actions")
        if isinstance(synced.get("lane_actions"), dict)
        else {}
    )
    updated_actions: dict[str, dict[str, Any]] = {}
    for lane, raw_action in list(lane_actions.items()):
        if not isinstance(raw_action, dict):
            continue
        action = dict(raw_action)
        action["active_revision_gate"] = active_gate
        action["scale_up_blocked_by_active_revision"] = True
        action["scale_up_allowed"] = False
        action["requires_waiting_entry"] = True
        lane_max = _prompt_float(action.get("max_budget_multiplier"))
        applied_max = _prompt_float(action.get("applied_max_budget_multiplier"))
        action["applied_max_budget_multiplier"] = round(
            min(
                value
                for value in (lane_max, applied_max, cap_multiplier)
                if value > 0
            ),
            4,
        )
        passport = (
            dict(action.get("risk_budget_passport"))
            if isinstance(action.get("risk_budget_passport"), dict)
            else {}
        )
        if passport:
            base = _prompt_float(passport.get("effective_risk_budget_multiplier"))
            if base <= 0:
                base = _prompt_float(passport.get("applied_risk_budget_multiplier"))
            passport["active_revision_gate_status"] = active_gate["status"]
            passport["active_revision_cap_multiplier"] = round(cap_multiplier, 6)
            passport["effective_risk_budget_multiplier"] = round(
                min(base, cap_multiplier) if base > 0 else cap_multiplier,
                6,
            )
            action["risk_budget_passport"] = passport
        _add_scale_blocker(
            action,
            blocker="active_revision_gate",
            repair_target="close_active_revision_samples_before_scale_up",
        )
        entry_requirements = [
            str(row)
            for row in list(action.get("entry_quality_requirements") or [])
            if str(row).strip()
        ]
        for requirement in (
            "active_revision_closed_samples_required_before_scale_up",
            "prefer_waiting_entry_until_active_revision_samples_close",
        ):
            if requirement not in entry_requirements:
                entry_requirements.append(requirement)
        action["entry_quality_requirements"] = entry_requirements
        grade = _prompt_text(action.get("grade"), limit=80).strip().lower()
        if grade in {"scale_candidate", "qualified"}:
            action["action"] = "active_revision_probe_until_samples_close"
            action["sizing_posture"] = "active_revision_probe"
        updated_actions[str(lane)] = action
    if updated_actions:
        synced["lane_actions"] = updated_actions
    synced["block_design_requirements"] = requirements
    packet["lane_authority"] = synced
    return packet


def build_authority_packet(
    *,
    venue: str,
    scorecards: list[dict[str, Any]],
    config: LiveAuthorityConfig,
) -> dict[str, Any]:
    grade = _lowest_effective_grade(scorecards)
    multipliers = [
        float(row.get("authority_multiplier") or 1.0)
        for row in scorecards
    ]
    if not multipliers:
        multiplier = float(config.observe_only_multiplier)
    elif grade in {"restricted", "observe_only"}:
        multiplier = min(multipliers)
    elif grade == "scale_candidate":
        ready_multipliers = [
            float(row.get("authority_multiplier") or 1.0)
            for row in scorecards
            if int(row.get("sample_count") or 0) >= int(config.min_samples_to_scale)
            and _lane_performance_is_scale_ready(row)
        ]
        if ready_multipliers:
            multiplier = min(max(ready_multipliers), float(config.max_scale_multiplier))
        else:
            multiplier = min(max(multipliers), 1.0)
    else:
        multiplier = min(max(multipliers), 1.0)

    max_budget_multiplier = round(
        float(config.base_budget_multiplier) * float(multiplier),
        4,
    )
    normalized_grades = [
        _normalize_authority_grade(row.get("grade"))
        for row in scorecards
    ]
    has_restricted_lane = any(
        item in {"restricted", "observe_only"}
        for item in normalized_grades
    )
    allow_scale_up = (
        grade == "scale_candidate"
        and multiplier > 1.0
        and not has_restricted_lane
    )
    return {
        "status": "ok",
        "venue": venue,
        "live_grade": grade,
        "max_budget_multiplier": max_budget_multiplier,
        "allow_scale_up": allow_scale_up,
        "scorecard_count": len(scorecards),
        "scorecards": scorecards,
        "lane_authority": _lane_authority_from_scorecards(
            scorecards,
            allow_scale_up=allow_scale_up,
            max_budget_multiplier=max_budget_multiplier,
            min_samples_to_scale=int(config.min_samples_to_scale),
        ),
    }


def apply_trading_validation_gate(
    packet: dict[str, Any],
    validation: dict[str, Any],
    *,
    config: LiveAuthorityConfig,
) -> dict[str, Any]:
    payload = _validation_payload(validation)
    summary = (
        validation.get("summary")
        if isinstance(validation.get("summary"), dict)
        else payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else {}
    )
    fail_count = int(summary.get("fail_count") or 0)
    hard_fail_count = _validation_hard_fail_count(summary)
    hard_blocking_count = _validation_hard_blocking_count(summary)
    readiness = str(summary.get("readiness") or "")
    if (
        readiness in {"", "normal", "scale_ready"}
        and fail_count > 0
        and hard_blocking_count <= 0
    ):
        readiness = "probe"
    validation_status = str(validation.get("status") or "")
    original_multiplier = float(packet.get("max_budget_multiplier") or 0.0)
    disciplines = _validation_disciplines(validation)
    discipline_count = _validation_effective_discipline_count(
        validation,
        summary=summary,
        disciplines=disciplines,
    )
    gate_status = "clear"
    reason = ""
    cap_multiplier = original_multiplier
    risk_governor = _validation_risk_governor(validation)

    if validation.get("stale") is True:
        gate_status = "validation_stale"
        reason = str(validation.get("stale_reason") or "trading_validation_stale")
        cap_multiplier = min(original_multiplier, float(config.observe_only_multiplier))
    elif validation_status == "error":
        gate_status = "validation_error"
        reason = str(validation.get("error_message") or "trading_validation_error")
        cap_multiplier = min(original_multiplier, float(config.observe_only_multiplier))
    elif readiness == "blocked_by_validation" or hard_blocking_count > 0:
        gate_status = "blocked_by_validation"
        reason = (
            f"readiness={readiness or 'unknown'}, "
            f"hard_fail_count={hard_fail_count}, "
            f"hard_blocking_count={hard_blocking_count}, "
            f"fail_count={fail_count}"
        )
        cap_multiplier = min(original_multiplier, float(config.observe_only_multiplier))
    elif (
        readiness == "scale_ready"
        and discipline_count != EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT
    ):
        gate_status = "validation_incomplete"
        reason = (
            f"discipline_count={discipline_count},"
            f"expected={EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT}"
        )
        cap_multiplier = min(original_multiplier, float(config.observe_only_multiplier))
    elif readiness == "research_only":
        gate_status = "validation_research_only"
        reason = "validation_readiness_research_only"
        cap_multiplier = min(original_multiplier, float(config.observe_only_multiplier))
    elif readiness in {"probe", "normal"}:
        gate_status = f"validation_{readiness}"
        reason = f"validation_readiness_{readiness}_not_scale_ready"
        cap_multiplier = min(original_multiplier, 1.0)
    elif not readiness:
        gate_status = "validation_missing"
        reason = "no_trading_validation_readiness"
        cap_multiplier = min(original_multiplier, float(config.observe_only_multiplier))
    if risk_governor:
        governor_cap = risk_governor.get("cap_multiplier")
        if governor_cap is None:
            governor_cap = cap_multiplier
        cap_multiplier = min(
            cap_multiplier,
            float(governor_cap),
        )

    packet["trading_validation"] = validation
    packet["validation_gate"] = {
        "status": gate_status,
        "reason": reason,
        "readiness": readiness,
        "fail_count": fail_count,
        "hard_fail_count": hard_fail_count,
        "hard_blocking_count": hard_blocking_count,
        "hard_missing_count": int(summary.get("hard_missing_count") or 0),
        "core_fail_count": int(summary.get("core_fail_count") or 0),
        "core_missing_count": int(summary.get("core_missing_count") or 0),
        "discipline_count": discipline_count,
        "expected_discipline_count": EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
        "original_max_budget_multiplier": round(original_multiplier, 4),
        "applied_max_budget_multiplier": round(cap_multiplier, 4),
    }
    validation_matrix = _compact_prompt_validation_matrix(
        validation,
        gate=packet["validation_gate"],
    )
    if validation_matrix:
        packet["validation_gate"]["discipline_matrix"] = validation_matrix
    if risk_governor:
        packet["validation_gate"]["risk_governor_action"] = risk_governor.get("action")
        packet["validation_gate"]["risk_governor_source"] = risk_governor.get("source")
        packet["validation_gate"]["risk_governor_reasons"] = list(
            risk_governor.get("reasons") or []
        )
        if isinstance(risk_governor.get("metrics"), dict):
            packet["validation_gate"]["risk_governor_metrics"] = dict(
                risk_governor.get("metrics") or {}
            )
    validation_passport = _compact_prompt_validation_passport(
        validation,
        gate=packet["validation_gate"],
    )
    if validation_passport:
        packet["validation_gate"]["validation_passport"] = validation_passport
    weak_disciplines = [
        row for row in disciplines if str(row.get("status") or "") != "pass"
    ][:6]
    failed_disciplines = [
        row for row in disciplines if str(row.get("status") or "") == "fail"
    ][:6]
    if failed_disciplines:
        packet["validation_gate"]["failed_disciplines"] = failed_disciplines
    if weak_disciplines:
        packet["validation_gate"]["weak_disciplines"] = weak_disciplines
    capacity_bottleneck = _validation_capacity_bottleneck(validation)
    if capacity_bottleneck:
        packet["validation_gate"]["capacity_bottleneck"] = capacity_bottleneck
    failure_attribution = _validation_failure_attribution(validation)
    if failure_attribution:
        packet["validation_gate"]["failure_attribution"] = failure_attribution
    lane_scorecards = _validation_lane_scorecards(validation)
    if lane_scorecards:
        packet["validation_gate"]["lane_scorecards"] = lane_scorecards
    loss_cooldown = _validation_loss_cooldown(validation)
    if loss_cooldown:
        packet["validation_gate"]["loss_cooldown"] = loss_cooldown
    cost_attribution = _validation_cost_attribution(validation)
    if cost_attribution:
        packet["validation_gate"]["cost_attribution"] = cost_attribution
    recovery_focus = _validation_recovery_focus(validation)
    if recovery_focus:
        packet["validation_gate"]["validation_recovery_focus"] = recovery_focus
    operator_guidance = _validation_operator_guidance(validation)
    if operator_guidance:
        packet["validation_gate"]["operator_guidance"] = operator_guidance
    remediation_plan = _validation_remediation_plan(validation)
    if remediation_plan:
        packet["validation_gate"]["remediation_plan"] = remediation_plan
    validation_pressure = _compact_prompt_validation_pressure(
        validation,
        gate=packet["validation_gate"],
        allow_scale_up=packet.get("allow_scale_up"),
    )
    if validation_pressure:
        packet["validation_gate"]["validation_pressure"] = validation_pressure
        pressure_severity = str(
            validation_pressure.get("severity") or ""
        ).strip()
        if pressure_severity in {"remediation_risk_off", "risk_off"}:
            cap_multiplier = min(cap_multiplier, 0.25)
        elif pressure_severity in {
            "remediation_waiting_probe",
            "diagnostic_de_risk",
        }:
            cap_multiplier = min(
                cap_multiplier,
                float(config.observe_only_multiplier),
            )
        elif pressure_severity in {"validation_caution", "de_risk", "reduced"}:
            cap_multiplier = min(cap_multiplier, 1.0)
        packet["validation_gate"]["applied_max_budget_multiplier"] = round(
            cap_multiplier,
            4,
        )
    pressure_limits_risk = (
        bool(validation_pressure)
        and str(validation_pressure.get("severity") or "").strip()
        not in {"", "clear"}
    )
    if gate_status != "clear" or risk_governor or pressure_limits_risk:
        packet["max_budget_multiplier"] = round(cap_multiplier, 4)
        packet["allow_scale_up"] = False
    _sync_lane_authority_with_gate(packet)
    return packet
