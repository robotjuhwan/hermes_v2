from __future__ import annotations

import pytest

from tradecraft.services.binance_entry_gate import (
    entry_reference_inside_tolerance,
    entry_fill_price_update_fields,
    entry_quality_gate_check,
    entry_quality_label_from_payload,
    entry_tolerance_price,
    entry_trigger_fired,
    is_waiting_entry_block,
    normalize_entry_trigger_operator,
    normalize_entry_quality_label,
    shadow_only_entry_qualities,
    volatile_attack_context,
    waiting_entry_metadata,
    wait_pullback_confirmation_rejection,
)


def test_shadow_only_entry_qualities_marks_bad_spot_long_pullback() -> None:
    result = shadow_only_entry_qualities(
        {
            "spot:long:wait_pullback": {
                "sample_count": 12,
                "profit_factor": 0.42,
                "pnl_usdt": -7.5,
                "win_rate_pct": 16.7,
                "avg_r_multiple": -0.8,
            },
            "futures:short:breakout": {
                "sample_count": 12,
                "profit_factor": 0.42,
                "pnl_usdt": -7.5,
                "avg_r_multiple": -0.8,
            },
        }
    )

    assert sorted(result) == ["spot:long:wait_pullback"]
    row = result["spot:long:wait_pullback"]
    assert row["status"] == "shadow_only"
    assert row["live_budget_multiplier"] == 0.0
    assert row["sample_count"] == 12
    assert row["profit_factor"] == 0.42


def test_normalize_entry_trigger_operator_accepts_aliases_and_defaults() -> None:
    assert normalize_entry_trigger_operator("breakout", default="<=") == ">="
    assert normalize_entry_trigger_operator("at_or_above", default="<=") == ">="
    assert normalize_entry_trigger_operator("pullback", default=">=") == "<="
    assert normalize_entry_trigger_operator("lte", default=">=") == "<="
    assert normalize_entry_trigger_operator("unknown", default=">=") == ">="
    assert normalize_entry_trigger_operator("unknown", default="bad") == "<="


def test_entry_trigger_fired_uses_metadata_operator_and_order_side_default() -> None:
    long_pullback = {
        "entry_price": 100.0,
        "metadata": {"entry_trigger_price": 98.0, "entry_trigger_operator": "<="},
    }
    short_breakout = {
        "entry_price": 100.0,
        "metadata": {"entry_trigger_price": 102.0, "entry_trigger_operator": ">="},
    }
    default_buy = {"entry_price": 100.0, "metadata": {"entry_trigger_price": 99.0}}

    assert entry_trigger_fired(long_pullback, price=97.9, order_side="buy") is True
    assert entry_trigger_fired(long_pullback, price=98.1, order_side="buy") is False
    assert entry_trigger_fired(short_breakout, price=102.1, order_side="sell") is True
    assert entry_trigger_fired(short_breakout, price=101.9, order_side="sell") is False
    assert entry_trigger_fired(default_buy, price=98.9, order_side="buy") is True
    assert entry_trigger_fired(default_buy, price=99.1, order_side="buy") is False


def test_entry_trigger_fired_ignores_missing_or_invalid_prices() -> None:
    assert entry_trigger_fired({"metadata": {"entry_trigger_price": 0}}, price=100, order_side="buy") is False
    assert entry_trigger_fired({"entry_price": 100}, price=0, order_side="buy") is False


def test_is_waiting_entry_block_detects_style_or_trigger_price() -> None:
    assert is_waiting_entry_block({"metadata": {"entry_style": "wait_for_price"}}) is True
    assert is_waiting_entry_block({"metadata": {"entry_style": "triggered_entry"}}) is True
    assert is_waiting_entry_block({"metadata": {"entry_trigger_price": "99.5"}}) is True
    assert is_waiting_entry_block({"metadata": {"entry_style": "immediate"}}) is False
    assert is_waiting_entry_block({}) is False


def test_entry_tolerance_price_applies_aggressive_bps_by_side() -> None:
    assert entry_tolerance_price(
        entry_price=100.0,
        side="buy",
        aggressive_limit_bps=30,
    ) == pytest.approx(100.3)
    assert entry_tolerance_price(
        entry_price=100.0,
        side="sell",
        aggressive_limit_bps=30,
    ) == pytest.approx(99.7)
    assert entry_tolerance_price(
        entry_price=0.0,
        side="buy",
        aggressive_limit_bps=30,
    ) == 0.0


def test_entry_reference_inside_tolerance_uses_side_direction() -> None:
    assert entry_reference_inside_tolerance(
        entry_price=100.0,
        reference_price=100.29,
        side="buy",
        aggressive_limit_bps=30,
    ) is True
    assert entry_reference_inside_tolerance(
        entry_price=100.0,
        reference_price=100.31,
        side="buy",
        aggressive_limit_bps=30,
    ) is False
    assert entry_reference_inside_tolerance(
        entry_price=100.0,
        reference_price=99.71,
        side="sell",
        aggressive_limit_bps=30,
    ) is True
    assert entry_reference_inside_tolerance(
        entry_price=100.0,
        reference_price=99.69,
        side="sell",
        aggressive_limit_bps=30,
    ) is False
    assert entry_reference_inside_tolerance(
        entry_price=0.0,
        reference_price=0.0,
        side="buy",
        aggressive_limit_bps=30,
    ) is True


def test_waiting_entry_metadata_preserves_existing_metadata_and_reference() -> None:
    metadata = waiting_entry_metadata(
        block={"metadata": {"custom": "keep"}},
        trigger_price=100.2,
        operator="<=",
        reason="best ask outside tolerance",
        reference={
            "bid": "99.9",
            "ask": "100.8",
            "execution_price": "100.8",
            "source": "book_ticker",
            "fetched_at": "2026-06-21T01:02:03+00:00",
        },
    )

    assert metadata["custom"] == "keep"
    assert metadata["entry_style"] == "wait_for_price"
    assert metadata["entry_trigger_price"] == 100.2
    assert metadata["entry_trigger_operator"] == "<="
    assert metadata["entry_trigger_status"] == "waiting"
    assert metadata["entry_trigger_reason"] == "best ask outside tolerance"
    assert metadata["last_entry_reference"] == {
        "bid": 99.9,
        "ask": 100.8,
        "execution_price": 100.8,
        "source": "book_ticker",
        "fetched_at": "2026-06-21T01:02:03+00:00",
    }


def test_entry_quality_label_from_payload_prefers_direct_then_nested_style() -> None:
    assert normalize_entry_quality_label(" Near 24h High! ") == "near_24h_high"
    assert entry_quality_label_from_payload(
        {"entry_quality": ""},
        {
            "entry_gate": {
                "entry_quality": {"setup": " Wait Pullback "},
                "recommended_entry_mode": "breakout",
            }
        },
    ) == "wait_pullback"
    assert entry_quality_label_from_payload(
        {"metadata": "ignored"},
        {"calculated_price_plan": {"recommended_entry_mode": "breakout-confirmed"}},
    ) == "breakout-confirmed"


def test_entry_quality_gate_check_requires_waiting_for_extended_chase_without_relief() -> None:
    result = entry_quality_gate_check(
        {
            "entry_quality": "extended_momentum",
            "chase_risk": "high",
            "price_location": "near_24h_high",
            "entry_quality_score": 42,
        },
        waiting_entry=False,
    )

    assert result["version"] == "binance_entry_quality_gate_v1"
    assert result["requires_waiting_entry"] is True
    assert "extended_momentum" in result["pressure"]
    assert "chase_risk_high" in result["pressure"]
    assert "price_location_near_24h_high" in result["pressure"]
    assert "entry_quality_requires_waiting_entry" in result["reasons"]


def test_entry_quality_gate_check_allows_low_score_when_confluence_repairs_soft_pressure() -> None:
    result = entry_quality_gate_check(
        {
            "entry_quality_score": 45,
            "regime_alignment": "aligned",
            "funding_context": "neutral",
        },
        waiting_entry=False,
    )

    assert result["has_signal"] is True
    assert result["hard_pressure"] is False
    assert result["confluence"] == ["regime_aligned", "funding_not_hostile"]
    assert result["requires_waiting_entry"] is False
    assert result["reasons"] == []


def test_entry_fill_price_update_fields_rebases_long_target_stop_after_better_fill() -> None:
    fields = entry_fill_price_update_fields(
        {
            "side": "long",
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "metadata": {"calculated_price_plan": {"risk_pct": 2.0, "target_pct": 4.0}},
        },
        fill_price=97.0,
        min_candidate_stop_pct=1.2,
    )

    assert fields["entry_price"] == 97.0
    assert fields["stop_price"] == 95.06
    assert fields["target_price"] == 100.88
    metadata = fields["metadata"]
    assert metadata["entry_fill_price_rebase"]["rebased"] is True
    assert metadata["entry_fill_price_rebase"]["old_structure_status"] == "invalid_price_structure"
    assert metadata["initial_stop_price"] == 95.06
    assert metadata["initial_target_price"] == 100.88


def test_entry_fill_price_update_fields_rebases_short_target_stop_after_higher_fill() -> None:
    fields = entry_fill_price_update_fields(
        {
            "side": "short",
            "entry_price": 100.0,
            "target_price": 96.0,
            "stop_price": 102.0,
            "metadata": {"calculated_price_plan": {"risk_pct": 2.0, "target_pct": 4.0}},
        },
        fill_price=105.0,
        min_candidate_stop_pct=1.2,
    )

    assert fields["entry_price"] == 105.0
    assert fields["stop_price"] == 107.1
    assert fields["target_price"] == 100.8
    assert fields["metadata"]["entry_fill_price_rebase"]["rebased"] is True


def test_wait_pullback_confirmation_rejects_futures_without_live_confirmation() -> None:
    row = {
        "symbol": "NEARUSDT",
        "market": "futures",
        "side": "long",
        "metadata": {
            "entry_quality": "wait_pullback",
            "calculated_price_plan": {"entry_quality": "wait_pullback"},
        },
    }

    result = wait_pullback_confirmation_rejection(row)

    assert result is not None
    assert result["status"] == "rejected"
    assert result["reason"] == "wait_pullback_live_confirmation_required"
    assert result["wait_pullback_confirmation"]["market"] == "futures"
    assert result["wait_pullback_confirmation"]["entry_quality"] == "wait_pullback"


def test_wait_pullback_confirmation_allows_aligned_live_crosscheck() -> None:
    row = {
        "market": "futures",
        "side": "short",
        "metadata": {
            "entry_quality": "wait_pullback",
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }

    assert wait_pullback_confirmation_rejection(row) is None


def test_volatile_attack_context_scores_explicit_high_volatility_candidate() -> None:
    result = volatile_attack_context(
        candidate={
            "symbol": "GENIUSUSDT",
            "lane": "volatile_attack",
            "volume_expansion_ratio": 4.0,
            "alpha_event_score": 72,
        },
        features={
            "wick_risk_score": 20,
            "orderbook_depth_usdt": 125000,
            "funding_rate": "0.0003",
            "open_interest": 1_000_000,
            "squeeze_risk_score": 72,
        },
        spread_bps=18,
        change_pct_24h=-22.5,
        market="binance-futures",
        enabled=True,
        min_change_pct=8.0,
        min_volume_expansion=2.0,
    )

    assert result["enabled"] is True
    assert result["explicit"] is True
    assert result["market"] == "futures"
    assert result["score"] > 80
    assert result["change_pct_24h"] == -22.5
    assert result["volume_expansion_ratio"] == 4.0
    assert result["spread_bps"] == 18
    assert result["wick_risk_score"] == 20.0
    assert result["orderbook_depth_usdt"] == 125000.0
    assert result["funding_rate"] == 0.0003
    assert result["open_interest"] == 1_000_000.0
    assert result["squeeze_risk_score"] == 72.0
    assert result["alpha_event_score"] == 72.0
    assert {
        "explicit_lane",
        "large_24h_move",
        "volume_expansion",
        "squeeze_setup",
        "alpha_event",
        "open_interest_present",
        "funding_dislocation",
    }.issubset(set(result["reasons"]))


def test_volatile_attack_context_penalizes_bad_microstructure() -> None:
    result = volatile_attack_context(
        candidate={"symbol": "THINUSDT"},
        features={
            "volume_expansion": 2.0,
            "upper_wick_risk_score": 88,
            "book_depth_usdt": 10_000,
        },
        spread_bps=75,
        change_pct_24h=9,
        market="spot",
        enabled=True,
        min_change_pct=8,
        min_volume_expansion=1.5,
    )

    assert result["enabled"] is False
    assert result["score"] < 45
    assert "spread_too_wide" in result["reasons"]
    assert "wick_risk_high" in result["reasons"]
    assert "depth_thin" in result["reasons"]


def test_volatile_attack_context_returns_disabled_state_when_feature_off() -> None:
    assert volatile_attack_context(
        candidate={"lane": "volatile_attack"},
        features={},
        spread_bps=0,
        change_pct_24h=100,
        market="futures",
        enabled=False,
    ) == {"enabled": False, "score": 0.0, "reasons": ["disabled"]}
