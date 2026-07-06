from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_live_authority import (
    active_revision_waiting_entry_reason,
    build_lane_authority_action,
    candidate_lanes_for_row,
    live_authority_budget_zero,
    live_authority_new_block_qty_cap,
    live_authority_waiting_entry_required,
    match_lane_authority_for_row,
    performance_lane_action,
)


def test_candidate_lanes_include_horizon_setup_and_kis_scoped_tokens() -> None:
    lanes = candidate_lanes_for_row(
        {
            "horizon": "mid",
            "strategy_family": "value_pullback",
            "entry_setup": "near_support",
            "metadata": {"lane": "earnings_reversal"},
        }
    )

    assert "mid" in lanes
    assert "kis:mid" in lanes
    assert "value_pullback" in lanes
    assert "mid:value_pullback" in lanes
    assert "kis:mid:value_pullback" in lanes
    assert "mid:near_support" in lanes
    assert "mid:earnings_reversal" in lanes


def test_candidate_lanes_include_validation_repair_discipline_tokens() -> None:
    lanes = candidate_lanes_for_row(
        {
            "horizon": "mid",
            "validation_repair": {
                "discipline_id": "cost_simulation",
                "dimension": "walk_forward_analysis",
            },
        }
    )

    assert "mid:validation:cost_simulation" in lanes
    assert "kis:mid:validation:cost_simulation" in lanes
    assert "mid:validation:walk_forward_analysis" in lanes
    assert "kis:mid:validation:walk_forward_analysis" in lanes


def test_candidate_lanes_include_period_memory_repair_quality_tokens() -> None:
    lanes = candidate_lanes_for_row(
        {
            "horizon": "mid",
            "validation_repair": {
                "period_memory_repair_qualities": ["successful_repair"],
                "block_design_constraints": [
                    {"period_memory_repair_quality": "repair_required"}
                ],
            },
        }
    )

    assert "mid:period_memory:successful_repair" in lanes
    assert "kis:mid:period_memory:successful_repair" in lanes
    assert "mid:period_memory:repair_required" in lanes
    assert "kis:mid:period_memory:repair_required" in lanes


def test_candidate_lanes_reads_metadata_validation_repair_when_row_lacks_it() -> None:
    lanes = candidate_lanes_for_row(
        {
            "horizon": "long",
            "metadata": {
                "validation_repair": {
                    "discipline_id": "capacity_analysis",
                }
            },
        }
    )

    assert "long:validation:capacity_analysis" in lanes
    assert "kis:long:validation:capacity_analysis" in lanes


def test_match_lane_authority_prefers_weak_lane_and_reports_source() -> None:
    match = match_lane_authority_for_row(
        {
            "cost_evidence_weak_lanes": ["mid:value_pullback"],
            "scale_candidate_lanes": ["mid"],
            "lane_actions": {
                "mid:value_pullback": {
                    "grade": "restricted",
                    "action": "cost_evidence_repair_waiting_probe",
                }
            },
        },
        {"horizon": "mid", "strategy_family": "value_pullback"},
    )

    assert match["matched_lane"] == "mid:value_pullback"
    assert match["matched_weak"] == ["mid:value_pullback"]
    assert match["matched_scale_candidate"] == ["mid"]
    assert match["weak_lane_sources"] == ["cost_evidence_weak_lanes"]
    assert match["lane_detail"]["action"] == "cost_evidence_repair_waiting_probe"


def test_match_lane_authority_uses_horizon_detail_when_specific_action_missing() -> None:
    match = match_lane_authority_for_row(
        {
            "qualified_lanes": ["mid:value_pullback"],
            "lane_actions": {
                "mid": {
                    "grade": "qualified",
                    "action": "normal_or_selective_press",
                }
            },
        },
        {"horizon": "mid", "strategy_family": "value_pullback"},
    )

    assert match["matched_lane"] == "mid:value_pullback"
    assert match["matched_qualified"] == ["mid:value_pullback"]
    assert match["lane_detail"]["action"] == "normal_or_selective_press"


def test_match_lane_authority_returns_empty_when_no_lane_matches() -> None:
    assert match_lane_authority_for_row(
        {"weak_lanes": ["short:late_chase"]},
        {"horizon": "mid", "strategy_family": "value_pullback"},
    ) == {}


def test_build_lane_authority_action_caps_weak_qty_by_risk_passport() -> None:
    lane_authority = {
        "weak_lanes": ["mid"],
        "lane_actions": {
            "mid": {
                "grade": "restricted",
                "action": "de_risk_or_waiting_entry",
                "max_budget_multiplier": 0.5,
                "applied_max_budget_multiplier": 0.4,
                "risk_budget_passport": {
                    "effective_risk_budget_multiplier": 0.25,
                    "budget_status": "reduced",
                    "reasons": ["weak live expectancy"],
                    "risk_of_ruin_pct": 4.2,
                },
                "scale_blockers": ["cost_evidence_repair"],
            }
        },
    }
    row = {"horizon": "mid", "qty": 8}
    lane_match = match_lane_authority_for_row(lane_authority, row)

    action = build_lane_authority_action(row=row, lane_match=lane_match)

    assert action["matched_lane"] == "mid"
    assert action["grade"] == "restricted"
    assert action["action"] == "de_risk_or_waiting_entry"
    assert action["budget_multiplier"] == 0.25
    assert action["budget_multiplier_source"] == "risk_budget_passport"
    assert action["qty_cap"] == 2
    assert action["qty_cap_source"] == "risk_budget_passport"
    assert action["requires_waiting_entry"] is True
    assert action["scale_blocked_by_cost_precision"] is True
    assert action["scale_blocked_by_cost_evidence"] is True
    assert action["risk_budget_passport_status"] == "reduced"
    assert action["risk_budget_passport_reasons"] == ["weak live expectancy"]
    assert action["risk_of_ruin_pct"] == 4.2


def test_build_lane_authority_action_marks_scale_candidate_qty_multiplier() -> None:
    lane_authority = {
        "scale_candidate_lanes": ["mid"],
        "lane_actions": {
            "mid": {
                "scale_up_allowed": True,
                "max_budget_multiplier": 1.5,
            }
        },
    }
    row = {"horizon": "mid", "qty": 2}
    lane_match = match_lane_authority_for_row(lane_authority, row)

    action = build_lane_authority_action(row=row, lane_match=lane_match)

    assert action["matched_lane"] == "mid"
    assert action["grade"] == "scale_candidate"
    assert action["action"] == "eligible_to_press_when_validation_clear"
    assert action["scale_up_allowed"] is True
    assert action["qty_cap"] == 2
    assert action["qty_scale_multiplier"] == 1.5


def test_active_revision_waiting_entry_reason_blocks_unscaled_active_revision() -> None:
    reason = active_revision_waiting_entry_reason(
        {
            "active_revision_evidence": {
                "status": "active_revision_samples_pending_close_with_proxy",
                "effective_sample_count": 2,
                "min_samples_to_scale": 10,
                "scale_up_allowed": False,
            }
        }
    )

    assert reason == (
        "active_revision_evidence:"
        "active_revision_samples_pending_close_with_proxy"
    )


def test_live_authority_waiting_entry_required_prioritizes_active_revision() -> None:
    reason = live_authority_waiting_entry_required(
        {
            "status": "ok",
            "active_revision_evidence": {
                "status": "insufficient_active_revision_samples",
            },
            "validation_gate": {
                "status": "clear",
                "validation_pressure": {
                    "entry_posture": "patient_waiting_entry",
                },
            },
        }
    )

    assert reason == "active_revision_evidence:insufficient_active_revision_samples"


def test_live_authority_waiting_entry_required_reads_shadow_gate_without_lane_match() -> None:
    reason = live_authority_waiting_entry_required(
        {
            "status": "ok",
            "validation_gate": {"status": "clear"},
            "lane_authority": {
                "validation_shadow_gate": {
                    "status": "revalidation_required_before_scale_up",
                    "requires_waiting_entry": True,
                }
            },
        }
    )

    assert reason == "validation_shadow_gate:revalidation_required_before_scale_up"


def test_live_authority_new_block_qty_cap_does_not_force_one_share_probe() -> None:
    cap = live_authority_new_block_qty_cap(
        {
            "status": "ok",
            "validation_gate": {
                "status": "validation_normal",
                "validation_pressure": {
                    "severity": "remediation_waiting_probe",
                    "sizing_posture": "reduced_probe_only",
                },
            },
        }
    )

    assert cap is None


def test_lane_authority_preserves_small_waiting_probe_value() -> None:
    action = build_lane_authority_action(
        row={
            "qty": 10,
            "horizon": "mid",
            "entry_style": "wait_for_price",
            "target_block_value_krw": 33_800,
        },
        lane_match={
            "matched_lane": "mid",
            "matched_insufficient": ["mid"],
            "lane_detail": {
                "grade": "insufficient",
                "action": "validation_evidence_repair_waiting_probe",
                "applied_max_budget_multiplier": 0.1,
                "max_budget_multiplier": 0.75,
            },
        },
    )

    assert action["qty_cap"] == 10
    assert action["qty_cap_source"].endswith("small_waiting_probe_value_preserved")


def test_live_authority_budget_zero_detects_zero_multiplier_only_when_present() -> None:
    assert live_authority_budget_zero({"max_budget_multiplier": 0.0}) is True
    assert live_authority_budget_zero({"max_budget_multiplier": 1.0}) is False
    assert live_authority_budget_zero({}) is False


def test_performance_lane_action_allows_verified_scale_candidate() -> None:
    action = performance_lane_action(
        live_authority={
            "allow_scale_up": True,
            "max_budget_multiplier": 2.0,
            "performance_lanes": [
                {
                    "venue": "kis",
                    "lane": "mid",
                    "quality_hint": "scale_candidate",
                    "risk_budget_multiplier": 1.4,
                    "profit_factor": 1.8,
                    "max_drawdown_pct": -4.2,
                    "recovery_factor": 1.7,
                    "risk_of_ruin_pct": 4.2,
                    "lane_confidence_score": 0.82,
                    "recommended_risk_fraction": 0.015,
                }
            ],
            "validation_gate": {"status": "clear"},
        },
        candidate_lanes={"mid"},
        row={
            "horizon": "mid",
            "qty": 4,
            "entry_style": "wait_for_price",
            "price_location": "near_support",
            "valuation_label": "undervalued",
            "regime_alignment": "risk_on",
            "supply_recovery": "foreign flow recovery",
        },
    )

    assert action["source"] == "performance_lanes"
    assert action["matched_lane"] == "mid"
    assert action["grade"] == "scale_candidate"
    assert action["scale_up_allowed"] is True
    assert action["budget_multiplier"] == 1.25
    assert action["qty_cap"] == 4
    assert action["qty_scale_multiplier"] == 1.25
    assert action["risk_profile_allows_scale"] is True
    assert action["scale_entry_quality"]["scale_up_allowed"] is True


def test_performance_lane_action_forces_weak_lane_waiting_probe() -> None:
    action = performance_lane_action(
        live_authority={
            "performance_lanes": [
                {
                    "venue": "kis",
                    "lane": "mid",
                    "quality_hint": "weak_review",
                    "action_hint": "observe_or_waiting_entry",
                }
            ],
        },
        candidate_lanes={"mid"},
        row={"horizon": "mid", "qty": 8},
    )

    assert action["source"] == "performance_lanes"
    assert action["grade"] == "observe_only"
    assert action["requires_waiting_entry"] is True
    assert action["budget_multiplier"] == 0.25
    assert action["qty_cap"] == 2


def test_kis_block_trader_does_not_reown_performance_lane_action() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text(
        encoding="utf-8"
    )

    assert "def _live_authority_performance_lane_action(" not in source
