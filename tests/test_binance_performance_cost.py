from __future__ import annotations

from pathlib import Path

from tradecraft.services.binance_performance_cost import (
    asset_amount_to_usdt,
    first_float,
    iter_payload_dicts,
    symbol_base_quote,
)

ROOT = Path(__file__).resolve().parents[1]


def test_symbol_base_quote_understands_binance_and_upbit_symbols() -> None:
    assert symbol_base_quote("BTCUSDT") == ("BTC", "USDT")
    assert symbol_base_quote("ETHBTC") == ("ETH", "BTC")
    assert symbol_base_quote("KRW-BTC", market="upbit_spot") == ("BTC", "KRW")
    assert symbol_base_quote("BTC-KRW", market="spot") == ("KRW", "BTC")


def test_iter_payload_dicts_flattens_nested_order_payloads() -> None:
    payload = {
        "root": True,
        "fills": [
            {"price": "101", "nested": {"commission": "0.01"}},
            [{"fee": {"cost": "0.02", "currency": "BNB"}}],
        ],
    }

    rows = iter_payload_dicts(payload)

    assert payload in rows
    assert {"price": "101", "nested": {"commission": "0.01"}} in rows
    assert {"commission": "0.01"} in rows
    assert {"fee": {"cost": "0.02", "currency": "BNB"}} in rows
    assert {"cost": "0.02", "currency": "BNB"} in rows


def test_first_float_skips_missing_zero_and_invalid_values() -> None:
    assert first_float({"a": "", "b": "0", "c": "1.25"}, ("a", "b", "c")) == 1.25
    assert first_float({"a": "bad", "b": None}, ("a", "b")) == 0.0


def test_asset_amount_to_usdt_converts_stables_krw_base_and_fee_assets() -> None:
    unconverted: list[dict[str, object]] = []

    assert (
        asset_amount_to_usdt(
            amount=2.5,
            asset="USDT",
            base_asset="BTC",
            quote_asset="USDT",
            price=100.0,
            upbit_usdt_krw_rate=1300.0,
            conversion_price_provider=lambda asset: 0.0,
            unconverted=unconverted,
        )
        == 2.5
    )
    assert (
        asset_amount_to_usdt(
            amount=1300.0,
            asset="KRW",
            base_asset="BTC",
            quote_asset="KRW",
            price=100.0,
            upbit_usdt_krw_rate=1300.0,
            conversion_price_provider=lambda asset: 0.0,
            unconverted=unconverted,
        )
        == 1.0
    )
    assert (
        asset_amount_to_usdt(
            amount=0.5,
            asset="BTC",
            base_asset="BTC",
            quote_asset="USDT",
            price=100.0,
            upbit_usdt_krw_rate=1300.0,
            conversion_price_provider=lambda asset: 0.0,
            unconverted=unconverted,
        )
        == 50.0
    )
    assert (
        asset_amount_to_usdt(
            amount=0.25,
            asset="BNB",
            base_asset="BTC",
            quote_asset="USDT",
            price=100.0,
            upbit_usdt_krw_rate=1300.0,
            conversion_price_provider=lambda asset: 300.0 if asset == "BNB" else 0.0,
            unconverted=unconverted,
        )
        == 75.0
    )


def test_asset_amount_to_usdt_records_unconverted_assets() -> None:
    unconverted: list[dict[str, object]] = []

    assert (
        asset_amount_to_usdt(
            amount=3.0,
            asset="UNKNOWN",
            base_asset="BTC",
            quote_asset="USDT",
            price=100.0,
            upbit_usdt_krw_rate=1300.0,
            conversion_price_provider=lambda asset: 0.0,
            unconverted=unconverted,
        )
        == 0.0
    )
    assert unconverted == [
        {
            "asset": "UNKNOWN",
            "amount": 3.0,
            "reason": "missing_conversion_price",
        }
    ]


def test_binance_performance_cost_helpers_live_outside_block_trader() -> None:
    trader_source = (
        ROOT / "src/tradecraft/services/binance_block_trader.py"
    ).read_text()
    helper_source = (
        ROOT / "src/tradecraft/services/binance_performance_cost.py"
    ).read_text()

    assert "def symbol_base_quote(" in helper_source
    assert "def iter_payload_dicts(" in helper_source
    assert "def first_float(" in helper_source
    assert "def asset_amount_to_usdt(" in helper_source
    assert "def _cost_value(" not in trader_source
    assert "def _symbol_base_quote(" not in trader_source
    assert "def _iter_payload_dicts(" not in trader_source
    assert "def _first_float(" not in trader_source
    assert "def _asset_amount_to_usdt(" not in trader_source
