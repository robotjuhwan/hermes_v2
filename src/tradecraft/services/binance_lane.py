from __future__ import annotations

import re
from typing import Any, Callable

from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    normalize_market as default_normalize_market,
    normalize_position_side as default_normalize_position_side,
)

BINANCE_BLOCK_HORIZONS = {"short", "mid", "long", "futures"}
BINANCE_HORIZON_ALIASES = {
    "short_term": "short",
    "shortterm": "short",
    "scalp": "short",
    "intraday": "short",
    "day": "short",
    "mid_term": "mid",
    "midterm": "mid",
    "swing": "mid",
    "medium": "mid",
    "long_term": "long",
    "longterm": "long",
    "position": "long",
    "core": "long",
    "future": "futures",
    "futures": "futures",
    "perp": "futures",
    "perpetual": "futures",
}
BINANCE_HORIZON_COLORS = {
    "short": "short",
    "mid": "mid",
    "long": "long",
    "futures": "futures",
}
BINANCE_MANAGER_LANES = (
    "spot:long",
    "futures:long",
    "futures:short",
    "upbit_spot:long",
    "volatile_attack",
)
BINANCE_ALLOCATION_LANES = ("short", "mid", "long", "futures", "volatile_attack")


def parse_universe(value: str) -> list[str]:
    symbols = [
        str(part or "").strip().upper()
        for part in re.split(r"[\s,]+", str(value or ""))
        if str(part or "").strip()
    ]
    return list(dict.fromkeys(symbols))


def normalize_binance_horizon(value: Any, *, market: str = "spot") -> str:
    if market == "futures":
        return "futures"
    raw = str(value or "short").strip().lower()
    compact = re.sub(r"[\s/_-]+", "_", raw)
    squashed = re.sub(r"[\s/_-]+", "", raw)
    horizon = (
        BINANCE_HORIZON_ALIASES.get(raw)
        or BINANCE_HORIZON_ALIASES.get(compact)
        or BINANCE_HORIZON_ALIASES.get(squashed)
        or (raw if raw in BINANCE_BLOCK_HORIZONS else "short")
    )
    return "short" if horizon == "futures" else horizon


def raw_binance_horizon_requests_futures(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[\s/_-]+", "_", raw)
    squashed = re.sub(r"[\s/_-]+", "", raw)
    return (
        raw in {"futures", "future", "perp", "perpetual"}
        or compact
        in {
            "futures",
            "future",
            "binance_futures",
            "binance_future",
            "usdm_futures",
            "um_futures",
            "perp",
            "perpetual",
        }
        or squashed in {"futures", "future", "binancefutures", "binancefuture"}
    )


def binance_block_lane(*, market: str, horizon: str, side: Any = "long") -> str:
    _ = side
    if market == "futures":
        return "futures"
    if market == UPBIT_SPOT_MARKET:
        return "upbit_spot:long"
    return horizon if horizon in {"short", "mid", "long"} else "short"


def normalize_binance_display_lane(
    *,
    lane: Any = "",
    market: str,
    horizon: str,
    side: Any = "long",
) -> str:
    raw = str(lane or "").strip().lower()
    if raw == "volatile_attack":
        return "volatile_attack"
    if raw in BINANCE_MANAGER_LANES:
        return raw
    return binance_block_lane(market=market, horizon=horizon, side=side)


def binance_market_side_lane(
    row: dict[str, Any],
    *,
    normalize_market: Callable[[Any], str] = default_normalize_market,
    normalize_position_side: Callable[[Any], str] = default_normalize_position_side,
) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    lane = str(
        row.get("lane")
        or metadata.get("lane")
        or calculated.get("lane")
        or ""
    ).strip().lower()
    if lane == "volatile_attack":
        return "volatile_attack"
    market = normalize_market(row.get("market") or row.get("venue"))
    side = normalize_position_side(row.get("side") or row.get("direction"))
    return f"{market}:{side}"


def canonical_binance_performance_lane(
    *,
    raw_lane: Any,
    market: Any,
    side: Any,
) -> str:
    lane = str(raw_lane or "").strip().lower()
    market_key = default_normalize_market(market)
    side_key = default_normalize_position_side(side)
    if lane == "volatile_attack":
        return "volatile_attack"
    if lane in BINANCE_MANAGER_LANES:
        return lane
    if market_key == "futures":
        return f"futures:{side_key}"
    if lane in {"short", "mid", "long"}:
        return f"{market_key}:{side_key}:{lane}"
    return lane or f"{market_key}:{side_key}"


def binance_performance_lane_from_payload(
    row: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    return canonical_binance_performance_lane(
        raw_lane=payload.get("lane"),
        market=row.get("market"),
        side=row.get("side"),
    )
