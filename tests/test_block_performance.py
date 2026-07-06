from __future__ import annotations

import pytest

from tradecraft.services.block_performance import summarize_block_path


def test_summarize_block_path_computes_mfe_mae_and_giveback() -> None:
    result = summarize_block_path(
        entry_price=100_000,
        current_price=107_000,
        prices=[
            {"price": 98_000},
            {"price": "103000"},
            {"price": None},
            112_000,
        ],
    )

    assert result["entry_price"] == pytest.approx(100_000)
    assert result["current_price"] == pytest.approx(107_000)
    assert result["peak_price"] == pytest.approx(112_000)
    assert result["trough_price"] == pytest.approx(98_000)
    assert result["mfe_pct"] == pytest.approx(12.0)
    assert result["mae_pct"] == pytest.approx(-2.0)
    assert result["current_pnl_pct"] == pytest.approx(7.0)
    assert result["giveback_pct"] == pytest.approx(5.0)


def test_summarize_block_path_keeps_drawdown_path_bounded_by_entry() -> None:
    result = summarize_block_path(
        entry_price=100_000,
        current_price=94_000,
        prices=[99_000, 96_000, 92_000, 94_000],
    )

    assert result["peak_price"] == pytest.approx(100_000)
    assert result["trough_price"] == pytest.approx(92_000)
    assert result["mfe_pct"] == pytest.approx(0.0)
    assert result["mae_pct"] == pytest.approx(-8.0)
    assert result["current_pnl_pct"] == pytest.approx(-6.0)
    assert result["giveback_pct"] == pytest.approx(6.0)


def test_summarize_block_path_tolerates_missing_entry_or_current_data() -> None:
    missing_entry = summarize_block_path(
        entry_price=None,
        current_price=107_000,
        prices=[98_000, 112_000],
    )
    missing_current = summarize_block_path(
        entry_price=100_000,
        current_price="inf",
        prices=["bad", "NaN", float("inf"), 98_000, 112_000],
    )

    assert missing_entry == {
        "entry_price": 0.0,
        "current_price": 0.0,
        "peak_price": 0.0,
        "trough_price": 0.0,
        "mfe_pct": 0.0,
        "mae_pct": 0.0,
        "current_pnl_pct": 0.0,
        "current_return_pct": 0.0,
        "giveback_pct": 0.0,
    }
    assert missing_current["entry_price"] == pytest.approx(100_000)
    assert missing_current["current_price"] == pytest.approx(0.0)
    assert missing_current["peak_price"] == pytest.approx(112_000)
    assert missing_current["trough_price"] == pytest.approx(98_000)
    assert missing_current["current_pnl_pct"] == pytest.approx(0.0)
    assert missing_current["giveback_pct"] == pytest.approx(0.0)
