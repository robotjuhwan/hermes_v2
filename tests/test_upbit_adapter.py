from __future__ import annotations

import pytest

from tradecraft.services.upbit import UpbitAdapter, UpbitConfig


def test_upbit_to_assets_maps_accounts() -> None:
    adapter = UpbitAdapter(UpbitConfig(access_key="a", secret_key="b"))
    accounts = [
        {
            "currency": "KRW",
            "balance": "1000000",
            "locked": "250000",
            "avg_buy_price": "0",
            "unit_currency": "KRW",
        },
        {
            "currency": "BTC",
            "balance": "0.1",
            "locked": "0.02",
            "avg_buy_price": "120000000",
            "unit_currency": "KRW",
        },
    ]
    prices = {"KRW-BTC": 130_000_000.0}

    assets = adapter._to_assets(accounts, prices)

    krw = next(item for item in assets if item["asset"] == "KRW")
    btc = next(item for item in assets if item["asset"] == "BTC")

    assert krw["kind"] == "cash"
    assert krw["qty"] == pytest.approx(1_250_000.0)
    assert btc["kind"] == "position"
    assert btc["qty"] == pytest.approx(0.12)
    assert btc["value_krw"] == pytest.approx(15_600_000.0)
    assert btc["pnl_krw"] == pytest.approx(1_200_000.0)


def test_upbit_build_krw_markets() -> None:
    adapter = UpbitAdapter(UpbitConfig(access_key="a", secret_key="b"))
    markets = adapter._build_krw_markets(
        [
            {"currency": "KRW"},
            {"currency": "BTC"},
            {"currency": "eth"},
            {"currency": "BTC"},
        ]
    )
    assert markets == ["KRW-BTC", "KRW-ETH"]


def test_upbit_to_assets_filters_unpriced_non_krw_assets() -> None:
    adapter = UpbitAdapter(UpbitConfig(access_key="a", secret_key="b"))
    accounts = [
        {"currency": "APENFT", "balance": "100", "locked": "0", "avg_buy_price": "0", "unit_currency": "KRW"},
        {"currency": "KRW", "balance": "5000", "locked": "0", "avg_buy_price": "0", "unit_currency": "KRW"},
    ]

    assets = adapter._to_assets(accounts, prices={})
    symbols = [item["asset"] for item in assets]

    assert "KRW" in symbols
    assert "APENFT" not in symbols
