from __future__ import annotations

from typing import Any

from tradecraft.services.kis_horizon import normalize_horizon

MANAGER_CLOSE_SIGNAL_EVENTS = {
    "exit_signal",
    "trim_review_due",
    "profit_lock_signal",
}
MANAGER_CLOSE_SIGNAL_REASONS = {
    "target_reached",
    "stop_reached",
    "profit_giveback",
}
MANAGER_CLOSE_ROW_TRIGGERS = {
    "target_reached",
    "stop_reached",
    "profit_giveback",
    "thesis_invalidated",
    "reconciliation_cleanup",
    "data_error",
    "manual_override",
    "operator_confirmed",
    "user_directive",
}
MANAGER_CLOSE_INVALIDATION_TOKENS = {
    "data_error",
    "invalid",
    "invalidated",
    "invalidation",
    "profit_giveback",
    "reconciliation",
    "risk_regime_break",
    "stop_reached",
    "target_reached",
    "thesis_broken",
    "trigger_broken",
    "manual_override",
    "operator_confirmed",
    "user_directive",
    "데이터 오류",
    "무효",
    "손절",
    "목표가",
    "익절",
    "논리 훼손",
    "리스크 전환",
    "수동 확인",
}
HORIZON_EARLY_CLOSE_MIN_AGE_SEC = {
    "mid": 72 * 60 * 60,
    "long": 14 * 24 * 60 * 60,
    "core_etf": 7 * 24 * 60 * 60,
}
PROFIT_LOCK_MIN_MFE_PCT = 8.0
PROFIT_LOCK_MIN_GIVEBACK_PCT = 4.0
PROFIT_LOCK_MIN_CURRENT_PNL_PCT = 2.0


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


def _safe_int(value: Any) -> int:
    return int(_safe_float(value))


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[: max(int(limit), 1)]


def exit_policy_for_block(block: dict[str, Any], reason: str) -> dict[str, str]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    horizon = normalize_horizon(metadata.get("horizon"))
    if reason in {"force_exit_requested", "manual_close"}:
        return {"action": "sell_all", "horizon": horizon}
    if horizon == "short":
        return {"action": "sell_all", "horizon": horizon}
    if horizon == "core_etf":
        return {"action": "manager_trim_review", "horizon": horizon}
    return {"action": "manager_review", "horizon": horizon}


def kis_sell_fill_update_plan(
    *,
    block: dict[str, Any],
    filled_qty: Any,
    order_status: str,
    now_iso: str,
) -> dict[str, Any]:
    filled = max(_safe_int(filled_qty), 0)
    remaining_open = max(_safe_int(block.get("qty_open")) - filled, 0)
    if str(order_status or "") == "filled" or remaining_open <= 0:
        return {
            "action": "closed",
            "remaining_open": 0,
            "update_fields": {
                "status": "closed",
                "qty_open": 0,
                "closed_at": now_iso,
                "force_exit_requested": 0,
                "llm_reason": "exit_filled_reconciled_by_order",
            },
        }
    if filled > 0:
        return {
            "action": "partial",
            "remaining_open": remaining_open,
            "update_fields": {
                "qty_open": remaining_open,
                "llm_reason": "partial_exit_reconciled",
            },
        }
    if str(order_status or "") == "canceled":
        return {
            "action": "canceled",
            "remaining_open": remaining_open,
            "update_fields": {
                "status": "open",
                "force_exit_requested": 0,
                "llm_reason": "exit_order_canceled",
            },
        }
    return {
        "action": "none",
        "remaining_open": remaining_open,
        "update_fields": {},
    }


def manager_close_guard(
    *,
    block: dict[str, Any] | None,
    row: dict[str, Any],
    quote: dict[str, Any],
    is_waiting_entry: bool,
    latest_signal: dict[str, Any],
    age_sec: float,
    min_age_by_horizon: dict[str, int] | None = None,
) -> dict[str, Any]:
    block_id = str(row.get("block_id") or "")
    if not block:
        return {
            "allowed": False,
            "reason": "block_missing",
            "block_id": block_id,
        }
    if is_waiting_entry:
        return {"allowed": True, "reason": "waiting_entry_cancel"}

    qty_open = max(_safe_int(block.get("qty_open")), 0)
    status = str(block.get("status") or "")
    if qty_open <= 0:
        return {"allowed": True, "reason": "ledger_cleanup_no_open_qty"}
    if status != "open":
        return {"allowed": True, "reason": f"non_open_status:{status}"}

    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    horizon = normalize_horizon(metadata.get("horizon"))
    if latest_signal:
        return {
            "allowed": True,
            "reason": "review_signal_present",
            "signal": latest_signal,
            "horizon": horizon,
        }

    price = _safe_float(quote.get("price"))
    target = _safe_float(block.get("target_price"))
    stop = _safe_float(block.get("stop_price"))
    if target > 0 and price >= target:
        return {
            "allowed": True,
            "reason": "target_touched_now",
            "horizon": horizon,
            "price": price,
        }
    if stop > 0 and price <= stop:
        return {
            "allowed": True,
            "reason": "stop_touched_now",
            "horizon": horizon,
            "price": price,
        }

    min_age_sec = (min_age_by_horizon or HORIZON_EARLY_CLOSE_MIN_AGE_SEC).get(
        horizon,
        0,
    )
    if min_age_sec > 0 and age_sec < min_age_sec:
        return {
            "allowed": False,
            "reason": "horizon_patience_guard",
            "block_id": block_id,
            "horizon": horizon,
            "age_sec": round(max(float(age_sec), 0.0), 3),
            "min_age_sec": min_age_sec,
            "price": price,
            "target_price": target,
            "stop_price": stop,
            "manager_reason": _clean_text(row.get("reason"), limit=500),
        }

    row_signal = manager_close_row_signal(row)
    if row_signal:
        return {
            "allowed": True,
            "reason": row_signal["reason"],
            "horizon": horizon,
            "age_sec": round(max(float(age_sec), 0.0), 3),
            "min_age_sec": min_age_sec,
            "row_signal": row_signal,
        }
    return {
        "allowed": False,
        "reason": "manager_close_requires_invalidation",
        "block_id": block_id,
        "horizon": horizon,
        "age_sec": round(max(float(age_sec), 0.0), 3),
        "min_age_sec": min_age_sec,
        "price": price,
        "target_price": target,
        "stop_price": stop,
        "manager_reason": _clean_text(row.get("reason"), limit=500),
        "required": sorted(MANAGER_CLOSE_ROW_TRIGGERS),
    }


def rule_exit_trigger_for_block(
    block: dict[str, Any],
    quote: dict[str, Any],
) -> dict[str, Any]:
    price = _safe_float(quote.get("price"))
    if price <= 0:
        return {"status": "no_price"}

    target = _safe_float(block.get("target_price"))
    stop = _safe_float(block.get("stop_price"))
    invalid_structure_reason = ""
    if target <= 0 or stop <= 0:
        invalid_structure_reason = "target_or_stop_missing"
    elif target <= stop:
        invalid_structure_reason = "target_not_above_stop"
    if invalid_structure_reason:
        return {
            "status": "invalid_price_structure",
            "detail": invalid_structure_reason,
            "payload": {
                "reason": invalid_structure_reason,
                "current_price": price,
                "target_price": target or None,
                "stop_price": stop or None,
            },
        }

    reason = ""
    if block.get("force_exit_requested"):
        reason = "force_exit_requested"
    elif target > 0 and price >= target:
        reason = "target_reached"
    elif stop > 0 and price <= stop:
        reason = "stop_reached"
    if not reason:
        return {
            "status": "no_trigger",
            "price": price,
            "target_price": target,
            "stop_price": stop,
        }
    return {
        "status": "triggered",
        "reason": reason,
        "price": price,
        "target_price": target,
        "stop_price": stop,
    }


def should_emit_profit_lock_signal(performance: dict[str, Any]) -> bool:
    return (
        _safe_float(performance.get("mfe_pct")) >= PROFIT_LOCK_MIN_MFE_PCT
        and _safe_float(performance.get("giveback_pct"))
        >= PROFIT_LOCK_MIN_GIVEBACK_PCT
        and _safe_float(performance.get("current_pnl_pct"))
        >= PROFIT_LOCK_MIN_CURRENT_PNL_PCT
    )


def manager_close_row_signal(row: dict[str, Any]) -> dict[str, Any]:
    if any(
        bool(row.get(key))
        for key in (
            "manual",
            "operator_confirmed",
            "user_directive",
            "user_confirmed",
        )
    ):
        return {"reason": "operator_confirmed", "source": "explicit_flag"}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    close_trigger = str(
        row.get("close_trigger") or metadata.get("close_trigger") or ""
    ).strip()
    if close_trigger in MANAGER_CLOSE_ROW_TRIGGERS:
        return {"reason": close_trigger, "source": "close_trigger"}
    parts = [
        str(row.get("reason") or ""),
        str(row.get("risk_note") or ""),
        str(row.get("decision_class") or ""),
        str(row.get("what_would_change_my_mind") or ""),
        str(metadata.get("reason") or ""),
        str(metadata.get("risk_note") or ""),
        str(metadata.get("invalidation") or ""),
    ]
    text = " ".join(parts).lower()
    for token in MANAGER_CLOSE_INVALIDATION_TOKENS:
        if token.lower() in text:
            return {"reason": "text_invalidation_signal", "source": token}
    return {}
