from __future__ import annotations

import math
import re
from typing import Any

from tradecraft.services.binance_lane import (
    BINANCE_MANAGER_LANES,
    binance_market_side_lane,
    normalize_binance_horizon,
)
from tradecraft.services.binance_symbol import normalize_market, normalize_position_side
from tradecraft.services.binance_performance_policy import growth_governor_row_lanes

LANE_CONCENTRATION_MIN_SAMPLE = 8
LANE_CONCENTRATION_SHARE_PCT = 70.0
ACTIVE_DUPLICATE_STATUSES = {"proposed", "entry_pending", "open", "exit_pending"}


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return 0.0
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> int:
    return int(math.floor(_safe_float(value)))


def prices_within_bps(left: float, right: float, tolerance_bps: float) -> bool:
    if left <= 0 or right <= 0:
        return False
    reference = max(abs(right), 1e-12)
    return abs(left - right) / reference * 10_000 <= tolerance_bps


def row_price_value(row: dict[str, Any], key: str) -> float:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    block_template = (
        row.get("block_template") if isinstance(row.get("block_template"), dict) else {}
    )
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    calculated_price_plan = (
        row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else metadata.get("calculated_price_plan")
        if isinstance(metadata.get("calculated_price_plan"), dict)
        else {}
    )
    containers = (row, block_template, metadata, calculated, calculated_price_plan)
    aliases = [key, key.replace("_price", "_price_usdt")]
    if key == "entry_price":
        aliases.extend(("entry_trigger_price", "trigger_price"))
    for container in containers:
        for alias in aliases:
            value = _safe_float(container.get(alias))
            if value > 0:
                return value
    return 0.0


def row_pattern_live_crosscheck_status(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    calculated_price_plan = (
        row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else metadata.get("calculated_price_plan")
        if isinstance(metadata.get("calculated_price_plan"), dict)
        else {}
    )
    for source in (row, metadata, calculated, calculated_price_plan):
        crosscheck = (
            source.get("pattern_live_crosscheck")
            if isinstance(source, dict)
            and isinstance(source.get("pattern_live_crosscheck"), dict)
            else {}
        )
        status = str(crosscheck.get("status") or "").strip().lower()
        if status:
            return status
    return ""


def lane_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {lane: 0 for lane in BINANCE_MANAGER_LANES}
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane = binance_market_side_lane(
            row,
            normalize_market=normalize_market,
            normalize_position_side=normalize_position_side,
        )
        counts.setdefault(lane, 0)
        counts[lane] += 1
        total += 1
    dominant_lane = ""
    dominant_count = 0
    if counts:
        dominant_lane, dominant_count = max(
            counts.items(),
            key=lambda item: (item[1], item[0]),
        )
    dominant_share = (dominant_count / total * 100.0) if total > 0 else 0.0
    return {
        "total": total,
        "items": {
            lane: {
                "count": count,
                "share_pct": (count / total * 100.0) if total else 0.0,
            }
            for lane, count in sorted(counts.items())
        },
        "dominant_lane": dominant_lane if dominant_count > 0 else "",
        "dominant_count": dominant_count,
        "dominant_share_pct": round(dominant_share, 4),
        "requires_review": (
            total >= LANE_CONCENTRATION_MIN_SAMPLE
            and dominant_share >= LANE_CONCENTRATION_SHARE_PCT
        ),
        "threshold": {
            "min_sample": LANE_CONCENTRATION_MIN_SAMPLE,
            "share_pct": LANE_CONCENTRATION_SHARE_PCT,
        },
    }


def lane_authority_key_variants(value: Any) -> set[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return set()
    compact = re.sub(r"[\s/]+", "_", raw)
    variants = {
        compact,
        compact.replace("_", ":"),
        compact.replace(":", "_"),
    }
    if compact.startswith("futures_"):
        variants.add(compact.replace("futures_", "futures:", 1))
    parts = compact.replace("_", ":").split(":")
    if len(parts) >= 2 and parts[0] == "futures" and parts[1] in {"long", "short"}:
        variants.add(f"{parts[0]}:{parts[1]}")
        variants.add(f"{parts[0]}_{parts[1]}")
    if compact.startswith("spot:") or compact.startswith("spot_"):
        variants.add("spot")
    if parts and parts[0] == "spot":
        variants.add("spot")
    if compact.startswith("upbit_spot:") or compact.startswith("upbit_spot_"):
        variants.add("upbit_spot")
        variants.add("upbit_spot:long")
    return {item for item in variants if item}


def candidate_lane_authority_context(
    live_authority: dict[str, Any] | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    payload = live_authority if isinstance(live_authority, dict) else {}
    lane_authority = (
        payload.get("lane_authority")
        if isinstance(payload.get("lane_authority"), dict)
        else {}
    )
    if not lane_authority:
        return {}
    row_lanes: set[str] = set()
    for lane in growth_governor_row_lanes(row):
        row_lanes.update(lane_authority_key_variants(lane))
    if not row_lanes:
        return {}
    lane_actions = (
        lane_authority.get("lane_actions")
        if isinstance(lane_authority.get("lane_actions"), dict)
        else {}
    )
    weak_lanes = {
        variant
        for item in lane_authority.get("weak_lanes") or []
        for variant in lane_authority_key_variants(item)
    }
    insufficient_lanes = {
        variant
        for item in lane_authority.get("insufficient_lanes") or []
        for variant in lane_authority_key_variants(item)
    }
    scale_candidate_lanes = {
        variant
        for item in lane_authority.get("scale_candidate_lanes") or []
        for variant in lane_authority_key_variants(item)
    }
    qualified_lanes = {
        variant
        for item in lane_authority.get("qualified_lanes") or []
        for variant in lane_authority_key_variants(item)
    }

    def build_context(matched_key: str, matched_action: dict[str, Any]) -> dict[str, Any]:
        matched_variants = lane_authority_key_variants(matched_key)
        weak = bool(row_lanes.intersection(weak_lanes)) or bool(
            matched_variants.intersection(weak_lanes)
        )
        insufficient = bool(row_lanes.intersection(insufficient_lanes)) or bool(
            matched_variants.intersection(insufficient_lanes)
        )
        scale_candidate = bool(row_lanes.intersection(scale_candidate_lanes)) or bool(
            matched_variants.intersection(scale_candidate_lanes)
        )
        qualified = bool(row_lanes.intersection(qualified_lanes)) or bool(
            matched_variants.intersection(qualified_lanes)
        )
        grade = str(matched_action.get("grade") or "").strip().lower()
        if not grade:
            grade = (
                "weak"
                if weak
                else "insufficient"
                if insufficient
                else "scale_candidate"
                if scale_candidate
                else "qualified"
                if qualified
                else ""
            )
        expectancy = _safe_float(matched_action.get("expectancy_pct"))
        win_rate = _safe_float(matched_action.get("win_rate_pct"))
        profit_factor = _safe_float(matched_action.get("profit_factor"))
        sample_count = _safe_int(matched_action.get("sample_count"))
        positive_sample_building = bool(
            grade in {"insufficient", "qualified", "scale_candidate"}
            and expectancy > 0
            and profit_factor >= 1.2
            and (
                win_rate >= 50.0
                or (win_rate <= 0 and sample_count >= 5 and profit_factor >= 1.5)
            )
        )
        selection_bias = "neutral"
        if positive_sample_building:
            selection_bias = "positive_sample_building"
        elif grade == "scale_candidate":
            selection_bias = "scale_candidate"
        elif (
            weak
            or grade in {"weak", "observe_only"}
            or expectancy < 0
            or (0 < profit_factor < 1.0)
        ):
            selection_bias = "avoid_weak_lane"
        context = {
            "version": "binance_lane_authority_candidate_v1",
            "lane": matched_key,
            "row_lanes": sorted(row_lanes)[:8],
            "grade": grade,
            "action": str(matched_action.get("action") or "").strip(),
            "selection_bias": selection_bias,
            "sample_count": sample_count,
            "expectancy_pct": expectancy,
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "requires_waiting_entry": bool(
                matched_action.get("requires_waiting_entry")
            ),
        }
        return {
            key: value
            for key, value in context.items()
            if value not in (None, "", [], {})
        }

    contexts: list[dict[str, Any]] = []
    for raw_key, raw_action in lane_actions.items():
        key_variants = lane_authority_key_variants(raw_key)
        if not key_variants.intersection(row_lanes):
            continue
        action = dict(raw_action) if isinstance(raw_action, dict) else {}
        contexts.append(build_context(str(raw_key), action))
    if not contexts:
        for collection in (
            weak_lanes,
            insufficient_lanes,
            scale_candidate_lanes,
            qualified_lanes,
        ):
            matched = sorted(row_lanes.intersection(collection))
            if matched:
                contexts.append(build_context(matched[0], {}))
                break
    if not contexts:
        return {}
    bias_rank = {
        "avoid_weak_lane": 0,
        "neutral": 1,
        "scale_candidate": 2,
        "positive_sample_building": 3,
    }
    return max(
        contexts,
        key=lambda context: (
            bias_rank.get(str(context.get("selection_bias") or ""), 1),
            _safe_float(context.get("expectancy_pct")),
            _safe_float(context.get("profit_factor")),
            _safe_int(context.get("sample_count")),
        ),
    )


def near_duplicate_active_blocks_context(
    active_blocks: list[dict[str, Any]],
    *,
    tolerance_bps: float,
) -> dict[str, Any]:
    tolerance = max(_safe_float(tolerance_bps), 0.0)
    clusters: dict[tuple[str, str, str, str], list[list[dict[str, Any]]]] = {}
    for block in active_blocks:
        if not isinstance(block, dict):
            continue
        status = str(block.get("status") or "").strip().lower()
        if status not in ACTIVE_DUPLICATE_STATUSES:
            continue
        symbol = str(block.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        market = normalize_market(block.get("market") or block.get("venue"))
        side = normalize_position_side(block.get("side"))
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        horizon = normalize_binance_horizon(
            block.get("horizon") or metadata.get("horizon"),
            market=market,
        )
        entry = _safe_float(block.get("entry_price"))
        target = _safe_float(block.get("target_price"))
        stop = _safe_float(block.get("stop_price"))
        if min(entry, target, stop) <= 0:
            continue
        row = {
            "block_id": str(block.get("block_id") or ""),
            "status": status,
            "symbol": symbol,
            "market": market,
            "side": side,
            "horizon": horizon,
            "entry_price": entry,
            "target_price": target,
            "stop_price": stop,
        }
        key = (symbol, market, side, horizon)
        key_clusters = clusters.setdefault(key, [])
        for cluster in key_clusters:
            reference = cluster[0]
            if (
                prices_within_bps(entry, reference["entry_price"], tolerance)
                and prices_within_bps(target, reference["target_price"], tolerance)
                and prices_within_bps(stop, reference["stop_price"], tolerance)
            ):
                cluster.append(row)
                break
        else:
            key_clusters.append([row])

    groups: list[dict[str, Any]] = []
    for key_clusters in clusters.values():
        for cluster in key_clusters:
            if len(cluster) < 2:
                continue
            entries = [_safe_float(row.get("entry_price")) for row in cluster]
            targets = [_safe_float(row.get("target_price")) for row in cluster]
            stops = [_safe_float(row.get("stop_price")) for row in cluster]
            first = cluster[0]
            groups.append(
                {
                    "symbol": first["symbol"],
                    "market": first["market"],
                    "side": first["side"],
                    "horizon": first["horizon"],
                    "block_count": len(cluster),
                    "block_ids": [
                        str(row.get("block_id") or "")
                        for row in cluster[:6]
                        if str(row.get("block_id") or "")
                    ],
                    "statuses": sorted(
                        {
                            str(row.get("status") or "")
                            for row in cluster
                            if str(row.get("status") or "")
                        }
                    ),
                    "entry_price_range": [min(entries), max(entries)],
                    "target_price_range": [min(targets), max(targets)],
                    "stop_price_range": [min(stops), max(stops)],
                }
            )
    groups.sort(
        key=lambda row: (
            -_safe_int(row.get("block_count")),
            str(row.get("symbol") or ""),
        )
    )
    status = "review_required" if groups else "ok"
    return {
        "version": "binance_near_duplicate_active_blocks_v1",
        "status": status,
        "group_count": len(groups),
        "tolerance_bps": tolerance,
        "groups": groups[:8],
        "instruction": (
            "avoid adding near-identical blocks; update/pause/close only with fresh risk evidence"
            if groups
            else "ok: no near-duplicate active block groups"
        ),
    }


def candidate_near_duplicate_active_block_context(
    row: dict[str, Any],
    active_blocks: list[dict[str, Any]],
    *,
    tolerance_bps: float,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return {}
    market = normalize_market(row.get("market") or row.get("venue"))
    side = normalize_position_side(row.get("side"))
    horizon = normalize_binance_horizon(row.get("horizon"), market=market)
    entry = row_price_value(row, "entry_price")
    target = row_price_value(row, "target_price")
    stop = row_price_value(row, "stop_price")
    if min(entry, target, stop) <= 0:
        return {}
    tolerance = max(_safe_float(tolerance_bps), 0.0)
    for block in active_blocks:
        if not isinstance(block, dict):
            continue
        status = str(block.get("status") or "").strip().lower()
        if status not in ACTIVE_DUPLICATE_STATUSES:
            continue
        if str(block.get("symbol") or "").upper().strip() != symbol:
            continue
        if normalize_market(block.get("market")) != market:
            continue
        if normalize_position_side(block.get("side")) != side:
            continue
        block_metadata = (
            block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        )
        block_horizon = normalize_binance_horizon(
            block.get("horizon") or block_metadata.get("horizon"),
            market=market,
        )
        if block_horizon != horizon:
            continue
        block_entry = _safe_float(block.get("entry_price"))
        block_target = _safe_float(block.get("target_price"))
        block_stop = _safe_float(block.get("stop_price"))
        if min(block_entry, block_target, block_stop) <= 0:
            continue
        if not (
            prices_within_bps(entry, block_entry, tolerance)
            and prices_within_bps(target, block_target, tolerance)
            and prices_within_bps(stop, block_stop, tolerance)
        ):
            continue
        return {
            "version": "binance_candidate_near_duplicate_active_block_v1",
            "status": "review_required",
            "action_hint": "manage_existing_block",
            "existing_block_id": str(block.get("block_id") or ""),
            "existing_status": status,
            "symbol": symbol,
            "market": market,
            "side": side,
            "horizon": horizon,
            "tolerance_bps": tolerance,
            "candidate": {
                "entry_price": entry,
                "target_price": target,
                "stop_price": stop,
            },
            "existing": {
                "entry_price": block_entry,
                "target_price": block_target,
                "stop_price": block_stop,
            },
            "instruction": (
                "Do not create another near-identical block. Use update_blocks, "
                "close_blocks, pause_blocks, or hold_decision for the existing block "
                "unless the thesis, horizon, or price geometry is materially different."
            ),
        }
    return {}


def manager_lane_balance_context(
    *,
    recent_blocks: list[dict[str, Any]],
    active_blocks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    performance: dict[str, Any],
    tolerance_bps: float,
) -> dict[str, Any]:
    side_scorecards = [
        {
            "lane": str(row.get("side") or ""),
            "sample_count": _safe_int(row.get("sample_count")),
            "pnl_usdt": _safe_float(row.get("pnl_usdt")),
            "avg_r_multiple": _safe_float(row.get("avg_r_multiple")),
            "win_rate_pct": _safe_float(row.get("win_rate_pct")),
        }
        for row in performance.get("side_scorecards") or []
        if isinstance(row, dict)
    ]
    recent_distribution = lane_distribution(recent_blocks)
    candidate_distribution = lane_distribution(candidates)
    near_duplicates = near_duplicate_active_blocks_context(
        active_blocks,
        tolerance_bps=tolerance_bps,
    )
    return {
        "version": "binance_lane_balance_v1",
        "recent_blocks": recent_distribution,
        "active_blocks": lane_distribution(active_blocks),
        "near_duplicate_active_blocks": near_duplicates,
        "candidate_lanes": candidate_distribution,
        "performance_lanes": side_scorecards,
        "review_required": bool(
            recent_distribution.get("requires_review")
            or near_duplicates.get("status") == "review_required"
        ),
        "dominant_lane": recent_distribution.get("dominant_lane") or "",
        "instructions": [
            "Evaluate spot:long, futures:long, and futures:short as separate lanes before selecting actions.",
            "If recent_blocks.requires_review is true and Jue creates another block in the dominant lane, lane_review must explain why non-dominant lanes were rejected.",
            "If near_duplicate_active_blocks.status is review_required, avoid adding another similar block and inspect whether one block should be updated, paused, or closed.",
            "This is not a hard filter: do not force a weak long, but do not let a backlog of short evidence hide live long candidates.",
        ],
    }
