from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, limit: int = 160) -> str:
    return str(value or "").strip()[: max(int(limit), 1)]


def _metadata_from_source(source: dict[str, Any] | None) -> dict[str, Any]:
    payload = source if isinstance(source, dict) else {}
    metadata: dict[str, Any] = {}
    block = payload.get("block") if isinstance(payload.get("block"), dict) else {}
    if isinstance(block, dict):
        block_metadata_json = _json_loads(block.get("metadata_json"))
        if isinstance(block_metadata_json, dict):
            metadata.update(block_metadata_json)
        block_metadata = block.get("metadata")
        if isinstance(block_metadata, dict):
            metadata.update(block_metadata)
    source_metadata = payload.get("metadata")
    if isinstance(source_metadata, dict):
        metadata.update(source_metadata)
    return metadata


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _clean_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_compact_strategy_key(value: str) -> bool:
    key = _clean_key(value)
    if not key or len(key) > 80:
        return False
    if not any("a" <= char <= "z" for char in key):
        return False
    return all(
        ("a" <= char <= "z") or char.isdigit() or char in {"_", ":"}
        for char in key
    )


def _first_compact_strategy_key(*values: Any) -> str:
    for value in values:
        key = _clean_key(value)
        if _is_compact_strategy_key(key):
            return key
    return ""


KIS_ETF_NAME_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "KBSTAR",
    "SOL",
    "RISE",
    "HANARO",
    "ARIRANG",
    "KOSEF",
    "TIMEFOLIO",
    "PLUS",
    "KINDEX",
    "TREX",
    "FOCUS",
)


def _metadata_value(
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
    key: str,
) -> str:
    return _clean_text(row_metadata.get(key) or source_metadata.get(key), limit=200)


def _strategy_revision_id(
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> str:
    for key in (
        "strategy_revision_id",
        "jue_strategy_revision_id",
        "revision_id",
    ):
        value = _clean_text(row_metadata.get(key) or source_metadata.get(key), limit=120)
        if value:
            return value
    return ""


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return out


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    source = _json_loads(row.get("source_json"))
    if not isinstance(source, dict):
        return {}
    return _metadata_from_source(source)


def _metadata_list(value: Any, *, limit: int = 80) -> list[str]:
    if isinstance(value, list):
        return [
            label
            for item in value
            if (label := _clean_text(item, limit=limit))
        ]
    if isinstance(value, str):
        parsed = _json_loads(value)
        if isinstance(parsed, list):
            return [
                label
                for item in parsed
                if (label := _clean_text(item, limit=limit))
            ]
        label = _clean_text(value, limit=limit)
        return [label] if label else []
    return []


def _metadata_label_counts(
    rows: list[dict[str, Any]],
    key: str,
    *,
    limit: int = 8,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        metadata = _row_metadata(row)
        values = (
            [_clean_text(metadata.get(key), limit=120)]
            if key == "cost_precision_reason"
            else _metadata_list(metadata.get(key), limit=80)
        )
        for value in values:
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
    return {
        label: count
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(int(limit), 0)]
    }


def _validation_pressure_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    direct = metadata.get("validation_pressure")
    if isinstance(direct, dict):
        return direct
    live_authority = metadata.get("live_authority")
    if isinstance(live_authority, dict) and isinstance(
        live_authority.get("validation_pressure"),
        dict,
    ):
        return live_authority["validation_pressure"]
    return {}


def _validation_pressure_label_counts(
    rows: list[dict[str, Any]],
    key: str,
    *,
    limit: int = 8,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        pressure = _validation_pressure_from_metadata(_row_metadata(row))
        if not pressure:
            continue
        raw_value = pressure.get(key)
        values = (
            _metadata_list(raw_value, limit=80)
            if isinstance(raw_value, list)
            else [_clean_text(raw_value, limit=100)]
        )
        for value in values:
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
    return {
        label: count
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(int(limit), 0)]
    }


def _validation_pressure_discipline_action_counts(
    rows: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        pressure = _validation_pressure_from_metadata(_row_metadata(row))
        actions = (
            pressure.get("discipline_actions")
            if isinstance(pressure.get("discipline_actions"), list)
            else []
        )
        for action in actions:
            if not isinstance(action, dict):
                continue
            discipline_id = _clean_text(action.get("id"), limit=80)
            status = _clean_text(action.get("status"), limit=40)
            if not discipline_id:
                continue
            label = f"{discipline_id}:{status}" if status else discipline_id
            counts[label] = counts.get(label, 0) + 1
    return {
        label: count
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(int(limit), 0)]
    }


def _performance_lane(row: dict[str, Any]) -> str:
    venue = _clean_key(row.get("venue"))
    metadata = _row_metadata(row)
    lane = _clean_key(metadata.get("lane"))
    market = _clean_key(metadata.get("market"))
    side = _clean_key(metadata.get("side"))
    horizon = _clean_key(
        metadata.get("horizon")
        or metadata.get("time_horizon")
        or metadata.get("block_horizon")
    )
    if venue == "kis":
        asset_type = _clean_key(metadata.get("asset_type") or metadata.get("asset_class"))
        name = str(metadata.get("name") or metadata.get("symbol_name") or "").upper()
        horizon_alias = {
            "core": "core_etf",
            "coreetf": "core_etf",
            "core_etf": "core_etf",
            "etf": "core_etf",
        }.get(horizon, horizon)
        if (
            asset_type in {"etf", "etn"}
            or horizon_alias == "core_etf"
            or " ETF" in f" {name}"
            or " ETN" in f" {name}"
            or any(name.startswith(prefix) for prefix in KIS_ETF_NAME_PREFIXES)
        ):
            return "core_etf"
        aliases = {
            "short_term": "short",
            "intraday": "short",
            "day": "short",
            "mid_term": "mid",
            "medium": "mid",
            "swing": "mid",
            "long_term": "long",
            "position": "long",
        }
        base_lane = aliases.get(horizon_alias, horizon_alias or "unknown")
        setup = _first_compact_strategy_key(
            metadata.get("strategy_family"),
            metadata.get("entry_setup"),
            metadata.get("setup"),
        )
        if setup and setup not in {base_lane, "all", "unknown"}:
            if setup.startswith(f"{base_lane}:"):
                return setup
            if setup.startswith(f"{base_lane}_"):
                return f"{base_lane}:{setup.removeprefix(f'{base_lane}_')}"
            return f"{base_lane}:{setup}"
        return base_lane
    if venue == "binance":
        if lane in {
            "futures:long",
            "futures_long",
            "futures_long_perp",
            "perp_long",
        }:
            return "futures_long"
        if lane in {
            "futures:short",
            "futures_short",
            "futures_short_perp",
            "perp_short",
        }:
            return "futures_short"
        if lane == "volatile_attack":
            return "volatile_attack"
        if market == "upbit_spot" or lane in {"upbit_spot", "upbit_spot_long"}:
            return "upbit_spot"
        if market == "spot" or lane in {"spot", "spot_long"}:
            return "spot"
        if market in {"futures", "perp", "perpetual"}:
            if side in {"long", "short"}:
                return f"futures_{side}"
            return "futures"
        return lane or market or side or "unknown"
    return lane or horizon or market or "unknown"


def _performance_lanes(row: dict[str, Any]) -> list[str]:
    primary = _performance_lane(row)
    lanes = [primary] if primary else []
    venue = _clean_key(row.get("venue"))
    metadata = _row_metadata(row)
    if venue == "kis" and ":" in primary:
        parent = primary.split(":", 1)[0]
        if parent and parent not in lanes:
            lanes.append(parent)
    if venue == "binance" and primary in {
        "futures_long",
        "futures_short",
        "futures:long",
        "futures:short",
    }:
        if "futures" not in lanes:
            lanes.append("futures")
    if venue == "binance":
        lane = _clean_key(metadata.get("lane"))
        if lane == "volatile_attack" and "volatile_attack" not in lanes:
            lanes.insert(0, "volatile_attack")
    return lanes or ["unknown"]


def _attribution_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        attribution = _clean_key(row.get("attribution")) or "unknown"
        counts[attribution] = counts.get(attribution, 0) + 1
    return counts


def _alpha_conversion_status(
    *,
    alpha_count: int,
    lane_rows: list[dict[str, Any]],
    attribution_counts: dict[str, int],
) -> tuple[str, str]:
    if alpha_count > 0:
        return (
            "alpha_samples_present",
            "closed round-trip samples are already included in lane edge scoring",
        )
    if not lane_rows:
        return "no_blocks", "no lane blocks are available for scoring"
    fill_blocked = (
        attribution_counts.get("unfilled_or_unrealized", 0)
        + attribution_counts.get("operational_failure_pre_fill", 0)
    )
    if fill_blocked > 0:
        return (
            "blocked_by_fill_or_execution_evidence",
            "repair fill evidence, execution errors, or wait for round-trip closes before sizing this lane",
        )
    adopted = (
        attribution_counts.get("adopted_existing_position", 0)
        + attribution_counts.get("adopted_wallet_position", 0)
    )
    if adopted >= len(lane_rows):
        return (
            "risk_management_only_adopted_positions",
            "adopted holdings are tracked for risk but must not prove Jue alpha",
        )
    return (
        "non_alpha_only",
        "lane has blocks but none currently qualify as Jue alpha evidence",
    )


def _max_drawdown_pct(returns_pct: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in returns_pct:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return max_drawdown


def _profit_factor(values: list[float]) -> float:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return 999.0 if gross_profit > 0 else 0.0


LANE_COST_PRECISION_VERIFIED_MIN_PCT = 60.0
LANE_MIN_SAMPLES_TO_SCALE = 10
LANE_REFERENCE_RISK_FRACTION = 0.02
LANE_KELLY_FRACTION = 0.25


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _raw_kelly_fraction(*, win_rate_pct: float, profit_factor: float) -> float:
    if win_rate_pct <= 0.0 or profit_factor <= 0.0:
        return 0.0
    win_probability = _clamp_float(win_rate_pct / 100.0, 0.0, 1.0)
    loss_probability = max(1.0 - win_probability, 0.0)
    if win_probability <= 0.0 or loss_probability <= 0.0:
        return 0.0
    payoff_ratio = profit_factor * loss_probability / win_probability
    if payoff_ratio <= 0.0:
        return 0.0
    return max(win_probability - (loss_probability / payoff_ratio), 0.0)


def _estimated_risk_of_ruin_pct(
    *,
    alpha_count: int,
    expectancy_pct: float,
    win_rate_pct: float,
    profit_factor: float,
    max_drawdown_pct: float,
    cost_drag_pct: float,
    cost_precision_verified_rate: float,
) -> float:
    if alpha_count <= 0:
        return 95.0
    sample_score = _clamp_float(alpha_count / LANE_MIN_SAMPLES_TO_SCALE, 0.0, 1.0)
    risk = 65.0
    risk -= sample_score * 18.0
    risk -= min(max(win_rate_pct - 45.0, 0.0) * 0.7, 16.0)
    risk -= min(max(profit_factor - 1.0, 0.0) * 18.0, 24.0)
    risk -= min(max(expectancy_pct, 0.0) * 10.0, 14.0)
    risk += max(45.0 - win_rate_pct, 0.0) * 0.8
    risk += max(1.0 - profit_factor, 0.0) * 22.0
    risk += abs(min(max_drawdown_pct, 0.0)) * 2.0
    risk += max(cost_drag_pct - 35.0, 0.0) * 0.3
    risk += max(LANE_COST_PRECISION_VERIFIED_MIN_PCT - cost_precision_verified_rate, 0.0) * 0.25
    return _clamp_float(risk, 1.0, 95.0)


def _entry_quality_summary(alpha_rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [
        _safe_float(row.get("entry_quality_score"))
        for row in alpha_rows
        if _safe_float(row.get("entry_quality_score")) > 0.0
    ]
    label_counts: dict[str, int] = {}
    bad_label_counts: dict[str, int] = {}
    good_label_counts: dict[str, int] = {}
    bad_sample_count = 0
    for row in alpha_rows:
        label = _clean_text(row.get("entry_quality_label"), limit=80)
        score = _safe_float(row.get("entry_quality_score"))
        is_bad = bool(
            score > 0.0
            and (score < 55.0 or _contains_label(label, BAD_ENTRY_QUALITY_LABELS))
        )
        if is_bad:
            bad_sample_count += 1
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1
            if is_bad:
                bad_label_counts[label] = bad_label_counts.get(label, 0) + 1
            if score >= 70.0 or _contains_label(label, GOOD_ENTRY_QUALITY_LABELS):
                good_label_counts[label] = good_label_counts.get(label, 0) + 1

    def compact_counts(counts: dict[str, int], *, limit: int = 8) -> dict[str, int]:
        return {
            key: count
            for key, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:limit]
            if count > 0
        }

    def dominant_label(counts: dict[str, int]) -> str:
        if not counts:
            return ""
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    sample_count = len(scores)
    average = sum(scores) / sample_count if sample_count else 0.0
    bad_count = bad_sample_count
    bad_rate = bad_count / sample_count * 100.0 if sample_count else 0.0
    return {
        "entry_quality_sample_count": sample_count,
        "avg_entry_quality_score": average,
        "bad_entry_quality_rate_pct": bad_rate,
        "entry_quality_label_counts": compact_counts(label_counts),
        "bad_entry_quality_label_counts": compact_counts(bad_label_counts),
        "good_entry_quality_label_counts": compact_counts(good_label_counts),
        "dominant_bad_entry_quality_label": dominant_label(bad_label_counts),
        "dominant_good_entry_quality_label": dominant_label(good_label_counts),
        "scale_blocked_by_entry_quality": bool(
            sample_count >= 3
            and (average < 55.0 or bad_rate >= 50.0)
        ),
    }


def _validation_repair_enforcement_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    adjustment_reason_counts: dict[str, int] = {}
    budget_multipliers: list[float] = []
    enforced_count = 0
    scale_up_blocked_count = 0
    waiting_entry_required_count = 0
    rejected_count = 0
    for row in rows:
        metadata = _row_metadata(row)
        enforcement = (
            metadata.get("validation_repair_enforcement")
            if isinstance(metadata.get("validation_repair_enforcement"), dict)
            else {}
        )
        if not enforcement:
            continue
        enforced_count += 1
        if bool(enforcement.get("scale_up_blocked")):
            scale_up_blocked_count += 1
        if bool(enforcement.get("waiting_entry_required")):
            waiting_entry_required_count += 1
        if bool(enforcement.get("rejected")):
            rejected_count += 1
        multiplier = _safe_float(enforcement.get("budget_multiplier"))
        if multiplier > 0:
            budget_multipliers.append(multiplier)
        for action_id in list(enforcement.get("repair_action_ids") or [])[:8]:
            action = _clean_text(action_id, limit=160)
            if action:
                action_counts[action] = action_counts.get(action, 0) + 1
        for adjustment in list(enforcement.get("adjustments") or [])[:8]:
            if not isinstance(adjustment, dict):
                continue
            reason = _clean_text(adjustment.get("reason"), limit=160)
            if reason:
                adjustment_reason_counts[reason] = (
                    adjustment_reason_counts.get(reason, 0) + 1
                )
    avg_budget_multiplier = (
        sum(budget_multipliers) / len(budget_multipliers)
        if budget_multipliers
        else 0.0
    )
    return {
        "validation_repair_enforced_count": enforced_count,
        "validation_repair_scale_up_blocked_count": scale_up_blocked_count,
        "validation_repair_waiting_entry_count": waiting_entry_required_count,
        "validation_repair_rejected_count": rejected_count,
        "validation_repair_avg_budget_multiplier": avg_budget_multiplier,
        "validation_repair_action_counts": dict(
            sorted(action_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ),
        "validation_repair_adjustment_reason_counts": dict(
            sorted(
                adjustment_reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        ),
    }


def _lane_risk_profile(
    *,
    alpha_count: int,
    expectancy_pct: float,
    win_rate_pct: float,
    profit_factor: float,
    max_drawdown_pct: float,
    recovery_factor: float,
    cost_verified_alpha_count: int,
    cost_verified_alpha_net_pnl: float,
    cost_drag_pct: float,
    cost_precision_verified_rate: float,
    cost_precision_sample_count: int,
    quality_hint: str,
    entry_quality: dict[str, Any],
) -> dict[str, Any]:
    sample_confidence = _clamp_float(
        alpha_count / LANE_MIN_SAMPLES_TO_SCALE,
        0.0,
        1.0,
    )
    cost_confidence = (
        _clamp_float(cost_precision_verified_rate / 100.0, 0.0, 1.0)
        if cost_precision_sample_count > 0
        else 0.0
    )
    entry_sample_count = int(entry_quality.get("entry_quality_sample_count") or 0)
    entry_confidence = (
        _clamp_float(_safe_float(entry_quality.get("avg_entry_quality_score")) / 100.0, 0.0, 1.0)
        if entry_sample_count > 0
        else 0.5
    )
    if max_drawdown_pct <= -10.0:
        drawdown_confidence = 0.25
    elif max_drawdown_pct <= -7.0:
        drawdown_confidence = 0.5
    elif max_drawdown_pct <= -4.0:
        drawdown_confidence = 0.75
    else:
        drawdown_confidence = 1.0
    lane_confidence = (
        sample_confidence * 0.4
        + cost_confidence * 0.25
        + entry_confidence * 0.2
        + drawdown_confidence * 0.15
    )
    raw_kelly = _raw_kelly_fraction(
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
    )
    fractional_kelly = raw_kelly * LANE_KELLY_FRACTION
    if fractional_kelly > 0:
        kelly_cap = min(fractional_kelly / LANE_REFERENCE_RISK_FRACTION, 1.25)
    else:
        kelly_cap = 0.25
    if max_drawdown_pct <= -7.0:
        drawdown_cap = 0.5
    elif max_drawdown_pct <= -4.0:
        drawdown_cap = 0.75
    else:
        drawdown_cap = 1.0
    risk_of_ruin = _estimated_risk_of_ruin_pct(
        alpha_count=alpha_count,
        expectancy_pct=expectancy_pct,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
        cost_drag_pct=cost_drag_pct,
        cost_precision_verified_rate=cost_precision_verified_rate,
    )
    if risk_of_ruin >= 20.0:
        ruin_cap = 0.25
    elif risk_of_ruin >= 10.0:
        ruin_cap = 0.5
    elif risk_of_ruin >= 5.0:
        ruin_cap = 0.75
    else:
        ruin_cap = 1.0
    if alpha_count >= 3 and recovery_factor <= 0.0:
        recovery_cap = 0.25
    elif alpha_count >= 3 and recovery_factor < 0.5:
        recovery_cap = 0.5
    elif alpha_count >= 3 and recovery_factor < 1.0:
        recovery_cap = 0.75
    else:
        recovery_cap = 1.0
    if quality_hint == "scale_candidate":
        quality_cap = 1.25
    elif quality_hint == "qualified":
        quality_cap = 1.0
    elif quality_hint == "weak_review":
        quality_cap = 0.25
    else:
        quality_cap = 0.5
    sample_cap = 1.0 if alpha_count >= LANE_MIN_SAMPLES_TO_SCALE else max(sample_confidence, 0.25)
    cost_precision_cap = (
        0.5
        if alpha_count >= 3
        and cost_precision_sample_count > 0
        and cost_precision_verified_rate < LANE_COST_PRECISION_VERIFIED_MIN_PCT
        else 1.0
    )
    verified_edge_net_weak = bool(
        cost_verified_alpha_count >= LANE_MIN_SAMPLES_TO_SCALE
        and cost_verified_alpha_net_pnl <= 0.0
    )
    verified_edge_net_cap = 0.25 if verified_edge_net_weak else 1.0
    entry_quality_cap = (
        0.5
        if bool(entry_quality.get("scale_blocked_by_entry_quality"))
        else 1.0
    )
    risk_budget_multiplier = min(
        quality_cap,
        kelly_cap,
        drawdown_cap,
        recovery_cap,
        ruin_cap,
        sample_cap,
        max(lane_confidence, 0.25),
        cost_precision_cap,
        verified_edge_net_cap,
        entry_quality_cap,
    )
    recommended_risk = LANE_REFERENCE_RISK_FRACTION * risk_budget_multiplier
    return {
        "risk_model_status": "estimated_from_live_lane_metrics",
        "lane_confidence_score": round(lane_confidence, 6),
        "raw_kelly_fraction": round(raw_kelly, 8),
        "raw_fractional_kelly_fraction": round(fractional_kelly, 8),
        "risk_of_ruin_pct": round(risk_of_ruin, 6),
        "recommended_risk_fraction": round(recommended_risk, 8),
        "max_risk_cap_fraction": round(
            LANE_REFERENCE_RISK_FRACTION * quality_cap,
            8,
        ),
        "risk_budget_multiplier": round(risk_budget_multiplier, 6),
        "recovery_factor_cap_multiplier": round(recovery_cap, 6),
        "verified_edge_net_cap_multiplier": round(verified_edge_net_cap, 6),
        "scale_blocked_by_verified_edge_net_pnl": verified_edge_net_weak,
        "sample_confidence": round(sample_confidence, 6),
        "cost_confidence": round(cost_confidence, 6),
        "entry_quality_confidence": round(entry_confidence, 6),
    }


def _lane_quality_hint(
    *,
    alpha_count: int,
    expectancy_pct: float,
    win_rate_pct: float,
    profit_factor: float,
    max_drawdown_pct: float,
    recovery_factor: float,
    cost_drag_pct: float,
    cost_precision_verified_rate: float,
) -> tuple[str, str]:
    if alpha_count <= 0:
        return "no_alpha_samples", "observe_until_closed_block_samples_exist"
    if alpha_count < 3:
        return "sample_building", "small_probe_or_shadow_until_sample_builds"
    if cost_precision_verified_rate < LANE_COST_PRECISION_VERIFIED_MIN_PCT:
        return "weak_review", "cost_evidence_repair_waiting_entry"
    if (
        expectancy_pct > 0.4
        and win_rate_pct >= 52.0
        and profit_factor >= 1.5
        and recovery_factor >= 1.0
        and max_drawdown_pct >= -7.0
        and cost_drag_pct <= 35.0
    ):
        return "scale_candidate", "eligible_to_review_for_sizing_increase"
    if (
        expectancy_pct > 0.0
        and win_rate_pct >= 48.0
        and profit_factor >= 1.05
        and recovery_factor >= 0.5
        and max_drawdown_pct >= -10.0
        and cost_drag_pct <= 55.0
    ):
        return "qualified", "normal_or_selective_press"
    return "weak_review", "observe_small_probe_or_waiting_entry"


BAD_ENTRY_QUALITY_LABELS = {
    "chase",
    "extended",
    "extended_momentum",
    "failed_breakout",
    "high_chase",
    "late_chase",
    "regime_mismatch",
    "regime_misaligned",
    "overextended",
    "고점",
    "고점 추격",
    "레짐 불일치",
    "추격",
}

GOOD_ENTRY_QUALITY_LABELS = {
    "actionable_now",
    "confirmed_pullback",
    "low_risk_pullback",
    "pullback",
    "pullback_reclaim",
    "wait_pullback",
    "wait_for_price",
    "undervalued_pullback",
    "value_pullback",
    "눌림",
    "저점",
}

EXTENDED_ENTRY_LOCATION_LABELS = {
    "20d_high",
    "24h_high",
    "52w_high",
    "breakout_extended",
    "near_20d_high",
    "near_24h_high",
    "near_52w_high",
    "near_high",
    "upper_band",
    "고점",
    "상단",
    "신고가",
}

PULLBACK_ENTRY_LOCATION_LABELS = {
    "discount",
    "low_risk",
    "near_support",
    "pullback",
    "reclaim",
    "support",
    "undervalued",
    "눌림",
    "저점",
    "지지",
    "할인",
}

HIGH_CHASE_RISK_LABELS = {
    "elevated",
    "high",
    "very_high",
    "높음",
    "위험",
}

POSITIVE_CONFLUENCE_LABELS = {
    "aligned",
    "discount",
    "favorable",
    "improving",
    "positive",
    "recovery",
    "risk_on",
    "undervalued",
    "우호",
    "저평가",
    "정합",
    "회복",
}
NEGATIVE_REGIME_LABELS = {
    "adverse",
    "bear",
    "bearish",
    "choppy",
    "misaligned",
    "mismatch",
    "negative",
    "risk_off",
    "unfavorable",
    "weak",
    "레짐 불일치",
    "비우호",
    "약세",
    "위험회피",
    "하락",
}
RISK_OFF_LABELS = {
    "bear",
    "bearish",
    "downtrend",
    "risk_off",
    "weak",
    "약세",
    "위험회피",
    "하락",
}
RISK_ON_LABELS = {
    "bull",
    "bullish",
    "risk_on",
    "uptrend",
    "강세",
    "상승",
}


def _metadata_signal(
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
    key: str,
) -> Any:
    if row_metadata.get(key) not in (None, ""):
        return row_metadata.get(key)
    if source_metadata.get(key) not in (None, ""):
        return source_metadata.get(key)
    row_gate = (
        row_metadata.get("entry_quality_gate")
        if isinstance(row_metadata.get("entry_quality_gate"), dict)
        else {}
    )
    source_gate = (
        source_metadata.get("entry_quality_gate")
        if isinstance(source_metadata.get("entry_quality_gate"), dict)
        else {}
    )
    if row_gate.get(key) not in (None, ""):
        return row_gate.get(key)
    return source_gate.get(key)


def _metadata_bool_signal(
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
    key: str,
) -> bool:
    value = _metadata_signal(row_metadata, source_metadata, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _negative_bool_or_label(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    text = str(value or "").strip().lower()
    return text in {
        "0",
        "false",
        "no",
        "n",
        "off",
        "mismatch",
        "misaligned",
        "negative",
        "adverse",
        "unfavorable",
        "불일치",
        "비우호",
    }


def _contains_label(value: Any, tokens: set[str]) -> bool:
    text = str(value or "").strip().lower()
    compact = text.replace("-", "_").replace(" ", "_")
    return any(token in text or token in compact for token in tokens if token)


def _entry_quality_payload(
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    raw_quality_label = (
        row_metadata.get("entry_quality")
        or row_metadata.get("entry_setup")
        or source_metadata.get("entry_quality")
        or source_metadata.get("entry_setup")
    )
    raw_style_label = (
        row_metadata.get("entry_style")
        or source_metadata.get("entry_style")
    )
    raw_label = raw_quality_label or raw_style_label
    label = _clean_text(raw_label, limit=120)
    label_from_style_only = not raw_quality_label and bool(raw_style_label)
    score = _safe_float(
        row_metadata.get("entry_quality_score")
        or source_metadata.get("entry_quality_score")
    )
    side = _clean_key(_metadata_signal(row_metadata, source_metadata, "side"))
    chase_risk = _metadata_signal(row_metadata, source_metadata, "chase_risk")
    price_location = _metadata_signal(row_metadata, source_metadata, "price_location")
    technical_setup = _metadata_signal(row_metadata, source_metadata, "technical_setup")
    valuation_label = _metadata_signal(row_metadata, source_metadata, "valuation_label")
    regime_alignment = _metadata_signal(
        row_metadata,
        source_metadata,
        "regime_alignment",
    )
    market_regime = _metadata_signal(row_metadata, source_metadata, "market_regime")
    regime_match = (
        _metadata_signal(row_metadata, source_metadata, "regime_match")
        or _metadata_signal(row_metadata, source_metadata, "regime_matched")
    )
    supply_recovery = _metadata_signal(row_metadata, source_metadata, "supply_recovery")
    flow_recovery = _metadata_signal(row_metadata, source_metadata, "flow_recovery")
    pullback_confirmed = _metadata_bool_signal(
        row_metadata,
        source_metadata,
        "pullback_confirmed",
    )
    high_chase_pressure = any(
        (
            _contains_label(label, BAD_ENTRY_QUALITY_LABELS),
            _contains_label(chase_risk, HIGH_CHASE_RISK_LABELS),
            _contains_label(price_location, EXTENDED_ENTRY_LOCATION_LABELS),
            _contains_label(technical_setup, BAD_ENTRY_QUALITY_LABELS),
        )
    )
    is_short = side in {"short", "sell_short", "futures_short"} or _contains_label(
        label,
        {"short", "숏"},
    )
    regime_mismatch = any(
        (
            _negative_bool_or_label(regime_match),
            _contains_label(regime_alignment, NEGATIVE_REGIME_LABELS),
            (not is_short and _contains_label(market_regime, RISK_OFF_LABELS)),
            (is_short and _contains_label(market_regime, RISK_ON_LABELS)),
        )
    )
    price_relief = any(
        (
            pullback_confirmed,
            _contains_label(label, GOOD_ENTRY_QUALITY_LABELS),
            _contains_label(price_location, PULLBACK_ENTRY_LOCATION_LABELS),
        )
    )
    confluence_values: tuple[Any, ...] = (
        valuation_label,
        regime_alignment,
        supply_recovery,
        flow_recovery,
    )
    if not regime_mismatch:
        confluence_values = (*confluence_values, market_regime)
    confluence = any(
        _contains_label(value, POSITIVE_CONFLUENCE_LABELS)
        for value in confluence_values
    )
    if score <= 0:
        if high_chase_pressure and not price_relief:
            score = 35.0
            if not label:
                label = "high_chase_without_pullback"
        elif high_chase_pressure and price_relief and regime_mismatch:
            score = 50.0
            if not label or label_from_style_only:
                label = "chase_risk_regime_mismatch_waiting_entry"
        elif regime_mismatch and not price_relief:
            score = 45.0
            if not label or label_from_style_only:
                label = "regime_mismatch_without_price_relief"
        elif high_chase_pressure and price_relief:
            score = 60.0
            if not label:
                label = "chase_risk_waiting_entry"
        elif price_relief and regime_mismatch:
            score = 58.0
            if not label or label_from_style_only:
                label = "regime_mismatch_waiting_entry"
        elif price_relief:
            score = 80.0
            if not label:
                label = "low_risk_pullback"
        elif confluence:
            score = 65.0
            if not label:
                label = "confluence_without_price_relief"
        elif label and "conditional" in label.strip().lower():
            score = 60.0
    return {
        "entry_quality_label": label,
        "entry_quality_score": max(min(score, 100.0), 0.0),
    }


def _cost_precision(
    *,
    cost_model_status: str,
    cost_total: float,
    filled: bool,
) -> str:
    normalized = cost_model_status.strip().lower()
    if "partial" in normalized or "unconverted" in normalized:
        return "partial"
    if (
        ("explicit" in normalized or "recorded" in normalized)
        and "estimated" in normalized
    ):
        return "hybrid"
    if "estimated" in normalized:
        return "estimated"
    if normalized == "recorded" or normalized.startswith("recorded_"):
        return "recorded"
    if normalized == "explicit" or normalized.startswith("explicit_"):
        return "recorded"
    if normalized in {"missing", "unknown", "error"}:
        return normalized
    if cost_total > 0:
        return "unverified_cost"
    if not filled:
        return "missing_or_unfilled"
    return "missing"


_COST_COMPONENT_ALIASES = {
    "fee": "fees",
    "fees": "fees",
    "commission": "fees",
    "commissions": "fees",
    "tax": "taxes",
    "taxes": "taxes",
    "funding": "funding",
    "funding_fee": "funding",
    "slippage": "slippage",
    "spread": "spread",
    "spread_cost": "spread",
    "book_spread": "spread",
}
_CANONICAL_COST_COMPONENTS = {
    "fees",
    "taxes",
    "funding",
    "slippage",
    "spread",
}
_COST_COMPONENT_DECLARATION_KEYS = (
    "recorded_cost_components",
    "verified_cost_components",
    "present_cost_components",
    "cost_components_present",
    "zero_cost_components",
    "explicit_zero_cost_components",
)


def _cost_component_label(value: Any) -> str:
    key = _clean_key(value)
    component = _COST_COMPONENT_ALIASES.get(key, key)
    return component if component in _CANONICAL_COST_COMPONENTS else ""


def _is_absent_component_marker(value: Any) -> bool:
    return value is None or value == "" or value is False


def _declared_cost_components(value: Any) -> set[str]:
    raw_value = value
    if isinstance(value, str):
        parsed = _json_loads(value)
        raw_value = parsed if parsed else value
    present: set[str] = set()
    if isinstance(raw_value, dict):
        for raw_key, raw_marker in raw_value.items():
            if _is_absent_component_marker(raw_marker):
                continue
            if component := _cost_component_label(raw_key):
                present.add(component)
        return present
    if isinstance(raw_value, (list, tuple, set)):
        for item in raw_value:
            if isinstance(item, dict):
                marker = item.get(
                    "present",
                    item.get("recorded", item.get("verified", True)),
                )
                if _is_absent_component_marker(marker):
                    continue
                label = (
                    item.get("component")
                    or item.get("name")
                    or item.get("key")
                    or item.get("id")
                )
                if component := _cost_component_label(label):
                    present.add(component)
                continue
            if component := _cost_component_label(item):
                present.add(component)
        return present
    if isinstance(raw_value, str):
        normalized = (
            raw_value.replace("\n", ",")
            .replace(";", ",")
            .replace("|", ",")
        )
        pieces = normalized.split(",") if "," in normalized else normalized.split()
        for piece in pieces:
            if component := _cost_component_label(piece):
                present.add(component)
    return present


def _cost_component_presence(
    row: Any,
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> set[str]:
    present: set[str] = set()
    for metadata in (source_metadata, row_metadata):
        components = (
            metadata.get("cost_components")
            or metadata.get("cost_breakdown")
            or metadata.get("cost_component_sources")
            or metadata.get("component_sources")
        )
        components = _json_loads(components)
        if isinstance(components, dict):
            for raw_key, raw_value in components.items():
                if raw_value in (None, ""):
                    continue
                key = _COST_COMPONENT_ALIASES.get(
                    _clean_key(raw_key),
                    _clean_key(raw_key),
                )
                if key:
                    present.add(key)
        for declaration_key in _COST_COMPONENT_DECLARATION_KEYS:
            present.update(_declared_cost_components(metadata.get(declaration_key)))
        for raw_key, component in _COST_COMPONENT_ALIASES.items():
            if raw_key in metadata and metadata.get(raw_key) not in (None, ""):
                present.add(component)
    for column in ("fees", "taxes", "funding", "slippage", "spread"):
        if _safe_float(getattr(row, column, 0.0)) > 0:
            present.add(column)
    return present


def _required_cost_components(
    row: Any,
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> set[str]:
    venue = _clean_key(getattr(row, "venue", ""))
    market = _clean_key(row_metadata.get("market") or source_metadata.get("market"))
    lane = _clean_key(row_metadata.get("lane") or source_metadata.get("lane"))
    side = _clean_key(row_metadata.get("side") or source_metadata.get("side"))
    if venue in {"binance", "upbit"}:
        is_futures = bool(
            market in {"futures", "perp", "perpetual"}
            or lane in {"futures", "futures_long", "futures_short", "volatile_attack"}
            or lane.startswith("futures")
            or side == "short"
        )
        if is_futures:
            return {"fees", "funding", "spread", "slippage"}
        return {"fees", "spread", "slippage"}
    if venue == "kis":
        return {"fees", "taxes", "spread", "slippage"}
    return {"fees"}


def _downgrade_cost_precision_for_missing_components(
    precision: str,
    row: Any,
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> str:
    if precision != "recorded":
        return precision
    missing = _required_cost_components(
        row,
        row_metadata,
        source_metadata,
    ) - _cost_component_presence(row, row_metadata, source_metadata)
    if missing:
        return "partial"
    return precision


def _cost_component_audit(
    row: Any,
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    required = _required_cost_components(row, row_metadata, source_metadata)
    present = _cost_component_presence(row, row_metadata, source_metadata)
    missing = required - present
    return {
        "required_cost_components": sorted(required),
        "present_cost_components": sorted(present),
        "missing_cost_components": sorted(missing),
    }


def _source_with_cost_audit(
    source: dict[str, Any] | None,
    *,
    audit: dict[str, Any],
    precision_before_audit: str,
    precision_after_audit: str,
) -> dict[str, Any]:
    stored = _json_loads(json.dumps(source or {}, ensure_ascii=False))
    if not isinstance(stored, dict):
        stored = {}
    metadata = stored.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(audit)
    if precision_before_audit != precision_after_audit:
        metadata["cost_precision_reason"] = (
            "recorded_cost_missing_required_components"
        )
        metadata["cost_precision_before_audit"] = precision_before_audit
        metadata["cost_precision_after_audit"] = precision_after_audit
    elif not metadata.get("cost_precision_reason"):
        metadata["cost_precision_reason"] = "cost_components_audited"
    stored["metadata"] = metadata
    return stored


def _source_with_performance_context(
    source: dict[str, Any],
    *,
    row: "BlockPerformanceInput",
    row_metadata: dict[str, Any],
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    stored = source if isinstance(source, dict) else {}
    metadata = stored.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    for key in (
        "market",
        "side",
        "horizon",
        "time_horizon",
        "block_horizon",
        "asset_type",
        "asset_class",
        "name",
        "symbol_name",
        "entry_style",
        "entry_setup",
        "strategy_family",
    ):
        value = metadata.get(key)
        if value in (None, "", [], {}):
            value = row_metadata.get(key)
        if value in (None, "", [], {}):
            value = source_metadata.get(key)
        if value not in (None, "", [], {}):
            metadata[key] = value

    if metadata.get("lane") in (None, "", [], {}):
        pseudo_row = {
            "venue": row.venue,
            "source_json": json.dumps({"metadata": metadata}, ensure_ascii=False),
        }
        lane = _performance_lane(pseudo_row)
        if lane and lane != "unknown":
            metadata["lane"] = lane

    stored["metadata"] = metadata
    for key in ("lane", "market", "side", "horizon", "asset_type", "asset_class"):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            stored[key] = value
    stored.setdefault("venue", row.venue)
    stored.setdefault("symbol", row.symbol)
    stored.setdefault("block_id", row.block_id)
    return stored


def _compact_source_scalar(value: Any, *, limit: int = 1000) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[: max(limit - 16, 1)] + "...[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean_text(value, limit=limit)


def _compact_source_list(
    value: Any,
    *,
    item_limit: int = 24,
    depth: int = 0,
) -> list[Any]:
    if not isinstance(value, list):
        return []
    compacted: list[Any] = []
    for item in value[: max(int(item_limit), 0)]:
        compacted.append(_compact_source_value(item, depth=depth + 1))
    if len(value) > item_limit:
        compacted.append({"truncated_count": len(value) - item_limit})
    return compacted


def _compact_validation_pressure(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in (
        "status",
        "severity",
        "entry_posture",
        "sizing_posture",
        "readiness",
        "risk_governor_action",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _compact_source_scalar(value.get(key), limit=160)
    for key in (
        "failed",
        "fail_ids",
        "warn_ids",
        "missing_ids",
        "failed_disciplines",
        "missing_disciplines",
        "warnings",
        "reasons",
    ):
        raw = value.get(key)
        if isinstance(raw, list) and raw:
            out[key] = [
                _clean_text(item, limit=120)
                for item in raw[:20]
                if _clean_text(item, limit=120)
            ]
    actions = value.get("discipline_actions")
    if isinstance(actions, list) and actions:
        out["discipline_actions"] = [
            {
                compact_key: _compact_source_scalar(action.get(compact_key), limit=120)
                for compact_key in ("id", "status", "action")
                if isinstance(action, dict)
                and action.get(compact_key) not in (None, "", [], {})
            }
            for action in actions[:20]
            if isinstance(action, dict)
        ]
    return out


def _compact_live_authority(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in (
        "status",
        "live_grade",
        "allow_scale_up",
        "max_budget_multiplier",
        "validation_gate_status",
        "validation_readiness",
        "risk_governor_action",
        "risk_governor_source",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _compact_source_scalar(value.get(key), limit=160)
    pressure = _compact_validation_pressure(value.get("validation_pressure"))
    if pressure:
        out["validation_pressure"] = pressure
    lane_authority = value.get("lane_authority")
    if isinstance(lane_authority, dict):
        lane_summary: dict[str, Any] = {}
        for key in (
            "version",
            "global_scale_up_allowed",
            "max_budget_multiplier",
            "validation_gate_status",
            "weak_lane_count",
            "insufficient_lane_count",
            "blocked_lane_count",
            "lane_action_count",
        ):
            if lane_authority.get(key) not in (None, "", [], {}):
                lane_summary[key] = _compact_source_scalar(
                    lane_authority.get(key),
                    limit=160,
                )
        if lane_summary:
            out["lane_authority"] = lane_summary
    return out


def _compact_source_summary_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, list):
            out[f"{clean_key}_count"] = len(item)
        elif isinstance(item, dict):
            out[f"{clean_key}_count"] = len(item)
        else:
            out[clean_key] = _compact_source_scalar(item, limit=240)
    return out


def _compact_validation_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    preserve_list_keys = {
        "required_evidence",
        "required_checks",
        "pass_collection_hooks",
        "pass_current_gaps",
        "pass_criteria",
        "verification_artifacts",
    }
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if item in (None, "", [], {}):
            continue
        if clean_key in preserve_list_keys and isinstance(item, list):
            out[clean_key] = _compact_source_list(item, item_limit=8)
        elif isinstance(item, list):
            out[f"{clean_key}_count"] = len(item)
        elif isinstance(item, dict):
            out[f"{clean_key}_count"] = len(item)
        else:
            out[clean_key] = _compact_source_scalar(item, limit=240)
    return out


def _compact_entry_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if item in (None, "", [], {}):
            continue
        if clean_key == "lane_scorecards" and isinstance(item, dict):
            out["lane_scorecard_count"] = len(item)
        elif clean_key in {"cooldown_symbols", "cooldown_lanes"} and isinstance(
            item,
            dict,
        ):
            out[f"{clean_key}_count"] = len(item)
        elif isinstance(item, list):
            out[clean_key] = _compact_source_list(item, item_limit=8)
        elif isinstance(item, dict):
            scalar_summary = {
                str(child_key): _compact_source_scalar(child_value, limit=160)
                for child_key, child_value in item.items()
                if isinstance(child_value, (str, int, float, bool))
                or child_value is None
            }
            if scalar_summary:
                out[clean_key] = scalar_summary
            else:
                out[f"{clean_key}_count"] = len(item)
        else:
            out[clean_key] = _compact_source_scalar(item, limit=240)
    return out


def _compact_entry_gate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if item in (None, "", [], {}):
            continue
        if clean_key in {"policy", "effective_policy"}:
            compacted = _compact_entry_policy(item)
        elif clean_key in {"entry_quality", "candidate"}:
            compacted = _compact_source_value(item, depth=1)
        elif isinstance(item, list):
            compacted = _compact_source_list(item, item_limit=8)
        elif isinstance(item, dict):
            compacted = _compact_source_summary_mapping(item)
        else:
            compacted = _compact_source_scalar(item, limit=240)
        if compacted not in (None, "", [], {}):
            out[clean_key] = compacted
    return out


def _compact_price_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if item in (None, "", [], {}):
            continue
        if clean_key == "pattern_inputs":
            continue
        if clean_key in {
            "market_inputs",
            "technical_inputs",
            "derivatives_inputs",
            "sizing_inputs",
            "volatile_attack_context",
            "pattern_live_crosscheck",
        }:
            compacted = _compact_source_value(item, depth=1)
        elif isinstance(item, list):
            compacted = _compact_source_list(item, item_limit=8)
        elif isinstance(item, dict):
            compacted = _compact_source_summary_mapping(item)
        else:
            compacted = _compact_source_scalar(item, limit=240)
        if compacted not in (None, "", [], {}):
            out[clean_key] = compacted
    return out


def _compact_pattern_prior(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = str(key)
        if item in (None, "", [], {}):
            continue
        if clean_key == "walk_forward_quality" and isinstance(item, dict):
            quality: dict[str, Any] = {}
            for q_key, q_value in item.items():
                if q_key == "windows" and isinstance(q_value, list):
                    quality["window_count"] = len(q_value)
                elif isinstance(q_value, (str, int, float, bool)) or q_value is None:
                    quality[str(q_key)] = _compact_source_scalar(q_value, limit=160)
            if quality:
                out[clean_key] = quality
        elif isinstance(item, dict):
            out[clean_key] = _compact_source_summary_mapping(item)
        elif isinstance(item, list):
            out[f"{clean_key}_count"] = len(item)
        else:
            out[clean_key] = _compact_source_scalar(item, limit=240)
    return out


def _compact_source_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, dict):
        if depth >= 3:
            return {
                str(key): _compact_source_scalar(item, limit=240)
                for key, item in value.items()
                if isinstance(item, (str, int, float, bool)) or item is None
            }
        out: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key)
            if clean_key in {"metadata_json", "raw_prompt", "raw_response"}:
                continue
            if item in (None, "", [], {}):
                continue
            if clean_key == "live_authority":
                compacted = _compact_live_authority(item)
            elif clean_key == "validation_pressure":
                compacted = _compact_validation_pressure(item)
            elif clean_key == "validation_evidence":
                compacted = _compact_validation_evidence(item)
            elif clean_key == "validation_repair":
                compacted = _compact_source_summary_mapping(item)
            elif clean_key in {"policy_effect_audit", "policy_effect_enforcement"}:
                compacted = _compact_source_summary_mapping(item)
            elif clean_key == "entry_gate":
                compacted = _compact_entry_gate(item)
            elif clean_key in {"calculated_price_plan", "executable_price_plan"}:
                compacted = _compact_price_plan(item)
            elif clean_key == "pattern_prior":
                compacted = _compact_pattern_prior(item)
            elif clean_key == "lane_authority_gate":
                compacted = _compact_source_summary_mapping(item)
            elif clean_key == "policy_rule_impacts":
                if isinstance(item, list):
                    out["policy_rule_impact_count"] = len(item)
                continue
            elif isinstance(item, dict):
                compacted = _compact_source_value(item, depth=depth + 1)
            elif isinstance(item, list):
                compacted = _compact_source_list(item, depth=depth + 1)
            else:
                compacted = _compact_source_scalar(item)
            if compacted not in (None, "", [], {}):
                out[clean_key] = compacted
        return out
    if isinstance(value, list):
        return _compact_source_list(value, depth=depth)
    return _compact_source_scalar(value)


def _compact_performance_block(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep_keys = (
        "block_id",
        "symbol",
        "name",
        "status",
        "created_by",
        "manager_run_id",
        "created_at",
        "updated_at",
        "opened_at",
        "closed_at",
        "entry_price",
        "exit_price",
        "target_price",
        "stop_price",
        "qty_initial",
        "qty_open",
        "thesis",
        "llm_reason",
        "risk_note",
    )
    out: dict[str, Any] = {}
    for key in keep_keys:
        if value.get(key) not in (None, "", [], {}):
            limit = 1200 if key in {"thesis", "llm_reason", "risk_note"} else 240
            out[key] = _compact_source_scalar(value.get(key), limit=limit)
    return out


def _compact_performance_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    out: dict[str, Any] = {}
    block = _compact_performance_block(source.get("block"))
    if block:
        out["block"] = block
    metadata = _compact_source_value(source.get("metadata"))
    if isinstance(metadata, dict) and metadata:
        out["metadata"] = metadata
    for key in (
        "lane",
        "market",
        "side",
        "horizon",
        "asset_type",
        "asset_class",
        "venue",
        "symbol",
        "block_id",
    ):
        if source.get(key) not in (None, "", [], {}):
            out[key] = _compact_source_scalar(source.get(key), limit=240)
    out["source_compacted"] = True
    return out


@dataclass(frozen=True, slots=True)
class BlockPerformanceInput:
    venue: str
    block_id: str
    symbol: str
    created_by: str
    status: str
    entry_price: float
    exit_price: float
    qty: float
    fees: float = 0.0
    taxes: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0
    spread: float = 0.0
    filled: bool = False
    error_type: str = ""
    metadata: dict[str, Any] | None = None


def classify_block_attribution(row: BlockPerformanceInput) -> dict[str, Any]:
    created_by = str(row.created_by or "").strip().lower()
    status = str(row.status or "").strip().lower()
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    execution_defect_reason = _clean_key(
        metadata.get("execution_defect_reason")
        or metadata.get("performance_exclusion_reason")
    )

    if bool(metadata.get("execution_defect")) or execution_defect_reason:
        return {
            "attribution": (
                f"execution_defect_{execution_defect_reason}"
                if execution_defect_reason
                else "execution_defect"
            ),
            "include_in_jue_alpha": False,
            "include_in_risk_management": True,
            "include_in_execution_quality": True,
        }

    if created_by in {"existing_position", "wallet_adoption"}:
        return {
            "attribution": (
                "adopted_existing_position"
                if created_by == "existing_position"
                else "adopted_wallet_position"
            ),
            "include_in_jue_alpha": False,
            "include_in_risk_management": True,
            "include_in_execution_quality": False,
        }

    if status == "error" and not bool(row.filled):
        return {
            "attribution": "operational_failure_pre_fill",
            "include_in_jue_alpha": False,
            "include_in_risk_management": False,
            "include_in_execution_quality": True,
        }

    if not bool(row.filled):
        return {
            "attribution": "unfilled_or_unrealized",
            "include_in_jue_alpha": False,
            "include_in_risk_management": True,
            "include_in_execution_quality": True,
        }

    return {
        "attribution": "jue_created_live_or_paper",
        "include_in_jue_alpha": created_by in {"llm", "jue", "manager"},
        "include_in_risk_management": True,
        "include_in_execution_quality": True,
    }


def compute_realized_pnl(row: BlockPerformanceInput) -> dict[str, Any]:
    classification = classify_block_attribution(row)
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    side = str(metadata.get("side") or "long").strip().lower()
    if side == "short":
        gross = (float(row.entry_price) - float(row.exit_price)) * float(row.qty)
    else:
        gross = (float(row.exit_price) - float(row.entry_price)) * float(row.qty)
    cost_total = (
        float(row.fees)
        + float(row.taxes)
        + float(row.funding)
        + float(row.slippage)
        + float(row.spread)
    )
    net = gross - cost_total
    capital = abs(float(row.entry_price) * float(row.qty))
    pnl_pct = (net / capital * 100.0) if capital > 0 else 0.0
    return {
        **classification,
        "venue": str(row.venue),
        "block_id": str(row.block_id),
        "symbol": str(row.symbol),
        "gross_pnl": gross,
        "net_pnl": net,
        "cost_total": cost_total,
        "pnl_pct": pnl_pct,
        "filled": bool(row.filled),
    }


class LivePerformanceRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_block_performance (
                    block_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    attribution TEXT NOT NULL DEFAULT '',
                    include_in_jue_alpha INTEGER NOT NULL DEFAULT 0,
                    include_in_risk_management INTEGER NOT NULL DEFAULT 0,
                    include_in_execution_quality INTEGER NOT NULL DEFAULT 0,
                    gross_pnl REAL NOT NULL DEFAULT 0,
                    net_pnl REAL NOT NULL DEFAULT 0,
                    cost_total REAL NOT NULL DEFAULT 0,
                    pnl_pct REAL NOT NULL DEFAULT 0,
                    entry_price REAL NOT NULL DEFAULT 0,
                    exit_price REAL NOT NULL DEFAULT 0,
                    qty REAL NOT NULL DEFAULT 0,
                    fees REAL NOT NULL DEFAULT 0,
                    taxes REAL NOT NULL DEFAULT 0,
                    funding REAL NOT NULL DEFAULT 0,
                    slippage REAL NOT NULL DEFAULT 0,
                    spread REAL NOT NULL DEFAULT 0,
                    cost_model_status TEXT NOT NULL DEFAULT '',
                    cost_source TEXT NOT NULL DEFAULT '',
                    cost_precision TEXT NOT NULL DEFAULT '',
                    fill_evidence_status TEXT NOT NULL DEFAULT '',
                    entry_price_source TEXT NOT NULL DEFAULT '',
                    exit_price_source TEXT NOT NULL DEFAULT '',
                    entry_quality_label TEXT NOT NULL DEFAULT '',
                    entry_quality_score REAL NOT NULL DEFAULT 0,
                    strategy_revision_id TEXT NOT NULL DEFAULT '',
                    filled INTEGER NOT NULL DEFAULT 0,
                    source_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (venue, block_id)
                );
                CREATE INDEX IF NOT EXISTS idx_live_perf_venue_symbol
                    ON live_block_performance(venue, symbol, computed_at DESC);
                """
            )
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(live_block_performance)")
        }
        for column in (
            "entry_price",
            "exit_price",
            "qty",
            "fees",
            "taxes",
            "funding",
            "slippage",
            "spread",
        ):
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE live_block_performance "
                    f"ADD COLUMN {column} REAL NOT NULL DEFAULT 0"
                )
        for column in (
            "cost_model_status",
            "cost_source",
            "cost_precision",
            "fill_evidence_status",
            "entry_price_source",
            "exit_price_source",
            "entry_quality_label",
        ):
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE live_block_performance "
                    f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        if "entry_quality_score" not in existing:
            conn.execute(
                "ALTER TABLE live_block_performance "
                "ADD COLUMN entry_quality_score REAL NOT NULL DEFAULT 0"
            )
        if "strategy_revision_id" not in existing:
            conn.execute(
                "ALTER TABLE live_block_performance "
                "ADD COLUMN strategy_revision_id TEXT NOT NULL DEFAULT ''"
            )

    def upsert_performance(
        self,
        row: BlockPerformanceInput,
        *,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = compute_realized_pnl(row)
        row_metadata = row.metadata if isinstance(row.metadata, dict) else {}
        source_metadata = _metadata_from_source(source)
        cost_model_status = _metadata_value(
            row_metadata,
            source_metadata,
            "cost_model_status",
        )
        cost_source = _metadata_value(row_metadata, source_metadata, "cost_source")
        fill_evidence_status = _metadata_value(
            row_metadata,
            source_metadata,
            "fill_evidence_status",
        )
        entry_price_source = _metadata_value(
            row_metadata,
            source_metadata,
            "entry_price_source",
        )
        exit_price_source = _metadata_value(
            row_metadata,
            source_metadata,
            "exit_price_source",
        )
        entry_quality = _entry_quality_payload(row_metadata, source_metadata)
        strategy_revision_id = _strategy_revision_id(row_metadata, source_metadata)
        cost_precision_before_audit = _cost_precision(
            cost_model_status=cost_model_status,
            cost_total=float(payload["cost_total"]),
            filled=bool(payload["filled"]),
        )
        cost_component_audit = _cost_component_audit(
            row,
            row_metadata,
            source_metadata,
        )
        cost_precision = _downgrade_cost_precision_for_missing_components(
            cost_precision_before_audit,
            row,
            row_metadata,
            source_metadata,
        )
        stored_source = _source_with_cost_audit(
            source,
            audit=cost_component_audit,
            precision_before_audit=cost_precision_before_audit,
            precision_after_audit=cost_precision,
        )
        stored_source = _source_with_performance_context(
            stored_source,
            row=row,
            row_metadata=row_metadata,
            source_metadata=source_metadata,
        )
        stored_source = _compact_performance_source(stored_source)
        payload.update(
            {
                "cost_model_status": cost_model_status,
                "cost_source": cost_source,
                "cost_precision": cost_precision,
                "cost_precision_before_audit": cost_precision_before_audit,
                "cost_precision_reason": (
                    "recorded_cost_missing_required_components"
                    if cost_precision_before_audit != cost_precision
                    else "cost_components_audited"
                ),
                **cost_component_audit,
                "fill_evidence_status": fill_evidence_status,
                "entry_price_source": entry_price_source,
                "exit_price_source": exit_price_source,
                "entry_quality_label": entry_quality["entry_quality_label"],
                "entry_quality_score": float(entry_quality["entry_quality_score"]),
                "strategy_revision_id": strategy_revision_id,
            }
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_block_performance (
                    block_id, venue, symbol, attribution,
                    include_in_jue_alpha, include_in_risk_management,
                    include_in_execution_quality, gross_pnl, net_pnl,
                    cost_total, pnl_pct, entry_price, exit_price, qty,
                    fees, taxes, funding, slippage, spread,
                    cost_model_status, cost_source, cost_precision,
                    fill_evidence_status, entry_price_source, exit_price_source,
                    entry_quality_label, entry_quality_score,
                    strategy_revision_id, filled, source_json, computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, block_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    attribution=excluded.attribution,
                    include_in_jue_alpha=excluded.include_in_jue_alpha,
                    include_in_risk_management=excluded.include_in_risk_management,
                    include_in_execution_quality=excluded.include_in_execution_quality,
                    gross_pnl=excluded.gross_pnl,
                    net_pnl=excluded.net_pnl,
                    cost_total=excluded.cost_total,
                    pnl_pct=excluded.pnl_pct,
                    entry_price=excluded.entry_price,
                    exit_price=excluded.exit_price,
                    qty=excluded.qty,
                    fees=excluded.fees,
                    taxes=excluded.taxes,
                    funding=excluded.funding,
                    slippage=excluded.slippage,
                    spread=excluded.spread,
                    cost_model_status=excluded.cost_model_status,
                    cost_source=excluded.cost_source,
                    cost_precision=excluded.cost_precision,
                    fill_evidence_status=excluded.fill_evidence_status,
                    entry_price_source=excluded.entry_price_source,
                    exit_price_source=excluded.exit_price_source,
                    entry_quality_label=excluded.entry_quality_label,
                    entry_quality_score=excluded.entry_quality_score,
                    strategy_revision_id=excluded.strategy_revision_id,
                    filled=excluded.filled,
                    source_json=excluded.source_json,
                    computed_at=excluded.computed_at
                """,
                (
                    payload["block_id"],
                    payload["venue"],
                    payload["symbol"],
                    payload["attribution"],
                    int(bool(payload["include_in_jue_alpha"])),
                    int(bool(payload["include_in_risk_management"])),
                    int(bool(payload["include_in_execution_quality"])),
                    float(payload["gross_pnl"]),
                    float(payload["net_pnl"]),
                    float(payload["cost_total"]),
                    float(payload["pnl_pct"]),
                    float(row.entry_price),
                    float(row.exit_price),
                    float(row.qty),
                    float(row.fees),
                    float(row.taxes),
                    float(row.funding),
                    float(row.slippage),
                    float(row.spread),
                    cost_model_status,
                    cost_source,
                    cost_precision,
                    fill_evidence_status,
                    entry_price_source,
                    exit_price_source,
                    entry_quality["entry_quality_label"],
                    float(entry_quality["entry_quality_score"]),
                    strategy_revision_id,
                    int(bool(payload["filled"])),
                    json.dumps(stored_source, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
        return payload

    def latest(self, *, venue: str = "", limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 500), 1)
        params: list[Any] = []
        where = ""
        if venue:
            where = "WHERE venue = ?"
            params.append(venue)
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM live_block_performance
                {where}
                ORDER BY computed_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, *, strategy_revision_id: str = "") -> dict[str, Any]:
        revision_id = str(strategy_revision_id or "").strip()
        where = ""
        params: list[Any] = []
        if revision_id:
            where = (
                "WHERE COALESCE(NULLIF(strategy_revision_id, ''), 'legacy') = ?"
            )
            params.append(revision_id)
        with self._connect() as conn:
            venue_rows = conn.execute(
                f"""
                SELECT
                    venue,
                    COUNT(*) AS block_count,
                    SUM(CASE WHEN include_in_jue_alpha THEN 1 ELSE 0 END) AS alpha_count,
                    SUM(CASE WHEN include_in_jue_alpha THEN net_pnl ELSE 0 END) AS alpha_net_pnl,
                    SUM(CASE WHEN include_in_execution_quality THEN 1 ELSE 0 END) AS execution_quality_count
                FROM live_block_performance
                {where}
                GROUP BY venue
                ORDER BY venue
                """,
                params,
            ).fetchall()
            row_params = list(params)
            row_params.append(500)
            rows = conn.execute(
                f"""
                SELECT *
                FROM live_block_performance
                {where}
                ORDER BY computed_at DESC
                LIMIT ?
                """,
                row_params,
            ).fetchall()
        return {
            "status": "ok",
            "db_path": str(self.path),
            "strategy_revision_id": revision_id,
            "venues": [dict(row) for row in venue_rows],
            "lanes": self._lane_summary([dict(row) for row in rows]),
        }

    @staticmethod
    def _lane_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            venue = str(row.get("venue") or "").strip().lower() or "unknown"
            for lane in _performance_lanes(row):
                grouped.setdefault((venue, lane), []).append(row)
        out: list[dict[str, Any]] = []
        for (venue, lane), lane_rows in grouped.items():
            alpha_rows = [
                row for row in lane_rows if int(row.get("include_in_jue_alpha") or 0)
            ]
            attribution_counts = _attribution_counts(lane_rows)
            non_alpha_count = len(lane_rows) - len(alpha_rows)
            execution_quality_count = sum(
                1 for row in lane_rows if int(row.get("include_in_execution_quality") or 0)
            )
            pnl_values = [_safe_float(row.get("net_pnl")) for row in alpha_rows]
            gross_values = [_safe_float(row.get("gross_pnl")) for row in alpha_rows]
            return_values = [_safe_float(row.get("pnl_pct")) for row in alpha_rows]
            wins = [value for value in pnl_values if value > 0]
            alpha_count = len(alpha_rows)
            win_rate_pct = len(wins) / alpha_count * 100.0 if alpha_count else 0.0
            expectancy_pct = (
                sum(return_values) / alpha_count if alpha_count else 0.0
            )
            max_drawdown = _max_drawdown_pct(return_values)
            cumulative_return_pct = sum(return_values)
            recovery_factor = (
                cumulative_return_pct / abs(max_drawdown)
                if max_drawdown < 0
                else (999.0 if cumulative_return_pct > 0 else 0.0)
            )
            total_cost = sum(_safe_float(row.get("cost_total")) for row in lane_rows)
            gross_basis = sum(abs(value) for value in gross_values)
            cost_drag_pct = total_cost / gross_basis * 100.0 if gross_basis > 0 else 0.0
            cost_precision_counts = {
                "recorded": 0,
                "hybrid": 0,
                "estimated": 0,
                "partial": 0,
                "missing": 0,
            }
            revision_counts: dict[str, int] = {}
            for row in alpha_rows:
                precision = str(row.get("cost_precision") or "").strip().lower()
                if precision == "recorded":
                    cost_precision_counts["recorded"] += 1
                elif precision == "hybrid":
                    cost_precision_counts["hybrid"] += 1
                elif precision == "estimated":
                    cost_precision_counts["estimated"] += 1
                elif precision == "partial":
                    cost_precision_counts["partial"] += 1
                else:
                    cost_precision_counts["missing"] += 1
                revision_id = str(row.get("strategy_revision_id") or "").strip()
                if not revision_id:
                    revision_id = "legacy"
                revision_counts[revision_id] = revision_counts.get(revision_id, 0) + 1
            cost_precision_sample_count = sum(cost_precision_counts.values())
            cost_precision_verified_rate = (
                cost_precision_counts["recorded"] / cost_precision_sample_count * 100.0
                if cost_precision_sample_count > 0
                else 0.0
            )
            cost_verified_alpha_rows = [
                row
                for row in alpha_rows
                if str(row.get("cost_precision") or "").strip().lower() == "recorded"
            ]
            cost_hybrid_alpha_rows = [
                row
                for row in alpha_rows
                if str(row.get("cost_precision") or "").strip().lower() == "hybrid"
            ]
            cost_unverified_alpha_rows = [
                row
                for row in alpha_rows
                if str(row.get("cost_precision") or "").strip().lower() != "recorded"
            ]
            cost_verified_alpha_count = len(cost_verified_alpha_rows)
            cost_hybrid_alpha_count = len(cost_hybrid_alpha_rows)
            cost_unverified_alpha_count = len(cost_unverified_alpha_rows)
            cost_verified_alpha_net_pnl = sum(
                _safe_float(row.get("net_pnl")) for row in cost_verified_alpha_rows
            )
            cost_hybrid_alpha_net_pnl = sum(
                _safe_float(row.get("net_pnl")) for row in cost_hybrid_alpha_rows
            )
            cost_unverified_alpha_net_pnl = sum(
                _safe_float(row.get("net_pnl")) for row in cost_unverified_alpha_rows
            )
            scale_blocked_by_verified_edge_samples = bool(
                alpha_count >= LANE_MIN_SAMPLES_TO_SCALE
                and cost_verified_alpha_count < LANE_MIN_SAMPLES_TO_SCALE
            )
            scale_blocked_by_verified_edge_net_pnl = bool(
                cost_verified_alpha_count >= LANE_MIN_SAMPLES_TO_SCALE
                and cost_verified_alpha_net_pnl <= 0.0
            )
            missing_cost_component_counts = _metadata_label_counts(
                alpha_rows,
                "missing_cost_components",
            )
            present_cost_component_counts = _metadata_label_counts(
                alpha_rows,
                "present_cost_components",
            )
            required_cost_component_counts = _metadata_label_counts(
                alpha_rows,
                "required_cost_components",
            )
            cost_precision_reason_counts = _metadata_label_counts(
                alpha_rows,
                "cost_precision_reason",
                limit=6,
            )
            validation_pressure_severity_counts = (
                _validation_pressure_label_counts(alpha_rows, "severity")
            )
            validation_pressure_entry_posture_counts = (
                _validation_pressure_label_counts(alpha_rows, "entry_posture")
            )
            validation_pressure_sizing_posture_counts = (
                _validation_pressure_label_counts(alpha_rows, "sizing_posture")
            )
            validation_pressure_fail_id_counts = (
                _validation_pressure_label_counts(alpha_rows, "fail_ids")
            )
            validation_pressure_warn_id_counts = (
                _validation_pressure_label_counts(alpha_rows, "warn_ids")
            )
            validation_pressure_missing_id_counts = (
                _validation_pressure_label_counts(alpha_rows, "missing_ids")
            )
            validation_pressure_discipline_action_counts = (
                _validation_pressure_discipline_action_counts(alpha_rows)
            )
            scale_blocked_by_cost_evidence = bool(
                alpha_count >= 3
                and cost_precision_verified_rate
                < LANE_COST_PRECISION_VERIFIED_MIN_PCT
            )
            cost_evidence_status = (
                "recorded_enough"
                if cost_precision_verified_rate >= LANE_COST_PRECISION_VERIFIED_MIN_PCT
                else "hybrid_needs_market_cost_repair"
                if cost_precision_counts["hybrid"] > 0
                else "estimated_or_missing"
                if cost_precision_sample_count > 0
                else "no_alpha_cost_samples"
            )
            alpha_evidence_status = (
                "no_alpha_samples"
                if alpha_count <= 0
                else "verified_cost_alpha"
                if cost_precision_verified_rate
                >= LANE_COST_PRECISION_VERIFIED_MIN_PCT
                else "hybrid_cost_alpha"
                if cost_precision_counts["hybrid"] > 0
                and not scale_blocked_by_cost_evidence
                else "unverified_cost_alpha"
                if scale_blocked_by_cost_evidence
                else "building_cost_evidence"
            )
            alpha_conversion_status, alpha_conversion_repair_hint = (
                _alpha_conversion_status(
                    alpha_count=alpha_count,
                    lane_rows=lane_rows,
                    attribution_counts=attribution_counts,
                )
            )
            entry_quality = _entry_quality_summary(alpha_rows)
            validation_repair_enforcement = (
                _validation_repair_enforcement_summary(alpha_rows)
            )
            quality_hint, action_hint = _lane_quality_hint(
                alpha_count=alpha_count,
                expectancy_pct=expectancy_pct,
                win_rate_pct=win_rate_pct,
                profit_factor=_profit_factor(pnl_values),
                max_drawdown_pct=max_drawdown,
                recovery_factor=recovery_factor,
                cost_drag_pct=cost_drag_pct,
                cost_precision_verified_rate=cost_precision_verified_rate,
            )
            risk_profile = _lane_risk_profile(
                alpha_count=alpha_count,
                expectancy_pct=expectancy_pct,
                win_rate_pct=win_rate_pct,
                profit_factor=_profit_factor(pnl_values),
                max_drawdown_pct=max_drawdown,
                recovery_factor=recovery_factor,
                cost_verified_alpha_count=cost_verified_alpha_count,
                cost_verified_alpha_net_pnl=cost_verified_alpha_net_pnl,
                cost_drag_pct=cost_drag_pct,
                cost_precision_verified_rate=cost_precision_verified_rate,
                cost_precision_sample_count=cost_precision_sample_count,
                quality_hint=quality_hint,
                entry_quality=entry_quality,
            )
            out.append(
                {
                    "venue": venue,
                    "lane": lane,
                    "block_count": len(lane_rows),
                    "alpha_count": alpha_count,
                    "sample_count": alpha_count,
                    "non_alpha_count": non_alpha_count,
                    "attribution_counts": attribution_counts,
                    "unfilled_or_unrealized_count": attribution_counts.get(
                        "unfilled_or_unrealized",
                        0,
                    ),
                    "operational_failure_pre_fill_count": attribution_counts.get(
                        "operational_failure_pre_fill",
                        0,
                    ),
                    "adopted_position_count": (
                        attribution_counts.get("adopted_existing_position", 0)
                        + attribution_counts.get("adopted_wallet_position", 0)
                    ),
                    "execution_quality_count": execution_quality_count,
                    "alpha_conversion_status": alpha_conversion_status,
                    "alpha_conversion_repair_hint": alpha_conversion_repair_hint,
                    "alpha_net_pnl": round(sum(pnl_values), 6),
                    "alpha_gross_pnl": round(sum(gross_values), 6),
                    "total_cost": round(total_cost, 6),
                    "expectancy_pct": round(expectancy_pct, 6),
                    "win_rate_pct": round(win_rate_pct, 6),
                    "profit_factor": round(_profit_factor(pnl_values), 6),
                    "max_drawdown_pct": round(max_drawdown, 6),
                    "recovery_factor": round(recovery_factor, 6),
                    "cumulative_return_pct": round(cumulative_return_pct, 6),
                    "cost_drag_pct_of_gross_pnl": round(cost_drag_pct, 6),
                    "cost_drag_pct_of_abs_gross_pnl": round(cost_drag_pct, 6),
                    "cost_precision_counts": cost_precision_counts,
                    "missing_cost_component_counts": (
                        missing_cost_component_counts
                    ),
                    "present_cost_component_counts": present_cost_component_counts,
                    "required_cost_component_counts": (
                        required_cost_component_counts
                    ),
                    "cost_precision_reason_counts": cost_precision_reason_counts,
                    "validation_pressure_severity_counts": (
                        validation_pressure_severity_counts
                    ),
                    "validation_pressure_entry_posture_counts": (
                        validation_pressure_entry_posture_counts
                    ),
                    "validation_pressure_sizing_posture_counts": (
                        validation_pressure_sizing_posture_counts
                    ),
                    "validation_pressure_fail_id_counts": (
                        validation_pressure_fail_id_counts
                    ),
                    "validation_pressure_warn_id_counts": (
                        validation_pressure_warn_id_counts
                    ),
                    "validation_pressure_missing_id_counts": (
                        validation_pressure_missing_id_counts
                    ),
                    "validation_pressure_discipline_action_counts": (
                        validation_pressure_discipline_action_counts
                    ),
                    "strategy_revision_counts": revision_counts,
                    "strategy_revision_ids": list(revision_counts.keys())[:8],
                    "primary_strategy_revision_id": (
                        max(revision_counts.items(), key=lambda item: item[1])[0]
                        if revision_counts
                        else ""
                    ),
                    "cost_precision_verified_rate": round(
                        cost_precision_verified_rate,
                        6,
                    ),
                    "cost_evidence_status": cost_evidence_status,
                    "alpha_evidence_status": alpha_evidence_status,
                    "cost_verified_alpha_count": cost_verified_alpha_count,
                    "cost_hybrid_alpha_count": cost_hybrid_alpha_count,
                    "cost_unverified_alpha_count": cost_unverified_alpha_count,
                    "cost_verified_alpha_net_pnl": round(
                        cost_verified_alpha_net_pnl,
                        6,
                    ),
                    "cost_hybrid_alpha_net_pnl": round(
                        cost_hybrid_alpha_net_pnl,
                        6,
                    ),
                    "cost_unverified_alpha_net_pnl": round(
                        cost_unverified_alpha_net_pnl,
                        6,
                    ),
                    "scale_blocked_by_verified_edge_samples": (
                        scale_blocked_by_verified_edge_samples
                    ),
                    "scale_blocked_by_verified_edge_net_pnl": (
                        scale_blocked_by_verified_edge_net_pnl
                    ),
                    "scale_blocked_by_cost_evidence": (
                        scale_blocked_by_cost_evidence
                        or scale_blocked_by_verified_edge_samples
                        or scale_blocked_by_verified_edge_net_pnl
                    ),
                    "scale_blocked_by_cost_precision": (
                        scale_blocked_by_cost_evidence
                    ),
                    "entry_quality_sample_count": entry_quality[
                        "entry_quality_sample_count"
                    ],
                    "avg_entry_quality_score": round(
                        _safe_float(entry_quality["avg_entry_quality_score"]),
                        6,
                    ),
                    "bad_entry_quality_rate_pct": round(
                        _safe_float(entry_quality["bad_entry_quality_rate_pct"]),
                        6,
                    ),
                    "entry_quality_label_counts": entry_quality[
                        "entry_quality_label_counts"
                    ],
                    "bad_entry_quality_label_counts": entry_quality[
                        "bad_entry_quality_label_counts"
                    ],
                    "good_entry_quality_label_counts": entry_quality[
                        "good_entry_quality_label_counts"
                    ],
                    "dominant_bad_entry_quality_label": entry_quality[
                        "dominant_bad_entry_quality_label"
                    ],
                    "dominant_good_entry_quality_label": entry_quality[
                        "dominant_good_entry_quality_label"
                    ],
                    "scale_blocked_by_entry_quality": bool(
                        entry_quality["scale_blocked_by_entry_quality"]
                    ),
                    **validation_repair_enforcement,
                    **risk_profile,
                    "quality_hint": quality_hint,
                    "action_hint": action_hint,
                }
            )
        out.sort(
            key=lambda row: (
                str(row.get("venue") or ""),
                str(row.get("lane") or ""),
            )
        )
        return out
