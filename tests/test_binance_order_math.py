from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import pytest

from tradecraft.services.binance_order_math import (
    candidate_stop_pct,
    candidate_volatility_pct,
    candidate_last_price,
    min_notional_from_filters,
    normalize_order_for_filters,
    quantize_to_step,
    reward_risk,
    round_candidate_price,
)


def test_quantize_to_step_rounds_down_or_up_to_exchange_step() -> None:
    assert quantize_to_step(Decimal("0.129"), Decimal("0.01"), rounding=ROUND_FLOOR) == Decimal(
        "0.12"
    )
    assert quantize_to_step(Decimal("0.121"), Decimal("0.01"), rounding=ROUND_CEILING) == Decimal(
        "0.13"
    )
    assert quantize_to_step(Decimal("-1"), Decimal("0.01"), rounding=ROUND_FLOOR) == Decimal("0")
    assert quantize_to_step(Decimal("1.23"), Decimal("0"), rounding=ROUND_FLOOR) == Decimal(
        "1.23"
    )


def test_round_candidate_price_uses_crypto_friendly_precision_bands() -> None:
    assert round_candidate_price(123.4567) == 123.46
    assert round_candidate_price(12.34567) == 12.346
    assert round_candidate_price(1.234567) == 1.2346
    assert round_candidate_price(0.1234567) == 0.12346
    assert round_candidate_price(0.01234567) == 0.012346
    assert round_candidate_price(0) == 0.0


def test_reward_risk_handles_long_short_and_invalid_geometry() -> None:
    assert reward_risk(side="long", entry_price=100, stop_price=95, target_price=115) == 3.0
    assert reward_risk(side="short", entry_price=100, stop_price=105, target_price=85) == 3.0
    assert reward_risk(side="long", entry_price=100, stop_price=101, target_price=115) == 0.0
    assert reward_risk(side="short", entry_price=100, stop_price=95, target_price=85) == 0.0


def test_candidate_last_price_prefers_candidate_then_features_price_keys() -> None:
    assert (
        candidate_last_price(
            candidate={"last_price": "101.5"},
            features={"price": "99"},
        )
        == 101.5
    )
    assert (
        candidate_last_price(
            candidate={"price": 0},
            features={"current_price": "98.25"},
        )
        == 98.25
    )
    assert candidate_last_price(candidate={}, features={}) == 0.0


def test_min_notional_from_filters_reads_binance_filter_variants() -> None:
    assert min_notional_from_filters({"MIN_NOTIONAL": {"notional": "20"}}) == Decimal("20")
    assert min_notional_from_filters({"MIN_NOTIONAL": {"minNotional": "5"}}) == Decimal("5")
    assert min_notional_from_filters({"NOTIONAL": {"min_notional": "12.5"}}) == Decimal("12.5")
    assert min_notional_from_filters({"MIN_NOTIONAL": {"notional": "0"}}) == Decimal("0")


def test_normalize_order_for_filters_quantizes_qty_and_price_by_side() -> None:
    filters = {
        "LOT_SIZE": {"stepSize": "0.01", "minQty": "0.01"},
        "PRICE_FILTER": {"tickSize": "0.1", "minPrice": "0.1"},
    }

    buy = normalize_order_for_filters(
        filters,
        symbol="BTCUSDT",
        side="buy",
        qty=1.239,
        limit_price=101.04,
    )
    sell = normalize_order_for_filters(
        filters,
        symbol="BTCUSDT",
        side="sell",
        qty=1.239,
        limit_price=101.04,
    )

    assert buy == {"quantity": 1.23, "limit_price": 101.1}
    assert sell == {"quantity": 1.23, "limit_price": 101.0}


def test_normalize_order_for_filters_can_bump_qty_for_near_min_notional() -> None:
    result = normalize_order_for_filters(
        {
            "LOT_SIZE": {"stepSize": "0.01", "minQty": "0.01"},
            "PRICE_FILTER": {"tickSize": "0.01", "minPrice": "0.01"},
            "MIN_NOTIONAL": {"notional": "20"},
        },
        symbol="LINKUSDT",
        side="buy",
        qty=1.98,
        limit_price=10.0,
        allow_min_notional_qty_bump=True,
    )

    assert result == {"quantity": 2.0, "limit_price": 10.0}


def test_normalize_order_for_filters_rejects_unfillable_min_notional() -> None:
    with pytest.raises(ValueError, match="order notional below minimum: LINKUSDT"):
        normalize_order_for_filters(
            {
                "LOT_SIZE": {"stepSize": "0.01", "minQty": "0.01"},
                "PRICE_FILTER": {"tickSize": "0.01", "minPrice": "0.01"},
                "MIN_NOTIONAL": {"notional": "20"},
            },
            symbol="LINKUSDT",
            side="buy",
            qty=1.0,
            limit_price=10.0,
            allow_min_notional_qty_bump=True,
        )


def test_candidate_volatility_pct_uses_change_spread_floor_and_cap() -> None:
    assert candidate_volatility_pct(
        change_pct_24h=2.0,
        spread_bps=20,
        horizon="mid",
        market="spot",
    ) == 1.1
    assert candidate_volatility_pct(
        change_pct_24h=20.0,
        spread_bps=30,
        horizon="short",
        market="spot",
    ) == pytest.approx(5.6)
    assert candidate_volatility_pct(
        change_pct_24h=50.0,
        spread_bps=10,
        horizon="long",
        market="spot",
    ) == 7.5
    assert candidate_volatility_pct(
        change_pct_24h=0.5,
        spread_bps=20,
        horizon="short",
        market="futures",
    ) == 1.0


def test_candidate_stop_pct_applies_horizon_multiplier_floor_and_cap() -> None:
    assert candidate_stop_pct(
        volatility_pct=1.0,
        horizon="short",
        market="spot",
        min_candidate_stop_pct=0.8,
    ) == 0.9
    assert candidate_stop_pct(
        volatility_pct=1.0,
        horizon="short",
        market="spot",
        min_candidate_stop_pct=1.2,
    ) == 1.2
    assert candidate_stop_pct(
        volatility_pct=2.0,
        horizon="long",
        market="spot",
        min_candidate_stop_pct=0.8,
    ) == 3.4
    assert candidate_stop_pct(
        volatility_pct=20.0,
        horizon="long",
        market="spot",
        min_candidate_stop_pct=0.8,
    ) == 9.0
    assert candidate_stop_pct(
        volatility_pct=2.0,
        horizon="mid",
        market="futures",
        min_candidate_stop_pct=0.8,
    ) == 1.8
