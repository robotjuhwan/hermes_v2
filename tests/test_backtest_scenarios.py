from __future__ import annotations

from tradecraft.backtest.engine import BacktestConfig
from tradecraft.backtest.scenarios import apply_scenario, list_scenarios


def test_backtest_scenarios_list_contains_baseline() -> None:
    rows = list_scenarios()
    keys = {str(row.get("key")) for row in rows}
    assert "baseline" in keys
    assert "high_vol" in keys


def test_backtest_apply_scenario_overrides_market_assumptions() -> None:
    config = BacktestConfig(
        cycles=10,
        step_sec=60,
        speed=120.0,
        drift_bps=0.2,
        volatility_bps=18.0,
        fee_rate=0.0005,
        slippage_bps=1.0,
    )
    updated = apply_scenario(config, "fee_stress")

    assert updated.fee_rate == 0.001
    assert updated.slippage_bps == 2.0
