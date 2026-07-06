from __future__ import annotations

from collections.abc import Callable
import math
from typing import Any

from tradecraft.services.binance_symbol import normalize_market, normalize_position_side

VISIBLE_BLOCK_STATUSES = {
    "proposed",
    "entry_pending",
    "open",
    "exit_pending",
    "paused",
    "error",
}
HISTORY_BLOCK_STATUSES = {"closed", "error"}
_BLOCK_TEXT_LIMIT = 220
_HISTORY_TEXT_LIMIT = 120


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _short_text(value: Any, *, limit: int = _BLOCK_TEXT_LIMIT) -> str:
    return str(value or "")[: max(int(limit), 1)]


def visible_block_rows(
    blocks: list[dict[str, Any]],
    *,
    visible_statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    statuses = visible_statuses or VISIBLE_BLOCK_STATUSES
    return [
        row
        for row in blocks
        if _is_visible_block(row, statuses=statuses)
    ]


def _is_visible_block(row: dict[str, Any], *, statuses: set[str]) -> bool:
    status = str(row.get("status") or "")
    if status not in statuses:
        return False
    if status == "error":
        return _safe_float(row.get("qty_open")) > 0
    return True


def actionable_error_block_rows(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in blocks
        if str(row.get("status") or "") == "error"
        and _safe_float(row.get("qty_open")) > 0
    ]


def inactive_error_block_rows(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in blocks
        if str(row.get("status") or "") == "error"
        and _safe_float(row.get("qty_open")) <= 0
    ]


def block_history_rows(
    blocks: list[dict[str, Any]],
    *,
    limit: int = 80,
) -> list[dict[str, Any]]:
    rows = [
        block
        for block in blocks
        if str(block.get("status") or "") in HISTORY_BLOCK_STATUSES
    ]
    rows.sort(
        key=lambda row: str(
            row.get("closed_at") or row.get("updated_at") or row.get("created_at") or ""
        ),
        reverse=True,
    )
    return rows[: max(int(limit), 1)]


def compact_history_block_rows(
    blocks: list[dict[str, Any]],
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    return [
        _compact_history_block(row)
        for row in block_history_rows(blocks, limit=limit)
    ]


def _compact_history_block(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    return {
        "block_id": row.get("block_id"),
        "symbol": row.get("symbol"),
        "market": row.get("market") or row.get("venue"),
        "side": row.get("side"),
        "status": row.get("status"),
        "horizon": row.get("horizon") or metadata.get("horizon"),
        "block_color": row.get("block_color") or metadata.get("block_color"),
        "lane": row.get("lane") or metadata.get("lane"),
        "qty_initial": row.get("qty_initial"),
        "qty_open": row.get("qty_open"),
        "entry_price": row.get("entry_price"),
        "target_price": row.get("target_price"),
        "stop_price": row.get("stop_price"),
        "current_price": row.get("current_price"),
        "leverage": row.get("leverage"),
        "thesis": _short_text(
            row.get("thesis") or row.get("llm_reason"),
            limit=_HISTORY_TEXT_LIMIT,
        ),
        "risk_note": _short_text(row.get("risk_note"), limit=_HISTORY_TEXT_LIMIT),
        "quote": {
            "price": quote.get("price"),
            "source": quote.get("source"),
            "fetched_at": quote.get("fetched_at"),
        },
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "closed_at": row.get("closed_at"),
        "realized_pnl_usdt": row.get("realized_pnl_usdt"),
        "r_multiple": row.get("r_multiple"),
    }


def enrich_blocks_with_latest_quotes(
    blocks: list[dict[str, Any]],
    quotes: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for block in blocks:
        row = dict(block)
        market = normalize_market(row.get("market"))
        symbol = str(row.get("symbol") or "").upper()
        quote = quotes.get((market, symbol))
        price = _safe_float(quote.get("price") if quote else 0)
        if quote:
            row["quote"] = quote
        if price > 0:
            row["current_price"] = price
            row["current_price_usdt"] = price
            entry = _safe_float(row.get("entry_price"))
            qty = _safe_float(row.get("qty_open"))
            side = normalize_position_side(row.get("side"))
            if entry > 0 and qty > 0:
                pnl = (price - entry) * qty
                if side == "short":
                    pnl = (entry - price) * qty
                row["unrealized_pnl_usdt"] = pnl
        enriched.append(row)
    return enriched


def attach_performance_reflections(
    blocks: list[dict[str, Any]],
    reflections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not reflections:
        return blocks
    enriched: list[dict[str, Any]] = []
    for block in blocks:
        row = dict(block)
        reflection = reflections.get(str(row.get("block_id") or ""))
        if reflection:
            row["performance"] = reflection
            row["performance_reflection"] = reflection
            row["realized_pnl_usdt"] = _safe_float(reflection.get("pnl_usdt"))
            row["r_multiple"] = _safe_float(reflection.get("r_multiple"))
            row["exit_price"] = _safe_float(reflection.get("exit_price"))
        enriched.append(row)
    return enriched


def normalize_account_snapshot(
    account: dict[str, Any],
    *,
    default_upbit_usdt_krw_rate: float,
) -> dict[str, Any]:
    if not isinstance(account, dict):
        return {"status": "missing", "spot_cash_usdt": 0.0, "futures_cash_usdt": 0.0}
    spot_cash = _safe_float(
        account.get("spot_cash_usdt")
        or account.get("cash_usdt")
        or account.get("spot_available_usdt")
    )
    futures_cash = _safe_float(
        account.get("futures_cash_usdt")
        or account.get("futures_available_usdt")
        or account.get("available_balance_usdt")
    )
    upbit_cash_krw = _safe_float(account.get("upbit_cash_krw"))
    upbit_usdt_krw_rate = max(
        _safe_float(account.get("upbit_usdt_krw_rate"))
        or _safe_float(default_upbit_usdt_krw_rate),
        1.0,
    )
    upbit_cash_usdt = _safe_float(account.get("upbit_cash_usdt"))
    if upbit_cash_usdt <= 0 and upbit_cash_krw > 0:
        upbit_cash_usdt = upbit_cash_krw / upbit_usdt_krw_rate
    return {
        **account,
        "spot_cash_usdt": spot_cash,
        "futures_cash_usdt": futures_cash,
        "upbit_cash_krw": upbit_cash_krw,
        "upbit_cash_usdt": upbit_cash_usdt,
        "upbit_usdt_krw_rate": upbit_usdt_krw_rate,
    }


def _dict_value(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def _manager_action_count(actions: dict[str, Any]) -> int:
    return sum(
        len(actions.get(key) or [])
        for key in (
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        )
        if isinstance(actions.get(key), list)
    )


def manager_run_with_decision_context(
    row: dict[str, Any],
    *,
    compact_prompt_context: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    prompt = _dict_value(row, "prompt")
    response = _dict_value(row, "response")
    actions = _dict_value(row, "actions")
    hold_decision = response.get("hold_decision") or row.get("hold_decision") or {}
    if not isinstance(hold_decision, dict):
        hold_decision = {}
    return {
        **row,
        "decision_context": compact_prompt_context(
            prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        ),
    }


def compact_snapshot_manager_run(
    row: dict[str, Any],
    *,
    normalize_hold_decision: Callable[
        [dict[str, Any], dict[str, Any]],
        dict[str, Any],
    ],
    compact_response_payload: Callable[[dict[str, Any]], dict[str, Any]],
    compact_prompt_context: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    actions = _dict_value(row, "actions")
    response = _dict_value(row, "response")
    prompt = _dict_value(row, "prompt")
    hold_decision = response.get("hold_decision") or row.get("hold_decision") or {}
    hold_decision = normalize_hold_decision(
        {**response, "hold_decision": hold_decision},
        actions,
    )
    return {
        "id": row.get("id"),
        "run_at": row.get("run_at"),
        "status": row.get("status"),
        "mode": row.get("mode"),
        "model": row.get("model"),
        "error_message": row.get("error_message"),
        "workflow_id": row.get("workflow_id"),
        "workflow_version": row.get("workflow_version"),
        "skill_ids": list(row.get("skill_ids") or [])[:8],
        "contract_ids": list(row.get("contract_ids") or [])[:8],
        "action_count": _manager_action_count(actions),
        "actions": actions,
        "hold_decision": hold_decision,
        "decision_payload": compact_response_payload(response),
        "decision_context": compact_prompt_context(
            prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        ),
    }
