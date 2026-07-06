from __future__ import annotations

import asyncio

import pytest

from tradecraft.services.binance import BinanceAdapter, BinanceConfig


def test_binance_config_futures_fallback_to_spot_key() -> None:
    config = BinanceConfig(
        spot_api_key="spot_key",
        spot_api_secret="spot_secret",
        futures_api_key="",
        futures_api_secret="",
    )
    assert config.spot_ready is True
    assert config.futures_ready is True
    assert config.futures_key == "spot_key"
    assert config.futures_secret == "spot_secret"


def test_binance_spot_assets_mapping(monkeypatch) -> None:
    adapter = BinanceAdapter(
        BinanceConfig(
            spot_api_key="k",
            spot_api_secret="s",
            usdt_krw_rate=1400.0,
        )
    )

    async def fake_signed_get_spot(_: str, __: dict) -> dict:
        return {
            "balances": [
                {"asset": "USDT", "free": "100", "locked": "20"},
                {"asset": "BTC", "free": "0.01", "locked": "0"},
                {"asset": "ZERO", "free": "0", "locked": "0"},
            ]
        }

    async def fake_get_spot_prices() -> dict[str, float]:
        return {"BTCUSDT": 100000.0}

    monkeypatch.setattr(adapter, "_signed_get_spot", fake_signed_get_spot)
    monkeypatch.setattr(adapter, "_get_spot_prices", fake_get_spot_prices)

    assets = asyncio.run(adapter.fetch_spot_assets())
    usdt = next(a for a in assets if a["asset"] == "USDT")
    btc = next(a for a in assets if a["asset"] == "BTC")

    assert usdt["kind"] == "cash"
    assert usdt["value_krw"] == pytest.approx(168000.0)
    assert btc["kind"] == "position"
    assert btc["value_krw"] == pytest.approx(1_400_000.0)


def test_binance_spot_assets_estimates_pnl_from_trade_history(monkeypatch) -> None:
    adapter = BinanceAdapter(
        BinanceConfig(
            spot_api_key="k",
            spot_api_secret="s",
            usdt_krw_rate=1400.0,
        )
    )

    async def fake_signed_get_spot(path: str, params: dict) -> dict | list[dict]:
        if path == "/api/v3/account":
            return {
                "balances": [
                    {"asset": "BTC", "free": "0.01", "locked": "0"},
                ]
            }
        assert path == "/api/v3/myTrades"
        assert params["symbol"] == "BTCUSDT"
        return [
            {
                "id": 1,
                "time": 1,
                "isBuyer": True,
                "price": "80000",
                "qty": "0.02",
                "quoteQty": "1600",
                "commission": "0",
                "commissionAsset": "USDT",
            },
            {
                "id": 2,
                "time": 2,
                "isBuyer": False,
                "price": "90000",
                "qty": "0.01",
                "quoteQty": "900",
                "commission": "0",
                "commissionAsset": "USDT",
            },
        ]

    async def fake_get_spot_prices() -> dict[str, float]:
        return {"BTCUSDT": 100000.0}

    monkeypatch.setattr(adapter, "_signed_get_spot", fake_signed_get_spot)
    monkeypatch.setattr(adapter, "_get_spot_prices", fake_get_spot_prices)

    assets = asyncio.run(adapter.fetch_spot_assets())
    btc = next(a for a in assets if a["asset"] == "BTC")

    assert btc["avg_price"] == pytest.approx(112_000_000.0)
    assert btc["mark_price"] == pytest.approx(140_000_000.0)
    assert btc["pnl_krw"] == pytest.approx(280_000.0)
    assert btc["pnl_status"] == "estimated_from_trade_history"


def test_binance_futures_assets_mapping(monkeypatch) -> None:
    adapter = BinanceAdapter(
        BinanceConfig(
            spot_api_key="k",
            spot_api_secret="s",
            usdt_krw_rate=1300.0,
        )
    )

    async def fake_signed_get_futures(_: str, __: dict) -> dict:
        return {
            "assets": [
                {"asset": "USDT", "walletBalance": "200", "availableBalance": "150"},
                {"asset": "BTC", "walletBalance": "0.5", "availableBalance": "0.5"},
            ],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.01",
                    "entryPrice": "98000",
                    "markPrice": "100000",
                    "unrealizedProfit": "20",
                },
                {
                    "symbol": "ETHUSDT",
                    "positionAmt": "0",
                    "entryPrice": "0",
                    "markPrice": "0",
                    "unrealizedProfit": "0",
                },
            ],
        }

    monkeypatch.setattr(adapter, "_signed_get_futures", fake_signed_get_futures)
    assets = asyncio.run(adapter.fetch_futures_assets())

    wallet = next(a for a in assets if a["asset"] == "USDT-FUT")
    pos = next(a for a in assets if a["asset"] == "BTCUSDT")

    assert wallet["kind"] == "cash"
    assert wallet["value_krw"] == pytest.approx(260000.0)
    assert pos["kind"] == "position"
    assert pos["value_krw"] == pytest.approx(1_300_000.0)
    assert pos["pnl_krw"] == pytest.approx(26000.0)


def test_binance_uses_runtime_fx_override(monkeypatch) -> None:
    adapter = BinanceAdapter(
        BinanceConfig(
            spot_api_key="k",
            spot_api_secret="s",
            usdt_krw_rate=1000.0,
        )
    )

    async def fake_signed_get_spot(_: str, __: dict) -> dict:
        return {"balances": [{"asset": "USDT", "free": "1", "locked": "0"}]}

    async def fake_get_spot_prices() -> dict[str, float]:
        return {}

    monkeypatch.setattr(adapter, "_signed_get_spot", fake_signed_get_spot)
    monkeypatch.setattr(adapter, "_get_spot_prices", fake_get_spot_prices)

    assets = asyncio.run(adapter.fetch_spot_assets(usdt_krw_rate=1500.0))
    usdt = next(a for a in assets if a["asset"] == "USDT")
    assert usdt["mark_price"] == pytest.approx(1500.0)


def test_binance_spot_limit_order_payload(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_post_spot(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        return {"orderId": 123, "status": "NEW", "symbol": "BTCUSDT"}

    monkeypatch.setattr(adapter, "_signed_post_spot", fake_signed_post_spot)
    result = asyncio.run(
        adapter.submit_spot_order(
            symbol="btcusdt",
            side="buy",
            quantity=0.01,
            limit_price=100000.0,
            client_order_id="block-1-entry",
        )
    )

    assert captured["path"] == "/api/v3/order"
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["side"] == "BUY"
    assert captured["params"]["type"] == "LIMIT"
    assert captured["params"]["timeInForce"] == "IOC"
    assert captured["params"]["newOrderRespType"] == "FULL"
    assert captured["params"]["quantity"] == "0.01"
    assert captured["params"]["price"] == "100000"
    assert captured["params"]["newClientOrderId"] == "block-1-entry"
    assert result["market"] == "spot"
    assert result["order_id"] == "123"
    assert result["client_order_id"] == "block-1-entry"
    assert result["quantity"] == "0.01"
    assert result["price"] == "100000"
    assert result["request_backfilled_fields"] == [
        "client_order_id",
        "price",
        "quantity",
        "side",
        "type",
    ]


def test_binance_fetch_spot_my_trades_by_order_id(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_get_spot(path: str, params: dict) -> list[dict]:
        captured["path"] = path
        captured["params"] = params
        return [{"id": 1, "orderId": 123, "commission": "0.01"}]

    monkeypatch.setattr(adapter, "_signed_get_spot", fake_signed_get_spot)

    rows = asyncio.run(adapter.fetch_spot_my_trades("btcusdt", order_id="123", limit=50))

    assert captured["path"] == "/api/v3/myTrades"
    assert captured["params"] == {"symbol": "BTCUSDT", "limit": 50, "orderId": 123}
    assert rows[0]["commission"] == "0.01"


def test_binance_futures_reduce_only_close_payload(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_post_futures(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        return {"orderId": 456, "status": "NEW", "symbol": "ETHUSDT"}

    monkeypatch.setattr(adapter, "_signed_post_futures", fake_signed_post_futures)
    result = asyncio.run(
        adapter.submit_futures_order(
            symbol="ethusdt",
            side="buy",
            quantity=0.2,
            limit_price=3000.0,
            client_order_id="block-2-close",
            reduce_only=True,
        )
    )

    assert captured["path"] == "/fapi/v1/order"
    assert captured["params"]["symbol"] == "ETHUSDT"
    assert captured["params"]["reduceOnly"] == "true"
    assert captured["params"]["timeInForce"] == "IOC"
    assert captured["params"]["newOrderRespType"] == "RESULT"
    assert result["market"] == "futures"
    assert result["order_id"] == "456"
    assert result["type"] == "LIMIT"


def test_binance_fetch_futures_user_trades_by_order_id(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_get_futures(path: str, params: dict) -> list[dict]:
        captured["path"] = path
        captured["params"] = params
        return [{"id": 7, "orderId": 456, "commission": "0.02"}]

    monkeypatch.setattr(adapter, "_signed_get_futures", fake_signed_get_futures)

    rows = asyncio.run(
        adapter.fetch_futures_user_trades("ethusdt", order_id="456", limit=25)
    )

    assert captured["path"] == "/fapi/v1/userTrades"
    assert captured["params"] == {"symbol": "ETHUSDT", "limit": 25, "orderId": 456}
    assert rows[0]["commission"] == "0.02"


def test_binance_fetch_futures_income_history_params(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_get_futures(path: str, params: dict) -> list[dict]:
        captured["path"] = path
        captured["params"] = params
        return [{"incomeType": "FUNDING_FEE", "income": "-0.03"}]

    monkeypatch.setattr(adapter, "_signed_get_futures", fake_signed_get_futures)

    rows = asyncio.run(
        adapter.fetch_futures_income_history(
            "ethusdt",
            income_type="funding_fee",
            start_time=111,
            end_time=222,
            limit=10,
        )
    )

    assert captured["path"] == "/fapi/v1/income"
    assert captured["params"] == {
        "symbol": "ETHUSDT",
        "incomeType": "FUNDING_FEE",
        "startTime": 111,
        "endTime": 222,
        "limit": 10,
    }
    assert rows[0]["income"] == "-0.03"


def test_binance_order_response_exposes_executed_quantity() -> None:
    result = BinanceAdapter._normalize_order_response(
        {
            "symbol": "BNBUSDT",
            "orderId": 789,
            "clientOrderId": "ju-test",
            "status": "FILLED",
            "origQty": "0.12",
            "executedQty": "0.12",
            "cumQuote": "78.8",
            "price": "655.360",
        },
        market="futures",
    )

    assert result["quantity"] == "0.12"
    assert result["executed_qty"] == "0.12"
    assert result["cum_quote"] == "78.8"
    assert result["request_backfilled_fields"] == []


def test_binance_order_response_uses_request_params_not_fallback_naming() -> None:
    source = (BinanceAdapter._normalize_order_response.__code__.co_varnames)

    assert "request_params" in source
    assert "fallback" not in source


def test_binance_futures_order_trims_decimal_precision(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_post_futures(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        return {
            "orderId": 789,
            "status": "NEW",
            "symbol": "BNBUSDT",
            "origQty": params["quantity"],
            "price": params["price"],
        }

    monkeypatch.setattr(adapter, "_signed_post_futures", fake_signed_post_futures)

    result = asyncio.run(
        adapter.submit_futures_order(
            symbol="bnbusdt",
            side="sell",
            quantity=0.12368,
            limit_price=593.472846,
            client_order_id="bnb-entry",
        )
    )

    assert captured["path"] == "/fapi/v1/order"
    assert captured["params"]["quantity"] == "0.12368"
    assert captured["params"]["price"] == "593.472846"
    assert result["quantity"] == "0.12368"
    assert result["price"] == "593.472846"


def test_binance_futures_position_risk_mapping(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))

    async def fake_signed_get_futures(path: str, params: dict) -> list[dict]:
        assert path == "/fapi/v2/positionRisk"
        assert params == {}
        return [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.01",
                "entryPrice": "90000",
                "markPrice": "100000",
                "unRealizedProfit": "100",
                "liquidationPrice": "70000",
                "leverage": "2",
                "marginType": "isolated",
            }
        ]

    monkeypatch.setattr(adapter, "_signed_get_futures", fake_signed_get_futures)
    rows = asyncio.run(adapter.fetch_futures_position_risk())

    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["position_amt"] == pytest.approx(0.01)
    assert rows[0]["entry_price"] == pytest.approx(90000.0)
    assert rows[0]["mark_price"] == pytest.approx(100000.0)
    assert rows[0]["unrealized_profit"] == pytest.approx(100.0)
    assert rows[0]["liquidation_price"] == pytest.approx(70000.0)
    assert rows[0]["leverage"] == 2
    assert rows[0]["margin_type"] == "isolated"


def test_binance_market_context_helpers(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    calls = []

    async def fake_public_get(market: str, path: str, params: dict) -> dict:
        calls.append((market, path, params))
        if path.endswith("/ticker/price"):
            return {"symbol": params["symbol"], "price": "101.25"}
        return {
            "symbols": [
                {
                    "symbol": params["symbol"],
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }

    monkeypatch.setattr(adapter, "_public_get", fake_public_get)

    spot_quote = asyncio.run(adapter.fetch_spot_quote("btcusdt"))
    futures_quote = asyncio.run(adapter.fetch_futures_quote("ethusdt"))
    filters = asyncio.run(adapter.fetch_exchange_filters("solusdt", market="spot"))

    assert spot_quote == {"symbol": "BTCUSDT", "price": 101.25, "raw": {"symbol": "BTCUSDT", "price": "101.25"}}
    assert futures_quote["symbol"] == "ETHUSDT"
    assert filters["PRICE_FILTER"]["tickSize"] == "0.10"
    assert filters["LOT_SIZE"]["stepSize"] == "0.001"
    assert calls == [
        ("spot", "/api/v3/ticker/price", {"symbol": "BTCUSDT"}),
        ("futures", "/fapi/v1/ticker/price", {"symbol": "ETHUSDT"}),
        ("spot", "/api/v3/exchangeInfo", {"symbol": "SOLUSDT"}),
    ]


def test_binance_public_market_research_helpers(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig())
    calls = []

    async def fake_public_get(market: str, path: str, params: dict) -> dict | list:
        calls.append((market, path, params))
        if path.endswith("/ticker/24hr"):
            return {
                "symbol": params["symbol"],
                "priceChangePercent": "2.5",
                "quoteVolume": "1000000",
            }
        if path.endswith("/bookTicker"):
            return {"symbol": params["symbol"], "bidPrice": "100", "askPrice": "100.1"}
        if path.endswith("/klines"):
            return [[1, "100", "110", "90", "105", "10", 2, "1050"]]
        if path.endswith("/premiumIndex"):
            return {
                "symbol": params["symbol"],
                "markPrice": "101",
                "indexPrice": "100",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 123,
            }
        if path.endswith("/openInterest"):
            return {"symbol": params["symbol"], "openInterest": "12345"}
        return {}

    monkeypatch.setattr(adapter, "_public_get", fake_public_get)

    assert adapter._to_float("1,234") == pytest.approx(1234.0)
    assert asyncio.run(adapter.fetch_24h_ticker("btcusdt"))["quote_volume"] == pytest.approx(
        1_000_000.0
    )
    assert asyncio.run(adapter.fetch_book_ticker("BTCUSDT"))["spread_bps"] == pytest.approx(
        9.99500249875
    )
    assert asyncio.run(adapter.fetch_klines("BTCUSDT", interval="1m", limit=1))[0][
        "close"
    ] == pytest.approx(105.0)
    assert asyncio.run(adapter.fetch_futures_premium_index("BTCUSDT"))[
        "funding_rate"
    ] == pytest.approx(0.0001)
    assert asyncio.run(adapter.fetch_futures_open_interest("BTCUSDT"))[
        "open_interest"
    ] == pytest.approx(12345.0)
    assert calls == [
        ("spot", "/api/v3/ticker/24hr", {"symbol": "BTCUSDT"}),
        ("spot", "/api/v3/ticker/bookTicker", {"symbol": "BTCUSDT"}),
        ("spot", "/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 1}),
        ("futures", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}),
        ("futures", "/fapi/v1/openInterest", {"symbol": "BTCUSDT"}),
    ]


def test_binance_fetch_24h_tickers_normalizes_all_symbol_rows(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig())

    async def fake_public_get(market: str, path: str, params: dict) -> list[dict]:
        assert market == "spot"
        assert path == "/api/v3/ticker/24hr"
        assert params == {}
        return [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "75000",
                "priceChangePercent": "-2.1",
                "quoteVolume": "1000000000",
            },
            {
                "symbol": "ETHUSDT",
                "lastPrice": "2000",
                "priceChangePercent": "1.2",
                "quoteVolume": "800000000",
            },
        ]

    monkeypatch.setattr(adapter, "_public_get", fake_public_get)

    rows = asyncio.run(adapter.fetch_24h_tickers())

    assert [row["symbol"] for row in rows] == ["BTCUSDT", "ETHUSDT"]
    assert rows[0]["price"] == pytest.approx(75000.0)
    assert rows[0]["change_pct_24h"] == pytest.approx(-2.1)
    assert rows[0]["quote_volume"] == pytest.approx(1_000_000_000.0)


def test_binance_fetch_open_orders_normalizes_market(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))

    async def fake_signed_get_spot(path: str, params: dict) -> list[dict]:
        assert path == "/api/v3/openOrders"
        assert params == {"symbol": "BTCUSDT"}
        return [
            {
                "orderId": 789,
                "clientOrderId": "block-open",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "type": "LIMIT",
                "origQty": "0.01000000",
                "price": "110000.00000000",
                "status": "NEW",
            }
        ]

    monkeypatch.setattr(adapter, "_signed_get_spot", fake_signed_get_spot)
    rows = asyncio.run(adapter.fetch_open_orders(market="spot", symbol="btcusdt"))

    assert rows[0]["market"] == "spot"
    assert rows[0]["order_id"] == "789"
    assert rows[0]["client_order_id"] == "block-open"
    assert rows[0]["quantity"] == "0.01000000"
