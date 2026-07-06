from __future__ import annotations

import math

from tradecraft.services import binance_lane as binance_lane_module
from tradecraft.services.binance_lane import (
    UPBIT_SPOT_MARKET,
    binance_block_lane,
    binance_market_side_lane,
    binance_performance_lane_from_payload,
    canonical_binance_performance_lane,
    normalize_binance_display_lane,
    normalize_binance_horizon,
    raw_binance_horizon_requests_futures,
)
from tradecraft.services.binance_ledger import build_lane_allocation_summary


def test_normalize_binance_horizon_aliases_and_market_override() -> None:
    assert normalize_binance_horizon("mid term") == "mid"
    assert normalize_binance_horizon("LONG_TERM") == "long"
    assert normalize_binance_horizon("perpetual") == "short"
    assert normalize_binance_horizon("mid", market="futures") == "futures"
    assert normalize_binance_horizon("unknown") == "short"


def test_raw_binance_horizon_requests_futures_detects_scope_aliases() -> None:
    assert raw_binance_horizon_requests_futures("binance futures")
    assert raw_binance_horizon_requests_futures("USDM-Futures")
    assert raw_binance_horizon_requests_futures("perp")
    assert not raw_binance_horizon_requests_futures("mid")


def test_parse_universe_normalizes_deduplicates_and_accepts_whitespace() -> None:
    assert hasattr(binance_lane_module, "parse_universe")
    assert binance_lane_module.parse_universe(" btcusdt,ETHUSDT\nbtcusdt\tSOLUSDT  ") == [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
    ]
    assert binance_lane_module.parse_universe("") == []


def test_binance_block_lane_and_display_lane_normalization() -> None:
    assert binance_block_lane(market="futures", horizon="short") == "futures"
    assert binance_block_lane(market=UPBIT_SPOT_MARKET, horizon="long") == "upbit_spot:long"
    assert binance_block_lane(market="spot", horizon="long") == "long"
    assert binance_block_lane(market="spot", horizon="futures") == "short"
    assert (
        normalize_binance_display_lane(
            lane="volatile_attack",
            market="spot",
            horizon="short",
        )
        == "volatile_attack"
    )
    assert (
        normalize_binance_display_lane(
            lane="futures:short",
            market="spot",
            horizon="long",
        )
        == "futures:short"
    )
    assert normalize_binance_display_lane(lane="", market="spot", horizon="mid") == "mid"


def test_binance_market_side_lane_uses_market_and_side_normalizers() -> None:
    assert (
        binance_market_side_lane(
            {"lane": "volatile_attack", "market": "spot", "side": "long"},
            normalize_market=lambda value: str(value or "spot"),
            normalize_position_side=lambda value: str(value or "long"),
        )
        == "volatile_attack"
    )
    assert (
        binance_market_side_lane(
            {"market": "binance_futures", "direction": "short"},
            normalize_market=lambda value: "futures"
            if "futures" in str(value)
            else "spot",
            normalize_position_side=lambda value: "short"
            if str(value) == "short"
            else "long",
        )
        == "futures:short"
    )


def test_canonical_binance_performance_lane_normalizes_market_side_and_legacy_lanes() -> None:
    assert (
        canonical_binance_performance_lane(
            raw_lane="volatile_attack",
            market="spot",
            side="long",
        )
        == "volatile_attack"
    )
    assert (
        canonical_binance_performance_lane(
            raw_lane="futures:short",
            market="spot",
            side="long",
        )
        == "futures:short"
    )
    assert (
        canonical_binance_performance_lane(
            raw_lane="",
            market="binance_futures",
            side="short",
        )
        == "futures:short"
    )
    assert (
        canonical_binance_performance_lane(
            raw_lane="mid",
            market="spot",
            side="long",
        )
        == "spot:long:mid"
    )
    assert (
        canonical_binance_performance_lane(
            raw_lane="spot:long:short",
            market="spot",
            side="long",
        )
        == "spot:long:short"
    )
    assert (
        canonical_binance_performance_lane(
            raw_lane="",
            market="upbit",
            side="long",
        )
        == "upbit_spot:long"
    )


def test_binance_performance_lane_from_payload_prefers_payload_lane() -> None:
    row = {"market": "futures", "side": "short"}

    assert (
        binance_performance_lane_from_payload(row, {"lane": "volatile_attack"})
        == "volatile_attack"
    )
    assert binance_performance_lane_from_payload(row, {}) == "futures:short"


def test_build_lane_allocation_summary_counts_active_blocks_by_lane() -> None:
    summary = build_lane_allocation_summary(
        [
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "lane": "short",
                "qty_open": 0.1,
                "entry_price": 100.0,
                "status": "open",
            },
            {
                "symbol": "ETHUSDT",
                "market": "spot",
                "horizon": "mid term",
                "qty_open": 0.2,
                "entry_price": 50.0,
                "status": "entry_pending",
            },
            {
                "symbol": "SOLUSDT",
                "market": "futures",
                "qty_open": 1.0,
                "entry_price": 20.0,
                "status": "open",
            },
            {
                "symbol": "MEMEUSDT",
                "market": "spot",
                "lane": "volatile_attack",
                "qty_open": 100.0,
                "entry_price": 0.02,
                "status": "open",
            },
            {
                "symbol": "BNBUSDT",
                "market": "spot",
                "horizon": "long",
                "qty_open": 1.0,
                "entry_price": 300.0,
                "status": "closed",
            },
        ]
    )

    rows = {row["lane"]: row for row in summary["items"]}
    assert rows["short"]["block_count"] == 1
    assert rows["short"]["value_usdt"] == 10.0
    assert rows["mid"]["block_count"] == 1
    assert rows["mid"]["value_usdt"] == 10.0
    assert rows["futures"]["block_count"] == 1
    assert rows["futures"]["value_usdt"] == 20.0
    assert rows["volatile_attack"]["block_count"] == 1
    assert rows["volatile_attack"]["value_usdt"] == 2.0
    assert rows["long"]["block_count"] == 0
    assert summary["total_value_usdt"] == 42.0


def test_build_lane_allocation_summary_ignores_non_finite_notional_inputs() -> None:
    summary = build_lane_allocation_summary(
        [
            {
                "symbol": "BADQTY",
                "market": "spot",
                "lane": "short",
                "qty_open": "inf",
                "entry_price": 100.0,
                "status": "open",
            },
            {
                "symbol": "BADPRICE",
                "market": "spot",
                "lane": "mid",
                "qty_open": 1.0,
                "entry_price": "1e9999",
                "status": "open",
            },
            {
                "symbol": "OK",
                "market": "spot",
                "lane": "long",
                "qty_open": 2.0,
                "entry_price": 10.0,
                "status": "open",
            },
        ]
    )

    rows = {row["lane"]: row for row in summary["items"]}
    assert rows["short"]["block_count"] == 0
    assert rows["mid"]["value_usdt"] == 0.0
    assert rows["long"]["value_usdt"] == 20.0
    assert math.isfinite(summary["total_value_usdt"])
