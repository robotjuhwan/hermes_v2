from __future__ import annotations

import re
import math
from typing import Any

from tradecraft.services.kis_price import round_policy_krx_price

ENTRY_WAIT_STYLE = "wait_for_price"
ENTRY_QUALITY_WAITING_TOKENS = {
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
ENTRY_QUALITY_HIGH_CHASE_TOKENS = {
    "elevated",
    "high",
    "높음",
    "위험",
    "very_high",
}
ENTRY_QUALITY_EXTENDED_LOCATION_TOKENS = (
    "20d_high",
    "52w_high",
    "breakout_extended",
    "고점",
    "상단",
    "신고가",
    "high",
    "near_high",
    "near_20d_high",
    "near_52w_high",
    "upper_band",
)
ENTRY_QUALITY_PULLBACK_LOCATION_TOKENS = (
    "discount",
    "low_risk",
    "near_support",
    "pullback",
    "눌림",
    "저점",
    "지지",
    "할인",
    "reclaim",
    "support",
)
ENTRY_QUALITY_TEXT_FIELDS = {
    "entry_quality": 200,
    "chase_risk": 200,
    "price_location": 300,
    "technical_setup": 500,
    "regime_alignment": 300,
    "valuation_label": 300,
    "supply_recovery": 300,
    "flow_recovery": 300,
    "sector_rotation": 300,
    "market_regime": 300,
}
POLICY_MIN_REWARD_RISK_KEYS = {
    "min_reward_risk",
    "minimum_reward_risk",
    "min_rr",
    "min_r_multiple",
    "min_reward_to_risk",
}
POLICY_MAX_STOP_RISK_PCT_KEYS = {
    "max_stop_risk_pct",
    "max_risk_pct",
    "max_stop_pct",
    "max_stop_loss_pct",
    "stop_loss_max_pct",
}
POLICY_WAITING_ENTRY_EFFECT_KEYS = {
    "entry_bias",
    "entry_style",
    "entry_mode",
    "entry_requirement",
    "required_entry_style",
    "requires_waiting_entry",
    "target_stop_review",
    "wait_for_price",
}
POLICY_WAITING_ENTRY_TOKENS = {
    "wait",
    "waiting",
    "wait_for_price",
    "pullback",
    "pullback_wait",
    "conditional",
    "trigger",
    "reprice",
    "price_improvement",
    "widen",
    "rebuild",
    "tighten",
    "risk_reward",
    "drawdown_review",
    "대기",
    "눌림",
}
POLICY_TRIGGER_PRICE_KEYS = {
    "entry_trigger_price",
    "trigger_price",
    "pullback_price",
    "waiting_entry_price",
}
POLICY_ENTRY_TRIGGER_PCT_KEYS = {
    "entry_trigger_pct",
    "entry_pullback_pct",
    "pullback_pct",
    "waiting_entry_pct",
    "price_improvement_pct",
    "entry_discount_pct",
}
POLICY_QTY_CAP_KEYS = {"qty_cap", "max_qty", "max_new_block_qty"}
POLICY_QTY_MULTIPLIER_KEYS = {
    "qty_multiplier",
    "sizing_multiplier",
    "risk_budget_multiplier",
    "allocation_multiplier",
    "budget_multiplier",
    "max_budget_multiplier",
    "applied_max_budget_multiplier",
}
POLICY_TARGET_PRICE_MULTIPLIER_KEYS = {
    "target_price_multiplier",
    "target_multiplier",
}
POLICY_TARGET_PROFIT_PCT_KEYS = {
    "target_profit_pct",
    "target_gain_pct",
    "target_upside_pct",
    "target_offset_pct",
}
POLICY_TARGET_REWARD_RISK_KEYS = {
    "target_reward_risk",
    "target_rr",
    "desired_reward_risk",
    "reward_risk_target",
}
POLICY_STOP_PRICE_MULTIPLIER_KEYS = {
    "stop_price_multiplier",
    "stop_multiplier",
}
POLICY_STOP_RISK_PCT_KEYS = {
    "stop_risk_pct",
    "stop_loss_pct",
    "stop_pct",
    "risk_pct",
    "invalidation_pct",
}


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "required"}:
        return True
    if text in {"0", "false", "no", "n", "off", "none", ""}:
        return False
    return True


def _safe_int(value: Any) -> int:
    return int(_safe_float(value))


def _normalized_gate_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def _contains_any_token(value: Any, tokens: tuple[str, ...] | set[str]) -> bool:
    raw = str(value or "").strip().lower()
    compact = _normalized_gate_token(value)
    return any(
        str(token).strip().lower()
        and (
            str(token).strip().lower() in compact
            or str(token).strip().lower() in raw
        )
        for token in tokens
    )


def policy_effect_audit(impacts: list[dict[str, Any]]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    all_fields: list[str] = []
    for impact in impacts[:8]:
        if not isinstance(impact, dict):
            continue
        effect = impact.get("effect") if isinstance(impact.get("effect"), dict) else {}
        effect_keys = sorted(str(key) for key in effect if str(key).strip())
        fields: list[str] = []
        for key in effect_keys:
            normalized = key.lower()
            mapped = ""
            if normalized == "target_stop_review":
                for field in ("entry_style", "target_price", "stop_price"):
                    if field not in fields:
                        fields.append(field)
                    if field not in all_fields:
                        all_fields.append(field)
                continue
            if "entry" in normalized or "pullback" in normalized:
                mapped = "entry_style"
            elif "risk_note" in normalized or normalized in {"risk", "note"}:
                mapped = "risk_note"
            elif "target" in normalized or "reward" in normalized:
                mapped = "target_price"
            elif "stop" in normalized or "invalidation" in normalized:
                mapped = "stop_price"
            elif any(
                token in normalized
                for token in ("qty", "size", "sizing", "budget", "allocation")
            ):
                mapped = "qty"
            if mapped and mapped not in fields:
                fields.append(mapped)
            if mapped and mapped not in all_fields:
                all_fields.append(mapped)
        if not fields and not effect_keys:
            continue
        rules.append(
            {
                "rule_id": str(impact.get("rule_id") or impact.get("policy_id") or ""),
                "policy_id": str(impact.get("policy_id") or ""),
                "status": str(impact.get("status") or ""),
                "affected_fields": fields,
                "effect_keys": effect_keys,
            }
        )
    if not rules:
        return {}
    return {
        "version": "policy_effect_audit_v1",
        "mode": "advisory",
        "affected_fields": all_fields,
        "rules": rules,
    }


def kis_buy_fill_update_plan(
    *,
    block: dict[str, Any],
    filled_qty: Any,
    avg_price: Any,
    order_status: str,
    now_iso: str,
) -> dict[str, Any]:
    filled = max(_safe_int(filled_qty), 0)
    entry_price = _safe_float(avg_price) or block.get("entry_price")
    normalized_status = str(order_status or "")
    if normalized_status == "filled":
        return {
            "action": "opened",
            "filled_qty": filled,
            "update_fields": {
                "status": "open",
                "qty_open": filled,
                "entry_price": entry_price,
                "opened_at": now_iso,
                "llm_reason": "filled_reconciled_by_order",
            },
        }
    if normalized_status == "partially_filled":
        return {
            "action": "partial",
            "filled_qty": filled,
            "update_fields": {
                "qty_open": filled,
                "entry_price": entry_price,
                "llm_reason": "partial_entry_reconciled",
            },
        }
    if normalized_status == "canceled":
        next_status = "open" if filled > 0 else "error"
        return {
            "action": "canceled_open" if filled > 0 else "canceled_unfilled",
            "filled_qty": filled,
            "update_fields": {
                "status": next_status,
                "qty_open": filled,
                "entry_price": entry_price,
                "opened_at": now_iso if filled > 0 else "",
                "llm_reason": "entry_order_canceled",
            },
        }
    return {"action": "none", "filled_qty": filled, "update_fields": {}}


def normalize_entry_style(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {
        ENTRY_WAIT_STYLE,
        "wait",
        "price_wait",
        "wait_price",
        "limit_wait",
        "conditional",
        "conditional_limit",
        "pullback",
        "pullback_wait",
        "trigger",
        "triggered_limit",
    }:
        return ENTRY_WAIT_STYLE
    return "aggressive_limit"


def normalize_entry_trigger_operator(
    value: Any,
    *,
    trigger_price: float = 0.0,
    reference_price: float = 0.0,
) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"lte", "<=", "below", "at_or_below", "pullback", "down"}:
        return "lte"
    if raw in {"gte", ">=", "above", "at_or_above", "breakout", "up"}:
        return "gte"
    if trigger_price > 0 and reference_price > 0:
        return "lte" if trigger_price <= reference_price else "gte"
    return "lte"


def entry_trigger_reached(
    price: float,
    *,
    trigger_price: float,
    operator: str,
) -> bool:
    if price <= 0 or trigger_price <= 0:
        return False
    return price <= trigger_price if operator == "lte" else price >= trigger_price


def entry_quality_fields(row: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, limit in ENTRY_QUALITY_TEXT_FIELDS.items():
        if key not in row:
            continue
        cleaned = _clean_text(row.get(key), limit=limit)
        if cleaned:
            metadata[key] = cleaned
    if "entry_quality_score" in row:
        metadata["entry_quality_score"] = max(
            _safe_float(row.get("entry_quality_score")),
            0.0,
        )
    if "pullback_confirmed" in row:
        metadata["pullback_confirmed"] = _safe_bool(row.get("pullback_confirmed"))
    return metadata


def _first_entry_quality_text(
    row: dict[str, Any],
    metadata: dict[str, Any],
    key: str,
    *,
    limit: int = 300,
) -> str:
    for source in (row, metadata):
        value = source.get(key) if isinstance(source, dict) else None
        if value not in (None, ""):
            return _clean_text(value, limit=limit)
    return ""


def performance_scale_entry_quality_check(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    entry_style = normalize_entry_style(row.get("entry_style") or metadata.get("entry_style"))
    entry_quality = _first_entry_quality_text(row, metadata, "entry_quality", limit=200)
    chase_risk = _first_entry_quality_text(row, metadata, "chase_risk", limit=200)
    price_location = _first_entry_quality_text(
        row,
        metadata,
        "price_location",
        limit=300,
    )
    technical_setup = _first_entry_quality_text(
        row,
        metadata,
        "technical_setup",
        limit=500,
    )
    valuation_label = _first_entry_quality_text(
        row,
        metadata,
        "valuation_label",
        limit=300,
    )
    regime_alignment = _first_entry_quality_text(
        row,
        metadata,
        "regime_alignment",
        limit=300,
    )
    market_regime = _first_entry_quality_text(
        row,
        metadata,
        "market_regime",
        limit=300,
    )
    supply_recovery = _first_entry_quality_text(
        row,
        metadata,
        "supply_recovery",
        limit=300,
    )
    flow_recovery = _first_entry_quality_text(
        row,
        metadata,
        "flow_recovery",
        limit=300,
    )
    pullback_confirmed = any(
        _safe_bool(source.get("pullback_confirmed"))
        for source in (row, metadata)
        if isinstance(source, dict) and "pullback_confirmed" in source
    )
    entry_quality_score_raw = next(
        (
            source.get("entry_quality_score")
            for source in (row, metadata)
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
    if _contains_any_token(entry_quality, ENTRY_QUALITY_WAITING_TOKENS):
        pressure.append(_normalized_gate_token(entry_quality) or "extended_momentum")
    if _contains_any_token(chase_risk, ENTRY_QUALITY_HIGH_CHASE_TOKENS):
        pressure.append(f"chase_risk_{_normalized_gate_token(chase_risk)}")
    if _contains_any_token(price_location, ENTRY_QUALITY_EXTENDED_LOCATION_TOKENS):
        pressure.append(f"price_location_{_normalized_gate_token(price_location)}")
    if _contains_any_token(technical_setup, ENTRY_QUALITY_WAITING_TOKENS):
        pressure.append(f"technical_setup_{_normalized_gate_token(technical_setup)}")
    if entry_quality_score is not None and 0 < entry_quality_score < 55:
        pressure.append("entry_quality_score_below_55")

    reliefs: list[str] = []
    confluence: list[str] = []
    if entry_style == ENTRY_WAIT_STYLE:
        reliefs.append("waiting_entry_structure")
    if pullback_confirmed:
        reliefs.append("pullback_confirmed")
    if _contains_any_token(price_location, ENTRY_QUALITY_PULLBACK_LOCATION_TOKENS):
        reliefs.append("low_risk_price_location")
    if _contains_any_token(valuation_label, {"undervalued", "저평가", "discount"}):
        confluence.append("valuation_discount")
    if _contains_any_token(
        regime_alignment,
        {"aligned", "positive", "favorable", "risk_on", "우호", "정합"},
    ) or _contains_any_token(
        market_regime,
        {"aligned", "positive", "favorable", "risk_on", "우호", "정합"},
    ):
        confluence.append("regime_aligned")
    if _contains_any_token(
        supply_recovery,
        {"recovery", "improving", "positive", "수급", "회복", "순매수"},
    ) or _contains_any_token(
        flow_recovery,
        {"recovery", "improving", "positive", "수급", "회복", "순매수"},
    ):
        confluence.append("flow_recovery")

    has_signal = bool(
        entry_quality
        or chase_risk
        or price_location
        or technical_setup
        or valuation_label
        or regime_alignment
        or market_regime
        or supply_recovery
        or flow_recovery
        or entry_quality_score is not None
        or pullback_confirmed
        or entry_style == ENTRY_WAIT_STYLE
    )
    scale_up_allowed = bool(
        has_signal
        and not pressure
        and (reliefs or len(confluence) >= 2)
    )
    return {
        "version": "kis_performance_scale_entry_quality_v1",
        "scale_up_allowed": scale_up_allowed,
        "has_signal": has_signal,
        "entry_style": entry_style,
        "pressure": pressure,
        "reliefs": reliefs,
        "confluence": confluence,
        "entry_quality_score": round(entry_quality_score, 6)
        if entry_quality_score is not None
        else None,
    }


def create_row_entry_quality_gate(row: dict[str, Any]) -> dict[str, Any]:
    entry_style = normalize_entry_style(row.get("entry_style"))
    entry_quality = _clean_text(row.get("entry_quality"), limit=200)
    chase_risk = _clean_text(row.get("chase_risk"), limit=200)
    price_location = _clean_text(row.get("price_location"), limit=300)
    technical_setup = _clean_text(row.get("technical_setup"), limit=500)
    regime_alignment = _clean_text(row.get("regime_alignment"), limit=300)
    valuation_label = _clean_text(row.get("valuation_label"), limit=300)
    supply_recovery = _clean_text(row.get("supply_recovery"), limit=300)
    flow_recovery = _clean_text(row.get("flow_recovery"), limit=300)
    sector_rotation = _clean_text(row.get("sector_rotation"), limit=300)
    market_regime = _clean_text(row.get("market_regime"), limit=300)
    entry_quality_score = (
        max(_safe_float(row.get("entry_quality_score")), 0.0)
        if row.get("entry_quality_score") is not None
        else None
    )
    pullback_confirmed = _safe_bool(row.get("pullback_confirmed"))
    reasons: list[str] = []
    if _contains_any_token(entry_quality, ENTRY_QUALITY_WAITING_TOKENS):
        reasons.append(_normalized_gate_token(entry_quality) or "extended_momentum")
    if _contains_any_token(chase_risk, ENTRY_QUALITY_HIGH_CHASE_TOKENS):
        reasons.append(f"chase_risk_{_normalized_gate_token(chase_risk)}")
    if _contains_any_token(price_location, ENTRY_QUALITY_EXTENDED_LOCATION_TOKENS):
        reasons.append(f"price_location_{_normalized_gate_token(price_location)}")
    if _contains_any_token(technical_setup, ENTRY_QUALITY_WAITING_TOKENS):
        reasons.append(f"technical_setup_{_normalized_gate_token(technical_setup)}")
    if entry_quality_score is not None and 0 < entry_quality_score < 55:
        reasons.append("entry_quality_score_below_55")

    reliefs: list[str] = []
    confluence: list[str] = []
    if pullback_confirmed:
        reliefs.append("pullback_confirmed")
    if _contains_any_token(price_location, ENTRY_QUALITY_PULLBACK_LOCATION_TOKENS):
        reliefs.append("low_risk_price_location")
    if _contains_any_token(valuation_label, {"undervalued", "저평가", "discount"}):
        confluence.append("valuation_discount")
    if _contains_any_token(
        regime_alignment,
        {"aligned", "positive", "favorable", "risk_on", "우호", "정합"},
    ) or _contains_any_token(
        market_regime,
        {"aligned", "positive", "favorable", "risk_on", "우호", "정합"},
    ):
        confluence.append("regime_aligned")
    if _contains_any_token(
        supply_recovery,
        {"recovery", "improving", "positive", "수급", "회복", "순매수"},
    ) or _contains_any_token(
        flow_recovery,
        {"recovery", "improving", "positive", "수급", "회복", "순매수"},
    ):
        confluence.append("flow_recovery")
    if _contains_any_token(
        sector_rotation,
        {"leader", "rotation", "positive", "주도", "순환매", "섹터"},
    ):
        confluence.append("sector_rotation")

    hard_pressure = any(reason != "entry_quality_score_below_55" for reason in reasons)
    price_relief_present = bool(reliefs)
    waiting_entry_preferred = bool(reasons) and not (
        price_relief_present or (not hard_pressure and bool(confluence))
    )
    allowed = entry_style == ENTRY_WAIT_STYLE or not waiting_entry_preferred
    gate: dict[str, Any] = {
        "version": "kis_entry_quality_gate_v1",
        "allowed": allowed,
        "entry_style": entry_style,
        "requires_waiting_entry": bool(
            waiting_entry_preferred and entry_style != ENTRY_WAIT_STYLE
        ),
        "waiting_entry_preferred": waiting_entry_preferred,
        "reasons": reasons,
        "reliefs": reliefs,
        "confluence": confluence,
        "hard_pressure": hard_pressure,
        "price_relief_present": price_relief_present,
    }
    for key, value in (
        ("entry_quality", entry_quality),
        ("chase_risk", chase_risk),
        ("price_location", price_location),
        ("technical_setup", technical_setup),
        ("regime_alignment", regime_alignment),
        ("valuation_label", valuation_label),
        ("supply_recovery", supply_recovery),
        ("flow_recovery", flow_recovery),
        ("sector_rotation", sector_rotation),
        ("market_regime", market_regime),
    ):
        if value:
            gate[key] = value
    if entry_quality_score is not None:
        gate["entry_quality_score"] = round(entry_quality_score, 6)
    if "pullback_confirmed" in row:
        gate["pullback_confirmed"] = pullback_confirmed
    return gate


def invalid_long_price_structure_reason(
    *,
    reference_price: float,
    target_price: float,
    stop_price: float,
) -> str:
    reference = _safe_float(reference_price)
    target = _safe_float(target_price)
    stop = _safe_float(stop_price)
    if reference <= 0:
        return "reference_price_missing"
    if target <= 0 or stop <= 0:
        return "target_or_stop_missing"
    if not (stop < reference < target):
        return "invalid_target_stop_bounds"
    return ""


def _policy_effects(
    impacts: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for impact in impacts[:8]:
        if not isinstance(impact, dict):
            continue
        effect = impact.get("effect") if isinstance(impact.get("effect"), dict) else {}
        if effect:
            rows.append((impact, effect))
    return rows


def _policy_effect_min_reward_risk(effect: dict[str, Any]) -> float:
    return max(
        (_safe_float(effect.get(key)) for key in POLICY_MIN_REWARD_RISK_KEYS),
        default=0.0,
    )


def _policy_effect_max_stop_risk_pct(effect: dict[str, Any]) -> float:
    values = [
        _safe_float(effect.get(key))
        for key in POLICY_MAX_STOP_RISK_PCT_KEYS
        if _safe_float(effect.get(key)) > 0
    ]
    return min(values) if values else 0.0


def policy_effect_waiting_required(effect: dict[str, Any]) -> bool:
    if _safe_bool(effect.get("requires_waiting_entry")):
        return True
    for key in POLICY_WAITING_ENTRY_EFFECT_KEYS:
        if key not in effect:
            continue
        value = effect.get(key)
        if key == "wait_for_price" and _safe_bool(value):
            return True
        if _contains_any_token(value, POLICY_WAITING_ENTRY_TOKENS):
            return True
    return any(_safe_float(effect.get(key)) > 0 for key in POLICY_TRIGGER_PRICE_KEYS) or any(
        _safe_float(effect.get(key)) > 0 for key in POLICY_ENTRY_TRIGGER_PCT_KEYS
    )


def policy_effect_trigger_price(effect: dict[str, Any]) -> float:
    for key in POLICY_TRIGGER_PRICE_KEYS:
        price = _safe_float(effect.get(key))
        if price > 0:
            return price
    return 0.0


def policy_first_positive_float(
    effect: dict[str, Any],
    keys: set[str],
) -> tuple[float, str]:
    for key in keys:
        value = _safe_float(effect.get(key))
        if value > 0:
            return value, key
    return 0.0, ""


def policy_reference_entry_price(
    row: dict[str, Any],
    *,
    reference_entry_price: float = 0.0,
) -> float:
    return (
        _safe_float(row.get("entry_trigger_price"))
        or _safe_float(reference_entry_price)
        or _safe_float(row.get("entry_price"))
    )


def policy_effect_derived_trigger_price(
    effect: dict[str, Any],
    *,
    reference_entry_price: float,
) -> tuple[float, str]:
    pct, key = policy_first_positive_float(effect, POLICY_ENTRY_TRIGGER_PCT_KEYS)
    reference = _safe_float(reference_entry_price)
    if pct <= 0 or reference <= 0:
        return 0.0, ""
    return (
        round_policy_krx_price(
            reference * (1.0 - pct / 100.0),
            field="entry_trigger_price",
        ),
        key,
    )


def _append_policy_price_adjustment(
    adjustments: list[dict[str, Any]],
    *,
    rule_id: str,
    field: str,
    original: float,
    new_value: float,
    method: str,
    reference_entry_price: float,
    effect_key: str,
) -> None:
    adjustments.append(
        {
            "rule_id": rule_id,
            "field": field,
            "from": original,
            "to": new_value,
            "method": method,
            "reference_entry_price": reference_entry_price,
            "effect_key": effect_key,
        }
    )


def apply_policy_relative_price_effects(
    row: dict[str, Any],
    effect: dict[str, Any],
    *,
    rule_id: str,
    adjustments: list[dict[str, Any]],
    reference_entry_price: float = 0.0,
) -> None:
    reference = policy_reference_entry_price(
        row,
        reference_entry_price=reference_entry_price,
    )
    if reference <= 0:
        return

    stop_multiplier, stop_multiplier_key = policy_first_positive_float(
        effect,
        POLICY_STOP_PRICE_MULTIPLIER_KEYS,
    )
    stop_pct, stop_pct_key = policy_first_positive_float(effect, POLICY_STOP_RISK_PCT_KEYS)
    new_stop = 0.0
    stop_method = ""
    stop_effect_key = ""
    if stop_multiplier > 0:
        new_stop = reference * stop_multiplier
        stop_method = "price_multiplier"
        stop_effect_key = stop_multiplier_key
    elif stop_pct > 0:
        new_stop = reference * (1.0 - stop_pct / 100.0)
        stop_method = "risk_pct"
        stop_effect_key = stop_pct_key
    if new_stop > 0:
        rounded_stop = round_policy_krx_price(new_stop, field="stop_price")
        original_stop = _safe_float(row.get("stop_price"))
        if rounded_stop > 0 and original_stop != rounded_stop:
            row["stop_price"] = rounded_stop
            _append_policy_price_adjustment(
                adjustments,
                rule_id=rule_id,
                field="stop_price",
                original=original_stop,
                new_value=rounded_stop,
                method=stop_method,
                reference_entry_price=reference,
                effect_key=stop_effect_key,
            )

    target_multiplier, target_multiplier_key = policy_first_positive_float(
        effect,
        POLICY_TARGET_PRICE_MULTIPLIER_KEYS,
    )
    target_pct, target_pct_key = policy_first_positive_float(
        effect,
        POLICY_TARGET_PROFIT_PCT_KEYS,
    )
    target_rr, target_rr_key = policy_first_positive_float(
        effect,
        POLICY_TARGET_REWARD_RISK_KEYS,
    )
    new_target = 0.0
    target_method = ""
    target_effect_key = ""
    if target_multiplier > 0:
        new_target = reference * target_multiplier
        target_method = "price_multiplier"
        target_effect_key = target_multiplier_key
    elif target_pct > 0:
        new_target = reference * (1.0 + target_pct / 100.0)
        target_method = "profit_pct"
        target_effect_key = target_pct_key
    if target_rr > 0:
        stop = _safe_float(row.get("stop_price"))
        risk = reference - stop
        if risk > 0:
            new_target = reference + risk * target_rr
            target_method = "reward_risk"
            target_effect_key = target_rr_key
    if new_target > 0:
        rounded_target = round_policy_krx_price(new_target, field="target_price")
        original_target = _safe_float(row.get("target_price"))
        if rounded_target > 0 and original_target != rounded_target:
            row["target_price"] = rounded_target
            _append_policy_price_adjustment(
                adjustments,
                rule_id=rule_id,
                field="target_price",
                original=original_target,
                new_value=rounded_target,
                method=target_method,
                reference_entry_price=reference,
                effect_key=target_effect_key,
            )


def policy_effect_qty_adjusted(qty: int, effect: dict[str, Any]) -> int:
    adjusted = max(int(qty), 0)
    for key in POLICY_QTY_CAP_KEYS:
        cap = _safe_int(effect.get(key))
        if cap > 0:
            adjusted = min(adjusted, cap)
    for key in POLICY_QTY_MULTIPLIER_KEYS:
        if effect.get(key) is None:
            continue
        multiplier = _safe_float(effect.get(key))
        if 0 < multiplier < 1:
            adjusted = min(adjusted, max(int(math.floor(qty * multiplier)), 1))
    return max(adjusted, 1) if qty > 0 else adjusted


def long_reward_risk(
    entry_price: float,
    target_price: float,
    stop_price: float,
) -> dict[str, Any]:
    entry = _safe_float(entry_price)
    target = _safe_float(target_price)
    stop = _safe_float(stop_price)
    if entry <= 0 or target <= 0 or stop <= 0:
        return {"status": "missing_price_structure"}
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return {
            "status": "invalid_price_structure",
            "entry_price": entry,
            "target_price": target,
            "stop_price": stop,
            "reward_risk": 0.0,
            "stop_risk_pct": 0.0,
        }
    return {
        "status": "ok",
        "entry_price": entry,
        "target_price": target,
        "stop_price": stop,
        "reward_risk": reward / risk,
        "stop_risk_pct": (risk / entry) * 100.0,
    }


def policy_target_stop_quality_gate(
    row: dict[str, Any],
    impacts: list[dict[str, Any]],
    *,
    reference_entry_price: float = 0.0,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    entry_price = (
        _safe_float(reference_entry_price)
        or _safe_float(row.get("entry_price"))
        or _safe_float(row.get("entry_trigger_price"))
    )
    if entry_price <= 0:
        return {
            "version": "policy_target_stop_quality_v1",
            "rejected": False,
            "checks": checks,
        }
    structure = long_reward_risk(
        entry_price,
        _safe_float(row.get("target_price")),
        _safe_float(row.get("stop_price")),
    )
    for impact, effect in _policy_effects(impacts):
        rule_id = str(impact.get("rule_id") or impact.get("policy_id") or "")
        min_reward_risk = _policy_effect_min_reward_risk(effect)
        max_stop_risk_pct = _policy_effect_max_stop_risk_pct(effect)
        if min_reward_risk <= 0 and max_stop_risk_pct <= 0:
            continue
        check = {
            "rule_id": rule_id,
            "field": "target_stop",
            "entry_price": round(entry_price, 6),
            "target_price": round(_safe_float(row.get("target_price")), 6),
            "stop_price": round(_safe_float(row.get("stop_price")), 6),
            "min_reward_risk": round(min_reward_risk, 6)
            if min_reward_risk > 0
            else None,
            "max_stop_risk_pct": round(max_stop_risk_pct, 6)
            if max_stop_risk_pct > 0
            else None,
        }
        if structure.get("status") != "ok":
            check.update(
                {
                    "status": "rejected",
                    "reason": "policy_invalid_target_stop_structure",
                }
            )
            checks.append(check)
            return {
                "version": "policy_target_stop_quality_v1",
                "rejected": True,
                "reason": "policy_invalid_target_stop_structure",
                "rule_id": rule_id,
                "checks": checks,
            }
        reward_risk = _safe_float(structure.get("reward_risk"))
        stop_risk_pct = _safe_float(structure.get("stop_risk_pct"))
        check.update(
            {
                "status": "ok",
                "reward_risk": round(reward_risk, 6),
                "stop_risk_pct": round(stop_risk_pct, 6),
            }
        )
        if min_reward_risk > 0 and reward_risk + 1e-9 < min_reward_risk:
            check.update(
                {"status": "rejected", "reason": "policy_min_reward_risk_not_met"}
            )
            checks.append(check)
            return {
                "version": "policy_target_stop_quality_v1",
                "rejected": True,
                "reason": "policy_min_reward_risk_not_met",
                "rule_id": rule_id,
                "checks": checks,
            }
        if max_stop_risk_pct > 0 and stop_risk_pct - 1e-9 > max_stop_risk_pct:
            check.update(
                {
                    "status": "rejected",
                    "reason": "policy_max_stop_risk_pct_exceeded",
                }
            )
            checks.append(check)
            return {
                "version": "policy_target_stop_quality_v1",
                "rejected": True,
                "reason": "policy_max_stop_risk_pct_exceeded",
                "rule_id": rule_id,
                "checks": checks,
            }
        checks.append(check)
    return {
        "version": "policy_target_stop_quality_v1",
        "rejected": False,
        "checks": checks,
    }
