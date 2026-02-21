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
