from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.binance_policy_effects import (
    apply_policy_relative_price_effects,
    policy_effect_audit,
    policy_impacts_for_row,
    policy_effect_qty_adjusted,
    policy_effect_waiting_required,
    policy_rule_ids,
    policy_target_stop_quality_gate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_binance_block_trader_does_not_reown_policy_effect_wrappers() -> None:
    source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def _normalized_gate_token(",
        "def _contains_any_gate_token(",
        "def _truthy_gate_value(",
        "def _policy_effects(",
        "def _policy_effect_waiting_required(",
        "def _policy_effect_trigger_price(",
        "def _policy_reference_entry_price(",
        "def _policy_effect_derived_trigger_price(",
        "def _apply_policy_relative_price_effects(",
        "def _policy_effect_qty_adjusted(",
        "def _policy_target_stop_quality_gate(",
        "def _policy_effect_audit(",
        "def _policy_rule_ids(",
        "def _manager_policy_impacts_for_row(",
    ):
        assert marker not in source
    assert "build_policy_impacts_for_row(" in source


def test_policy_effect_audit_maps_effect_keys_to_affected_fields() -> None:
    impacts = [
        {
            "rule_id": "rule-a",
            "policy_id": "policy-a",
            "status": "active",
            "effect": {
                "entry_pullback_pct": 1.5,
                "target_reward_risk": 2.0,
                "stop_risk_pct": 3.0,
                "qty_multiplier": 0.5,
            },
        },
        {"policy_id": "policy-b", "effect": {"target_stop_review": True}},
    ]

    assert policy_effect_audit(impacts) == {
        "version": "policy_effect_audit_v1",
        "mode": "advisory",
        "affected_fields": [
            "entry_style",
            "qty",
            "stop_price",
            "target_price",
        ],
        "rules": [
            {
                "rule_id": "rule-a",
                "policy_id": "policy-a",
                "status": "active",
                "affected_fields": [
                    "entry_style",
                    "qty",
                    "stop_price",
                    "target_price",
                ],
                "effect_keys": [
                    "entry_pullback_pct",
                    "qty_multiplier",
                    "stop_risk_pct",
                    "target_reward_risk",
                ],
            },
            {
                "rule_id": "policy-b",
                "policy_id": "policy-b",
                "status": "",
                "affected_fields": [
                    "entry_style",
                    "target_price",
                    "stop_price",
                ],
                "effect_keys": ["target_stop_review"],
            },
        ],
    }


def test_policy_rule_ids_deduplicates_rule_and_policy_ids() -> None:
    assert policy_rule_ids(
        [
            {"rule_id": "a", "policy_id": "p-a"},
            {"rule_id": "a", "policy_id": "p-a"},
            {"policy_id": "p-b"},
            {},
        ]
    ) == ["a", "p-b"]


def test_policy_impacts_for_row_combines_global_and_symbol_specific_impacts() -> None:
    impacts_by_key = {
        "_global": [
            {"rule_id": "global-a", "effect": {"entry_pullback_pct": 1.0}},
            {"rule_id": "dup", "effect": {"min_reward_risk": 2.0}},
        ],
        "BTCUSDT": [
            {"rule_id": "dup", "effect": {"qty_multiplier": 0.5}},
            {"policy_id": "btc-only", "effect": {"target_stop_review": True}},
        ],
        "ETHUSDT": [{"rule_id": "wrong-symbol"}],
    }

    assert policy_impacts_for_row(impacts_by_key, {"symbol": "btcusdt"}) == [
        {"rule_id": "global-a", "effect": {"entry_pullback_pct": 1.0}},
        {"rule_id": "dup", "effect": {"min_reward_risk": 2.0}},
        {"policy_id": "btc-only", "effect": {"target_stop_review": True}},
    ]


def test_policy_effect_waiting_required_detects_price_and_language_tokens() -> None:
    assert policy_effect_waiting_required({"entry_pullback_pct": 1.5}) is True
    assert policy_effect_waiting_required({"entry_requirement": "눌림 대기"}) is True
    assert policy_effect_waiting_required({"requires_waiting_entry": "yes"}) is True
    assert policy_effect_waiting_required({"entry_requirement": "normal"}) is False


def test_apply_policy_relative_price_effects_reprices_long_target_stop() -> None:
    row = {"side": "long", "entry_price": 10.0, "target_price": 11.0, "stop_price": 9.5}
    adjustments: list[dict[str, object]] = []

    apply_policy_relative_price_effects(
        row,
        {"stop_risk_pct": 3.0, "target_reward_risk": 2.0},
        rule_id="rule-a",
        adjustments=adjustments,
    )

    assert row["stop_price"] == 9.7
    assert row["target_price"] == 10.6
    assert [item["field"] for item in adjustments] == ["stop_price", "target_price"]
    assert adjustments[0]["method"] == "risk_pct"
    assert adjustments[1]["method"] == "reward_risk"


def test_policy_effect_qty_adjusted_applies_cap_and_multiplier() -> None:
    assert policy_effect_qty_adjusted(
        10.0,
        {"max_qty": 8.0, "risk_budget_multiplier": 0.25},
    ) == 2.5


def test_policy_target_stop_quality_gate_rejects_weak_reward_risk() -> None:
    gate = policy_target_stop_quality_gate(
        {"side": "long", "entry_price": 100.0, "target_price": 102.0, "stop_price": 98.0},
        [{"rule_id": "rr", "effect": {"min_reward_risk": 2.0}}],
    )

    assert gate["rejected"] is True
    assert gate["reason"] == "policy_min_reward_risk_not_met"
    assert gate["rule_id"] == "rr"
    assert gate["checks"][0]["reward_risk"] == 1.0


def test_policy_target_stop_quality_gate_allows_volatile_attack_wide_probe_stop() -> None:
    gate = policy_target_stop_quality_gate(
        {
            "symbol": "ALCXUSDT",
            "market": "spot",
            "side": "long",
            "lane": "volatile_attack",
            "entry_style": "wait_for_price",
            "entry_price": 2.7505,
            "entry_trigger_price": 2.7505,
            "target_price": 3.2644,
            "stop_price": 2.4999,
        },
        [
            {
                "rule_id": "validation.binance.calmar_ratio@v1",
                "effect": {
                    "min_reward_risk": 2.0,
                    "max_stop_risk_pct": 6.0,
                },
            }
        ],
    )

    assert gate["rejected"] is False
    assert gate["checks"][0]["status"] == "ok"
    assert gate["checks"][0]["stop_risk_pct"] == pytest.approx(9.111071)
    assert gate["checks"][0]["max_stop_risk_pct"] == pytest.approx(12.0)
    assert gate["checks"][0]["max_stop_risk_override_reason"] == "volatile_attack_wide_stop"


def test_policy_target_stop_quality_gate_keeps_regular_lane_stop_cap() -> None:
    gate = policy_target_stop_quality_gate(
        {
            "symbol": "ALCXUSDT",
            "market": "spot",
            "side": "long",
            "lane": "short",
            "entry_price": 2.7505,
            "target_price": 3.2644,
            "stop_price": 2.4999,
        },
        [
            {
                "rule_id": "validation.binance.calmar_ratio@v1",
                "effect": {
                    "min_reward_risk": 2.0,
                    "max_stop_risk_pct": 6.0,
                },
            }
        ],
    )

    assert gate["rejected"] is True
    assert gate["reason"] == "policy_max_stop_risk_pct_exceeded"
    assert gate["checks"][0]["max_stop_risk_pct"] == pytest.approx(6.0)
