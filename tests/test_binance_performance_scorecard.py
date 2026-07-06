from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.binance_performance_scorecard import (
    performance_group_scorecards,
    performance_scorecard_from_reflections,
)

ROOT = Path(__file__).resolve().parents[1]


def _sample_reflections() -> list[dict[str, object]]:
    return [
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
            "entry_quality_label": "breakout_confirmed",
            "pattern_key": "trend:breakout",
            "r_multiple": 1.0,
            "mfe_r_multiple": 1.4,
            "mae_r_multiple": -0.2,
            "pnl_usdt": 2.0,
            "gross_pnl_usdt": 2.3,
            "fee_usdt": 0.1,
            "funding_usdt": 0.05,
            "slippage_usdt": 0.1,
            "spread_usdt": 0.05,
            "lesson": {"takeaway": "held winner"},
        },
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "lane": "futures:short",
            "entry_quality_label": "late_chase",
            "pattern_key": "mean_reversion:failed",
            "r_multiple": -0.5,
            "mfe_r_multiple": 0.2,
            "mae_r_multiple": -0.8,
            "pnl_usdt": -1.0,
            "gross_pnl_usdt": -0.9,
            "fee_usdt": 0.05,
            "funding_usdt": 0.0,
            "slippage_usdt": 0.03,
            "spread_usdt": 0.02,
            "lesson": {"takeaway": "avoid late chase"},
        },
    ]


def test_performance_scorecard_from_reflections_summarizes_cost_and_edge() -> None:
    scorecard = performance_scorecard_from_reflections(_sample_reflections())

    assert scorecard["sample_count"] == 2
    assert scorecard["avg_r_multiple"] == pytest.approx(0.25)
    assert scorecard["win_rate_pct"] == pytest.approx(50.0)
    assert scorecard["realized_pnl_usdt"] == pytest.approx(1.0)
    assert scorecard["gross_realized_pnl_usdt"] == pytest.approx(1.4)
    assert scorecard["total_cost_usdt"] == pytest.approx(0.4)
    assert scorecard["profit_factor"] == pytest.approx(2.0)
    assert scorecard["max_drawdown_usdt"] == pytest.approx(-1.0)
    assert scorecard["recovery_factor"] == pytest.approx(1.0)
    assert scorecard["recent_lessons"] == [
        {"takeaway": "held winner"},
        {"takeaway": "avoid late chase"},
    ]


def test_performance_group_scorecards_orders_worst_groups_first() -> None:
    cards = performance_group_scorecards(
        _sample_reflections(),
        key_name="side",
        key_fn=lambda row: str(row.get("side") or "long"),
    )

    assert [card["side"] for card in cards] == ["short", "long"]
    assert cards[0]["pnl_usdt"] == pytest.approx(-1.0)
    assert cards[1]["pnl_usdt"] == pytest.approx(2.0)


def test_binance_performance_scorecard_helpers_live_outside_block_trader() -> None:
    trader_source = (
        ROOT / "src/tradecraft/services/binance_block_trader.py"
    ).read_text()
    scorecard_source = (
        ROOT / "src/tradecraft/services/binance_performance_scorecard.py"
    ).read_text()

    assert "def performance_scorecard_from_reflections(" in scorecard_source
    assert "def performance_group_scorecards(" in scorecard_source
    assert "def performance_entry_quality_scorecards(" in scorecard_source
    assert "def performance_improvement_points(" in scorecard_source
    assert "def performance_pattern_scorecards(" in scorecard_source
    assert "def _performance_scorecard_from_reflections(" not in trader_source
    assert "def _performance_group_scorecards(" not in trader_source
    assert "def _performance_entry_quality_scorecards(" not in trader_source
    assert "def _performance_improvement_points(" not in trader_source
    assert "def _performance_pattern_scorecards(" not in trader_source
