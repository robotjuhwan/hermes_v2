from __future__ import annotations

import re
from typing import Any

from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    normalize_market,
    normalize_position_side,
)


REWARD_RISK_MIN_TOLERANCE = 0.005
VALIDATION_REPAIR_MIN_USDT_NOTIONAL_FLOOR = 20.0
VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR = 10_000.0
VOLATILE_ATTACK_MAX_STOP_RISK_PCT = 12.0

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


def safe_float(value: Any) -> float:
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalized_gate_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def contains_any_gate_token(value: Any, tokens: tuple[str, ...] | set[str]) -> bool:
    raw = str(value or "").strip().lower()
    compact = normalized_gate_token(value)
    return any(
        str(token).strip().lower()
        and (
            str(token).strip().lower() in compact
            or str(token).strip().lower() in raw
        )
        for token in tokens
    )


def truthy_gate_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def policy_effects(impacts: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for impact in impacts[:8]:
        if not isinstance(impact, dict):
            continue
        effect = impact.get("effect") if isinstance(impact.get("effect"), dict) else {}
        if effect:
            rows.append((impact, effect))
    return rows


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


def policy_rule_ids(impacts: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for impact in impacts:
        rule_id = str(impact.get("rule_id") or impact.get("policy_id") or "")
        if rule_id and rule_id not in out:
            out.append(rule_id)
    return out


def policy_impacts_for_row(
    impacts_by_key: dict[str, list[dict[str, Any]]] | Any,
    row: dict[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not isinstance(impacts_by_key, dict):
        return []
    symbol = str(row.get("symbol") or "").upper().strip()
    rows: list[dict[str, Any]] = []
    for key in ("_global", symbol):
        value = impacts_by_key.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                rows.append(dict(item))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in rows:
        key = str(item.get("rule_id") or item.get("policy_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def policy_effect_waiting_required(effect: dict[str, Any]) -> bool:
    if truthy_gate_value(effect.get("requires_waiting_entry")):
        return True
    for key in POLICY_WAITING_ENTRY_EFFECT_KEYS:
        if key not in effect:
            continue
        value = effect.get(key)
        if key == "wait_for_price" and truthy_gate_value(value):
            return True
        if contains_any_gate_token(value, POLICY_WAITING_ENTRY_TOKENS):
            return True
    return any(safe_float(effect.get(key)) > 0 for key in POLICY_TRIGGER_PRICE_KEYS) or any(
        safe_float(effect.get(key)) > 0 for key in POLICY_ENTRY_TRIGGER_PCT_KEYS
    )


def policy_effect_trigger_price(effect: dict[str, Any]) -> float:
    for key in POLICY_TRIGGER_PRICE_KEYS:
        price = safe_float(effect.get(key))
        if price > 0:
            return price
    return 0.0


def policy_first_positive_float(effect: dict[str, Any], keys: set[str]) -> tuple[float, str]:
    for key in keys:
        value = safe_float(effect.get(key))
        if value > 0:
            return value, key
    return 0.0, ""


def policy_reference_entry_price(
    row: dict[str, Any],
    *,
    reference_entry_price: float = 0.0,
) -> float:
    return (
        safe_float(row.get("entry_trigger_price"))
        or safe_float(row.get("trigger_price"))
        or safe_float(reference_entry_price)
        or safe_float(row.get("entry_price"))
    )


def round_policy_crypto_price(value: float) -> float:
    price = max(safe_float(value), 0.0)
    if price <= 0:
        return 0.0
    if price >= 100:
        digits = 2
    elif price >= 10:
        digits = 4
    elif price >= 1:
        digits = 5
    elif price >= 0.1:
        digits = 6
    else:
        digits = 8
    return round(price, digits)


def policy_effect_derived_trigger_price(
    effect: dict[str, Any],
    *,
    reference_entry_price: float,
    side: str,
) -> tuple[float, str, str]:
    pct, key = policy_first_positive_float(effect, POLICY_ENTRY_TRIGGER_PCT_KEYS)
    reference = safe_float(reference_entry_price)
    if pct <= 0 or reference <= 0:
        return 0.0, "", ""
    normalized_side = normalize_position_side(side)
    if normalized_side == "short":
        return round_policy_crypto_price(reference * (1.0 + pct / 100.0)), key, ">="
    return round_policy_crypto_price(reference * (1.0 - pct / 100.0)), key, "<="


def append_policy_price_adjustment(
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
    side = normalize_position_side(row.get("side"))
    side_sign = -1.0 if side == "short" else 1.0

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
        new_stop = reference * (1.0 - side_sign * stop_pct / 100.0)
        stop_method = "risk_pct"
        stop_effect_key = stop_pct_key
    if new_stop > 0:
        rounded_stop = round_policy_crypto_price(new_stop)
        original_stop = safe_float(row.get("stop_price") or row.get("stop_price_usdt"))
        if rounded_stop > 0 and original_stop != rounded_stop:
            row["stop_price"] = rounded_stop
            append_policy_price_adjustment(
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
    target_rr, target_rr_key = policy_first_positive_float(effect, POLICY_TARGET_REWARD_RISK_KEYS)
    new_target = 0.0
    target_method = ""
    target_effect_key = ""
    if target_multiplier > 0:
        new_target = reference * target_multiplier
        target_method = "price_multiplier"
        target_effect_key = target_multiplier_key
    elif target_pct > 0:
        new_target = reference * (1.0 + side_sign * target_pct / 100.0)
        target_method = "profit_pct"
        target_effect_key = target_pct_key
    if target_rr > 0:
        stop = safe_float(row.get("stop_price") or row.get("stop_price_usdt"))
        risk = (stop - reference) if side == "short" else (reference - stop)
        if risk > 0:
            new_target = reference - risk * target_rr if side == "short" else reference + risk * target_rr
            target_method = "reward_risk"
            target_effect_key = target_rr_key
    if new_target > 0:
        rounded_target = round_policy_crypto_price(new_target)
        original_target = safe_float(row.get("target_price") or row.get("target_price_usdt"))
        if rounded_target > 0 and original_target != rounded_target:
            row["target_price"] = rounded_target
            append_policy_price_adjustment(
                adjustments,
                rule_id=rule_id,
                field="target_price",
                original=original_target,
                new_value=rounded_target,
                method=target_method,
                reference_entry_price=reference,
                effect_key=target_effect_key,
            )


def policy_effect_qty_adjusted(qty: float, effect: dict[str, Any]) -> float:
    adjusted = max(float(qty), 0.0)
    for key in POLICY_QTY_CAP_KEYS:
        cap = safe_float(effect.get(key))
        if cap > 0:
            adjusted = min(adjusted, cap)
    for key in POLICY_QTY_MULTIPLIER_KEYS:
        if effect.get(key) is None:
            continue
        multiplier = safe_float(effect.get(key))
        if 0 < multiplier < 1:
            adjusted = min(adjusted, qty * multiplier)
    return max(adjusted, 0.0)


def policy_effect_min_reward_risk(effect: dict[str, Any]) -> float:
    return max((safe_float(effect.get(key)) for key in POLICY_MIN_REWARD_RISK_KEYS), default=0.0)


def reward_risk_meets_minimum(reward_risk: float, min_reward_risk: float) -> bool:
    if min_reward_risk <= 0:
        return True
    return reward_risk + REWARD_RISK_MIN_TOLERANCE >= min_reward_risk


def validation_repair_notional_floor(
    *,
    field: str,
    market: str,
    original: float,
    adjusted: float,
) -> float:
    if original <= 0 or adjusted <= 0:
        return adjusted
    normalized_market = normalize_market(market)
    if field in {
        "quote_budget_usdt",
        "risk_budget_usdt",
        "max_notional_usdt",
        "notional_usdt",
        "target_block_value_usdt",
    }:
        floor = VALIDATION_REPAIR_MIN_USDT_NOTIONAL_FLOOR
    elif field in {"quote_budget_krw", "risk_budget_krw", "notional_krw"}:
        floor = VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR
    elif normalized_market == UPBIT_SPOT_MARKET:
        floor = VALIDATION_REPAIR_MIN_KRW_NOTIONAL_FLOOR
    else:
        return adjusted
    if original >= floor and adjusted < floor:
        return floor
    return adjusted


def policy_effect_max_stop_risk_pct(effect: dict[str, Any]) -> float:
    values = [
        safe_float(effect.get(key))
        for key in POLICY_MAX_STOP_RISK_PCT_KEYS
        if safe_float(effect.get(key)) > 0
    ]
    return min(values) if values else 0.0


def is_volatile_attack_row(row: dict[str, Any]) -> bool:
    fields: list[Any] = [
        row.get("lane"),
        row.get("horizon"),
        row.get("block_color"),
        row.get("entry_quality"),
    ]
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    fields.extend(
        [
            metadata.get("lane"),
            metadata.get("horizon"),
            metadata.get("block_color"),
            calculated.get("lane"),
            calculated.get("horizon"),
        ]
    )
    return any(
        normalized_gate_token(value) == "volatile_attack"
        for value in fields
        if str(value or "").strip()
    )


def effective_max_stop_risk_pct_for_row(
    row: dict[str, Any],
    max_stop_risk_pct: float,
) -> tuple[float, str]:
    base = safe_float(max_stop_risk_pct)
    if base <= 0:
        return 0.0, ""
    if is_volatile_attack_row(row) and base < VOLATILE_ATTACK_MAX_STOP_RISK_PCT:
        return VOLATILE_ATTACK_MAX_STOP_RISK_PCT, "volatile_attack_wide_stop"
    return base, ""


def crypto_reward_risk(
    *,
    side: str,
    entry_price: float,
    target_price: float,
    stop_price: float,
) -> dict[str, Any]:
    entry = safe_float(entry_price)
    target = safe_float(target_price)
    stop = safe_float(stop_price)
    if entry <= 0 or target <= 0 or stop <= 0:
        return {"status": "missing_price_structure"}
    normalized_side = normalize_position_side(side)
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


def policy_target_stop_quality_gate(
    row: dict[str, Any],
    impacts: list[dict[str, Any]],
    *,
    reference_entry_price: float = 0.0,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    entry_price = (
        safe_float(reference_entry_price)
        or safe_float(row.get("entry_price"))
        or safe_float(row.get("entry_trigger_price"))
        or safe_float(row.get("trigger_price"))
    )
    if entry_price <= 0:
        return {
            "version": "policy_target_stop_quality_v1",
            "rejected": False,
            "checks": checks,
        }
    structure = crypto_reward_risk(
        side=str(row.get("side") or "long"),
        entry_price=entry_price,
        target_price=safe_float(row.get("target_price") or row.get("target_price_usdt")),
        stop_price=safe_float(row.get("stop_price") or row.get("stop_price_usdt")),
    )
    for impact, effect in policy_effects(impacts):
        rule_id = str(impact.get("rule_id") or impact.get("policy_id") or "")
        min_reward_risk = policy_effect_min_reward_risk(effect)
        raw_max_stop_risk_pct = policy_effect_max_stop_risk_pct(effect)
        max_stop_risk_pct, max_stop_override_reason = effective_max_stop_risk_pct_for_row(
            row,
            raw_max_stop_risk_pct,
        )
        if min_reward_risk <= 0 and max_stop_risk_pct <= 0:
            continue
        check = {
            "rule_id": rule_id,
            "field": "target_stop",
            "side": str(row.get("side") or "long").strip().lower() or "long",
            "entry_price": round(entry_price, 8),
            "target_price": round(
                safe_float(row.get("target_price") or row.get("target_price_usdt")),
                8,
            ),
            "stop_price": round(
                safe_float(row.get("stop_price") or row.get("stop_price_usdt")),
                8,
            ),
            "min_reward_risk": round(min_reward_risk, 6) if min_reward_risk > 0 else None,
            "max_stop_risk_pct": round(max_stop_risk_pct, 6) if max_stop_risk_pct > 0 else None,
        }
        if max_stop_override_reason:
            check["raw_max_stop_risk_pct"] = round(raw_max_stop_risk_pct, 6)
            check["max_stop_risk_override_reason"] = max_stop_override_reason
        if structure.get("status") != "ok":
            check.update({"status": "rejected", "reason": "policy_invalid_target_stop_structure"})
            checks.append(check)
            return {
                "version": "policy_target_stop_quality_v1",
                "rejected": True,
                "reason": "policy_invalid_target_stop_structure",
                "rule_id": rule_id,
                "checks": checks,
            }
        reward_risk = safe_float(structure.get("reward_risk"))
        stop_risk_pct = safe_float(structure.get("stop_risk_pct"))
        check.update(
            {
                "status": "ok",
                "reward_risk": round(reward_risk, 6),
                "stop_risk_pct": round(stop_risk_pct, 6),
            }
        )
        if min_reward_risk > 0 and not reward_risk_meets_minimum(
            reward_risk,
            min_reward_risk,
        ):
            check.update({"status": "rejected", "reason": "policy_min_reward_risk_not_met"})
            checks.append(check)
            return {
                "version": "policy_target_stop_quality_v1",
                "rejected": True,
                "reason": "policy_min_reward_risk_not_met",
                "rule_id": rule_id,
                "checks": checks,
            }
        if max_stop_risk_pct > 0 and stop_risk_pct - 1e-9 > max_stop_risk_pct:
            check.update({"status": "rejected", "reason": "policy_max_stop_risk_pct_exceeded"})
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
