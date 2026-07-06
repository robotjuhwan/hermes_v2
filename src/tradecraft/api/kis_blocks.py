from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from tradecraft.services.kis_snapshot import (
    VISIBLE_BLOCK_STATUSES,
    compact_kis_manager_run,
)
from tradecraft.services.live_authority import compact_live_authority_for_status

_TEXT_LIMIT = 220
_ACTIVE_TEXT_LIMIT = 120
_HISTORY_TEXT_LIMIT = 140
_EVENT_TEXT_LIMIT = 240
_REPAIR_ITEM_LIMIT = 2
_REPAIR_TEXT_LIMIT = 60
_STATUS_PASSTHROUGH_KEYS = {
    "status",
    "db_path",
    "block_count",
    "open_block_count",
    "waiting_entry_block_count",
    "order_count",
    "pending_order_count",
    "manager_run_count",
    "latest_manager_run_at",
    "latest_manager_status",
    "latest_manager_mode",
    "enabled",
    "execution_mode",
    "execute_orders",
    "clock",
    "kis_ready",
    "llm_ready",
    "model",
    "reasoning_effort",
    "config",
    "kill_switch",
    "latest_decision_input",
}
_BLOCK_KEYS = {
    "block_id",
    "symbol",
    "name",
    "status",
    "horizon",
    "block_color",
    "qty_initial",
    "qty_open",
    "entry_price",
    "target_price",
    "stop_price",
    "current_price",
    "created_at",
    "updated_at",
    "closed_at",
    "realized_pnl_krw",
    "r_multiple",
}
_COMPACT_METADATA_KEYS = {
    "allocation_reason",
    "decision_class",
    "entry_trigger_operator",
    "entry_trigger_price",
    "entry_trigger_status",
    "horizon",
    "max_loss_krw",
    "stop_policy",
    "user_preferred_horizon",
    "what_would_change_my_mind",
}
_HISTORY_METADATA_KEYS = {
    "allocation_reason",
    "decision_class",
    "entry_trigger_operator",
    "entry_trigger_price",
    "entry_trigger_status",
    "horizon",
    "max_loss_krw",
    "stop_policy",
    "user_preferred_horizon",
    "what_would_change_my_mind",
}
_HISTORY_METADATA_TEXT_KEYS = {
    "allocation_reason",
    "what_would_change_my_mind",
}
_ORDER_KEYS = {
    "id",
    "block_id",
    "symbol",
    "side",
    "qty",
    "limit_price",
    "avg_fill_price",
    "filled_qty",
    "remaining_qty",
    "status",
    "reason",
    "created_at",
    "updated_at",
}
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
}
_EVENT_KEYS = {
    "id",
    "block_id",
    "symbol",
    "event_type",
    "message",
    "created_at",
}
_ACCOUNT_KEYS = {
    "status",
    "captured_at",
    "account_label",
    "cash_krw",
    "settled_cash_krw",
    "orderable_cash_krw",
    "receivable_cash_krw",
    "position_value_krw",
    "total_value_krw",
    "total_asset_krw",
    "position_count",
}
_ACCOUNT_POSITION_KEYS = {
    "symbol",
    "name",
    "qty",
    "available_qty",
    "avg_price",
    "mark_price",
    "value_krw",
    "unrealized_pnl_krw",
    "unrealized_pnl_pct",
    "position_weight",
}
_READINESS_KEYS = {
    "status",
    "blockers",
    "warnings",
    "advisories",
    "trading_validation_advisories",
    "stale_processes",
    "missing_processes",
    "restart_required",
}
_READINESS_SECTION_KEYS = {
    "kis_block_trader",
    "market_judge",
    "market_pulse",
    "investment_memory",
    "naver_reports",
    "strategy_insights",
}
_SECTION_KEYS = {
    "status",
    "alive",
    "pid",
    "started_at",
    "stale_process",
    "next_manager_run_at",
    "next_llm_due_at",
    "last_tick_at",
    "last_run_at",
    "enabled",
    "running",
}
_SCHEDULE_KEYS = {
    "next_llm_due_at",
    "next_quote_due_at",
    "next_run_at",
    "next_pre_open_at",
    "next_midday_at",
    "next_post_close_at",
}

@dataclass(frozen=True)
class KISBlockRouteDeps:
    require_admin_auth: Callable[..., Any]
    primary_ready: Callable[[], bool]
    status: Callable[[], dict[str, Any]]
    snapshot: Callable[[], Any]
    attach_block_memory: Callable[[dict[str, Any]], dict[str, Any]]
    validation_repair_ops_summary: Callable[..., dict[str, Any]]
    ops_readiness: Callable[[], dict[str, Any]]
    manager_run_once: Callable[[], Any]
    adoption_run_once: Callable[[], Any]
    executor_tick: Callable[..., Any]
    set_kill_switch: Callable[[bool, str], dict[str, Any]]
    cancel_order: Callable[..., Any]
    block_detail: Callable[[str], dict[str, Any]]
    block_memory: Callable[[str], dict[str, Any]]
    add_user_directive: Callable[..., dict[str, Any]]
    pause_block: Callable[[str, str], dict[str, Any]]
    resume_block: Callable[[str, str], dict[str, Any]]
    close_block: Callable[..., Any]
    status_readiness: Callable[[], dict[str, Any]] | None = None
    snapshot_compact: Callable[[], Any] | None = None


def build_kis_blocks_router(deps: KISBlockRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/kis/blocks/status")
    async def kis_blocks_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = _compact_kis_status_payload(deps.status())
        payload["validation_repair_ops"] = _compact_validation_repair_ops(
            deps.validation_repair_ops_summary(
                target_scope="kis",
                limit=4,
            )
        )
        readiness = (
            deps.status_readiness()
            if deps.status_readiness is not None
            else deps.ops_readiness()
        )
        payload["next_manager_run_at"] = (
            readiness.get("kis_block_trader") or {}
        ).get("next_manager_run_at", "")
        payload["next_market_judge"] = (
            (readiness.get("market_judge") or {}).get("schedule") or {}
        ).get("next_llm_due_at", "")
        runner = (readiness.get("kis_block_trader") or {}).get("runner")
        if isinstance(runner, dict):
            payload["runner"] = _compact_runner_status(runner)
        payload["stale_processes"] = readiness.get("stale_processes") or []
        payload["readiness"] = _compact_ops_readiness(readiness)
        return payload

    @router.get("/api/kis/blocks")
    async def kis_blocks(
        compact: bool = False,
        active_only: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        if compact and active_only:
            payload = (
                await _maybe_await(
                    _call_snapshot_compact(deps.snapshot_compact, refresh_live=False)
                )
                if deps.snapshot_compact is not None
                else deps.attach_block_memory(await _maybe_await(deps.snapshot()))
            )
            payload["validation_repair_ops"] = deps.validation_repair_ops_summary(
                target_scope="kis",
                limit=4,
            )
            return _compact_kis_blocks_payload(payload, active_only=True)
        payload = deps.attach_block_memory(await _maybe_await(deps.snapshot()))
        payload["validation_repair_ops"] = deps.validation_repair_ops_summary(
            target_scope="kis",
            limit=4,
        )
        if compact:
            return _compact_kis_blocks_payload(payload)
        return payload

    @router.post("/api/kis/blocks/manager/run-once")
    async def kis_blocks_manager_run_once(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        _ensure_primary_ready(deps)
        return await _maybe_await(deps.manager_run_once())

    @router.post("/api/kis/blocks/adopt-existing/run-once")
    async def kis_blocks_adopt_existing_run_once(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        _ensure_primary_ready(deps)
        return await _maybe_await(deps.adoption_run_once())

    @router.post("/api/kis/blocks/executor/tick")
    async def kis_blocks_executor_tick(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        _ensure_primary_ready(deps)
        return await _maybe_await(deps.executor_tick(manual=True))

    @router.post("/api/kis/blocks/kill-switch")
    async def kis_blocks_kill_switch(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual")
        return {
            "status": "ok",
            "kill_switch": deps.set_kill_switch(True, reason),
        }

    @router.post("/api/kis/blocks/kill-switch/release")
    async def kis_blocks_kill_switch_release(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual_release")
        return {
            "status": "ok",
            "kill_switch": deps.set_kill_switch(False, reason),
        }

    @router.post("/api/kis/blocks/orders/{order_id}/cancel")
    async def kis_block_order_cancel(
        order_id: int,
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual_cancel")
        result = await _maybe_await(deps.cancel_order(order_id, reason=reason))
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="order not found")
        return result

    @router.get("/api/kis/blocks/{block_id}")
    async def kis_block_detail(
        block_id: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.block_detail(block_id)
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="block not found")
        result["memory"] = deps.block_memory(block_id)
        return result

    @router.post("/api/kis/blocks/{block_id}/directive")
    async def kis_block_directive(
        block_id: str,
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        row = payload or {}
        result = deps.add_user_directive(
            block_id,
            message=str(row.get("message") or ""),
            preferred_horizon=str(row.get("preferred_horizon") or ""),
            scope=str(row.get("scope") or "block"),
            source="ui",
        )
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="block not found")
        if result.get("status") == "rejected":
            raise HTTPException(
                status_code=400,
                detail=result.get("reason") or "directive rejected",
            )
        return result

    @router.post("/api/kis/blocks/{block_id}/pause")
    async def kis_block_pause(
        block_id: str,
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual_pause")
        result = deps.pause_block(block_id, reason)
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="block not found")
        return result

    @router.post("/api/kis/blocks/{block_id}/resume")
    async def kis_block_resume(
        block_id: str,
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        reason = str((payload or {}).get("reason") or "manual_resume")
        result = deps.resume_block(block_id, reason)
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="block not found")
        return result

    @router.post("/api/kis/blocks/{block_id}/close")
    async def kis_block_close(
        block_id: str,
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        _ensure_primary_ready(deps)
        reason = str((payload or {}).get("reason") or "manual_close")
        result = await _maybe_await(deps.close_block(block_id, reason=reason))
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="block not found")
        return result

    return router


def _ensure_primary_ready(deps: KISBlockRouteDeps) -> None:
    if not deps.primary_ready():
        raise HTTPException(
            status_code=400,
            detail="kis primary account not configured",
        )


def _call_snapshot_compact(
    snapshot_compact: Callable[..., Any],
    *,
    refresh_live: bool,
) -> Any:
    try:
        signature = inspect.signature(snapshot_compact)
    except (TypeError, ValueError):
        return snapshot_compact()
    params = signature.parameters
    if "refresh_live" in params or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
    ):
        return snapshot_compact(refresh_live=refresh_live)
    return snapshot_compact()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _clean_text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    return str(value or "")[: max(int(limit), 0)]


def _compact_kis_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown"}
    summary = payload.get("summary")
    status_source = summary if isinstance(summary, dict) else payload
    compact = {
        key: value
        for key, value in status_source.items()
        if key in _STATUS_PASSTHROUGH_KEYS and value not in (None, "", [], {})
    }
    if "status" not in compact and payload.get("status") not in (None, "", [], {}):
        compact["status"] = payload["status"]
    config = status_source.get("config")
    if isinstance(config, dict):
        for source_key, target_key in (
            ("manager_interval_sec", "manager_interval_sec"),
            ("rule_interval_sec", "rule_interval_sec"),
        ):
            value = config.get(source_key)
            if value not in (None, "", [], {}):
                compact[target_key] = value
    account = payload.get("account")
    if isinstance(account, dict):
        compact["account"] = _compact_account(account)
    runner = payload.get("runner")
    if isinstance(runner, dict):
        compact["runner"] = {
            key: value
            for key, value in runner.items()
            if key in {"status", "started_at", "last_tick_at", "error_message"}
            and value not in (None, "", [], {})
        }
    for key in ("open_blocks", "blocks", "block_history"):
        rows = payload.get(key)
        if isinstance(rows, list):
            compact[key] = [
                _compact_block(row) for row in rows[:80] if isinstance(row, dict)
            ]
    manager_runs = payload.get("manager_runs")
    if isinstance(manager_runs, list):
        compact["manager_runs"] = [
            compact_kis_manager_run(row) for row in manager_runs[:12] if isinstance(row, dict)
        ]
    live_authority = payload.get("live_authority")
    if isinstance(live_authority, dict):
        compact["live_authority"] = compact_live_authority_for_status(live_authority)
    return compact


def _compact_runner_status(runner: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in runner.items()
        if key in _RUNNER_KEYS and value not in (None, "", [], {})
    }


def _compact_account(account: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in account.items()
        if key in _ACCOUNT_KEYS and value not in (None, "", [], {})
    }
    positions = account.get("positions")
    if isinstance(positions, list):
        light_positions = [
            _compact_account_position(row)
            for row in positions[:30]
            if isinstance(row, dict)
        ]
        if light_positions:
            compact["positions"] = light_positions
    return compact


def _compact_account_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in _ACCOUNT_POSITION_KEYS and value not in (None, "", [], {})
    }


def _compact_kis_blocks_payload(
    payload: dict[str, Any],
    *,
    active_only: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown", "compact": True}
    compact: dict[str, Any] = {"compact": True}
    if active_only:
        compact["active_only"] = True
    for key in ("status", "updated_at", "memory_attached"):
        if payload.get(key) not in (None, "", [], {}):
            compact[key] = payload[key]
    summary = payload.get("summary")
    if isinstance(summary, dict):
        compact_summary = _compact_kis_status_payload({"summary": summary})
        compact["summary"] = compact_summary
        for key, value in compact_summary.items():
            if key not in compact and key != "account":
                compact[key] = value
    account = payload.get("account")
    if isinstance(account, dict):
        compact["account"] = _compact_account(account)
    active_rows, history_rows = _split_compact_block_rows(payload)
    if active_rows:
        block_compactor = _compact_active_block if active_only else _compact_block
        compact["active_blocks"] = [
            block_compactor(row) for row in active_rows if isinstance(row, dict)
        ]
    if history_rows:
        compact["block_history"] = [
            _compact_history_block(row) for row in history_rows if isinstance(row, dict)
        ]
    allocation = payload.get("allocation")
    if isinstance(allocation, dict):
        compact["allocation"] = _compact_allocation(allocation)
    horizon_allocation = payload.get("horizon_allocation")
    if isinstance(horizon_allocation, dict):
        compact["horizon_allocation"] = horizon_allocation
    if active_only:
        latest_manager_run = payload.get("latest_manager_run")
        if isinstance(latest_manager_run, dict):
            compact["latest_manager_run"] = _compact_active_manager_run(latest_manager_run)
        validation_repair_ops = payload.get("validation_repair_ops")
        if isinstance(validation_repair_ops, dict):
            compact["validation_repair_ops"] = _compact_validation_repair_ops(
                validation_repair_ops
            )
        memory = payload.get("memory")
        if isinstance(memory, dict):
            compact["memory"] = _compact_memory_links(memory)
        for key in ("total_count", "open_total_count", "open_count", "closed_sample_count"):
            value = payload.get(key)
            if value not in (None, "", [], {}):
                compact[key] = value
        return compact
    orders = payload.get("orders")
    if isinstance(orders, list):
        compact["orders"] = [
            _compact_row(row, _ORDER_KEYS) for row in orders[:200] if isinstance(row, dict)
        ]
    events = payload.get("events")
    if isinstance(events, list):
        compact["events"] = [
            _compact_event(row) for row in events[:200] if isinstance(row, dict)
        ]
    latest_manager_run = payload.get("latest_manager_run")
    if isinstance(latest_manager_run, dict):
        compact["latest_manager_run"] = compact_kis_manager_run(latest_manager_run)
    validation_repair_ops = payload.get("validation_repair_ops")
    if isinstance(validation_repair_ops, dict):
        compact["validation_repair_ops"] = _compact_validation_repair_ops(
            validation_repair_ops
        )
    memory = payload.get("memory")
    if isinstance(memory, dict):
        compact["memory"] = _compact_memory_links(memory)
    return compact


def _split_compact_block_rows(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocks = [
        row
        for row in (payload.get("blocks") if isinstance(payload.get("blocks"), list) else [])
        if isinstance(row, dict)
    ]
    active_rows = (
        [
            row
            for row in payload.get("active_blocks", [])
            if isinstance(row, dict)
        ]
        if isinstance(payload.get("active_blocks"), list)
        else [
            row
            for row in blocks
            if str(row.get("status") or "") in VISIBLE_BLOCK_STATUSES
        ]
    )
    history_rows = (
        [
            row
            for row in payload.get("block_history", [])
            if isinstance(row, dict)
        ]
        if isinstance(payload.get("block_history"), list)
        else [
            row
            for row in blocks
            if str(row.get("status") or "") not in VISIBLE_BLOCK_STATUSES
        ][:50]
    )
    return active_rows, history_rows


def _compact_active_manager_run(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict) or row.get("status") == "missing":
        return {"status": "missing"}
    actions = row.get("actions") if isinstance(row.get("actions"), dict) else {}
    action_counts = {
        key: len(value)
        for key, value in actions.items()
        if isinstance(value, list) and value
    }
    applied = row.get("applied") if isinstance(row.get("applied"), dict) else {}
    compact = {
        "id": row.get("id"),
        "run_at": row.get("run_at"),
        "market_session": row.get("market_session"),
        "status": row.get("status"),
        "mode": row.get("mode"),
        "model": row.get("model"),
        "error_message": _clean_text(row.get("error_message"), limit=500),
        "workflow_id": row.get("workflow_id"),
        "workflow_version": row.get("workflow_version"),
        "skill_ids": list(row.get("skill_ids") or [])[:4],
        "contract_ids": list(row.get("contract_ids") or [])[:4],
        "action_counts": action_counts,
    }
    if applied:
        compact["applied"] = {
            key: value
            for key, value in applied.items()
            if key in {"status", "created_count", "updated_count", "closed_count"}
            and value not in (None, "", [], {})
        }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _compact_active_block(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in row.items()
        if key in _BLOCK_KEYS and value not in (None, "", [], {})
    }
    thesis = _clean_text(row.get("thesis") or row.get("llm_reason"), limit=_ACTIVE_TEXT_LIMIT)
    if thesis:
        compact["thesis"] = thesis
    risk_note = _clean_text(row.get("risk_note"), limit=_ACTIVE_TEXT_LIMIT)
    if risk_note:
        compact["risk_note"] = risk_note
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        compact_metadata = _compact_active_metadata(metadata)
        if compact_metadata:
            compact["metadata"] = compact_metadata
    for key in (
        "quote",
        "performance",
        "reflection_status",
        "memory_links",
        "next_rule_action",
        "rule_exit_mode",
        "created_by",
    ):
        value = row.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "quote" and isinstance(value, dict):
            compact_value = _compact_quote(value)
        elif key == "performance" and isinstance(value, dict):
            compact_value = _compact_performance(value)
        else:
            compact_value = value
        if compact_value not in (None, "", [], {}):
            compact[key] = compact_value
    return compact


def _compact_block(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in row.items()
        if key in _BLOCK_KEYS and value not in (None, "", [], {})
    }
    thesis = _clean_text(row.get("thesis") or row.get("llm_reason"))
    if thesis:
        compact["thesis"] = thesis
    risk_note = _clean_text(row.get("risk_note"))
    if risk_note:
        compact["risk_note"] = risk_note
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        compact_metadata = _compact_block_metadata(metadata)
        if compact_metadata:
            compact["metadata"] = compact_metadata
    for key in (
        "quote",
        "performance",
        "reflection_status",
        "memory_links",
        "next_rule_action",
        "rule_exit_mode",
        "created_by",
    ):
        value = row.get(key)
        if value not in (None, "", [], {}):
            if key == "quote" and isinstance(value, dict):
                compact_value = _compact_quote(value)
            elif key == "performance" and isinstance(value, dict):
                compact_value = _compact_performance(value)
            else:
                compact_value = value
            if compact_value not in (None, "", [], {}):
                compact[key] = compact_value
    policy_impacts = row.get("policy_impacts")
    if isinstance(policy_impacts, list):
        compact["policy_impacts"] = [
            _compact_policy_impact(item) for item in policy_impacts[:3] if isinstance(item, dict)
        ]
    return compact


def _compact_history_block(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in row.items()
        if key in _BLOCK_KEYS and value not in (None, "", [], {})
    }
    thesis = _clean_text(row.get("thesis") or row.get("llm_reason"), limit=_HISTORY_TEXT_LIMIT)
    if thesis:
        compact["thesis"] = thesis
    risk_note = _clean_text(row.get("risk_note"), limit=_HISTORY_TEXT_LIMIT)
    if risk_note:
        compact["risk_note"] = risk_note
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        compact_metadata = _compact_history_metadata(metadata)
        if compact_metadata:
            compact["metadata"] = compact_metadata
    for key in (
        "reflection_status",
        "memory_links",
        "next_rule_action",
        "rule_exit_mode",
        "created_by",
    ):
        value = row.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    return compact


def _compact_row(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", [], {})
    }


def _compact_allocation(allocation: dict[str, Any]) -> dict[str, Any]:
    compact = dict(allocation)
    items = allocation.get("items")
    if isinstance(items, list):
        compact["items"] = [
            item
            for item in items
            if isinstance(item, dict) and _allocation_item_has_quantity(item)
        ]
    return compact


def _allocation_item_has_quantity(item: dict[str, Any]) -> bool:
    return any(
        _safe_float(item.get(key)) > 0
        for key in (
            "account_qty",
            "block_qty",
            "unallocated_qty",
            "overallocated_qty",
        )
    )


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            if not value or value == "-":
                return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_event(row: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_row(row, _EVENT_KEYS)
    if "message" in compact:
        message = _clean_text(compact.get("message"), limit=_EVENT_TEXT_LIMIT)
        if message:
            compact["message"] = message
        else:
            compact.pop("message", None)
    return compact


def _compact_validation_repair_ops(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    compact = _compact_metric_dict(
        payload,
        keys={
            "version",
            "status",
            "scope",
            "target_scope",
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
        compact_recovery = _compact_metric_dict(
            recovery,
            keys={"status", "item_count", "updated_at"},
        )
        recovery_items = _compact_recovery_rows(recovery.get("items"))
        if recovery_items:
            compact_recovery["items"] = recovery_items
        if compact_recovery:
            compact["recovery"] = compact_recovery
    return compact


def _compact_repair_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows[:_REPAIR_ITEM_LIMIT]:
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
                item[key] = _clean_text(value, limit=_REPAIR_TEXT_LIMIT)
        checks = row.get("required_checks")
        if isinstance(checks, list):
            item["required_checks"] = [
                _clean_text(check, limit=50)
                for check in checks[:2]
                if str(check or "").strip()
            ]
        if item:
            compact.append(item)
    return compact


def _compact_recovery_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows[:_REPAIR_ITEM_LIMIT]:
        if not isinstance(row, dict):
            continue
        item = _compact_metric_dict(
            row,
            keys={"discipline_id", "policy_id", "status"},
        )
        responses = row.get("current_jue_response")
        if isinstance(responses, list):
            item["current_jue_response"] = [
                _clean_text(response, limit=60)
                for response in responses[:2]
                if str(response or "").strip()
            ]
        elif responses not in (None, "", [], {}):
            item["current_jue_response"] = [_clean_text(responses, limit=60)]
        if item:
            compact.append(item)
    return compact


def _compact_metric_dict(row: dict[str, Any], *, keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", [], {})
    }


def _compact_block_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in metadata.items()
        if key in _COMPACT_METADATA_KEYS and value not in (None, "", [], {})
    }
    for key in (
        "cost_feasibility",
        "live_authority",
        "policy_effect_audit",
        "user_directive_latest",
    ):
        value = metadata.get(key)
        if not isinstance(value, dict):
            continue
        if key == "live_authority":
            compact_value = _compact_block_live_authority(value)
        elif key == "policy_effect_audit":
            compact_value = _compact_policy_effect_audit(value)
        elif key == "cost_feasibility":
            compact_value = _compact_cost_feasibility(value)
        else:
            compact_value = value
        if compact_value:
            compact[key] = compact_value
    directives = metadata.get("user_directives")
    if isinstance(directives, list):
        compact["user_directives"] = [
            item for item in directives[:3] if isinstance(item, dict)
        ]
    applied = metadata.get("applied_policy_versions")
    if isinstance(applied, list):
        compact["applied_policy_versions"] = [
            str(value) for value in applied[:8] if str(value or "").strip()
        ]
    return compact


def _compact_active_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in _COMPACT_METADATA_KEYS or value in (None, "", [], {}):
            continue
        if key in {"allocation_reason", "what_would_change_my_mind"}:
            compact_value = _clean_text(value, limit=_ACTIVE_TEXT_LIMIT)
        else:
            compact_value = value
        if compact_value not in (None, "", [], {}):
            compact[key] = compact_value
    latest = metadata.get("user_directive_latest")
    if isinstance(latest, dict):
        compact_latest = {
            key: _clean_text(value, limit=_ACTIVE_TEXT_LIMIT) if key == "message" else value
            for key, value in latest.items()
            if key in {"message", "preferred_horizon", "scope", "created_at"}
            and value not in (None, "", [], {})
        }
        if compact_latest:
            compact["user_directive_latest"] = compact_latest
    return compact


def _compact_history_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in _HISTORY_METADATA_KEYS or value in (None, "", [], {}):
            continue
        if key in _HISTORY_METADATA_TEXT_KEYS:
            compact_value = _clean_text(value, limit=_HISTORY_TEXT_LIMIT)
        else:
            compact_value = value
        if compact_value not in (None, "", [], {}):
            compact[key] = compact_value
    latest = metadata.get("user_directive_latest")
    if isinstance(latest, dict):
        compact_latest = {
            key: _clean_text(value, limit=_HISTORY_TEXT_LIMIT) if key == "message" else value
            for key, value in latest.items()
            if key in {"message", "preferred_horizon", "scope", "created_at"}
            and value not in (None, "", [], {})
        }
        if compact_latest:
            compact["user_directive_latest"] = compact_latest
    return compact


def _compact_quote(quote: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in quote.items()
        if key
        in {
            "symbol",
            "name",
            "price",
            "change",
            "change_pct",
            "open_price",
            "high_price",
            "low_price",
            "volume",
            "trading_value",
            "source",
            "fetched_at",
            "status",
            "error_message",
        }
        and value not in (None, "", [], {})
    }


def _compact_performance(performance: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in performance.items()
        if key
        in {
            "entry_price",
            "current_price",
            "peak_price",
            "trough_price",
            "mfe_pct",
            "mae_pct",
            "current_pnl_pct",
            "current_return_pct",
            "giveback_pct",
        }
        and value not in (None, "", [], {})
    }


def _compact_cost_feasibility(cost: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in cost.items()
        if key
        in {
            "status",
            "net_target_profit_after_cost_krw",
            "target_cost_multiple",
            "design_note",
            "gross_target_profit_krw",
            "target_round_trip_cost_krw",
            "net_stop_loss_after_cost_krw",
        }
        and value not in (None, "", [], {})
    }


def _compact_block_live_authority(payload: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in payload.items()
        if key
        in {
            "validation_gate_status",
            "validation_gate_reason",
            "expected_discipline_count",
            "discipline_count",
        }
        and value not in (None, "", [], {})
    }
    matrix = payload.get("discipline_matrix")
    if isinstance(matrix, dict):
        summary = matrix.get("summary")
        compact_matrix: dict[str, Any] = {}
        if isinstance(summary, dict):
            compact_matrix["summary"] = {
                key: value
                for key, value in summary.items()
                if key
                in {
                    "pass_count",
                    "warn_count",
                    "fail_count",
                    "missing_count",
                    "readiness",
                    "score",
                }
                and value not in (None, "", [], {})
            }
        for key in ("expected_count", "actual_count", "row_detail_count", "row_detail_complete"):
            if matrix.get(key) not in (None, "", [], {}):
                compact_matrix[key] = matrix[key]
        if compact_matrix:
            compact["discipline_matrix"] = compact_matrix
    passport = payload.get("validation_passport")
    if isinstance(passport, dict):
        compact["validation_passport"] = {
            key: value
            for key, value in passport.items()
            if key
            in {
                "status",
                "readiness",
                "score",
                "expected_count",
                "actual_count",
                "row_detail_count",
                "row_detail_complete",
                "failed_ids",
                "weak_ids",
                "requires_revalidation",
                "risk_governor_action",
                "version",
            }
            and value not in (None, "", [], {})
        }
    return compact


def _compact_policy_effect_audit(audit: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in audit.items()
        if key in {"version", "mode", "affected_fields"} and value not in (None, "", [], {})
    }
    rules = audit.get("rules")
    if isinstance(rules, list):
        compact["rules"] = [
            _compact_policy_impact(row) for row in rules[:8] if isinstance(row, dict)
        ]
    return compact


def _compact_policy_impact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in {"rule_id", "policy_id", "field", "action", "reason"}
        and value not in (None, "", [], {})
    }


def _compact_memory_links(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in memory.items()
        if key in {"status", "links", "lessons", "updated_at"} and value not in (None, "", [], {})
    }


def _compact_ops_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(readiness, dict):
        return {}
    compact = {
        key: value
        for key, value in readiness.items()
        if key in _READINESS_KEYS and value not in (None, "", {})
    }
    for key in _READINESS_SECTION_KEYS:
        section = readiness.get(key)
        if isinstance(section, dict):
            compact_section = _compact_readiness_section(section)
            if compact_section:
                compact[key] = compact_section
    return compact


def _compact_readiness_section(section: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in section.items()
        if key in _SECTION_KEYS
        and isinstance(value, (str, int, float, bool))
        and value not in (None, "")
    }
    schedule = section.get("schedule")
    if isinstance(schedule, dict):
        compact_schedule = {
            key: value
            for key, value in schedule.items()
            if key in _SCHEDULE_KEYS and value not in (None, "", [], {})
        }
        if compact_schedule:
            compact["schedule"] = compact_schedule
    return compact
