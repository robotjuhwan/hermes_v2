from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from tradecraft.services.binance_lane import (
    BINANCE_ALLOCATION_LANES,
    BINANCE_HORIZON_COLORS,
    normalize_binance_display_lane,
    normalize_binance_horizon,
)
from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    normalize_market,
    normalize_position_side,
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
        if not math.isfinite(float(value)):
            return 0.0
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return 0.0
    try:
        parsed = float(text)
    except ValueError:
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return parsed


def safe_int(value: Any) -> int:
    return int(math.floor(safe_float(value)))


def build_lane_allocation_summary(
    blocks: list[dict[str, Any]],
    *,
    active_statuses: set[str] | None = None,
    upbit_usdt_krw_rate: float = 1.0,
) -> dict[str, Any]:
    statuses = active_statuses or {"entry_pending", "open", "exit_pending"}
    values = {lane: 0.0 for lane in BINANCE_ALLOCATION_LANES}
    counts = {lane: 0 for lane in BINANCE_ALLOCATION_LANES}
    for block in blocks:
        if str(block.get("status") or "") not in statuses:
            continue
        qty = safe_float(block.get("qty_open"))
        if qty <= 0:
            continue
        lane = str(block.get("lane") or "").strip().lower()
        market = str(block.get("market") or "spot").strip().lower()
        if lane not in values:
            horizon_market = "spot" if market == UPBIT_SPOT_MARKET else market
            horizon = normalize_binance_horizon(
                block.get("horizon") or block.get("block_color"),
                market=horizon_market,
            )
            lane = horizon if horizon in values else "short"
        entry = safe_float(block.get("entry_price"))
        notional = max(entry * qty, 0.0)
        if market == UPBIT_SPOT_MARKET:
            notional = notional / max(safe_float(upbit_usdt_krw_rate), 1.0)
        values[lane] += notional
        counts[lane] += 1
    total = sum(values.values())
    return {
        "items": [
            {
                "lane": lane,
                "value_usdt": values[lane],
                "weight_pct": (values[lane] / total * 100.0) if total > 0 else 0.0,
                "block_count": counts[lane],
            }
            for lane in BINANCE_ALLOCATION_LANES
        ],
        "total_value_usdt": total,
    }


def _row_get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _row_has(row: Mapping[str, Any] | Any, key: str) -> bool:
    try:
        return key in row.keys()
    except AttributeError:
        return isinstance(row, Mapping) and key in row


def _first_positive_float(row: Mapping[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = safe_float(row.get(key))
        if value > 0:
            return value
    return 0.0


def _binance_order_fill_summary(
    *,
    status: Any,
    qty: float,
    response: dict[str, Any],
) -> dict[str, Any]:
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    filled_qty = _first_positive_float(
        response,
        (
            "executedQty",
            "executed_qty",
            "cumQty",
            "cum_qty",
            "filledQty",
            "filled_qty",
        ),
    )
    if filled_qty <= 0:
        filled_qty = _first_positive_float(
            raw,
            (
                "executedQty",
                "executed_qty",
                "cumQty",
                "cum_qty",
                "filledQty",
                "filled_qty",
            ),
        )
    filled_quote = _first_positive_float(
        response,
        (
            "cum_quote",
            "cummulativeQuoteQty",
            "cumulative_quote",
            "executed_quote",
            "quoteQty",
            "quote_qty",
        ),
    )
    if filled_quote <= 0:
        filled_quote = _first_positive_float(
            raw,
            (
                "cum_quote",
                "cummulativeQuoteQty",
                "cumulative_quote",
                "executed_quote",
                "quoteQty",
                "quote_qty",
            ),
        )
    response_status = str(response.get("status") or raw.get("status") or "").strip().upper()
    row_status = str(status or "").strip().lower()
    execution_status = ""
    if row_status == "paper":
        execution_status = "paper"
    elif response_status == "FILLED":
        execution_status = "filled"
    elif response_status == "PARTIALLY_FILLED":
        execution_status = "partially_filled"
    elif filled_qty > 0:
        execution_status = (
            "filled"
            if qty > 0 and filled_qty >= max(qty - max(qty * 1e-6, 1e-12), 0.0)
            else "partially_filled"
        )
    summary: dict[str, Any] = {}
    if execution_status:
        summary["execution_status"] = execution_status
    if filled_qty > 0:
        summary["filled_qty"] = filled_qty
        summary["effective_fill"] = True
    else:
        summary["effective_fill"] = False
    if filled_quote > 0:
        summary["filled_quote"] = filled_quote
    if filled_qty > 0 and filled_quote > 0:
        summary["avg_fill_price"] = filled_quote / filled_qty
    return summary


def row_to_block(
    row: Mapping[str, Any] | Any,
    *,
    compact_validation_repair: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = ledger_json_loads(_row_get(row, "metadata_json"), {})
    if not isinstance(metadata, dict):
        metadata = {}
    validation_repair = metadata.get("validation_repair")
    if isinstance(validation_repair, dict) and compact_validation_repair is not None:
        compacted_repair = compact_validation_repair(validation_repair)
        if compacted_repair:
            metadata["validation_repair"] = compacted_repair
    market = normalize_market(_row_get(row, "market"))
    side = _row_get(row, "side")
    horizon = normalize_binance_horizon(metadata.get("horizon"), market=market)
    lane = normalize_binance_display_lane(
        lane=metadata.get("lane"),
        market=market,
        horizon=horizon,
        side=side,
    )
    block_color = metadata.get("block_color") or BINANCE_HORIZON_COLORS.get(horizon, horizon)
    metadata = {
        **metadata,
        "horizon": horizon,
        "block_color": block_color,
        "lane": lane,
    }
    return {
        "block_id": _row_get(row, "block_id"),
        "symbol": _row_get(row, "symbol"),
        "market": market,
        "side": side,
        "qty_initial": float(_row_get(row, "qty_initial") or 0.0),
        "qty_open": float(_row_get(row, "qty_open") or 0.0),
        "entry_price": _row_get(row, "entry_price"),
        "target_price": _row_get(row, "target_price"),
        "stop_price": _row_get(row, "stop_price"),
        "leverage": int(_row_get(row, "leverage") or 1),
        "margin_type": _row_get(row, "margin_type"),
        "liquidation_price": _row_get(row, "liquidation_price"),
        "thesis": _row_get(row, "thesis"),
        "llm_reason": _row_get(row, "llm_reason"),
        "risk_note": _row_get(row, "risk_note"),
        "created_by": _row_get(row, "created_by"),
        "manager_run_id": _row_get(row, "manager_run_id"),
        "status": _row_get(row, "status"),
        "force_exit_requested": bool(_row_get(row, "force_exit_requested")),
        "metadata": metadata,
        "horizon": horizon,
        "lane": lane,
        "block_color": block_color,
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
        "opened_at": _row_get(row, "opened_at"),
        "closed_at": _row_get(row, "closed_at"),
    }


def row_order_payload(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    qty = float(_row_get(row, "qty") or 0.0)
    status = _row_get(row, "status")
    response = ledger_json_loads(_row_get(row, "response_json"), {})
    if not isinstance(response, dict):
        response = {}
    return {
        "block_id": _row_get(row, "block_id"),
        "symbol": _row_get(row, "symbol"),
        "market": _row_get(row, "market"),
        "side": _row_get(row, "side"),
        "qty": qty,
        "order_type": _row_get(row, "order_type"),
        "status": status,
        **_binance_order_fill_summary(status=status, qty=qty, response=response),
        "reason": _row_get(row, "reason"),
        "response": response,
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
    }


def row_to_order(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return {
        "id": int(_row_get(row, "id") or 0),
        **row_order_payload(row),
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


def row_to_performance_reflection(
    row: Mapping[str, Any] | Any,
    *,
    canonical_performance_lane: Callable[..., str],
    entry_quality_label: Callable[..., str],
) -> dict[str, Any]:
    fee = float(_row_get(row, "fee_usdt") or 0.0)
    funding = float(_row_get(row, "funding_usdt") or 0.0)
    slippage = float(_row_get(row, "slippage_usdt") or 0.0)
    spread = float(_row_get(row, "spread_usdt") or 0.0) if _row_has(row, "spread_usdt") else 0.0
    lesson = ledger_json_loads(_row_get(row, "lesson_json"), {})
    market = normalize_market(_row_get(row, "market"))
    side = normalize_position_side(_row_get(row, "side"))
    raw_lane = str(_row_get(row, "lane", "") or "").strip().lower()
    lane = canonical_performance_lane(
        raw_lane=raw_lane,
        market=market,
        side=side,
    )
    block_metadata = (
        ledger_json_loads(_row_get(row, "block_metadata_json"), {})
        if _row_has(row, "block_metadata_json")
        else {}
    )
    entry_quality = entry_quality_label(lesson, block_metadata)
    return {
        "block_id": _row_get(row, "block_id"),
        "symbol": _row_get(row, "symbol"),
        "market": market,
        "side": side,
        "lane": lane,
        "entry_price": float(_row_get(row, "entry_price") or 0.0),
        "exit_price": float(_row_get(row, "exit_price") or 0.0),
        "stop_price": float(_row_get(row, "stop_price") or 0.0),
        "target_price": float(_row_get(row, "target_price") or 0.0),
        "pnl_usdt": float(_row_get(row, "pnl_usdt") or 0.0),
        "gross_pnl_usdt": float(
            _row_get(row, "gross_pnl_usdt") or _row_get(row, "pnl_usdt") or 0.0
        ),
        "net_pnl_usdt": float(
            _row_get(row, "net_pnl_usdt") or _row_get(row, "pnl_usdt") or 0.0
        ),
        "fee_usdt": fee,
        "funding_usdt": funding,
        "slippage_usdt": slippage,
        "spread_usdt": spread,
        "total_cost_usdt": fee + funding + slippage + spread,
        "cost_source": _row_get(row, "cost_source"),
        "r_multiple": float(_row_get(row, "r_multiple") or 0.0),
        "mfe_r_multiple": float(_row_get(row, "mfe_r_multiple") or 0.0),
        "mae_r_multiple": float(_row_get(row, "mae_r_multiple") or 0.0),
        "pattern_key": _row_get(row, "pattern_key"),
        "entry_quality_label": entry_quality,
        "present_cost_components": list(lesson.get("present_cost_components") or [])
        if isinstance(lesson, dict)
        else [],
        "lesson": lesson,
        "created_at": _row_get(row, "created_at"),
    }


def row_to_manager_run(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return {
        "id": int(_row_get(row, "id") or 0),
        "run_at": _row_get(row, "run_at"),
        "status": _row_get(row, "status"),
        "mode": _row_get(row, "mode"),
        "model": _row_get(row, "model"),
        "error_message": _row_get(row, "error_message"),
        "workflow_id": _row_get(row, "workflow_id"),
        "workflow_version": int(_row_get(row, "workflow_version") or 0),
        "skill_ids": ledger_json_loads(_row_get(row, "skill_ids_json"), []),
        "contract_ids": ledger_json_loads(_row_get(row, "contract_ids_json"), []),
        "skill_ids_json": _row_get(row, "skill_ids_json"),
        "contract_ids_json": _row_get(row, "contract_ids_json"),
        "prompt": ledger_json_loads(_row_get(row, "prompt_json"), {}),
        "response": ledger_json_loads(_row_get(row, "response_json"), {}),
        "actions": ledger_json_loads(_row_get(row, "actions_json"), {}),
    }
