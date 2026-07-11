from __future__ import annotations

from tradecraft.api.crypto_payloads import (
    DEFAULT_CRYPTO_KLINE_INTERVALS,
    crypto_research_symbols,
    default_crypto_research_symbols,
    parse_crypto_kline_intervals,
)


def test_crypto_research_symbols_normalizes_filters_and_deduplicates() -> None:
    assert crypto_research_symbols("btc, eth;$bad ABC BTC BTCUSDT A") == [
        "BTC",
        "ETH",
        "ABC",
        "BTCUSDT",
    ]
    assert crypto_research_symbols(["sol", "SOL", "x:y", "bad symbol"]) == [
        "SOL",
        "X:Y",
    ]


def test_default_crypto_research_symbols_uses_configured_universe() -> None:
    assert default_crypto_research_symbols("btc,eth") == ["BTC", "ETH"]


def test_parse_crypto_kline_intervals_keeps_positive_integer_limits() -> None:
    assert parse_crypto_kline_intervals("1m:120,5m:96,bad,1h:nope,4h:0") == {
        "1m": 120,
        "5m": 96,
    }


def test_parse_crypto_kline_intervals_returns_default_when_empty() -> None:
    assert parse_crypto_kline_intervals("bad") == DEFAULT_CRYPTO_KLINE_INTERVALS
