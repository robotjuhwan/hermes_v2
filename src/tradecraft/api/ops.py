from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


_TEXT_LIMIT = 220
_TOP_LEVEL_KEYS = {
    "status",
    "checked_at",
    "live_trading_enabled",
    "paper_mode",
    "admin_token_configured",
    "telegram_ready",
    "kis_ready",
    "llm_ready",
    "next_market_open_at",
}
_SIGNAL_LIST_KEYS = {
    "blockers",
    "warnings",
    "advisories",
    "trading_validation_advisories",
    "stale_processes",
    "missing_processes",
    "duplicate_processes",
}
_PROCESS_KEYS = {
    "key",
    "label",
    "status",
    "alive",
    "effective_alive",
    "covered_by_alive",
    "direct_alive",
    "pid",
    "started_at",
    "pid_file_pid",
    "pid_file_status",
    "matched_count",
    "code_mtime",
    "stale_process",
    "last_tick_at",
    "error_message",
}
_SECTION_KEYS = {
    "status",
    "enabled",
    "running",
    "seeded",
    "alive",
    "pid",
    "started_at",
    "last_tick_at",
    "last_run_at",
    "latest_run_at",
    "latest_run_status",
    "latest_manager_run_at",
    "latest_manager_status",
    "next_manager_run_at",
    "next_llm_due_at",
    "model",
    "reasoning_effort",
    "execution_mode",
    "execute_orders",
    "reflection_count",
    "pending_reflection_count",
    "scorecard_count",
    "policy_rule_count",
    "active_policy_rule_count",
    "validation_repair_backlog_status",
    "validation_repair_backlog_count",
}
_STATUS_KEYS = {
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
    "execution_mode",
    "execute_orders",
    "kis_ready",
    "llm_ready",
    "model",
    "enabled",
    "queued_count",
    "failed_count",
}
_CONFIG_KEYS = {
    "quote_interval_sec",
    "judge_interval_sec",
    "manager_interval_sec",
    "rule_interval_sec",
    "max_symbols",
    "llm_max_symbols",
    "max_manager_symbols",
    "interval_sec",
    "db_path",
    "autonomy_mode",
    "max_tasks_per_cycle",
    "max_patch_bytes",
    "market_hours_hot_deploy",
}
_SCHEDULE_KEYS = {
    "status",
    "quote_interval_sec",
    "judge_interval_sec",
    "latest_llm_run_at",
    "seconds_since_llm",
    "next_llm_due_at",
    "next_quote_due_at",
    "next_run_at",
    "next_pre_open_at",
    "next_midday_at",
    "next_post_close_at",
}
_DISK_KEYS = {
    "status",
    "path",
    "total_bytes",
    "used_bytes",
    "free_bytes",
    "free_pct",
    "warn_free_bytes",
    "critical_free_bytes",
}
_ADVISORY_KEYS = {
    "signal",
    "venue",
    "readiness",
    "diagnostic_status",
    "score",
    "fail_count",
    "diagnostic_fail_count",
    "sample_count",
    "min_samples_to_scale",
    "execution_posture",
    "probe_lane_count",
    "scale_blocked_lane_count",
    "reduced_lane_count",
    "probe_lane_names",
    "scale_blocked_lanes",
    "failed_discipline_ids",
    "weak_lanes",
    "note",
}
_BOTTLENECK_KEYS = {
    "venue",
    "id",
    "label",
    "status",
    "evidence",
    "action",
}
_TRADING_VALIDATION_KEYS = {
    "status",
    "latest_run_id",
    "latest_at",
    "readiness",
    "diagnostic_status",
    "score",
    "discipline_count",
    "expected_discipline_count",
    "run_once_endpoint",
    "status_endpoint",
}
_VALIDATION_SUMMARY_KEYS = {
    "total_score",
    "readiness",
    "diagnostic_status",
    "pass_count",
    "warn_count",
    "fail_count",
    "missing_count",
    "hard_fail_count",
    "hard_missing_count",
    "core_fail_count",
    "core_missing_count",
}
_NEXT_ACTION_KEYS = {
    "venue",
    "action",
    "reason",
    "severity",
    "endpoint",
    "method",
}
_REMEDIATION_KEYS = {
    "id",
    "label",
    "detail",
    "severity",
    "endpoint",
    "method",
    "signals",
}
_SECTION_NAMES = {
    "memory",
    "market_judge",
    "market_pulse",
    "kis_block_trader",
    "binance_block_trader",
    "reports",
    "watchdog",
    "jue_wiki",
    "crypto_alpha",
    "crypto_market_research",
    "live_evaluator",
    "codex_native",
    "llm",
    "llm_usage",
    "semantic_checks",
}


@dataclass(frozen=True)
class OpsRouteDeps:
    require_admin_auth: Callable[..., Any]
    build_ops_readiness: Callable[[], dict[str, Any]]
    build_codex_native_status: Callable[[], dict[str, Any]]
    refresh_codex_native_checks: Callable[..., Any]
    system_metrics_snapshot: Callable[[], dict[str, Any]]
    watchdog_status: Callable[[], dict[str, Any]]
    restart_runner_processes: Callable[..., dict[str, Any]]
    build_settings_catalog: Callable[[], dict[str, Any]]
    update_settings_env: Callable[..., dict[str, Any]]
    build_ops_restart_readiness: Callable[[], dict[str, Any]] | None = None


def _restart_includes_kis_block_trader(keys: list[Any] | None) -> bool:
    if keys is None:
        return True
    return any(str(key) == "kis_block_trader" for key in keys)


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(int(float(value or 0)), 0)
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    return str(value or "")[: max(int(limit), 0)]


def _copy_present(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", {})
    }


def _compact_ops_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown", "compact": True}
    compact: dict[str, Any] = {"compact": True}
    for key, value in payload.items():
        if key in _TOP_LEVEL_KEYS and value not in (None, "", {}):
            compact[key] = value
        elif key in _SIGNAL_LIST_KEYS:
            compact[key] = list(value) if isinstance(value, list) else []
    processes = payload.get("processes")
    if isinstance(processes, dict):
        compact["processes"] = {
            str(key): _compact_process(row)
            for key, row in processes.items()
            if isinstance(row, dict)
        }
    disk_space = payload.get("disk_space")
    if isinstance(disk_space, dict):
        compact["disk_space"] = _copy_present(disk_space, _DISK_KEYS)
    for key in _SECTION_NAMES:
        section = payload.get(key)
        if isinstance(section, dict):
            compact_section = _compact_readiness_section(section)
            if compact_section:
                compact[key] = compact_section
    advisory_details = payload.get("advisory_details")
    if isinstance(advisory_details, list):
        compact["advisory_details"] = [
            _compact_advisory_detail(row)
            for row in advisory_details[:8]
            if isinstance(row, dict)
        ]
    trading_validation = payload.get("trading_validation")
    if isinstance(trading_validation, dict):
        compact["trading_validation"] = _compact_trading_validation(trading_validation)
    remediation_actions = payload.get("remediation_actions")
    if isinstance(remediation_actions, list):
        compact["remediation_actions"] = [
            _compact_remediation_action(row)
            for row in remediation_actions[:8]
            if isinstance(row, dict)
        ]
    return compact


def _compact_process(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _PROCESS_KEYS)
    if "error_message" in compact:
        compact["error_message"] = _clean_text(compact["error_message"])
    return compact


def _compact_readiness_section(section: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in section.items()
        if key in _SECTION_KEYS
        and isinstance(value, (str, int, float, bool))
        and value not in (None, "")
    }
    status = section.get("status")
    if isinstance(status, dict):
        compact_status = _copy_present(status, _STATUS_KEYS)
        config = status.get("config")
        if isinstance(config, dict):
            compact_config = _copy_present(config, _CONFIG_KEYS)
            if compact_config:
                compact_status["config"] = compact_config
        clock = status.get("clock")
        if isinstance(clock, dict):
            compact_status["clock"] = _copy_present(
                clock,
                {
                    "status",
                    "timezone",
                    "now",
                    "date",
                    "session",
                    "is_market_open",
                    "next_open_at",
                },
            )
        if compact_status:
            compact["status"] = compact_status
    schedule = section.get("schedule")
    if isinstance(schedule, dict):
        compact_schedule = _copy_present(schedule, _SCHEDULE_KEYS)
        if compact_schedule:
            compact["schedule"] = compact_schedule
    runner = section.get("runner")
    if isinstance(runner, dict):
        compact["runner"] = _compact_process(runner)
    config = section.get("config")
    if isinstance(config, dict):
        compact_config = _copy_present(config, _CONFIG_KEYS)
        if compact_config:
            compact["config"] = compact_config
    return compact


def _compact_advisory_detail(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _ADVISORY_KEYS)
    if isinstance(compact.get("failed_discipline_ids"), list):
        compact["failed_discipline_ids"] = compact["failed_discipline_ids"][:8]
    if "note" in compact:
        compact["note"] = _clean_text(compact["note"])
    top_bottlenecks = row.get("top_bottlenecks")
    if isinstance(top_bottlenecks, list):
        compact["top_bottlenecks"] = [
            _compact_bottleneck(item)
            for item in top_bottlenecks[:3]
            if isinstance(item, dict)
        ]
    return compact


def _compact_bottleneck(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _BOTTLENECK_KEYS)
    for key in ("evidence", "action"):
        if key in compact:
            compact[key] = _clean_text(compact[key])
    return compact


def _compact_trading_validation(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _TRADING_VALIDATION_KEYS)
    summary = row.get("summary")
    if isinstance(summary, dict):
        compact["summary"] = _copy_present(summary, _VALIDATION_SUMMARY_KEYS)
    bottlenecks = row.get("bottlenecks")
    if isinstance(bottlenecks, list):
        compact["bottlenecks"] = [
            _compact_bottleneck(item)
            for item in bottlenecks[:8]
            if isinstance(item, dict)
        ]
    next_actions = row.get("primary_next_actions")
    if isinstance(next_actions, list):
        compact["primary_next_actions"] = [
            _compact_next_action(item)
            for item in next_actions[:6]
            if isinstance(item, dict)
        ]
    return compact


def _compact_next_action(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _NEXT_ACTION_KEYS)
    for key in ("action", "reason"):
        if key in compact:
            compact[key] = _clean_text(compact[key])
    return compact


def _compact_remediation_action(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _REMEDIATION_KEYS)
    if isinstance(compact.get("signals"), list):
        compact["signals"] = compact["signals"][:8]
    if "detail" in compact:
        compact["detail"] = _clean_text(compact["detail"])
    return compact


def _restart_keys_from_payload(body: dict[str, Any]) -> list[Any] | None:
    key_field = "keys" if "keys" in body else "targets" if "targets" in body else ""
    if not key_field:
        return None
    value = body.get(key_field)
    if isinstance(value, list):
        return value
    raise HTTPException(
        status_code=400,
        detail=f"{key_field} must be a list of runner keys",
    )


def _kis_restart_needs_active_trading_confirmation(
    readiness: dict[str, Any],
    keys: list[Any] | None,
) -> bool:
    if not _restart_includes_kis_block_trader(keys):
        return False
    kis_payload = (
        readiness.get("kis_block_trader")
        if isinstance(readiness.get("kis_block_trader"), dict)
        else {}
    )
    kis_status = (
        kis_payload.get("status")
        if isinstance(kis_payload.get("status"), dict)
        else {}
    )
    market_clock = (
        readiness.get("market_clock")
        if isinstance(readiness.get("market_clock"), dict)
        else kis_status.get("clock")
        if isinstance(kis_status.get("clock"), dict)
        else {}
    )
    if not bool(market_clock.get("is_market_open")):
        return False
    kis_blocks = (
        readiness.get("kis_blocks")
        if isinstance(readiness.get("kis_blocks"), dict)
        else kis_status
    )
    summary = (
        kis_blocks.get("summary")
        if isinstance(kis_blocks.get("summary"), dict)
        else kis_blocks
    )
    active_count = sum(
        _safe_non_negative_int(summary.get(key))
        for key in (
            "open_block_count",
            "waiting_entry_block_count",
            "pending_order_count",
        )
    )
    return active_count > 0


def build_ops_router(deps: OpsRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "tradecraft-control",
            "ops_endpoint": "/api/ops/readiness",
            "ops_auth_required": True,
        }

    @router.get("/api/ops/readiness")
    async def ops_readiness(
        compact: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = deps.build_ops_readiness()
        if compact:
            return _compact_ops_readiness(payload)
        return payload

    @router.get("/api/ops/processes")
    @router.get("/api/runtime/processes")
    async def ops_processes(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        readiness = deps.build_ops_readiness()
        processes = readiness.get("processes")
        return {
            "status": "ok",
            "checked_at": readiness.get("checked_at", ""),
            "processes": processes if isinstance(processes, dict) else {},
            "stale_processes": list(readiness.get("stale_processes") or []),
            "missing_processes": list(readiness.get("missing_processes") or []),
            "duplicate_processes": list(readiness.get("duplicate_processes") or []),
        }

    @router.get("/api/codex/native/status")
    @router.get("/api/ops/codex-native/status")
    async def codex_native_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.build_codex_native_status()

    @router.post("/api/codex/native/check")
    async def codex_native_check(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.refresh_codex_native_checks(force=True)
        if inspect.isawaitable(result):
            await result
        return deps.build_codex_native_status()

    @router.get("/api/ops/system-metrics")
    async def ops_system_metrics(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.system_metrics_snapshot()

    @router.get("/api/ops/watchdog/status")
    async def ops_watchdog_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.watchdog_status()

    @router.post("/api/ops/restart")
    async def ops_restart(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        keys = _restart_keys_from_payload(body)
        readiness_builder = deps.build_ops_restart_readiness or deps.build_ops_readiness
        readiness = readiness_builder()
        if (
            not bool(body.get("confirm_active_trading_restart"))
            and _kis_restart_needs_active_trading_confirmation(readiness, keys)
        ):
            raise HTTPException(
                status_code=409,
                detail="kis restart requires confirmation during active market blocks",
            )
        try:
            result = deps.restart_runner_processes(keys, delay_sec=0.5)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["message"] = "control/runner restart scheduled"
        return result

    @router.get("/api/settings/catalog")
    async def settings_catalog(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.build_settings_catalog()

    @router.patch("/api/settings/values")
    async def settings_update(
        payload: dict[str, Any],
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        updates = payload.get("updates")
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="updates required")
        return deps.update_settings_env(
            updates,
            confirm_high_risk=bool(payload.get("confirm_high_risk")),
        )

    return router
