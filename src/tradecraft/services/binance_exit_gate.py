from __future__ import annotations

import math
import re
from typing import Any

UPBIT_SPOT_MARKET = "upbit_spot"
ALLOWED_MARKETS = {"spot", "futures", UPBIT_SPOT_MARKET}
SPOT_ADOPTION_MIN_NOTIONAL_USDT = 5.0


def binance_exit_reason(block: dict[str, Any], price: float) -> str | None:
    if block.get("force_exit_requested"):
        return "force_exit_requested"
    side = normalize_position_side(block.get("side"))
    target = safe_float(block.get("target_price"))
    stop = safe_float(block.get("stop_price"))
    if side == "short":
        if target > 0 and price <= target:
            return "target_reached"
        if stop > 0 and price >= stop:
            return "stop_reached"
        return None
    if target > 0 and price >= target:
        return "target_reached"
    if stop > 0 and price <= stop:
        return "stop_reached"
    return None


def binance_exit_order_side(block: dict[str, Any]) -> str:
    market = normalize_market(block.get("market"))
    if market in {"spot", UPBIT_SPOT_MARKET}:
        return "sell"
    return "buy" if normalize_position_side(block.get("side")) == "short" else "sell"


def remaining_exit_qty(
    *,
    requested_qty: float,
    filled_qty: float,
    price: float,
    min_notional: float = SPOT_ADOPTION_MIN_NOTIONAL_USDT,
) -> float:
    remaining = max(float(requested_qty) - float(filled_qty), 0.0)
    if price > 0 and remaining * price < float(min_notional):
        return 0.0
    return remaining


def favorable_r_multiple(
    *,
    entry_price: float,
    stop_price: float,
    price: float,
    side: Any,
) -> float:
    entry = safe_float(entry_price)
    stop = safe_float(stop_price)
    current = safe_float(price)
    if entry <= 0 or stop <= 0 or current <= 0:
        return 0.0
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if normalize_position_side(side) == "short":
        return (entry - current) / risk
    return (current - entry) / risk


def partial_profit_quantity_plan(
    *,
    qty_open: float,
    price: float,
    fraction: float,
    market: Any,
    min_notional: float,
) -> dict[str, Any]:
    qty = safe_float(qty_open)
    current = safe_float(price)
    normalized_market = normalize_market(market)
    bounded_fraction = min(max(safe_float(fraction), 0.0), 0.95)
    if qty <= 0 or current <= 0 or bounded_fraction <= 0:
        return {"status": "skip", "reason": "invalid_qty_price_or_fraction"}

    partial_qty = qty * bounded_fraction
    original_partial_qty = partial_qty
    remaining_qty = max(qty - partial_qty, 0.0)
    if partial_qty <= 0 or remaining_qty <= 0:
        return {"status": "skip", "reason": "invalid_partial_split"}

    full_exit_for_min_notional = False
    exit_mode = "partial"
    minimum = max(safe_float(min_notional), 0.0)
    if minimum > 0 and (
        partial_qty * current < minimum or remaining_qty * current < minimum
    ):
        if normalized_market == "futures" or (
            normalized_market in {"spot", UPBIT_SPOT_MARKET}
            and qty * current >= minimum
        ):
            partial_qty = qty
            remaining_qty = 0.0
            full_exit_for_min_notional = True
            exit_mode = "full_exit_min_notional"
        elif normalized_market in {"spot", UPBIT_SPOT_MARKET}:
            return {"status": "skip", "reason": "spot_position_below_min_notional"}

    return {
        "status": "ok",
        "partial_qty": partial_qty,
        "original_partial_qty": original_partial_qty,
        "remaining_qty": remaining_qty,
        "full_exit_for_min_notional": full_exit_for_min_notional,
        "exit_mode": exit_mode,
    }


def partial_profit_full_exit_retry_decision(
    *,
    market: Any,
    full_exit_for_min_notional: bool,
    requested_qty: float,
    qty_open: float,
    error_message: str = "",
    response_is_min_notional_error: bool = False,
    response_error_message: str = "",
    fallback_error_message: str = "",
) -> dict[str, Any]:
    normalized_market = normalize_market(market)
    requested = safe_float(requested_qty)
    open_qty = safe_float(qty_open)
    if normalized_market != "futures":
        return {"status": "skip", "reason": "market_not_futures"}
    if full_exit_for_min_notional:
        return {"status": "skip", "reason": "already_full_exit_for_min_notional"}
    if requested <= 0 or open_qty <= 0 or requested >= open_qty:
        return {"status": "skip", "reason": "not_partial_exit"}

    message = str(error_message or "")
    min_notional_text_match = (
        "below minimum" in message.lower() or "below min" in message.lower()
    )
    if not min_notional_text_match and not response_is_min_notional_error:
        return {"status": "skip", "reason": "not_min_notional_error"}

    retry_reason = str(
        response_error_message or message or fallback_error_message or "min_notional_error"
    )
    return {
        "status": "retry",
        "retry_qty": open_qty,
        "exit_mode": "full_exit_min_notional_retry",
        "retry_reason": retry_reason,
    }


def partial_profit_full_exit_retry_state_plan(
    *,
    metadata: dict[str, Any],
    retry_decision: dict[str, Any],
    fallback_error_message: str = "",
) -> dict[str, Any]:
    if str(retry_decision.get("status") or "") != "retry":
        return {
            "status": "skip",
            "reason": str(retry_decision.get("reason") or "not_retry"),
        }
    retry_qty = safe_float(retry_decision.get("retry_qty"))
    if retry_qty <= 0:
        return {"status": "skip", "reason": "invalid_retry_qty"}
    updated_metadata = dict(metadata)
    updated_metadata["partial_profit_retry_reason"] = str(
        retry_decision.get("retry_reason")
        or fallback_error_message
        or "min_notional_error"
    )
    return {
        "status": "retry",
        "retry_qty": retry_qty,
        "remaining_qty": 0.0,
        "full_exit_for_min_notional": True,
        "exit_mode": str(
            retry_decision.get("exit_mode") or "full_exit_min_notional_retry"
        ),
        "metadata": updated_metadata,
    }


def partial_profit_trigger_plan(
    *,
    block: dict[str, Any],
    price: float,
    weak_lane_context: dict[str, Any] | None,
    weak_trigger_r: float,
    weak_trigger_source: str,
    entry_quality_repair_context: dict[str, Any] | None,
    global_repair_context: dict[str, Any] | None,
    base_fraction: float,
    distressed_fraction_context: dict[str, Any] | None,
    distressed_fraction: float,
) -> dict[str, Any]:
    metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
    if metadata.get("partial_profit_taken_at"):
        return {"status": "skip", "reason": "partial_profit_already_taken"}
    if str(block.get("status") or "") != "open":
        return {"status": "skip", "reason": "block_not_open"}
    if block.get("force_exit_requested"):
        return {"status": "skip", "reason": "force_exit_requested"}

    weak_context = weak_lane_context if isinstance(weak_lane_context, dict) else {}
    repair_context: dict[str, Any] = {}
    if bool(weak_context.get("matched")):
        trigger_r = max(safe_float(weak_trigger_r), 0.0)
        trigger_source = str(weak_trigger_source or "weak_performance_lane")
    else:
        entry_repair = (
            entry_quality_repair_context
            if isinstance(entry_quality_repair_context, dict)
            else {}
        )
        if bool(entry_repair.get("enabled")):
            repair_context = entry_repair
            trigger_source = "entry_quality_mfe_surrender_repair"
        else:
            global_repair = (
                global_repair_context if isinstance(global_repair_context, dict) else {}
            )
            if bool(global_repair.get("enabled")):
                repair_context = global_repair
                trigger_source = "mfe_surrender_repair"
            else:
                return {"status": "skip", "reason": "partial_profit_trigger_disabled"}
        trigger_r = max(safe_float(repair_context.get("trigger_r")), 0.0)

    entry = safe_float(block.get("entry_price"))
    stop = safe_float(metadata.get("profit_lock_original_stop_price")) or safe_float(
        block.get("stop_price")
    )
    target = safe_float(block.get("target_price"))
    qty_open = safe_float(block.get("qty_open"))
    current = safe_float(price)
    if entry <= 0 or stop <= 0 or target <= 0 or current <= 0 or qty_open <= 0:
        return {"status": "skip", "reason": "invalid_price_or_quantity"}
    risk = abs(entry - stop)
    if risk <= 0:
        return {"status": "skip", "reason": "invalid_risk"}

    side = normalize_position_side(block.get("side"))
    favorable_r = favorable_r_multiple(
        entry_price=entry,
        stop_price=stop,
        price=current,
        side=side,
    )
    if trigger_r <= 0:
        return {"status": "skip", "reason": "invalid_trigger_r"}
    if favorable_r < trigger_r:
        return {"status": "skip", "reason": "trigger_not_reached"}

    fraction = min(max(safe_float(base_fraction), 0.0), 0.95)
    fraction_source = "weak_lane_partial_profit_fraction"
    fraction_context: dict[str, Any] = {}
    distressed_context = (
        distressed_fraction_context
        if isinstance(distressed_fraction_context, dict)
        else {}
    )
    if bool(distressed_context.get("enabled")):
        override_fraction = min(max(safe_float(distressed_fraction), 0.0), 0.95)
        if override_fraction > fraction:
            fraction = override_fraction
            fraction_source = "distressed_entry_quality"
            fraction_context = distressed_context
    if fraction <= 0:
        return {"status": "skip", "reason": "invalid_fraction"}

    return {
        "status": "ok",
        "block_id": str(block.get("block_id") or ""),
        "symbol": str(block.get("symbol") or "").upper(),
        "market": normalize_market(block.get("market")),
        "side": side,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "qty_open": qty_open,
        "risk": risk,
        "favorable_r": favorable_r,
        "trigger_r": trigger_r,
        "trigger_source": trigger_source,
        "weak_lane_context": weak_context,
        "repair_context": repair_context,
        "fraction": fraction,
        "fraction_source": fraction_source,
        "fraction_context": fraction_context,
        "metadata": metadata,
    }


def partial_profit_quantity_unavailable_plan(
    *,
    metadata: dict[str, Any],
    message: str,
    block_id: str,
    symbol: str,
    market: str,
    order_side: str,
    requested_qty: float,
    quantity_context: dict[str, Any],
    price: float,
    now_iso: str,
) -> dict[str, Any]:
    clean_message = str(message or "partial exit quantity unavailable")
    updated_metadata = dict(metadata)
    updated_metadata["partial_profit_error_at"] = now_iso
    updated_metadata["partial_profit_error_message"] = clean_message
    payload = {
        "price": safe_float(price),
        "side": str(order_side or ""),
        "requested_qty": safe_float(requested_qty),
        "quantity_context": quantity_context,
    }
    result = {
        "status": "skipped",
        "block_id": str(block_id or ""),
        "symbol": str(symbol or "").upper(),
        "market": str(market or ""),
        "side": str(order_side or ""),
        "qty": 0.0,
        "requested_qty": safe_float(requested_qty),
        "quantity_context": quantity_context,
        "price": safe_float(price),
        "reason": "partial_profit_reached",
    }
    return {
        "update_fields": {
            "risk_note": clean_message,
            "metadata": updated_metadata,
        },
        "event": {
            "type": "partial_profit_skipped",
            "message": clean_message,
            "payload": payload,
        },
        "result": result,
    }


def partial_profit_unfilled_plan(
    *,
    metadata: dict[str, Any],
    error_message: str,
    status: str,
    block_id: str,
    symbol: str,
    market: str,
    order_side: str,
    qty: float,
    requested_qty: float,
    quantity_context: dict[str, Any],
    price: float,
    order: dict[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    clean_message = str(error_message or status or "partial exit failed")
    updated_metadata = dict(metadata)
    updated_metadata["partial_profit_error_at"] = now_iso
    updated_metadata["partial_profit_error_message"] = clean_message
    payload = {
        "price": safe_float(price),
        "side": str(order_side or ""),
        "qty": safe_float(qty),
        "requested_qty": safe_float(requested_qty),
        "quantity_context": quantity_context,
        "order": order,
    }
    result_status = str(status or "")
    if result_status == "sent":
        result_status = "unfilled"
    return {
        "update_fields": {
            "risk_note": f"partial profit not filled: {clean_message}",
            "metadata": updated_metadata,
        },
        "event": {
            "type": "partial_profit_not_filled",
            "message": "partial profit order did not fill",
            "payload": payload,
        },
        "result": {
            "status": result_status,
            "block_id": str(block_id or ""),
            "symbol": str(symbol or "").upper(),
            "market": str(market or ""),
            "side": str(order_side or ""),
            "qty": safe_float(qty),
            "requested_qty": safe_float(requested_qty),
            "quantity_context": quantity_context,
            "price": safe_float(price),
            "reason": "partial_profit_reached",
            "order": order,
        },
    }


def partial_profit_block_update_plan(
    *,
    metadata: dict[str, Any],
    qty_open: float,
    filled_qty: float,
    remaining_qty: float,
    price: float,
    favorable_r: float,
    trigger_r: float,
    trigger_source: str,
    fraction: float,
    fraction_source: str,
    exit_mode: str,
    min_notional: float,
    original_requested_qty: float,
    requested_qty: float,
    order_status: str,
    new_stop_price: float | None,
    original_stop_price: float,
    weak_lane_context: dict[str, Any] | None = None,
    repair_context: dict[str, Any] | None = None,
    fraction_context: dict[str, Any] | None = None,
    taken_at: str,
    close_epsilon: float = 0.00000001,
) -> dict[str, Any]:
    updated_metadata = dict(metadata)
    updated_metadata.update(
        {
            "partial_profit_taken_at": taken_at,
            "partial_profit_trigger_source": trigger_source,
            "partial_profit_trigger_r": safe_float(trigger_r),
            "partial_profit_favorable_r": safe_float(favorable_r),
            "partial_profit_fraction": safe_float(fraction),
            "partial_profit_fraction_source": fraction_source,
            "partial_profit_exit_mode": exit_mode,
            "partial_profit_min_notional": safe_float(min_notional),
            "partial_profit_original_requested_qty": safe_float(
                original_requested_qty
            ),
            "partial_profit_requested_qty": safe_float(requested_qty),
            "partial_profit_filled_qty": safe_float(filled_qty),
            "partial_profit_remaining_qty": safe_float(remaining_qty),
            "partial_profit_reference_price": safe_float(price),
            "partial_profit_order_status": order_status,
        }
    )
    weak_context = weak_lane_context if isinstance(weak_lane_context, dict) else {}
    if weak_context.get("source") == "runtime_scorecard":
        updated_metadata["runtime_weak_performance_lane"] = weak_context
    if repair_context:
        updated_metadata["partial_profit_repair_context"] = repair_context
    if fraction_context:
        updated_metadata["partial_profit_fraction_context"] = fraction_context

    update_fields: dict[str, Any] = {
        "qty_open": safe_float(remaining_qty),
        "llm_reason": "partial_profit_reached",
        "risk_note": (
            f"partial profit: favorable_r={safe_float(favorable_r):.3f}, "
            f"filled={safe_float(filled_qty):g}/{safe_float(qty_open):g}"
        ),
        "metadata": updated_metadata,
    }
    if new_stop_price is not None:
        update_fields["stop_price"] = safe_float(new_stop_price)
        updated_metadata.setdefault("profit_lock_triggered_at", taken_at)
        updated_metadata.setdefault("profit_lock_trigger_r", safe_float(trigger_r))
        updated_metadata.setdefault("profit_lock_trigger_source", trigger_source)
        updated_metadata.setdefault(
            "profit_lock_reference_price",
            safe_float(price),
        )
        updated_metadata.setdefault(
            "profit_lock_favorable_r",
            safe_float(favorable_r),
        )
        updated_metadata.setdefault(
            "profit_lock_original_stop_price",
            safe_float(original_stop_price),
        )
    if safe_float(remaining_qty) <= close_epsilon:
        update_fields.update(
            {
                "status": "closed",
                "qty_open": 0.0,
                "force_exit_requested": 0,
                "closed_at": taken_at,
            }
        )
    return {"metadata": updated_metadata, "update_fields": update_fields}


def partial_profit_success_plan(
    *,
    block_id: str,
    symbol: str,
    market: str,
    side: str,
    order_side: str,
    qty: float,
    requested_qty: float,
    filled_qty: float,
    remaining_qty: float,
    quantity_context: dict[str, Any],
    price: float,
    favorable_r: float,
    trigger_r: float,
    trigger_source: str,
    new_stop_price: float | None,
    order: dict[str, Any],
    block: dict[str, Any],
) -> dict[str, Any]:
    clean_block_id = str(block_id or "")
    clean_symbol = str(symbol or "").upper()
    clean_market = str(market or "")
    clean_side = str(side or "")
    clean_order_side = str(order_side or "")
    payload = {
        "price": safe_float(price),
        "side": clean_side,
        "order_side": clean_order_side,
        "qty": safe_float(qty),
        "requested_qty": safe_float(requested_qty),
        "filled_qty": safe_float(filled_qty),
        "remaining_qty": safe_float(remaining_qty),
        "favorable_r": safe_float(favorable_r),
        "trigger_r": safe_float(trigger_r),
        "new_stop_price": (
            None if new_stop_price is None else safe_float(new_stop_price)
        ),
        "order": order,
        "quantity_context": quantity_context,
    }
    return {
        "event": {
            "type": "partial_profit",
            "message": "weak lane partial profit taken",
            "payload": payload,
        },
        "result": {
            "status": "partial_profit_taken",
            "block_id": clean_block_id,
            "symbol": clean_symbol,
            "market": clean_market,
            "side": clean_side,
            "order_side": clean_order_side,
            "qty": safe_float(qty),
            "requested_qty": safe_float(requested_qty),
            "filled_qty": safe_float(filled_qty),
            "remaining_qty": safe_float(remaining_qty),
            "quantity_context": quantity_context,
            "price": safe_float(price),
            "favorable_r": safe_float(favorable_r),
            "trigger_r": safe_float(trigger_r),
            "trigger_source": str(trigger_source or ""),
            "reason": "partial_profit_reached",
            "order": order,
            "block": block,
        },
    }


def exit_retry_metadata(
    metadata: dict[str, Any],
    *,
    reason: str,
    cooldown_sec: int,
    retry_after_ts: float | None,
    now_iso: str,
) -> dict[str, Any]:
    updated_metadata = dict(metadata)
    cooldown = max(int(cooldown_sec), 0)
    updated_metadata["last_exit_retry_reason"] = clean_text(reason, limit=300)
    updated_metadata["last_exit_failure_at"] = now_iso
    updated_metadata["exit_retry_cooldown_sec"] = cooldown
    if cooldown > 0 and retry_after_ts is not None:
        updated_metadata["exit_retry_after_ts"] = safe_float(retry_after_ts)
    else:
        updated_metadata.pop("exit_retry_after_ts", None)
    return updated_metadata


def exit_quantity_unavailable_plan(
    *,
    metadata: dict[str, Any],
    message: str,
    price: float,
    side: str,
    requested_qty: float,
    quantity_context: dict[str, Any],
    reason: str,
    now_iso: str,
    retry_cooldown_sec: int,
    retry_after_ts: float | None,
) -> dict[str, Any]:
    del reason
    clean_message = str(message or "exit quantity unavailable")
    event_payload = {
        "price": safe_float(price),
        "side": side,
        "requested_qty": safe_float(requested_qty),
        "quantity_context": quantity_context,
    }
    if quantity_context.get("reconciliation_error"):
        updated_metadata = dict(metadata)
        updated_metadata["exit_reconciliation_error"] = {
            **event_payload,
            "message": clean_message,
            "detected_at": now_iso,
        }
        return {
            "status": "reconciliation_error",
            "update_fields": {
                "status": "error",
                "force_exit_requested": 0,
                "risk_note": clean_message,
                "metadata": updated_metadata,
            },
            "event": {
                "type": "exit_reconciliation_error",
                "message": clean_message,
                "payload": event_payload,
            },
        }

    if quantity_context.get("close_as_dust"):
        return {
            "status": "closed_as_dust",
            "update_fields": {
                "status": "closed",
                "qty_open": 0.0,
                "force_exit_requested": 0,
                "closed_at": now_iso,
                "risk_note": clean_message,
            },
            "event": {
                "type": "exit_closed_as_dust",
                "message": clean_message,
                "payload": event_payload,
            },
        }

    return {
        "status": "skipped",
        "update_fields": {
            "status": "open",
            "force_exit_requested": 1,
            "risk_note": clean_message,
            "metadata": exit_retry_metadata(
                metadata,
                reason=clean_message,
                cooldown_sec=retry_cooldown_sec,
                retry_after_ts=retry_after_ts,
                now_iso=now_iso,
            ),
        },
        "event": {
            "type": "exit_skipped",
            "message": clean_message,
            "payload": event_payload,
        },
    }


def exit_reconciliation_error_plan(
    *,
    metadata: dict[str, Any],
    error_message: str,
    price: float,
    side: str,
    requested_qty: float,
    order_qty: float,
    quantity_context: dict[str, Any],
    order: dict[str, Any],
    detected_at: str,
) -> dict[str, Any]:
    clean_message = str(error_message or "unknown")
    clean_side = str(side or "")
    event_payload = {
        "price": safe_float(price),
        "side": clean_side,
        "requested_qty": safe_float(requested_qty),
        "order_qty": safe_float(order_qty),
        "quantity_context": quantity_context,
        "order": order,
    }
    updated_metadata = dict(metadata)
    updated_metadata["exit_reconciliation_error"] = {
        "message": clean_message,
        "price": safe_float(price),
        "side": clean_side,
        "requested_qty": safe_float(requested_qty),
        "order_qty": safe_float(order_qty),
        "quantity_context": quantity_context,
        "detected_at": str(detected_at or ""),
    }
    return {
        "update_fields": {
            "status": "error",
            "force_exit_requested": 0,
            "risk_note": clean_message,
            "metadata": updated_metadata,
        },
        "event": {
            "type": "exit_reconciliation_error",
            "message": clean_message,
            "payload": event_payload,
        },
    }


def exit_fill_update_plan(
    *,
    metadata: dict[str, Any],
    status: str,
    response_status: str,
    reason: str,
    requested_qty: float,
    order_qty: float,
    filled_qty: float,
    remaining_qty: float,
    retry_reason: str,
    retry_cooldown_sec: int,
    retry_after_ts: float | None,
    now_iso: str,
    close_epsilon: float = 0.00000001,
) -> dict[str, Any]:
    normalized_status = str(status or "")
    normalized_response_status = str(response_status or "").upper()
    update_fields: dict[str, Any] = {"llm_reason": reason}
    remaining = safe_float(remaining_qty)

    def retry_metadata(retry_text: str) -> dict[str, Any]:
        return exit_retry_metadata(
            metadata,
            reason=retry_text,
            cooldown_sec=retry_cooldown_sec,
            retry_after_ts=retry_after_ts,
            now_iso=now_iso,
        )

    if normalized_status == "paper" or normalized_response_status == "FILLED":
        if normalized_status != "paper" and remaining > close_epsilon:
            update_fields.update(
                {
                    "status": "open",
                    "qty_open": remaining,
                    "force_exit_requested": 1,
                    "risk_note": (
                        f"partial exit fill after balance clamp: "
                        f"{safe_float(filled_qty)}/{safe_float(requested_qty)}"
                    ),
                    "metadata": retry_metadata(retry_reason),
                }
            )
        else:
            update_fields.update(
                {
                    "status": "closed",
                    "qty_open": 0.0,
                    "force_exit_requested": 0,
                    "closed_at": now_iso,
                }
            )
        return {"update_fields": update_fields}

    if normalized_response_status == "PARTIALLY_FILLED" and remaining <= close_epsilon:
        update_fields.update(
            {
                "status": "closed",
                "qty_open": 0.0,
                "force_exit_requested": 0,
                "closed_at": now_iso,
            }
        )
    elif normalized_response_status == "PARTIALLY_FILLED":
        update_fields.update(
            {
                "status": "open",
                "qty_open": remaining,
                "force_exit_requested": 1,
                "risk_note": (
                    f"partial exit fill: "
                    f"{safe_float(filled_qty)}/{safe_float(order_qty)}"
                ),
                "metadata": retry_metadata(retry_reason),
            }
        )
    else:
        retry_text = str(retry_reason or normalized_response_status or normalized_status)
        update_fields.update(
            {
                "status": "open",
                "force_exit_requested": 1,
                "risk_note": f"exit order not filled; retry armed: {retry_text}",
                "metadata": retry_metadata(retry_text),
            }
        )
    return {"update_fields": update_fields}


def entry_quality_loss_tighten_plan(
    *,
    metadata: dict[str, Any],
    block_id: str,
    symbol: str,
    market: str,
    side: str,
    entry_quality: str,
    entry_quality_lane: str,
    trigger_r: float,
    unfavorable_r: float,
    price: float,
    old_stop_price: float,
    new_stop_price: float,
    original_stop_price: float,
    quality_card: dict[str, Any],
    min_samples: int,
    now_iso: str,
) -> dict[str, Any]:
    clean_market = normalize_market(market)
    clean_symbol = str(symbol or "").upper()
    clean_side = normalize_position_side(side)
    clean_lane = str(
        entry_quality_lane or f"{clean_market}:{clean_side}:{entry_quality}"
    ).strip()
    context = {
        "version": "binance_entry_quality_loss_tighten_v1",
        "entry_quality_lane": clean_lane,
        "entry_quality": str(entry_quality or ""),
        "trigger_r": safe_float(trigger_r),
        "unfavorable_r": round(safe_float(unfavorable_r), 6),
        "reference_price": safe_float(price),
        "old_stop_price": safe_float(old_stop_price),
        "new_stop_price": safe_float(new_stop_price),
        "original_stop_price": safe_float(original_stop_price),
        "sample_count": int(safe_float(quality_card.get("sample_count"))),
        "min_samples": max(int(min_samples), 0),
        "pnl_usdt": safe_float(quality_card.get("pnl_usdt")),
        "win_rate_pct": safe_float(quality_card.get("win_rate_pct")),
        "avg_r_multiple": safe_float(quality_card.get("avg_r_multiple")),
        "profit_factor": safe_float(quality_card.get("profit_factor")),
        "tightened_at": str(now_iso or ""),
    }
    updated_metadata = dict(metadata)
    updated_metadata["entry_quality_loss_tighten"] = context
    updated_metadata["entry_quality_loss_tighten_original_stop_price"] = safe_float(
        original_stop_price
    )
    return {
        "context": context,
        "update_fields": {
            "stop_price": safe_float(new_stop_price),
            "llm_reason": "entry_quality_loss_tighten",
            "risk_note": (
                f"entry-quality loss tighten: {clean_lane} "
                f"unfavorable_r={safe_float(unfavorable_r):.3f}, "
                f"stop moved {safe_float(old_stop_price):g}->{safe_float(new_stop_price):g}"
            ),
            "metadata": updated_metadata,
        },
        "event": {
            "type": "entry_quality_loss_tighten",
            "message": "entry-quality loss stop tightened",
            "payload": context,
        },
        "result_fields": {
            "status": "entry_quality_loss_tightened",
            "block_id": str(block_id or ""),
            "symbol": clean_symbol,
            "market": clean_market,
            "side": clean_side,
            "price": safe_float(price),
            "old_stop_price": safe_float(old_stop_price),
            "new_stop_price": safe_float(new_stop_price),
            "unfavorable_r": safe_float(unfavorable_r),
            "trigger_r": safe_float(trigger_r),
            "entry_quality_lane": clean_lane,
            "reason": "entry_quality_loss_tighten",
        },
    }


def exit_quantity_unavailable_result_plan(
    *,
    status: str,
    block_id: str,
    symbol: str,
    market: str,
    order_side: str,
    requested_qty: float,
    quantity_context: dict[str, Any],
    price: float,
    reason: str,
    block: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "status": str(status or "skipped"),
        "block_id": str(block_id or ""),
        "symbol": str(symbol or "").upper(),
        "market": normalize_market(market),
        "side": str(order_side or ""),
        "qty": 0.0,
        "requested_qty": safe_float(requested_qty),
        "quantity_context": quantity_context,
        "price": safe_float(price),
        "reason": str(reason or ""),
        "block": block,
    }


def spot_exit_retry_update_plan(
    *,
    metadata: dict[str, Any],
    initial_error_message: str,
    price: float,
    side: str,
    requested_qty: float,
    failed_qty: float,
    retry_qty: float,
    filled_qty: float,
    remaining_qty: float,
    status: str,
    response_status: str,
    response: dict[str, Any],
    retry_context: dict[str, Any],
    initial_order: dict[str, Any],
    retry_order: dict[str, Any],
    reason: str,
    now_iso: str,
    retry_cooldown_sec: int,
    retry_after_ts: float | None,
    close_epsilon: float = 0.00000001,
) -> dict[str, Any]:
    updated_metadata = dict(metadata)
    updated_metadata["insufficient_balance_exit_retry"] = {
        "retried_at": now_iso,
        "initial_error_message": str(initial_error_message or ""),
        "initial_order_qty": safe_float(failed_qty),
        "retry_order_qty": safe_float(retry_qty),
        "filled_qty": safe_float(filled_qty),
        "remaining_qty": safe_float(remaining_qty),
        "status": str(status or ""),
        "response_status": str(response_status or ""),
    }
    update_fields: dict[str, Any] = {
        "llm_reason": str(reason or ""),
        "metadata": updated_metadata,
    }
    if safe_float(filled_qty) > 0 and safe_float(remaining_qty) <= close_epsilon:
        update_fields.update(
            {
                "status": "closed",
                "qty_open": 0.0,
                "force_exit_requested": 0,
                "closed_at": now_iso,
                "risk_note": (
                    f"spot exit retried with fresh sellable balance: "
                    f"{safe_float(retry_qty):g}/{safe_float(requested_qty):g}"
                ),
            }
        )
    elif safe_float(filled_qty) > 0:
        update_fields.update(
            {
                "status": "open",
                "qty_open": safe_float(remaining_qty),
                "force_exit_requested": 1,
                "risk_note": (
                    f"spot exit retry partially filled: "
                    f"{safe_float(filled_qty):g}/{safe_float(requested_qty):g}"
                ),
                "metadata": exit_retry_metadata(
                    updated_metadata,
                    reason="partial_fill_after_insufficient_balance_retry",
                    cooldown_sec=retry_cooldown_sec,
                    retry_after_ts=retry_after_ts,
                    now_iso=now_iso,
                ),
            }
        )
    else:
        retry_error = str(
            response.get("error_message") or response_status or status or "unknown"
        )
        updated_metadata["exit_reconciliation_error"] = {
            "message": retry_error,
            "initial_error_message": str(initial_error_message or ""),
            "price": safe_float(price),
            "side": str(side or ""),
            "requested_qty": safe_float(requested_qty),
            "order_qty": safe_float(retry_qty),
            "quantity_context": retry_context,
            "detected_at": now_iso,
        }
        update_fields.update(
            {
                "status": "error",
                "force_exit_requested": 0,
                "risk_note": retry_error,
                "metadata": updated_metadata,
            }
        )
    return {
        "update_fields": update_fields,
        "event": {
            "type": "insufficient_balance_exit_retry",
            "message": "spot exit retried with fresh lower sellable balance",
            "payload": {
                "price": safe_float(price),
                "side": str(side or ""),
                "requested_qty": safe_float(requested_qty),
                "failed_qty": safe_float(failed_qty),
                "retry_qty": safe_float(retry_qty),
                "filled_qty": safe_float(filled_qty),
                "remaining_qty": safe_float(remaining_qty),
                "initial_order": initial_order,
                "retry_order": retry_order,
                "quantity_context": retry_context,
            },
        },
    }


def exit_success_plan(
    *,
    status: str,
    block_id: str,
    symbol: str,
    market: str,
    order_side: str,
    qty: float,
    requested_qty: float,
    quantity_context: dict[str, Any],
    price: float,
    reason: str,
    order: dict[str, Any],
    block: dict[str, Any] | None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "status": str(status or ""),
        "block_id": str(block_id or ""),
        "symbol": str(symbol or "").upper(),
        "market": normalize_market(market),
        "side": str(order_side or ""),
        "qty": safe_float(qty),
        "requested_qty": safe_float(requested_qty),
        "quantity_context": quantity_context,
        "price": safe_float(price),
        "reason": str(reason or ""),
        "order": order,
        "block": block,
    }
    if isinstance(extra_fields, dict):
        result.update(extra_fields)
    return result


def profit_lock_stop_price(
    *,
    side: Any,
    entry_price: float,
    stop_price: float,
    risk: float,
    price: float,
    lock_r: float,
    estimated_cost_pct: float,
    min_net_buffer_pct: float,
    default_cost_pct: float,
) -> float | None:
    plan = profit_lock_stop_plan(
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        risk=risk,
        price=price,
        lock_r=lock_r,
        estimated_cost_pct=estimated_cost_pct,
        min_net_buffer_pct=min_net_buffer_pct,
        default_cost_pct=default_cost_pct,
    )
    if plan.get("status") != "ok":
        return None
    return safe_float(plan.get("stop_price"))


def profit_lock_stop_plan(
    *,
    side: Any,
    entry_price: float,
    stop_price: float,
    risk: float,
    price: float,
    lock_r: float,
    estimated_cost_pct: float,
    min_net_buffer_pct: float,
    default_cost_pct: float,
) -> dict[str, Any]:
    entry = safe_float(entry_price)
    stop = safe_float(stop_price)
    risk_value = safe_float(risk)
    current = safe_float(price)

    lock_multiple = max(safe_float(lock_r), 0.0)
    cost_pct = safe_float(estimated_cost_pct)
    if cost_pct <= 0:
        cost_pct = max(safe_float(default_cost_pct), 0.0)
    buffer_pct = max(safe_float(min_net_buffer_pct), 0.0)
    cost_floor_pct = max(
        cost_pct + buffer_pct,
        0.0,
    )

    base = {
        "cost_floor_pct": cost_floor_pct,
        "estimated_round_trip_cost_pct": cost_pct,
        "min_net_buffer_pct": buffer_pct,
        "lock_r": lock_multiple,
        "side": normalize_position_side(side),
    }
    if entry <= 0 or stop <= 0 or risk_value <= 0 or current <= 0:
        return {
            "status": "skip",
            "reason": "invalid_profit_lock_inputs",
            **base,
        }

    if base["side"] == "short":
        new_stop = min(
            entry - risk_value * lock_multiple,
            entry * (1.0 - cost_floor_pct / 100.0),
        )
        if new_stop >= stop or current >= new_stop:
            return {
                "status": "skip",
                "reason": "unexecutable_profit_lock_stop",
                "stop_price": new_stop,
                **base,
            }
        return {
            "status": "ok",
            "stop_price": new_stop,
            **base,
        }

    new_stop = max(
        entry + risk_value * lock_multiple,
        entry * (1.0 + cost_floor_pct / 100.0),
    )
    if new_stop <= stop or current <= new_stop:
        return {
            "status": "skip",
            "reason": "unexecutable_profit_lock_stop",
            "stop_price": new_stop,
            **base,
        }
    return {
        "status": "ok",
        "stop_price": new_stop,
        **base,
    }


def normalize_market(value: Any) -> str:
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


def normalize_position_side(value: Any) -> str:
    side = str(value or "long").strip().lower()
    return "short" if side in {"short", "sell"} else "long"


def clean_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if limit > 0 and len(text) > limit:
        return text[:limit].rstrip()
    return text


def safe_float(value: Any) -> float:
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
