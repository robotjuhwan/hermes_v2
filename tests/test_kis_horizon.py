from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_horizon import (
    BLOCK_HORIZONS,
    HORIZON_COLORS,
    normalize_horizon,
)
from tradecraft.services.kis_ledger import (
    build_allocation_summary,
    build_horizon_allocation_summary,
)


def test_kis_block_trader_does_not_reown_horizon_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _normalize_horizon(" not in source


def test_normalize_horizon_supports_korean_and_english_aliases() -> None:
    assert normalize_horizon(None) == "short"
    assert normalize_horizon("단기") == "short"
    assert normalize_horizon("mid term") == "mid"
    assert normalize_horizon("medium-term") == "mid"
    assert normalize_horizon("장기") == "long"
    assert normalize_horizon("core ETF") == "core_etf"
    assert normalize_horizon("etf-core") == "core_etf"
    assert normalize_horizon("unknown") == "short"


def test_horizon_constants_expose_trading_lanes_and_colors() -> None:
    assert BLOCK_HORIZONS == {"short", "mid", "long", "core_etf"}
    assert HORIZON_COLORS["core_etf"] == "etf"
    assert HORIZON_COLORS["cash"] == "cash"


def test_build_allocation_summary_reports_unallocated_and_overallocated_qty() -> None:
    summary = build_allocation_summary(
        account={
            "positions": [
                {"symbol": "277810", "name": "레인보우로보틱스", "qty": 3},
                {"symbol": "005930", "name": "삼성전자", "available_qty": 1},
            ]
        },
        blocks=[
            {"symbol": "277810", "qty_open": 1, "status": "open"},
            {"symbol": "005930", "qty_initial": 2, "status": "entry_pending"},
        ],
        quotes={
            "277810": {"name": "레인보우로보틱스", "price": 100_000},
            "005930": {"name": "삼성전자", "price": 75_000},
        },
    )

    rows = {row["symbol"]: row for row in summary["items"]}
    assert rows["277810"]["account_qty"] == 3
    assert rows["277810"]["block_qty"] == 1
    assert rows["277810"]["unallocated_qty"] == 2
    assert rows["277810"]["overallocated_qty"] == 0
    assert rows["005930"]["account_qty"] == 1
    assert rows["005930"]["block_qty"] == 2
    assert rows["005930"]["unallocated_qty"] == 0
    assert rows["005930"]["overallocated_qty"] == 1


def test_build_horizon_allocation_summary_groups_active_block_value_and_cash() -> None:
    summary = build_horizon_allocation_summary(
        account={"orderable_cash_krw": 500_000, "cash_krw": 700_000},
        blocks=[
            {
                "symbol": "277810",
                "qty_open": 2,
                "entry_price": 100_000,
                "status": "open",
                "metadata": {"horizon": "중기"},
            },
            {
                "symbol": "069500",
                "qty_initial": 1,
                "entry_price": 40_000,
                "status": "entry_pending",
                "metadata": {"horizon": "core ETF"},
            },
            {
                "symbol": "000660",
                "qty_open": 9,
                "entry_price": 1,
                "status": "closed",
                "metadata": {"horizon": "long"},
            },
        ],
        quotes={
            "277810": {"price": 110_000},
            "069500": {"price": 41_000},
        },
        targets={"cash": 0.4, "short": 0.1, "mid": 0.3, "long": 0.1, "core_etf": 0.1},
    )

    rows = {row["horizon"]: row for row in summary["items"]}
    assert summary["status"] == "ok"
    assert summary["total_value_krw"] == 761_000
    assert rows["cash"]["current_value_krw"] == 500_000
    assert rows["mid"]["current_value_krw"] == 220_000
    assert rows["core_etf"]["current_value_krw"] == 41_000
    assert rows["long"]["current_value_krw"] == 0
    assert rows["core_etf"]["block_color"] == "etf"
    assert rows["cash"]["drift"] == rows["cash"]["current_weight"] - 0.4
