from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from tradecraft.services.live_authority import compact_live_authority_for_status

_BLOCK_TEXT_LIMIT = 220
_HISTORY_TEXT_LIMIT = 120
_EVENT_TEXT_LIMIT = 120
_COMPACT_HISTORY_LIMIT = 12
_COMPACT_ORDER_LIMIT = 8
_COMPACT_EVENT_LIMIT = 8
_STATUS_HISTORY_LIMIT = 4
_STATUS_ORDER_LIMIT = 4
_STATUS_EVENT_LIMIT = 4
_STATUS_MANAGER_RUN_LIMIT = 2
_STATUS_REPAIR_ITEM_LIMIT = 2
_COMPACT_MANAGER_ACTION_LIMIT = 6
_MANAGER_TEXT_LIMIT = 160
_REPAIR_TEXT_LIMIT = 140
_RUNNER_KEYS = {
    "alive",
    "effective_alive",
    "covered_by_alive",
    "status",
    "pid",
    "started_at",
    "last_tick_at",
    "stale_process",
    "error_message",
    "manager_due_reason",
    "last_manager_due_reason",
    "manager_error_retry_sec",
}
_RUNNER_MANAGER_RESULT_KEYS = {
    "status",
    "started_at",
    "run_id",
    "manager_run_id",
    "error_message",
    "elapsed_sec",
    "timeout_sec",
}
_MANAGER_DIAGNOSTIC_SCALAR_KEYS = (
    "version",
    "action_count",
    "jue_wiki_repair_priority_count",
    "jue_wiki_repair_action_batch_count",
    "jue_wiki_requested_symbol_coverage_status",
    "jue_wiki_attention_status",
    "jue_wiki_attention_resolution_status",
    "jue_wiki_memory_card_quality_status",
    "jue_wiki_selection_guidance_status",
    "jue_wiki_selection_guidance_resolution_status",
    "jue_wiki_context_gap_status",
    "jue_wiki_context_gap_resolution_status",
    "jue_wiki_action_reference_memory_status",
    "jue_wiki_action_reference_memory_resolution_status",
    "jue_wiki_action_reference_status",
    "jue_wiki_action_reference_count",
    "jue_wiki_action_reference_ratio",
    "degraded_jue_wiki_effectiveness_count",
    "degraded_jue_wiki_effectiveness_resolution_status",
)
_MANAGER_DIAGNOSTIC_COLLECTION_KEYS = (
    "blocker_tags",
    "top_blockers",
    "jue_wiki_missing_summary_symbols",
    "jue_wiki_prompt_omitted_symbols",
    "jue_wiki_attention_must_address",
    "jue_wiki_weak_memory_card_symbols",
    "degraded_jue_wiki_effectiveness_page_ids",
)


@dataclass(frozen=True)
class BinanceBlockRouteDeps:
    require_admin_auth: Callable[..., Any]
    blocks_snapshot: Callable[..., Any]
    validation_repair_ops_summary: Callable[..., dict[str, Any]]
    build_readiness: Callable[[dict[str, Any]], dict[str, Any]]
    quant_signals: Callable[[list[str], int], dict[str, Any]]
    pattern_context: Callable[[list[str], int], dict[str, Any]]
    manager_run_once: Callable[[], Any]
    spot_adoption_once: Callable[[], Any]
    upbit_adoption_once: Callable[[], Any] | None
    executor_tick: Callable[[], Any]
    set_kill_switch: Callable[[bool, str], dict[str, Any]]


def _short_text(value: Any, *, limit: int = _BLOCK_TEXT_LIMIT) -> str:
    text = str(value or "")
    return text[:limit]


def _compact_active_block(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    compact = {
        "block_id": row.get("block_id"),
        "symbol": row.get("symbol"),
        "market": row.get("market") or row.get("venue"),
        "side": row.get("side"),
        "status": row.get("status"),
        "horizon": row.get("horizon") or metadata.get("horizon"),
        "block_color": row.get("block_color") or metadata.get("block_color"),
        "lane": row.get("lane") or metadata.get("lane") or calculated.get("lane"),
        "qty_initial": row.get("qty_initial"),
        "qty_open": row.get("qty_open"),
        "entry_price": row.get("entry_price"),
        "target_price": row.get("target_price"),
        "stop_price": row.get("stop_price"),
        "current_price": row.get("current_price"),
        "leverage": row.get("leverage"),
        "thesis": _short_text(row.get("thesis") or row.get("llm_reason")),
        "risk_note": _short_text(row.get("risk_note")),
        "quote": _drop_empty_values({
            "price": quote.get("price"),
            "source": quote.get("source"),
            "fetched_at": quote.get("fetched_at"),
        }),
    }
    return _drop_empty_values(compact)


def _compact_history_block(row: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_active_block(row)
    compact["thesis"] = _short_text(
        row.get("thesis") or row.get("llm_reason"),
        limit=_HISTORY_TEXT_LIMIT,
    )
    compact["risk_note"] = _short_text(
        row.get("risk_note"),
        limit=_HISTORY_TEXT_LIMIT,
    )
    compact.update(
        _drop_empty_values({
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "closed_at": row.get("closed_at"),
            "realized_pnl_usdt": row.get("realized_pnl_usdt"),
            "r_multiple": row.get("r_multiple"),
        })
    )
    return _drop_empty_values(compact)


def _drop_empty_values(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {})
    }


def _compact_metric_dict(row: dict[str, Any], *, keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", [], {})
    }


def _compact_manager_diagnostic_value(value: Any, *, limit: int = 8) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[: max(int(limit), 1)]:
            clean_key = str(key or "")[:80]
            if not clean_key:
                continue
            if isinstance(item, str):
                out[clean_key] = _short_text(item, limit=_REPAIR_TEXT_LIMIT)
            elif isinstance(item, (int, float, bool)) or item is None:
                out[clean_key] = item
        return _drop_empty_values(out)
    if isinstance(value, list):
        rows: list[Any] = []
        for item in value[: max(int(limit), 1)]:
            if isinstance(item, dict):
                compact_item = _compact_manager_diagnostic_value(item, limit=6)
                if compact_item:
                    rows.append(compact_item)
            elif str(item or "").strip():
                rows.append(_short_text(item, limit=_REPAIR_TEXT_LIMIT))
        return rows
    if isinstance(value, str):
        return _short_text(value, limit=_REPAIR_TEXT_LIMIT)
    return value


def _compact_manager_diagnostics(value: Any) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    if not diagnostics:
        return {}
    compact: dict[str, Any] = {}
    for key in _MANAGER_DIAGNOSTIC_SCALAR_KEYS:
        if diagnostics.get(key) not in (None, "", [], {}):
            compact[key] = diagnostics.get(key)
    for key in _MANAGER_DIAGNOSTIC_COLLECTION_KEYS:
        if diagnostics.get(key) not in (None, "", [], {}):
            compact_value = _compact_manager_diagnostic_value(diagnostics.get(key))
            if compact_value not in (None, "", [], {}):
                compact[key] = compact_value
    return compact


def _compact_runner_status(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    compact = _compact_metric_dict(row, keys=_RUNNER_KEYS)
    for key in ("manager_result", "last_manager_result"):
        value = row.get(key)
        if isinstance(value, dict) and value:
            compact[key] = _compact_metric_dict(
                value,
                keys=_RUNNER_MANAGER_RESULT_KEYS,
            )
    return _drop_empty_values(compact)


def _compact_performance(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return _compact_metric_dict(
        row,
        keys={
            "sample_count",
            "avg_r_multiple",
            "avg_mfe_r_multiple",
            "avg_mae_r_multiple",
            "win_rate_pct",
            "realized_pnl_usdt",
            "gross_realized_pnl_usdt",
            "total_cost_usdt",
            "avg_pnl_usdt",
            "profit_factor",
            "max_drawdown_usdt",
            "max_drawdown_r_multiple",
            "recovery_factor",
        },
    )


def _compact_growth_unlock(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    compact = _compact_metric_dict(
        row,
        keys={"version", "phase", "can_leave_edge_rebuild"},
    )
    action_permissions = row.get("action_permissions")
    if isinstance(action_permissions, dict):
        compact["action_permissions"] = {
            key: value
            for key, value in action_permissions.items()
            if isinstance(value, bool)
        }
    criteria = row.get("criteria")
    if isinstance(criteria, list):
        compact_criteria = [
            _compact_metric_dict(
                criterion,
                keys={"id", "label", "current", "target", "passed"},
            )
            for criterion in criteria[:8]
            if isinstance(criterion, dict)
        ]
        if compact_criteria:
            compact["criteria"] = compact_criteria
    return compact


def _compact_growth_governor(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    compact = _compact_metric_dict(
        row,
        keys={
            "version",
            "status",
            "mode",
            "allow_new_blocks",
            "max_new_blocks",
            "require_waiting_entry",
            "aggression_multiplier",
            "positive_lane_count",
            "probation_lane_count",
            "scope",
        },
    )
    for key in ("reasons", "weak_lanes"):
        values = row.get(key)
        if isinstance(values, list):
            compact[key] = [
                value
                for value in values[:8]
                if value not in (None, "", [], {})
            ]
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        compact["metrics"] = _compact_metric_dict(
            metrics,
            keys={
                "growth_target_status",
                "required_daily_return_pct",
                "sample_count",
                "win_rate_pct",
                "avg_r_multiple",
                "realized_pnl_usdt",
                "risk_guard_status",
            },
        )
    return compact


def _compact_order(row: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_metric_dict(
        row,
        keys={
            "id",
            "block_id",
            "symbol",
            "market",
            "side",
            "qty",
            "order_type",
            "status",
            "execution_status",
            "filled_qty",
            "filled_quote",
            "avg_fill_price",
            "effective_fill",
            "reason",
            "created_at",
            "updated_at",
        },
    )
    response = row.get("response")
    if isinstance(response, dict):
        compact_response = _compact_metric_dict(
            response,
            keys={
                "order_id",
                "client_order_id",
                "status",
                "symbol",
                "market",
                "price",
                "executed_qty",
                "executedQty",
                "remaining_qty",
                "orig_qty",
            },
        )
        if compact_response:
            compact["response"] = compact_response
    return compact


def _compact_event(row: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_metric_dict(
        row,
        keys={"id", "block_id", "event_type", "message", "created_at"},
    )
    if "message" in compact:
        compact["message"] = _short_text(compact["message"], limit=_EVENT_TEXT_LIMIT)
    payload = row.get("payload")
    if isinstance(payload, dict):
        compact_payload = _compact_metric_dict(
            payload,
            keys={
                "error_message",
                "run_id",
                "venue",
                "status",
                "reason",
                "order_id",
                "symbol",
            },
        )
        if "error_message" in compact_payload:
            compact_payload["error_message"] = _short_text(
                compact_payload["error_message"],
                limit=_EVENT_TEXT_LIMIT,
            )
        if compact_payload:
            compact["payload"] = compact_payload
    return compact


def _compact_manager_action(row: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_metric_dict(
        row,
        keys={
            "block_id",
            "symbol",
            "market",
            "venue",
            "side",
            "status",
            "horizon",
            "lane",
            "entry_style",
            "entry_trigger_operator",
            "entry_trigger_price",
            "entry_price",
            "target_price",
            "stop_price",
            "qty",
            "quote_budget_usdt",
            "confidence",
            "reason",
        },
    )
    return compact


def _compact_manager_actions(actions: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("create_blocks", "update_blocks", "close_blocks", "pause_blocks"):
        values = actions.get(key)
        if not isinstance(values, list):
            continue
        compact_values = [
            _compact_manager_action(row)
            for row in values[:_COMPACT_MANAGER_ACTION_LIMIT]
            if isinstance(row, dict)
        ]
        if compact_values:
            compact[key] = compact_values
        if len(values) > _COMPACT_MANAGER_ACTION_LIMIT:
            compact[f"{key}_omitted_count"] = len(values) - _COMPACT_MANAGER_ACTION_LIMIT
    return compact


def _compact_text_list(values: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        _short_text(value, limit=_MANAGER_TEXT_LIMIT)
        for value in values[:limit]
        if str(value or "").strip()
    ]


def _compact_hold_decision(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    compact: dict[str, Any] = {}
    if row.get("summary"):
        compact["summary"] = _short_text(row.get("summary"), limit=_MANAGER_TEXT_LIMIT)
    for key in (
        "reasons",
        "risk_notes",
        "data_gaps",
        "next_triggers",
        "watch_symbols",
        "planned_actions",
    ):
        values = _compact_text_list(row.get(key))
        if values:
            compact[key] = values
    if "action_count" in row:
        compact["action_count"] = row.get("action_count")
    return compact


def _manager_run_status_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "id": row.get("id"),
        "run_id": row.get("run_id") or row.get("id"),
        "run_at": row.get("run_at"),
        "started_at": row.get("started_at") or row.get("run_at"),
        "status": row.get("status"),
        "mode": row.get("mode"),
        "model": row.get("model"),
        "error_message": (
            _short_text(row.get("error_message"), limit=_EVENT_TEXT_LIMIT)
            if row.get("error_message")
            else row.get("error_message")
        ),
        "action_count": row.get("action_count"),
    }
    diagnostics = _compact_manager_diagnostics(row.get("diagnostics"))
    if diagnostics:
        summary["diagnostics"] = diagnostics
    return summary


def compact_binance_blocks_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for row in payload.get("manager_runs") or []:
        if not isinstance(row, dict):
            continue
        actions = row.get("actions") if isinstance(row.get("actions"), dict) else {}
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        action_count = sum(
            len(actions.get(key) or [])
            for key in ("create_blocks", "update_blocks", "close_blocks", "pause_blocks")
            if isinstance(actions.get(key), list)
        )
        hold_decision = row.get("hold_decision") or response.get("hold_decision") or {}
        if action_count == 0 and not hold_decision:
            hold_decision = {
                "summary": "관망: manager returned no block actions.",
                "reasons": ["manager returned no block actions"],
                "watch_symbols": [],
                "next_triggers": [],
                "data_gaps": [],
                "risk_notes": [],
                "action_count": 0,
            }
        compact_actions = _compact_manager_actions(actions)
        diagnostics = _compact_manager_diagnostics(row.get("diagnostics"))
        compact_run = {
            "id": row.get("id"),
            "run_id": row.get("run_id") or row.get("id"),
            "run_at": row.get("run_at"),
            "started_at": row.get("started_at") or row.get("run_at"),
            "status": row.get("status"),
            "mode": row.get("mode"),
            "model": row.get("model"),
            "error_message": row.get("error_message"),
            "action_count": action_count,
            "actions": compact_actions,
            "hold_decision": _compact_hold_decision(hold_decision),
        }
        if diagnostics:
            compact_run["diagnostics"] = diagnostics
        rows.append(compact_run)
    compact = {
        key: value
        for key, value in payload.items()
        if key not in {"blocks", "manager_runs"}
    }
    compact["active_blocks"] = [
        _compact_active_block(row)
        for row in payload.get("active_blocks") or []
        if isinstance(row, dict)
    ]
    compact["block_history"] = [
        _compact_history_block(row)
        for row in (payload.get("block_history") or [])[:_COMPACT_HISTORY_LIMIT]
        if isinstance(row, dict)
    ]
    if isinstance(payload.get("orders"), list):
        compact["orders"] = [
            _compact_order(row)
            for row in payload.get("orders", [])[:_COMPACT_ORDER_LIMIT]
            if isinstance(row, dict)
        ]
    if isinstance(payload.get("events"), list):
        compact["events"] = [
            _compact_event(row)
            for row in payload.get("events", [])[:_COMPACT_EVENT_LIMIT]
            if isinstance(row, dict)
        ]
    for key in ("performance", "performance_today"):
        if isinstance(payload.get(key), dict):
            compact[key] = _compact_performance(payload[key])
    if isinstance(payload.get("growth_unlock"), dict):
        compact["growth_unlock"] = _compact_growth_unlock(payload["growth_unlock"])
    if isinstance(payload.get("growth_governor"), dict):
        compact["growth_governor"] = _compact_growth_governor(
            payload["growth_governor"]
        )
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    execution = (
        payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    )
    compact.setdefault(
        "execute_orders",
        bool(
            payload.get("execute_spot_orders")
            or payload.get("execute_futures_orders")
            or execution.get("spot_orders_enabled")
            or execution.get("futures_orders_enabled")
            or execution.get("upbit_orders_enabled")
        ),
    )
    for key in (
        "quote_interval_sec",
        "rule_interval_sec",
        "manager_interval_sec",
        "llm_timeout_ms",
        "prompt_target_chars",
        "prompt_warn_chars",
        "prompt_max_chars",
    ):
        if key in compact or key not in config:
            continue
        compact[key] = config[key]
    live_authority = payload.get("live_authority")
    if isinstance(live_authority, dict):
        compact["live_authority"] = compact_live_authority_for_status(live_authority)
    compact["manager_runs"] = rows
    if rows:
        compact["latest_manager_run"] = _manager_run_status_summary(rows[0])
        recent_errors = [
            _manager_run_status_summary(row)
            for row in rows
            if str(row.get("status") or "").lower() not in {"ok", "success"}
            or str(row.get("error_message") or "").strip()
        ]
        if recent_errors:
            compact["recent_manager_errors"] = recent_errors[:3]
    compact["compact"] = True
    return compact


def _compact_binance_active_only_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown", "compact": True, "active_only": True}
    compact: dict[str, Any] = {
        "status": payload.get("status") or "ok",
        "compact": True,
        "active_only": True,
    }
    for key in (
        "enabled",
        "execution_mode",
        "execute_orders",
        "execute_spot_orders",
        "execute_futures_orders",
        "execute_upbit_orders",
        "latest_manager_run_at",
        "latest_manager_status",
        "manager_operational_status",
        "reasoning_effort",
        "model",
        "manager_interval_sec",
        "rule_interval_sec",
        "quote_interval_sec",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    for key in (
        "account",
        "execution",
        "kill_switch",
        "risk",
        "performance_today",
        "growth_target",
        "growth_governor",
        "risk_guard",
        "lane_allocation",
        "runner",
    ):
        value = payload.get(key)
        if isinstance(value, dict) and value:
            compact[key] = value
    compact["active_blocks"] = [
        _compact_active_block(row)
        for row in payload.get("active_blocks") or []
        if isinstance(row, dict)
    ]
    return compact


def _trim_binance_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown", "compact": True}
    compact = dict(payload)
    _separate_recovered_manager_error(compact)
    for key, limit in (
        ("block_history", _STATUS_HISTORY_LIMIT),
        ("orders", _STATUS_ORDER_LIMIT),
        ("events", _STATUS_EVENT_LIMIT),
        ("manager_runs", _STATUS_MANAGER_RUN_LIMIT),
    ):
        rows = compact.get(key)
        if isinstance(rows, list):
            compact[key] = rows[:limit]
            omitted = len(rows) - limit
            if omitted > 0:
                compact[f"{key}_omitted_count"] = omitted
    repair_ops = compact.get("validation_repair_ops")
    if isinstance(repair_ops, dict):
        compact["validation_repair_ops"] = _compact_validation_repair_ops(repair_ops)
    compact["compact"] = True
    return compact


def _separate_recovered_manager_error(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    if not bool(payload.get("latest_manager_error_recovered")):
        return
    error = payload.get("latest_manager_error")
    if isinstance(error, dict) and error:
        payload["latest_recovered_manager_error"] = error
    payload.pop("latest_manager_error", None)
    payload.pop("latest_unresolved_manager_error", None)


def _compact_validation_repair_ops(payload: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_metric_dict(
        payload,
        keys={
            "version",
            "status",
            "scope",
            "limit",
            "backlog_count",
            "constraint_count",
        },
    )
    top_backlog = _compact_repair_rows(payload.get("top_backlog"))
    if top_backlog:
        compact["top_backlog"] = top_backlog
    top_constraints = _compact_repair_rows(payload.get("top_constraints"))
    if top_constraints:
        compact["top_constraints"] = top_constraints
    recovery = payload.get("recovery")
    if isinstance(recovery, dict):
        recovery_items = _compact_recovery_rows(recovery.get("items"))
        compact_recovery = _compact_metric_dict(
            recovery,
            keys={"status", "item_count", "updated_at"},
        )
        if recovery_items:
            compact_recovery["items"] = recovery_items
        if compact_recovery:
            compact["recovery"] = compact_recovery
    return compact


def _compact_repair_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows[:_STATUS_REPAIR_ITEM_LIMIT]:
        if not isinstance(row, dict):
            continue
        item = _compact_metric_dict(
            row,
            keys={
                "discipline_id",
                "policy_id",
                "priority",
                "status",
                "scale_blocker",
                "risk_budget_multiplier",
                "max_budget_multiplier",
                "min_reward_risk",
            },
        )
        for key in ("entry_bias", "target_stop_review", "sizing_policy"):
            value = row.get(key)
            if value not in (None, "", [], {}):
                item[key] = _short_text(value, limit=_REPAIR_TEXT_LIMIT)
        checks = row.get("required_checks")
        if isinstance(checks, list):
            item["required_checks"] = [
                _short_text(check, limit=80)
                for check in checks[:2]
                if str(check or "").strip()
            ]
        if item:
            compact.append(item)
    return compact


def _live_execution_enabled(readiness: dict[str, Any]) -> bool:
    if not isinstance(readiness, dict):
        return False
    execution = (
        readiness.get("execution")
        if isinstance(readiness.get("execution"), dict)
        else {}
    )
    return any(
        str(execution.get(key) or "").strip().lower() == "live"
        for key in ("spot_mode", "futures_mode", "upbit_spot_mode")
    )


def _compact_recovery_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows[:_STATUS_REPAIR_ITEM_LIMIT]:
        if not isinstance(row, dict):
            continue
        item = _compact_metric_dict(
            row,
            keys={"discipline_id", "policy_id", "status"},
        )
        responses = row.get("current_jue_response")
        if isinstance(responses, list):
            item["current_jue_response"] = [
                _short_text(response, limit=80)
                for response in responses[:2]
                if str(response or "").strip()
            ]
        elif responses not in (None, "", [], {}):
            item["current_jue_response"] = [
                _short_text(responses, limit=80),
            ]
        if item:
            compact.append(item)
    return compact


def build_binance_blocks_router(deps: BinanceBlockRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/binance/blocks/status")
    async def binance_blocks_status(
        compact: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = await _maybe_await(deps.blocks_snapshot(compact=compact))
        if not isinstance(payload, dict):
            payload = {"status": "ok", "blocks": []}
        payload["validation_repair_ops"] = deps.validation_repair_ops_summary(
            target_scope="binance",
            limit=4,
        )
        readiness = deps.build_readiness(payload)
        runner = readiness.get("runner")
        if not isinstance(runner, dict):
            runner = (readiness.get("binance_block_trader") or {}).get("runner")
        if isinstance(runner, dict):
            payload["runner"] = _compact_runner_status(runner)
        payload["readiness"] = readiness
        activity_pressure = readiness.get("activity_pressure")
        if (
            isinstance(activity_pressure, dict)
            and activity_pressure
            and not isinstance(payload.get("activity_pressure"), dict)
        ):
            payload["activity_pressure"] = activity_pressure
        if compact:
            payload = _trim_binance_status_payload(payload)
        return payload

    @router.get("/api/binance/blocks")
    async def binance_blocks(
        compact: bool = False,
        active_only: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = await _maybe_await(deps.blocks_snapshot(compact=compact))
        if compact and active_only:
            return _compact_binance_active_only_payload(payload)
        return payload if isinstance(payload, dict) else {"status": "ok", "blocks": []}

    @router.get("/api/binance/quant/signals")
    async def binance_quant_signals(
        symbols: str = "",
        limit: int = 16,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        clean_symbols = _parse_symbols(symbols)
        return deps.quant_signals(clean_symbols, max(min(int(limit), 100), 1))

    @router.get("/api/binance/patterns/context")
    def binance_pattern_context(
        symbols: str = "",
        limit: int = 12,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        clean_symbols = _parse_symbols(symbols)
        return deps.pattern_context(clean_symbols, max(min(int(limit), 50), 1))

    @router.post("/api/binance/blocks/manager/run-once")
    async def binance_blocks_manager_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        if (
            not bool(body.get("confirm_live_manager_run"))
            and _live_execution_enabled(deps.build_readiness({}))
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "binance manager run requires confirmation while live crypto "
                    "execution is enabled"
                ),
            )
        return await _maybe_await(deps.manager_run_once())

    @router.post("/api/binance/blocks/adopt-existing/run-once")
    async def binance_blocks_adopt_existing_run_once(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        spot_result = await _maybe_await(deps.spot_adoption_once())
        if deps.upbit_adoption_once is None:
            return spot_result
        return {
            "binance_spot": spot_result,
            "upbit_spot": await _maybe_await(deps.upbit_adoption_once()),
        }

    @router.post("/api/binance/blocks/executor/tick")
    async def binance_blocks_executor_tick(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        if (
            not bool(body.get("confirm_live_executor_tick"))
            and _live_execution_enabled(deps.build_readiness({}))
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "binance executor tick requires confirmation while live crypto "
                    "execution is enabled"
                ),
            )
        return await _maybe_await(deps.executor_tick())

    @router.post("/api/binance/blocks/kill-switch")
    async def binance_blocks_kill_switch(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual")
        return {
            "status": "ok",
            "kill_switch": deps.set_kill_switch(True, reason),
        }

    @router.post("/api/binance/blocks/kill-switch/release")
    async def binance_blocks_kill_switch_release(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual_release")
        return {
            "status": "ok",
            "kill_switch": deps.set_kill_switch(False, reason),
        }

    return router


def _parse_symbols(symbols: str | None) -> list[str]:
    return [
        symbol.strip().upper()
        for symbol in re.split(r"[\s,;]+", str(symbols or ""))
        if symbol.strip()
    ]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
