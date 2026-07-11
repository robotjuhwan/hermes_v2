from __future__ import annotations

import math
from typing import Any

from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    explicit_market_scope,
)
from tradecraft.services.jue_wiki_contract import WikiDecisionGateV1


_SIZE_FIELDS = (
    "qty", "quantity", "qty_open", "qty_initial", "target_qty",
    "target_quantity", "new_qty", "size", "position_size", "position_qty",
)
_KIS_NOTIONAL_FIELDS = (
    "notional", "notional_krw", "target_notional", "target_notional_krw",
    "quote_budget_krw", "max_notional_krw", "target_block_value_krw",
)
_USDT_NOTIONAL_FIELDS = (
    "notional_usdt", "target_notional_usdt", "quote_budget_usdt",
    "max_notional_usdt", "risk_budget_usdt",
)
_KRW_NOTIONAL_FIELDS = (
    "notional_krw", "target_notional_krw", "quote_budget_krw",
    "max_notional_krw", "risk_budget_krw",
)
_NATIVE_NOTIONAL_FIELDS = ("notional", "target_notional")
_LEVERAGE_FIELDS = ("leverage", "target_leverage", "new_leverage")
_IDENTITY_MAX_CHARS = 120


def _safe_float(value: Any) -> float:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _trusted_gate(
    gate: WikiDecisionGateV1 | dict[str, Any] | None,
    *,
    trusted_read_mode: str,
) -> dict[str, Any]:
    if trusted_read_mode != "required":
        return {
            "allow_new_risk": True,
            "allow_exit_actions": True,
            "reason": "wiki_context_advisory",
            "read_mode": trusted_read_mode,
            "snapshot_id": "",
            "version": "wiki_decision_gate_v1",
        }
    payload = (
        gate.to_dict()
        if isinstance(gate, WikiDecisionGateV1)
        else dict(gate)
        if isinstance(gate, dict)
        else {}
    )
    if not payload:
        reason = "wiki_required_gate_missing"
    elif payload.get("version") != "wiki_decision_gate_v1":
        reason = "wiki_required_gate_invalid:version"
    elif payload.get("read_mode") != "required":
        reason = "wiki_required_gate_invalid:read_mode"
    elif type(payload.get("allow_new_risk")) is not bool:
        reason = "wiki_required_gate_invalid:allow_new_risk"
    elif payload.get("allow_exit_actions") is not True:
        reason = "wiki_required_gate_invalid:allow_exit_actions"
    elif not isinstance(payload.get("reason"), str) or not payload.get("reason"):
        reason = "wiki_required_gate_invalid:reason"
    elif len(payload["reason"]) > _IDENTITY_MAX_CHARS:
        reason = "wiki_required_gate_invalid:reason"
    elif not isinstance(payload.get("snapshot_id"), str):
        reason = "wiki_required_gate_invalid:snapshot_id"
    elif len(payload["snapshot_id"]) > _IDENTITY_MAX_CHARS:
        reason = "wiki_required_gate_invalid:snapshot_id"
    elif payload.get("allow_new_risk") is True and payload.get("reason") != "wiki_context_eligible":
        reason = "wiki_required_gate_invalid:reason"
    elif payload.get("allow_new_risk") is False and not str(payload.get("reason")).startswith("wiki_required_"):
        reason = "wiki_required_gate_invalid:reason"
    elif payload.get("allow_new_risk") is True and not str(payload.get("snapshot_id") or "").strip():
        reason = "wiki_required_gate_invalid:snapshot_id"
    else:
        return {
            "allow_new_risk": payload["allow_new_risk"],
            "allow_exit_actions": True,
            "reason": str(payload["reason"]),
            "read_mode": "required",
            "snapshot_id": str(payload.get("snapshot_id") or ""),
            "version": "wiki_decision_gate_v1",
        }
    return {
        "allow_new_risk": False,
        "allow_exit_actions": True,
        "reason": reason,
        "read_mode": "required",
        "snapshot_id": "",
        "version": "wiki_decision_gate_v1",
    }


def _block_index(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return {str(key): row for key, row in value.items() if isinstance(row, dict)}
    return {
        str(row.get("block_id") or ""): row
        for row in list(value or [])
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }


def _numeric_aliases(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[bool, tuple[float, ...], bool]:
    values: list[float] = []
    invalid = False
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, bool):
            invalid = True
            continue
        try:
            parsed = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            invalid = True
            continue
        if not math.isfinite(parsed) or parsed < 0:
            invalid = True
            continue
        values.append(parsed)
    return bool(values) or invalid, tuple(values), invalid


def _alias_increases(row: dict[str, Any], keys: tuple[str, ...], *, current: float) -> bool:
    present, values, invalid = _numeric_aliases(row, keys)
    if not present:
        return False
    if invalid or not values:
        return True
    first = values[0]
    if any(not math.isclose(value, first, rel_tol=1e-9, abs_tol=1e-12) for value in values[1:]):
        return True
    return first > current if current > 0 else first > 0


def _current_alias(row: dict[str, Any], keys: tuple[str, ...], *, fallback: float = 0.0) -> tuple[float, bool]:
    present, values, invalid = _numeric_aliases(row, keys)
    if invalid:
        return 0.0, True
    if not present:
        return fallback, False
    first = values[0]
    return first, any(not math.isclose(value, first, rel_tol=1e-9, abs_tol=1e-12) for value in values[1:])


def kis_update_adds_new_risk(row: dict[str, Any], current: dict[str, Any]) -> bool:
    size = next((_safe_float(current.get(key)) for key in ("qty_open", "qty", "quantity", "qty_initial") if current.get(key) not in (None, "")), 0.0)
    notional = next((_safe_float(current.get(key)) for key in ("notional_krw", "notional", "target_notional_krw", "target_block_value_krw") if current.get(key) not in (None, "")), size * _safe_float(current.get("entry_price")))
    return _alias_increases(row, _SIZE_FIELDS, current=size) or _alias_increases(row, _KIS_NOTIONAL_FIELDS, current=notional)


def binance_update_adds_new_risk(row: dict[str, Any], current: dict[str, Any]) -> bool:
    size = next((_safe_float(current.get(key)) for key in ("qty_open", "qty", "quantity", "qty_initial") if current.get(key) not in (None, "")), 0.0)
    current_market = explicit_market_scope(current.get("market")) if current.get("market") else ""
    action_market = explicit_market_scope(row.get("market")) if row.get("market") else ""
    market = action_market or current_market
    invalid_market = bool((current.get("market") and not current_market) or (row.get("market") and not action_market) or (current_market and action_market and current_market != action_market))
    row_has_notional = any(key in row for key in (*_USDT_NOTIONAL_FIELDS, *_KRW_NOTIONAL_FIELDS, *_NATIVE_NOTIONAL_FIELDS))
    native_relevant = any(key in row or key in current for key in _NATIVE_NOTIONAL_FIELDS)
    if row_has_notional and (invalid_market or (native_relevant and not market)):
        return True
    usdt = (*_USDT_NOTIONAL_FIELDS, *_NATIVE_NOTIONAL_FIELDS) if market in {"spot", "futures"} else _USDT_NOTIONAL_FIELDS
    krw = (*_KRW_NOTIONAL_FIELDS, *_NATIVE_NOTIONAL_FIELDS) if market == UPBIT_SPOT_MARKET else _KRW_NOTIONAL_FIELDS
    native = size * _safe_float(current.get("entry_price"))
    current_usdt, usdt_invalid = _current_alias(current, usdt, fallback=native if market in {"spot", "futures"} else 0.0)
    current_krw, krw_invalid = _current_alias(current, krw, fallback=native if market == UPBIT_SPOT_MARKET else 0.0)
    return (
        _alias_increases(row, _LEVERAGE_FIELDS, current=_safe_float(current.get("leverage")))
        or _alias_increases(row, _SIZE_FIELDS, current=size)
        or _alias_increases(row, usdt, current=current_usdt)
        or _alias_increases(row, krw, current=current_krw)
        or (row_has_notional and (usdt_invalid or krw_invalid))
    )


def _apply(
    actions: dict[str, Any],
    gate: WikiDecisionGateV1 | dict[str, Any] | None,
    *,
    venue: str,
    trusted_read_mode: str,
    current_blocks: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    gate_payload = _trusted_gate(gate, trusted_read_mode=trusted_read_mode)
    filtered = {
        key: [dict(row) if isinstance(row, dict) else row for row in value]
        if isinstance(value, list)
        else value
        for key, value in actions.items()
    }
    index = _block_index(current_blocks)
    suppressed: list[dict[str, Any]] = []
    if trusted_read_mode == "required" and gate_payload.get("allow_new_risk") is False:
        kept: list[Any] = []
        classifier = kis_update_adds_new_risk if venue == "kis" else binance_update_adds_new_risk
        for row in list(filtered.get("update_blocks") or []):
            if not isinstance(row, dict):
                kept.append(row)
                continue
            block_id = str(row.get("block_id") or "")
            if not classifier(row, index.get(block_id, {})):
                kept.append(row)
                continue
            suppressed.append({"venue": venue, "action_kind": "update_blocks", "symbol": str(row.get("symbol") or index.get(block_id, {}).get("symbol") or "")[:_IDENTITY_MAX_CHARS], "block_id": block_id[:_IDENTITY_MAX_CHARS], "snapshot_id": str(gate_payload.get("snapshot_id") or ""), "read_mode": str(gate_payload.get("read_mode") or ""), "reason": str(gate_payload.get("reason") or "")})
        filtered["update_blocks"] = kept
        for row in list(filtered.get("create_blocks") or []):
            payload = row if isinstance(row, dict) else {}
            suppressed.append({"venue": venue, "action_kind": "create_blocks", "symbol": str(payload.get("symbol") or "")[:_IDENTITY_MAX_CHARS], "block_id": str(payload.get("block_id") or "")[:_IDENTITY_MAX_CHARS], "snapshot_id": str(gate_payload.get("snapshot_id") or ""), "read_mode": str(gate_payload.get("read_mode") or ""), "reason": str(gate_payload.get("reason") or "")})
        filtered["create_blocks"] = []
    original = sum(len(value) for value in actions.values() if isinstance(value, list))
    final = sum(len(value) for value in filtered.values() if isinstance(value, list))
    return filtered, {
        "venue": venue,
        "snapshot_id": str(gate_payload.get("snapshot_id") or ""),
        "read_mode": str(gate_payload.get("read_mode") or ""),
        "reason": str(gate_payload.get("reason") or ""),
        "original_action_count": original,
        "filtered_action_count": final,
        "suppressed_new_risk_count": len(suppressed),
        "suppressed_actions": suppressed,
    }


def apply_kis_wiki_decision_gate(actions: dict[str, Any], gate: WikiDecisionGateV1 | dict[str, Any] | None, *, trusted_read_mode: str = "shadow", current_blocks: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    return _apply(actions, gate, venue="kis", trusted_read_mode=trusted_read_mode, current_blocks=current_blocks)


def apply_binance_wiki_decision_gate(actions: dict[str, Any], gate: WikiDecisionGateV1 | dict[str, Any] | None, *, trusted_read_mode: str = "shadow", current_blocks: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    return _apply(actions, gate, venue="binance", trusted_read_mode=trusted_read_mode, current_blocks=current_blocks)
