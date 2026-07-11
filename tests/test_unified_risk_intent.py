from __future__ import annotations

import pytest

from tradecraft.services.unified_risk_intent import build_unified_risk_intent


def test_validated_risk_sizing_includes_round_trip_cost() -> None:
    intent = build_unified_risk_intent(
        venue="binance",
        symbol="BTCUSDT",
        equity=10_000,
        high_water_equity=10_000,
        entry_price=100,
        stop_price=98,
        round_trip_cost_per_unit=0.10,
        authority_grade="validated",
        signal_risk_fraction=1.0,
        leverage=3.0,
        margin_mode="isolated",
        open_positions=[],
        cluster="btc_beta",
        quantity_step=0.001,
    )

    assert intent.allowed is True
    assert intent.max_risk_pct == pytest.approx(0.75)
    assert intent.max_loss_amount == pytest.approx(75.0)
    assert intent.risk_per_unit == pytest.approx(2.10)
    assert intent.quantity == pytest.approx(35.714)
    assert intent.leverage == pytest.approx(3.0)


def test_twelve_percent_drawdown_engages_kill_switch_intent() -> None:
    intent = build_unified_risk_intent(
        venue="kis",
        symbol="005930",
        equity=8_800,
        high_water_equity=10_000,
        entry_price=100,
        stop_price=95,
        round_trip_cost_per_unit=0.20,
        authority_grade="validated",
        signal_risk_fraction=1.0,
        leverage=1.0,
        margin_mode="cash",
        open_positions=[],
        cluster="semiconductor",
        quantity_step=1.0,
    )

    assert intent.allowed is False
    assert intent.action == "kill_switch"
    assert intent.max_risk_pct == 0.0
    assert intent.quantity == 0.0


def test_existing_cluster_risk_caps_new_symbol_risk() -> None:
    intent = build_unified_risk_intent(
        venue="binance",
        symbol="BTCUSDT",
        equity=10_000,
        high_water_equity=10_000,
        entry_price=100,
        stop_price=98,
        round_trip_cost_per_unit=0.10,
        authority_grade="validated",
        signal_risk_fraction=1.0,
        leverage=3.0,
        margin_mode="isolated",
        open_positions=[
            {
                "venue": "binance",
                "symbol": "ETHUSDT",
                "cluster": "crypto_beta",
                "risk_pct": 1.30,
            }
        ],
        cluster="crypto_beta",
        quantity_step=0.001,
    )

    assert intent.allowed is True
    assert intent.max_risk_pct == pytest.approx(0.20)
    assert intent.max_loss_amount == pytest.approx(20.0)
    assert intent.quantity == pytest.approx(9.523)
    assert "cluster_risk_remaining" in intent.applied_caps
