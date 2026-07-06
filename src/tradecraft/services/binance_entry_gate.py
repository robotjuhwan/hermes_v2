from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from tradecraft.services.binance_order_math import round_candidate_price
from tradecraft.services.binance_policy_effects import (
    contains_any_gate_token,
    normalized_gate_token,
)

UPBIT_SPOT_MARKET = "upbit_spot"
ALLOWED_MARKETS = {"spot", "futures", UPBIT_SPOT_MARKET}
BINANCE_ENTRY_QUALITY_WAITING_TOKENS = {
    "chase",
    "고점",
    "과열",
    "급등",
    "추격",
    "extended",
    "extended_momentum",
    "late_chase",
    "momentum_only",
    "overextended",
    "wait_for_price",
    "wait_pullback",
}
BINANCE_ENTRY_QUALITY_HIGH_CHASE_TOKENS = {
    "elevated",
    "high",
    "높음",
    "위험",
    "very_high",
}
BINANCE_ENTRY_QUALITY_EXTENDED_LOCATION_TOKENS = (
    "24h_high",
    "breakout_extended",
    "고점",
    "상단",
    "신고가",
    "high",
    "near_high",
    "near_24h_high",
    "upper_band",
)
BINANCE_ENTRY_QUALITY_RELIEF_TOKENS = (
    "discount",
    "low_risk",
    "pullback",
    "눌림",
    "저점",
    "지지",
    "할인",
    "reclaim",
    "support",
    "wait_pullback",
    "wait_for_price",
)


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


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[: max(int(limit), 1)]


def _truthy_gate_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_market(value: Any) -> str:
    market = str(value or "spot").strip().lower()
    compact = re.sub(r"[\s/:-]+", "_", market)
    if market in {"upbit", "upbit-spot", "krw_spot", "krw-spot"}:
        market = UPBIT_SPOT_MARKET
    elif compact in {
        "binance_futures",
        "binance_future",
        "binance_perp",
        "binance_perpetual",
        "binance_futures_account",
        "binance_futures_wallet",
        "futures_account",
        "futures_wallet",
        "usdm_futures",
        "um_futures",
    }:
        market = "futures"
    elif compact in {"binance_spot", "spot_account", "spot_wallet"}:
        market = "spot"
    return market if market in ALLOWED_MARKETS else "spot"


def _normalize_position_side(value: Any) -> str:
    side = str(value or "long").strip().lower()
    return "short" if side in {"short", "sell"} else "long"


def normalize_entry_trigger_operator(value: Any, *, default: str) -> str:
    text = str(value or "").strip().lower()
    if text in {">", ">=", "above", "up", "breakout", "gte", "at_or_above"}:
        return ">="
    if text in {"<", "<=", "below", "down", "pullback", "lte", "at_or_below"}:
        return "<="
    return default if default in {"<=", ">="} else "<="


def entry_trigger_fired(
    block: dict[str, Any],
    *,
    price: float,
    order_side: str,
) -> bool:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    trigger = _safe_float(metadata.get("entry_trigger_price") or block.get("entry_price"))
    current_price = _safe_float(price)
    if trigger <= 0 or current_price <= 0:
        return False
    operator = normalize_entry_trigger_operator(
        metadata.get("entry_trigger_operator"),
        default="<=" if str(order_side or "").strip().lower() == "buy" else ">=",
    )
    if operator == ">=":
        return current_price >= trigger
    return current_price <= trigger


def is_waiting_entry_block(block: dict[str, Any]) -> bool:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    style = str(metadata.get("entry_style") or "").strip().lower()
    if style in {"wait_for_price", "waiting_entry", "triggered_entry"}:
        return True
    return _safe_float(metadata.get("entry_trigger_price")) > 0


def entry_tolerance_price(
    *,
    entry_price: float,
    side: str,
    aggressive_limit_bps: float,
) -> float:
    entry = _safe_float(entry_price)
    if entry <= 0:
        return 0.0
    bps = max(_safe_float(aggressive_limit_bps), 0.0)
    multiplier = 1.0 + (bps / 10_000.0) if str(side or "").lower() == "buy" else 1.0 - (bps / 10_000.0)
    return max(entry * multiplier, 0.0)


def entry_reference_inside_tolerance(
    *,
    entry_price: float,
    reference_price: float,
    side: str,
    aggressive_limit_bps: float,
) -> bool:
    tolerance = entry_tolerance_price(
        entry_price=entry_price,
        side=side,
        aggressive_limit_bps=aggressive_limit_bps,
    )
    entry = _safe_float(entry_price)
    reference = _safe_float(reference_price)
    if entry <= 0 or reference <= 0 or tolerance <= 0:
        return True
    if str(side or "").lower() == "buy":
        return reference <= tolerance
    return reference >= tolerance


def waiting_entry_metadata(
    *,
    block: dict[str, Any],
    trigger_price: float,
    operator: str,
    reason: str,
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
    metadata.update(
        {
            "entry_style": "wait_for_price",
            "entry_trigger_price": trigger_price,
            "entry_trigger_operator": operator,
            "entry_trigger_status": "waiting",
            "entry_trigger_reason": reason,
        }
    )
    if isinstance(reference, dict) and reference:
        metadata["last_entry_reference"] = {
            "bid": _safe_float(reference.get("bid")),
            "ask": _safe_float(reference.get("ask")),
            "execution_price": _safe_float(reference.get("execution_price")),
            "source": str(reference.get("source") or reference.get("execution_source") or ""),
            "fetched_at": str(reference.get("fetched_at") or ""),
        }
    return metadata


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _crypto_reward_risk(
    *,
    side: str,
    entry_price: float,
    target_price: float,
    stop_price: float,
) -> dict[str, Any]:
    entry = _safe_float(entry_price)
    target = _safe_float(target_price)
    stop = _safe_float(stop_price)
    if entry <= 0 or target <= 0 or stop <= 0:
        return {"status": "missing_price_structure"}
    normalized_side = _normalize_position_side(side)
    if normalized_side == "short":
        risk = stop - entry
        reward = entry - target
    else:
        risk = entry - stop
        reward = target - entry
    if risk <= 0 or reward <= 0:
        return {
            "status": "invalid_price_structure",
            "side": normalized_side,
            "entry_price": entry,
            "target_price": target,
            "stop_price": stop,
            "reward_risk": 0.0,
            "stop_risk_pct": 0.0,
        }
    return {
        "status": "ok",
        "side": normalized_side,
        "entry_price": entry,
        "target_price": target,
        "stop_price": stop,
        "reward_risk": reward / risk,
        "stop_risk_pct": (risk / entry) * 100.0,
    }


def entry_fill_price_update_fields(
    block: dict[str, Any],
    *,
    fill_price: float,
    min_candidate_stop_pct: float,
) -> dict[str, Any]:
    filled_entry = _safe_float(fill_price)
    if filled_entry <= 0:
        return {}
    side = _normalize_position_side(block.get("side"))
    planned_entry = _safe_float(block.get("entry_price"))
    planned_target = _safe_float(block.get("target_price"))
    planned_stop = _safe_float(block.get("stop_price"))
    fields: dict[str, Any] = {"entry_price": filled_entry}
    structure = _crypto_reward_risk(
        side=side,
        entry_price=filled_entry,
        target_price=planned_target,
        stop_price=planned_stop,
    )
    metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
    calculated = (
        metadata.get("calculated_price_plan")
        if isinstance(metadata.get("calculated_price_plan"), dict)
        else {}
    )
    context: dict[str, Any] = {
        "version": "entry_fill_price_rebase_v1",
        "side": side,
        "planned_entry_price": planned_entry,
        "filled_entry_price": filled_entry,
        "old_target_price": planned_target,
        "old_stop_price": planned_stop,
        "old_structure_status": str(structure.get("status") or ""),
        "rebased": False,
        "rebased_at": _utc_now_iso(),
    }
    if planned_entry > 0 and abs(filled_entry - planned_entry) > max(
        planned_entry * 0.00001,
        0.00000001,
    ):
        metadata["entry_fill_price"] = context
        fields["metadata"] = metadata
    if structure.get("status") == "ok":
        return fields

    risk_pct = _safe_float(calculated.get("risk_pct"))
    target_pct = _safe_float(calculated.get("target_pct"))
    if risk_pct <= 0 and planned_entry > 0 and planned_stop > 0:
        risk_pct = abs(planned_entry - planned_stop) / planned_entry * 100.0
    if target_pct <= 0 and planned_entry > 0 and planned_target > 0:
        target_pct = abs(planned_target - planned_entry) / planned_entry * 100.0
    if risk_pct <= 0:
        risk_pct = max(_safe_float(min_candidate_stop_pct), 0.1)
    if target_pct <= 0:
        target_pct = risk_pct * 1.5
    target_pct = max(target_pct, risk_pct * 1.05)
    if side == "short":
        new_stop = filled_entry * (1 + risk_pct / 100.0)
        new_target = filled_entry * (1 - target_pct / 100.0)
    else:
        new_stop = filled_entry * (1 - risk_pct / 100.0)
        new_target = filled_entry * (1 + target_pct / 100.0)
    new_stop = round_candidate_price(new_stop)
    new_target = round_candidate_price(new_target)
    new_structure = _crypto_reward_risk(
        side=side,
        entry_price=filled_entry,
        target_price=new_target,
        stop_price=new_stop,
    )
    context.update(
        {
            "rebased": True,
            "risk_pct": round(risk_pct, 6),
            "target_pct": round(target_pct, 6),
            "new_target_price": new_target,
            "new_stop_price": new_stop,
            "new_structure_status": str(new_structure.get("status") or ""),
        }
    )
    metadata["entry_fill_price_rebase"] = context
    metadata["entry_fill_price"] = context
    metadata.setdefault("initial_stop_price", new_stop)
    metadata.setdefault("initial_target_price", new_target)
    fields.update(
        {
            "target_price": new_target,
            "stop_price": new_stop,
            "metadata": metadata,
        }
    )
    return fields


def normalize_entry_quality_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^0-9a-zA-Z가-힣_:-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_:-")
    return text[:80]


def entry_quality_label_from_payload(*sources: Any) -> str:
    direct_keys = (
        "entry_quality_label",
        "entry_quality",
        "raw_entry_quality",
        "entry_setup",
        "setup",
    )
    nested_keys = (
        "calculated_price_plan",
        "calculated",
        "block_template",
        "entry_gate",
        "entry_quality_gate",
    )
    style_keys = ("entry_style", "recommended_entry_mode")

    def walk(source: Any, *, allow_style: bool = False, depth: int = 0) -> str:
        if depth > 4 or not isinstance(source, dict):
            return ""
        for key in direct_keys:
            raw = source.get(key)
            if isinstance(raw, dict):
                label = walk(raw, allow_style=True, depth=depth + 1)
                if label:
                    return label
                continue
            label = normalize_entry_quality_label(raw)
            if label:
                return label
        for key in nested_keys:
            label = walk(source.get(key), allow_style=True, depth=depth + 1)
            if label:
                return label
        nested_quality = source.get("entry_quality")
        if isinstance(nested_quality, dict):
            label = walk(nested_quality, allow_style=True, depth=depth + 1)
            if label:
                return label
        if allow_style:
            for key in style_keys:
                label = normalize_entry_quality_label(source.get(key))
                if label:
                    return label
        return ""

    for source in sources:
        label = walk(source)
        if label:
            return label
    return ""


def entry_quality_gate_check(
    row: dict[str, Any],
    *,
    waiting_entry: bool,
) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}

    def first_text(*keys: str) -> str:
        for source in (row, metadata, calculated):
            for key in keys:
                value = source.get(key) if isinstance(source, dict) else None
                if value not in (None, ""):
                    return _clean_text(value, limit=240)
        return ""

    entry_quality = first_text("entry_quality", "raw_entry_quality", "entry_style")
    chase_risk = first_text("chase_risk")
    price_location = first_text("price_location", "location")
    technical_setup = first_text("technical_setup", "setup", "entry_setup")
    regime_alignment = first_text("regime_alignment", "market_regime", "regime")
    funding_context = first_text("funding_context", "funding_status")
    alpha_event = first_text("alpha_event", "narrative", "market_narrative")
    pullback_confirmed = any(
        _truthy_gate_value(source.get("pullback_confirmed"))
        for source in (row, metadata, calculated)
        if isinstance(source, dict) and "pullback_confirmed" in source
    )
    entry_quality_score_raw = next(
        (
            source.get("entry_quality_score")
            for source in (row, metadata, calculated)
            if isinstance(source, dict)
            and source.get("entry_quality_score") not in (None, "")
        ),
        None,
    )
    entry_quality_score = (
        max(_safe_float(entry_quality_score_raw), 0.0)
        if entry_quality_score_raw is not None
        else None
    )

    pressure: list[str] = []
    reliefs: list[str] = []
    confluence: list[str] = []
    if contains_any_gate_token(entry_quality, BINANCE_ENTRY_QUALITY_WAITING_TOKENS):
        pressure.append(normalized_gate_token(entry_quality) or "extended_momentum")
    if contains_any_gate_token(chase_risk, BINANCE_ENTRY_QUALITY_HIGH_CHASE_TOKENS):
        pressure.append(f"chase_risk_{normalized_gate_token(chase_risk)}")
    if contains_any_gate_token(
        price_location,
        BINANCE_ENTRY_QUALITY_EXTENDED_LOCATION_TOKENS,
    ):
        pressure.append(f"price_location_{normalized_gate_token(price_location)}")
    if contains_any_gate_token(technical_setup, BINANCE_ENTRY_QUALITY_WAITING_TOKENS):
        pressure.append(f"technical_setup_{normalized_gate_token(technical_setup)}")
    if entry_quality_score is not None and 0 < entry_quality_score < 55:
        pressure.append("entry_quality_score_below_55")

    if pullback_confirmed:
        reliefs.append("pullback_confirmed")
    if contains_any_gate_token(price_location, BINANCE_ENTRY_QUALITY_RELIEF_TOKENS):
        reliefs.append("low_risk_price_location")
    if contains_any_gate_token(entry_quality, BINANCE_ENTRY_QUALITY_RELIEF_TOKENS):
        reliefs.append("entry_quality_waits_for_price")
    if contains_any_gate_token(
        regime_alignment,
        {"aligned", "positive", "favorable", "risk_on", "우호", "정합"},
    ):
        confluence.append("regime_aligned")
    if contains_any_gate_token(
        funding_context,
        {"neutral", "favorable", "low", "우호", "중립"},
    ):
        confluence.append("funding_not_hostile")
    if contains_any_gate_token(
        alpha_event,
        {"catalyst", "narrative", "event", "positive", "재료", "내러티브"},
    ):
        confluence.append("alpha_event_context")

    hard_pressure = any(reason != "entry_quality_score_below_55" for reason in pressure)
    price_relief_present = pullback_confirmed or contains_any_gate_token(
        price_location,
        BINANCE_ENTRY_QUALITY_RELIEF_TOKENS,
    )
    requires_waiting_entry = bool(pressure) and not waiting_entry and not (
        price_relief_present or (not hard_pressure and bool(confluence))
    )
    reasons = ["entry_quality_requires_waiting_entry"] if requires_waiting_entry else []
    return {
        "version": "binance_entry_quality_gate_v1",
        "has_signal": bool(
            entry_quality
            or chase_risk
            or price_location
            or technical_setup
            or regime_alignment
            or funding_context
            or alpha_event
            or entry_quality_score is not None
            or pullback_confirmed
        ),
        "waiting_entry": waiting_entry,
        "requires_waiting_entry": requires_waiting_entry,
        "pressure": pressure,
        "reliefs": reliefs,
        "confluence": confluence,
        "hard_pressure": hard_pressure,
        "price_relief_present": price_relief_present,
        "entry_quality": entry_quality,
        "chase_risk": chase_risk,
        "price_location": price_location,
        "technical_setup": technical_setup,
        "regime_alignment": regime_alignment,
        "funding_context": funding_context,
        "alpha_event": alpha_event,
        "entry_quality_score": round(entry_quality_score, 6)
        if entry_quality_score is not None
        else None,
        "pullback_confirmed": pullback_confirmed,
        "reasons": reasons,
    }


_normalize_entry_quality_label = normalize_entry_quality_label
_entry_quality_label_from_payload = entry_quality_label_from_payload


def shadow_only_entry_qualities(
    entry_quality_cooldowns: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, row in entry_quality_cooldowns.items():
        if key != "spot:long:wait_pullback":
            continue
        sample_count = _safe_int(row.get("sample_count"))
        profit_factor = _safe_float(row.get("profit_factor"))
        pnl = _safe_float(row.get("pnl_usdt"))
        avg_r = _safe_float(row.get("avg_r_multiple"))
        if sample_count <= 0:
            continue
        if not (profit_factor < 0.7 or pnl < 0.0 or avg_r < 0.0):
            continue
        out[key] = {
            "status": "shadow_only",
            "live_budget_multiplier": 0.0,
            "sample_count": sample_count,
            "pnl_usdt": pnl,
            "win_rate_pct": _safe_float(row.get("win_rate_pct")),
            "avg_r_multiple": avg_r,
            "profit_factor": profit_factor,
            "instruction": (
                "Spot long wait_pullback has poor realized expectancy. Keep it "
                "as research/shadow tracking only until closed-block recovery "
                "or explicit operator override proves the behavior changed."
            ),
        }
    return out


def wait_pullback_confirmation_rejection(
    row: dict[str, Any],
) -> dict[str, Any] | None:
    market = _normalize_market(row.get("market") or row.get("venue"))
    if market != "futures":
        return None
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    calculated_price_plan = (
        row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else metadata.get("calculated_price_plan")
        if isinstance(metadata.get("calculated_price_plan"), dict)
        else {}
    )
    entry_quality = _entry_quality_label_from_payload(
        row,
        metadata,
        calculated,
        calculated_price_plan,
    )
    if entry_quality != "wait_pullback":
        return None

    sources = (row, metadata, calculated, calculated_price_plan)
    live_statuses: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        crosscheck = source.get("pattern_live_crosscheck")
        if isinstance(crosscheck, dict):
            status = str(crosscheck.get("status") or "").strip().lower()
            if status:
                live_statuses.append(status)
            if status in {"aligned", "confirmed"}:
                return None
        for key in (
            "live_microstructure_confirmation",
            "microstructure_confirmation",
            "orderbook_confirmation",
            "funding_confirmation",
        ):
            confirmation = source.get(key)
            if isinstance(confirmation, dict):
                status = str(confirmation.get("status") or "").strip().lower()
                if status:
                    live_statuses.append(status)
                if status in {"aligned", "confirmed", "ok"}:
                    return None
            elif _truthy_gate_value(confirmation):
                return None
        if any(
            _truthy_gate_value(source.get(key))
            for key in (
                "live_microstructure_confirmed",
                "microstructure_confirmed",
                "orderbook_confirmed",
                "book_spread_ok",
                "spread_ok",
                "funding_acceptable",
                "failed_breakout_confirmed",
                "pullback_confirmed",
                "pullback_reclaim_confirmed",
            )
        ):
            return None

    side = _normalize_position_side(row.get("side"))
    return {
        "status": "rejected",
        "reason": "wait_pullback_live_confirmation_required",
        "input": row,
        "wait_pullback_confirmation": {
            "version": "binance_wait_pullback_confirmation_v1",
            "market": market,
            "side": side,
            "entry_quality": entry_quality,
            "observed_statuses": live_statuses,
            "required": [
                "pattern_live_crosscheck.status=aligned",
                "or explicit live_microstructure/orderbook/funding confirmation",
            ],
            "instruction": (
                "Futures wait_pullback may be proposed only after live book, "
                "spread, funding, or failed-breakout/pullback evidence confirms "
                "the setup. A price structure alone is not enough."
            ),
        },
    }


def volatile_attack_context(
    *,
    candidate: dict[str, Any],
    features: dict[str, Any],
    spread_bps: float,
    change_pct_24h: float,
    market: str,
    enabled: bool,
    min_change_pct: float = 0.0,
    min_volume_expansion: float = 0.0,
) -> dict[str, Any]:
    if not bool(enabled):
        return {"enabled": False, "score": 0.0, "reasons": ["disabled"]}
    raw_lane = str(
        candidate.get("lane")
        or candidate.get("candidate_lane")
        or features.get("lane")
        or features.get("candidate_lane")
        or ""
    ).strip().lower()
    explicit = raw_lane == "volatile_attack" or bool(candidate.get("volatile_attack"))
    volume_expansion = _safe_float(
        features.get("volume_expansion_ratio")
        or candidate.get("volume_expansion_ratio")
        or features.get("volume_expansion")
        or candidate.get("volume_expansion")
    )
    wick_risk = _safe_float(
        features.get("wick_risk_score")
        or candidate.get("wick_risk_score")
        or features.get("upper_wick_risk_score")
        or candidate.get("upper_wick_risk_score")
    )
    depth_usdt = _safe_float(
        features.get("orderbook_depth_usdt")
        or candidate.get("orderbook_depth_usdt")
        or features.get("book_depth_usdt")
        or candidate.get("book_depth_usdt")
    )
    funding = _safe_float(features.get("funding_rate") or candidate.get("funding_rate"))
    open_interest = _safe_float(features.get("open_interest") or candidate.get("open_interest"))
    squeeze = _safe_float(
        features.get("squeeze_risk_score")
        or candidate.get("squeeze_risk_score")
        or features.get("squeeze_score")
        or candidate.get("squeeze_score")
    )
    alpha_score = _safe_float(
        candidate.get("alpha_event_score")
        or features.get("alpha_event_score")
        or candidate.get("event_score")
        or features.get("event_score")
    )
    abs_change = abs(change_pct_24h)
    min_change = max(_safe_float(min_change_pct), 0.0)
    min_volume = max(_safe_float(min_volume_expansion), 0.0)
    score = 0.0
    reasons: list[str] = []
    if explicit:
        score += 35.0
        reasons.append("explicit_lane")
    if abs_change >= min_change:
        score += min(30.0, abs_change)
        reasons.append("large_24h_move")
    if volume_expansion >= min_volume:
        score += min(25.0, volume_expansion * 8.0)
        reasons.append("volume_expansion")
    if squeeze >= 65:
        score += min(15.0, (squeeze - 50.0) * 0.4)
        reasons.append("squeeze_setup")
    if alpha_score > 0:
        score += min(15.0, alpha_score * 0.15)
        reasons.append("alpha_event")
    if open_interest > 0:
        score += 5.0
        reasons.append("open_interest_present")
    if abs(funding) >= 0.0001:
        score += 4.0
        reasons.append("funding_dislocation")
    if spread_bps > 60:
        score -= 25.0
        reasons.append("spread_too_wide")
    elif spread_bps > 35:
        score -= 12.0
        reasons.append("spread_cost_warning")
    if wick_risk >= 75:
        score -= 20.0
        reasons.append("wick_risk_high")
    elif wick_risk >= 55:
        score -= 8.0
        reasons.append("wick_risk_warning")
    if depth_usdt > 0 and depth_usdt < 25_000:
        score -= 15.0
        reasons.append("depth_thin")
    is_enabled = bool(explicit or score >= 45.0)
    return {
        "enabled": is_enabled,
        "score": round(max(score, 0.0), 4),
        "explicit": explicit,
        "market": _normalize_market(market),
        "change_pct_24h": change_pct_24h,
        "volume_expansion_ratio": volume_expansion,
        "spread_bps": spread_bps,
        "wick_risk_score": wick_risk,
        "orderbook_depth_usdt": depth_usdt,
        "funding_rate": funding,
        "open_interest": open_interest,
        "squeeze_risk_score": squeeze,
        "alpha_event_score": alpha_score,
        "reasons": reasons,
    }
