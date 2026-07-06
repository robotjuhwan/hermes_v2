from __future__ import annotations

from tradecraft.services.binance_growth_governor import growth_governor_row_lanes


def test_growth_governor_row_lanes_keeps_spot_short_horizon_separate_from_short_side() -> None:
    lanes = growth_governor_row_lanes(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "lane": "short",
            "validation_repair": {
                "discipline_ids": ["walk_forward_analysis"],
            },
        }
    )

    assert "spot:long" in lanes
    assert "short" in lanes
    assert "spot:long:short" in lanes
    assert "spot:long:validation:walk_forward_analysis" in lanes
    assert "spot:short" not in lanes
    assert "spot:short:validation:walk_forward_analysis" not in lanes
    assert "spot:long:short:short" not in lanes


def test_growth_governor_row_lanes_include_period_memory_repair_quality_tokens() -> None:
    lanes = growth_governor_row_lanes(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "lane": "short",
            "metadata": {
                "validation_repair": {
                    "period_memory_repair_qualities": ["successful_repair"],
                    "block_design_constraints": [
                        {"period_memory_repair_quality": "repair_required"}
                    ],
                }
            },
        }
    )

    assert "spot:long:period_memory:successful_repair" in lanes
    assert "spot:long:short:period_memory:successful_repair" in lanes
    assert "spot:long:period_memory:repair_required" in lanes
    assert "spot:long:short:period_memory:repair_required" in lanes


def test_growth_governor_row_lanes_does_not_duplicate_canonical_lane_tokens() -> None:
    spot_lanes = growth_governor_row_lanes(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "lane": "spot:long:short",
            "metadata": {"strategy_family": "spot:long:short"},
        }
    )
    futures_lanes = growth_governor_row_lanes(
        {
            "market": "futures",
            "side": "long",
            "horizon": "futures",
            "lane": "futures:long",
            "metadata": {"strategy_family": "futures"},
        }
    )

    assert "spot:long:short" in spot_lanes
    assert "spot:long:short:short" not in spot_lanes
    assert "spot:long:short:spot:long:short" not in spot_lanes
    assert "futures:long" in futures_lanes
    assert "futures:long:futures" not in futures_lanes


def test_growth_governor_row_lanes_adds_setup_specific_futures_lanes() -> None:
    lanes = growth_governor_row_lanes(
        {
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "metadata": {"strategy_family": "Late Chase"},
            "calculated": {"entry_setup": "EMA Trend"},
        }
    )

    assert "futures:short" in lanes
    assert "futures" in lanes
    assert "late_chase" in lanes
    assert "ema_trend" in lanes
    assert "futures:short:late_chase" in lanes
    assert "futures:short:ema_trend" in lanes
