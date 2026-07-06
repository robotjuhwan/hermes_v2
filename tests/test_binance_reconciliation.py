from __future__ import annotations

from pathlib import Path

from tradecraft.services.binance_reconciliation import (
    allocated_qty_by_symbol,
    position_assets_for_market,
    spot_position_assets,
    upbit_position_assets,
)


def test_binance_block_trader_does_not_reown_reconciliation_asset_helpers() -> None:
    source = Path("src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def _spot_position_assets(",
        "def _upbit_position_assets(",
        "def _position_assets_for_market(",
        "def _allocated_spot_qty_by_symbol(",
        "def _allocated_qty_by_symbol(",
        "self._spot_position_assets(",
        "self._upbit_position_assets(",
        "self._position_assets_for_market(",
        "self._allocated_spot_qty_by_symbol(",
        "self._allocated_qty_by_symbol(",
    ):
        assert marker not in source


def test_spot_position_assets_normalizes_symbol_rows_and_excludes_cash() -> None:
    account = {
        "spot_assets": [
            {"asset": "USDT", "qty": 100, "kind": "cash"},
            {"asset": "SOL", "available": "1.2", "locked": "0.3"},
            {"symbol": "NEARUSDT", "qty": "2.5"},
            {"symbol": "NEARUSDT", "qty": "2.5"},
            {"asset": "ETH", "qty": "0"},
        ],
        "positions": [
            {"market": "futures", "symbol": "BTCUSDT", "qty": 1},
            {"market": "spot", "symbol": "XRPUSDT", "balance": "7"},
        ],
    }

    assets = spot_position_assets(account)

    assert assets == [
        {
            "asset": "SOL",
            "available": 1.2,
            "locked": 0.3,
            "qty": 1.5,
            "symbol": "SOLUSDT",
        },
        {
            "symbol": "NEARUSDT",
            "qty": 2.5,
            "asset": "NEAR",
            "available": 0.0,
            "locked": 0.0,
        },
        {
            "market": "spot",
            "symbol": "XRPUSDT",
            "balance": "7",
            "asset": "XRP",
            "qty": 7.0,
            "available": 0.0,
            "locked": 0.0,
        },
    ]


def test_upbit_position_assets_normalizes_krw_symbols_and_excludes_cash() -> None:
    account = {
        "upbit_spot_assets": [
            {"asset": "KRW", "qty": 50000, "kind": "cash"},
            {"asset": "BTC", "available": "0.01", "locked": "0.02"},
            {"symbol": "KRW-SOL", "qty": "3"},
        ],
        "positions": [
            {"market": "upbit", "symbol": "KRW-ETH", "balance": "0.4"},
            {"market": "spot", "symbol": "ETHUSDT", "qty": 1},
        ],
    }

    assets = upbit_position_assets(account)

    assert assets == [
        {
            "asset": "BTC",
            "available": 0.01,
            "locked": 0.02,
            "market": "upbit_spot",
            "qty": 0.03,
            "symbol": "KRW-BTC",
        },
        {
            "symbol": "KRW-SOL",
            "qty": 3.0,
            "asset": "SOL",
            "market": "upbit_spot",
            "available": 0.0,
            "locked": 0.0,
        },
        {
            "market": "upbit_spot",
            "symbol": "KRW-ETH",
            "balance": "0.4",
            "asset": "ETH",
            "qty": 0.4,
            "available": 0.0,
            "locked": 0.0,
        },
    ]


def test_position_assets_for_market_switches_between_spot_and_upbit() -> None:
    account = {
        "spot_assets": [{"asset": "SOL", "qty": 1}],
        "upbit_spot_assets": [{"asset": "BTC", "qty": 2}],
    }

    assert position_assets_for_market(account, market="spot")[0]["symbol"] == "SOLUSDT"
    assert position_assets_for_market(account, market="upbit")[0]["symbol"] == "KRW-BTC"


def test_allocated_qty_by_symbol_counts_active_long_blocks_only() -> None:
    blocks = [
        {"symbol": "SOLUSDT", "market": "spot", "side": "long", "status": "open", "qty_open": "1.2"},
        {"symbol": "SOLUSDT", "market": "spot", "side": "long", "status": "entry_pending", "qty_initial": "0.8"},
        {"symbol": "SOLUSDT", "market": "spot", "side": "short", "status": "open", "qty_open": "9"},
        {"symbol": "SOLUSDT", "market": "spot", "side": "long", "status": "closed", "qty_open": "5"},
        {"symbol": "KRW-BTC", "market": "upbit", "side": "long", "status": "paused", "qty_open": "2"},
    ]

    assert allocated_qty_by_symbol(blocks, market="spot") == {"SOLUSDT": 2.0}
    assert allocated_qty_by_symbol(blocks, market="upbit_spot") == {"KRW-BTC": 2.0}
