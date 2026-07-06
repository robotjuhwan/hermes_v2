from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.kis_entry_gate import (
    ENTRY_QUALITY_TEXT_FIELDS,
    apply_policy_relative_price_effects,
    create_row_entry_quality_gate,
    entry_quality_fields,
    entry_trigger_reached,
    invalid_long_price_structure_reason,
    kis_buy_fill_update_plan,
    long_reward_risk,
    normalize_entry_style,
    performance_scale_entry_quality_check,
    normalize_entry_trigger_operator,
    policy_effect_derived_trigger_price,
    policy_effect_qty_adjusted,
    policy_effect_trigger_price,
    policy_effect_waiting_required,
    policy_effect_audit,
    policy_target_stop_quality_gate,
)


def test_kis_block_trader_does_not_reown_entry_gate_price_policy_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _long_reward_risk(" not in source
    assert "def _policy_target_stop_quality_gate(" not in source
    assert "def _invalid_long_price_structure_reason(" not in source
    assert "def _normalize_entry_style(" not in source
    assert "def _normalize_entry_trigger_operator(" not in source
    assert "def _entry_trigger_reached(" not in source
    assert "def _policy_effect_trigger_price(" not in source
    assert "def _policy_effect_waiting_required(" not in source
    assert "def _policy_first_positive_float(" not in source
    assert "def _policy_reference_entry_price(" not in source
    assert "def _policy_effect_derived_trigger_price(" not in source
    assert "def _apply_policy_relative_price_effects(" not in source
    assert "def _policy_effect_qty_adjusted(" not in source
    assert "def _entry_quality_fields(" not in source
    assert "def _performance_scale_entry_quality_check(" not in source
    assert "def _create_row_entry_quality_gate(" not in source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("wait_for_price", "wait_for_price"),
        ("wait-price", "wait_for_price"),
        ("pullback", "wait_for_price"),
        ("triggered_limit", "wait_for_price"),
        ("aggressive_limit", "aggressive_limit"),
        ("", "aggressive_limit"),
    ],
)
def test_normalize_entry_style_maps_waiting_aliases(raw: str, expected: str) -> None:
    assert normalize_entry_style(raw) == expected


@pytest.mark.parametrize(
    ("raw", "trigger_price", "reference_price", "expected"),
    [
        ("lte", 0, 0, "lte"),
        ("<=", 0, 0, "lte"),
        ("pullback", 0, 0, "lte"),
        ("gte", 0, 0, "gte"),
        (">=", 0, 0, "gte"),
        ("breakout", 0, 0, "gte"),
        ("", 98_000, 100_000, "lte"),
        ("", 102_000, 100_000, "gte"),
        ("", 0, 0, "lte"),
    ],
)
def test_normalize_entry_trigger_operator_uses_aliases_and_reference_price(
    raw: str,
    trigger_price: float,
    reference_price: float,
    expected: str,
) -> None:
    assert (
        normalize_entry_trigger_operator(
            raw,
            trigger_price=trigger_price,
            reference_price=reference_price,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("price", "trigger_price", "operator", "expected"),
    [
        (98_000, 98_000, "lte", True),
        (97_900, 98_000, "lte", True),
        (98_100, 98_000, "lte", False),
        (102_000, 102_000, "gte", True),
        (102_100, 102_000, "gte", True),
        (101_900, 102_000, "gte", False),
        (0, 98_000, "lte", False),
        (98_000, 0, "lte", False),
    ],
)
def test_entry_trigger_reached_compares_lte_and_gte(
    price: float,
    trigger_price: float,
    operator: str,
    expected: bool,
) -> None:
    assert (
        entry_trigger_reached(
            price,
            trigger_price=trigger_price,
            operator=operator,
        )
        is expected
    )


def test_entry_quality_fields_compacts_text_score_and_bool_values() -> None:
    row = {
        "entry_quality": "눌림 확인 " * 80,
        "chase_risk": "high",
        "price_location": "near_support",
        "technical_setup": "pullback reclaim",
        "ignored": "DROP",
        "entry_quality_score": "-12",
        "pullback_confirmed": "yes",
    }

    fields = entry_quality_fields(row)

    assert set(fields) == {
        "entry_quality",
        "chase_risk",
        "price_location",
        "technical_setup",
        "entry_quality_score",
        "pullback_confirmed",
    }
    assert len(fields["entry_quality"]) <= ENTRY_QUALITY_TEXT_FIELDS["entry_quality"]
    assert fields["entry_quality_score"] == 0.0
    assert fields["pullback_confirmed"] is True


def test_performance_scale_entry_quality_check_allows_scale_on_pullback_confluence() -> None:
    result = performance_scale_entry_quality_check(
        {
            "entry_style": "wait_for_price",
            "price_location": "near_support",
            "valuation_label": "undervalued",
            "regime_alignment": "risk_on",
            "supply_recovery": "foreign flow recovery",
            "entry_quality_score": 72,
        }
    )

    assert result["version"] == "kis_performance_scale_entry_quality_v1"
    assert result["scale_up_allowed"] is True
    assert result["reliefs"] == ["waiting_entry_structure", "low_risk_price_location"]
    assert set(result["confluence"]) == {
        "valuation_discount",
        "regime_aligned",
        "flow_recovery",
    }
    assert result["pressure"] == []
    assert result["entry_quality_score"] == 72.0


def test_performance_scale_entry_quality_check_blocks_extended_chase() -> None:
    result = performance_scale_entry_quality_check(
        {
            "metadata": {
                "entry_quality": "extended_momentum",
                "chase_risk": "high",
                "price_location": "near_52w_high",
                "entry_quality_score": 44,
            }
        }
    )

    assert result["scale_up_allowed"] is False
    assert "extended_momentum" in result["pressure"]
    assert "chase_risk_high" in result["pressure"]
    assert "price_location_near_52w_high" in result["pressure"]
    assert "entry_quality_score_below_55" in result["pressure"]


def test_create_row_entry_quality_gate_requires_waiting_for_chase_without_relief() -> None:
    gate = create_row_entry_quality_gate(
        {
            "entry_style": "aggressive_limit",
            "entry_quality": "late_chase",
            "chase_risk": "high",
            "price_location": "near_20d_high",
            "entry_quality_score": 40,
        }
    )

    assert gate["version"] == "kis_entry_quality_gate_v1"
    assert gate["allowed"] is False
    assert gate["requires_waiting_entry"] is True
    assert gate["waiting_entry_preferred"] is True
    assert gate["hard_pressure"] is True
    assert "late_chase" in gate["reasons"]
    assert "chase_risk_high" in gate["reasons"]
    assert gate["entry_quality_score"] == 40.0


def test_create_row_entry_quality_gate_allows_pullback_reclaim_with_confluence() -> None:
    gate = create_row_entry_quality_gate(
        {
            "entry_style": "aggressive_limit",
            "entry_quality": "momentum_only",
            "price_location": "pullback support",
            "technical_setup": "pullback reclaim",
            "valuation_label": "discount",
            "sector_rotation": "leader rotation",
            "pullback_confirmed": "yes",
        }
    )

    assert gate["allowed"] is True
    assert gate["requires_waiting_entry"] is False
    assert gate["price_relief_present"] is True
    assert "pullback_confirmed" in gate["reliefs"]
    assert "low_risk_price_location" in gate["reliefs"]
    assert "valuation_discount" in gate["confluence"]
    assert "sector_rotation" in gate["confluence"]


@pytest.mark.parametrize(
    ("reference_price", "target_price", "stop_price", "expected"),
    [
        (100_000, 110_000, 95_000, ""),
        (0, 110_000, 95_000, "reference_price_missing"),
        (100_000, 0, 95_000, "target_or_stop_missing"),
        (100_000, 110_000, 0, "target_or_stop_missing"),
        (100_000, 99_000, 95_000, "invalid_target_stop_bounds"),
        (100_000, 110_000, 101_000, "invalid_target_stop_bounds"),
    ],
)
def test_invalid_long_price_structure_reason_requires_stop_reference_target_order(
    reference_price: float,
    target_price: float,
    stop_price: float,
    expected: str,
) -> None:
    assert (
        invalid_long_price_structure_reason(
            reference_price=reference_price,
            target_price=target_price,
            stop_price=stop_price,
        )
        == expected
    )


def test_long_reward_risk_calculates_reward_and_stop_risk() -> None:
    structure = long_reward_risk(
        entry_price=100_000,
        target_price=112_000,
        stop_price=96_000,
    )

    assert structure["status"] == "ok"
    assert structure["reward_risk"] == pytest.approx(3.0)
    assert structure["stop_risk_pct"] == pytest.approx(4.0)


def test_policy_target_stop_quality_gate_rejects_weak_policy_price_structure() -> None:
    gate = policy_target_stop_quality_gate(
        {
            "entry_price": 100_000,
            "target_price": 104_000,
            "stop_price": 96_000,
        },
        [
            {
                "rule_id": "rule-min-rr",
                "effect": {"min_reward_risk": 1.5, "max_stop_risk_pct": 3.0},
            }
        ],
    )

    assert gate["version"] == "policy_target_stop_quality_v1"
    assert gate["rejected"] is True
    assert gate["rule_id"] == "rule-min-rr"
    assert gate["reason"] in {
        "policy_min_reward_risk_not_met",
        "policy_max_stop_risk_pct_exceeded",
    }
    assert gate["checks"][0]["reward_risk"] == pytest.approx(1.0)
    assert gate["checks"][0]["stop_risk_pct"] == pytest.approx(4.0)


def test_policy_effect_waiting_required_detects_direct_tokens_and_prices() -> None:
    assert policy_effect_waiting_required({"requires_waiting_entry": "yes"}) is True
    assert policy_effect_waiting_required({"entry_bias": "pullback_wait"}) is True
    assert policy_effect_waiting_required({"entry_trigger_price": 98_000}) is True
    assert policy_effect_waiting_required({"entry_discount_pct": 2.0}) is True
    assert policy_effect_waiting_required({"note": "observe"}) is False


def test_policy_effect_trigger_and_derived_trigger_price_use_krx_ticks() -> None:
    assert policy_effect_trigger_price({"pullback_price": "98,100"}) == 98_100.0

    trigger_price, effect_key = policy_effect_derived_trigger_price(
        {"entry_discount_pct": 2.0},
        reference_entry_price=100_000,
    )

    assert trigger_price == 98_000.0
    assert effect_key == "entry_discount_pct"


def test_policy_effect_qty_adjusted_caps_and_scales_positive_qty() -> None:
    assert policy_effect_qty_adjusted(10, {"max_qty": 3}) == 3
    assert policy_effect_qty_adjusted(10, {"qty_multiplier": 0.4}) == 4
    assert policy_effect_qty_adjusted(1, {"qty_multiplier": 0.1}) == 1
    assert policy_effect_qty_adjusted(0, {"max_qty": 3}) == 0


def test_policy_effect_audit_maps_effect_keys_to_block_fields() -> None:
    audit = policy_effect_audit(
        [
            {
                "rule_id": "rule-entry",
                "policy_id": "policy-a",
                "status": "active",
                "effect": {
                    "entry_bias": "wait_for_price",
                    "risk_note": "crowded",
                    "target_stop_review": "required",
                    "qty_multiplier": 0.5,
                },
            }
        ]
    )

    assert audit == {
        "version": "policy_effect_audit_v1",
        "mode": "advisory",
        "affected_fields": ["entry_style", "qty", "risk_note", "target_price", "stop_price"],
        "rules": [
            {
                "rule_id": "rule-entry",
                "policy_id": "policy-a",
                "status": "active",
                "affected_fields": ["entry_style", "qty", "risk_note", "target_price", "stop_price"],
                "effect_keys": [
                    "entry_bias",
                    "qty_multiplier",
                    "risk_note",
                    "target_stop_review",
                ],
            }
        ],
    }


def test_apply_policy_relative_price_effects_updates_stop_target_and_adjustments() -> None:
    row = {
        "entry_price": 100_000,
        "stop_price": 95_000,
        "target_price": 108_000,
    }
    adjustments: list[dict[str, object]] = []

    apply_policy_relative_price_effects(
        row,
        {"stop_risk_pct": 3.0, "target_reward_risk": 2.0},
        rule_id="rule-price",
        adjustments=adjustments,
    )

    assert row["stop_price"] == 97_000.0
    assert row["target_price"] == 106_000.0
    assert [item["field"] for item in adjustments] == ["stop_price", "target_price"]
    assert adjustments[0]["method"] == "risk_pct"
    assert adjustments[1]["method"] == "reward_risk"


def test_kis_buy_fill_update_plan_opens_filled_entry() -> None:
    plan = kis_buy_fill_update_plan(
        block={"entry_price": 95_000},
        filled_qty=3,
        avg_price=97_000,
        order_status="filled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "opened",
        "filled_qty": 3,
        "update_fields": {
            "status": "open",
            "qty_open": 3,
            "entry_price": 97_000.0,
            "opened_at": "2026-06-20T01:02:03+00:00",
            "llm_reason": "filled_reconciled_by_order",
        },
    }


def test_kis_buy_fill_update_plan_records_partial_entry() -> None:
    plan = kis_buy_fill_update_plan(
        block={"entry_price": 95_000},
        filled_qty=2,
        avg_price=0,
        order_status="partially_filled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "partial",
        "filled_qty": 2,
        "update_fields": {
            "qty_open": 2,
            "entry_price": 95_000,
            "llm_reason": "partial_entry_reconciled",
        },
    }


def test_kis_buy_fill_update_plan_marks_canceled_entry_open_when_partly_filled() -> None:
    plan = kis_buy_fill_update_plan(
        block={"entry_price": 95_000},
        filled_qty=1,
        avg_price=96_000,
        order_status="canceled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "canceled_open",
        "filled_qty": 1,
        "update_fields": {
            "status": "open",
            "qty_open": 1,
            "entry_price": 96_000.0,
            "opened_at": "2026-06-20T01:02:03+00:00",
            "llm_reason": "entry_order_canceled",
        },
    }


def test_kis_buy_fill_update_plan_marks_canceled_entry_error_when_unfilled() -> None:
    plan = kis_buy_fill_update_plan(
        block={"entry_price": 95_000},
        filled_qty=0,
        avg_price=0,
        order_status="canceled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "canceled_unfilled",
        "filled_qty": 0,
        "update_fields": {
            "status": "error",
            "qty_open": 0,
            "entry_price": 95_000,
            "opened_at": "",
            "llm_reason": "entry_order_canceled",
        },
    }
