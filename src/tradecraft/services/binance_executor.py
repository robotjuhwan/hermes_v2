from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import Any

from tradecraft.services.binance_exit_gate import (
    partial_profit_full_exit_retry_decision,
    partial_profit_full_exit_retry_state_plan,
)
from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    normalize_market,
    normalize_position_side,
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


def _first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = _safe_float(row.get(key))
        if value > 0:
            return value
    return 0.0


def response_filled_qty(response: dict[str, Any], *, requested_qty: float) -> float:
    _ = requested_qty
    for key in (
        "executedQty",
        "executed_qty",
        "cumQty",
        "cum_qty",
        "filledQty",
        "filled_qty",
    ):
        qty = _safe_float(response.get(key))
        if qty > 0:
            return qty
    return 0.0


async def exit_order_execution(
    *,
    execution_enabled: bool,
    submit_exit_order: Callable[..., Awaitable[dict[str, Any]]],
    enrich_order_response_for_costs: Callable[..., Awaitable[dict[str, Any]]] | None,
    block: dict[str, Any],
    market: str,
    symbol: str,
    order_side: str,
    qty: float,
    price: float,
    allow_reduce_only_below_min_notional: bool,
) -> dict[str, Any]:
    if not execution_enabled:
        return {"status": "paper", "response": {}}
    try:
        response = await submit_exit_order(
            market=market,
            symbol=symbol,
            side=order_side,
            qty=qty,
            price=price,
            allow_reduce_only_below_min_notional=allow_reduce_only_below_min_notional,
        )
        status = "sent"
    except Exception as exc:
        return {
            "status": "error",
            "response": {"status": "error", "error_message": str(exc)},
        }
    if enrich_order_response_for_costs is not None:
        response = await enrich_order_response_for_costs(
            block=block,
            market=market,
            symbol=symbol,
            response=response,
            include_funding=True,
        )
    return {"status": status, "response": response}


async def partial_profit_exit_order_execution(
    *,
    execution_enabled: bool,
    submit_exit_order: Callable[..., Awaitable[dict[str, Any]]],
    market: str,
    symbol: str,
    order_side: str,
    qty: float,
    requested_qty: float,
    qty_open: float,
    remaining_qty: float,
    price: float,
    full_exit_for_min_notional: bool,
    exit_mode: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    current_qty = _safe_float(qty)
    current_remaining_qty = _safe_float(remaining_qty)
    current_full_exit = bool(full_exit_for_min_notional)
    current_exit_mode = str(exit_mode or "partial")
    current_metadata = dict(metadata)
    response: dict[str, Any] = {}
    status = "paper"
    if not execution_enabled:
        return {
            "status": status,
            "response": response,
            "qty": current_qty,
            "remaining_qty": current_remaining_qty,
            "full_exit_for_min_notional": current_full_exit,
            "exit_mode": current_exit_mode,
            "metadata": current_metadata,
        }

    try:
        response = await submit_exit_order(
            market=market,
            symbol=symbol,
            side=order_side,
            qty=current_qty,
            price=price,
            allow_reduce_only_below_min_notional=current_full_exit,
        )
        status = "sent"
    except Exception as exc:
        error_message = str(exc)
        retry_decision = partial_profit_full_exit_retry_decision(
            market=market,
            full_exit_for_min_notional=current_full_exit,
            requested_qty=requested_qty,
            qty_open=qty_open,
            error_message=error_message,
        )
        if str(retry_decision.get("status") or "") == "retry":
            retry_qty = _safe_float(retry_decision.get("retry_qty"))
            try:
                response = await submit_exit_order(
                    market=market,
                    symbol=symbol,
                    side=order_side,
                    qty=retry_qty,
                    price=price,
                    allow_reduce_only_below_min_notional=True,
                )
                status = "sent"
                retry_state = partial_profit_full_exit_retry_state_plan(
                    metadata=current_metadata,
                    retry_decision=retry_decision,
                    fallback_error_message=error_message,
                )
                current_qty = _safe_float(retry_state.get("retry_qty"))
                current_remaining_qty = _safe_float(retry_state.get("remaining_qty"))
                current_full_exit = bool(
                    retry_state.get("full_exit_for_min_notional")
                )
                current_exit_mode = str(
                    retry_state.get("exit_mode") or "full_exit_min_notional_retry"
                )
                current_metadata = (
                    retry_state.get("metadata")
                    if isinstance(retry_state.get("metadata"), dict)
                    else current_metadata
                )
            except Exception as retry_exc:
                response = {
                    "status": "error",
                    "error_message": str(retry_exc),
                    "initial_error_message": error_message,
                }
                status = "error"
        else:
            response = {"status": "error", "error_message": error_message}
            status = "error"

    response_retry_decision = partial_profit_full_exit_retry_decision(
        market=market,
        full_exit_for_min_notional=current_full_exit,
        requested_qty=requested_qty,
        qty_open=qty_open,
        response_is_min_notional_error=is_min_notional_error_response(response),
        response_error_message=response_error_message(response) or "",
        fallback_error_message=str(response.get("status") or "partial exit failed"),
    )
    if status == "sent" and str(response_retry_decision.get("status") or "") == "retry":
        error_message = response_error_message(response) or str(
            response.get("status") or "partial exit failed"
        )
        retry_qty = _safe_float(response_retry_decision.get("retry_qty"))
        try:
            response = await submit_exit_order(
                market=market,
                symbol=symbol,
                side=order_side,
                qty=retry_qty,
                price=price,
                allow_reduce_only_below_min_notional=True,
            )
            status = "sent"
            retry_state = partial_profit_full_exit_retry_state_plan(
                metadata=current_metadata,
                retry_decision=response_retry_decision,
                fallback_error_message=error_message,
            )
            current_qty = _safe_float(retry_state.get("retry_qty"))
            current_remaining_qty = _safe_float(retry_state.get("remaining_qty"))
            current_full_exit = bool(retry_state.get("full_exit_for_min_notional"))
            current_exit_mode = str(
                retry_state.get("exit_mode") or "full_exit_min_notional_retry"
            )
            current_metadata = (
                retry_state.get("metadata")
                if isinstance(retry_state.get("metadata"), dict)
                else current_metadata
            )
        except Exception as retry_exc:
            response = {
                "status": "error",
                "error_message": str(retry_exc),
                "initial_response": response,
                "initial_error_message": error_message,
            }
            status = "error"

    return {
        "status": status,
        "response": response,
        "qty": current_qty,
        "remaining_qty": current_remaining_qty,
        "full_exit_for_min_notional": current_full_exit,
        "exit_mode": current_exit_mode,
        "metadata": current_metadata,
    }


def response_error_message(response: dict[str, Any]) -> str:
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    for source in (response, raw):
        for key in ("error_message", "error", "msg", "message"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def is_min_notional_error_response(response: dict[str, Any]) -> bool:
    status = str(response.get("status") or "").strip().lower()
    message = response_error_message(response).lower()
    if status not in {"error", "rejected", "failed"} and not message:
        return False
    return "below minimum" in message or "below min" in message


def response_order_id(response: dict[str, Any]) -> str:
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    for source in (response, raw):
        for key in ("order_id", "orderId", "orderID"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def entry_not_filled_reason(
    response: dict[str, Any],
    *,
    fallback_status: str,
) -> str:
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    expiry = (
        response.get("expiryReason")
        or response.get("expiry_reason")
        or raw.get("expiryReason")
        or raw.get("expiry_reason")
    )
    return (
        f"entry order not filled: {fallback_status}; "
        f"{expiry or response.get('error_message') or ''}"
    ).strip()


def entry_order_side(block: dict[str, Any]) -> str:
    market = normalize_market(block.get("market"))
    if market in {"spot", UPBIT_SPOT_MARKET}:
        return "buy"
    return "sell" if normalize_position_side(block.get("side")) == "short" else "buy"


def filled_order_price(order: dict[str, Any]) -> float:
    response = order.get("response") if isinstance(order.get("response"), dict) else {}
    response_status = str(response.get("status") or "").upper()
    order_status = str(order.get("status") or "").lower()
    if response_status not in {"FILLED", "PARTIALLY_FILLED"} and order_status != "paper":
        return 0.0
    fills = response.get("trade_fills")
    if isinstance(fills, list):
        notional = 0.0
        qty = 0.0
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            fill_price = _first_float(fill, ("price", "avgPrice", "avg_price"))
            fill_qty = _first_float(
                fill,
                ("qty", "quantity", "executedQty", "executed_qty"),
            )
            if fill_price > 0 and fill_qty > 0:
                notional += fill_price * fill_qty
                qty += fill_qty
        if qty > 0:
            return notional / qty
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    for source in (raw, response, order):
        if not isinstance(source, dict):
            continue
        price = _first_float(
            source,
            (
                "avgPrice",
                "avg_price",
                "avg_fill_price",
                "average",
                "average_price",
            ),
        )
        if price > 0:
            return price
        qty = _first_float(
            source,
            (
                "executedQty",
                "executed_qty",
                "cumQty",
                "cum_qty",
                "filledQty",
                "filled_qty",
            ),
        )
        quote = _first_float(
            source,
            ("cumQuote", "cum_quote", "quoteQty", "quote_qty", "cost"),
        )
        if qty > 0 and quote > 0:
            return quote / qty
        price = _first_float(source, ("price", "limit_price"))
        if price > 0:
            return price
    return 0.0
