from __future__ import annotations

import pytest

from tradecraft.services.binance_risk import (
    BinanceRiskConfig,
    BinanceRiskSizer,
    block_notional_usdt,
    cash_reference_usdt,
    current_symbol_exposure_usdt,
    current_total_exposure_usdt,
)


def test_risk_sizer_calculates_qty_from_stop_distance() -> None:
    sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=0.25,
            max_symbol_exposure_pct=25.0,
            min_reward_risk=1.3,
        )
    )

    result = sizer.size_block(
        symbol="BTCUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        entry_price=50_000,
        stop_price=49_500,
        target_price=51_000,
        side="long",
        proposed_qty=None,
        leverage=1,
    )

    assert result["status"] == "ok"
    assert result["risk_budget_usdt"] == pytest.approx(25.0)
    assert result["qty"] == pytest.approx(0.05)
    assert result["reward_risk"] == pytest.approx(2.0)


def test_risk_sizer_rejects_bad_reward_risk() -> None:
    sizer = BinanceRiskSizer(BinanceRiskConfig(account_risk_pct=0.25, min_reward_risk=1.3))

    result = sizer.size_block(
        symbol="BTCUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        entry_price=50_000,
        stop_price=49_500,
        target_price=50_300,
        side="long",
        proposed_qty=None,
        leverage=1,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "reward_risk_too_low"


def test_risk_sizer_rejects_directionally_invalid_prices() -> None:
    sizer = BinanceRiskSizer(BinanceRiskConfig(account_risk_pct=0.25, min_reward_risk=1.3))

    long_result = sizer.size_block(
        symbol="BTCUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        entry_price=50_000,
        stop_price=50_500,
        target_price=51_000,
        side="long",
        proposed_qty=None,
        leverage=1,
    )
    short_result = sizer.size_block(
        symbol="BTCUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        entry_price=50_000,
        stop_price=49_500,
        target_price=49_000,
        side="short",
        proposed_qty=None,
        leverage=1,
    )

    assert long_result["status"] == "rejected"
    assert long_result["reason"] == "invalid_price_direction"
    assert short_result["status"] == "rejected"
    assert short_result["reason"] == "invalid_price_direction"


def test_risk_sizer_caps_qty_by_symbol_exposure() -> None:
    sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=5.0,
            max_symbol_exposure_pct=25.0,
            min_reward_risk=1.3,
        )
    )

    result = sizer.size_block(
        symbol="ETHUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=2_000,
        entry_price=100,
        stop_price=90,
        target_price=130,
        side="long",
        proposed_qty=None,
        leverage=1,
    )

    assert result["status"] == "ok"
    assert result["qty"] == pytest.approx(5.0)
    assert result["notional_usdt"] == pytest.approx(500.0)


def test_volatile_attack_uses_smaller_lane_risk_budget() -> None:
    sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=0.25,
            max_symbol_exposure_pct=25.0,
            min_reward_risk=1.3,
        )
    )

    normal = sizer.size_block(
        symbol="ALTUSDT",
        account_equity_usdt=1_000,
        current_symbol_exposure_usdt=0,
        entry_price=1.0,
        stop_price=0.95,
        target_price=1.10,
        side="long",
        proposed_qty=None,
        leverage=1,
        lane="core_trend",
    )
    volatile = sizer.size_block(
        symbol="ALTUSDT",
        account_equity_usdt=1_000,
        current_symbol_exposure_usdt=0,
        entry_price=1.0,
        stop_price=0.90,
        target_price=1.20,
        side="long",
        proposed_qty=None,
        leverage=1,
        lane="volatile_attack",
    )

    assert normal["status"] == "ok"
    assert volatile["status"] == "ok"
    assert volatile["notional_usdt"] < normal["notional_usdt"]
    assert volatile["lane"] == "volatile_attack"
    assert volatile["lane_risk_multiplier"] == pytest.approx(0.35)


def test_risk_sizer_applies_live_lane_performance_multiplier() -> None:
    sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=0.25,
            max_symbol_exposure_pct=25.0,
            min_reward_risk=1.3,
        )
    )

    result = sizer.size_block(
        symbol="MEMEUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        current_total_exposure_usdt=0,
        entry_price=1.0,
        stop_price=0.95,
        target_price=1.15,
        side="long",
        proposed_qty=None,
        lane="volatile_attack",
        performance_multiplier=0.5,
    )

    assert result["status"] == "ok"
    assert result["lane"] == "volatile_attack"
    assert result["lane_risk_multiplier"] == pytest.approx(0.35)
    assert result["performance_multiplier"] == pytest.approx(0.5)
    assert result["effective_risk_multiplier"] == pytest.approx(0.175)
    assert result["risk_budget_usdt"] == pytest.approx(4.375)


def test_block_notional_usdt_converts_upbit_krw_blocks() -> None:
    assert block_notional_usdt(
        {
            "symbol": "KRW-BTC",
            "market": "upbit_spot",
            "qty_open": 0.01,
            "entry_price": 60_000_000,
        },
        upbit_usdt_krw_rate=1_500,
    ) == pytest.approx(400.0)


def test_current_exposure_helpers_match_symbol_aliases_and_active_statuses() -> None:
    blocks = [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty_open": 0.01,
            "entry_price": 60_000,
            "status": "open",
        },
        {
            "symbol": "KRW-BTC",
            "market": "upbit_spot",
            "qty_open": 0.01,
            "entry_price": 60_000_000,
            "status": "entry_pending",
        },
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "qty_open": 0.2,
            "entry_price": 3_000,
            "status": "exit_pending",
        },
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty_open": 1.0,
            "entry_price": 1,
            "status": "closed",
        },
    ]

    assert current_symbol_exposure_usdt(
        blocks,
        "BTCUSDT",
        upbit_usdt_krw_rate=1_500,
    ) == pytest.approx(1_000.0)
    assert current_symbol_exposure_usdt(
        blocks,
        "KRW-BTC",
        upbit_usdt_krw_rate=1_500,
    ) == pytest.approx(1_000.0)
    assert current_total_exposure_usdt(
        blocks,
        upbit_usdt_krw_rate=1_500,
    ) == pytest.approx(1_600.0)


def test_cash_reference_usdt_uses_market_specific_cash_sources() -> None:
    account = {
        "cash_usdt": 90.0,
        "total_value_usdt": 120.0,
        "spot_cash_usdt": 40.0,
        "futures_cash_usdt": 70.0,
        "upbit_cash_krw": 150_000.0,
    }

    assert cash_reference_usdt(
        market="upbit_spot",
        account=account,
        upbit_usdt_krw_rate=1_500.0,
    ) == pytest.approx(100.0)
    assert cash_reference_usdt(market="futures", account=account) == pytest.approx(70.0)
    assert cash_reference_usdt(market="spot", account=account) == pytest.approx(40.0)
    assert cash_reference_usdt(
        market="spot",
        account={"total_value_usdt": 33.0},
    ) == pytest.approx(33.0)
