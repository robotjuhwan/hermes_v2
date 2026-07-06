from __future__ import annotations

from typing import Any

from tradecraft.services.block_performance import summarize_block_path
from tradecraft.services.kis_exit_gate import should_emit_profit_lock_signal
from tradecraft.services.kis_horizon import normalize_horizon


def block_performance_summary(
    block: dict[str, Any],
    *,
    current_price: float,
    prices: list[Any],
) -> dict[str, float]:
    return summarize_block_path(
        entry_price=block.get("entry_price"),
        current_price=current_price,
        prices=prices,
    )


def has_exit_signal(
    events: list[dict[str, Any]],
    reason: str,
    *,
    event_type: str = "exit_signal",
) -> bool:
    for event in events:
        if str(event.get("event_type") or "") != event_type:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("reason") or "") == reason:
            return True
    return False


def profit_lock_signal_plan(
    block: dict[str, Any],
    *,
    price: float,
    performance: dict[str, float],
    already_signaled: bool,
) -> dict[str, Any] | None:
    if not should_emit_profit_lock_signal(performance):
        return None
    block_id = str(block.get("block_id") or "")
    if not block_id or already_signaled:
        return None
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    horizon = normalize_horizon(metadata.get("horizon"))
    reason = "profit_giveback"
    event = {
        "block_id": block_id,
        "event_type": "profit_lock_signal",
        "message": (
            f"{horizon} block gave back profit from path peak; "
            "manager review required"
        ),
        "payload": {
            "horizon": horizon,
            "reason": reason,
            "price": price,
            "policy_action": "manager_review",
            "performance": performance,
            "manager_review": "regular_market_30m_full_portfolio",
        },
    }
    return {
        "status": "profit_lock_signal",
        "reason": reason,
        "horizon": horizon,
        "block_id": block_id,
        "event": event,
    }
