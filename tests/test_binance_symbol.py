from __future__ import annotations

from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    explicit_market_scope,
    is_upbit_market,
    normalize_market,
    normalize_position_side,
    upbit_market_symbol,
    upbit_market_to_usdt_symbol,
)


def test_normalize_market_understands_binance_and_upbit_aliases() -> None:
    assert normalize_market(None) == "spot"
    assert normalize_market("binance spot") == "spot"
    assert normalize_market("spot/account") == "spot"
    assert normalize_market("binance futures") == "futures"
    assert normalize_market("USDM-Futures") == "futures"
    assert normalize_market("futures_wallet") == "futures"
    assert normalize_market("upbit-spot") == UPBIT_SPOT_MARKET
    assert normalize_market("krw spot") == UPBIT_SPOT_MARKET
    assert normalize_market("unknown") == "spot"


def test_explicit_market_scope_returns_only_requested_scopes() -> None:
    assert explicit_market_scope("") == ""
    assert explicit_market_scope("spot") == "spot"
    assert explicit_market_scope("binance futures") == "futures"
    assert explicit_market_scope("upbit") == UPBIT_SPOT_MARKET
    assert explicit_market_scope("something else") == ""


def test_upbit_symbol_helpers_convert_between_krw_and_usdt_forms() -> None:
    assert is_upbit_market("upbit")
    assert upbit_market_symbol("BTCUSDT") == "KRW-BTC"
    assert upbit_market_symbol("BTCKRW") == "KRW-BTC"
    assert upbit_market_symbol("KRW-BTC") == "KRW-BTC"
    assert upbit_market_symbol("USDT-BTC") == "KRW-BTC"
    assert upbit_market_to_usdt_symbol("KRW-BTC") == "BTCUSDT"
    assert upbit_market_to_usdt_symbol("BTCKRW") == "BTCUSDT"
    assert upbit_market_to_usdt_symbol("BTCUSDT") == "BTCUSDT"


def test_normalize_position_side_accepts_short_aliases_only() -> None:
    assert normalize_position_side("sell") == "short"
    assert normalize_position_side("SHORT") == "short"
    assert normalize_position_side("숏") == "long"
    assert normalize_position_side("long") == "long"
    assert normalize_position_side(None) == "long"
