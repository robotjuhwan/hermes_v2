from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def match_inquired_order(
    order: dict[str, Any],
    inquiry: dict[str, Any],
) -> dict[str, Any] | None:
    order_no = str(order.get("order_no") or "")
    symbol = str(order.get("symbol") or "")
    rows = [row for row in list(inquiry.get("orders") or []) if isinstance(row, dict)]
    for row in rows:
        if str(row.get("order_no") or "") == order_no:
            return row
    for row in rows:
        if str(row.get("symbol") or "") == symbol:
            return row
    return None


def status_from_order_fill(order: dict[str, Any], match: dict[str, Any]) -> str:
    qty = max(_safe_int(order.get("qty")), 0)
    filled_qty = max(_safe_int(match.get("filled_qty")), 0)
    remaining_qty = max(_safe_int(match.get("remaining_qty")), 0)
    if bool(match.get("canceled")) and filled_qty <= 0:
        return "canceled"
    if qty > 0 and filled_qty >= qty and remaining_qty <= 0:
        return "filled"
    if filled_qty > 0:
        if remaining_qty <= 0:
            return "filled"
        return "partially_filled"
    if str(order.get("status") or "") == "cancel_requested":
        return "cancel_requested"
    return "sent"


def is_order_stale(
    order: dict[str, Any],
    *,
    now: datetime | None = None,
    timeout_sec: int,
) -> bool:
    created = _parse_iso_datetime(order.get("created_at"))
    if created is None:
        return False
    current = now or datetime.now(timezone.utc)
    age = (current.astimezone(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    return age >= max(int(timeout_sec), 30)


def order_query_start_date(
    order: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    created = _parse_iso_datetime(order.get("created_at"))
    if created is None:
        return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Seoul")).strftime(
            "%Y%m%d"
        )
    return created.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
