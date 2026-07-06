from __future__ import annotations

from tradecraft.services.live_authority import (
    LiveAuthorityConfig,
    apply_active_revision_evidence_gate,
    apply_trading_validation_gate,
    build_authority_packet,
    compact_live_authority_for_prompt,
    compact_live_authority_for_status,
)


def test_restricted_grade_caps_budget_even_if_llm_is_confident() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_momentum",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 20,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert packet["venue"] == "binance"
    assert packet["live_grade"] == "restricted"
    assert packet["max_budget_multiplier"] == 0.5
    assert packet["allow_scale_up"] is False


def test_mixed_lane_authority_does_not_let_one_restricted_lane_cap_global_probe_budget() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot:long:short",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 32,
            },
            {
                "strategy_family": "futures:long",
                "grade": "insufficient",
                "authority_multiplier": 0.75,
                "sample_count": 6,
            },
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert packet["live_grade"] == "insufficient"
    assert packet["max_budget_multiplier"] == 0.75
    assert packet["allow_scale_up"] is False
    assert packet["lane_authority"]["lane_actions"]["spot:long:short"][
        "grade"
    ] == "restricted"
    assert packet["lane_authority"]["lane_actions"]["futures:long"][
        "grade"
    ] == "insufficient"


def test_compact_live_authority_for_status_keeps_summary_without_lane_bloat() -> None:
    payload = {
        "status": "ok",
        "live_grade": "probe",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.25,
        "scorecard_count": 12,
        "lane_authority": {
            "version": "lane_authority_v1",
            "global_scale_up_allowed": False,
            "max_budget_multiplier": 0.25,
            "validation_gate_status": "probe",
            "weak_lanes": ["short", "mid"],
            "insufficient_lanes": ["long"],
            "shadow_blocked_lanes": ["spot:long"],
            "exposure_blocked_lanes": ["futures:long"],
            "remediation_blocked_lanes": ["volatile_attack"],
            "lane_actions": {
                f"lane-{idx}": {"raw": "x" * 10_000}
                for idx in range(50)
            },
            "block_design_requirements": {"huge": "y" * 80_000},
        },
        "validation_gate": {
            "status": "warn",
            "readiness": "probe",
            "reason": "z" * 1_000,
            "fail_count": 2,
            "discipline_count": 17,
            "expected_discipline_count": 19,
            "risk_governor_action": "probe_only",
            "risk_governor_reasons": ["r" * 500] * 20,
        },
        "performance_lanes": [
            {"lane": f"lane-{idx}", "quality_hint": "q" * 500}
            for idx in range(30)
        ],
    }

    compact = compact_live_authority_for_status(payload)

    assert compact["live_grade"] == "probe"
    assert compact["lane_authority"]["weak_lane_count"] == 2
    assert compact["lane_authority"]["blocked_lane_count"] == 3
    assert compact["lane_authority"]["scale_blocked_lane_count"] == 3
    assert compact["lane_authority"]["probe_lane_count"] == 1
    assert compact["lane_authority"]["probe_lane_names"] == ["long"]
    assert compact["lane_authority"]["execution_posture"] == (
        "probe_allowed_scale_blocked"
    )
    assert compact["lane_authority"]["lane_action_count"] == 50
    assert compact["validation_gate"]["reason"] == "z" * 220
    assert len(compact["validation_gate"]["risk_governor_reasons"]) == 3
    assert len(compact["performance_lanes"]) == 6
    assert "block_design_requirements" not in compact["lane_authority"]
    assert len(str(compact)) < 8_000


def test_compact_live_authority_for_prompt_caps_validation_lane_bloat() -> None:
    payload = {
        "status": "ok",
        "live_grade": "insufficient",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.25,
        "scorecard_count": 7,
        "lane_authority": {
            "version": "lane_authority_v1",
            "global_scale_up_allowed": False,
            "max_budget_multiplier": 0.25,
            "validation_gate_status": "validation_probe",
            "weak_lanes": ["mid", "long"],
            "insufficient_lanes": ["short"],
            "lane_actions": {
                f"lane-{idx}": {
                    "grade": "insufficient",
                    "action": "small_probe_until_sample_builds",
                    "sample_count": idx,
                    "scale_up_allowed": False,
                    "scale_decision": "probe_only",
                    "scale_blockers": [f"blocker-{j}" for j in range(20)],
                    "scale_repair_targets": [f"repair-{j}" for j in range(20)],
                    "performance_missing_metrics": ["profit_factor"] * 12,
                    "performance_weak_metrics": ["expectancy"] * 12,
                    "validation_evidence_pass_criteria": ["c" * 500] * 12,
                    "validation_evidence_verification_artifacts": ["a" * 500] * 12,
                    "risk_budget_passport": {
                        "raw_kelly_fraction": 0.12,
                        "applied_risk_budget_multiplier": 0.25,
                        "cost_repair_targets": ["x" * 300] * 30,
                        "validation_evidence_repair_targets": ["y" * 300] * 30,
                    },
                    "active_revision_gate": {
                        "status": "active_revision_sample_building",
                        "focus_reasons": ["z" * 300] * 20,
                    },
                }
                for idx in range(40)
            },
            "block_design_requirements": ["r" * 500] * 30,
        },
        "validation_gate": {
            "status": "validation_probe",
            "reason": "z" * 5_000,
            "failed_disciplines": [{"id": f"d{idx}", "action": "a" * 500} for idx in range(30)],
            "cost_attribution": {
                "rows": [{"block_id": f"b{idx}", "note": "n" * 500} for idx in range(60)]
            },
        },
        "performance_lanes": [
            {"lane": f"lane-{idx}", "quality_hint": "q" * 500}
            for idx in range(40)
        ],
    }

    compact = compact_live_authority_for_prompt(payload)

    assert compact["lane_authority"]["execution_posture"] in {
        "probe_allowed_scale_blocked",
        "probe_allowed_sample_building",
    }
    assert compact["lane_authority"]["lane_actions"]
    first_action = next(iter(compact["lane_authority"]["lane_actions"].values()))
    assert "validation_evidence_verification_artifacts" not in first_action
    assert "validation_evidence_verification_artifacts" not in first_action.get(
        "risk_budget_passport",
        {},
    )
    assert len(str(compact)) < 30_000


def test_compact_live_authority_for_prompt_separates_probe_from_scale_block() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "probe",
            "allow_scale_up": False,
            "lane_authority": {
                "version": "lane_authority_v1",
                "global_scale_up_allowed": False,
                "insufficient_lanes": ["long"],
                "shadow_blocked_lanes": ["mid"],
                "lane_actions": {
                    "long": {
                        "grade": "insufficient",
                        "action": "small_probe_until_sample_builds",
                        "requires_waiting_entry": True,
                        "scale_up_allowed": False,
                    },
                    "mid": {
                        "grade": "qualified",
                        "action": "shadow_or_waiting_entry_until_validation_rebuilt",
                        "requires_waiting_entry": True,
                        "scale_up_allowed": False,
                        "scale_up_blocked_by_shadow_gate": True,
                    },
                },
            },
        }
    )

    lane_authority = compact["lane_authority"]
    assert lane_authority["probe_lane_count"] == 2
    assert lane_authority["probe_lane_names"] == ["long", "mid"]
    assert lane_authority["scale_blocked_lane_count"] == 1
    assert lane_authority["execution_posture"] == "probe_allowed_scale_blocked"
    assert lane_authority["probe_policy"] == (
        "scale-up is blocked, but small waiting-entry/probe blocks are allowed "
        "when price structure and safety gates agree"
    )


def test_compact_live_authority_for_status_keeps_failure_and_cost_drivers() -> None:
    payload = {
        "status": "ok",
        "live_grade": "restricted",
        "validation_gate": {
            "status": "validation_probe",
            "readiness": "probe",
            "failure_attribution": {
                "recovery_focus": [
                    "symbol=NIGHTUSDT net -0.35, PF 0.00, expectancy -1.76%",
                ],
                "worst_groups": [
                    {
                        "group_type": "symbol",
                        "group": "NIGHTUSDT",
                        "total_net_pnl": -0.351409,
                        "profit_factor": 0.0,
                        "risk_score": 47.58937,
                    }
                ],
            },
            "cost_attribution": {
                "worst_cost_groups": [
                    {
                        "group_type": "strategy_family",
                        "group": "futures",
                        "sample_count": 19,
                        "total_cost": 0.386691,
                        "cost_drag_pct_of_abs_gross_pnl": 426.221741,
                    }
                ],
            },
        },
    }

    compact = compact_live_authority_for_status(payload)

    gate = compact["validation_gate"]
    assert gate["failure_attribution"]["recovery_focus"][0].startswith(
        "symbol=NIGHTUSDT"
    )
    assert gate["failure_attribution"]["worst_groups"][0]["group"] == "NIGHTUSDT"
    assert gate["cost_attribution"]["groups"][0]["group"] == "futures"
    assert len(str(compact)) < 8_000


def test_weak_grade_de_risks_like_restricted_lane() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "mid",
                "grade": "weak",
                "authority_multiplier": 0.5,
                "sample_count": 16,
                "expectancy_pct": 0.12,
                "win_rate_pct": 49.0,
                "profit_factor": 0.92,
                "recovery_factor": 0.32,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = packet["lane_authority"]["lane_actions"]["mid"]

    assert packet["live_grade"] == "restricted"
    assert packet["max_budget_multiplier"] == 0.5
    assert packet["allow_scale_up"] is False
    assert packet["lane_authority"]["weak_lanes"] == ["mid"]
    assert action["grade"] == "restricted"
    assert action["action"] == "de_risk_or_waiting_entry"
    assert action["requires_waiting_entry"] is True
    assert action["risk_budget_passport"]["applied_risk_budget_multiplier"] <= 0.5


def test_trading_validation_lane_scorecard_caps_live_lane_authority() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 24,
                "expectancy_pct": 0.62,
                "win_rate_pct": 56.0,
                "profit_factor": 1.8,
                "recovery_factor": 1.2,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
            },
            "payload": {
                "metrics": {
                    "lane_scorecards": {
                        "version": "lane_scorecards_v1",
                        "status": "warn",
                        "weak_lanes": ["spot:long"],
                        "lane_actions": {
                            "spot:long": {
                                "grade": "weak",
                                "action": "entry_quality_repair_before_scale",
                                "sample_count": 12,
                                "expectancy_pct": 0.08,
                                "profit_factor": 0.94,
                                "recovery_factor": 0.25,
                                "risk_of_ruin_pct": 14.0,
                                "max_budget_multiplier": 0.25,
                                "requires_waiting_entry": True,
                                "validation_repair_enforced_count": 2,
                                "validation_repair_scale_up_blocked_count": 2,
                                "validation_repair_waiting_entry_count": 2,
                                "validation_repair_avg_budget_multiplier": 0.25,
                                "validation_repair_action_counts": {
                                    "validation_repair.cost_evidence_repair.cost_simulation": 2,
                                },
                            }
                        },
                    }
                }
            },
        },
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = gated["lane_authority"]["lane_actions"]["spot:long"]

    assert gated["lane_authority"]["weak_lanes"] == ["spot:long"]
    assert action["requires_waiting_entry"] is True
    assert action["max_budget_multiplier"] == 0.25
    assert action["applied_max_budget_multiplier"] == 0.25
    assert action["scale_up_allowed"] is False
    assert action["validation_lane_scorecard_action"]["grade"] == "restricted"
    assert action["scale_blocked_by_validation_repair"] is True
    assert action["validation_repair_enforced_count"] == 2
    assert action["risk_budget_passport"]["validation_repair_cap_multiplier"] == 0.25
    assert "respect_trading_validation_lane_scorecard_before_entry" in action[
        "entry_quality_requirements"
    ]
    assert "respect_validation_repair_enforcement_until_repair_passes" in action[
        "entry_quality_requirements"
    ]


def test_trading_validation_lane_scorecards_canonicalize_binance_duplicate_lanes() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "lane": "spot:long:short",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 12,
            },
            {
                "lane": "futures:long",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 12,
            },
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
            },
            "payload": {
                "metrics": {
                    "lane_scorecards": {
                        "version": "lane_scorecards_v1",
                        "status": "warn",
                        "weak_lanes": [
                            "spot:long:short:short",
                            "futures:long:futures",
                        ],
                        "lane_actions": {
                            "spot:long:short:short": {
                                "grade": "weak",
                                "action": "cost_evidence_repair_waiting_entry",
                                "sample_count": 31,
                                "max_budget_multiplier": 0.25,
                                "requires_waiting_entry": True,
                            },
                            "futures:long:futures": {
                                "grade": "weak",
                                "action": "cost_evidence_repair_waiting_entry",
                                "sample_count": 6,
                                "max_budget_multiplier": 0.25,
                                "requires_waiting_entry": True,
                            },
                        },
                    }
                }
            },
        },
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = gated["lane_authority"]

    assert "spot:long:short" in lane_authority["lane_actions"]
    assert "futures:long" in lane_authority["lane_actions"]
    assert "spot:long:short:short" not in lane_authority["lane_actions"]
    assert "futures:long:futures" not in lane_authority["lane_actions"]
    assert "spot:long:short" in lane_authority["weak_lanes"]
    assert "futures:long" in lane_authority["weak_lanes"]
    assert "spot:long:short:short" not in lane_authority["weak_lanes"]
    assert "futures:long:futures" not in lane_authority["weak_lanes"]


def test_trading_validation_verified_cost_alpha_gap_blocks_scale_candidate() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "evidence_key": "pullback_reclaim",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 18,
                "expectancy_pct": 0.78,
                "win_rate_pct": 58.0,
                "max_drawdown_pct": -2.0,
                "profit_factor": 1.9,
                "recovery_factor": 1.4,
                "risk_of_ruin_pct": 3.5,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
            },
            "payload": {
                "metrics": {
                    "lane_scorecards": {
                        "version": "lane_scorecards_v1",
                        "status": "warn",
                        "cost_evidence_weak_lanes": ["futures:long:pullback_reclaim"],
                        "lane_actions": {
                            "futures:long:pullback_reclaim": {
                                "grade": "scale_candidate",
                                "action": "eligible_to_press_when_validation_clear",
                                "sample_count": 18,
                                "cost_precision_verified_rate": 61.0,
                                "cost_verified_alpha_count": 6,
                                "cost_unverified_alpha_count": 12,
                                "cost_verified_alpha_net_pnl": 1.25,
                                "cost_unverified_alpha_net_pnl": 3.75,
                                "scale_blocked_by_verified_edge_samples": True,
                                "scale_blocked_by_cost_evidence": True,
                            }
                        },
                    }
                }
            },
        },
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = gated["lane_authority"]
    lane = "futures:long:pullback_reclaim"
    action = lane_authority["lane_actions"][lane]
    passport = action["risk_budget_passport"]

    assert lane in lane_authority["cost_evidence_weak_lanes"]
    assert lane in lane_authority["weak_lanes"]
    assert lane not in lane_authority["scale_candidate_lanes"]
    assert action["scale_up_allowed"] is False
    assert action["requires_waiting_entry"] is True
    assert action["action"] == "cost_evidence_repair_waiting_probe"
    assert action["cost_verified_alpha_count"] == 6
    assert action["cost_unverified_alpha_count"] == 12
    assert action["scale_blocked_by_verified_edge_samples"] is True
    assert action["scale_blockers"] == ["verified_edge_sample_cap"]
    assert passport["cost_verified_alpha_count"] == 6
    assert passport["cost_unverified_alpha_count"] == 12
    assert passport["scale_blocked_by_verified_edge_samples"] is True
    assert passport["effective_risk_budget_multiplier"] == 0.5


def test_trading_validation_estimated_cost_samples_become_cost_repair_lane() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
            },
            "payload": {
                "metrics": {
                    "lane_scorecards": {
                        "version": "lane_scorecards_v1",
                        "status": "warn",
                        "lane_actions": {
                            "spot": {
                                "grade": "insufficient",
                                "action": "small_probe_until_sample_builds",
                                "sample_count": 8,
                                "cost_precision_verified_rate": 0.0,
                                "cost_precision_counts": {
                                    "recorded": 0,
                                    "hybrid": 0,
                                    "estimated": 8,
                                    "partial": 0,
                                    "missing": 0,
                                },
                                "cost_unverified_alpha_count": 8,
                                "cost_unverified_alpha_net_pnl": -4.65,
                            }
                        },
                    }
                }
            },
        },
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = gated["lane_authority"]
    action = lane_authority["lane_actions"]["spot"]

    assert lane_authority["cost_evidence_weak_lanes"] == ["spot"]
    assert lane_authority["weak_lanes"] == ["spot"]
    assert action["requires_waiting_entry"] is True
    assert action["scale_blocked_by_cost_precision"] is True
    assert action["scale_blocked_by_cost_evidence"] is True
    assert action["cost_evidence_status"] == "estimated_or_missing"
    assert action["cost_precision_counts"]["estimated"] == 8
    assert action["cost_unverified_alpha_count"] == 8
    assert action["cost_unverified_alpha_net_pnl"] == -4.65
    assert "cost_evidence_repair" in action["scale_blockers"]
    assert "replace_estimated_costs_with_recorded_fill_evidence" in action[
        "scale_repair_targets"
    ]
    assert (
        "prefer_recorded_fill_cost_evidence_before_size_increase"
        in action["entry_quality_requirements"]
    )


def test_scale_candidate_can_increase_budget_with_sample_size() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "value_pullback",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 15,
                "expectancy_pct": 0.7,
                "win_rate_pct": 57.0,
                "profit_factor": 1.8,
                "max_drawdown_pct": -2.5,
                "recovery_factor": 1.3,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            max_scale_multiplier=1.5,
        ),
    )

    assert packet["live_grade"] == "scale_candidate"
    assert packet["max_budget_multiplier"] == 1.25
    assert packet["allow_scale_up"] is True


def test_scale_candidate_without_performance_metrics_is_capped_to_probe() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "value_pullback",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 15,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            max_scale_multiplier=1.5,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["value_pullback"]
    passport = action["risk_budget_passport"]
    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["value_pullback"]

    assert packet["live_grade"] == "scale_candidate"
    assert packet["max_budget_multiplier"] == 1.0
    assert packet["allow_scale_up"] is False
    assert lane_authority["scale_candidate_lanes"] == []
    assert lane_authority["performance_evidence_weak_lanes"] == ["value_pullback"]
    assert action["action"] == "performance_evidence_repair_waiting_probe"
    assert action["scale_up_allowed"] is False
    assert action["requires_waiting_entry"] is True
    assert action["applied_max_budget_multiplier"] == 0.5
    assert action["performance_evidence_status"] == "missing"
    assert action["performance_missing_metrics"] == [
        "expectancy_pct",
        "win_rate_pct",
        "profit_factor",
        "max_drawdown_pct",
        "recovery_factor",
    ]
    assert action["scale_blocked_by_performance_evidence"] is True
    assert "performance_evidence_repair" in action["scale_blockers"]
    assert "record_core_performance_metrics_before_scale_up" in action[
        "scale_repair_targets"
    ]
    assert passport["performance_evidence_cap_multiplier"] == 0.5
    assert passport["scale_blocked_by_performance_evidence"] is True
    assert compact_action["performance_evidence_status"] == "missing"
    assert compact_action["scale_blocked_by_performance_evidence"] is True
    assert compact_action["risk_budget_passport"][
        "performance_evidence_cap_multiplier"
    ] == 0.5


def test_scale_candidate_with_weak_performance_metrics_is_capped_to_probe() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures",
                "evidence_key": "breakout",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 22,
                "expectancy_pct": -0.18,
                "win_rate_pct": 42.0,
                "profit_factor": 0.92,
                "max_drawdown_pct": -2.8,
                "recovery_factor": 1.2,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            max_scale_multiplier=1.5,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["futures:breakout"]
    passport = action["risk_budget_passport"]
    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["futures:breakout"]

    assert packet["live_grade"] == "scale_candidate"
    assert packet["max_budget_multiplier"] == 1.0
    assert packet["allow_scale_up"] is False
    assert lane_authority["scale_candidate_lanes"] == []
    assert lane_authority["performance_evidence_weak_lanes"] == ["futures:breakout"]
    assert action["action"] == "performance_evidence_repair_waiting_probe"
    assert action["scale_up_allowed"] is False
    assert action["applied_max_budget_multiplier"] == 0.25
    assert action["performance_evidence_status"] == "weak"
    assert action["performance_scale_blocking_metrics"] == [
        "expectancy_non_positive",
        "win_rate_below_45pct",
        "profit_factor_below_1",
        "negative_expectancy",
    ]
    assert action["scale_blocked_by_performance_evidence"] is True
    assert "performance_evidence_repair" in action["scale_blockers"]
    assert "produce_positive_expectancy_before_size_increase" in action[
        "scale_repair_targets"
    ]
    assert "raise_profit_factor_above_1_before_pressing" in action[
        "scale_repair_targets"
    ]
    assert passport["performance_evidence_cap_multiplier"] == 0.25
    assert passport["scale_blocked_by_performance_evidence"] is True
    assert compact_action["performance_evidence_status"] == "weak"
    assert compact_action["performance_scale_blocking_metrics"] == [
        "expectancy_non_positive",
        "win_rate_below_45pct",
        "profit_factor_below_1",
        "negative_expectancy",
    ]


def test_insufficient_lane_with_severe_performance_is_capped_to_probe() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot",
                "grade": "insufficient",
                "authority_multiplier": 1.0,
                "sample_count": 8,
                "expectancy_pct": -0.58,
                "win_rate_pct": 37.5,
                "profit_factor": 0.39,
                "max_drawdown_pct": -6.2,
                "recovery_factor": -0.74,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            max_scale_multiplier=1.5,
            min_samples_to_scale=30,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["spot"]
    passport = action["risk_budget_passport"]

    assert lane_authority["performance_evidence_weak_lanes"] == ["spot"]
    assert action["action"] == "early_loss_waiting_probe"
    assert action["sizing_posture"] == "early_loss_waiting_probe"
    assert action["scale_up_allowed"] is False
    assert action["applied_max_budget_multiplier"] == 0.25
    assert action["scale_blocked_by_performance_evidence"] is True
    assert action["performance_evidence_status"] == "weak"
    assert action["performance_severe_metrics"] == [
        "negative_expectancy",
        "profit_factor_below_0_8",
        "non_positive_recovery_factor",
    ]
    assert "performance_evidence_repair" in action["scale_blockers"]
    assert "raise_profit_factor_above_1_before_pressing" in action[
        "scale_repair_targets"
    ]
    assert passport["scale_blocked_by_performance_evidence"] is True
    assert passport["performance_evidence_cap_multiplier"] == 0.25


def test_live_authority_builds_lane_authority_from_scorecards() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "short",
                "grade": "observe_only",
                "authority_multiplier": 0.5,
                "sample_count": 17,
                "expectancy_pct": -0.24,
                "win_rate": 29.4,
                "max_drawdown_pct": -4.1,
            },
            {
                "strategy_family": "mid",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 14,
                "expectancy_pct": -0.52,
                "win_rate": 28.6,
                "max_drawdown_pct": -9.9,
            },
            {
                "strategy_family": "core_etf",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 12,
                "expectancy_pct": 0.82,
                "win_rate": 58.0,
                "max_drawdown_pct": -1.2,
                "profit_factor": 2.1,
                "recovery_factor": 1.6,
                "cost_drag_pct_of_gross_pnl": 18.0,
            },
            {
                "strategy_family": "long",
                "grade": "insufficient",
                "authority_multiplier": 0.75,
                "sample_count": 2,
                "expectancy_pct": 0.2,
                "win_rate": 50.0,
                "max_drawdown_pct": -1.0,
            },
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]

    assert lane_authority["version"] == "lane_authority_v1"
    assert lane_authority["global_scale_up_allowed"] is False
    assert lane_authority["weak_lanes"] == ["short", "mid"]
    assert lane_authority["scale_candidate_lanes"] == ["core_etf"]
    assert lane_authority["insufficient_lanes"] == ["long"]
    assert lane_authority["lane_actions"]["short"]["action"] == "observe_or_waiting_entry"
    assert lane_authority["lane_actions"]["mid"]["action"] == "de_risk_or_waiting_entry"
    assert lane_authority["lane_actions"]["core_etf"]["action"] == "eligible_to_press_when_validation_clear"
    assert lane_authority["lane_actions"]["core_etf"]["profit_factor"] == 2.1
    assert lane_authority["lane_actions"]["core_etf"]["recovery_factor"] == 1.6
    assert lane_authority["lane_actions"]["core_etf"]["cost_drag_pct_of_gross_pnl"] == 18.0
    assert lane_authority["lane_actions"]["long"]["action"] == "small_probe_until_sample_builds"
    assert lane_authority["lane_actions"]["short"]["max_budget_multiplier"] == 0.5
    assert lane_authority["lane_actions"]["mid"]["max_budget_multiplier"] == 0.5
    assert lane_authority["lane_actions"]["core_etf"]["max_budget_multiplier"] == 1.25
    assert lane_authority["lane_actions"]["long"]["max_budget_multiplier"] == 0.75
    assert "press_only_scale_candidate_or_qualified_lanes" in lane_authority["block_design_requirements"]
    compact = compact_live_authority_for_prompt(packet)
    assert (
        compact["lane_authority"]["lane_actions"]["core_etf"]["profit_factor"]
        == 2.1
    )
    assert (
        compact["lane_authority"]["lane_actions"]["core_etf"]["recovery_factor"]
        == 1.6
    )
    assert (
        compact["lane_authority"]["lane_actions"]["core_etf"][
            "cost_drag_pct_of_gross_pnl"
        ]
        == 18.0
    )


def test_lane_authority_accepts_validation_lane_scorecard_win_rate_pct() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "lane": "futures_long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 40,
                "expectancy_pct": 0.68,
                "win_rate_pct": 57.5,
                "max_drawdown_pct": -1.8,
                "profit_factor": 1.85,
                "recovery_factor": 1.4,
                "risk_of_ruin_pct": 3.0,
                "lane_confidence_score": 0.92,
                "recommended_risk_fraction": 0.016,
                "max_risk_cap_fraction": 0.02,
                "cost_drag_pct_of_gross_pnl": 18.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = packet["lane_authority"]["lane_actions"]["futures_long"]
    passport = action["risk_budget_passport"]

    assert action["action"] == "eligible_to_press_when_validation_clear"
    assert action["win_rate"] == 57.5
    assert passport["raw_fractional_kelly_fraction"] > 0
    assert passport["kelly_cap_multiplier"] > 0.25
    assert passport["risk_of_ruin_pct"] == 3.0
    assert passport["lane_confidence_score"] == 0.92
    assert passport["recommended_risk_fraction"] == 0.016
    assert passport["risk_fraction_cap_multiplier"] == 0.8

    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["futures_long"]
    assert compact_action["win_rate"] == 57.5
    assert compact_action["risk_budget_passport"]["risk_of_ruin_pct"] == 3.0
    assert (
        compact_action["risk_budget_passport"]["risk_fraction_cap_multiplier"]
        == 0.8
    )
    assert (
        compact_action["risk_budget_passport"]["raw_fractional_kelly_fraction"]
        > 0
    )


def test_lane_authority_caps_scale_candidate_with_low_recovery_factor() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "lane": "mid",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 24,
                "expectancy_pct": 0.62,
                "win_rate_pct": 58.0,
                "max_drawdown_pct": -3.0,
                "profit_factor": 1.9,
                "recovery_factor": 0.8,
                "recovery_factor_cap_multiplier": 0.75,
                "risk_of_ruin_pct": 3.0,
                "lane_confidence_score": 0.94,
                "recommended_risk_fraction": 0.02,
                "max_risk_cap_fraction": 0.025,
                "cost_drag_pct_of_gross_pnl": 18.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = packet["lane_authority"]["lane_actions"]["mid"]
    passport = action["risk_budget_passport"]
    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["mid"]

    assert action["scale_up_allowed"] is False
    assert action["applied_max_budget_multiplier"] == 0.75
    assert action["scale_decision"] == "capped_until_repairs"
    assert "recovery_factor_cap" in action["scale_blockers"]
    assert "improve_recovery_factor_before_size_increase" in action[
        "scale_repair_targets"
    ]
    assert passport["recovery_factor_cap_multiplier"] == 0.75
    assert passport["applied_risk_budget_multiplier"] == 0.75
    assert compact_action["risk_budget_passport"][
        "recovery_factor_cap_multiplier"
    ] == 0.75


def test_lane_authority_caps_scale_candidate_when_validation_repair_enforced() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 36,
                "expectancy_pct": 0.74,
                "win_rate_pct": 58.0,
                "profit_factor": 1.9,
                "recovery_factor": 1.4,
                "max_drawdown_pct": -1.5,
                "validation_repair_enforced_count": 3,
                "validation_repair_scale_up_blocked_count": 3,
                "validation_repair_waiting_entry_count": 3,
                "validation_repair_avg_budget_multiplier": 0.25,
                "validation_repair_action_counts": {
                    "validation_repair.backtest_wfa_oos_rebuild.walk_forward_analysis": 3,
                },
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = packet["lane_authority"]["lane_actions"]["futures_long"]
    passport = action["risk_budget_passport"]
    compact = compact_live_authority_for_prompt(packet)

    assert packet["lane_authority"]["validation_repair_weak_lanes"] == [
        "futures_long"
    ]
    assert "futures_long" not in packet["lane_authority"]["scale_candidate_lanes"]
    assert action["action"] == "validation_repair_enforced_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["scale_blocked_by_validation_repair"] is True
    assert action["applied_max_budget_multiplier"] == 0.25
    assert action["scale_up_allowed"] is False
    assert passport["validation_repair_cap_multiplier"] == 0.25
    assert passport["scale_blocked_by_validation_repair"] is True
    assert "validation_repair_clearance_required_before_lane_scale_up" in packet[
        "lane_authority"
    ]["block_design_requirements"]
    assert compact["lane_authority"]["lane_actions"]["futures_long"][
        "validation_repair_enforced_count"
    ] == 3
    assert compact["lane_authority"]["lane_actions"]["futures_long"][
        "risk_budget_passport"
    ]["validation_repair_cap_multiplier"] == 0.25


def test_live_authority_keeps_setup_specific_lane_scorecards_separate() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "short",
                "evidence_key": "late_chase",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 14,
                "expectancy_pct": -0.4,
                "win_rate": 31.0,
                "profit_factor": 0.72,
            },
            {
                "strategy_family": "short",
                "evidence_key": "pullback_reclaim",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 16,
                "expectancy_pct": 0.7,
                "win_rate": 57.0,
                "max_drawdown_pct": -1.4,
                "profit_factor": 1.8,
                "recovery_factor": 1.2,
            },
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    authority = packet["lane_authority"]

    assert "short:late_chase" in authority["weak_lanes"]
    assert "short:pullback_reclaim" in authority["scale_candidate_lanes"]
    assert authority["lane_actions"]["short:late_chase"]["action"] == (
        "de_risk_or_waiting_entry"
    )
    assert authority["lane_actions"]["short:pullback_reclaim"]["action"] == (
        "eligible_to_press_when_validation_clear"
    )


def test_lane_authority_caps_negative_edge_scorecard_to_probe_risk_fraction() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "lane": "futures_short",
                "grade": "observe_only",
                "authority_multiplier": 0.5,
                "sample_count": 18,
                "expectancy_pct": -0.32,
                "win_rate": 38.0,
                "max_drawdown_pct": -3.2,
                "profit_factor": 0.74,
                "recovery_factor": -0.6,
                "risk_of_ruin_pct": 28.0,
                "lane_confidence_score": 0.44,
                "recommended_risk_fraction": 0.0,
                "max_risk_cap_fraction": 0.005,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = packet["lane_authority"]["lane_actions"]["futures_short"]
    passport = action["risk_budget_passport"]

    assert action["action"] == "observe_or_waiting_entry"
    assert action["requires_waiting_entry"] is True
    assert passport["risk_of_ruin_pct"] == 28.0
    assert passport["recommended_risk_fraction"] == 0.0
    assert passport["risk_fraction_cap_multiplier"] == 0.25
    assert passport["applied_risk_budget_multiplier"] == 0.25


def test_high_cost_insufficient_lane_requires_waiting_entry_repair() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "long",
                "grade": "insufficient",
                "authority_multiplier": 0.75,
                "sample_count": 4,
                "expectancy_pct": 0.15,
                "win_rate": 50.0,
                "profit_factor": 1.05,
                "recovery_factor": 0.4,
                "cost_drag_pct_of_gross_pnl": 88.0,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    lane_authority = packet["lane_authority"]

    assert lane_authority["cost_weak_lanes"] == ["long"]
    assert lane_authority["weak_lanes"] == ["long"]
    assert lane_authority["insufficient_lanes"] == ["long"]
    assert (
        lane_authority["lane_actions"]["long"]["action"]
        == "cost_repair_waiting_probe"
    )
    assert lane_authority["lane_actions"]["long"]["requires_waiting_entry"] is True
    assert lane_authority["lane_actions"]["long"]["entry_quality_requirements"] == [
        "use_waiting_entry_until_cost_drag_below_60pct",
        "target_move_must_clear_estimated_round_trip_cost",
        "do_not_scale_until_profit_factor_and_recovery_repair",
    ]
    assert (
        "cost_weak_lanes_require_waiting_entry"
        in lane_authority["block_design_requirements"]
    )


def test_cost_evidence_weak_lane_is_demoted_to_waiting_probe() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "sample_count": 12,
                "expectancy_pct": 0.72,
                "win_rate": 58.0,
                "max_drawdown_pct": -1.1,
                "profit_factor": 1.9,
                "recovery_factor": 1.3,
                "cost_drag_pct_of_gross_pnl": 18.0,
                "cost_precision_verified_rate": 25.0,
                "cost_precision_counts": {
                    "recorded": 2,
                    "hybrid": 6,
                    "estimated": 1,
                    "partial": 3,
                    "missing": 0,
                },
                "cost_evidence_status": "hybrid_needs_market_cost_repair",
                "cost_hybrid_alpha_count": 6,
                "cost_hybrid_alpha_net_pnl": 1.23,
                "scale_blocked_by_cost_precision": True,
                "risk_of_ruin_pct": 4.0,
                "lane_confidence_score": 0.86,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["futures:long"]
    passport = action["risk_budget_passport"]

    assert lane_authority["cost_evidence_weak_lanes"] == ["futures:long"]
    assert lane_authority["weak_lanes"] == ["futures:long"]
    assert lane_authority["qualified_lanes"] == ["futures:long"]
    assert lane_authority["scale_candidate_lanes"] == []
    assert action["action"] == "cost_evidence_repair_waiting_probe"
    assert action["sizing_posture"] == "cost_evidence_repair_probe"
    assert action["requires_waiting_entry"] is True
    assert action["cost_precision_verified_rate"] == 25.0
    assert action["cost_precision_counts"] == {
        "recorded": 2,
        "hybrid": 6,
        "estimated": 1,
        "partial": 3,
        "missing": 0,
    }
    assert action["cost_evidence_status"] == "hybrid_needs_market_cost_repair"
    assert (
        action["cost_evidence_repair_hint"]
        == "replace_hybrid_estimates_with_recorded_fill_book_cost_evidence"
    )
    assert action["cost_repair_targets"] == [
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "replace_estimated_costs_with_recorded_execution_costs",
        "complete_partial_cost_components_before_size_increase",
    ]
    assert action["cost_hybrid_alpha_count"] == 6
    assert action["cost_hybrid_alpha_net_pnl"] == 1.23
    assert action["scale_blocked_by_cost_precision"] is True
    assert action["scale_decision"] == "capped_until_repairs"
    assert action["scale_blockers"] == [
        "lane_confidence_cap",
        "cost_evidence_repair",
    ]
    assert action["scale_repair_targets"] == [
        "raise_lane_confidence_before_size_increase",
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "replace_estimated_costs_with_recorded_execution_costs",
        "complete_partial_cost_components_before_size_increase",
    ]
    assert passport["cost_precision_verified_rate"] == 25.0
    assert passport["cost_precision_counts"]["hybrid"] == 6
    assert passport["cost_evidence_status"] == "hybrid_needs_market_cost_repair"
    assert (
        passport["cost_evidence_repair_hint"]
        == "replace_hybrid_estimates_with_recorded_fill_book_cost_evidence"
    )
    assert passport["cost_repair_targets"] == [
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "replace_estimated_costs_with_recorded_execution_costs",
        "complete_partial_cost_components_before_size_increase",
    ]
    assert passport["cost_hybrid_alpha_count"] == 6
    assert passport["cost_hybrid_alpha_net_pnl"] == 1.23
    assert passport["scale_blocked_by_cost_precision"] is True
    assert passport["scale_decision"] == "capped_until_repairs"
    assert passport["scale_blockers"] == [
        "lane_confidence_cap",
        "cost_evidence_repair",
    ]
    assert passport["cost_precision_cap_multiplier"] == 0.5
    assert passport["applied_risk_budget_multiplier"] == 0.5
    assert action["entry_quality_requirements"] == [
        "prefer_recorded_fill_cost_evidence_before_size_increase",
        "use_probe_or_waiting_entry_until_cost_precision_verified",
        "do_not_press_lane_until_recorded_cost_samples_reach_60pct",
    ]
    assert (
        "cost_evidence_weak_lanes_require_recorded_cost_repair"
        in lane_authority["block_design_requirements"]
    )

    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["futures:long"]
    assert compact["lane_authority"]["cost_evidence_weak_lanes"] == ["futures:long"]
    assert compact_action["cost_precision_verified_rate"] == 25.0
    assert compact_action["cost_precision_counts"]["partial"] == 3
    assert compact_action["cost_evidence_status"] == "hybrid_needs_market_cost_repair"
    assert (
        compact_action["cost_evidence_repair_hint"]
        == "replace_hybrid_estimates_with_recorded_fill_book_cost_evidence"
    )
    assert compact_action["cost_repair_targets"] == [
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "replace_estimated_costs_with_recorded_execution_costs",
        "complete_partial_cost_components_before_size_increase",
    ]
    assert compact_action["cost_hybrid_alpha_count"] == 6
    assert compact_action["scale_blocked_by_cost_precision"] is True
    assert compact_action["scale_blocked_by_cost_evidence"] is True
    assert compact_action["scale_decision"] == "capped_until_repairs"
    assert compact_action["scale_blockers"] == [
        "lane_confidence_cap",
        "cost_evidence_repair",
    ]
    assert (
        compact_action["risk_budget_passport"]["cost_precision_counts"]["hybrid"]
        == 6
    )
    assert compact_action["risk_budget_passport"]["cost_repair_targets"] == [
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "replace_estimated_costs_with_recorded_execution_costs",
        "complete_partial_cost_components_before_size_increase",
    ]
    assert (
        compact_action["risk_budget_passport"]["cost_precision_cap_multiplier"]
        == 0.5
    )


def test_low_recorded_cost_precision_rate_infers_waiting_probe() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "volatile_attack",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 20,
                "expectancy_pct": 1.2,
                "win_rate": 61.0,
                "max_drawdown_pct": -1.4,
                "profit_factor": 2.1,
                "recovery_factor": 1.4,
                "cost_drag_pct_of_gross_pnl": 12.0,
                "cost_precision_verified_rate": 42.0,
                "cost_precision_counts": {
                    "recorded": 4,
                    "hybrid": 11,
                    "partial": 5,
                },
                "risk_of_ruin_pct": 3.5,
                "lane_confidence_score": 0.9,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["volatile_attack"]
    passport = action["risk_budget_passport"]

    assert lane_authority["cost_evidence_weak_lanes"] == ["volatile_attack"]
    assert lane_authority["scale_candidate_lanes"] == []
    assert action["action"] == "cost_evidence_repair_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["scale_blocked_by_cost_precision"] is True
    assert action["scale_blocked_by_cost_evidence"] is True
    assert (
        action["cost_evidence_repair_hint"]
        == "increase_recorded_cost_precision_before_size_increase"
    )
    assert passport["cost_precision_cap_multiplier"] == 0.5
    assert passport["applied_risk_budget_multiplier"] == 0.5
    assert passport["cost_precision_counts"] == {
        "recorded": 4,
        "hybrid": 11,
        "partial": 5,
    }


def test_verified_edge_sample_count_caps_scale_even_when_cost_rate_passes() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 10,
                "expectancy_pct": 0.82,
                "win_rate": 61.0,
                "max_drawdown_pct": -1.2,
                "profit_factor": 2.0,
                "recovery_factor": 1.5,
                "cost_drag_pct_of_gross_pnl": 12.0,
                "cost_precision_verified_rate": 60.0,
                "cost_precision_counts": {
                    "recorded": 6,
                    "hybrid": 4,
                    "estimated": 0,
                    "partial": 0,
                    "missing": 0,
                },
                "cost_verified_alpha_count": 6,
                "cost_unverified_alpha_count": 4,
                "cost_verified_alpha_net_pnl": 1.8,
                "cost_unverified_alpha_net_pnl": 1.2,
                "cost_evidence_status": "recorded_enough",
                "risk_of_ruin_pct": 3.0,
                "lane_confidence_score": 1.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["futures:long"]
    passport = action["risk_budget_passport"]

    assert lane_authority["cost_evidence_weak_lanes"] == ["futures:long"]
    assert lane_authority["scale_candidate_lanes"] == []
    assert lane_authority["qualified_lanes"] == ["futures:long"]
    assert action["action"] == "cost_evidence_repair_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["scale_up_allowed"] is False
    assert action["cost_precision_verified_rate"] == 60.0
    assert action["cost_verified_alpha_count"] == 6
    assert action["cost_unverified_alpha_count"] == 4
    assert action["scale_blocked_by_cost_precision"] is False
    assert action["scale_blocked_by_cost_evidence"] is True
    assert action["scale_blocked_by_verified_edge_samples"] is True
    assert action["scale_decision"] == "capped_until_repairs"
    assert action["scale_blockers"] == ["verified_edge_sample_cap"]
    assert action["scale_repair_targets"] == [
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "close_more_recorded_cost_alpha_samples_before_scale_up"
    ]
    assert (
        "build_recorded_cost_alpha_sample_count_before_pressing"
        in action["entry_quality_requirements"]
    )
    assert passport["cost_verified_alpha_count"] == 6
    assert passport["cost_unverified_alpha_count"] == 4
    assert passport["verified_edge_sample_cap_multiplier"] == 0.5
    assert passport["scale_blocked_by_verified_edge_samples"] is True
    assert passport["applied_risk_budget_multiplier"] == 0.5
    assert passport["scale_blockers"] == ["verified_edge_sample_cap"]

    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["futures:long"]
    assert compact_action["cost_verified_alpha_count"] == 6
    assert compact_action["cost_unverified_alpha_count"] == 4
    assert compact_action["scale_blocked_by_verified_edge_samples"] is True
    assert (
        compact_action["risk_budget_passport"]["verified_edge_sample_cap_multiplier"]
        == 0.5
    )


def test_verified_edge_net_pnl_caps_scale_even_when_total_alpha_is_positive() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 15,
                "expectancy_pct": 0.76,
                "win_rate": 60.0,
                "max_drawdown_pct": -1.5,
                "profit_factor": 1.9,
                "recovery_factor": 1.3,
                "cost_drag_pct_of_gross_pnl": 10.0,
                "cost_precision_verified_rate": 66.666667,
                "cost_precision_counts": {
                    "recorded": 10,
                    "hybrid": 0,
                    "estimated": 5,
                    "partial": 0,
                    "missing": 0,
                },
                "cost_verified_alpha_count": 10,
                "cost_unverified_alpha_count": 5,
                "cost_verified_alpha_net_pnl": -0.7,
                "cost_unverified_alpha_net_pnl": 4.2,
                "cost_evidence_status": "recorded_enough",
                "risk_of_ruin_pct": 3.0,
                "lane_confidence_score": 1.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["futures:long"]
    passport = action["risk_budget_passport"]

    assert lane_authority["cost_evidence_weak_lanes"] == ["futures:long"]
    assert lane_authority["scale_candidate_lanes"] == []
    assert lane_authority["qualified_lanes"] == ["futures:long"]
    assert action["action"] == "cost_evidence_repair_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["scale_up_allowed"] is False
    assert action["scale_blocked_by_cost_precision"] is False
    assert action["scale_blocked_by_cost_evidence"] is True
    assert action["scale_blocked_by_verified_edge_samples"] is False
    assert action["scale_blocked_by_verified_edge_net_pnl"] is True
    assert action["scale_blockers"] == ["verified_edge_net_pnl_cap"]
    assert (
        "produce_positive_recorded_cost_alpha_net_pnl_before_scale_up"
        in action["scale_repair_targets"]
    )
    assert (
        "require_positive_recorded_cost_alpha_net_pnl_before_pressing"
        in action["entry_quality_requirements"]
    )
    assert passport["cost_verified_alpha_count"] == 10
    assert passport["cost_unverified_alpha_count"] == 5
    assert passport["verified_edge_net_cap_multiplier"] == 0.25
    assert passport["scale_blocked_by_verified_edge_net_pnl"] is True
    assert passport["applied_risk_budget_multiplier"] == 0.25
    assert passport["scale_blockers"] == ["verified_edge_net_pnl_cap"]

    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["futures:long"]
    assert compact_action["scale_blocked_by_verified_edge_net_pnl"] is True
    assert (
        compact_action["risk_budget_passport"]["verified_edge_net_cap_multiplier"]
        == 0.25
    )


def test_cost_evidence_weak_lane_accepts_pct_alias_from_validation() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot",
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "sample_count": 12,
                "expectancy_pct": 0.68,
                "win_rate": 58.0,
                "max_drawdown_pct": -1.0,
                "profit_factor": 1.8,
                "recovery_factor": 1.2,
                "cost_drag_pct_of_gross_pnl": 18.0,
                "cost_precision_verified_rate_pct": 40.0,
                "scale_blocked_by_cost_precision": True,
                "risk_of_ruin_pct": 4.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    action = packet["lane_authority"]["lane_actions"]["spot"]
    passport = action["risk_budget_passport"]

    assert packet["lane_authority"]["cost_evidence_weak_lanes"] == ["spot"]
    assert action["cost_precision_verified_rate"] == 40.0
    assert action["requires_waiting_entry"] is True
    assert passport["cost_precision_verified_rate"] == 40.0
    assert passport["cost_precision_cap_multiplier"] == 0.5


def test_entry_quality_weak_lane_requires_pullback_or_waiting_entry() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "short",
                "evidence_key": "late_chase",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 8,
                "expectancy_pct": 0.4,
                "win_rate": 55.0,
                "profit_factor": 1.4,
                "entry_quality_sample_count": 8,
                "avg_entry_quality_score": 35.0,
                "bad_entry_quality_rate_pct": 100.0,
                "entry_quality_label_counts": {"extended_momentum": 5, "late_chase": 3},
                "bad_entry_quality_label_counts": {
                    "extended_momentum": 5,
                    "late_chase": 3,
                },
                "good_entry_quality_label_counts": {},
                "dominant_bad_entry_quality_label": "extended_momentum",
                "scale_blocked_by_entry_quality": True,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=5,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["short:late_chase"]
    passport = action["risk_budget_passport"]

    assert lane_authority["entry_quality_weak_lanes"] == ["short:late_chase"]
    assert "short:late_chase" in lane_authority["weak_lanes"]
    assert action["action"] == "entry_quality_repair_waiting_entry"
    assert action["sizing_posture"] == "entry_quality_repair_probe"
    assert action["requires_waiting_entry"] is True
    assert action["avg_entry_quality_score"] == 35.0
    assert action["bad_entry_quality_rate_pct"] == 100.0
    assert action["entry_quality_label_counts"] == {
        "extended_momentum": 5,
        "late_chase": 3,
    }
    assert action["bad_entry_quality_label_counts"] == {
        "extended_momentum": 5,
        "late_chase": 3,
    }
    assert action["dominant_bad_entry_quality_label"] == "extended_momentum"
    assert (
        action["entry_quality_repair_hint"]
        == "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks"
    )
    assert action["entry_repair_targets"] == [
        "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
        "require_entry_quality_score_above_60_before_size_increase",
        "reduce_frequency_until_bad_entry_rate_below_50pct",
        "use_waiting_entry_or_price_improvement_before_live_block",
    ]
    assert action["scale_blocked_by_entry_quality"] is True
    assert action["scale_decision"] == "capped_until_repairs"
    assert action["scale_blockers"] == ["entry_quality_repair"]
    assert action["scale_repair_targets"] == [
        "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
        "require_entry_quality_score_above_60_before_size_increase",
        "reduce_frequency_until_bad_entry_rate_below_50pct",
        "use_waiting_entry_or_price_improvement_before_live_block",
    ]
    assert passport["bad_entry_quality_label_counts"]["extended_momentum"] == 5
    assert (
        passport["entry_quality_repair_hint"]
        == "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks"
    )
    assert passport["entry_repair_targets"] == [
        "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
        "require_entry_quality_score_above_60_before_size_increase",
        "reduce_frequency_until_bad_entry_rate_below_50pct",
        "use_waiting_entry_or_price_improvement_before_live_block",
    ]
    assert passport["entry_quality_cap_multiplier"] == 0.5
    assert passport["scale_decision"] == "capped_until_repairs"
    assert passport["scale_blockers"] == ["entry_quality_repair"]
    assert passport["applied_risk_budget_multiplier"] == 0.5
    assert action["entry_quality_requirements"] == [
        "prefer_pullback_reclaim_or_value_location_before_immediate_entry",
        "require_entry_quality_score_above_60_before_size_increase",
        "do_not_chase_extended_moves_until_live_entry_quality_repairs",
    ]
    assert (
        "entry_quality_weak_lanes_require_pullback_or_waiting_entry"
        in lane_authority["block_design_requirements"]
    )

    compact = compact_live_authority_for_prompt(packet)
    compact_action = compact["lane_authority"]["lane_actions"]["short:late_chase"]
    assert compact["lane_authority"]["entry_quality_weak_lanes"] == [
        "short:late_chase"
    ]
    assert compact_action["avg_entry_quality_score"] == 35.0
    assert compact_action["bad_entry_quality_label_counts"]["late_chase"] == 3
    assert (
        compact_action["entry_quality_repair_hint"]
        == "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks"
    )
    assert compact_action["entry_repair_targets"] == [
        "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
        "require_entry_quality_score_above_60_before_size_increase",
        "reduce_frequency_until_bad_entry_rate_below_50pct",
        "use_waiting_entry_or_price_improvement_before_live_block",
    ]
    assert (
        compact_action["risk_budget_passport"]["dominant_bad_entry_quality_label"]
        == "extended_momentum"
    )
    assert compact_action["risk_budget_passport"]["entry_repair_targets"] == [
        "replace_chase_entries_with_pullback_reclaim_or_value_waiting_blocks",
        "require_entry_quality_score_above_60_before_size_increase",
        "reduce_frequency_until_bad_entry_rate_below_50pct",
        "use_waiting_entry_or_price_improvement_before_live_block",
    ]
    assert (
        compact_action["risk_budget_passport"]["entry_quality_cap_multiplier"]
        == 0.5
    )
    assert compact_action["scale_decision"] == "capped_until_repairs"
    assert compact_action["scale_blockers"] == ["entry_quality_repair"]


def test_early_loss_insufficient_lane_requires_waiting_entry_probe() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures",
                "evidence_key": "late_chase",
                "grade": "insufficient",
                "authority_multiplier": 0.75,
                "sample_count": 3,
                "expectancy_pct": -0.18,
                "win_rate": 33.3,
                "profit_factor": 0.72,
                "recovery_factor": -0.4,
                "cost_drag_pct_of_gross_pnl": 22.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    lane_authority = packet["lane_authority"]
    action = lane_authority["lane_actions"]["futures:late_chase"]

    assert lane_authority["early_loss_lanes"] == ["futures:late_chase"]
    assert lane_authority["weak_lanes"] == ["futures:late_chase"]
    assert lane_authority["insufficient_lanes"] == ["futures:late_chase"]
    assert action["action"] == "early_loss_waiting_probe"
    assert action["sizing_posture"] == "early_loss_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["entry_quality_requirements"][:3] == [
        "use_waiting_entry_until_expectancy_and_win_rate_recover",
        "keep_probe_budget_until_min_sample_and_positive_expectancy",
        "require_price_improvement_or_setup_confirmation_before_entry",
    ]
    assert "record_expectancy_win_rate_pf_mdd_recovery_before_pressing" in action[
        "entry_quality_requirements"
    ]
    assert "do_not_scale_lane_without_positive_expectancy_pf_and_recovery" in action[
        "entry_quality_requirements"
    ]
    assert action["scale_blocked_by_performance_evidence"] is True
    assert (
        "early_loss_lanes_require_waiting_entry"
        in lane_authority["block_design_requirements"]
    )


def test_validation_gate_updates_lane_authority_scale_permission() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 16,
                "expectancy_pct": 0.9,
                "win_rate": 58.0,
                "max_drawdown_pct": -1.4,
                "profit_factor": 1.8,
                "recovery_factor": 1.3,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )
    assert packet["allow_scale_up"] is True
    assert packet["lane_authority"]["global_scale_up_allowed"] is True

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "normal",
                "pass_count": 9,
                "warn_count": 7,
                "fail_count": 3,
                "missing_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
                "hard_fail_count": 0,
            },
            "payload": {
                "disciplines": [
                    {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                    {"id": "capacity_analysis", "label": "용량 분석", "status": "pass"},
                    {"id": "mdd_limit", "label": "MDD 제한", "status": "pass"},
                    {"id": "monte_carlo", "label": "몬테카를로", "status": "fail"},
                ]
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert gated["allow_scale_up"] is False
    assert gated["validation_gate"]["status"] == "validation_probe"
    assert gated["lane_authority"]["global_scale_up_allowed"] is False
    assert gated["lane_authority"]["scale_candidate_lanes"] == ["futures:long"]
    assert (
        gated["lane_authority"]["lane_actions"]["futures:long"][
            "applied_max_budget_multiplier"
        ]
        == gated["max_budget_multiplier"]
    )
    assert (
        gated["lane_authority"]["lane_actions"]["futures:long"]["scale_up_allowed"]
        is False
    )
    action = gated["lane_authority"]["lane_actions"]["futures:long"]
    assert action["scale_decision"] == "capped_until_repairs"
    assert "validation_gate_scale_cap" in action["scale_blockers"]
    assert "clear_validation_gate_before_scale_up" in action["scale_repair_targets"]
    assert "scale_up_blocked_by_validation_gate" in gated["lane_authority"]["block_design_requirements"]
    compact = compact_live_authority_for_prompt(gated)
    assert compact["lane_authority"]["global_scale_up_allowed"] is False
    assert compact["lane_authority"]["scale_candidate_lanes"] == ["futures:long"]
    assert (
        compact["lane_authority"]["lane_actions"]["futures:long"][
            "max_budget_multiplier"
        ]
        == 1.25
    )
    assert (
        compact["lane_authority"]["lane_actions"]["futures:long"][
            "applied_max_budget_multiplier"
        ]
        == gated["max_budget_multiplier"]
    )
    assert (
        compact["lane_authority"]["lane_actions"]["futures:long"]["scale_up_allowed"]
        is False
    )
    compact_action = compact["lane_authority"]["lane_actions"]["futures:long"]
    assert "validation_gate_scale_cap" in compact_action["scale_blockers"]


def test_active_revision_gate_blocks_lane_authority_scale_up_until_samples_close() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 16,
                "expectancy_pct": 0.9,
                "win_rate": 58.0,
                "profit_factor": 1.8,
                "max_drawdown_pct": -1.4,
                "recovery_factor": 1.4,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )
    assert packet["allow_scale_up"] is True

    gated = apply_active_revision_evidence_gate(
        packet,
        {
            "version": "active_revision_evidence_v1",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v2",
            "status": "active_revision_samples_pending_close_with_proxy",
            "validation_sample_role": "legacy_proxy_metrics_no_scale",
            "active_sample_count": 0,
            "effective_sample_count": 0,
            "legacy_proxy_sample_count": 149,
            "pending_block_count": 14,
            "min_samples_to_scale": 20,
            "scale_up_allowed": False,
        },
    )

    action = gated["lane_authority"]["lane_actions"]["futures:long"]
    passport = action["risk_budget_passport"]

    assert gated["allow_scale_up"] is False
    assert gated["max_budget_multiplier"] == 0.25
    assert gated["lane_authority"]["global_scale_up_allowed"] is False
    assert gated["lane_authority"]["active_revision_gate"]["status"] == (
        "active_revision_samples_pending_close_with_proxy"
    )
    assert action["scale_up_allowed"] is False
    assert action["requires_waiting_entry"] is True
    assert action["scale_up_blocked_by_active_revision"] is True
    assert action["action"] == "active_revision_probe_until_samples_close"
    assert action["applied_max_budget_multiplier"] == 0.25
    assert action["scale_decision"] == "capped_until_repairs"
    assert "active_revision_gate" in action["scale_blockers"]
    assert "close_active_revision_samples_before_scale_up" in action[
        "scale_repair_targets"
    ]
    assert passport["active_revision_gate_status"] == (
        "active_revision_samples_pending_close_with_proxy"
    )
    assert passport["active_revision_cap_multiplier"] == 0.25
    assert passport["effective_risk_budget_multiplier"] == 0.25
    assert passport["scale_decision"] == "capped_until_repairs"
    assert "active_revision_gate" in passport["scale_blockers"]
    assert (
        "active_revision_closed_samples_required_before_lane_scale_up"
        in gated["lane_authority"]["block_design_requirements"]
    )

    compact = compact_live_authority_for_prompt(gated)
    compact_action = compact["lane_authority"]["lane_actions"]["futures:long"]
    assert compact["lane_authority"]["active_revision_gate"]["cap_multiplier"] == 0.25
    assert compact_action["scale_up_allowed"] is False
    assert compact_action["active_revision_gate"]["pending_block_count"] == 14
    assert "active_revision_gate" in compact_action["scale_blockers"]
    assert compact_action["risk_budget_passport"]["active_revision_cap_multiplier"] == (
        0.25
    )
    assert "active_revision_gate" in compact_action["risk_budget_passport"][
        "scale_blockers"
    ]


def test_lane_risk_budget_passport_caps_scale_candidate_with_ruin_and_drawdown() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 30,
                "expectancy_pct": 0.72,
                "win_rate": 55.0,
                "max_drawdown_pct": -5.5,
                "profit_factor": 1.62,
                "recovery_factor": 1.1,
                "cost_drag_pct_of_gross_pnl": 22.0,
                "risk_of_ruin_pct": 12.0,
            }
        ],
        config=LiveAuthorityConfig(
            base_budget_multiplier=1.0,
            min_samples_to_scale=10,
        ),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    action = gated["lane_authority"]["lane_actions"]["futures:long"]
    passport = action["risk_budget_passport"]

    assert passport["version"] == "lane_risk_budget_passport_v1"
    assert passport["raw_fractional_kelly_fraction"] > 0
    assert passport["ruin_cap_multiplier"] == 0.5
    assert passport["drawdown_cap_multiplier"] == 0.75
    assert passport["applied_risk_budget_multiplier"] == 0.5
    assert passport["scale_decision"] == "capped_until_repairs"
    assert passport["scale_blockers"] == ["drawdown_cap", "risk_of_ruin_cap"]
    assert passport["scale_repair_targets"] == [
        "reduce_drawdown_usage_before_size_increase",
        "lower_risk_of_ruin_before_size_increase",
    ]
    assert action["applied_max_budget_multiplier"] == 0.5
    assert action["scale_up_allowed"] is False
    assert action["scale_decision"] == "capped_until_repairs"
    assert action["scale_blockers"] == ["drawdown_cap", "risk_of_ruin_cap"]

    compact = compact_live_authority_for_prompt(gated)
    compact_action = compact["lane_authority"]["lane_actions"]["futures:long"]
    assert compact_action["risk_budget_passport"]["applied_risk_budget_multiplier"] == 0.5
    assert compact_action["scale_decision"] == "capped_until_repairs"
    assert compact_action["scale_blockers"] == ["drawdown_cap", "risk_of_ruin_cap"]
    assert compact_action["risk_budget_passport"]["scale_repair_targets"] == [
        "reduce_drawdown_usage_before_size_increase",
        "lower_risk_of_ruin_before_size_increase",
    ]


def test_validation_exposure_gate_caps_scale_candidate_until_concentration_clears() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 36,
                "expectancy_pct": 0.82,
                "win_rate": 58.0,
                "max_drawdown_pct": -4.5,
                "profit_factor": 1.9,
                "recovery_factor": 1.35,
                "cost_drag_pct_of_gross_pnl": 18.0,
                "risk_of_ruin_pct": 3.0,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 17,
                "warn_count": 2,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
            },
            "payload": {
                "disciplines": [
                    {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                    {
                        "id": "correlation",
                        "label": "상관관계",
                        "status": "warn",
                        "action": "상관 블록 증액 전 exposure 확인",
                    },
                    {
                        "id": "factor_exposure",
                        "label": "팩터 익스포저",
                        "status": "warn",
                        "action": "팩터 쏠림 완화 전 증액 보류",
                    },
                ],
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    authority = gated["lane_authority"]
    action = authority["lane_actions"]["futures:long"]
    passport = action["risk_budget_passport"]

    assert gated["validation_gate"]["status"] == "clear"
    assert gated["allow_scale_up"] is False
    assert authority["validation_exposure_gate"]["cap_multiplier"] == 0.75
    assert authority["exposure_blocked_lanes"] == ["futures:long"]
    assert action["action"] == "exposure_review_waiting_entry_until_validation_clear"
    assert action["scale_up_blocked_by_exposure_gate"] is True
    assert action["requires_waiting_entry"] is True
    assert action["applied_max_budget_multiplier"] == 0.75
    assert action["scale_up_allowed"] is False
    assert (
        "require_regime_correlation_factor_review_before_scale_up"
        in action["entry_quality_requirements"]
    )
    assert (
        passport["validation_exposure_gate_status"]
        == "regime_correlation_factor_review_required"
    )
    assert passport["validation_exposure_cap_multiplier"] == 0.75
    assert passport["effective_risk_budget_multiplier"] == 0.75

    compact = compact_live_authority_for_prompt(gated)
    compact_authority = compact["lane_authority"]
    compact_action = compact_authority["lane_actions"]["futures:long"]
    assert compact_authority["exposure_blocked_lanes"] == ["futures:long"]
    assert compact_action["validation_exposure_gate"]["cap_multiplier"] == 0.75
    assert (
        compact_action["risk_budget_passport"]["validation_exposure_gate_status"]
        == "regime_correlation_factor_review_required"
    )


def test_validation_governor_cap_is_reflected_in_lane_risk_budget_passport() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "core_etf",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 30,
                "expectancy_pct": 0.8,
                "win_rate": 58.0,
                "max_drawdown_pct": -1.0,
                "profit_factor": 2.0,
                "recovery_factor": 1.5,
                "cost_drag_pct_of_gross_pnl": 12.0,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 18,
                "warn_count": 1,
                "fail_count": 0,
                "missing_count": 0,
            },
            "payload": {
                "metrics": {
                    "ruin_profile": {
                        "status": "warn",
                        "governor_action": "risk_off",
                        "risk_of_ruin_pct": 12.5,
                    }
                }
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    passport = gated["lane_authority"]["lane_actions"]["core_etf"][
        "risk_budget_passport"
    ]

    assert gated["validation_gate"]["risk_governor_action"] == "risk_off"
    assert passport["validation_governor_cap_multiplier"] == 0.25
    assert passport["effective_risk_budget_multiplier"] == 0.25
    assert (
        gated["lane_authority"]["lane_actions"]["core_etf"][
            "applied_max_budget_multiplier"
        ]
        == 0.25
    )


def test_validation_gate_does_not_block_on_diagnostic_failures_when_core_passes() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "normal",
                "pass_count": 3,
                "warn_count": 0,
                "fail_count": 16,
                "missing_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
                "hard_fail_count": 0,
            },
            "payload": {
                "discipline_count": 19,
                "disciplines": [
                    {"id": "data_validation", "status": "pass"},
                    {"id": "capacity_analysis", "status": "pass"},
                    {"id": "mdd_limit", "status": "pass"},
                    {"id": "monte_carlo", "status": "fail"},
                ],
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gate["status"] == "validation_probe"
    assert gate["fail_count"] == 16
    assert gate["hard_fail_count"] == 0


def test_validation_pressure_translates_diagnostic_failures_into_block_design_actions() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "restricted",
            "allow_scale_up": False,
            "max_budget_multiplier": 0.25,
            "trading_validation": {
                "payload": {
                    "summary": {
                        "total_score": 38.0,
                        "readiness": "normal",
                        "pass_count": 6,
                        "warn_count": 4,
                        "fail_count": 9,
                        "missing_count": 0,
                        "core_pass_count": 3,
                        "core_fail_count": 0,
                        "hard_fail_count": 0,
                    },
                    "disciplines": [
                        {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                        {"id": "capacity_analysis", "label": "용량 분석", "status": "pass"},
                        {"id": "mdd_limit", "label": "MDD 제한", "status": "pass"},
                        {
                            "id": "cost_simulation",
                            "label": "거래비용 시뮬레이션",
                            "status": "fail",
                            "action": "비용보다 큰 기대폭 요구",
                        },
                        {
                            "id": "kelly_sizing",
                            "label": "켈리 공식",
                            "status": "fail",
                            "action": "fractional Kelly로 축소",
                        },
                        {
                            "id": "monte_carlo",
                            "label": "몬테카를로",
                            "status": "fail",
                            "action": "순서 리스크 축소",
                        },
                        {
                            "id": "profit_factor",
                            "label": "수익팩터",
                            "status": "fail",
                            "action": "PF 회복 전 빈도 축소",
                        },
                        {
                            "id": "walk_forward_analysis",
                            "label": "Walk Forward Analysis",
                            "status": "warn",
                            "action": "WFA 확인 전 증액 보류",
                        },
                    ],
                }
            },
            "validation_gate": {
                "status": "validation_normal",
                "readiness": "normal",
                "reason": "diagnostic failures only; hard gate clear",
                "risk_governor_action": "risk_off",
                "risk_governor_source": "kelly_sizing",
                "fail_count": 9,
                "hard_fail_count": 0,
                "original_max_budget_multiplier": 1.0,
                "applied_max_budget_multiplier": 0.25,
            },
        }
    )

    pressure = compact["validation_gate"]["validation_pressure"]

    assert pressure["version"] == "validation_pressure_v1"
    assert pressure["hard_block"] is False
    assert pressure["severity"] == "risk_off"
    assert pressure["entry_posture"] == "patient_waiting_entry"
    assert pressure["sizing_posture"] == "fractional_small_only"
    assert pressure["scale_up_allowed"] is False
    assert "cost_simulation" in pressure["fail_ids"]
    assert "walk_forward_analysis" in pressure["warn_ids"]
    assert "require_positive_net_edge_after_all_costs" in pressure["block_design_requirements"]
    assert "use_fractional_kelly_with_drawdown_and_ruin_caps" in pressure["block_design_requirements"]
    assert "prefer_waiting_entry_or_price_improvement" in pressure["block_design_requirements"]
    actions = {
        row["id"]: row
        for row in pressure["discipline_actions"]
    }
    assert actions["cost_simulation"]["entry_constraint"] == (
        "entry_must_clear_round_trip_cost_and_slippage"
    )
    assert actions["cost_simulation"]["sizing_constraint"] == (
        "do_not_scale_until_recorded_cost_evidence_repairs"
    )
    assert actions["kelly_sizing"]["sizing_constraint"] == (
        "use_fractional_kelly_mdd_ruin_confidence_cap"
    )
    assert actions["monte_carlo"]["entry_constraint"] == (
        "patient_entry_only_when_sequence_risk_is_reduced"
    )
    assert actions["walk_forward_analysis"]["repair_action"] == (
        "rebuild_recent_rolling_walk_forward_windows"
    )
    assert "require_oos_wfa_or_live_shadow_before_scale_up" in pressure["block_design_requirements"]


def test_compact_live_authority_for_prompt_surfaces_validation_lane_scorecards() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "qualified",
            "allow_scale_up": False,
            "max_budget_multiplier": 0.5,
            "trading_validation": {
                "payload": {
                    "summary": {"readiness": "normal"},
                    "metrics": {
                        "lane_scorecards": {
                            "version": "lane_scorecards_v1",
                            "status": "warn",
                            "weak_lanes": ["futures_short"],
                            "scale_candidate_lanes": ["spot"],
                            "insufficient_lanes": ["volatile_attack"],
                            "cost_evidence_weak_lanes": ["spot"],
                            "validation_evidence_weak_lanes": ["spot"],
                            "lane_actions": {
                                "futures_short": {
                                    "grade": "weak",
                                    "action": "de_risk_or_waiting_entry",
                                    "sample_count": 8,
                                    "profit_factor": 0.72,
                                    "recovery_factor": -0.4,
                                    "cost_drag_pct_of_gross_pnl": 18.0,
                                    "authority_multiplier": 0.5,
                                    "max_budget_multiplier": 0.5,
                                    "requires_waiting_entry": True,
                                },
                                "spot": {
                                    "grade": "scale_candidate",
                                    "action": "eligible_to_press_when_validation_clear",
                                    "sample_count": 12,
                                    "profit_factor": 1.8,
                                    "recovery_factor": 1.2,
                                    "cost_drag_pct_of_gross_pnl": 20.0,
                                    "authority_multiplier": 0.5,
                                    "max_budget_multiplier": 0.5,
                                    "risk_budget_multiplier": 0.5,
                                    "risk_budget_scale_decision": (
                                        "capped_until_repairs"
                                    ),
                                    "risk_budget_blockers": [
                                        "cost_precision_cap",
                                        "validation_evidence_cap",
                                    ],
                                    "risk_budget_repair_targets": [
                                        "record_fee_tax_spread_slippage_funding_before_scale_up",
                                        "pass_backtest_walk_forward_oos_live_shadow_before_scale_up",
                                    ],
                                    "validation_evidence_required_evidence": [
                                        "fee",
                                        "spread",
                                        "slippage",
                                    ],
                                    "validation_evidence_required_checks": [
                                        "positive_net_edge"
                                    ],
                                    "validation_evidence_pass_collection_hooks": [
                                        "sync precise fills/costs -> refresh_trading_validation"
                                    ],
                                    "validation_evidence_pass_current_gaps": [
                                        "precise cost evidence missing"
                                    ],
                                    "validation_evidence_pass_criteria": [
                                        "net edge remains positive after 2x cost stress"
                                    ],
                                    "validation_evidence_verification_artifacts": [
                                        "recorded cost components survive 2x stress"
                                    ],
                                    "raw_kelly_fraction": 0.42,
                                    "fractional_kelly_fraction": 0.105,
                                    "kelly_cap_multiplier": 1.25,
                                    "drawdown_cap_multiplier": 1.0,
                                    "ruin_cap_multiplier": 1.0,
                                    "lane_confidence_score": 0.72,
                                    "lane_confidence_cap_multiplier": 0.72,
                                    "cost_precision_verified_rate_pct": 40.0,
                                    "missing_cost_component_counts": {
                                        "funding": 3,
                                    },
                                    "present_cost_component_counts": {
                                        "fees": 12,
                                        "spread": 9,
                                    },
                                    "required_cost_component_counts": {
                                        "fees": 12,
                                        "funding": 12,
                                        "spread": 12,
                                    },
                                    "cost_precision_reason_counts": {
                                        "recorded_cost_missing_required_components": 3,
                                    },
                                    "scale_blocked_by_cost_precision": True,
                                },
                            },
                        }
                    },
                }
            },
            "validation_gate": {
                "status": "validation_normal",
                "readiness": "normal",
                "reason": "diagnostic only",
            },
        }
    )

    lanes = compact["validation_gate"]["lane_scorecards"]

    assert lanes["version"] == "lane_scorecards_v1"
    assert lanes["weak_lanes"] == ["futures_short"]
    assert lanes["scale_candidate_lanes"] == ["spot"]
    assert lanes["cost_evidence_weak_lanes"] == ["spot"]
    assert lanes["validation_evidence_weak_lanes"] == ["spot"]
    assert lanes["lane_actions"]["futures_short"]["requires_waiting_entry"] is True
    assert lanes["lane_actions"]["futures_short"]["authority_multiplier"] == 0.5
    assert lanes["lane_actions"]["spot"]["action"] == (
        "eligible_to_press_when_validation_clear"
    )
    assert lanes["lane_actions"]["spot"]["max_budget_multiplier"] == 0.5
    assert lanes["lane_actions"]["spot"]["risk_budget_multiplier"] == 0.5
    assert lanes["lane_actions"]["spot"]["risk_budget_scale_decision"] == (
        "capped_until_repairs"
    )
    assert lanes["lane_actions"]["spot"]["risk_budget_blockers"] == [
        "cost_precision_cap",
        "validation_evidence_cap",
    ]
    assert lanes["lane_actions"]["spot"]["risk_budget_repair_targets"] == [
        "record_fee_tax_spread_slippage_funding_before_scale_up",
        "pass_backtest_walk_forward_oos_live_shadow_before_scale_up",
    ]
    assert lanes["lane_actions"]["spot"][
        "validation_evidence_required_evidence"
    ] == ["fee", "spread", "slippage"]
    assert lanes["lane_actions"]["spot"]["validation_evidence_required_checks"] == [
        "positive_net_edge"
    ]
    assert lanes["lane_actions"]["spot"][
        "validation_evidence_pass_collection_hooks"
    ] == ["sync precise fills/costs -> refresh_trading_validation"]
    assert lanes["lane_actions"]["spot"]["validation_evidence_pass_current_gaps"] == [
        "precise cost evidence missing"
    ]
    assert lanes["lane_actions"]["spot"]["validation_evidence_pass_criteria"] == [
        "net edge remains positive after 2x cost stress"
    ]
    assert lanes["lane_actions"]["spot"][
        "validation_evidence_verification_artifacts"
    ] == ["recorded cost components survive 2x stress"]
    assert lanes["lane_actions"]["spot"]["fractional_kelly_fraction"] == 0.105
    assert lanes["lane_actions"]["spot"]["kelly_cap_multiplier"] == 1.25
    assert lanes["lane_actions"]["spot"]["lane_confidence_score"] == 0.72
    assert lanes["lane_actions"]["spot"]["cost_precision_verified_rate"] == 40.0
    assert lanes["lane_actions"]["spot"]["missing_cost_component_counts"] == {
        "funding": 3,
    }
    assert lanes["lane_actions"]["spot"]["present_cost_component_counts"] == {
        "fees": 12,
        "spread": 9,
    }
    assert lanes["lane_actions"]["spot"]["required_cost_component_counts"] == {
        "fees": 12,
        "funding": 12,
        "spread": 12,
    }
    assert lanes["lane_actions"]["spot"]["cost_precision_reason_counts"] == {
        "recorded_cost_missing_required_components": 3,
    }
    assert (
        lanes["lane_actions"]["spot"]["scale_blocked_by_cost_precision"] is True
    )


def test_compact_live_authority_prioritizes_validation_repair_lanes() -> None:
    noisy_actions = {
        f"lane_{index}": {
            "grade": "qualified",
            "action": "normal_or_selective_press",
            "sample_count": 20,
        }
        for index in range(20)
    }
    noisy_actions["mid:validation:cost_simulation"] = {
        "grade": "restricted",
        "action": "de_risk_or_waiting_entry",
        "sample_count": 4,
        "requires_waiting_entry": True,
    }

    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "qualified",
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": [
                    *(f"lane_{index}" for index in range(20)),
                    "mid:validation:cost_simulation",
                ],
                "lane_actions": noisy_actions,
            },
        }
    )

    lane_authority = compact["lane_authority"]
    assert list(lane_authority["lane_actions"])[0] == (
        "mid:validation:cost_simulation"
    )
    assert "mid:validation:cost_simulation" in lane_authority["lane_actions"]
    assert lane_authority["weak_lanes"][0] == "mid:validation:cost_simulation"


def test_compact_live_authority_for_prompt_preserves_performance_lane_hints() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "venue": "binance",
            "status": "ok",
            "live_grade": "restricted",
            "performance_lanes": [
                {
                    "venue": "kis",
                    "lane": "mid",
                    "alpha_count": 10,
                    "quality_hint": "qualified",
                    "action_hint": "KIS lane only",
                },
                {
                    "venue": "binance",
                    "lane": "futures:short",
                    "block_count": 6,
                    "alpha_count": 6,
                    "non_alpha_count": 2,
                    "unfilled_or_unrealized_count": 1,
                    "operational_failure_pre_fill_count": 1,
                    "execution_quality_count": 8,
                    "alpha_conversion_status": (
                        "blocked_by_fill_or_execution_evidence"
                    ),
                    "alpha_conversion_repair_hint": (
                        "repair fill evidence before sizing this lane"
                    ),
                    "expectancy_pct": -0.42,
                    "win_rate_pct": 33.3,
                    "profit_factor": 0.61,
                    "max_drawdown_pct": -3.2,
                    "recovery_factor": 0.0,
                    "cost_drag_pct_of_abs_gross_pnl": 85.0,
                    "cost_precision_counts": {
                        "recorded": 4,
                        "hybrid": 1,
                        "estimated": 2,
                        "partial": 1,
                        "missing": 0,
                    },
                    "missing_cost_component_counts": {
                        "spread": 1,
                        "slippage": 1,
                    },
                    "present_cost_component_counts": {
                        "fees": 6,
                        "funding": 3,
                    },
                    "required_cost_component_counts": {
                        "fees": 6,
                        "spread": 6,
                        "slippage": 6,
                    },
                    "cost_precision_reason_counts": {
                        "recorded_cost_missing_required_components": 1,
                    },
                    "validation_pressure_severity_counts": {
                        "risk_off": 2,
                    },
                    "validation_pressure_entry_posture_counts": {
                        "patient_waiting_entry": 2,
                    },
                    "validation_pressure_sizing_posture_counts": {
                        "fractional_small_only": 2,
                    },
                    "validation_pressure_fail_id_counts": {
                        "cost_simulation": 1,
                    },
                    "validation_pressure_warn_id_counts": {
                        "monte_carlo": 1,
                    },
                    "validation_pressure_missing_id_counts": {
                        "walk_forward_analysis": 1,
                    },
                    "validation_pressure_discipline_action_counts": {
                        "cost_simulation:fail": 1,
                        "monte_carlo:warn": 1,
                    },
                    "cost_hybrid_alpha_count": 1,
                    "cost_hybrid_alpha_net_pnl": -0.12,
                    "quality_hint": "weak_review",
                    "action_hint": (
                        "reduce_or_wait; require better entry quality before new blocks"
                    ),
                },
            ],
        }
    )

    lanes = compact["performance_lanes"]
    assert len(lanes) == 1
    assert lanes[0]["venue"] == "binance"
    assert lanes[0]["lane"] == "futures:short"
    assert lanes[0]["quality_hint"] == "weak_review"
    assert "reduce" in lanes[0]["action_hint"]
    assert lanes[0]["alpha_count"] == 6
    assert lanes[0]["sample_count"] == 6
    assert lanes[0]["non_alpha_count"] == 2
    assert lanes[0]["unfilled_or_unrealized_count"] == 1
    assert lanes[0]["operational_failure_pre_fill_count"] == 1
    assert lanes[0]["execution_quality_count"] == 8
    assert lanes[0]["alpha_conversion_status"] == (
        "blocked_by_fill_or_execution_evidence"
    )
    assert "repair fill evidence" in lanes[0]["alpha_conversion_repair_hint"]
    assert lanes[0]["cost_precision_counts"]["recorded"] == 4
    assert lanes[0]["cost_precision_counts"]["hybrid"] == 1
    assert lanes[0]["cost_precision_counts"]["partial"] == 1
    assert lanes[0]["missing_cost_component_counts"] == {
        "slippage": 1,
        "spread": 1,
    }
    assert lanes[0]["present_cost_component_counts"]["fees"] == 6
    assert lanes[0]["required_cost_component_counts"]["spread"] == 6
    assert lanes[0]["cost_precision_reason_counts"] == {
        "recorded_cost_missing_required_components": 1,
    }
    assert lanes[0]["cost_repair_targets"] == [
        "record_missing_cost_component:slippage",
        "record_missing_cost_component:spread",
        "replace_hybrid_cost_estimates_with_recorded_fill_book_evidence",
        "replace_estimated_costs_with_recorded_execution_costs",
        "complete_partial_cost_components_before_size_increase",
    ]
    assert lanes[0]["validation_pressure_severity_counts"] == {"risk_off": 2}
    assert lanes[0]["validation_pressure_entry_posture_counts"] == {
        "patient_waiting_entry": 2
    }
    assert lanes[0]["validation_pressure_sizing_posture_counts"] == {
        "fractional_small_only": 2
    }
    assert lanes[0]["validation_pressure_fail_id_counts"] == {
        "cost_simulation": 1
    }
    assert lanes[0]["validation_pressure_warn_id_counts"] == {"monte_carlo": 1}
    assert lanes[0]["validation_pressure_missing_id_counts"] == {
        "walk_forward_analysis": 1
    }
    assert lanes[0]["validation_pressure_discipline_action_counts"] == {
        "cost_simulation:fail": 1,
        "monte_carlo:warn": 1,
    }
    assert lanes[0]["cost_hybrid_alpha_count"] == 1
    assert lanes[0]["cost_hybrid_alpha_net_pnl"] == -0.12
    assert compact["performance_lane_policy"]["role"] == "realized lane feedback"


def test_compact_validation_lane_scorecards_prioritizes_repair_lanes() -> None:
    noisy_actions = {
        f"lane_{index}": {
            "grade": "qualified",
            "action": "normal_or_selective_press",
            "sample_count": 20,
        }
        for index in range(20)
    }
    noisy_actions["futures:long:validation:correlation"] = {
        "grade": "restricted",
        "action": "de_risk_or_waiting_entry",
        "sample_count": 5,
        "requires_waiting_entry": True,
    }

    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "trading_validation": {
                "payload": {
                    "summary": {"readiness": "normal"},
                    "metrics": {
                        "lane_scorecards": {
                            "version": "lane_scorecards_v1",
                            "weak_lanes": [
                                *(f"lane_{index}" for index in range(20)),
                                "futures:long:validation:correlation",
                            ],
                            "lane_actions": noisy_actions,
                        }
                    },
                }
            },
            "validation_gate": {
                "status": "validation_normal",
                "readiness": "normal",
            },
        }
    )

    lanes = compact["validation_gate"]["lane_scorecards"]
    assert list(lanes["lane_actions"])[0] == (
        "futures:long:validation:correlation"
    )
    assert "futures:long:validation:correlation" in lanes["lane_actions"]
    assert lanes["weak_lanes"][0] == "futures:long:validation:correlation"


def test_validation_risk_governor_demotes_diagnostic_halt_when_core_passes() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "normal",
                "pass_count": 3,
                "warn_count": 0,
                "fail_count": 10,
                "missing_count": 0,
                "hard_fail_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
            },
            "payload": {
                "summary": {
                    "readiness": "normal",
                    "hard_fail_count": 0,
                    "core_fail_count": 0,
                    "core_missing_count": 0,
                },
                "metrics": {
                    "ruin_profile": {"governor_action": "halt_new_risk"},
                    "drawdown_budget": {"governor_action": "normal"},
                    "kelly_sizing": {"governor_action": "halt_new_risk"},
                },
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gate["status"] == "validation_probe"
    assert gate["risk_governor_action"] == "de_risk"
    assert gate["applied_max_budget_multiplier"] == 0.5


def test_validation_risk_governor_demotes_diagnostic_halt_to_derisk_not_risk_off() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "probe",
                "pass_count": 9,
                "warn_count": 10,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
                "active_revision_sample_mode": "active_revision_sample_building",
                "active_revision_sample_count": 5,
            },
            "payload": {
                "summary": {
                    "readiness": "probe",
                    "hard_fail_count": 0,
                    "core_fail_count": 0,
                    "core_missing_count": 0,
                    "active_revision_sample_mode": "active_revision_sample_building",
                    "active_revision_sample_count": 5,
                },
                "metrics": {
                    "ruin_profile": {"governor_action": "normal"},
                    "drawdown_budget": {"governor_action": "normal"},
                    "kelly_sizing": {
                        "cap_reason": "no_positive_edge",
                        "recommended_risk_fraction": 0.0,
                        "max_risk_cap_fraction": 0.02,
                    },
                },
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gate["status"] == "validation_probe"
    assert gate["risk_governor_action"] == "de_risk"
    assert gate["applied_max_budget_multiplier"] == 0.5
    assert gated["max_budget_multiplier"] == 0.5
    assert "hard_gate_clear:diagnostic_halt_demoted_to_de_risk" in gate[
        "risk_governor_reasons"
    ]


def test_active_revision_gate_uses_half_budget_for_sample_building_probe() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "mid",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 30,
                "win_rate": 58.0,
                "profit_factor": 1.8,
                "max_drawdown_pct": -1.4,
                "recovery_factor": 1.4,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_active_revision_evidence_gate(
        packet,
        {
            "version": "active_revision_evidence_v1",
            "venue": "kis",
            "strategy_revision_id": "jue_edge_repair_v1",
            "status": "active_revision_sample_building",
            "active_sample_count": 5,
            "effective_sample_count": 5,
            "pending_block_count": 2,
            "min_samples_to_scale": 10,
            "scale_up_allowed": False,
        },
    )

    action = gated["lane_authority"]["lane_actions"]["mid"]
    assert gated["allow_scale_up"] is False
    assert gated["max_budget_multiplier"] == 0.5
    assert gated["lane_authority"]["active_revision_gate"]["cap_multiplier"] == 0.5
    assert action["applied_max_budget_multiplier"] == 0.5


def test_compact_live_authority_for_prompt_preserves_validation_recovery_focus() -> None:
    payload = {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.5,
        "validation_gate": {
            "status": "blocked_by_validation",
            "readiness": "blocked_by_validation",
            "reason": "readiness=blocked_by_validation, fail_count=6",
            "risk_governor_action": "halt_new_risk",
            "failed_disciplines": [
                {
                    "id": "monte_carlo",
                    "label": "몬테카를로 시뮬레이션",
                    "status": "fail",
                    "action": "sequence risk를 낮추기 전까지 size-up 금지" * 20,
                }
            ],
            "loss_cooldown": {
                "symbols": [
                    {
                        "symbol": "ZECUSDT",
                        "risk_score": 55.14,
                        "action": "do_not_scale_or_create_live_entry_without_new_evidence",
                    }
                ],
                "groups": [
                    {
                        "group_type": "lane",
                        "group": "futures_short",
                        "risk_score": 41.0,
                        "action": "deprioritize_until_revalidated",
                    }
                ],
            },
            "operator_guidance": [
                "몬테카를로: sequence risk를 낮추기 전까지 size-up 금지",
            ],
            "remediation_plan": {
                "status": "blocked",
                "primary_next_action": "rolling WFA window 재생성 후 OOS 재검증",
                "weak_count": 5,
                "failed_count": 6,
                "categories": [
                    {
                        "id": "research_validation_work",
                        "label": "연구/백테스트 보강",
                        "items": [
                            {
                                "discipline_id": "walk_forward_analysis",
                                "label": "Walk Forward Analysis",
                                "status": "fail",
                                "action": "rolling WFA window 재생성 후 OOS 재검증",
                            }
                        ],
                    }
                ],
                "work_queue": [
                    {
                        "task_id": "validation:walk_forward_analysis:fail",
                        "discipline_id": "walk_forward_analysis",
                        "status": "fail",
                        "priority": "p0",
                        "owner": "pattern_lab",
                        "cadence": "next_research_cycle",
                        "lane_policy_hint": "shadow_or_waiting_only_until_wfa_rebuilt",
                        "blocks_scaling": "no_scale_up_until_wfa_oos_clean",
                        "blocks_new_entries": "scale_up_and_unvalidated_immediate_entries",
                        "runner_hint": "crypto_pattern_lab then refresh_trading_validation",
                        "verification_artifact": "active strategy set has WFA and OOS evidence",
                        "exit_criteria": "WFA/OOS returns to pass",
                        "pass_path": {
                            "version": "validation_pass_path_v1",
                            "current_gap": "evidence_failed_threshold",
                            "collection_hook": "pattern_lab_rebuild_wfa_oos",
                            "pass_criteria": "WFA/OOS returns to pass",
                            "jue_behavior_until_pass": {
                                "allowed_entry_posture": "shadow_or_waiting_entry_only",
                                "scale_up_blocked": True,
                            },
                        },
                    }
                ],
                "pass_path_summary": {
                    "version": "validation_pass_path_summary_v1",
                    "scale_up_blocked_count": 1,
                    "automation_hooks": ["pattern_lab_rebuild_wfa_oos"],
                },
            },
        },
    }

    compact = compact_live_authority_for_prompt(payload)

    gate = compact["validation_gate"]
    assert gate["status"] == "blocked_by_validation"
    assert gate["failed_disciplines"][0]["id"] == "monte_carlo"
    assert gate["loss_cooldown"]["symbols"][0]["symbol"] == "ZECUSDT"
    assert gate["operator_guidance"][0].startswith("몬테카를로")
    assert gate["remediation_plan"]["primary_next_action"] == (
        "rolling WFA window 재생성 후 OOS 재검증"
    )
    work = gate["remediation_plan"]["work_queue"][0]
    assert work["discipline_id"] == "walk_forward_analysis"
    assert work["blocks_new_entries"] == "scale_up_and_unvalidated_immediate_entries"
    assert "refresh_trading_validation" in work["runner_hint"]
    assert "WFA" in work["verification_artifact"]
    assert work["pass_path"]["collection_hook"] == "pattern_lab_rebuild_wfa_oos"
    assert work["pass_path"]["jue_behavior_until_pass"]["scale_up_blocked"] is True
    assert gate["remediation_plan"]["pass_path_summary"][
        "scale_up_blocked_count"
    ] == 1


def test_compact_live_authority_for_prompt_preserves_failure_attribution() -> None:
    payload = {
        "status": "ok",
        "live_grade": "restricted",
        "validation_gate": {
            "status": "validation_probe",
            "readiness": "probe",
            "failure_attribution": {
                "status": "ok",
                "sample_count": 51,
                "recovery_focus": [
                    "symbol=NIGHTUSDT net -3.20, PF 0.00, expectancy -1.85%",
                ],
                "worst_groups": [
                    {
                        "group_type": "symbol",
                        "group": "NIGHTUSDT",
                        "total_net_pnl": -3.2,
                        "profit_factor": 0.0,
                        "risk_score": 48.7,
                        "expectancy_pct": -1.85,
                    }
                ],
            },
        },
    }

    compact = compact_live_authority_for_prompt(payload)

    attribution = compact["validation_gate"]["failure_attribution"]
    assert attribution["recovery_focus"][0].startswith("symbol=NIGHTUSDT")
    assert attribution["worst_groups"][0]["group"] == "NIGHTUSDT"
    assert attribution["worst_groups"][0]["risk_score"] == 48.7


def test_compact_live_authority_for_prompt_includes_full_validation_matrix() -> None:
    raw_marker = "RAW_VALIDATION_DIAGNOSTICS"
    payload = {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.0,
        "trading_validation": {
            "payload": {
                "summary": {
                    "total_score": 42.5,
                    "readiness": "blocked_by_validation",
                    "pass_count": 1,
                    "warn_count": 1,
                    "fail_count": 1,
                    "missing_count": 0,
                },
                "disciplines": [
                    {
                        "id": "data_validation",
                        "label": "데이터 검증",
                        "status": "pass",
                        "action": "data looks usable",
                    },
                    {
                        "id": "monte_carlo",
                        "label": "몬테카를로 시뮬레이션",
                        "status": "fail",
                        "action": "sequence risk 축소",
                    },
                    {
                        "id": "kelly_sizing",
                        "label": "켈리 공식",
                        "status": "warn",
                        "action": "fractional sizing only",
                    },
                ],
                "raw_diagnostics": raw_marker * 30,
            }
        },
        "validation_gate": {
            "status": "blocked_by_validation",
            "readiness": "blocked_by_validation",
            "reason": "readiness=blocked_by_validation, fail_count=1",
            "discipline_count": 3,
            "expected_discipline_count": 19,
        },
    }

    compact = compact_live_authority_for_prompt(payload)

    gate = compact["validation_gate"]
    matrix = gate["discipline_matrix"]
    assert raw_marker not in str(compact)
    assert matrix["expected_count"] == 19
    assert matrix["summary"]["readiness"] == "blocked_by_validation"
    assert matrix["summary"]["fail_count"] == 1
    matrix_status_ids = [row["id"] for row in matrix["statuses"]]
    assert matrix_status_ids[:3] == [
        "data_validation",
        "monte_carlo",
        "kelly_sizing",
    ]
    assert len(matrix_status_ids) == 19
    assert "walk_forward_analysis" in matrix_status_ids
    assert matrix["statuses"][0]["status"] == "pass"
    assert matrix["statuses"][1]["status"] == "fail"
    assert matrix["statuses"][2]["status"] == "warn"
    assert any(
        row["id"] == "walk_forward_analysis" and row["status"] == "missing"
        for row in matrix["statuses"]
    )


def test_compact_live_authority_for_prompt_includes_active_revision_evidence() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "restricted",
            "allow_scale_up": False,
            "max_budget_multiplier": 0.25,
            "active_revision_evidence": {
                "version": "active_revision_evidence_v1",
                "venue": "binance",
                "strategy_revision_id": "jue_edge_repair_v1",
                "status": "no_active_revision_samples",
                "validation_sample_role": "legacy_proxy_metrics_no_scale",
                "legacy_proxy_gate_mode": "probe_only",
                "authority_posture": "observe_only_until_new_revision_trades_close",
                "active_sample_count": 0,
                "effective_sample_count": 0,
                "validation_sample_count": 0,
                "legacy_proxy_sample_count": 18,
                "lane_alpha_count": 0,
                "min_samples_to_scale": 20,
                "scorecard_count": 0,
                "performance_lane_count": 0,
                "validation_fail_count": 0,
                "validation_missing_count": 16,
                "hard_blocking_count": 3,
                "scale_up_allowed": False,
                "can_scale_from_proxy": False,
                "block_design_requirement": (
                    "새 revision 표본이 쌓일 때까지 대기진입/소액 검증만 허용"
                ),
                "next_action": "collect_active_revision_probe_samples_before_scaling",
                "legacy_proxy_failed_discipline_ids": ["cost_simulation", "kelly_sizing"],
                "raw_evidence_rows": ["SHOULD_NOT_BE_IN_PROMPT"] * 100,
            },
        }
    )

    evidence = compact["active_revision_evidence"]
    assert evidence == {
        "version": "active_revision_evidence_v1",
        "venue": "binance",
        "strategy_revision_id": "jue_edge_repair_v1",
        "status": "no_active_revision_samples",
        "validation_sample_role": "legacy_proxy_metrics_no_scale",
        "legacy_proxy_gate_mode": "probe_only",
        "authority_posture": "observe_only_until_new_revision_trades_close",
        "active_sample_count": 0,
        "effective_sample_count": 0,
        "validation_sample_count": 0,
        "legacy_proxy_sample_count": 18,
        "lane_alpha_count": 0,
        "min_samples_to_scale": 20,
        "scorecard_count": 0,
        "performance_lane_count": 0,
        "validation_fail_count": 0,
        "validation_missing_count": 16,
        "hard_blocking_count": 3,
        "scale_up_allowed": False,
        "can_scale_from_proxy": False,
        "block_design_requirement": (
            "새 revision 표본이 쌓일 때까지 대기진입/소액 검증만 허용"
        ),
        "next_action": "collect_active_revision_probe_samples_before_scaling",
        "legacy_proxy_failed_discipline_ids": ["cost_simulation", "kelly_sizing"],
    }
    assert "SHOULD_NOT_BE_IN_PROMPT" not in str(compact)


def test_compact_live_authority_for_prompt_includes_validation_passport() -> None:
    payload = {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.0,
        "trading_validation": {
            "payload": {
                "summary": {
                    "total_score": 42.5,
                    "readiness": "blocked_by_validation",
                    "pass_count": 16,
                    "warn_count": 2,
                    "fail_count": 1,
                    "missing_count": 0,
                },
                "disciplines": [
                    {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                    {"id": "monte_carlo", "label": "몬테카를로", "status": "fail"},
                    {"id": "kelly_sizing", "label": "켈리 공식", "status": "warn"},
                ],
            }
        },
        "validation_gate": {
            "status": "blocked_by_validation",
            "readiness": "blocked_by_validation",
            "reason": "readiness=blocked_by_validation, fail_count=1",
            "discipline_count": 3,
            "expected_discipline_count": 19,
            "risk_governor_action": "halt_new_risk",
            "risk_governor_source": "ruin_profile",
        },
    }

    compact = compact_live_authority_for_prompt(payload)

    passport = compact["validation_gate"]["validation_passport"]
    assert passport == {
        "version": "trading_validation_passport_v1",
        "status": "blocked_by_validation",
        "readiness": "blocked_by_validation",
        "score": 42.5,
        "expected_count": 19,
        "actual_count": 19,
        "row_detail_count": 3,
        "row_detail_complete": False,
        "is_complete": True,
        "pass_count": 16,
        "warn_count": 2,
        "fail_count": 1,
        "missing_count": 0,
        "failed_ids": ["monte_carlo"],
        "weak_ids": ["monte_carlo", "kelly_sizing"],
        "risk_governor_action": "halt_new_risk",
        "risk_governor_source": "ruin_profile",
        "requires_revalidation": True,
    }


def test_compact_live_authority_for_prompt_builds_passport_from_summary_only() -> None:
    payload = {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.0,
        "trading_validation": {
            "status": "ok",
            "summary": {
                "total_score": 70.0,
                "readiness": "blocked_by_validation",
                "pass_count": 12,
                "warn_count": 4,
                "fail_count": 1,
                "missing_count": 2,
            },
        },
        "validation_gate": {
            "status": "blocked_by_validation",
            "readiness": "blocked_by_validation",
            "reason": "readiness=blocked_by_validation, fail_count=1",
            "discipline_count": 0,
            "expected_discipline_count": 19,
        },
    }

    compact = compact_live_authority_for_prompt(payload)

    matrix = compact["validation_gate"]["discipline_matrix"]
    passport = compact["validation_gate"]["validation_passport"]
    assert compact["validation_gate"]["discipline_count"] == 19
    assert matrix == {
        "expected_count": 19,
        "actual_count": 19,
        "row_detail_count": 0,
        "row_detail_complete": False,
        "summary": {
            "score": 70.0,
            "readiness": "blocked_by_validation",
            "pass_count": 12,
            "warn_count": 4,
            "fail_count": 1,
            "missing_count": 2,
        },
    }
    assert passport == {
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


def test_summary_only_scale_ready_validation_is_not_marked_incomplete() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_momentum",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 30,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "total_score": 91.0,
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert gated["validation_gate"]["status"] == "clear"
    assert gated["validation_gate"]["discipline_count"] == 19
    assert gated["validation_gate"]["discipline_matrix"]["actual_count"] == 19
    assert gated["validation_gate"]["validation_passport"]["is_complete"] is True
    assert gated["validation_gate"]["validation_passport"]["requires_revalidation"] is False


def test_compact_live_authority_for_prompt_preserves_repair_execution() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "restricted",
            "live_grade": "restricted",
            "allow_scale_up": False,
            "repair_execution": {
                "version": "validation_repair_execution_compact_v1",
                "source_version": "validation_repair_execution_v1",
                "venue": "binance",
                "status": "queued",
                "item_count": 1,
                "executed_count": 0,
                "queued_count": 1,
                "m1_execution_posture": "sequential_priority_queue",
                "actions": [
                    {
                        "discipline_id": "walk_forward_analysis",
                        "priority": "p0",
                        "status": "queued_external_runner",
                        "validation_mode": "backtest_wfa_oos_rebuild",
                        "scale_up_blocked": True,
                        "live_shadow_required": True,
                        "artifact": "crypto_pattern_lab_runner",
                        "reason": "WFA/OOS evidence must be rebuilt before scale-up.",
                        "runner_status": "missing",
                        "active_optimized_set_count": 0,
                        "evidence_status": "insufficient_evidence",
                        "evidence_reasons": ["out_of_sample_missing"],
                        "discipline_status": "fail",
                        "recommended_risk_fraction": 0.0,
                        "risk_of_ruin_pct": 8.5,
                        "profit_factor": 1.01,
                        "recovery_factor": 0.2,
                    }
                ],
            },
        }
    )

    repair = compact["repair_execution"]
    assert repair["status"] == "queued"
    assert repair["queued_count"] == 1
    assert repair["m1_execution_posture"] == "sequential_priority_queue"
    assert repair["actions"][0]["discipline_id"] == "walk_forward_analysis"
    assert repair["actions"][0]["validation_mode"] == "backtest_wfa_oos_rebuild"
    assert repair["actions"][0]["scale_up_blocked"] is True
    assert repair["actions"][0]["live_shadow_required"] is True
    assert repair["actions"][0]["active_optimized_set_count"] == 0
    assert repair["actions"][0]["evidence_status"] == "insufficient_evidence"
    assert "out_of_sample_missing" in repair["actions"][0]["evidence_reasons"]
    assert repair["actions"][0]["discipline_status"] == "fail"
    assert repair["actions"][0]["risk_of_ruin_pct"] == 8.5
    assert repair["actions"][0]["profit_factor"] == 1.01
    assert repair["actions"][0]["recovery_factor"] == 0.2


def test_partial_validation_rows_do_not_reduce_complete_summary_count() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "qualified",
            "allow_scale_up": True,
            "trading_validation": {
                "payload": {
                    "summary": {
                        "total_score": 88.0,
                        "readiness": "scale_ready",
                        "pass_count": 19,
                        "warn_count": 0,
                        "fail_count": 0,
                        "missing_count": 0,
                    },
                    "disciplines": [
                        {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                        {"id": "monte_carlo", "label": "몬테카를로", "status": "pass"},
                    ],
                }
            },
            "validation_gate": {
                "status": "clear",
                "readiness": "scale_ready",
                "discipline_count": 19,
                "expected_discipline_count": 19,
            },
        }
    )

    matrix = compact["validation_gate"]["discipline_matrix"]
    passport = compact["validation_gate"]["validation_passport"]
    assert matrix["actual_count"] == 19
    assert matrix["row_detail_count"] == 2
    assert matrix["row_detail_complete"] is False
    assert len(matrix["statuses"]) == 2
    assert passport["is_complete"] is True
    assert passport["row_detail_count"] == 2
    assert passport["row_detail_complete"] is False
    assert passport["requires_revalidation"] is False


def test_compact_validation_matrix_surfaces_absent_rows_when_packet_is_incomplete() -> None:
    compact = compact_live_authority_for_prompt(
        {
            "status": "ok",
            "live_grade": "restricted",
            "allow_scale_up": False,
            "trading_validation": {
                "payload": {
                    "summary": {
                        "total_score": 15.79,
                        "readiness": "probe",
                        "pass_count": 3,
                        "warn_count": 0,
                        "fail_count": 0,
                        "missing_count": 0,
                    },
                    "disciplines": [
                        {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                        {"id": "walk_forward_analysis", "label": "WFA", "status": "pass"},
                        {"id": "monte_carlo", "label": "몬테카를로", "status": "pass"},
                    ],
                }
            },
            "validation_gate": {
                "status": "validation_incomplete",
                "readiness": "probe",
                "discipline_count": 3,
                "expected_discipline_count": 19,
            },
        }
    )

    matrix = compact["validation_gate"]["discipline_matrix"]
    passport = compact["validation_gate"]["validation_passport"]
    assert matrix["actual_count"] == 3
    assert matrix["expected_count"] == 19
    assert matrix["summary"]["pass_count"] == 3
    assert matrix["summary"]["missing_count"] == 16
    assert len(matrix["statuses"]) == 19
    assert any(
        row["id"] == "sharpe_ratio" and row["status"] == "missing"
        for row in matrix["statuses"]
    )
    assert passport["is_complete"] is False
    assert passport["missing_count"] == 16
    assert passport["requires_revalidation"] is True
    assert "sharpe_ratio" in passport["weak_ids"]


def test_apply_trading_validation_gate_exposes_discipline_matrix() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_momentum",
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "sample_count": 30,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "payload": {
                "summary": {
                    "total_score": 66.0,
                    "readiness": "blocked_by_validation",
                    "pass_count": 1,
                    "warn_count": 1,
                    "fail_count": 1,
                    "missing_count": 0,
                },
                "disciplines": [
                    {
                        "id": "data_validation",
                        "label": "데이터 검증",
                        "status": "pass",
                    },
                    {
                        "id": "monte_carlo",
                        "label": "몬테카를로 시뮬레이션",
                        "status": "fail",
                        "action": "sequence risk 축소",
                    },
                    {
                        "id": "kelly_sizing",
                        "label": "켈리 공식",
                        "status": "warn",
                    },
                ],
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    matrix = gated["validation_gate"]["discipline_matrix"]
    assert matrix["expected_count"] == 19
    assert matrix["actual_count"] == 3
    assert matrix["summary"]["score"] == 66.0
    assert matrix["summary"]["readiness"] == "blocked_by_validation"
    matrix_status_ids = [row["id"] for row in matrix["statuses"]]
    assert matrix_status_ids[:3] == [
        "data_validation",
        "monte_carlo",
        "kelly_sizing",
    ]
    assert len(matrix_status_ids) == 19
    assert any(
        row["id"] == "walk_forward_analysis" and row["status"] == "missing"
        for row in matrix["statuses"]
    )


def test_no_scorecards_defaults_to_observe_only() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert packet["live_grade"] == "observe_only"
    assert packet["max_budget_multiplier"] < 1.0


def test_lane_authority_surfaces_validation_evidence_repair_lanes() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures:long",
                "evidence_key": "ema_trend",
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "sample_count": 14,
                "expectancy_pct": 0.6,
                "win_rate": 57.0,
                "profit_factor": 1.8,
                "validation_evidence_status": "missing",
                "validation_missing_dimensions": [
                    "backtest",
                    "walk_forward",
                    "out_of_sample",
                    "live_shadow",
                ],
                "validation_evidence_required_evidence": [
                    "funding",
                    "spread",
                    "slippage",
                ],
                "validation_evidence_required_checks": ["positive_net_edge"],
                "validation_evidence_pass_collection_hooks": [
                    "sync futures fills/funding -> refresh_trading_validation",
                ],
                "validation_evidence_pass_current_gaps": [
                    "live shadow and funding adjusted edge missing",
                ],
                "validation_evidence_pass_criteria": [
                    "net edge remains positive after funding and spread stress",
                ],
                "validation_evidence_verification_artifacts": [
                    "futures funding/spread/slippage proof packet",
                ],
                "scale_blocked_by_validation_evidence": True,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    authority = packet["lane_authority"]
    lane = "futures:long:ema_trend"
    action = authority["lane_actions"][lane]
    assert lane in authority["validation_evidence_weak_lanes"]
    assert lane in authority["weak_lanes"]
    assert action["action"] == "validation_evidence_repair_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["validation_evidence_status"] == "missing"
    assert "backtest" in action["validation_missing_dimensions"]
    assert action["validation_evidence_repair_hint"] == (
        "rebuild_backtest_wfa_oos_before_scale_up"
    )
    assert action["core_validation_evidence_gaps"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert action["validation_evidence_repair_targets"] == [
        "rerun_backtest_before_scale_up",
        "rebuild_walk_forward_windows_before_scale_up",
        "pass_out_of_sample_validation_before_scale_up",
        "collect_live_shadow_samples_before_scale_up",
        "rebuild_backtest_wfa_oos_before_scale_up",
    ]
    assert action["risk_budget_passport"][
        "validation_evidence_cap_multiplier"
    ] == 0.5
    assert action["risk_budget_passport"][
        "validation_evidence_required_evidence"
    ] == ["funding", "spread", "slippage"]
    assert action["risk_budget_passport"][
        "validation_evidence_required_checks"
    ] == ["positive_net_edge"]
    assert action["risk_budget_passport"][
        "validation_evidence_pass_collection_hooks"
    ] == ["sync futures fills/funding -> refresh_trading_validation"]
    assert action["risk_budget_passport"][
        "validation_evidence_pass_current_gaps"
    ] == ["live shadow and funding adjusted edge missing"]
    assert action["risk_budget_passport"][
        "validation_evidence_pass_criteria"
    ] == ["net edge remains positive after funding and spread stress"]
    assert "validation_backtest_wfa_oos_shadow_cap" in action["scale_blockers"]
    assert "collect_live_shadow_samples_before_scale_up" in action[
        "scale_repair_targets"
    ]
    assert "validation_evidence_required_before_lane_scale_up" in (
        authority["block_design_requirements"]
    )
    assert action["risk_budget_passport"][
        "scale_blocked_by_validation_evidence"
    ] is True

    compact = compact_live_authority_for_prompt(packet)
    compact_authority = compact["lane_authority"]
    compact_action = compact_authority["lane_actions"][lane]
    assert lane in compact_authority["validation_evidence_weak_lanes"]
    assert compact_action["validation_evidence_status"] == "missing"
    assert "backtest" in compact_action["validation_missing_dimensions"]
    assert compact_action["validation_evidence_repair_hint"] == (
        "rebuild_backtest_wfa_oos_before_scale_up"
    )
    assert compact_action["validation_evidence_required_evidence"] == [
        "funding",
        "spread",
        "slippage",
    ]
    assert compact_action["validation_evidence_pass_collection_hooks"] == [
        "sync futures fills/funding -> refresh_trading_validation"
    ]
    assert compact_action["core_validation_evidence_gaps"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert "pass_out_of_sample_validation_before_scale_up" in compact_action[
        "validation_evidence_repair_targets"
    ]
    assert compact_action["scale_blocked_by_validation_evidence"] is True


def test_lane_authority_infers_validation_repair_from_missing_dimensions() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 24,
                "expectancy_pct": 0.8,
                "win_rate": 60.0,
                "max_drawdown_pct": -1.2,
                "profit_factor": 2.0,
                "recovery_factor": 1.5,
                "risk_of_ruin_pct": 3.0,
                "validation_evidence_status": "missing",
                "validation_missing_dimensions": [
                    "walk_forward",
                    "out_of_sample",
                    "live_shadow",
                ],
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    authority = packet["lane_authority"]
    action = authority["lane_actions"]["spot"]
    passport = action["risk_budget_passport"]

    assert authority["scale_candidate_lanes"] == []
    assert authority["validation_evidence_weak_lanes"] == ["spot"]
    assert action["action"] == "validation_evidence_repair_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["scale_blocked_by_validation_evidence"] is True
    assert action["validation_evidence_repair_hint"] == (
        "rebuild_backtest_wfa_oos_before_scale_up"
    )
    assert passport["validation_evidence_cap_multiplier"] == 0.5
    assert passport["scale_blocked_by_validation_evidence"] is True
    assert passport["validation_missing_dimensions"] == [
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert passport["core_validation_evidence_gaps"] == [
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert passport["validation_evidence_repair_targets"] == [
        "rebuild_walk_forward_windows_before_scale_up",
        "pass_out_of_sample_validation_before_scale_up",
        "collect_live_shadow_samples_before_scale_up",
        "rebuild_backtest_wfa_oos_before_scale_up",
    ]
    assert "validation_backtest_wfa_oos_shadow_cap" in passport["scale_blockers"]


def test_lane_authority_treats_thin_validation_evidence_as_repair_gate() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 24,
                "expectancy_pct": 0.8,
                "win_rate": 60.0,
                "max_drawdown_pct": -1.2,
                "profit_factor": 2.0,
                "recovery_factor": 1.5,
                "risk_of_ruin_pct": 3.0,
                "validation_evidence_status": "thin",
                "validation_thin_dimensions": [
                    "backtest",
                    "walk_forward",
                    "out_of_sample",
                    "live_shadow",
                ],
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    authority = packet["lane_authority"]
    action = authority["lane_actions"]["spot"]
    passport = action["risk_budget_passport"]

    assert authority["scale_candidate_lanes"] == []
    assert authority["validation_evidence_weak_lanes"] == ["spot"]
    assert action["action"] == "validation_evidence_repair_waiting_probe"
    assert action["requires_waiting_entry"] is True
    assert action["validation_evidence_status"] == "thin"
    assert action["validation_thin_dimensions"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert action["core_validation_evidence_gaps"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert passport["validation_evidence_cap_multiplier"] == 0.5
    assert passport["scale_blocked_by_validation_evidence"] is True
    assert passport["validation_thin_dimensions"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert "collect_live_shadow_samples_before_scale_up" in passport[
        "validation_evidence_repair_targets"
    ]


def test_validation_probe_is_visible_even_without_scale_multiplier() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot_probe",
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "sample_count": 12,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "probe",
                "pass_count": 4,
                "warn_count": 5,
                "fail_count": 0,
                "missing_count": 10,
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert gated["max_budget_multiplier"] == 1.0
    assert gated["allow_scale_up"] is False
    assert gated["validation_gate"]["status"] == "validation_probe"
    assert gated["validation_gate"]["readiness"] == "probe"
    assert gated["validation_gate"]["applied_max_budget_multiplier"] == 1.0


def test_stale_validation_blocks_scale_up_even_when_readiness_is_scale_ready() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_breakout",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 20,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "stale": True,
            "stale_reason": "age_sec=3601,max_age_sec=1800",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 15,
                "warn_count": 2,
                "fail_count": 0,
                "missing_count": 2,
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert gated["allow_scale_up"] is False
    assert gated["max_budget_multiplier"] == 0.5
    assert gated["validation_gate"]["status"] == "validation_stale"
    assert gated["validation_gate"]["readiness"] == "scale_ready"
    assert gated["validation_gate"]["reason"] == "age_sec=3601,max_age_sec=1800"


def test_incomplete_19_discipline_packet_blocks_scale_up_even_when_scale_ready() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_breakout",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 20,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 3,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
            "payload": {
                "disciplines": [
                    {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                    {
                        "id": "walk_forward_analysis",
                        "label": "Walk Forward Analysis",
                        "status": "pass",
                    },
                    {
                        "id": "monte_carlo",
                        "label": "몬테카를로 시뮬레이션",
                        "status": "pass",
                    },
                ]
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gated["allow_scale_up"] is False
    assert gated["max_budget_multiplier"] == 0.5
    assert gate["status"] == "validation_incomplete"
    assert gate["discipline_count"] == 3
    assert gate["expected_discipline_count"] == 19
    assert gate["reason"] == "discipline_count=3,expected=19"


def test_validation_gate_carries_actionable_failure_context_from_payload() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "kr_equity",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 20,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 11,
                "warn_count": 5,
                "fail_count": 3,
                "missing_count": 0,
            },
            "payload": {
                "disciplines": [
                    {
                        "id": "monte_carlo",
                        "label": "몬테카를로 시뮬레이션",
                        "status": "fail",
                        "action": "Reduce sequence risk before scale-up.",
                    },
                    {
                        "id": "stress_test",
                        "label": "스트레스 테스트",
                        "status": "fail",
                        "action": "Crisis drawdown breaches the operating band.",
                    },
                    {
                        "id": "cost_simulation",
                        "label": "거래비용 시뮬레이션",
                        "status": "warn",
                        "action": "Keep slippage assumptions fresh.",
                    },
                ],
                "metrics": {
                    "capacity": {
                        "status": "fail",
                        "capacity_method": "metadata_capacity_ratio",
                        "min_capacity_ratio": 0.79563,
                        "tightest_symbol": "023810",
                        "tightest_block_id": "blk_023810",
                    },
                    "failure_attribution": {
                        "status": "ok",
                        "recovery_focus": [
                            "symbol=034730 net -53062.65, PF 0.00, expectancy -7.97%",
                            "strategy_family=late_chase net -11000.00, PF 0.30",
                        ],
                        "worst_groups": [
                            {
                                "group_type": "symbol",
                                "group": "034730",
                                "total_net_pnl": -53062.65,
                                "profit_factor": 0.0,
                                "risk_score": 56.32,
                                "expectancy_pct": -7.97,
                            },
                            {
                                "group_type": "strategy_family",
                                "group": "late_chase",
                                "total_net_pnl": -11000.0,
                                "profit_factor": 0.3,
                                "risk_score": 31.0,
                                "expectancy_pct": -1.2,
                            }
                        ],
                    }
                },
                "operator_guidance": [
                    "몬테카를로: sequence risk를 낮추기 전까지 size-up 금지",
                    "용량 분석: 023810 체결 크기 축소",
                ],
                "remediation_plan": {
                    "status": "blocked",
                    "primary_next_action": "rolling WFA window 재생성",
                    "weak_count": 5,
                    "failed_count": 2,
                    "categories": [
                        {
                            "id": "research_validation_work",
                            "label": "연구/백테스트 보강",
                            "weak_count": 2,
                            "fail_count": 2,
                            "items": [
                                {
                                    "discipline_id": "walk_forward_analysis",
                                    "label": "Walk Forward Analysis",
                                    "status": "fail",
                                    "action": "rolling WFA window 재생성",
                                }
                            ],
                        },
                        {
                            "id": "sizing_risk_controls",
                            "label": "사이징/리스크 제한",
                            "weak_count": 2,
                            "fail_count": 1,
                            "items": [
                                {
                                    "discipline_id": "monte_carlo",
                                    "label": "몬테카를로 시뮬레이션",
                                    "status": "fail",
                                    "action": "sequence risk 축소",
                                }
                            ],
                        },
                    ],
                },
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gate["status"] == "blocked_by_validation"
    assert gate["failed_disciplines"] == [
        {
            "id": "monte_carlo",
            "label": "몬테카를로 시뮬레이션",
            "status": "fail",
            "action": "Reduce sequence risk before scale-up.",
        },
        {
            "id": "stress_test",
            "label": "스트레스 테스트",
            "status": "fail",
            "action": "Crisis drawdown breaches the operating band.",
        },
    ]
    assert gate["weak_disciplines"][0]["id"] == "monte_carlo"
    assert gate["weak_disciplines"][2]["id"] == "cost_simulation"
    assert gate["capacity_bottleneck"] == {
        "status": "fail",
        "capacity_method": "metadata_capacity_ratio",
        "min_capacity_ratio": 0.79563,
        "tightest_symbol": "023810",
        "tightest_block_id": "blk_023810",
    }
    assert gate["failure_attribution"]["recovery_focus"][0].startswith(
        "symbol=034730"
    )
    assert gate["failure_attribution"]["worst_groups"][0]["group"] == "034730"
    assert gate["loss_cooldown"]["symbols"][0] == {
        "symbol": "034730",
        "total_net_pnl": -53062.65,
        "profit_factor": 0.0,
        "expectancy_pct": -7.97,
        "risk_score": 56.32,
        "action": "do_not_scale_or_create_live_entry_without_new_evidence",
    }
    assert gate["loss_cooldown"]["groups"][0] == {
        "group_type": "strategy_family",
        "group": "late_chase",
        "total_net_pnl": -11000.0,
        "profit_factor": 0.3,
        "expectancy_pct": -1.2,
        "risk_score": 31.0,
        "action": "deprioritize_until_revalidated",
    }
    assert gate["operator_guidance"] == [
        "몬테카를로: sequence risk를 낮추기 전까지 size-up 금지",
        "용량 분석: 023810 체결 크기 축소",
    ]
    assert gate["remediation_plan"]["status"] == "blocked"
    assert gate["remediation_plan"]["primary_next_action"] == (
        "rolling WFA window 재생성"
    )
    assert gate["remediation_plan"]["categories"][0]["id"] == (
        "research_validation_work"
    )
    assert gate["remediation_plan"]["categories"][0]["items"][0] == {
        "discipline_id": "walk_forward_analysis",
        "label": "Walk Forward Analysis",
        "status": "fail",
        "action": "rolling WFA window 재생성",
    }


def test_validation_gate_surfaces_pattern_lab_recovery_focus() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_momentum",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 20,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 12,
                "warn_count": 4,
                "fail_count": 1,
                "missing_count": 2,
            },
            "payload": {
                "disciplines": [
                    {
                        "id": "walk_forward_analysis",
                        "label": "Walk-forward analysis",
                        "status": "fail",
                        "action": "Re-run rolling WFA before scale-up.",
                    }
                ],
                "metrics": {
                    "pattern_lab": {
                        "status": "ok",
                        "validation_status": "fail",
                        "source_scope": "crypto_pattern_lab",
                        "active_set_count": 1,
                        "active_missing_walk_forward_set_count": 1,
                        "active_walk_forward_coverage_rate_pct": 0.0,
                        "validation_reasons": [
                            "active_walk_forward_windows_missing"
                        ],
                    }
                },
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    focus = gated["validation_gate"]["validation_recovery_focus"]
    assert focus[0]["source"] == "pattern_lab"
    assert focus[0]["reason"] == "active_walk_forward_windows_missing"
    assert focus[0]["action"].startswith("Re-run rolling WFA")
    assert focus[0]["active_set_count"] == 1
    authority = gated["lane_authority"]
    action = authority["lane_actions"]["futures_momentum"]
    shadow_gate = action["validation_shadow_gate"]
    passport = action["risk_budget_passport"]
    assert authority["validation_shadow_gate"]["blocks_scale_up"] is True
    assert authority["shadow_blocked_lanes"] == ["futures_momentum"]
    assert action["scale_up_allowed"] is False
    assert action["scale_up_blocked_by_shadow_gate"] is True
    assert action["requires_waiting_entry"] is True
    assert action["action"] == "shadow_or_waiting_entry_until_validation_rebuilt"
    assert passport["validation_shadow_gate_status"] == (
        "revalidation_required_before_scale_up"
    )
    assert passport["validation_shadow_cap_multiplier"] == 1.0
    assert passport["effective_risk_budget_multiplier"] == 0.5
    assert shadow_gate["requires_live_shadow"] is True
    assert "active_walk_forward_windows_missing" in shadow_gate["focus_reasons"]
    assert (
        "live_shadow_or_oos_wfa_required_before_lane_scale_up"
        in authority["block_design_requirements"]
    )
    compact = compact_live_authority_for_prompt(gated)
    compact_action = compact["lane_authority"]["lane_actions"]["futures_momentum"]
    assert compact["lane_authority"]["shadow_blocked_lanes"] == ["futures_momentum"]
    assert (
        compact_action["validation_shadow_gate"]["scale_policy"]
        == "no_size_increase_until_backtest_wfa_oos_live_shadow_clear"
    )
    assert (
        compact_action["risk_budget_passport"]["validation_shadow_cap_multiplier"]
        == 1.0
    )
    assert (
        compact_action["risk_budget_passport"]["effective_risk_budget_multiplier"]
        == 0.5
    )
    assert compact_action["scale_up_blocked_by_shadow_gate"] is True


def test_validation_gate_halts_new_risk_when_ruin_governor_is_critical() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "kr_equity",
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "sample_count": 30,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 5,
                "warn_count": 1,
                "fail_count": 13,
                "missing_count": 0,
            },
            "payload": {
                "disciplines": [
                    {
                        "id": "monte_carlo",
                        "label": "몬테카를로 시뮬레이션",
                        "status": "fail",
                    },
                    {
                        "id": "risk_of_ruin",
                        "label": "파산확률",
                        "status": "fail",
                    },
                ],
                "metrics": {
                    "ruin_profile": {
                        "status": "fail",
                        "governor_action": "halt_new_risk",
                        "risk_of_ruin_pct": 46.8,
                    },
                    "drawdown_budget": {
                        "status": "warn",
                        "governor_action": "risk_off",
                        "drawdown_usage_ratio": 0.82,
                    },
                },
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gated["allow_scale_up"] is False
    assert gated["max_budget_multiplier"] == 0.0
    assert gate["risk_governor_action"] == "halt_new_risk"
    assert gate["applied_max_budget_multiplier"] == 0.0
    assert gate["risk_governor_reasons"] == [
        "ruin_profile:halt_new_risk",
        "drawdown_budget:risk_off",
    ]


def test_validation_governor_tail_risk_metrics_are_copied_to_lane_passport() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "core_etf",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 30,
                "win_rate": 58.0,
                "profit_factor": 1.9,
                "recovery_factor": 1.4,
                "max_drawdown_pct": -1.2,
                "cost_drag_pct_of_gross_pnl": 15.0,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 18,
                "warn_count": 1,
                "fail_count": 0,
                "missing_count": 0,
            },
            "payload": {
                "metrics": {
                    "ruin_profile": {
                        "status": "warn",
                        "governor_action": "risk_off",
                        "risk_of_ruin_pct": 11.7,
                    },
                    "kelly_sizing": {
                        "status": "warn",
                        "cap_reason": "validation_quality_warning_cap",
                        "recommended_risk_fraction": 0.006,
                        "max_risk_cap_fraction": 0.02,
                    },
                }
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    action = gated["lane_authority"]["lane_actions"]["core_etf"]
    passport = action["risk_budget_passport"]
    assert gated["max_budget_multiplier"] == 0.25
    assert action["applied_max_budget_multiplier"] == 0.25
    assert action["scale_up_allowed"] is False
    assert passport["validation_governor_action"] == "risk_off"
    assert passport["validation_risk_of_ruin_pct"] == 11.7
    assert passport["validation_recommended_risk_fraction"] == 0.006
    assert passport["validation_max_risk_cap_fraction"] == 0.02
    assert passport["validation_governor_cap_multiplier"] == 0.25
    assert passport["effective_risk_budget_multiplier"] == 0.25


def test_validation_gate_applies_kelly_quality_cap_even_when_scale_ready() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_momentum",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 80,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
            "payload": {
                "metrics": {
                    "kelly_sizing": {
                        "status": "warn",
                        "cap_reason": "validation_quality_missing_cap",
                        "recommended_risk_fraction": 0.01,
                        "max_risk_cap_fraction": 0.02,
                    }
                }
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gate = gated["validation_gate"]
    assert gate["status"] == "clear"
    assert gate["risk_governor_action"] == "de_risk"
    assert gate["risk_governor_source"] == "kelly_sizing"
    assert gate["applied_max_budget_multiplier"] == 0.5
    assert gated["max_budget_multiplier"] == 0.5
    assert gated["allow_scale_up"] is False


def test_validation_gate_uses_fractional_kelly_cap_ratio() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "volatile_attack",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 80,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
            "payload": {
                "metrics": {
                    "kelly_sizing": {
                        "status": "warn",
                        "cap_reason": "validation_quality_warning_cap",
                        "recommended_risk_fraction": 0.005,
                        "max_risk_cap_fraction": 0.02,
                    }
                }
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert gated["validation_gate"]["risk_governor_action"] == "de_risk"
    assert gated["validation_gate"]["applied_max_budget_multiplier"] == 0.25
    assert gated["max_budget_multiplier"] == 0.25
    assert gated["allow_scale_up"] is False


def test_validation_gate_applies_insufficient_sample_kelly_cap() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "new_edge_probe",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 80,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
            "payload": {
                "metrics": {
                    "kelly_sizing": {
                        "status": "warn",
                        "cap_reason": "insufficient_sample_cap",
                        "recommended_risk_fraction": 0.005,
                        "max_risk_cap_fraction": 0.02,
                    }
                }
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert gated["validation_gate"]["risk_governor_action"] == "de_risk"
    assert gated["validation_gate"]["risk_governor_source"] == "kelly_sizing"
    assert gated["validation_gate"]["applied_max_budget_multiplier"] == 0.25
    assert gated["max_budget_multiplier"] == 0.25
    assert gated["allow_scale_up"] is False


def test_validation_gate_turns_remediation_waiting_hint_into_execution_pressure() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "mid",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 40,
                "expectancy_pct": 0.8,
                "win_rate_pct": 60.0,
                "profit_factor": 1.9,
                "recovery_factor": 1.4,
                "risk_of_ruin_pct": 3.0,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    gated = apply_trading_validation_gate(
        packet,
        {
            "status": "ok",
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 19,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
                "hard_fail_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
            },
            "payload": {
                "summary": {
                    "readiness": "scale_ready",
                    "pass_count": 19,
                    "warn_count": 0,
                    "fail_count": 0,
                    "missing_count": 0,
                },
                "remediation_plan": {
                    "status": "needs_work",
                    "lane_policy_hints": {
                        "scale_up_allowed": False,
                        "entry_mode": "verified_waiting_probe",
                        "requires_shadow_or_waiting_entry": True,
                        "risk_budget_mode": "normal",
                    },
                },
            },
        },
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    pressure = gated["validation_gate"]["validation_pressure"]

    assert gated["validation_gate"]["status"] == "clear"
    assert pressure["severity"] == "remediation_waiting_probe"
    assert pressure["entry_posture"] == "patient_waiting_entry"
    assert pressure["remediation_entry_mode"] == "verified_waiting_probe"
    assert (
        "follow_remediation_waiting_probe_mode"
        in pressure["block_design_requirements"]
    )
    assert gated["validation_gate"]["applied_max_budget_multiplier"] == 0.5
    assert gated["max_budget_multiplier"] == 0.5
    assert gated["allow_scale_up"] is False

    authority = gated["lane_authority"]
    action = authority["lane_actions"]["mid"]
    passport = action["risk_budget_passport"]

    assert authority["validation_remediation_gate"]["status"] == (
        "remediation_required_before_scale_up"
    )
    assert authority["remediation_blocked_lanes"] == ["mid"]
    assert action["scale_up_blocked_by_validation_remediation"] is True
    assert action["requires_waiting_entry"] is True
    assert action["action"] in {
        "remediation_waiting_probe_until_validation_clears",
        "shadow_or_waiting_entry_until_validation_rebuilt",
    }
    assert "validation_remediation_gate" in action["scale_blockers"]
    assert (
        "respect_validation_remediation_work_queue_before_entry"
        in action["entry_quality_requirements"]
    )
    assert passport["validation_remediation_cap_multiplier"] == 0.5
    assert passport["scale_blocked_by_validation_remediation"] is True

    compact = compact_live_authority_for_prompt(gated)
    compact_authority = compact["lane_authority"]
    compact_action = compact_authority["lane_actions"]["mid"]
    assert compact_authority["validation_remediation_gate"]["status"] == (
        "remediation_required_before_scale_up"
    )
    assert compact_authority["remediation_blocked_lanes"] == ["mid"]
    assert compact_action["scale_up_blocked_by_validation_remediation"] is True
    assert (
        compact_action["risk_budget_passport"][
            "validation_remediation_cap_multiplier"
        ]
        == 0.5
    )
