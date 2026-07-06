from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from tradecraft.services.kis_horizon import (
    ACTIVE_BLOCK_STATUSES,
    HORIZON_COLORS,
    normalize_horizon,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ledger_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ledger_json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        parsed = float(value)
        return 0.0 if math.isnan(parsed) else parsed
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A"} or text.lower() == "nan":
        return 0.0
    try:
        parsed = float(text)
    except ValueError:
        return 0.0
    return 0.0 if math.isnan(parsed) else parsed


def safe_int(value: Any) -> int:
    return int(math.floor(safe_float(value)))


def _is_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"\d{6}", text))


def positions_by_symbol(account: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("symbol") or ""): row
        for row in list(account.get("positions") or [])
        if isinstance(row, dict) and _is_symbol(row.get("symbol"))
    }


def unallocated_qty_by_symbol(
    *,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    active_statuses: set[str] | None = None,
) -> dict[str, int]:
    statuses = active_statuses or ACTIVE_BLOCK_STATUSES
    out: dict[str, int] = {}
    for symbol, position in positions_by_symbol(account).items():
        out[symbol] = max(
            safe_int(position.get("available_qty") or position.get("qty")),
            0,
        )
    for block in blocks:
        status = str(block.get("status") or "")
        if status not in statuses:
            continue
        symbol = str(block.get("symbol") or "")
        if not _is_symbol(symbol):
            continue
        qty = max(safe_int(block.get("qty_open") or block.get("qty_initial")), 0)
        out[symbol] = max(int(out.get(symbol, 0)) - qty, 0)
    return out


def _row_get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def row_to_block(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return {
        "block_id": _row_get(row, "block_id"),
        "symbol": _row_get(row, "symbol"),
        "name": _row_get(row, "name"),
        "qty_initial": int(_row_get(row, "qty_initial") or 0),
        "qty_open": int(_row_get(row, "qty_open") or 0),
        "entry_price": _row_get(row, "entry_price"),
        "target_price": _row_get(row, "target_price"),
        "stop_price": _row_get(row, "stop_price"),
        "thesis": _row_get(row, "thesis"),
        "llm_reason": _row_get(row, "llm_reason"),
        "risk_note": _row_get(row, "risk_note"),
        "created_by": _row_get(row, "created_by"),
        "manager_run_id": _row_get(row, "manager_run_id"),
        "status": _row_get(row, "status"),
        "force_exit_requested": bool(_row_get(row, "force_exit_requested")),
        "metadata": ledger_json_loads(_row_get(row, "metadata_json"), {}),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
        "opened_at": _row_get(row, "opened_at"),
        "closed_at": _row_get(row, "closed_at"),
    }


def build_allocation_summary(
    *,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    active_statuses: set[str] | None = None,
) -> dict[str, Any]:
    statuses = active_statuses or ACTIVE_BLOCK_STATUSES
    positions = {
        str(row.get("symbol") or ""): row
        for row in list(account.get("positions") or [])
        if isinstance(row, dict)
    }
    symbols = sorted(
        {
            *positions.keys(),
            *[
                str(row.get("symbol") or "")
                for row in blocks
                if _is_symbol(row.get("symbol"))
            ],
        }
    )
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        block_qty = sum(
            safe_int(row.get("qty_open") or row.get("qty_initial"))
            for row in blocks
            if str(row.get("symbol") or "") == symbol
            and str(row.get("status") or "") in statuses
        )
        position = positions.get(symbol) or {}
        account_qty = safe_int(position.get("available_qty") or position.get("qty"))
        quote = quotes.get(symbol) or {}
        rows.append(
            {
                "symbol": symbol,
                "name": str(position.get("name") or quote.get("name") or symbol),
                "account_qty": account_qty,
                "block_qty": block_qty,
                "unallocated_qty": max(account_qty - block_qty, 0),
                "overallocated_qty": max(block_qty - account_qty, 0),
            }
        )
    return {"status": "ok", "items": rows}


def build_horizon_allocation_summary(
    *,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    targets: dict[str, float],
    active_statuses: set[str] | None = None,
) -> dict[str, Any]:
    statuses = active_statuses or ACTIVE_BLOCK_STATUSES
    values = {horizon: 0.0 for horizon in targets}
    cash = safe_float(account.get("orderable_cash_krw")) or safe_float(
        account.get("cash_krw")
    )
    values["cash"] = max(cash, 0.0)
    for block in blocks:
        if str(block.get("status") or "") not in statuses:
            continue
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        horizon = normalize_horizon(metadata.get("horizon"))
        qty = max(safe_int(block.get("qty_open") or block.get("qty_initial")), 0)
        quote = quotes.get(str(block.get("symbol") or "")) or {}
        price = safe_float(quote.get("price")) or safe_float(block.get("entry_price"))
        values[horizon] = values.get(horizon, 0.0) + max(price * qty, 0.0)
    total_value = sum(values.values())

    def build_item(horizon: str) -> dict[str, Any]:
        current_value = values.get(horizon, 0.0)
        current_weight = current_value / total_value if total_value > 0 else 0.0
        target_weight = targets.get(horizon, 0.0)
        return {
            "horizon": horizon,
            "block_color": HORIZON_COLORS.get(horizon, horizon),
            "current_value_krw": current_value,
            "current_weight": current_weight,
            "target_weight": target_weight,
            "drift": current_weight - target_weight,
        }

    ordered = [
        horizon
        for horizon in ("short", "mid", "long", "core_etf")
        if values.get(horizon, 0.0) > 0
    ]
    ordered.extend(
        horizon
        for horizon in ("cash", "short", "mid", "long", "core_etf")
        if horizon not in ordered
    )
    return {
        "status": "ok",
        "targets": targets,
        "total_value_krw": total_value,
        "items": [build_item(horizon) for horizon in ordered],
    }


def row_to_order(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return {
        "id": int(_row_get(row, "id") or 0),
        "block_id": _row_get(row, "block_id"),
        "symbol": _row_get(row, "symbol"),
        "side": _row_get(row, "side"),
        "qty": int(_row_get(row, "qty") or 0),
        "limit_price": int(_row_get(row, "limit_price") or 0),
        "order_type": _row_get(row, "order_type"),
        "status": _row_get(row, "status"),
        "order_no": _row_get(row, "order_no"),
        "order_orgno": _row_get(row, "order_orgno"),
        "reason": _row_get(row, "reason"),
        "filled_qty": int(_row_get(row, "filled_qty") or 0),
        "remaining_qty": int(_row_get(row, "remaining_qty") or 0),
        "avg_fill_price": _row_get(row, "avg_fill_price"),
        "last_checked_at": _row_get(row, "last_checked_at"),
        "cancel_requested": bool(_row_get(row, "cancel_requested")),
        "cancel_order_no": _row_get(row, "cancel_order_no"),
        "cancel_response": ledger_json_loads(_row_get(row, "cancel_response_json"), {}),
        "response": ledger_json_loads(_row_get(row, "response_json"), {}),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
    }


def row_to_event(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return {
        "id": int(_row_get(row, "id") or 0),
        "block_id": _row_get(row, "block_id"),
        "event_type": _row_get(row, "event_type"),
        "message": _row_get(row, "message"),
        "payload": ledger_json_loads(_row_get(row, "payload_json"), {}),
        "created_at": _row_get(row, "created_at"),
    }


def row_to_manager_run(
    row: Mapping[str, Any] | Any,
    *,
    safe_int: Callable[[Any], int],
    sanitize_hold_decision: Callable[..., dict[str, Any]],
    sanitize_creative_hypotheses: Callable[[Any], list[dict[str, Any]]],
) -> dict[str, Any]:
    actions_payload = ledger_json_loads(_row_get(row, "actions_json"), {})
    if not isinstance(actions_payload, dict):
        actions_payload = {}
    applied = actions_payload.get("_applied") or actions_payload.get("applied") or {}
    actions = {
        key: value
        for key, value in actions_payload.items()
        if key not in {"_applied", "applied"}
    }
    response_payload = ledger_json_loads(_row_get(row, "response_json"), {})
    if not isinstance(response_payload, dict):
        response_payload = {}
    action_count = sum(
        len(actions.get(key) or [])
        for key in (
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        )
    )
    hold_decision = sanitize_hold_decision(
        response_payload.get("hold_decision"),
        action_count=action_count,
    )
    creative_hypotheses = sanitize_creative_hypotheses(
        response_payload.get("creative_hypotheses")
    )
    return {
        "id": int(_row_get(row, "id") or 0),
        "run_at": _row_get(row, "run_at"),
        "market_session": _row_get(row, "market_session"),
        "status": _row_get(row, "status"),
        "mode": _row_get(row, "mode"),
        "model": _row_get(row, "model"),
        "error_message": _row_get(row, "error_message"),
        "workflow_id": _row_get(row, "workflow_id"),
        "workflow_version": safe_int(_row_get(row, "workflow_version")),
        "skill_ids": ledger_json_loads(_row_get(row, "skill_ids_json"), []),
        "contract_ids": ledger_json_loads(_row_get(row, "contract_ids_json"), []),
        "skill_ids_json": _row_get(row, "skill_ids_json"),
        "contract_ids_json": _row_get(row, "contract_ids_json"),
        "prompt": ledger_json_loads(_row_get(row, "prompt_json"), {}),
        "response": response_payload,
        "hold_decision": hold_decision,
        "creative_hypotheses": creative_hypotheses,
        "actions": actions,
        "applied": applied if isinstance(applied, dict) else {},
    }
