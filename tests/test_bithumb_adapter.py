from __future__ import annotations

import base64
import json

import pytest

from tradecraft.services.bithumb import BithumbAdapter, BithumbConfig


def test_bithumb_to_assets_maps_accounts() -> None:
    adapter = BithumbAdapter(BithumbConfig(access_key="a", secret_key="b"))
    accounts = [
        {
            "currency": "KRW",
            "balance": "1200000",
            "locked": "300000",
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
    assert krw["qty"] == pytest.approx(1_500_000.0)
    assert btc["kind"] == "position"
    assert btc["qty"] == pytest.approx(0.12)
    assert btc["value_krw"] == pytest.approx(15_600_000.0)
    assert btc["pnl_krw"] == pytest.approx(1_200_000.0)


def test_bithumb_build_krw_markets() -> None:
    adapter = BithumbAdapter(BithumbConfig(access_key="a", secret_key="b"))
    markets = adapter._build_krw_markets(
        [
            {"currency": "KRW"},
            {"currency": "BTC"},
            {"currency": "eth"},
            {"currency": "BTC"},
        ]
    )
    assert markets == ["KRW-BTC", "KRW-ETH"]


def test_bithumb_sign_jwt_with_query_hash() -> None:
    adapter = BithumbAdapter(BithumbConfig(access_key="access", secret_key="secret"))
    token = adapter._sign_jwt({"market": "KRW-BTC"})
    payload_b64 = token.split(".")[1]
    padded = payload_b64 + ("=" * ((4 - len(payload_b64) % 4) % 4))
    payload = json.loads(base64.urlsafe_b64decode(padded).decode())

    assert payload["access_key"] == "access"
    assert isinstance(payload["timestamp"], int)
    assert payload["query_hash_alg"] == "SHA512"
    assert payload["query_hash"]
