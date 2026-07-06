from __future__ import annotations

import re
from typing import Any

from tradecraft.services.binance_lane import (
    binance_block_lane,
    normalize_binance_display_lane,
    normalize_binance_horizon,
)
from tradecraft.services.binance_manager_prompt import (
    validation_repair_discipline_tokens,
    validation_repair_period_memory_quality_tokens,
)
from tradecraft.services.binance_symbol import normalize_market, normalize_position_side


def growth_governor_row_lanes(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    market = normalize_market(row.get("market") or row.get("venue"))
    side = normalize_position_side(row.get("side"))
    horizon = normalize_binance_horizon(row.get("horizon"), market=market)
    lane = normalize_binance_display_lane(
        lane=row.get("lane") or metadata.get("lane") or calculated.get("lane"),
        market=market,
        horizon=horizon,
        side=side,
    )
    setup_tokens = {
        re.sub(r"[\s/]+", "_", str(value or "").strip().lower())
        for value in (
            row.get("strategy_family"),
            row.get("entry_setup"),
            row.get("setup"),
            row.get("evidence_key"),
            metadata.get("strategy_family"),
            metadata.get("entry_setup"),
            metadata.get("setup"),
            metadata.get("evidence_key"),
            calculated.get("strategy_family"),
            calculated.get("entry_setup"),
            calculated.get("setup"),
            calculated.get("evidence_key"),
        )
        if str(value or "").strip()
    }
    lanes = {
        f"{market}:{side}",
        binance_block_lane(market=market, horizon=horizon, side=side),
    }
    if lane:
        lanes.add(lane)
        if market and lane in {"short", "mid", "long"} and market != "futures":
            lanes.add(f"{market}:{side}:{lane}")
        elif market and ":" not in lane and lane not in {"futures", "volatile_attack"}:
            lanes.add(f"{market}:{lane}")
    if market == "futures":
        lanes.add("futures")
    base_lanes = {item for item in lanes if item}

    def should_append_setup_token(base_lane: str, token: str) -> bool:
        if not base_lane or not token:
            return False
        if token == base_lane:
            return False
        base_parts = [part for part in base_lane.split(":") if part]
        token_parts = [part for part in token.split(":") if part]
        if len(token_parts) == 1 and token_parts[0] in base_parts:
            return False
        if len(token_parts) > 1 and (
            token.startswith(f"{base_lane}:")
            or base_lane.startswith(f"{token}:")
            or token in base_lanes
        ):
            return False
        return True

    for token in setup_tokens:
        if token not in base_lanes:
            lanes.add(token)
        for base_lane in base_lanes:
            if not should_append_setup_token(base_lane, token):
                continue
            lanes.add(f"{base_lane}:{token}")
    repair = row.get("validation_repair")
    if not isinstance(repair, dict):
        repair = metadata.get("validation_repair")
    for token in validation_repair_discipline_tokens(repair):
        for base_lane in base_lanes:
            lanes.add(f"{base_lane}:validation:{token}")
    for token in validation_repair_period_memory_quality_tokens(repair):
        for base_lane in base_lanes:
            lanes.add(f"{base_lane}:period_memory:{token}")
    return {item for item in lanes if item}
