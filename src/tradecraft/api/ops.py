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
    "active_read_mode",
    "active_read_mode_status",
    "publication_age_sec",
    "publication_status",
    "configured_read_mode",
    "stored_read_mode",
    "read_mode_mismatch",
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
    "active_read_mode",
    "active_read_mode_status",
    "publication_age_sec",
    "publication_status",
    "configured_read_mode",
    "stored_read_mode",
    "read_mode_mismatch",
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
    "requires_confirmation",
}
_FOLLOW_UP_ACTION_KEYS = {
    "id",
    "label",
    "detail",
    "severity",
    "endpoint",
    "method",
    "requires_confirmation",
}
_ACTIVITY_PRESSURE_KEYS = {
    "status",
    "level",
    "source",
    "zero_action_streak",
    "binance_zero_action_streak",
    "activity_gap_status",
    "entry_stale_hours",
    "candidate_symbols",
}
_ENTRY_ACTIVITY_KEYS = {
    "version",
    "status",
    "latest_binance_entry_at",
    "latest_binance_entry_market",
    "latest_upbit_entry_at",
    "binance_entry_stale_hours",
    "binance_entry_count",
    "upbit_entry_count",
}
_CONTRACT_REPLAY_KEYS = {
    "contract_replay_status",
    "stored_error_message",
    "current_contract_error",
    "action_count",
    "current_replay_action_count",
    "current_replay_auto_action_count",
    "current_replay_action_sections",
    "current_replay_hold_summary",
    "current_replay_watch_symbols",
    "current_replay_next_triggers",
    "current_replay_data_gaps",
    "current_replay_auto_create_preview",
}
_CONTRACT_REPLAY_AUTO_CREATE_PREVIEW_TEXT_KEYS = {
    "symbol",
    "market",
    "side",
    "entry_style",
    "entry_trigger_operator",
    "auto_materialized_reason",
}
_CONTRACT_REPLAY_AUTO_CREATE_PREVIEW_NUMERIC_KEYS = {
    "entry_trigger_price",
    "entry_price",
    "target_price",
    "stop_price",
    "qty",
    "quote_budget_usdt",
    "quote_budget_krw",
    "min_executable_notional_usdt",
    "min_executable_notional_krw",
    "min_executable_qty",
    "notional_estimate_usdt",
    "notional_estimate_krw",
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
    build_compact_ops_readiness: Callable[[], dict[str, Any]] | None = None


def _restart_includes_kis_block_trader(keys: list[Any] | None) -> bool:
    if keys is None:
        return True
    return any(str(key) == "kis_block_trader" for key in keys)


def _restart_includes_binance_block_trader(keys: list[Any] | None) -> bool:
    if keys is None:
        return True
    return any(str(key) == "binance_block_trader" for key in keys)


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(int(float(value or 0)), 0)
    except (TypeError, ValueError):
        return 0


def _safe_positive_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


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
    for action_key in (
        "remediation_actions",
        "operational_remediation_actions",
        "advisory_actions",
    ):
        actions = payload.get(action_key)
        if isinstance(actions, list):
            compact[action_key] = [
                _compact_remediation_action(row)
                for row in actions[:8]
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
        v3 = status.get("v3")
        if isinstance(v3, dict):
            compact_status["v3"] = _compact_wiki_v3(v3)
        for key in ("comparison_count_by_venue", "eligibility_by_venue"):
            value = status.get(key)
            if isinstance(value, dict):
                compact_status[key] = _compact_wiki_venue_map(value)
        scope_health = status.get("scope_health_by_venue")
        if isinstance(scope_health, dict):
            compact_status["scope_health_by_venue"] = _compact_wiki_v3(
                {"by_scope": scope_health}
            ).get("by_scope", {})
        if compact_status:
            compact["status"] = compact_status
    section_v3 = section.get("v3")
    if isinstance(section_v3, dict):
        compact["v3"] = _compact_wiki_v3(section_v3)
    for key in ("comparison_count_by_venue", "eligibility_by_venue"):
        value = section.get(key)
        if isinstance(value, dict):
            compact[key] = _compact_wiki_venue_map(value)
    scope_health = section.get("scope_health_by_venue")
    if isinstance(scope_health, dict):
        compact["scope_health_by_venue"] = _compact_wiki_v3(
            {"by_scope": scope_health}
        ).get("by_scope", {})
    schedule = section.get("schedule")
    if isinstance(schedule, dict):
        compact_schedule = _copy_present(schedule, _SCHEDULE_KEYS)
        if compact_schedule:
            compact["schedule"] = compact_schedule
    runner = section.get("runner")
    if isinstance(runner, dict):
        compact["runner"] = _compact_process(runner)
    warnings = section.get("warnings")
    if isinstance(warnings, list):
        compact["warnings"] = [str(item) for item in warnings[:8]]
    activity_pressure = section.get("activity_pressure")
    if isinstance(activity_pressure, dict):
        compact_pressure = _copy_present(activity_pressure, _ACTIVITY_PRESSURE_KEYS)
        if isinstance(compact_pressure.get("candidate_symbols"), list):
            compact_pressure["candidate_symbols"] = [
                str(symbol)
                for symbol in compact_pressure["candidate_symbols"][:8]
                if symbol not in (None, "")
            ]
        if compact_pressure:
            compact["activity_pressure"] = compact_pressure
    activity_repair_actions = section.get("activity_repair_actions")
    if isinstance(activity_repair_actions, list):
        compact_activity_repairs = [
            _compact_remediation_action(action)
            for action in activity_repair_actions[:6]
            if isinstance(action, dict)
        ]
        if compact_activity_repairs:
            compact["activity_repair_actions"] = compact_activity_repairs
    entry_activity = section.get("entry_activity")
    if isinstance(entry_activity, dict):
        compact_entry_activity = _copy_present(entry_activity, _ENTRY_ACTIVITY_KEYS)
        for key in (
            "version",
            "status",
            "latest_binance_entry_at",
            "latest_binance_entry_market",
            "latest_upbit_entry_at",
        ):
            if key in compact_entry_activity:
                compact_entry_activity[key] = _clean_text(
                    compact_entry_activity[key],
                    limit=120,
                )
        if compact_entry_activity:
            compact["entry_activity"] = compact_entry_activity
    contract_replay = section.get("manager_contract_replay")
    if isinstance(contract_replay, dict):
        compact_replay = _compact_contract_replay(contract_replay)
        if compact_replay:
            compact["manager_contract_replay"] = compact_replay
    config = section.get("config")
    if isinstance(config, dict):
        compact_config = _copy_present(config, _CONFIG_KEYS)
        if compact_config:
            compact["config"] = compact_config
    return compact


def _compact_wiki_venue_map(value: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for venue in ("kis", "binance"):
        row = value.get(venue)
        if isinstance(row, dict):
            compact[venue] = {
                key: child
                for key, child in row.items()
                if key
                in {
                    "venue",
                    "version",
                    "required_eligible",
                    "complete_sample_count",
                    "reason",
                    "evaluated_at",
                    "evaluated_through",
                }
                and isinstance(child, (str, int, float, bool))
            }
            blockers = row.get("blockers")
            if isinstance(blockers, list):
                compact[venue]["blockers"] = [
                    _clean_text(blocker, limit=120) for blocker in blockers[:8]
                ]
            for key in (
                "venue",
                "version",
                "reason",
                "evaluated_at",
                "evaluated_through",
            ):
                if key in compact[venue]:
                    compact[venue][key] = _clean_text(
                        compact[venue][key],
                        limit=120,
                    )
        elif isinstance(row, (int, float)) and not isinstance(row, bool):
            compact[venue] = row
    return compact


def _compact_wiki_v3(v3: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in v3.items()
        if key
        in {
            "active_read_mode",
            "stale_count",
            "conflicted_count",
            "orphan_page_count",
            "repair_backlog_count",
            "last_ingest_status",
            "last_compile_status",
            "last_lint_status",
            "last_publish_status",
            "last_projection_status",
        }
        and isinstance(value, (str, int, float, bool))
    }
    by_scope = v3.get("by_scope")
    if isinstance(by_scope, dict):
        compact["by_scope"] = {}
        for venue in ("kis", "binance"):
            row = by_scope.get(venue)
            if not isinstance(row, dict):
                continue
            compact_row = {
                key: child
                for key, child in row.items()
                if key
                in {
                    "snapshot_id",
                    "snapshot_created_at",
                    "snapshot_age_sec",
                    "stale_count",
                    "conflicted_count",
                    "orphan_page_count",
                    "repair_backlog_count",
                    "last_ingest_status",
                    "last_compile_status",
                    "last_lint_status",
                    "last_publish_status",
                    "last_projection_status",
                    "projection_warning_reason",
                }
                and isinstance(child, (str, int, float, bool))
            }
            index_rebuild = row.get("index_rebuild")
            if isinstance(index_rebuild, dict):
                compact_row["index_rebuild"] = _copy_present(
                    index_rebuild,
                    {"status", "scope", "updated_at"},
                )
            compact["by_scope"][venue] = compact_row
    mode_eligibility = v3.get("mode_eligibility")
    if isinstance(mode_eligibility, dict):
        compact["mode_eligibility"] = _compact_wiki_venue_map(
            mode_eligibility
        )
    return compact


def _compact_contract_replay(replay: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(replay, _CONTRACT_REPLAY_KEYS)
    for key in (
        "stored_error_message",
        "current_contract_error",
        "current_replay_hold_summary",
    ):
        if key in compact:
            compact[key] = _clean_text(compact[key])
    sections = compact.get("current_replay_action_sections")
    if isinstance(sections, dict):
        compact_sections: dict[str, Any] = {}
        for key, value in list(sections.items())[:8]:
            if isinstance(value, (int, float, bool)) and value not in (None, ""):
                compact_sections[str(key)] = value
        if compact_sections:
            compact["current_replay_action_sections"] = compact_sections
        else:
            compact.pop("current_replay_action_sections", None)
    symbols = compact.get("current_replay_watch_symbols")
    if isinstance(symbols, list):
        compact["current_replay_watch_symbols"] = [
            _clean_text(symbol, limit=120)
            for symbol in symbols[:8]
            if symbol not in (None, "")
        ]
    triggers = compact.get("current_replay_next_triggers")
    if isinstance(triggers, list):
        compact_triggers: list[dict[str, Any]] = []
        for row in triggers[:8]:
            if not isinstance(row, dict):
                continue
            trigger: dict[str, Any] = {}
            for key in ("symbol", "market", "condition", "reason"):
                value = row.get(key)
                if value not in (None, "", [], {}):
                    trigger[key] = _clean_text(value, limit=220 if key == "reason" else 120)
            price = row.get("price")
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                trigger["price"] = price
            elif price not in (None, "", [], {}):
                trigger["price"] = _clean_text(price, limit=120)
            if trigger:
                compact_triggers.append(trigger)
        compact["current_replay_next_triggers"] = compact_triggers
    gaps = compact.get("current_replay_data_gaps")
    if isinstance(gaps, list):
        compact["current_replay_data_gaps"] = [
            _clean_text(gap, limit=120)
            for gap in gaps[:8]
            if gap not in (None, "")
        ]
    auto_create_preview = compact.get("current_replay_auto_create_preview")
    if isinstance(auto_create_preview, list):
        compact["current_replay_auto_create_preview"] = (
            _compact_contract_replay_auto_create_preview(auto_create_preview)
        )
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _compact_contract_replay_auto_create_preview(
    value: list[Any],
) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for row in value[:8]:
        if not isinstance(row, dict):
            continue
        preview: dict[str, Any] = {}
        for key in _CONTRACT_REPLAY_AUTO_CREATE_PREVIEW_TEXT_KEYS:
            item = row.get(key)
            if item not in (None, "", [], {}):
                preview[key] = _clean_text(item, limit=160)
        for key in _CONTRACT_REPLAY_AUTO_CREATE_PREVIEW_NUMERIC_KEYS:
            item = row.get(key)
            parsed = _safe_positive_float(item)
            if parsed > 0:
                preview[key] = parsed
        if preview:
            previews.append(preview)
    return previews


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
    request_payload = row.get("request_payload")
    if isinstance(request_payload, dict):
        compact_request = _compact_request_payload(request_payload)
        if compact_request:
            compact["request_payload"] = compact_request
    follow_up_actions = row.get("follow_up_actions")
    if isinstance(follow_up_actions, list):
        compact_follow_ups = [
            _compact_follow_up_action(action)
            for action in follow_up_actions[:6]
            if isinstance(action, dict)
        ]
        if compact_follow_ups:
            compact["follow_up_actions"] = compact_follow_ups
    return compact


def _compact_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact_request = _copy_present(
        payload,
        {
            "keys",
            "symbols",
            "targets",
            "confirm_active_trading_restart",
            "confirm_live_manager_run",
            "confirm_live_executor_tick",
        },
    )
    for key in ("keys", "symbols", "targets"):
        if isinstance(compact_request.get(key), list):
            compact_request[key] = [
                str(item)
                for item in compact_request[key][:8]
                if item not in (None, "")
            ]
    return compact_request


def _compact_follow_up_action(row: dict[str, Any]) -> dict[str, Any]:
    compact = _copy_present(row, _FOLLOW_UP_ACTION_KEYS)
    if "detail" in compact:
        compact["detail"] = _clean_text(compact["detail"])
    request_payload = row.get("request_payload")
    if isinstance(request_payload, dict):
        compact_request = _compact_request_payload(request_payload)
        if compact_request:
            compact["request_payload"] = compact_request
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


def _binance_restart_needs_active_trading_confirmation(
    readiness: dict[str, Any],
    keys: list[Any] | None,
) -> bool:
    if not _restart_includes_binance_block_trader(keys):
        return False
    binance_payload = (
        readiness.get("binance_block_trader")
        if isinstance(readiness.get("binance_block_trader"), dict)
        else {}
    )
    execution = (
        binance_payload.get("execution")
        if isinstance(binance_payload.get("execution"), dict)
        else {}
    )
    return any(
        str(execution.get(key) or "").strip().lower() == "live"
        for key in ("spot_mode", "futures_mode", "upbit_spot_mode")
    )


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
        if compact and deps.build_compact_ops_readiness is not None:
            payload = deps.build_compact_ops_readiness()
            if bool(payload.get("compact")):
                return payload
            return _compact_ops_readiness(payload)
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
        if (
            not bool(body.get("confirm_active_trading_restart"))
            and _binance_restart_needs_active_trading_confirmation(readiness, keys)
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "binance restart requires confirmation while live crypto "
                    "execution is enabled"
                ),
            )
        try:
            result = deps.restart_runner_processes(keys, delay_sec=0.5)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["message"] = "verified rolling runner recovery scheduled"
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
