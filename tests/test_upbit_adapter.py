from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import httpx
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


def _jwt_payload(token: str) -> dict[str, object]:
    _header, payload, _signature = token.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def test_upbit_sign_jwt_includes_query_hash() -> None:
    adapter = UpbitAdapter(UpbitConfig(access_key="access", secret_key="secret"))

    token = adapter._sign_jwt({"market": "KRW-BTC", "side": "bid", "volume": "0.01"})
    payload = _jwt_payload(token)

    expected = hashlib.sha512(b"market=KRW-BTC&side=bid&volume=0.01").hexdigest()
    assert payload["access_key"] == "access"
    assert payload["query_hash"] == expected
    assert payload["query_hash_alg"] == "SHA512"


def test_upbit_market_symbol_normalization() -> None:
    assert UpbitAdapter._to_krw_market("BTCUSDT") == "KRW-BTC"
    assert UpbitAdapter._to_krw_market("BTCKRW") == "KRW-BTC"
    assert UpbitAdapter._to_krw_market("KRW-ETH") == "KRW-ETH"


def test_upbit_submit_spot_order_payload(monkeypatch) -> None:
    adapter = UpbitAdapter(UpbitConfig(access_key="a", secret_key="b"))
    captured: dict[str, object] = {}

    async def fake_signed_post(path: str, payload: dict[str, object]) -> httpx.Response:
        captured["path"] = path
        captured["payload"] = payload
        return httpx.Response(
            201,
            json={
                "uuid": "order-1",
                "identifier": payload["identifier"],
                "market": payload["market"],
                "state": "done",
                "volume": payload["volume"],
                "executed_volume": payload["volume"],
                "remaining_volume": "0",
                "price": payload["price"],
            },
        )

    monkeypatch.setattr(adapter, "_signed_post", fake_signed_post)

    response = asyncio.run(
        adapter.submit_spot_order(
            symbol="BTCUSDT",
            side="buy",
            quantity=0.001,
            limit_price=100_000_000,
            client_order_id="client-1",
        )
    )

    assert captured["path"] == "/v1/orders"
    assert captured["payload"] == {
        "market": "KRW-BTC",
        "side": "bid",
        "volume": "0.001",
        "price": "100000000",
        "ord_type": "limit",
        "time_in_force": "ioc",
        "identifier": "client-1",
    }
    assert response["status"] == "FILLED"
    assert response["executed_qty"] == pytest.approx(0.001)


def test_upbit_order_response_uses_exchange_executed_volume_only() -> None:
    response = UpbitAdapter._normalize_order_response(
        {
            "uuid": "order-2",
            "identifier": "client-2",
            "market": "KRW-ETH",
            "state": "done",
            "volume": "2.5",
            "executed_volume": "0",
            "remaining_volume": "2.5",
            "price": "3000000",
        },
        market="upbit_spot",
    )

    assert response["status"] == "FILLED"
    assert response["orig_qty"] == pytest.approx(2.5)
    assert response["executed_qty"] == pytest.approx(0.0)
    assert response["executedQty"] == pytest.approx(0.0)
    assert "request_backfilled_fields" not in response


def test_upbit_fetch_book_ticker_normalizes_orderbook(monkeypatch) -> None:
    adapter = UpbitAdapter(UpbitConfig())

    async def fake_public_json(path: str, params: dict[str, object]) -> list[dict[str, object]]:
        assert path == "/v1/orderbook"
        assert params["markets"] == "KRW-BTC"
        return [
            {
                "market": "KRW-BTC",
                "orderbook_units": [
                    {
                        "bid_price": 99_900_000,
                        "ask_price": 100_000_000,
                        "bid_size": 0.2,
                        "ask_size": 0.1,
                    }
                ],
            }
        ]

    monkeypatch.setattr(adapter, "_get_public_json", fake_public_json)

    book = asyncio.run(adapter.fetch_book_ticker("BTCUSDT"))

    assert book["symbol"] == "KRW-BTC"
    assert book["market"] == "upbit_spot"
    assert book["bid"] == pytest.approx(99_900_000)
    assert book["ask"] == pytest.approx(100_000_000)
    assert book["spread_bps"] > 0
