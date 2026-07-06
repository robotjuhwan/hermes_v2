from __future__ import annotations

import json
from typing import Any

from tradecraft.services.kis_ledger import safe_float, safe_int
from tradecraft.services.kis_symbol import clean_symbol_name, extract_symbol_name


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def has_order_notification(
    events: list[dict[str, Any]],
    *,
    order_id: Any,
    order_status: str,
    filled_qty: int,
) -> bool:
    for event in events:
        if str(event.get("event_type") or "") != "telegram_notified":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if (
            str(payload.get("order_id") or "") == str(order_id or "")
            and str(payload.get("status") or "") == str(order_status or "")
            and safe_int(payload.get("filled_qty")) == filled_qty
        ):
            return True
    return False


def format_reconciled_order_message(
    *,
    order: dict[str, Any],
    match: dict[str, Any],
    block: dict[str, Any],
    filled_qty: int,
) -> str:
    side = str(order.get("side") or "").lower()
    symbol = str(order.get("symbol") or block.get("symbol") or "")
    name = (
        clean_symbol_name(block.get("name"), symbol=symbol)
        or extract_symbol_name(match, symbol=symbol)
        or extract_symbol_name(order, symbol=symbol)
        or extract_symbol_name(_dict_payload(order.get("response_json")), symbol=symbol)
    )
    display_symbol = f"{name} ({symbol})" if name else symbol
    block_id = str(block.get("block_id") or order.get("block_id") or "")
    avg_price = safe_float(match.get("avg_fill_price")) or safe_float(
        order.get("avg_fill_price")
    )
    limit_price = safe_float(order.get("limit_price"))
    reason = str(order.get("reason") or "")
    title = "쥬 블록 진입 체결" if side == "buy" else "쥬 블록 청산 체결"
    lines = [
        title,
        f"{display_symbol} · {block_id}",
        f"수량 {filled_qty:,}주 · 체결가 {avg_price or limit_price:,.0f}원",
    ]
    if side == "buy":
        target = safe_float(block.get("target_price"))
        stop = safe_float(block.get("stop_price"))
        if target or stop:
            lines.append(f"목표 {target:,.0f}원 · 손절 {stop:,.0f}원")
        thesis = str(block.get("thesis") or "").strip()
        if thesis:
            lines.append(f"가설: {thesis[:120]}")
        return "\n".join(lines)

    reason_label = {
        "target_reached": "목표가 도달",
        "stop_reached": "손절가 도달",
        "force_exit_requested": "수동 청산 요청",
    }.get(reason, reason or "청산")
    entry_price = safe_float(block.get("entry_price"))
    pnl = (avg_price - entry_price) * filled_qty if entry_price and avg_price else 0.0
    lines.append(f"사유 {reason_label} ({reason})")
    if entry_price and avg_price:
        lines.append(f"블록 손익 {pnl:+,.0f}원")
    return "\n".join(lines)
