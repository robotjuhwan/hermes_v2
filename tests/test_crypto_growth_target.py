from __future__ import annotations

from math import isfinite

import pytest

from tradecraft.services.crypto_growth_target import CryptoGrowthTargetLedger


def test_monthly_target_ledger_computes_run_rate_and_gap() -> None:
    ledger = CryptoGrowthTargetLedger(monthly_target_pct=50.0)

    result = ledger.snapshot(
        start_equity_usdt=1000.0,
        current_equity_usdt=1100.0,
        elapsed_days=10.0,
        month_days=30.0,
    )

    assert result["target_equity_usdt"] == 1500.0
    assert result["basis"] == "account_equity_monthly_run_rate"
    assert result["current_return_pct"] == 10.0
    assert result["remaining_return_pct"] == pytest.approx(36.3636)
    assert result["required_daily_return_pct"] > 1.0
    assert result["status"] == "behind_target"


def test_monthly_target_ledger_caps_unbounded_required_daily_return() -> None:
    ledger = CryptoGrowthTargetLedger(monthly_target_pct=50.0)

    result = ledger.snapshot(
        start_equity_usdt=1000.0,
        current_equity_usdt=100.0,
        elapsed_days=30.999,
        month_days=31.0,
    )

    assert result["required_daily_return_pct"] == 1000000.0
    assert result["required_daily_return_capped"] is True
    assert result["status"] == "behind_target"


def test_monthly_target_ledger_returns_only_finite_numeric_fields_for_extreme_gap() -> None:
    ledger = CryptoGrowthTargetLedger(monthly_target_pct=50.0)

    result = ledger.snapshot(
        start_equity_usdt=1000.0,
        current_equity_usdt=1e-320,
        elapsed_days=29.999999999,
        month_days=30.0,
    )

    for value in result.values():
        if isinstance(value, float):
            assert isfinite(value)
    assert result["remaining_return_pct"] == 1000000.0
    assert result["required_daily_return_pct"] == 1000000.0
    assert result["required_daily_return_capped"] is True
