from __future__ import annotations

import json

import pytest

from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
    classify_block_attribution,
    compute_realized_pnl,
)


def test_existing_position_is_managed_but_not_jue_created_alpha() -> None:
    row = BlockPerformanceInput(
        venue="kis",
        block_id="kis-1",
        symbol="005930",
        created_by="existing_position",
        status="closed",
        entry_price=70000,
        exit_price=73500,
        qty=2,
        fees=20,
        taxes=30,
        slippage=0,
        filled=True,
    )

    classified = classify_block_attribution(row)

    assert classified["attribution"] == "adopted_existing_position"
    assert classified["include_in_jue_alpha"] is False
    assert classified["include_in_risk_management"] is True


def test_llm_filled_block_realized_pnl_is_cost_aware() -> None:
    row = BlockPerformanceInput(
        venue="kis",
        block_id="kis-2",
        symbol="000660",
        created_by="llm",
        status="closed",
        entry_price=120000,
        exit_price=123000,
        qty=1,
        fees=15,
        taxes=25,
        slippage=10,
        filled=True,
    )

    pnl = compute_realized_pnl(row)

    assert pnl["gross_pnl"] == 3000
    assert pnl["net_pnl"] == 2950
    assert pnl["cost_total"] == 50
    assert pnl["include_in_jue_alpha"] is True


def test_short_block_realized_pnl_uses_inverse_price_direction() -> None:
    row = BlockPerformanceInput(
        venue="binance",
        block_id="bn-short",
        symbol="NEARUSDT",
        created_by="llm",
        status="closed",
        entry_price=3.0,
        exit_price=2.7,
        qty=10,
        fees=0.1,
        filled=True,
        metadata={"side": "short"},
    )

    pnl = compute_realized_pnl(row)

    assert pnl["gross_pnl"] == pytest.approx(3.0)
    assert pnl["net_pnl"] == pytest.approx(2.9)
    assert pnl["include_in_jue_alpha"] is True


def test_pre_fill_operational_failure_is_not_realized_loss() -> None:
    row = BlockPerformanceInput(
        venue="binance",
        block_id="bn-1",
        symbol="LTCUSDT",
        created_by="llm",
        status="error",
        entry_price=100,
        exit_price=0,
        qty=0,
        fees=0,
        taxes=0,
        slippage=0,
        filled=False,
        error_type="exchange_filter_reject",
    )

    classified = classify_block_attribution(row)

    assert classified["attribution"] == "operational_failure_pre_fill"
    assert classified["include_in_jue_alpha"] is False
    assert classified["include_in_execution_quality"] is True


def test_live_performance_persists_precise_execution_cost_components(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-precise",
            symbol="ETHUSDT",
            created_by="llm",
            status="closed",
            entry_price=2450.5,
            exit_price=2472.25,
            qty=0.42,
            fees=0.31,
            taxes=0.0,
            funding=0.08,
            slippage=0.12,
            spread=0.05,
            filled=True,
            metadata={"side": "long"},
        ),
        source={"metadata": {"side": "long"}},
    )

    latest = repo.latest(venue="binance", limit=1)[0]

    assert latest["entry_price"] == pytest.approx(2450.5)
    assert latest["exit_price"] == pytest.approx(2472.25)
    assert latest["qty"] == pytest.approx(0.42)
    assert latest["fees"] == pytest.approx(0.31)
    assert latest["taxes"] == pytest.approx(0.0)
    assert latest["funding"] == pytest.approx(0.08)
    assert latest["slippage"] == pytest.approx(0.12)
    assert latest["spread"] == pytest.approx(0.05)
    assert latest["cost_total"] == pytest.approx(0.56)
    assert latest["gross_pnl"] == pytest.approx((2472.25 - 2450.5) * 0.42)
    assert latest["net_pnl"] == pytest.approx(latest["gross_pnl"] - 0.56)


def test_live_performance_compacts_large_source_payloads(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    large_live_authority = {
        "status": "ok",
        "live_grade": "insufficient",
        "lane_authority": {
            "lanes": [
                {"lane": f"lane-{index}", "reason": "x" * 200}
                for index in range(200)
            ]
        },
        "remediation_plan": {"body": "y" * 50000},
    }
    metadata = {
        "side": "long",
        "horizon": "mid",
        "lane": "mid:value_pullback",
        "live_authority": large_live_authority,
        "validation_pressure": {"status": "warn", "failed": ["cost_simulation"]},
        "policy_rule_impacts": [{"id": "rule", "body": "z" * 1000} for _ in range(40)],
    }
    source = {
        "block": {
            "block_id": "kis-large",
            "symbol": "005930",
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "metadata": metadata,
            "thesis": "keep me",
        },
        "metadata": metadata,
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-large",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70000,
            exit_price=73500,
            qty=1,
            fees=10,
            taxes=0,
            filled=True,
            metadata=metadata,
        ),
        source=source,
    )

    stored = json.loads(repo.latest(venue="kis", limit=1)[0]["source_json"])

    assert len(json.dumps(stored, ensure_ascii=False)) < 12000
    assert "metadata_json" not in stored["block"]
    assert "metadata" not in stored["block"]
    assert "lane_authority" not in stored["metadata"]["live_authority"]
    assert stored["metadata"]["live_authority"] == {
        "status": "ok",
        "live_grade": "insufficient",
    }
    assert stored["metadata"]["validation_pressure"] == {
        "status": "warn",
        "failed": ["cost_simulation"],
    }
    assert stored["metadata"]["policy_rule_impact_count"] == 40


def test_live_performance_summarizes_heavy_operational_metadata(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "long",
        "horizon": "short",
        "lane": "short:spot",
        "validation_repair": {
            "status": "needs_work",
            "version": "validation_repair_v1",
            "repair_backlog": [{"body": "x" * 5000} for _ in range(12)],
            "block_design_constraints": [{"body": "y" * 3000} for _ in range(8)],
        },
        "entry_gate": {
            "ok": True,
            "waiting_entry": False,
            "policy": {
                "version": "entry_policy_v1",
                "lane_scorecards": {
                    f"lane-{idx}": {"body": "z" * 1000} for idx in range(20)
                },
                "min_confidence": 0.55,
            },
            "effective_policy": {
                "version": "entry_policy_v1",
                "effective_adjustment": "spot_exploration",
                "lane_scorecards": {
                    f"lane-{idx}": {"body": "a" * 1000} for idx in range(20)
                },
            },
        },
        "calculated_price_plan": {
            "method_version": "price_plan_v1",
            "entry_price": 1.23,
            "target_price": 1.35,
            "stop_price": 1.18,
            "reward_risk": 2.4,
            "pattern_inputs": {
                "prior": {
                    "pattern_key": "pullback",
                    "walk_forward_quality": {
                        "status": "pass",
                        "windows": [{"body": "w" * 2500} for _ in range(10)],
                    },
                }
            },
        },
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="heavy-operational",
            symbol="WLDUSDT",
            created_by="llm",
            status="closed",
            entry_price=1.23,
            exit_price=1.31,
            qty=10,
            fees=0.01,
            filled=True,
            metadata=metadata,
        ),
        source={"block": {"block_id": "heavy-operational"}, "metadata": metadata},
    )

    stored = json.loads(repo.latest(venue="binance", limit=1)[0]["source_json"])
    stored_text = json.dumps(stored, ensure_ascii=False)

    assert len(stored_text) < 12000
    assert stored["metadata"]["validation_repair"]["repair_backlog_count"] == 12
    assert stored["metadata"]["entry_gate"]["policy"]["lane_scorecard_count"] == 20
    assert (
        stored["metadata"]["entry_gate"]["effective_policy"]["lane_scorecard_count"]
        == 20
    )
    assert stored["metadata"]["calculated_price_plan"]["entry_price"] == 1.23
    assert stored["metadata"]["calculated_price_plan"]["target_price"] == 1.35
    assert stored["metadata"]["calculated_price_plan"]["stop_price"] == 1.18
    assert "pattern_inputs" not in stored["metadata"]["calculated_price_plan"]


def test_live_performance_flattens_lane_context_for_operational_queries(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-lane-context",
            symbol="ETHUSDT",
            created_by="llm",
            status="closed",
            entry_price=2400,
            exit_price=2430,
            qty=0.2,
            fees=0.2,
            slippage=0.1,
            spread=0.1,
            filled=True,
            metadata={"market": "futures", "side": "long"},
        ),
        source={"metadata": {"market": "futures", "side": "long"}},
    )

    latest = repo.latest(venue="binance", limit=1)[0]
    stored_source = json.loads(latest["source_json"])

    assert stored_source["lane"] == "futures_long"
    assert stored_source["market"] == "futures"
    assert stored_source["side"] == "long"
    assert stored_source["metadata"]["lane"] == "futures_long"


def test_live_performance_exposes_cost_precision_and_evidence_status(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "long",
        "cost_model_status": "estimated_from_notional",
        "cost_source": "estimated_round_trip_notional",
        "fill_evidence_status": "order_round_trip_filled",
        "entry_price_source": "order_response_or_fill",
        "exit_price_source": "reflection",
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-cost-status",
            symbol="BTCUSDT",
            created_by="llm",
            status="closed",
            entry_price=100.0,
            exit_price=103.0,
            qty=0.5,
            fees=0.05,
            slippage=0.02,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="binance", limit=1)[0]

    assert payload["cost_model_status"] == "estimated_from_notional"
    assert payload["cost_source"] == "estimated_round_trip_notional"
    assert payload["cost_precision"] == "estimated"
    assert payload["fill_evidence_status"] == "order_round_trip_filled"
    assert payload["entry_price_source"] == "order_response_or_fill"
    assert payload["exit_price_source"] == "reflection"
    assert latest["cost_model_status"] == "estimated_from_notional"
    assert latest["cost_source"] == "estimated_round_trip_notional"
    assert latest["cost_precision"] == "estimated"
    assert latest["fill_evidence_status"] == "order_round_trip_filled"
    assert latest["entry_price_source"] == "order_response_or_fill"
    assert latest["exit_price_source"] == "reflection"


def test_live_performance_does_not_mark_unverified_cost_amount_as_recorded(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-unverified-cost",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=72_000,
            qty=1,
            fees=20,
            filled=True,
            metadata={"horizon": "mid"},
        ),
        source={"metadata": {"horizon": "mid"}},
    )

    latest = repo.latest(venue="kis", limit=1)[0]
    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    assert latest["cost_precision"] == "unverified_cost"
    assert lanes[("kis", "mid")]["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 0,
        "partial": 0,
        "missing": 1,
    }
    assert lanes[("kis", "mid")]["cost_verified_alpha_count"] == 0
    assert lanes[("kis", "mid")]["cost_unverified_alpha_count"] == 1


def test_live_performance_downgrades_kis_recorded_cost_without_market_components(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "cost_model_status": "recorded",
        "cost_components": {"fees": 20.0},
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-recorded-missing-market-costs",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=72_000,
            qty=1,
            fees=20,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]
    stored_metadata = json.loads(latest["source_json"])["metadata"]
    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("kis", "mid")]

    assert payload["cost_precision_before_audit"] == "recorded"
    assert payload["cost_precision_reason"] == (
        "recorded_cost_missing_required_components"
    )
    assert payload["required_cost_components"] == [
        "fees",
        "slippage",
        "spread",
        "taxes",
    ]
    assert payload["present_cost_components"] == ["fees"]
    assert payload["missing_cost_components"] == ["slippage", "spread", "taxes"]
    assert latest["cost_precision"] == "partial"
    assert stored_metadata["cost_precision_before_audit"] == "recorded"
    assert stored_metadata["cost_precision_after_audit"] == "partial"
    assert stored_metadata["cost_precision_reason"] == (
        "recorded_cost_missing_required_components"
    )
    assert stored_metadata["missing_cost_components"] == [
        "slippage",
        "spread",
        "taxes",
    ]
    assert lane["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 0,
        "partial": 1,
        "missing": 0,
    }
    assert lane["missing_cost_component_counts"] == {
        "slippage": 1,
        "spread": 1,
        "taxes": 1,
    }
    assert lane["present_cost_component_counts"] == {"fees": 1}
    assert lane["required_cost_component_counts"] == {
        "fees": 1,
        "slippage": 1,
        "spread": 1,
        "taxes": 1,
    }
    assert lane["cost_precision_reason_counts"] == {
        "recorded_cost_missing_required_components": 1
    }
    assert lane["cost_verified_alpha_count"] == 0
    assert lane["cost_unverified_alpha_count"] == 1


def test_live_performance_accepts_kis_recorded_cost_with_explicit_zero_components(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 20.0,
            "taxes": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
        },
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-recorded-explicit-zero-costs",
            symbol="455850",
            created_by="llm",
            status="closed",
            entry_price=10_000,
            exit_price=10_300,
            qty=3,
            fees=20,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]
    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("kis", "mid")]

    assert latest["cost_precision"] == "recorded"
    assert lane["cost_precision_counts"]["recorded"] == 1
    assert lane["missing_cost_component_counts"] == {}
    assert lane["present_cost_component_counts"] == {
        "fees": 1,
        "slippage": 1,
        "spread": 1,
        "taxes": 1,
    }
    assert lane["cost_precision_reason_counts"] == {
        "cost_components_audited": 1
    }
    assert lane["cost_verified_alpha_count"] == 1


def test_live_performance_accepts_declared_zero_cost_components_for_binance_futures(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "market": "futures",
        "side": "long",
        "cost_model_status": "recorded",
        "recorded_cost_components": [
            "fees",
            "funding",
            "spread",
            "slippage",
        ],
        "zero_cost_components": "funding,spread,slippage",
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-recorded-zero-costs",
            symbol="ETHUSDT",
            created_by="llm",
            status="closed",
            entry_price=2_000,
            exit_price=2_040,
            qty=0.1,
            fees=0.0,
            funding=0.0,
            spread=0.0,
            slippage=0.0,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="binance", limit=1)[0]
    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("binance", "futures_long")]

    assert payload["cost_precision"] == "recorded"
    assert payload["missing_cost_components"] == []
    assert latest["cost_precision"] == "recorded"
    assert lane["cost_precision_counts"]["recorded"] == 1
    assert lane["missing_cost_component_counts"] == {}
    assert lane["present_cost_component_counts"] == {
        "fees": 1,
        "funding": 1,
        "slippage": 1,
        "spread": 1,
    }
    assert lane["cost_verified_alpha_count"] == 1


def test_live_performance_downgrades_binance_futures_recorded_cost_without_component_evidence(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "market": "futures",
        "side": "short",
        "cost_model_status": "recorded",
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-recorded-no-cost-proof",
            symbol="NEARUSDT",
            created_by="llm",
            status="closed",
            entry_price=3.0,
            exit_price=2.9,
            qty=10.0,
            fees=0.0,
            funding=0.0,
            spread=0.0,
            slippage=0.0,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("binance", "futures_short")]

    assert payload["cost_precision_before_audit"] == "recorded"
    assert payload["cost_precision"] == "partial"
    assert payload["missing_cost_components"] == [
        "fees",
        "funding",
        "slippage",
        "spread",
    ]
    assert lane["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 0,
        "partial": 1,
        "missing": 0,
    }
    assert lane["cost_verified_alpha_count"] == 0
    assert lane["cost_unverified_alpha_count"] == 1


def test_live_performance_downgrades_binance_spot_recorded_cost_without_market_costs(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "market": "spot",
        "side": "long",
        "cost_model_status": "recorded",
        "cost_components": {"fees": 0.01},
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-spot-recorded-missing-market-costs",
            symbol="SOLUSDT",
            created_by="llm",
            status="closed",
            entry_price=150.0,
            exit_price=153.0,
            qty=0.2,
            fees=0.01,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("binance", "spot")]

    assert payload["cost_precision_before_audit"] == "recorded"
    assert payload["cost_precision"] == "partial"
    assert payload["required_cost_components"] == ["fees", "slippage", "spread"]
    assert payload["present_cost_components"] == ["fees"]
    assert payload["missing_cost_components"] == ["slippage", "spread"]
    assert lane["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 0,
        "partial": 1,
        "missing": 0,
    }
    assert lane["cost_verified_alpha_count"] == 0
    assert lane["cost_unverified_alpha_count"] == 1


def test_live_performance_accepts_binance_spot_recorded_zero_market_costs(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "market": "spot",
        "side": "long",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 0.01,
            "spread": 0.0,
            "slippage": 0.0,
        },
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-spot-recorded-complete-market-costs",
            symbol="ETHUSDT",
            created_by="llm",
            status="closed",
            entry_price=2_000,
            exit_price=2_040,
            qty=0.1,
            fees=0.01,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("binance", "spot")]

    assert payload["cost_precision"] == "recorded"
    assert payload["missing_cost_components"] == []
    assert lane["cost_precision_counts"]["recorded"] == 1
    assert lane["present_cost_component_counts"] == {
        "fees": 1,
        "slippage": 1,
        "spread": 1,
    }
    assert lane["cost_verified_alpha_count"] == 1


def test_live_performance_persists_strategy_revision_for_lane_evidence(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "strategy_revision_id": "jue_edge_repair_v2",
        "cost_model_status": "recorded",
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-revision",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=72_000,
            qty=1,
            fees=20,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]
    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    assert payload["strategy_revision_id"] == "jue_edge_repair_v2"
    assert payload["cost_model_status"] == "recorded"
    assert payload["cost_precision"] == "partial"
    assert latest["strategy_revision_id"] == "jue_edge_repair_v2"
    assert lanes[("kis", "mid")]["strategy_revision_counts"] == {
        "jue_edge_repair_v2": 1
    }
    assert lanes[("kis", "mid")]["primary_strategy_revision_id"] == (
        "jue_edge_repair_v2"
    )


def test_live_performance_summarizes_nested_validation_pressure_by_lane(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 20.0,
            "taxes": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
        },
        "live_authority": {
            "validation_pressure": {
                "severity": "risk_off",
                "entry_posture": "patient_waiting_entry",
                "sizing_posture": "fractional_small_only",
                "fail_ids": ["cost_simulation"],
                "warn_ids": ["monte_carlo"],
                "missing_ids": ["walk_forward_analysis"],
                "discipline_actions": [
                    {"id": "cost_simulation", "status": "fail"},
                    {"id": "monte_carlo", "status": "warn"},
                    {"id": "walk_forward_analysis", "status": "missing"},
                ],
            }
        },
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-pressure",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=71_000,
            qty=1,
            fees=20,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("kis", "mid")]

    assert lane["validation_pressure_severity_counts"] == {"risk_off": 1}
    assert lane["validation_pressure_entry_posture_counts"] == {
        "patient_waiting_entry": 1
    }
    assert lane["validation_pressure_sizing_posture_counts"] == {
        "fractional_small_only": 1
    }
    assert lane["validation_pressure_fail_id_counts"] == {"cost_simulation": 1}
    assert lane["validation_pressure_warn_id_counts"] == {"monte_carlo": 1}
    assert lane["validation_pressure_missing_id_counts"] == {
        "walk_forward_analysis": 1
    }
    assert lane["validation_pressure_discipline_action_counts"] == {
        "cost_simulation:fail": 1,
        "monte_carlo:warn": 1,
        "walk_forward_analysis:missing": 1,
    }


def test_live_performance_extracts_revision_from_nested_block_metadata(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    block_metadata = {
        "horizon": "mid",
        "strategy_revision_id": "jue_edge_repair_v2",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 10.0,
            "taxes": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
        },
    }

    payload = repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-nested-revision",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=71_000,
            qty=1,
            fees=10,
            filled=True,
        ),
        source={
            "block": {
                "block_id": "kis-nested-revision",
                "metadata_json": json.dumps(block_metadata),
            }
        },
    )

    latest = repo.latest(venue="kis", limit=1)[0]
    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary(strategy_revision_id="jue_edge_repair_v2")["lanes"]
    }

    assert payload["strategy_revision_id"] == "jue_edge_repair_v2"
    assert payload["cost_precision"] == "recorded"
    assert latest["strategy_revision_id"] == "jue_edge_repair_v2"
    assert latest["cost_precision"] == "recorded"
    assert lanes[("kis", "mid")]["alpha_count"] == 1


def test_live_performance_summary_can_filter_strategy_revision(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    for block_id, exit_price, revision_id in (
        ("legacy-loss", 98.0, "legacy_rev"),
        ("repair-win", 104.0, "jue_edge_repair_v2"),
    ):
        metadata = {
            "horizon": "mid",
            "strategy_revision_id": revision_id,
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=block_id,
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    full_summary = repo.summary()
    active_summary = repo.summary(strategy_revision_id="jue_edge_repair_v2")
    active_lanes = {
        (row["venue"], row["lane"]): row
        for row in active_summary["lanes"]
    }

    assert full_summary["strategy_revision_id"] == ""
    assert active_summary["strategy_revision_id"] == "jue_edge_repair_v2"
    assert active_summary["venues"][0]["alpha_count"] == 1
    assert active_lanes[("kis", "mid")]["alpha_count"] == 1
    assert active_lanes[("kis", "mid")]["alpha_net_pnl"] == 4.0
    assert active_lanes[("kis", "mid")]["strategy_revision_counts"] == {
        "jue_edge_repair_v2": 1
    }


def test_live_performance_treats_mixed_explicit_estimated_costs_as_hybrid(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "side": "long",
        "cost_model_status": "explicit_order_costs_plus_estimated_market_costs",
        "cost_source": "kis_order_payload",
    }

    for index in range(3):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-mixed-cost-{index}",
                symbol="277810",
                created_by="llm",
                status="closed",
                entry_price=100_000,
                exit_price=101_000,
                qty=1,
                fees=20,
                taxes=150,
                slippage=105.5,
                spread=42.2,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    latest = repo.latest(venue="kis", limit=1)[0]
    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("kis", "mid")]

    assert latest["cost_model_status"] == (
        "explicit_order_costs_plus_estimated_market_costs"
    )
    assert latest["cost_precision"] == "hybrid"
    assert lane["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 3,
        "estimated": 0,
        "partial": 0,
        "missing": 0,
    }
    assert lane["cost_evidence_status"] == "hybrid_needs_market_cost_repair"
    assert lane["alpha_evidence_status"] == "unverified_cost_alpha"
    assert lane["cost_hybrid_alpha_count"] == 3
    assert lane["cost_verified_alpha_count"] == 0
    assert lane["scale_blocked_by_cost_evidence"] is True


def test_live_performance_lane_does_not_scale_with_only_estimated_costs(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "cost_model_status": "estimated_from_notional",
        "cost_source": "estimated_round_trip_notional",
    }
    for index in range(3):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-estimated-cost-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=70_000,
                exit_price=72_000,
                qty=1,
                fees=20,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    assert lanes[("kis", "mid")]["alpha_count"] == 3
    assert lanes[("kis", "mid")]["expectancy_pct"] > 0
    assert lanes[("kis", "mid")]["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 3,
        "partial": 0,
        "missing": 0,
    }
    assert lanes[("kis", "mid")]["cost_precision_verified_rate"] == 0
    assert lanes[("kis", "mid")]["cost_evidence_status"] == "estimated_or_missing"
    assert lanes[("kis", "mid")]["alpha_evidence_status"] == "unverified_cost_alpha"
    assert lanes[("kis", "mid")]["cost_verified_alpha_count"] == 0
    assert lanes[("kis", "mid")]["cost_unverified_alpha_count"] == 3
    assert lanes[("kis", "mid")]["cost_unverified_alpha_net_pnl"] > 0
    assert lanes[("kis", "mid")]["scale_blocked_by_cost_evidence"] is True
    assert lanes[("kis", "mid")]["scale_blocked_by_cost_precision"] is True
    assert lanes[("kis", "mid")]["risk_model_status"] == (
        "estimated_from_live_lane_metrics"
    )
    assert lanes[("kis", "mid")]["risk_budget_multiplier"] <= 0.5
    assert lanes[("kis", "mid")]["recommended_risk_fraction"] <= 0.01
    assert lanes[("kis", "mid")]["quality_hint"] == "weak_review"
    assert lanes[("kis", "mid")]["action_hint"] == "cost_evidence_repair_waiting_entry"


def test_live_performance_caps_lane_when_recorded_cost_alpha_is_negative(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    recorded_metadata = {
        "market": "futures",
        "side": "long",
        "cost_model_status": "recorded",
        "recorded_cost_components": [
            "fees",
            "funding",
            "spread",
            "slippage",
        ],
        "zero_cost_components": [
            "funding",
            "spread",
            "slippage",
        ],
    }
    estimated_metadata = {
        "market": "futures",
        "side": "long",
        "cost_model_status": "estimated_from_notional",
        "cost_source": "estimated_round_trip_notional",
    }

    for index in range(10):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"bn-recorded-negative-edge-{index}",
                symbol="ETHUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=99.8,
                qty=1.0,
                fees=0.01,
                funding=0.0,
                spread=0.0,
                slippage=0.0,
                filled=True,
                metadata=recorded_metadata,
            ),
            source={"metadata": recorded_metadata},
        )
    for index in range(5):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"bn-estimated-positive-edge-{index}",
                symbol="ETHUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=estimated_metadata,
            ),
            source={"metadata": estimated_metadata},
        )

    lane = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }[("binance", "futures_long")]

    assert lane["alpha_count"] == 15
    assert lane["alpha_net_pnl"] > 0
    assert lane["cost_precision_verified_rate"] == pytest.approx(66.666667)
    assert lane["cost_verified_alpha_count"] == 10
    assert lane["cost_unverified_alpha_count"] == 5
    assert lane["cost_verified_alpha_net_pnl"] < 0
    assert lane["cost_unverified_alpha_net_pnl"] > 0
    assert lane["scale_blocked_by_cost_precision"] is False
    assert lane["scale_blocked_by_verified_edge_samples"] is False
    assert lane["scale_blocked_by_verified_edge_net_pnl"] is True
    assert lane["scale_blocked_by_cost_evidence"] is True
    assert lane["verified_edge_net_cap_multiplier"] == 0.25
    assert lane["risk_budget_multiplier"] <= 0.25


def test_live_performance_lane_does_not_scale_with_missing_costs(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {"horizon": "mid"}
    for index in range(3):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-missing-cost-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=70_000,
                exit_price=72_000,
                qty=1,
                fees=0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    lane = lanes[("kis", "mid")]
    assert lane["alpha_count"] == 3
    assert lane["expectancy_pct"] > 0
    assert lane["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 0,
        "partial": 0,
        "missing": 3,
    }
    assert lane["cost_evidence_status"] == "estimated_or_missing"
    assert lane["alpha_evidence_status"] == "unverified_cost_alpha"
    assert lane["cost_verified_alpha_count"] == 0
    assert lane["cost_unverified_alpha_count"] == 3
    assert lane["cost_unverified_alpha_net_pnl"] > 0
    assert lane["scale_blocked_by_cost_evidence"] is True
    assert lane["scale_blocked_by_cost_precision"] is True
    assert lane["risk_budget_multiplier"] <= 0.5
    assert lane["quality_hint"] == "weak_review"
    assert lane["action_hint"] == "cost_evidence_repair_waiting_entry"


def test_live_performance_keeps_upbit_spot_separate_from_binance_spot(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    rows = [
        ("binance-spot", "BTCUSDT", {"market": "spot", "side": "long"}),
        ("upbit-spot", "KRW-BTC", {"market": "upbit_spot", "side": "long"}),
    ]
    for block_id, symbol, metadata in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=101.0,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    assert ("binance", "spot") in lanes
    assert ("binance", "upbit_spot") in lanes
    assert lanes[("binance", "spot")]["block_count"] == 1
    assert lanes[("binance", "upbit_spot")]["block_count"] == 1


def test_live_performance_lane_surfaces_non_alpha_conversion_reasons(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {"horizon": "short"}
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-closed-without-fill",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=70_000,
            qty=1,
            filled=False,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-error-pre-fill",
            symbol="005930",
            created_by="llm",
            status="error",
            entry_price=70_000,
            exit_price=70_000,
            qty=1,
            filled=False,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("kis", "short")]

    assert lane["block_count"] == 2
    assert lane["alpha_count"] == 0
    assert lane["non_alpha_count"] == 2
    assert lane["unfilled_or_unrealized_count"] == 1
    assert lane["operational_failure_pre_fill_count"] == 1
    assert lane["execution_quality_count"] == 2
    assert lane["attribution_counts"] == {
        "operational_failure_pre_fill": 1,
        "unfilled_or_unrealized": 1,
    }
    assert lane["alpha_conversion_status"] == (
        "blocked_by_fill_or_execution_evidence"
    )
    assert "repair fill evidence" in lane["alpha_conversion_repair_hint"]


def test_live_performance_lane_summary_exposes_risk_budget_inputs(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 1.0,
            "taxes": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
        },
        "entry_quality": "low_risk_pullback",
    }
    for index in range(8):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-mid-win-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=1_000,
                exit_price=1_015,
                qty=1,
                fees=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    for index in range(4):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-mid-loss-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=1_000,
                exit_price=997,
                qty=1,
                fees=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("kis", "mid")]

    assert lane["quality_hint"] == "scale_candidate"
    assert lane["cost_drag_pct_of_gross_pnl"] == pytest.approx(
        lane["cost_drag_pct_of_abs_gross_pnl"]
    )
    assert lane["entry_quality_sample_count"] == 12
    assert lane["avg_entry_quality_score"] == pytest.approx(80.0)
    assert lane["entry_quality_label_counts"] == {"low_risk_pullback": 12}
    assert lane["good_entry_quality_label_counts"] == {"low_risk_pullback": 12}
    assert lane["bad_entry_quality_label_counts"] == {}
    assert lane["dominant_good_entry_quality_label"] == "low_risk_pullback"
    assert lane["dominant_bad_entry_quality_label"] == ""
    assert lane["scale_blocked_by_entry_quality"] is False
    assert lane["lane_confidence_score"] > 0.85
    assert lane["risk_of_ruin_pct"] < 20
    assert lane["raw_fractional_kelly_fraction"] > 0
    assert lane["risk_budget_multiplier"] > 0.5
    assert lane["recommended_risk_fraction"] > 0.01
    assert lane["max_risk_cap_fraction"] == pytest.approx(0.025)


def test_live_performance_lane_risk_budget_caps_low_recovery_factor(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 0.0,
            "taxes": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
        },
        "entry_quality": "low_risk_pullback",
    }
    for index in range(9):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-mid-recovery-win-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=1_000,
                exit_price=1_008,
                qty=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-mid-recovery-loss",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=1_000,
            exit_price=960,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("kis", "mid")]

    assert lane["alpha_count"] == 10
    assert lane["profit_factor"] == pytest.approx(1.8)
    assert lane["max_drawdown_pct"] == pytest.approx(-4.0)
    assert lane["recovery_factor"] == pytest.approx(0.8)
    assert lane["quality_hint"] == "qualified"
    assert lane["recovery_factor_cap_multiplier"] == pytest.approx(0.75)
    assert lane["risk_budget_multiplier"] <= 0.75
    assert lane["recommended_risk_fraction"] <= 0.015


def test_live_performance_persists_entry_quality_from_metadata(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "long",
        "entry_quality": "extended_momentum",
        "entry_setup": "late_chase",
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-entry-quality",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=69_000,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]

    assert latest["entry_quality_label"] == "extended_momentum"
    assert latest["entry_quality_score"] == pytest.approx(35.0)


def test_live_performance_summary_exposes_bad_entry_quality_labels(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    for index in range(3):
        metadata = {
            "horizon": "short",
            "strategy_family": "late_chase",
            "entry_quality": "extended_momentum",
            "entry_setup": "late_chase",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-late-chase-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=80_000,
                exit_price=78_500,
                qty=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("kis", "short:late_chase")]

    assert lane["entry_quality_label_counts"] == {"extended_momentum": 3}
    assert lane["bad_entry_quality_label_counts"] == {"extended_momentum": 3}
    assert lane["dominant_bad_entry_quality_label"] == "extended_momentum"
    assert lane["scale_blocked_by_entry_quality"] is True


def test_live_performance_summary_exposes_validation_repair_enforcement(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "market": "futures",
        "side": "long",
        "validation_repair_enforcement": {
            "version": "validation_repair_enforcement_v1",
            "repair_action_ids": [
                "validation_repair.backtest_wfa_oos_rebuild.walk_forward_analysis"
            ],
            "scale_up_blocked": True,
            "waiting_entry_required": True,
            "budget_multiplier": 0.25,
            "adjustments": [
                {
                    "field": "quote_budget_usdt",
                    "from": 400.0,
                    "to": 100.0,
                    "reason": "validation_repair_scale_up_blocked_probe_budget",
                }
            ],
        },
    }
    for index in range(3):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"bnb-repair-{index}",
                symbol="BTCUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=101.0,
                qty=1.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("binance", "futures_long")]

    assert lane["validation_repair_enforced_count"] == 3
    assert lane["validation_repair_scale_up_blocked_count"] == 3
    assert lane["validation_repair_waiting_entry_count"] == 3
    assert lane["validation_repair_avg_budget_multiplier"] == pytest.approx(0.25)
    assert lane["validation_repair_action_counts"] == {
        "validation_repair.backtest_wfa_oos_rebuild.walk_forward_analysis": 3
    }
    assert lane["validation_repair_adjustment_reason_counts"] == {
        "validation_repair_scale_up_blocked_probe_budget": 3
    }


def test_live_performance_scores_high_chase_without_pullback_as_bad_entry(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "long",
        "chase_risk": "high",
        "price_location": "near_20d_high",
        "valuation_label": "undervalued",
        "regime_alignment": "positive aligned",
        "pullback_confirmed": False,
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-high-chase",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=80_000,
            exit_price=78_500,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]

    assert latest["entry_quality_label"] == "high_chase_without_pullback"
    assert latest["entry_quality_score"] == pytest.approx(35.0)


def test_live_performance_scores_regime_mismatch_as_bad_entry(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "long",
        "price_location": "middle",
        "regime_alignment": "misaligned",
        "market_regime": "risk_off",
        "pullback_confirmed": False,
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-regime-mismatch",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=80_000,
            exit_price=78_500,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]

    assert latest["entry_quality_label"] == "regime_mismatch_without_price_relief"
    assert latest["entry_quality_score"] == pytest.approx(45.0)


def test_live_performance_bad_entry_rate_counts_bad_labels_above_score_floor(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "horizon": "mid",
        "strategy_family": "value_pullback",
        "entry_quality": "regime_mismatch_waiting_entry",
        "entry_quality_score": 58.0,
    }
    for index in range(3):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-regime-label-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=80_000,
                exit_price=80_500,
                qty=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }
    lane = lanes[("kis", "mid:value_pullback")]

    assert lane["avg_entry_quality_score"] == pytest.approx(58.0)
    assert lane["bad_entry_quality_rate_pct"] == pytest.approx(100.0)
    assert lane["bad_entry_quality_label_counts"] == {
        "regime_mismatch_waiting_entry": 3
    }
    assert lane["scale_blocked_by_entry_quality"] is True
    assert lane["risk_budget_multiplier"] <= 0.5


def test_live_performance_scores_pullback_relief_as_good_entry(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "long",
        "entry_style": "wait_for_price",
        "price_location": "near_support",
        "pullback_confirmed": True,
        "regime_alignment": "risk_on aligned",
        "supply_recovery": "foreign flow recovery",
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-pullback",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=74_000,
            exit_price=77_000,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="kis", limit=1)[0]

    assert latest["entry_quality_label"] == "wait_for_price"
    assert latest["entry_quality_score"] == pytest.approx(80.0)


def test_live_performance_reads_nested_entry_quality_gate(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    metadata = {
        "side": "short",
        "entry_quality_gate": {
            "chase_risk": "very_high",
            "price_location": "near_24h_high",
            "pullback_confirmed": False,
        },
    }

    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-high-chase",
            symbol="BTCUSDT",
            created_by="llm",
            status="closed",
            entry_price=100.0,
            exit_price=99.0,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    latest = repo.latest(venue="binance", limit=1)[0]

    assert latest["entry_quality_label"] == "high_chase_without_pullback"
    assert latest["entry_quality_score"] == pytest.approx(35.0)


def test_live_performance_summary_splits_kis_and_binance_lanes(tmp_path) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    samples = [
        (
            BlockPerformanceInput(
                venue="kis",
                block_id="kis-mid",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=70_000,
                exit_price=72_000,
                qty=1,
                fees=100,
                filled=True,
                metadata={"horizon": "mid"},
            ),
            {"metadata": {"horizon": "mid"}},
        ),
        (
            BlockPerformanceInput(
                venue="kis",
                block_id="kis-etf",
                symbol="069500",
                created_by="llm",
                status="closed",
                entry_price=42_000,
                exit_price=42_500,
                qty=1,
                fees=20,
                filled=True,
                metadata={"horizon": "core_etf", "name": "KODEX 200"},
            ),
            {"metadata": {"horizon": "core_etf", "name": "KODEX 200"}},
        ),
        (
            BlockPerformanceInput(
                venue="kis",
                block_id="kis-short-chase",
                symbol="277810",
                created_by="llm",
                status="closed",
                entry_price=50_000,
                exit_price=49_000,
                qty=1,
                fees=100,
                filled=True,
                metadata={"horizon": "short", "strategy_family": "late_chase"},
            ),
            {"metadata": {"horizon": "short", "strategy_family": "late_chase"}},
        ),
        (
            BlockPerformanceInput(
                venue="binance",
                block_id="bn-spot",
                symbol="ETHUSDT",
                created_by="llm",
                status="closed",
                entry_price=100,
                exit_price=101,
                qty=1,
                fees=0.1,
                filled=True,
                metadata={"market": "spot", "side": "long"},
            ),
            {"metadata": {"market": "spot", "side": "long"}},
        ),
        (
            BlockPerformanceInput(
                venue="binance",
                block_id="bn-short",
                symbol="BTCUSDT",
                created_by="llm",
                status="closed",
                entry_price=100,
                exit_price=99,
                qty=1,
                fees=0.1,
                filled=True,
                metadata={"market": "futures", "side": "short"},
            ),
            {"metadata": {"market": "futures", "side": "short"}},
        ),
        (
            BlockPerformanceInput(
                venue="binance",
                block_id="bn-volatile",
                symbol="ALTUSDT",
                created_by="llm",
                status="closed",
                entry_price=10,
                exit_price=9,
                qty=1,
                fees=0.1,
                filled=True,
                metadata={"market": "futures", "side": "long", "lane": "volatile_attack"},
            ),
            {
                "metadata": {
                    "market": "futures",
                    "side": "long",
                    "lane": "volatile_attack",
                }
            },
        ),
    ]
    for row, source in samples:
        repo.upsert_performance(row, source=source)

    summary = repo.summary()
    lanes = {
        (row["venue"], row["lane"]): row
        for row in summary["lanes"]
    }

    assert lanes[("kis", "mid")]["alpha_count"] == 1
    assert lanes[("kis", "mid")]["sample_count"] == 1
    assert lanes[("kis", "short")]["alpha_count"] == 1
    assert lanes[("kis", "short")]["sample_count"] == 1
    assert lanes[("kis", "short:late_chase")]["alpha_count"] == 1
    assert lanes[("kis", "short:late_chase")]["sample_count"] == 1
    assert lanes[("kis", "core_etf")]["alpha_count"] == 1
    assert lanes[("kis", "core_etf")]["sample_count"] == 1
    assert lanes[("binance", "spot")]["alpha_count"] == 1
    assert lanes[("binance", "spot")]["sample_count"] == 1
    assert lanes[("binance", "futures")]["alpha_count"] == 1
    assert lanes[("binance", "futures")]["sample_count"] == 1
    assert lanes[("binance", "futures_short")]["alpha_count"] == 1
    assert lanes[("binance", "futures_short")]["sample_count"] == 1
    assert lanes[("binance", "volatile_attack")]["alpha_count"] == 1
    assert lanes[("binance", "volatile_attack")]["sample_count"] == 1
    assert lanes[("kis", "mid")]["alpha_net_pnl"] == pytest.approx(1_900)
    assert lanes[("kis", "mid")]["alpha_gross_pnl"] == pytest.approx(2_000)
    assert lanes[("kis", "mid")]["expectancy_pct"] == pytest.approx(
        1_900 / 70_000 * 100
    )
    assert lanes[("kis", "mid")]["profit_factor"] == pytest.approx(999.0)
    assert lanes[("kis", "mid")]["max_drawdown_pct"] == pytest.approx(0.0)
    assert lanes[("kis", "mid")]["recovery_factor"] == pytest.approx(999.0)
    assert lanes[("kis", "mid")]["cost_drag_pct_of_abs_gross_pnl"] == pytest.approx(
        5.0
    )
    assert lanes[("kis", "mid")]["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 0,
        "estimated": 0,
        "partial": 0,
        "missing": 1,
    }
    assert lanes[("kis", "short")]["quality_hint"] == "sample_building"
    assert lanes[("kis", "short:late_chase")]["quality_hint"] == "sample_building"
    assert lanes[("binance", "futures")]["quality_hint"] == "sample_building"
    assert lanes[("kis", "mid")]["quality_hint"] == "sample_building"
    assert lanes[("binance", "volatile_attack")]["quality_hint"] == "sample_building"


def test_live_performance_normalizes_lane_qualified_setups_and_etf_names(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-qualified-setup",
            symbol="277810",
            created_by="llm",
            status="closed",
            entry_price=50_000,
            exit_price=49_000,
            qty=1,
            fees=100,
            filled=True,
            metadata={
                "horizon": "short",
                "strategy_family": "short:late_chase",
            },
        ),
        source={
            "metadata": {
                "horizon": "short",
                "strategy_family": "short:late_chase",
            }
        },
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-hanaro-etf",
            symbol="293180",
            created_by="llm",
            status="closed",
            entry_price=10_000,
            exit_price=10_200,
            qty=1,
            fees=20,
            filled=True,
            metadata={"horizon": "mid", "name": "HANARO 200"},
        ),
        source={"metadata": {"horizon": "mid", "name": "HANARO 200"}},
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-freeform-setup",
            symbol="003550",
            created_by="llm",
            status="closed",
            entry_price=100_000,
            exit_price=101_000,
            qty=1,
            fees=50,
            filled=True,
            metadata={
                "horizon": "mid",
                "entry_setup": "기존 31,400원 이하 가치 눌림 1주 실험",
            },
        ),
        source={
            "metadata": {
                "horizon": "mid",
                "entry_setup": "기존 31,400원 이하 가치 눌림 1주 실험",
            }
        },
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-family-over-freeform",
            symbol="001750",
            created_by="llm",
            status="closed",
            entry_price=20_000,
            exit_price=20_400,
            qty=1,
            fees=20,
            filled=True,
            metadata={
                "horizon": "mid",
                "strategy_family": "value_pullback",
                "entry_setup": "기존 48,500원 이하 눌림 진입 블록의 stop 이탈",
            },
        ),
        source={
            "metadata": {
                "horizon": "mid",
                "strategy_family": "value_pullback",
                "entry_setup": "기존 48,500원 이하 눌림 진입 블록의 stop 이탈",
            }
        },
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-colon-lane",
            symbol="BTCUSDT",
            created_by="llm",
            status="closed",
            entry_price=100,
            exit_price=99,
            qty=1,
            fees=0.1,
            filled=True,
            metadata={"lane": "futures:short", "side": "short"},
        ),
        source={"metadata": {"lane": "futures:short", "side": "short"}},
    )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    assert ("kis", "short:short:late_chase") not in lanes
    assert not any(
        venue == "kis" and "기존" in lane
        for venue, lane in lanes
    )
    assert lanes[("kis", "short:late_chase")]["alpha_count"] == 1
    assert lanes[("kis", "short")]["alpha_count"] == 1
    assert lanes[("kis", "mid")]["alpha_count"] == 2
    assert lanes[("kis", "mid:value_pullback")]["alpha_count"] == 1
    assert lanes[("kis", "core_etf")]["alpha_count"] == 1
    assert lanes[("binance", "futures_short")]["alpha_count"] == 1
    assert lanes[("binance", "futures")]["alpha_count"] == 1


def test_live_performance_normalizes_extended_etf_and_binance_alias_lanes(
    tmp_path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-plus-etf",
            symbol="455850",
            created_by="llm",
            status="closed",
            entry_price=10_000,
            exit_price=10_300,
            qty=1,
            fees=20,
            filled=True,
            metadata={"horizon": "mid", "name": "PLUS 고배당주"},
        ),
        source={"metadata": {"horizon": "mid", "name": "PLUS 고배당주"}},
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-perp-short",
            symbol="ETHUSDT",
            created_by="llm",
            status="closed",
            entry_price=100,
            exit_price=98,
            qty=1,
            fees=0.1,
            filled=True,
            metadata={"lane": "perp_short", "market": "perpetual"},
        ),
        source={"metadata": {"lane": "perp_short", "market": "perpetual"}},
    )

    lanes = {
        (row["venue"], row["lane"]): row
        for row in repo.summary()["lanes"]
    }

    assert lanes[("kis", "core_etf")]["alpha_count"] == 1
    assert ("kis", "mid") not in lanes
    assert lanes[("binance", "futures_short")]["alpha_count"] == 1
    assert lanes[("binance", "futures")]["alpha_count"] == 1
