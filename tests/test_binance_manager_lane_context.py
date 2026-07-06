from __future__ import annotations

import pytest

from tradecraft.services.binance_manager_lane_context import (
    candidate_near_duplicate_active_block_context,
    candidate_lane_authority_context,
    lane_authority_key_variants,
    lane_distribution,
    manager_lane_balance_context,
    near_duplicate_active_blocks_context,
    row_pattern_live_crosscheck_status,
)


def test_lane_distribution_counts_canonical_binance_lanes() -> None:
    payload = lane_distribution(
        [
            {"market": "spot", "side": "long"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
        ]
    )

    assert payload["total"] == 3
    assert payload["items"]["spot:long"]["count"] == 1
    assert payload["items"]["futures:short"]["count"] == 2
    assert payload["dominant_lane"] == "futures:short"


def test_near_duplicate_context_groups_active_blocks_by_price_geometry() -> None:
    payload = near_duplicate_active_blocks_context(
        [
            {
                "block_id": "a",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 100.0,
                "target_price": 90.0,
                "stop_price": 105.0,
                "metadata": {"horizon": "futures"},
            },
            {
                "block_id": "b",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "status": "proposed",
                "entry_price": 100.2,
                "target_price": 90.1,
                "stop_price": 105.1,
                "metadata": {"horizon": "futures"},
            },
        ],
        tolerance_bps=75.0,
    )

    assert payload["status"] == "review_required"
    assert payload["groups"][0]["block_ids"] == ["a", "b"]


def test_candidate_duplicate_context_requires_same_horizon_and_prices() -> None:
    payload = candidate_near_duplicate_active_block_context(
        {
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "entry_price": 4172.94,
            "target_price": 4106.18,
            "stop_price": 4206.33,
        },
        [
            {
                "block_id": "bnb_futures_PAXGUSDT_open",
                "symbol": "PAXGUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 4154.65,
                "target_price": 4088.18,
                "stop_price": 4187.89,
                "metadata": {"horizon": "futures"},
            }
        ],
        tolerance_bps=75.0,
    )

    assert payload["status"] == "review_required"
    assert payload["existing_block_id"] == "bnb_futures_PAXGUSDT_open"
    assert payload["candidate"]["entry_price"] == pytest.approx(4172.94)


def test_row_pattern_live_crosscheck_status_reads_row_metadata_and_price_plan() -> None:
    assert (
        row_pattern_live_crosscheck_status(
            {"pattern_live_crosscheck": {"status": "passed"}}
        )
        == "passed"
    )
    assert (
        row_pattern_live_crosscheck_status(
            {"metadata": {"pattern_live_crosscheck": {"status": "blocked"}}}
        )
        == "blocked"
    )
    assert (
        row_pattern_live_crosscheck_status(
            {
                "metadata": {
                    "calculated_price_plan": {
                        "pattern_live_crosscheck": {"status": "shadow_only"}
                    }
                }
            }
        )
        == "shadow_only"
    )
    assert row_pattern_live_crosscheck_status({}) == ""


def test_manager_lane_balance_context_combines_recent_active_candidate_and_performance() -> None:
    payload = manager_lane_balance_context(
        recent_blocks=[
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "spot", "side": "long"},
        ],
        active_blocks=[],
        candidates=[{"market": "spot", "side": "long"}],
        performance={"side_scorecards": [{"side": "spot:long", "sample_count": 2}]},
        tolerance_bps=75.0,
    )

    assert payload["version"] == "binance_lane_balance_v1"
    assert payload["dominant_lane"] == "futures:short"
    assert payload["recent_blocks"]["requires_review"] is True
    assert payload["candidate_lanes"]["items"]["spot:long"]["count"] == 1
    assert payload["performance_lanes"][0]["lane"] == "spot:long"


def test_lane_authority_key_variants_normalize_market_side_aliases() -> None:
    variants = lane_authority_key_variants("futures_short")

    assert "futures_short" in variants
    assert "futures:short" in variants

    spot_variants = lane_authority_key_variants("spot:long")
    assert "spot" in spot_variants
    assert "spot:long" in spot_variants


def test_candidate_lane_authority_context_prefers_positive_sample_building_lane() -> None:
    context = candidate_lane_authority_context(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": ["futures:short:validation:capacity_analysis"],
                "insufficient_lanes": [
                    "futures:short:validation:capacity_analysis",
                    "futures:short:futures",
                ],
                "lane_actions": {
                    "futures:short:validation:capacity_analysis": {
                        "grade": "insufficient",
                        "action": "validation_evidence_repair_waiting_probe",
                        "sample_count": 1,
                        "expectancy_pct": -0.04,
                        "profit_factor": 0.0,
                        "requires_waiting_entry": True,
                    },
                    "futures:short:futures": {
                        "grade": "insufficient",
                        "action": "validation_evidence_repair_waiting_probe",
                        "sample_count": 7,
                        "expectancy_pct": 0.25,
                        "profit_factor": 2.13,
                        "requires_waiting_entry": True,
                    },
                },
            }
        },
        {
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
        },
    )

    assert context["version"] == "binance_lane_authority_candidate_v1"
    assert context["lane"] == "futures:short:futures"
    assert context["selection_bias"] == "positive_sample_building"
    assert context["profit_factor"] == pytest.approx(2.13)


def test_candidate_lane_authority_context_falls_back_to_weak_lane_list() -> None:
    context = candidate_lane_authority_context(
        {"lane_authority": {"weak_lanes": ["spot"]}},
        {"symbol": "BTCUSDT", "market": "spot", "side": "long", "horizon": "short"},
    )

    assert context["lane"] == "spot"
    assert context["grade"] == "weak"
    assert context["selection_bias"] == "avoid_weak_lane"
