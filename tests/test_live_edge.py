from __future__ import annotations

import sqlite3

from tradecraft.services.live_edge import (
    EvidenceOutcome,
    LiveEdgeRepository,
    compute_edge_scorecard,
    live_grade_from_scorecard,
)


def test_scorecard_requires_sample_size_before_high_grade() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="kis",
            strategy_family="value_pullback",
            evidence_key="valuation_discount",
            net_pnl_pct=2.0,
            r_multiple=1.2,
            rule_followed=True,
        )
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["sample_count"] == 1
    assert scorecard["grade"] in {"insufficient", "watch"}
    assert scorecard["authority_multiplier"] <= 1.0


def test_positive_expectancy_with_enough_samples_gets_scaling_grade() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="trend_breakout",
            evidence_key="quant_momentum",
            net_pnl_pct=value,
            r_multiple=r,
            rule_followed=True,
            backtest_passed=True,
            walk_forward_passed=True,
            out_of_sample_passed=True,
            live_shadow_passed=True,
        )
        for value, r in [
            (1.1, 0.8),
            (0.9, 0.7),
            (-0.3, -0.2),
            (1.5, 1.0),
            (0.4, 0.3),
        ]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["sample_count"] == 5
    assert scorecard["expectancy_pct"] > 0
    assert scorecard["validation_evidence_status"] == "validated"
    assert scorecard["grade"] == "scale_candidate"
    assert scorecard["authority_multiplier"] > 1.0
    assert scorecard["lane_confidence_score"] > 0.75
    assert scorecard["risk_of_ruin_pct"] < 10.0
    assert scorecard["recommended_risk_fraction"] > 0
    assert scorecard["max_risk_cap_fraction"] > 0


def test_positive_lane_without_validation_evidence_cannot_scale_up() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="trend_breakout",
            evidence_key="quant_momentum",
            net_pnl_pct=value,
            r_multiple=r,
            rule_followed=True,
        )
        for value, r in [
            (1.1, 0.8),
            (0.9, 0.7),
            (-0.3, -0.2),
            (1.5, 1.0),
            (0.4, 0.3),
        ]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["validation_evidence_status"] == "missing"
    assert scorecard["scale_blocked_by_validation_evidence"] is True
    assert scorecard["grade"] == "qualified"
    assert scorecard["authority_multiplier"] == 1.0


def test_failed_walk_forward_evidence_blocks_lane_scale_up() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="kis",
            strategy_family="mid",
            evidence_key="value_pullback",
            net_pnl_pct=value,
            r_multiple=value / 2.0,
            rule_followed=True,
            backtest_passed=True,
            walk_forward_passed=False,
            out_of_sample_passed=True,
            live_shadow_passed=True,
        )
        for value in [1.2, 1.1, 0.9, 1.4, 1.0]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["validation_evidence_status"] == "failed"
    assert scorecard["validation_failed_dimensions"] == ["walk_forward"]
    assert scorecard["scale_blocked_by_validation_evidence"] is True
    assert scorecard["grade"] == "qualified"
    assert scorecard["authority_multiplier"] == 1.0


def test_scorecard_tracks_profit_factor_recovery_and_cost_drag() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="futures:long",
            evidence_key="all",
            net_pnl_pct=value,
            r_multiple=r,
            rule_followed=True,
        )
        for value, r in [(2.0, 1.0), (-1.0, -0.5), (3.0, 1.5), (-0.5, -0.25)]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=4)

    assert scorecard["profit_factor"] == 10.0 / 3.0
    assert scorecard["recovery_factor"] == 3.5
    assert scorecard["total_gain_pct"] == 5.0
    assert scorecard["total_loss_pct"] == -1.5
    assert scorecard["cumulative_return_pct"] == 3.5


def test_scorecard_tracks_cost_drag_and_blocks_cost_weak_scaling() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="futures:short",
            evidence_key="all",
            net_pnl_pct=0.4,
            r_multiple=0.2,
            rule_followed=True,
            gross_pnl=1.0,
            cost_total=0.6,
        )
        for _ in range(6)
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["total_gross_pnl"] == 6.0
    assert scorecard["total_cost"] == 3.6
    assert scorecard["cost_drag_pct_of_gross_pnl"] == 60.0
    assert scorecard["grade"] != "scale_candidate"
    assert scorecard["authority_multiplier"] <= 1.0


def test_scorecard_blocks_scaling_when_costs_are_only_estimated() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="futures:long",
            evidence_key="trend_breakout",
            net_pnl_pct=value,
            r_multiple=value / 2.0,
            rule_followed=True,
            gross_pnl=value,
            cost_total=0.05,
            cost_precision="estimated_from_notional",
        )
        for value in [1.2, 1.1, 0.9, 1.4, 1.0]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["sample_count"] == 5
    assert scorecard["recorded_cost_sample_count"] == 0
    assert scorecard["estimated_cost_sample_count"] == 5
    assert scorecard["missing_cost_sample_count"] == 0
    assert scorecard["cost_precision_verified_rate"] == 0
    assert scorecard["cost_verified_alpha_count"] == 0
    assert scorecard["cost_unverified_alpha_count"] == 5
    assert scorecard["cost_unverified_alpha_net_pnl"] == 5.6
    assert scorecard["scale_blocked_by_cost_precision"] is True
    assert scorecard["scale_blocked_by_cost_evidence"] is True
    assert scorecard["grade"] == "qualified"
    assert scorecard["authority_multiplier"] == 1.0


def test_scorecard_blocks_scale_until_enough_recorded_cost_alpha_samples() -> None:
    values = [1.2, 1.1, 0.9, 1.4, 1.0]
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="futures:long",
            evidence_key="trend_breakout",
            net_pnl_pct=value,
            r_multiple=value / 2.0,
            rule_followed=True,
            gross_pnl=value,
            cost_total=0.05,
            cost_precision="recorded" if index < 3 else "estimated_from_notional",
            backtest_passed=True,
            walk_forward_passed=True,
            out_of_sample_passed=True,
            live_shadow_passed=True,
        )
        for index, value in enumerate(values)
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["sample_count"] == 5
    assert scorecard["cost_precision_verified_rate"] == 60.0
    assert scorecard["scale_blocked_by_cost_precision"] is False
    assert scorecard["cost_verified_alpha_count"] == 3
    assert scorecard["cost_unverified_alpha_count"] == 2
    assert scorecard["cost_verified_alpha_net_pnl"] == 3.2
    assert scorecard["cost_unverified_alpha_net_pnl"] == 2.4
    assert scorecard["scale_blocked_by_cost_evidence"] is True
    assert scorecard["scale_blocked_by_verified_edge_samples"] is True
    assert scorecard["validation_evidence_status"] == "validated"
    assert scorecard["grade"] == "qualified"
    assert scorecard["authority_multiplier"] == 1.0


def test_scorecard_treats_mixed_explicit_estimated_costs_as_estimated() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="kis",
            strategy_family="mid",
            evidence_key="value_pullback",
            net_pnl_pct=value,
            r_multiple=value / 2.0,
            rule_followed=True,
            gross_pnl=value,
            cost_total=0.05,
            cost_precision="explicit_order_costs_plus_estimated_market_costs",
        )
        for value in [1.2, 1.1, 0.9, 1.4, 1.0]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["recorded_cost_sample_count"] == 0
    assert scorecard["estimated_cost_sample_count"] == 5
    assert scorecard["cost_precision_verified_rate"] == 0
    assert scorecard["cost_verified_alpha_count"] == 0
    assert scorecard["cost_unverified_alpha_count"] == 5
    assert scorecard["scale_blocked_by_cost_precision"] is True
    assert scorecard["grade"] == "qualified"


def test_scorecard_blocks_scaling_for_bad_entry_quality_lane() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="kis",
            strategy_family="short",
            evidence_key="late_chase",
            net_pnl_pct=value,
            r_multiple=value,
            rule_followed=True,
            gross_pnl=value,
            cost_total=10.0,
            cost_precision="recorded",
            entry_quality_score=35.0,
            entry_quality_label="extended_momentum",
        )
        for value in [1.0, 0.8, 1.2, 0.9, 1.1]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["entry_quality_sample_count"] == 5
    assert scorecard["avg_entry_quality_score"] == 35.0
    assert scorecard["bad_entry_quality_rate_pct"] == 100.0
    assert scorecard["scale_blocked_by_entry_quality"] is True
    assert scorecard["grade"] == "restricted"
    assert scorecard["authority_multiplier"] == 0.5


def test_scorecard_penalizes_cost_only_churn_when_gross_pnl_is_flat() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="kis",
            strategy_family="short",
            evidence_key="late_chase",
            net_pnl_pct=-0.08,
            r_multiple=-0.1,
            rule_followed=True,
            gross_pnl=0.0,
            cost_total=240.0,
        )
        for _ in range(5)
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["total_gross_pnl"] == 0.0
    assert scorecard["total_cost"] == 1200.0
    assert scorecard["cost_drag_pct_of_gross_pnl"] == 999.0
    assert scorecard["grade"] == "restricted"
    assert scorecard["authority_multiplier"] == 0.5


def test_scorecard_does_not_scale_thin_profit_factor_lane() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="spot:long",
            evidence_key="all",
            net_pnl_pct=value,
            r_multiple=value,
            rule_followed=True,
        )
        for value in [3.0, 3.0, 3.0, -3.1, -3.1]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["expectancy_pct"] > 0
    assert scorecard["win_rate"] >= 52.0
    assert scorecard["profit_factor"] < 1.5
    assert scorecard["grade"] != "scale_candidate"
    assert scorecard["authority_multiplier"] <= 1.0


def test_scorecard_sets_zero_recommended_risk_for_negative_edge_lane() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="futures:short",
            evidence_key="late_chase",
            net_pnl_pct=value,
            r_multiple=value,
            rule_followed=True,
        )
        for value in [-0.8, -0.4, 0.2, -0.7, -0.3]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["expectancy_pct"] < 0
    assert scorecard["profit_factor"] < 1.0
    assert scorecard["risk_of_ruin_pct"] >= 20.0
    assert scorecard["recommended_risk_fraction"] == 0
    assert scorecard["grade"] in {"observe_only", "restricted"}


def test_repository_list_scorecards_exposes_raw_edge_quality_metrics(tmp_path) -> None:
    repository = LiveEdgeRepository(tmp_path / "live_edge.db")
    repository.upsert_scorecard(
        venue="binance",
        strategy_family="futures:long",
        evidence_key="all",
        scorecard={
            "sample_count": 8,
            "expectancy_pct": 0.8,
            "win_rate": 62.5,
            "rule_follow_rate": 100.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -1.1,
            "profit_factor": 2.4,
            "recovery_factor": 1.8,
            "cumulative_return_pct": 6.4,
            "total_gross_pnl": 10.0,
            "total_cost": 2.0,
            "cost_drag_pct_of_gross_pnl": 20.0,
            "recorded_cost_sample_count": 6,
            "hybrid_cost_sample_count": 1,
            "estimated_cost_sample_count": 2,
            "missing_cost_sample_count": 0,
            "cost_precision_verified_rate": 75.0,
            "scale_blocked_by_cost_precision": False,
            "scale_blocked_by_cost_evidence": False,
            "cost_verified_alpha_count": 6,
            "cost_hybrid_alpha_count": 1,
            "cost_unverified_alpha_count": 2,
            "cost_verified_alpha_net_pnl": 7.5,
            "cost_hybrid_alpha_net_pnl": 0.8,
            "cost_unverified_alpha_net_pnl": 1.1,
            "scale_blocked_by_verified_edge_samples": False,
            "entry_quality_sample_count": 8,
            "avg_entry_quality_score": 72.5,
            "low_entry_quality_sample_count": 1,
            "bad_entry_quality_rate_pct": 12.5,
            "scale_blocked_by_entry_quality": False,
            "lane_confidence_score": 0.92,
            "risk_of_ruin_pct": 2.4,
            "raw_kelly_fraction": 0.22,
            "fractional_kelly_fraction": 0.055,
            "recommended_risk_fraction": 0.014,
            "max_risk_cap_fraction": 0.02,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
        },
    )

    row = repository.list_scorecards(venue="binance", limit=1)[0]

    assert row["profit_factor"] == 2.4
    assert row["recovery_factor"] == 1.8
    assert row["cumulative_return_pct"] == 6.4
    assert row["total_gross_pnl"] == 10.0
    assert row["total_cost"] == 2.0
    assert row["cost_drag_pct_of_gross_pnl"] == 20.0
    assert row["recorded_cost_sample_count"] == 6
    assert row["hybrid_cost_sample_count"] == 1
    assert row["estimated_cost_sample_count"] == 2
    assert row["missing_cost_sample_count"] == 0
    assert row["cost_precision_verified_rate"] == 75.0
    assert row["scale_blocked_by_cost_precision"] is False
    assert row["scale_blocked_by_cost_evidence"] is False
    assert row["cost_verified_alpha_count"] == 6
    assert row["cost_hybrid_alpha_count"] == 1
    assert row["cost_unverified_alpha_count"] == 2
    assert row["cost_verified_alpha_net_pnl"] == 7.5
    assert row["cost_hybrid_alpha_net_pnl"] == 0.8
    assert row["cost_unverified_alpha_net_pnl"] == 1.1
    assert row["scale_blocked_by_verified_edge_samples"] is False
    assert row["entry_quality_sample_count"] == 8
    assert row["avg_entry_quality_score"] == 72.5
    assert row["low_entry_quality_sample_count"] == 1
    assert row["bad_entry_quality_rate_pct"] == 12.5
    assert row["scale_blocked_by_entry_quality"] is False
    assert row["lane_confidence_score"] == 0.92
    assert row["risk_of_ruin_pct"] == 2.4
    assert row["recommended_risk_fraction"] == 0.014
    assert row["max_risk_cap_fraction"] == 0.02


def test_repository_upsert_scorecard_is_revision_scoped(tmp_path) -> None:
    repository = LiveEdgeRepository(tmp_path / "live_edge.db")
    base_scorecard = {
        "sample_count": 4,
        "expectancy_pct": 0.4,
        "win_rate": 50.0,
        "rule_follow_rate": 100.0,
        "execution_error_rate": 0.0,
        "max_drawdown_pct": -1.0,
        "profit_factor": 1.4,
        "recovery_factor": 1.2,
        "cumulative_return_pct": 1.6,
        "grade": "qualified",
        "authority_multiplier": 1.0,
    }

    repository.upsert_scorecard(
        venue="binance",
        strategy_family="futures:long",
        evidence_key="trend_breakout",
        scorecard={**base_scorecard, "strategy_revision_id": "rev-a"},
    )
    repository.upsert_scorecard(
        venue="binance",
        strategy_family="futures:long",
        evidence_key="trend_breakout",
        scorecard={
            **base_scorecard,
            "strategy_revision_id": "rev-b",
            "sample_count": 7,
        },
    )
    repository.upsert_scorecard(
        venue="binance",
        strategy_family="futures:long",
        evidence_key="trend_breakout",
        scorecard={
            **base_scorecard,
            "strategy_revision_id": "rev-a",
            "sample_count": 9,
        },
    )

    rev_a = repository.list_scorecards(
        venue="binance",
        strategy_revision_id="rev-a",
        limit=10,
    )
    rev_b = repository.list_scorecards(
        venue="binance",
        strategy_revision_id="rev-b",
        limit=10,
    )

    assert len(rev_a) == 1
    assert len(rev_b) == 1
    assert rev_a[0]["sample_count"] == 9
    assert rev_b[0]["sample_count"] == 7


def test_repository_persists_edge_quality_metrics_as_columns(tmp_path) -> None:
    db_path = tmp_path / "live_edge.db"
    repository = LiveEdgeRepository(db_path)
    repository.upsert_scorecard(
        venue="kis",
        strategy_family="core_etf",
        evidence_key="index_pullback",
        scorecard={
            "sample_count": 12,
            "expectancy_pct": 0.45,
            "win_rate": 58.0,
            "rule_follow_rate": 100.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -1.2,
            "profit_factor": 1.9,
            "recovery_factor": 1.4,
            "cumulative_return_pct": 5.4,
            "total_gross_pnl": 120_000.0,
            "total_cost": 18_000.0,
            "cost_drag_pct_of_gross_pnl": 15.0,
            "recorded_cost_sample_count": 10,
            "hybrid_cost_sample_count": 1,
            "estimated_cost_sample_count": 2,
            "missing_cost_sample_count": 0,
            "cost_precision_verified_rate": 83.333333,
            "scale_blocked_by_cost_precision": False,
            "scale_blocked_by_cost_evidence": False,
            "cost_verified_alpha_count": 10,
            "cost_hybrid_alpha_count": 1,
            "cost_unverified_alpha_count": 2,
            "cost_verified_alpha_net_pnl": 6.2,
            "cost_hybrid_alpha_net_pnl": 0.4,
            "cost_unverified_alpha_net_pnl": 0.7,
            "scale_blocked_by_verified_edge_samples": False,
            "entry_quality_sample_count": 12,
            "avg_entry_quality_score": 78.0,
            "low_entry_quality_sample_count": 0,
            "bad_entry_quality_rate_pct": 0.0,
            "scale_blocked_by_entry_quality": False,
            "lane_confidence_score": 0.88,
            "risk_of_ruin_pct": 3.2,
            "raw_kelly_fraction": 0.18,
            "fractional_kelly_fraction": 0.045,
            "recommended_risk_fraction": 0.013,
            "max_risk_cap_fraction": 0.02,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
        },
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT profit_factor, recovery_factor, cumulative_return_pct,
                   total_gross_pnl, total_cost, cost_drag_pct_of_gross_pnl,
                   recorded_cost_sample_count, hybrid_cost_sample_count,
                   estimated_cost_sample_count,
                   missing_cost_sample_count, cost_precision_verified_rate,
                   scale_blocked_by_cost_precision,
                   scale_blocked_by_cost_evidence,
                   cost_verified_alpha_count, cost_hybrid_alpha_count,
                   cost_unverified_alpha_count, cost_verified_alpha_net_pnl,
                   cost_hybrid_alpha_net_pnl, cost_unverified_alpha_net_pnl,
                   scale_blocked_by_verified_edge_samples,
                   entry_quality_sample_count, avg_entry_quality_score,
                   low_entry_quality_sample_count, bad_entry_quality_rate_pct,
                   scale_blocked_by_entry_quality,
                   lane_confidence_score, risk_of_ruin_pct,
                   recommended_risk_fraction, max_risk_cap_fraction
            FROM live_edge_scorecards
            WHERE venue = 'kis' AND strategy_family = 'core_etf'
            """
        ).fetchone()

    assert row["profit_factor"] == 1.9
    assert row["recovery_factor"] == 1.4
    assert row["cumulative_return_pct"] == 5.4
    assert row["total_gross_pnl"] == 120_000.0
    assert row["total_cost"] == 18_000.0
    assert row["cost_drag_pct_of_gross_pnl"] == 15.0
    assert row["recorded_cost_sample_count"] == 10
    assert row["hybrid_cost_sample_count"] == 1
    assert row["estimated_cost_sample_count"] == 2
    assert row["missing_cost_sample_count"] == 0
    assert row["cost_precision_verified_rate"] == 83.333333
    assert row["scale_blocked_by_cost_precision"] == 0
    assert row["scale_blocked_by_cost_evidence"] == 0
    assert row["cost_verified_alpha_count"] == 10
    assert row["cost_hybrid_alpha_count"] == 1
    assert row["cost_unverified_alpha_count"] == 2
    assert row["cost_verified_alpha_net_pnl"] == 6.2
    assert row["cost_hybrid_alpha_net_pnl"] == 0.4
    assert row["cost_unverified_alpha_net_pnl"] == 0.7
    assert row["scale_blocked_by_verified_edge_samples"] == 0
    assert row["entry_quality_sample_count"] == 12
    assert row["avg_entry_quality_score"] == 78.0
    assert row["low_entry_quality_sample_count"] == 0
    assert row["bad_entry_quality_rate_pct"] == 0.0
    assert row["scale_blocked_by_entry_quality"] == 0
    assert row["lane_confidence_score"] == 0.88
    assert row["risk_of_ruin_pct"] == 3.2
    assert row["recommended_risk_fraction"] == 0.013
    assert row["max_risk_cap_fraction"] == 0.02


def test_repository_list_scorecards_reads_verified_cost_columns_without_raw_json(
    tmp_path,
) -> None:
    db_path = tmp_path / "live_edge.db"
    repository = LiveEdgeRepository(db_path)
    repository.upsert_scorecard(
        venue="binance",
        strategy_family="futures:long",
        evidence_key="trend_breakout",
        scorecard={
            "sample_count": 5,
            "expectancy_pct": 0.9,
            "win_rate": 60.0,
            "rule_follow_rate": 100.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -1.0,
            "profit_factor": 2.0,
            "recovery_factor": 1.5,
            "cumulative_return_pct": 4.5,
            "total_gross_pnl": 5.0,
            "total_cost": 0.2,
            "cost_drag_pct_of_gross_pnl": 4.0,
            "recorded_cost_sample_count": 3,
            "hybrid_cost_sample_count": 1,
            "estimated_cost_sample_count": 1,
            "missing_cost_sample_count": 0,
            "cost_precision_verified_rate": 60.0,
            "scale_blocked_by_cost_precision": False,
            "scale_blocked_by_cost_evidence": True,
            "cost_verified_alpha_count": 3,
            "cost_hybrid_alpha_count": 1,
            "cost_unverified_alpha_count": 2,
            "cost_verified_alpha_net_pnl": 3.2,
            "cost_hybrid_alpha_net_pnl": 0.6,
            "cost_unverified_alpha_net_pnl": 1.3,
            "scale_blocked_by_verified_edge_samples": True,
            "grade": "qualified",
            "authority_multiplier": 1.0,
        },
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE live_edge_scorecards SET raw_json = '{}'")

    row = repository.list_scorecards(venue="binance", limit=1)[0]

    assert row["cost_verified_alpha_count"] == 3
    assert row["cost_hybrid_alpha_count"] == 1
    assert row["cost_unverified_alpha_count"] == 2
    assert row["cost_verified_alpha_net_pnl"] == 3.2
    assert row["scale_blocked_by_cost_precision"] is False
    assert row["scale_blocked_by_cost_evidence"] is True
    assert row["scale_blocked_by_verified_edge_samples"] is True


def test_live_grade_penalizes_drawdown_and_bad_rule_following() -> None:
    scorecard = {
        "sample_count": 20,
        "expectancy_pct": 0.5,
        "win_rate": 55.0,
        "max_drawdown_pct": -8.0,
        "rule_follow_rate": 45.0,
        "execution_error_rate": 20.0,
    }

    grade = live_grade_from_scorecard(scorecard)

    assert grade["grade"] in {"restricted", "observe_only"}
    assert grade["authority_multiplier"] < 1.0
