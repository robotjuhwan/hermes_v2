from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_policy_effects import (
    append_policy_reason,
    candidate_policy_impacts_for_strategy,
    contains_any_token,
    dedupe_policy_impacts,
    normalized_gate_token,
    policy_effects,
    policy_rule_ids,
    policy_rule_impacts_for_block,
    policy_rule_impacts_for_symbol,
)

ROOT = Path(__file__).resolve().parents[1]


def test_normalized_gate_token_and_contains_any_token_match_compact_and_raw_text() -> None:
    assert normalized_gate_token("High Chase / 급등 추격") == "high_chase"
    assert contains_any_token("Price Location: Extended", {"extended"})
    assert contains_any_token("급등 추격 위험", {"추격"})
    assert not contains_any_token("pullback entry", {"extended", "chase"})


def test_policy_effects_returns_only_impacts_with_effect_payload() -> None:
    rows = [
        {"rule_id": "r1", "effect": {"risk_note": "wait"}},
        {"rule_id": "r2", "effect": {}},
        {"rule_id": "r3"},
        "bad",
    ]

    assert policy_effects(rows) == [
        ({"rule_id": "r1", "effect": {"risk_note": "wait"}}, {"risk_note": "wait"})
    ]


def test_dedupe_policy_impacts_and_rule_ids_keep_first_unique_rules() -> None:
    impacts = [
        {"rule_id": "r1", "reason": "first"},
        {"rule_id": "r1", "reason": "duplicate"},
        {"policy_id": "p2", "reason": "policy fallback"},
        {"reason": "missing id"},
    ]

    deduped = dedupe_policy_impacts(impacts)

    assert deduped == [
        {"rule_id": "r1", "reason": "first"},
        {"policy_id": "p2", "reason": "policy fallback"},
    ]
    assert policy_rule_ids(deduped) == ["r1"]


def test_policy_rule_impacts_for_symbol_block_and_strategy_candidates() -> None:
    evaluation = {
        "global": [{"rule_id": "global", "effect": {"risk_note": "global note"}}],
        "by_symbol": {
            "005930": [{"rule_id": "samsung", "effect": {"risk_note": "symbol note"}}]
        },
        "by_block": {
            "block-1": [{"rule_id": "block", "effect": {"risk_note": "block note"}}]
        },
    }
    strategy_payload = {
        "candidates": [{"symbol": "005930"}, {"symbol": "000660"}]
    }

    assert [
        row["rule_id"] for row in policy_rule_impacts_for_symbol("005930", evaluation)
    ] == ["global", "samsung"]
    assert [
        row["rule_id"] for row in policy_rule_impacts_for_block("block-1", evaluation)
    ] == ["global", "block"]
    mapped = candidate_policy_impacts_for_strategy(strategy_payload, evaluation)
    assert [row["rule_id"] for row in mapped["005930"]] == ["global", "samsung"]
    assert [row["rule_id"] for row in mapped["000660"]] == ["global"]


def test_append_policy_reason_adds_compact_policy_rationale() -> None:
    reason = append_policy_reason(
        "base reason",
        [
            {
                "rule_id": "r1",
                "effect": {"risk_note": "wait for pullback"},
            },
            {
                "policy_id": "p2",
                "reason": "prefer smaller size",
            },
        ],
    )

    assert reason == (
        "base reason 정책룰 반영 - "
        "r1: wait for pullback / p2: prefer smaller size"
    )


def test_kis_policy_effect_helpers_live_outside_block_trader() -> None:
    trader_source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()
    helper_source = (ROOT / "src/tradecraft/services/kis_policy_effects.py").read_text()

    assert "def normalized_gate_token(" in helper_source
    assert "def contains_any_token(" in helper_source
    assert "def policy_effects(" in helper_source
    assert "def policy_rule_ids(" in helper_source
    assert "def normalize_impacts(" in helper_source
    assert "def dedupe_policy_impacts(" in helper_source
    assert "def append_policy_reason(" in helper_source
    assert "def _normalized_gate_token(" not in trader_source
    assert "def _contains_any_token(" not in trader_source
    assert "def _policy_effects(" not in trader_source
    assert "def _policy_rule_ids(" not in trader_source
    assert "def _normalize_impacts(" not in trader_source
    assert "def _dedupe_policy_impacts(" not in trader_source
    assert "def _append_policy_reason(" not in trader_source
