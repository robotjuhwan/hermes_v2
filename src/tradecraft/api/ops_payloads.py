from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from tradecraft.services.live_authority import (
    EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
    compact_live_authority_for_status,
)
from tradecraft.services.jue_wiki_context import wiki_eligibility_freshness_reason

LLM_FAILURE_STATUSES = {"error", "llm_unavailable", "llm_error", "llm_empty"}
TRADING_VALIDATION_ADVISORY_PREFIXES = (
    "trading_validation_strategy_blocked",
    "trading_validation_diagnostic_failures",
    "trading_validation_probe",
    "trading_validation_lane_authority_reduced",
)
_TEXT_LIMIT = 220
_TRADER_STATUS_KEYS = {
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
    "latest_manager_error",
    "latest_manager_error_recovered",
    "latest_recovered_manager_error",
    "latest_unresolved_manager_error",
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
    "execution",
    "risk",
    "risk_guard",
    "performance",
    "performance_today",
    "growth_unlock",
    "growth_governor",
    "growth_target",
}
_RUNNER_KEYS = {
    "key",
    "label",
    "status",
    "alive",
    "pid",
    "started_at",
    "started_at_epoch",
    "stale_process",
    "direct_alive",
    "effective_alive",
    "pid_file_status",
}
_TRADING_VALIDATION_VENUE_KEYS = {
    "status",
    "readiness",
    "diagnostic_status",
    "score",
    "discipline_count",
    "expected_discipline_count",
    "computed_at",
    "age_sec",
    "max_age_sec",
    "stale",
    "stale_reason",
    "summary",
    "lane_authority_summary",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _csv_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _short_text(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    return str(value or "")[: max(int(limit), 0)]


def _compact_json_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _short_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        if depth >= 2:
            return {
                key: _compact_json_value(child, depth=depth + 1)
                for key, child in list(value.items())[:12]
                if child is None or isinstance(child, (str, bool, int, float))
            }
        return {
            str(key): _compact_json_value(child, depth=depth + 1)
            for key, child in list(value.items())[:16]
            if child not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            _compact_json_value(child, depth=depth + 1)
            for child in value[:8]
            if child not in (None, "", [], {})
        ]
    return _short_text(value)


def _compact_runner_payload(runner: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(runner, dict):
        return {}
    return {
        key: _compact_json_value(value)
        for key, value in runner.items()
        if key in _RUNNER_KEYS and value not in (None, "", [], {})
    }


def _compact_trader_status(status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {"status": "unknown"}
    compact = {
        key: _compact_json_value(value)
        for key, value in status.items()
        if key in _TRADER_STATUS_KEYS and value not in (None, "", [], {})
    }
    if bool(status.get("latest_manager_error_recovered")):
        recovered_error = status.get("latest_recovered_manager_error")
        if not isinstance(recovered_error, dict) or not recovered_error:
            recovered_error = status.get("latest_manager_error")
        if isinstance(recovered_error, dict) and recovered_error:
            compact["latest_recovered_manager_error"] = _compact_json_value(
                recovered_error
            )
        compact.pop("latest_manager_error", None)
        compact.pop("latest_unresolved_manager_error", None)
    live_authority = status.get("live_authority")
    if isinstance(live_authority, dict):
        compact["live_authority"] = compact_live_authority_for_status(live_authority)
    return compact


def _has_unresolved_manager_error(
    status: dict[str, Any],
    process_status: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(status, dict):
        return False
    if bool(status.get("latest_manager_error_recovered")):
        return False
    if _manager_contract_replay_resolved_stored_error(status):
        return False
    unresolved = status.get("latest_unresolved_manager_error")
    if isinstance(unresolved, dict) and unresolved:
        attempt = {
            "run_at": str(unresolved.get("run_at") or ""),
            "status": str(unresolved.get("status") or ""),
            "mode": str(unresolved.get("mode") or ""),
            "error_message": str(unresolved.get("error_message") or ""),
        }
    else:
        attempt = {
            "run_at": str(status.get("latest_manager_run_at") or ""),
            "status": str(status.get("latest_manager_status") or ""),
            "mode": str(status.get("latest_manager_mode") or ""),
        }
    if _llm_attempt_stale_after_process_restart(attempt, process_status):
        return False
    latest_status = str(status.get("latest_manager_status") or "").strip().lower()
    if latest_status in LLM_FAILURE_STATUSES:
        return True
    if not isinstance(unresolved, dict) or not unresolved:
        return False
    unresolved_status = str(unresolved.get("status") or "").strip().lower()
    return unresolved_status in LLM_FAILURE_STATUSES or bool(
        str(unresolved.get("error_message") or "").strip()
    )


def _manager_contract_replay_resolved_stored_error(status: dict[str, Any]) -> bool:
    latest = status.get("latest_decision_input")
    if not isinstance(latest, dict):
        return False
    return (
        latest.get("contract_replay_status")
        == "stored_error_resolved_by_current_contract"
        and not str(latest.get("current_contract_error") or "").strip()
    )


def _manager_error_stale_after_restart(
    status: dict[str, Any],
    process_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(status, dict) or bool(status.get("latest_manager_error_recovered")):
        return {}
    unresolved = (
        status.get("latest_unresolved_manager_error")
        if isinstance(status.get("latest_unresolved_manager_error"), dict)
        else {}
    )
    if isinstance(unresolved, dict) and unresolved:
        attempt = {
            "run_at": str(unresolved.get("run_at") or ""),
            "status": str(unresolved.get("status") or ""),
            "mode": str(unresolved.get("mode") or ""),
            "error_message": str(unresolved.get("error_message") or ""),
        }
    else:
        latest_error = (
            status.get("latest_manager_error")
            if isinstance(status.get("latest_manager_error"), dict)
            else {}
        )
        attempt = {
            "run_at": str(
                latest_error.get("run_at")
                or status.get("latest_manager_run_at")
                or ""
            ),
            "status": str(
                latest_error.get("status")
                or status.get("latest_manager_status")
                or ""
            ),
            "mode": str(
                latest_error.get("mode")
                or status.get("latest_manager_mode")
                or ""
            ),
            "error_message": str(latest_error.get("error_message") or ""),
        }
    if not _llm_attempt_stale_after_process_restart(attempt, process_status):
        return {}
    return {
        key: value
        for key, value in attempt.items()
        if value not in (None, "", [], {})
    }


def _latest_manager_run_diagnostics(status: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    if not isinstance(status, dict):
        return "", {}
    runs = status.get("manager_runs")
    if not isinstance(runs, list):
        return "", {}
    for row in runs:
        if not isinstance(row, dict):
            continue
        diagnostics = row.get("diagnostics")
        if isinstance(diagnostics, dict) and diagnostics:
            return row.get("run_id") or row.get("id") or row.get("run_at") or "", diagnostics
    return "", {}


def _wiki_action_reference_gap(status: dict[str, Any]) -> dict[str, Any]:
    run_id, diagnostics = _latest_manager_run_diagnostics(status)
    if not diagnostics:
        return {}
    action_reference_status = str(
        diagnostics.get("jue_wiki_action_reference_status") or ""
    ).strip()
    resolution_status = str(
        diagnostics.get("jue_wiki_action_reference_memory_resolution_status") or ""
    ).strip()
    memory_status = str(
        diagnostics.get("jue_wiki_action_reference_memory_status") or ""
    ).strip()
    blockers = (
        diagnostics.get("blocker_tags")
        if isinstance(diagnostics.get("blocker_tags"), dict)
        else {}
    )
    is_unresolved = (
        action_reference_status == "missing"
        or resolution_status == "unresolved"
        or "unresolved_jue_wiki_action_reference_memory" in blockers
    )
    if not is_unresolved:
        return {}
    payload = {
        "status": action_reference_status or "unknown",
        "resolution_status": resolution_status or "unknown",
        "memory_status": memory_status or "unknown",
        "run_id": run_id,
        "blocker_tags": _compact_json_value(blockers),
    }
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }


def _compact_rows(rows: Any, *, limit: int = 8) -> list[Any]:
    if not isinstance(rows, list):
        return []
    return [
        _compact_json_value(row)
        for row in rows[: max(int(limit), 0)]
        if row not in (None, "", [], {})
    ]


def _compact_trading_validation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "discipline_count",
        "expected_discipline_count",
        "readiness",
        "diagnostic_status",
        "score",
        "run_id",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _compact_json_value(value)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        compact["summary"] = _compact_json_value(summary)
    return compact


def _compact_trading_validation_venues(venues: Any) -> dict[str, Any]:
    if not isinstance(venues, dict):
        return {}
    compact: dict[str, Any] = {}
    for venue, payload in venues.items():
        if not isinstance(payload, dict):
            continue
        venue_payload = {
            key: _compact_json_value(value)
            for key, value in payload.items()
            if key in _TRADING_VALIDATION_VENUE_KEYS
            and value not in (None, "", [], {})
        }
        if (
            isinstance(venue_payload.get("summary"), dict)
            and not venue_payload["summary"].get("diagnostic_status")
            and venue_payload.get("diagnostic_status")
        ):
            venue_payload["summary"]["diagnostic_status"] = venue_payload[
                "diagnostic_status"
            ]
        if isinstance(payload.get("lane_authority_summary"), dict):
            venue_payload["lane_authority_summary"] = _compact_lane_authority_summary(
                payload["lane_authority_summary"]
            )
        compact_payload = _compact_trading_validation_payload(payload.get("payload"))
        if compact_payload:
            venue_payload["payload"] = compact_payload
        compact[str(venue)] = venue_payload
    return compact


def _compact_lane_authority_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    compact = {
        key: value
        for key, value in payload.items()
        if key
        in {
            "version",
            "status",
            "venue",
            "execution_posture",
            "probe_policy",
            "probe_lane_count",
            "scale_blocked_lane_count",
            "reduced_lane_count",
            "insufficient_lane_count",
            "blocked_lane_count",
        }
        and value not in (None, "", [], {})
    }
    for key in ("probe_lane_names", "scale_blocked_lanes"):
        values = payload.get(key)
        if isinstance(values, list):
            compact[key] = [
                str(value)
                for value in values[:8]
                if value not in (None, "", [], {})
            ]
    insufficient_lanes = payload.get("insufficient_lanes")
    if isinstance(insufficient_lanes, list):
        compact["insufficient_lanes"] = [
            str(value)
            for value in insufficient_lanes[:8]
            if value not in (None, "", [], {})
        ]
    reduced_lanes = payload.get("reduced_lanes")
    if isinstance(reduced_lanes, list):
        compact_lanes = [
            _compact_lane_authority_row(row)
            for row in reduced_lanes[:4]
            if isinstance(row, dict)
        ]
        if compact_lanes:
            compact["reduced_lanes"] = compact_lanes
    return compact


def _compact_lane_authority_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        in {
            "venue",
            "lane",
            "grade",
            "action",
            "authority_multiplier",
            "requires_waiting_entry",
            "reasons",
            "cost_verified_alpha_count",
            "cost_unverified_alpha_count",
            "validation_repair_enforced_count",
            "validation_repair_scale_up_blocked_count",
        }
        and value not in (None, "", [], {})
    }


def build_disk_space_status(
    *,
    runtime_state_path: str,
    disk_usage: Callable[[Path], Any] | None = None,
    warn_bytes: int = 2 * 1024 * 1024 * 1024,
    critical_bytes: int = 1 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    runtime_dir = Path(runtime_state_path).parent or Path(".runtime")
    disk_usage_fn = disk_usage or shutil.disk_usage
    try:
        usage = disk_usage_fn(runtime_dir)
    except OSError as exc:
        return {
            "status": "error",
            "path": str(runtime_dir),
            "error_message": str(exc),
            "warn_free_bytes": int(warn_bytes),
            "critical_free_bytes": int(critical_bytes),
        }
    total_bytes = int(usage.total)
    free_bytes = int(usage.free)
    status = "ok"
    if free_bytes <= critical_bytes:
        status = "critical"
    elif free_bytes <= warn_bytes:
        status = "low"
    return {
        "status": status,
        "path": str(runtime_dir),
        "total_bytes": total_bytes,
        "used_bytes": int(usage.used),
        "free_bytes": free_bytes,
        "free_pct": round(free_bytes / total_bytes * 100.0, 4)
        if total_bytes > 0
        else 0.0,
        "warn_free_bytes": int(warn_bytes),
        "critical_free_bytes": int(critical_bytes),
    }


def _runtime_storage_size_bytes(runtime_dir: Path) -> int:
    if not runtime_dir.exists():
        return 0
    if runtime_dir.is_file():
        return int(runtime_dir.stat().st_size)
    total = 0
    for item in runtime_dir.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += int(item.stat().st_size)
        except OSError:
            continue
    return total


def build_runtime_storage_size_status(
    *,
    runtime_state_path: str,
    size_reader: Callable[[Path], int] | None = None,
    warn_bytes: int = 4 * 1024 * 1024 * 1024,
    risk_bytes: int = 6 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    runtime_dir = Path(runtime_state_path).parent or Path(".runtime")
    try:
        total_bytes = int((size_reader or _runtime_storage_size_bytes)(runtime_dir))
    except OSError as exc:
        return {
            "status": "error",
            "path": str(runtime_dir),
            "error_message": str(exc),
            "warn_bytes": int(warn_bytes),
            "risk_bytes": int(risk_bytes),
        }
    status = "ok"
    if total_bytes >= int(risk_bytes):
        status = "risk"
    elif total_bytes >= int(warn_bytes):
        status = "warning"
    return {
        "status": status,
        "path": str(runtime_dir),
        "total_bytes": total_bytes,
        "total_size_gb": round(total_bytes / 1024**3, 3),
        "warn_bytes": int(warn_bytes),
        "risk_bytes": int(risk_bytes),
    }


def _iso_to_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_llm_attempt_from_runs(runs: Any) -> dict[str, Any]:
    if not isinstance(runs, list):
        return {"status": "missing"}
    for row in runs:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip()
        if not status or status == "quotes_only":
            continue
        return {
            "run_at": str(row.get("run_at") or ""),
            "status": status,
            "mode": str(row.get("mode") or ""),
            "error_message": str(row.get("error_message") or "")[:400],
        }
    return {"status": "missing"}


def _llm_attempt_stale_after_process_restart(
    attempt: dict[str, Any],
    process_status: dict[str, Any] | None,
) -> bool:
    if not isinstance(attempt, dict) or not isinstance(process_status, dict):
        return False
    run_at = _iso_to_utc(attempt.get("run_at"))
    started_epoch = process_status.get("started_at_epoch")
    if run_at is None or started_epoch is None:
        return False
    try:
        started_at = datetime.fromtimestamp(float(started_epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    return bool(started_at > run_at + timedelta(seconds=1))


def build_llm_operational_status(
    *,
    block_status: dict[str, Any],
    binance_block_status: dict[str, Any] | None = None,
    market_schedule: dict[str, Any],
    processes: dict[str, dict[str, Any]] | None = None,
    configured: bool = False,
    model: str = "",
    reasoning_effort: str = "",
    native_mode: str = "",
) -> dict[str, Any]:
    process_payload = processes or {}
    kis_payload = _block_manager_llm_payload(
        block_status,
        process_payload.get("kis_block_trader"),
    )
    binance_payload = _block_manager_llm_payload(
        binance_block_status or {},
        process_payload.get("binance_block_trader"),
    )
    market_payload = _latest_llm_attempt_from_runs(
        market_schedule.get("recent_runs") if isinstance(market_schedule, dict) else []
    )
    market_payload["stale_after_restart"] = _llm_attempt_stale_after_process_restart(
        market_payload,
        process_payload.get("market_judge"),
    )
    return {
        "configured": bool(configured),
        "model": str(model or ""),
        "reasoning_effort": str(reasoning_effort or ""),
        "native_mode": str(native_mode or ""),
        "critical": {
            "kis_block_manager": kis_payload,
            "binance_block_manager": binance_payload,
            "market_judge": market_payload,
        },
    }


def _block_manager_llm_payload(
    status: dict[str, Any],
    process_status: dict[str, Any] | None,
) -> dict[str, Any]:
    latest_status = str(status.get("latest_manager_status") or "missing")
    if bool(status.get("latest_manager_error_recovered")):
        payload = {
            "run_at": str(status.get("latest_manager_run_at") or ""),
            "status": "recovered",
            "mode": str(status.get("latest_manager_mode") or ""),
            "latest_manager_status": latest_status,
            "latest_manager_error_recovered": True,
        }
        payload["stale_after_restart"] = _llm_attempt_stale_after_process_restart(
            payload,
            process_status,
        )
        return payload
    unresolved = (
        status.get("latest_unresolved_manager_error")
        if isinstance(status.get("latest_unresolved_manager_error"), dict)
        else {}
    )
    if (
        unresolved
        and not bool(status.get("latest_manager_error_recovered"))
        and (
            str(unresolved.get("status") or "").strip().lower()
            in LLM_FAILURE_STATUSES
            or bool(str(unresolved.get("error_message") or "").strip())
        )
    ):
        payload = {
            "run_at": str(unresolved.get("run_at") or ""),
            "status": str(unresolved.get("status") or "error"),
            "mode": str(unresolved.get("mode") or ""),
            "error_message": str(unresolved.get("error_message") or "")[:400],
            "latest_manager_status": latest_status,
        }
    else:
        payload = {
            "run_at": str(status.get("latest_manager_run_at") or ""),
            "status": latest_status,
            "mode": str(status.get("latest_manager_mode") or ""),
        }
    payload["stale_after_restart"] = _llm_attempt_stale_after_process_restart(
        payload,
        process_status,
    )
    return payload


def append_trading_validation_ops_signals(
    trading_validation_status: dict[str, Any],
    *,
    blockers: list[str],
    warnings: list[str],
    expected_discipline_count: int = EXPECTED_TRADING_VALIDATION_DISCIPLINE_COUNT,
) -> None:
    def add_unique(target: list[str], value: str) -> None:
        if value not in target:
            target.append(value)

    def append_one(
        row: dict[str, Any],
        suffix: str = "",
        *,
        include_non_blocking_advisories: bool = True,
    ) -> None:
        status = str(row.get("status") or "").strip().lower()
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        disciplines = (
            payload.get("disciplines")
            if isinstance(payload.get("disciplines"), list)
            else row.get("disciplines")
            if isinstance(row.get("disciplines"), list)
            else []
        )
        discipline_count = _safe_int(
            payload.get("discipline_count")
            or row.get("discipline_count")
            or len(disciplines)
            or 0
        )
        readiness = str(summary.get("readiness") or "").strip().lower()
        fail_count = _safe_int(summary.get("fail_count"))
        if (
            "hard_fail_count" in summary
            or "core_fail_count" in summary
            or "core_missing_count" in summary
        ):
            hard_fail_count = _safe_int(
                summary.get("hard_fail_count")
                if summary.get("hard_fail_count") is not None
                else _safe_int(summary.get("core_fail_count"))
                + _safe_int(summary.get("core_missing_count"))
            )
        else:
            hard_fail_count = fail_count
        suffix_text = f"_{suffix}" if suffix else ""

        if bool(row.get("stale")):
            add_unique(warnings, f"trading_validation_stale{suffix_text}")
        if bool(row.get("revision_mismatch")):
            add_unique(warnings, f"trading_validation_revision_mismatch{suffix_text}")
        if status == "error":
            add_unique(warnings, f"trading_validation_error{suffix_text}")
            return
        if not readiness:
            add_unique(warnings, f"trading_validation_missing{suffix_text}")
            return
        if hard_fail_count > 0:
            add_unique(blockers, f"trading_validation_blocked{suffix_text}")
            return
        if readiness == "blocked_by_validation" and include_non_blocking_advisories:
            add_unique(warnings, f"trading_validation_strategy_blocked{suffix_text}")
        diagnostic_fail_count = _safe_int(
            summary.get("diagnostic_fail_count")
            if summary.get("diagnostic_fail_count") is not None
            else max(fail_count - hard_fail_count, 0)
        )
        if diagnostic_fail_count > 0 and readiness in {"normal", "scale_ready"}:
            readiness = "probe"
        if include_non_blocking_advisories and diagnostic_fail_count > 0:
            add_unique(warnings, f"trading_validation_diagnostic_failures{suffix_text}")
        if readiness == "scale_ready" and discipline_count != expected_discipline_count:
            add_unique(blockers, f"trading_validation_incomplete{suffix_text}")
            return
        if include_non_blocking_advisories and readiness == "research_only":
            add_unique(warnings, f"trading_validation_research_only{suffix_text}")
        elif include_non_blocking_advisories and readiness == "probe":
            add_unique(warnings, f"trading_validation_probe{suffix_text}")
        lane_summary = (
            row.get("lane_authority_summary")
            if isinstance(row.get("lane_authority_summary"), dict)
            else {}
        )
        if (
            include_non_blocking_advisories
            and _safe_int(lane_summary.get("reduced_lane_count")) > 0
        ):
            add_unique(
                warnings,
                f"trading_validation_lane_authority_reduced{suffix_text}",
            )

    venues = (
        trading_validation_status.get("venues")
        if isinstance(trading_validation_status.get("venues"), dict)
        else {}
    )
    has_aggregate_payload = (
        not bool(venues)
        or isinstance(trading_validation_status.get("summary"), dict)
        or isinstance(trading_validation_status.get("payload"), dict)
        or bool(trading_validation_status.get("stale"))
        or bool(trading_validation_status.get("revision_mismatch"))
        or bool(trading_validation_status.get("status"))
    )
    if has_aggregate_payload:
        append_one(
            trading_validation_status,
            include_non_blocking_advisories=not bool(venues),
        )
    for venue, row in venues.items():
        if isinstance(row, dict):
            append_one(row, str(venue or "").strip().lower())


def _is_trading_validation_advisory_signal(signal: str) -> bool:
    value = str(signal or "").strip()
    return any(
        value.startswith(prefix)
        for prefix in TRADING_VALIDATION_ADVISORY_PREFIXES
    )


def _failed_discipline_summary(
    row: dict[str, Any],
    *,
    limit: int = 8,
) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    disciplines = (
        payload.get("disciplines")
        if isinstance(payload.get("disciplines"), list)
        else row.get("disciplines")
        if isinstance(row.get("disciplines"), list)
        else []
    )
    failed: list[str] = []
    for discipline in disciplines:
        if not isinstance(discipline, dict):
            continue
        if str(discipline.get("status") or "").strip().lower() != "fail":
            continue
        discipline_id = str(discipline.get("id") or "").strip()
        if discipline_id and discipline_id not in failed:
            failed.append(discipline_id)
    limit = max(int(limit or 0), 0)
    visible = failed[:limit] if limit > 0 else []
    return {
        "ids": visible,
        "count": len(failed),
        "omitted_ids": failed[limit:] if limit > 0 else failed,
    }


def _failed_discipline_ids(row: dict[str, Any], *, limit: int = 8) -> list[str]:
    return list(_failed_discipline_summary(row, limit=limit)["ids"])


def _validation_advisory_note(signal: str) -> str:
    if signal.startswith("trading_validation_diagnostic_failures"):
        return "성과/위험 진단 실패가 남아 있어 표본 축적 거래는 유지하되 확대 조건을 더 치밀하게 검증합니다."
    if signal.startswith("trading_validation_strategy_blocked"):
        return "전략 성과 검증이 해당 venue의 공격 진입/스케일업을 막고 있습니다. 시스템 장애가 아니라 전략 게이트입니다."
    if signal.startswith("trading_validation_lane_authority_reduced"):
        return "lane별 실거래 근거를 쌓는 단계입니다. 탐색/대기진입은 유지하고, 확대만 제한합니다."
    if signal.startswith("trading_validation_probe"):
        return "표본 축적/probe 단계입니다. 탐색 거래와 대기진입을 유지하고, 검증될수록 sizing을 확대합니다."
    return "거래 검증 상태를 확인해야 합니다."


def _validation_bottlenecks_for_venue(
    trading_validation_status: dict[str, Any],
    venue: str,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    rows = (
        trading_validation_status.get("bottlenecks")
        if isinstance(trading_validation_status.get("bottlenecks"), list)
        else []
    )
    clean_venue = str(venue or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_venue = str(row.get("venue") or "").strip().lower()
        if clean_venue and row_venue != clean_venue:
            continue
        out.append(
            {
                "venue": str(row.get("venue") or ""),
                "id": str(row.get("id") or ""),
                "label": str(row.get("label") or row.get("id") or ""),
                "status": str(row.get("status") or ""),
                "evidence": str(row.get("evidence") or ""),
                "action": str(row.get("action") or ""),
            }
        )
        if len(out) >= max(int(limit or 0), 0):
            break
    return out


def _trading_validation_advisory_details(
    trading_validation_status: dict[str, Any],
    advisories: list[str],
) -> list[dict[str, Any]]:
    advisory_set = {str(signal or "").strip() for signal in advisories}
    if not advisory_set:
        return []

    rows: list[tuple[str, dict[str, Any]]] = []
    venues = (
        trading_validation_status.get("venues")
        if isinstance(trading_validation_status.get("venues"), dict)
        else {}
    )
    if venues:
        rows.extend(
            (str(venue or "").strip().lower(), row)
            for venue, row in venues.items()
            if isinstance(row, dict)
        )
    else:
        rows.append(("", trading_validation_status))

    details: list[dict[str, Any]] = []
    for venue, row in rows:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        nested_summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        lane_summary = (
            row.get("lane_authority_summary")
            if isinstance(row.get("lane_authority_summary"), dict)
            else {}
        )
        readiness = str(summary.get("readiness") or "").strip().lower()
        fail_count = _safe_int(summary.get("fail_count"))
        hard_fail_count = _safe_int(
            summary.get("hard_fail_count")
            if summary.get("hard_fail_count") is not None
            else _safe_int(summary.get("core_fail_count"))
            + _safe_int(summary.get("core_missing_count"))
        )
        diagnostic_fail_count = _safe_int(
            summary.get("diagnostic_fail_count")
            if summary.get("diagnostic_fail_count") is not None
            else max(fail_count - hard_fail_count, 0)
        )
        signal_readiness = readiness
        if diagnostic_fail_count > 0 and signal_readiness in {"normal", "scale_ready"}:
            signal_readiness = "probe"
        suffix = f"_{venue}" if venue else ""
        candidate_signals: list[str] = []
        if diagnostic_fail_count > 0:
            candidate_signals.append(f"trading_validation_diagnostic_failures{suffix}")
        if readiness == "blocked_by_validation":
            candidate_signals.append(f"trading_validation_strategy_blocked{suffix}")
        if signal_readiness == "probe":
            candidate_signals.append(f"trading_validation_probe{suffix}")
        if _safe_int(lane_summary.get("reduced_lane_count")) > 0:
            candidate_signals.append(f"trading_validation_lane_authority_reduced{suffix}")

        for signal in candidate_signals:
            if signal not in advisory_set:
                continue
            failed_summary = _failed_discipline_summary(row)
            detail = {
                "signal": signal,
                "venue": venue or "aggregate",
                "readiness": signal_readiness or readiness,
                "diagnostic_status": str(
                    summary.get("diagnostic_status")
                    or row.get("diagnostic_status")
                    or ""
                ),
                "score": _safe_float(
                    summary.get("total_score")
                    if summary.get("total_score") is not None
                    else row.get("score")
                ),
                "fail_count": fail_count,
                "diagnostic_fail_count": diagnostic_fail_count,
                "sample_count": _safe_int(
                    summary.get("active_revision_sample_count")
                    if summary.get("active_revision_sample_count") is not None
                    else nested_summary.get("active_revision_sample_count")
                ),
                "min_samples_to_scale": _safe_int(
                    summary.get("min_samples_to_scale")
                    if summary.get("min_samples_to_scale") is not None
                    else nested_summary.get("min_samples_to_scale")
                ),
                "reduced_lane_count": _safe_int(
                    lane_summary.get("reduced_lane_count")
                ),
                "failed_discipline_ids": failed_summary["ids"],
                "note": _validation_advisory_note(signal),
            }
            if lane_summary.get("execution_posture"):
                detail["execution_posture"] = str(lane_summary.get("execution_posture"))
            if lane_summary.get("probe_lane_count") is not None:
                detail["probe_lane_count"] = _safe_int(
                    lane_summary.get("probe_lane_count")
                )
            if lane_summary.get("scale_blocked_lane_count") is not None:
                detail["scale_blocked_lane_count"] = _safe_int(
                    lane_summary.get("scale_blocked_lane_count")
                )
            if failed_summary["count"] > len(failed_summary["ids"]):
                detail["failed_discipline_count"] = failed_summary["count"]
                detail["omitted_failed_discipline_ids"] = failed_summary[
                    "omitted_ids"
                ]
            top_bottlenecks = _validation_bottlenecks_for_venue(
                trading_validation_status,
                venue,
            )
            if top_bottlenecks:
                detail["top_bottlenecks"] = top_bottlenecks
            weak_lanes = _csv_list(lane_summary.get("weak_lanes"))
            if weak_lanes:
                detail["weak_lanes"] = weak_lanes[:8]
            probe_lane_names = _csv_list(lane_summary.get("probe_lane_names"))
            if probe_lane_names:
                detail["probe_lane_names"] = probe_lane_names[:8]
            scale_blocked_lanes = _csv_list(lane_summary.get("scale_blocked_lanes"))
            if scale_blocked_lanes:
                detail["scale_blocked_lanes"] = scale_blocked_lanes[:8]
            details.append(detail)
    return details


def build_ops_runner_liveness(
    *,
    processes: dict[str, dict[str, Any]],
    enabled: dict[str, bool],
) -> dict[str, list[str]]:
    stale_processes = [
        key
        for key, row in processes.items()
        if isinstance(row, dict) and bool(row.get("stale_process"))
    ]
    missing_processes = [
        key
        for key, is_enabled in enabled.items()
        if bool(is_enabled)
        and not bool(
            (processes.get(key) if isinstance(processes.get(key), dict) else {}).get(
                "direct_alive"
            )
        )
    ]
    duplicate_processes = [
        key
        for key, row in processes.items()
        if isinstance(row, dict) and _safe_int(row.get("matched_count")) > 1
    ]
    warnings: list[str] = []
    if stale_processes or duplicate_processes:
        warnings.append("restart_required")
    for key in missing_processes:
        warnings.append(f"{key}_runner_stopped")
    for key in duplicate_processes:
        warnings.append(f"{key}_runner_duplicated")
    return {
        "stale_processes": stale_processes,
        "missing_processes": missing_processes,
        "duplicate_processes": duplicate_processes,
        "warnings": warnings,
    }


def build_ops_environment_signals(
    *,
    admin_token_configured: bool,
    disk_space_status: dict[str, Any],
    live_execution: dict[str, bool],
    readiness: dict[str, bool],
    kill_switch_enabled: bool,
    binance_kill_switch_enabled: bool,
    memory_status: dict[str, Any],
    feature_enabled: dict[str, bool],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    live_trading_enabled = any(bool(value) for value in live_execution.values())

    if not bool(admin_token_configured):
        blockers.append("admin_token_not_configured")
    disk_status = str(disk_space_status.get("status") or "").strip().lower()
    if disk_status == "critical":
        blockers.append("disk_space_critical")
    elif disk_status == "low":
        warnings.append("disk_space_low")
    elif disk_status == "error":
        warnings.append("disk_space_status_error")
    runtime_storage = (
        disk_space_status.get("runtime_storage")
        if isinstance(disk_space_status.get("runtime_storage"), dict)
        else {}
    )
    runtime_storage_status = str(runtime_storage.get("status") or "").lower()
    if runtime_storage_status == "risk":
        blockers.append("runtime_storage_risk")
    elif runtime_storage_status == "warning":
        warnings.append("runtime_storage_warning")
    elif runtime_storage_status == "error":
        warnings.append("runtime_storage_status_error")
    cold_archive = (
        runtime_storage.get("cold_archive")
        if isinstance(runtime_storage.get("cold_archive"), dict)
        else {}
    )
    cold_archive_status = str(cold_archive.get("status") or "").lower()
    cold_snapshot = (
        cold_archive.get("verification_snapshot")
        if isinstance(cold_archive.get("verification_snapshot"), dict)
        else {}
    )
    if cold_archive_status == "corrupt" or list(
        cold_archive.get("corrupt_entry_ids") or []
    ):
        warnings.append("runtime_cold_archive_corrupt")
    elif cold_archive_status in {"warning", "error"} or str(
        cold_snapshot.get("status") or ""
    ).lower() in {"missing", "stale", "invalid"}:
        warnings.append("runtime_cold_archive_unverified")

    readiness_checks = {
        "kis": ("kis_primary", "kis_primary_not_ready_for_live_orders"),
        "binance_spot": ("binance_spot", "binance_spot_not_ready_for_live_orders"),
        "binance_futures": (
            "binance_futures",
            "binance_futures_not_ready_for_live_orders",
        ),
        "upbit": ("upbit", "upbit_not_ready_for_live_orders"),
    }
    for execution_key, (ready_key, blocker) in readiness_checks.items():
        if bool(live_execution.get(execution_key)) and not bool(
            readiness.get(ready_key)
        ):
            blockers.append(blocker)

    if bool(kill_switch_enabled):
        warnings.append("kill_switch_enabled")
    if bool(binance_kill_switch_enabled):
        warnings.append("binance_kill_switch_enabled")
    if not bool(memory_status.get("seeded")):
        warnings.append("memory_not_seeded")
    if _safe_int(memory_status.get("validation_repair_backlog_count")) > 0:
        warnings.append("validation_repair_backlog_pending")

    feature_warning_keys = {
        "investment_memory": "investment_memory_disabled",
        "live_evaluator": "live_evaluator_disabled",
        "market_judge": "market_judge_disabled",
        "market_pulse": "market_pulse_disabled",
        "watchdog": "watchdog_disabled",
        "crypto_market_research": "crypto_market_research_disabled",
        "crypto_alpha": "crypto_alpha_disabled",
    }
    for feature_key, warning in feature_warning_keys.items():
        if not bool(feature_enabled.get(feature_key)):
            warnings.append(warning)

    return {
        "blockers": blockers,
        "warnings": warnings,
        "live_trading_enabled": live_trading_enabled,
        "paper_mode": not live_trading_enabled,
    }


def _split_readiness_actions(
    *,
    blockers: list[str],
    warnings: list[str],
    advisories: list[str],
    stale_processes: list[str],
    missing_processes: list[str],
    duplicate_processes: list[str],
) -> dict[str, list[dict[str, Any]]]:
    operational = build_ops_remediation_actions(
        blockers=blockers,
        warnings=warnings,
        stale_processes=stale_processes,
        missing_processes=missing_processes,
        duplicate_processes=duplicate_processes,
    )
    advisory = build_ops_remediation_actions(
        blockers=[],
        warnings=advisories,
        stale_processes=[],
        missing_processes=[],
        duplicate_processes=[],
    )
    return {
        "operational_remediation_actions": operational,
        "advisory_actions": advisory,
        "remediation_actions": [*operational, *advisory],
    }


def finalize_ops_readiness_signals(
    *,
    environment_signals: dict[str, Any],
    trading_validation_status: dict[str, Any],
    runner_liveness: dict[str, Any],
    llm_operational: dict[str, Any],
    semantic_checks: dict[str, Any],
) -> dict[str, Any]:
    def add_unique(target: list[str], value: Any) -> None:
        clean = str(value or "").strip()
        if clean and clean not in target:
            target.append(clean)

    blockers: list[str] = list(environment_signals.get("blockers") or [])
    warnings: list[str] = list(environment_signals.get("warnings") or [])
    trading_validation_signals: list[str] = []
    append_trading_validation_ops_signals(
        trading_validation_status,
        blockers=blockers,
        warnings=trading_validation_signals,
    )
    advisories: list[str] = []
    for signal in trading_validation_signals:
        if _is_trading_validation_advisory_signal(signal):
            add_unique(advisories, signal)
        else:
            add_unique(warnings, signal)
    for warning in list(runner_liveness.get("warnings") or []):
        add_unique(warnings, warning)

    critical_llm = (
        llm_operational.get("critical") if isinstance(llm_operational, dict) else {}
    )
    critical_llm = critical_llm if isinstance(critical_llm, dict) else {}
    for manager_key, warning in (
        ("kis_block_manager", "kis_block_manager_last_run_failed"),
        ("binance_block_manager", "binance_block_manager_last_run_failed"),
    ):
        manager_status = critical_llm.get(manager_key)
        manager_status = manager_status if isinstance(manager_status, dict) else {}
        if (
            str(manager_status.get("status")) in LLM_FAILURE_STATUSES
            and not bool(manager_status.get("stale_after_restart"))
        ):
            add_unique(warnings, warning)
    market_llm_status = critical_llm.get("market_judge")
    market_llm_status = market_llm_status if isinstance(market_llm_status, dict) else {}
    if (
        str(market_llm_status.get("status")) in LLM_FAILURE_STATUSES
        and not bool(market_llm_status.get("stale_after_restart"))
    ):
        add_unique(warnings, "market_judge_llm_recent_failure")
    for warning in list(semantic_checks.get("warnings") or []):
        add_unique(warnings, warning)

    stale_processes = list(runner_liveness.get("stale_processes") or [])
    missing_processes = list(runner_liveness.get("missing_processes") or [])
    duplicate_processes = list(runner_liveness.get("duplicate_processes") or [])
    status = "green"
    if blockers:
        status = "red"
    elif warnings:
        status = "yellow"
    advisory_details = _trading_validation_advisory_details(
        trading_validation_status,
        advisories,
    )
    return {
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "advisories": advisories,
        "trading_validation_advisories": advisories,
        "advisory_details": advisory_details,
        "trading_validation_advisory_details": advisory_details,
        "stale_processes": stale_processes,
        "missing_processes": missing_processes,
        "duplicate_processes": duplicate_processes,
        **_split_readiness_actions(
            blockers=blockers,
            warnings=warnings,
            advisories=advisories,
            stale_processes=stale_processes,
            missing_processes=missing_processes,
            duplicate_processes=duplicate_processes,
        ),
    }


def build_ops_memory_payload(
    *,
    enabled: bool,
    memory_status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "seeded": bool(memory_status.get("seeded")),
        "pending_reflection_count": _safe_int(
            memory_status.get("pending_event_count")
        ),
        "reflection_count": _safe_int(memory_status.get("reflection_count")),
        "latest_reflection_at": str(
            memory_status.get("latest_reflection_at") or ""
        ),
        "scorecard_count": _safe_int(memory_status.get("scorecard_count")),
        "policy_rule_count": _safe_int(memory_status.get("policy_rule_count")),
        "active_policy_rule_count": _safe_int(
            memory_status.get("active_policy_rule_count")
        ),
        "validation_repair_backlog_status": str(
            memory_status.get("validation_repair_backlog_status") or "clear"
        ),
        "validation_repair_backlog_count": _safe_int(
            memory_status.get("validation_repair_backlog_count")
        ),
        "status": memory_status.get("status"),
    }


def build_ops_reports_payload(
    *,
    enabled: bool,
    repository_status: dict[str, Any],
    runner: dict[str, Any],
    state_path: str,
    interval_sec: Any,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "repository": repository_status,
        "runner": runner,
        "state_path": state_path,
        "interval_sec": _safe_int(interval_sec),
    }


def build_ops_live_evaluator_payload(
    *,
    enabled: bool,
    state_path: str,
    edge_db_path: str,
    performance_db_path: str,
    interval_sec: Any,
    runner: dict[str, Any],
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "state_path": state_path,
        "edge_db_path": edge_db_path,
        "performance_db_path": performance_db_path,
        "interval_sec": _safe_int(interval_sec),
        "runner": runner,
        "authority_endpoint": "/api/live/authority",
    }


def build_ops_trading_validation_payload(
    *,
    status: dict[str, Any],
    db_path: str,
) -> dict[str, Any]:
    return {
        "status": status.get("status"),
        "db_path": db_path,
        "latest_run_id": status.get("run_id", ""),
        "latest_at": status.get("computed_at", ""),
        "summary": status.get("summary", {}),
        "readiness": status.get("readiness", ""),
        "diagnostic_status": status.get("diagnostic_status", ""),
        "score": status.get("score"),
        "discipline_count": status.get("discipline_count"),
        "expected_discipline_count": status.get("expected_discipline_count"),
        "venues": _compact_trading_validation_venues(status.get("venues", {})),
        "lane_authority_summary": _compact_lane_authority_summary(
            status.get("lane_authority_summary", {})
        ),
        "bottlenecks": _compact_rows(status.get("bottlenecks", [])),
        "primary_next_actions": _compact_rows(status.get("primary_next_actions", [])),
        "status_endpoint": "/api/trading/validation/status",
        "run_once_endpoint": "/api/trading/validation/run-once",
    }


def build_ops_watchdog_payload(
    *,
    enabled: bool,
    state_path: str,
    db_path: str,
    interval_sec: Any,
    runner: dict[str, Any],
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "state_path": state_path,
        "db_path": db_path,
        "interval_sec": _safe_int(interval_sec),
        "runner": runner,
        "status_endpoint": "/api/ops/watchdog/status",
    }


def build_ops_market_judge_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "status": status,
        "schedule": _compact_market_judge_schedule(schedule),
    }


def _compact_market_judge_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schedule, dict):
        return {}
    scalar_keys = {
        "status",
        "quote_interval_sec",
        "judge_interval_sec",
        "latest_llm_run_at",
        "seconds_since_llm",
        "next_llm_due_at",
    }
    compact = {
        key: value
        for key, value in schedule.items()
        if key in scalar_keys
        and isinstance(value, (str, int, float, bool))
        and value not in (None, "")
    }
    clock = schedule.get("clock")
    if isinstance(clock, dict):
        compact_clock = {
            key: value
            for key, value in clock.items()
            if key
            in {
                "status",
                "timezone",
                "now",
                "date",
                "session",
                "phase",
                "is_market_open",
                "next_open_at",
            }
            and value not in (None, "", [], {})
        }
        if compact_clock:
            compact["clock"] = compact_clock
    recent_runs = schedule.get("recent_runs")
    if isinstance(recent_runs, list):
        rows = [
            _compact_market_judge_run(row)
            for row in recent_runs[:3]
            if isinstance(row, dict)
        ]
        if rows:
            compact["recent_runs"] = rows
    return compact


def _compact_market_judge_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        in {
            "id",
            "run_at",
            "market_session",
            "status",
            "mode",
            "model",
            "error_message",
        }
        and value not in (None, "", [], {})
    }


def build_ops_market_pulse_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "status": _compact_market_pulse_status(status),
    }


def _compact_market_pulse_status(status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {}
    compact = {
        key: value
        for key, value in status.items()
        if key in {"status", "db_path", "enabled"}
        and value not in (None, "", [], {})
    }
    latest = status.get("latest")
    if isinstance(latest, dict):
        compact_latest = _compact_market_pulse_latest(latest)
        if compact_latest:
            compact["latest"] = compact_latest
    return compact


def _compact_market_pulse_latest(latest: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in latest.items()
        if key
        in {
            "status",
            "captured_at",
            "trading_day",
            "regime",
            "score",
            "score_method_version",
            "risk_flags",
            "data_gaps",
        }
        and value not in (None, "")
    }
    for key, keys, limit in (
        (
            "indices",
            {"code", "name", "value", "change", "change_pct", "direction", "status"},
            8,
        ),
        (
            "investor_flows",
            {
                "market",
                "name",
                "bias",
                "foreign_net_buy_100m_krw",
                "institution_net_buy_100m_krw",
                "individual_net_buy_100m_krw",
                "foreign_institution_sum_100m_krw",
                "as_of",
                "status",
            },
            6,
        ),
        (
            "program_trading",
            {
                "market",
                "name",
                "bias",
                "program_net_buy_100m_krw",
                "arbitrage_net_buy_100m_krw",
                "non_arbitrage_net_buy_100m_krw",
                "as_of",
                "status",
            },
            6,
        ),
    ):
        rows = latest.get(key)
        if isinstance(rows, list):
            compact_rows = [
                _compact_market_pulse_row(row, keys=keys)
                for row in rows[:limit]
                if isinstance(row, dict)
            ]
            if compact_rows:
                compact[key] = compact_rows
    for key, keys in (
        (
            "futures",
            {
                "status",
                "basis",
                "basis_pct",
                "basis_signal",
                "futures_code",
                "futures_value",
                "futures_change_pct",
                "spot_code",
                "spot_value",
                "spot_change_pct",
            },
        ),
        (
            "fx",
            {"status", "code", "name", "value", "change", "direction", "as_of"},
        ),
        (
            "block_exposure",
            {
                "status",
                "block_count",
                "concentration_flags",
                "pressure_flags",
            },
        ),
    ):
        row = latest.get(key)
        if isinstance(row, dict):
            compact_row = _compact_market_pulse_row(row, keys=keys)
            if compact_row:
                compact[key] = compact_row
    return compact


def _compact_market_pulse_row(row: dict[str, Any], *, keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", [], {})
    }


def build_ops_kis_block_trader_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
    next_manager_run_at: str,
) -> dict[str, Any]:
    payload = {
        "enabled": bool(enabled),
        "status": _compact_trader_status(status),
        "next_manager_run_at": next_manager_run_at,
    }
    gap = _wiki_action_reference_gap(status)
    if gap:
        payload["wiki_action_reference_gap"] = gap
        payload["warnings"] = ["kis_jue_wiki_action_reference_gap_unresolved"]
    return payload


def build_ops_binance_block_trader_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
    runner: dict[str, Any],
    model: str,
    reasoning_effort: str,
    spot_live: bool,
    futures_live: bool,
    upbit_live: bool,
    account_risk_pct: Any,
    max_total_exposure_usdt: Any,
    max_symbol_exposure_pct: Any,
    min_reward_risk: Any,
    next_manager_run_at: str,
) -> dict[str, Any]:
    compact_status = _compact_binance_trader_status(status)
    stale_manager_error = _manager_error_stale_after_restart(status, runner)
    if stale_manager_error:
        compact_status["latest_manager_error_stale_after_restart"] = True
        compact_status["latest_stale_manager_error"] = _compact_json_value(
            stale_manager_error
        )
        compact_status.pop("latest_manager_error", None)
        compact_status.pop("latest_unresolved_manager_error", None)
    payload = {
        "enabled": bool(enabled),
        "status": compact_status,
        "execution": {
            "spot_mode": "live" if bool(spot_live) else "paper",
            "futures_mode": "live" if bool(futures_live) else "paper",
            "upbit_spot_mode": "live" if bool(upbit_live) else "paper",
        },
        "runner": _compact_runner_payload(runner),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "risk": {
            "account_risk_pct": _safe_float(account_risk_pct),
            "max_total_exposure_usdt": _safe_float(max_total_exposure_usdt),
            "max_symbol_exposure_pct": _safe_float(max_symbol_exposure_pct),
            "min_reward_risk": _safe_float(min_reward_risk),
        },
        "next_manager_run_at": next_manager_run_at,
    }
    warnings: list[str] = []
    if isinstance(runner, dict) and bool(runner.get("stale_process")):
        warnings.append("binance_runner_stale_restart_required")
    if _has_unresolved_manager_error(status, runner):
        warnings.append("binance_block_manager_last_run_failed")
    entry_activity = _compact_binance_entry_activity(status)
    if entry_activity:
        payload["entry_activity"] = entry_activity
    pressure = _compact_binance_activity_pressure(status)
    if pressure:
        payload["activity_pressure"] = pressure
        if str(pressure.get("status") or "") == "action_required":
            warnings.append("binance_activity_pressure_open")
        repair_actions = _compact_binance_activity_repair_actions(pressure)
        if repair_actions:
            payload["activity_repair_actions"] = repair_actions
    contract_replay = _compact_binance_contract_replay_recovery(status)
    if contract_replay:
        payload["manager_contract_replay"] = contract_replay
        if _binance_contract_replay_recovered_warning(contract_replay):
            warnings.append("binance_manager_contract_replay_recovered")
        if _binance_contract_replay_current_error_warning(contract_replay):
            warnings.append("binance_manager_contract_replay_current_error")
    gap = _wiki_action_reference_gap(status)
    if gap:
        payload["wiki_action_reference_gap"] = gap
        warnings.append("binance_jue_wiki_action_reference_gap_unresolved")
    if warnings:
        payload["warnings"] = warnings
    return payload


def _compact_binance_entry_activity(status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {}
    activity = status.get("entry_activity")
    if not isinstance(activity, dict):
        return {}
    field_names = (
        "version",
        "status",
        "latest_binance_entry_at",
        "latest_binance_entry_market",
        "latest_upbit_entry_at",
        "binance_entry_stale_hours",
        "binance_entry_count",
        "upbit_entry_count",
    )
    return {
        key: _compact_json_value(activity.get(key))
        for key in field_names
        if activity.get(key) not in (None, "", [], {})
    }


def _compact_binance_activity_pressure(status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {}
    latest = status.get("latest_decision_input")
    source_payload = (
        {**status, **latest}
        if isinstance(latest, dict)
        else status
    )
    field_map = {
        "current_replay_pressure_status": "status",
        "current_replay_pressure_level": "level",
        "current_replay_pressure_source": "source",
        "current_replay_zero_action_streak": "zero_action_streak",
        "current_replay_binance_zero_action_streak": "binance_zero_action_streak",
        "current_replay_binance_activity_gap_status": "activity_gap_status",
        "current_replay_binance_entry_stale_hours": "entry_stale_hours",
        "current_replay_binance_candidate_symbols": "candidate_symbols",
    }
    return {
        target: _compact_json_value(source_payload.get(source))
        for source, target in field_map.items()
        if source_payload.get(source) not in (None, "", [], {})
    }


def _compact_binance_activity_repair_actions(
    pressure: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(pressure, dict):
        return []
    if str(pressure.get("status") or "") != "action_required":
        return []
    candidate_symbols = _binance_activity_candidate_symbols(
        pressure.get("candidate_symbols")
    )
    symbol_payload = {"symbols": candidate_symbols} if candidate_symbols else None
    actions = [
        {
            "id": "refresh_binance_crypto_research_context",
            "label": "Binance 후보 리서치 갱신",
            "detail": (
                "활동 공백 후보의 최신 뉴스, 구조, 근거를 다시 수집해 "
                "research_only/insufficient gate를 줄입니다."
            ),
            "severity": "warn",
            "endpoint": "/api/crypto/research/run-once",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
        },
        {
            "id": "collect_binance_market_structure",
            "label": "Binance 시장 구조 수집",
            "detail": (
                "후보 심볼의 kline/market-structure 근거를 갱신해 "
                "pattern prior와 live crosscheck 결손을 줄입니다."
            ),
            "severity": "warn",
            "endpoint": "/api/crypto/research/collect",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
        },
        {
            "id": "refresh_binance_alpha_context",
            "label": "Binance 알파 컨텍스트 갱신",
            "detail": (
                "알파 컨텍스트를 새로 수집해 confidence/live-authority "
                "판정에 최신 후보 근거를 반영합니다."
            ),
            "severity": "warn",
            "endpoint": "/api/crypto/alpha/collect",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
        },
    ]
    if symbol_payload:
        for action in actions[:2]:
            action["request_payload"] = dict(symbol_payload)
    return actions


def _binance_activity_candidate_symbols(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    symbols: list[str] = []
    for raw_symbol in value:
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol or symbol in symbols:
            continue
        symbols.append(symbol)
        if len(symbols) >= 8:
            break
    return symbols


def _compact_binance_contract_replay_recovery(
    status: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {}
    latest = status.get("latest_decision_input")
    if not isinstance(latest, dict):
        return {}
    replay_status = str(latest.get("contract_replay_status") or "").strip()
    if replay_status not in {
        "stored_error_resolved_by_current_contract",
        "current_contract_error",
    }:
        return {}
    field_names = (
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
    )
    return {
        key: _compact_binance_contract_replay_value(key, latest.get(key))
        for key in field_names
        if latest.get(key) not in (None, "", [], {})
    }


def _compact_binance_contract_replay_value(key: str, value: Any) -> Any:
    if key == "current_replay_auto_create_preview":
        return _compact_binance_contract_replay_auto_create_preview(value)
    return _compact_json_value(value)


def _compact_binance_contract_replay_auto_create_preview(
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    text_keys = {
        "symbol",
        "market",
        "side",
        "entry_style",
        "entry_trigger_operator",
        "auto_materialized_reason",
    }
    numeric_keys = {
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
    previews: list[dict[str, Any]] = []
    for row in value[:8]:
        if not isinstance(row, dict):
            continue
        preview: dict[str, Any] = {}
        for item_key in text_keys:
            item = row.get(item_key)
            if item not in (None, "", [], {}):
                preview[item_key] = _short_text(item, limit=160)
        for item_key in numeric_keys:
            item = row.get(item_key)
            if item not in (None, "", [], {}):
                parsed = _safe_float(item)
                if parsed > 0:
                    preview[item_key] = parsed
        if preview:
            previews.append(preview)
    return previews


def _binance_contract_replay_recovered_warning(replay: dict[str, Any]) -> bool:
    if not replay:
        return False
    if replay.get("contract_replay_status") != "stored_error_resolved_by_current_contract":
        return False
    return _safe_int(replay.get("current_replay_action_count")) > _safe_int(
        replay.get("action_count")
    )


def _binance_contract_replay_current_error_warning(replay: dict[str, Any]) -> bool:
    if not replay:
        return False
    return replay.get("contract_replay_status") == "current_contract_error"


def _compact_binance_trader_status(status: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_trader_status(status)
    if not isinstance(status, dict):
        return compact
    performance = status.get("performance")
    if isinstance(performance, dict):
        compact["performance"] = _compact_metric_dict(
            performance,
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
    performance_today = status.get("performance_today")
    if isinstance(performance_today, dict):
        compact["performance_today"] = _compact_metric_dict(
            performance_today,
            keys={
                "sample_count",
                "avg_r_multiple",
                "win_rate_pct",
                "realized_pnl_usdt",
                "total_cost_usdt",
                "profit_factor",
                "max_drawdown_usdt",
                "recovery_factor",
            },
        )
    risk = status.get("risk")
    if isinstance(risk, dict):
        compact["risk"] = _compact_metric_dict(
            risk,
            keys={
                "account_risk_pct",
                "max_total_exposure_usdt",
                "max_symbol_exposure_pct",
                "min_reward_risk",
            },
        )
    growth_unlock = status.get("growth_unlock")
    if isinstance(growth_unlock, dict):
        compact["growth_unlock"] = _compact_growth_unlock(growth_unlock)
    growth_governor = status.get("growth_governor")
    if isinstance(growth_governor, dict):
        compact["growth_governor"] = _compact_growth_governor(growth_governor)
    growth_target = status.get("growth_target")
    if isinstance(growth_target, dict):
        compact["growth_target"] = _compact_metric_dict(
            growth_target,
            keys={
                "status",
                "month_key",
                "monthly_target_pct",
                "current_return_pct",
                "remaining_return_pct",
                "required_daily_return_pct",
                "remaining_days",
            },
        )
    risk_guard = status.get("risk_guard")
    if isinstance(risk_guard, dict):
        compact["risk_guard"] = _compact_risk_guard(risk_guard)
    return compact


def _compact_metric_dict(row: dict[str, Any], *, keys: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", [], {})
    }


def _compact_growth_unlock(row: dict[str, Any]) -> dict[str, Any]:
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


def _compact_growth_governor(row: dict[str, Any]) -> dict[str, Any]:
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


def _compact_risk_guard(row: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_metric_dict(
        row,
        keys={
            "version",
            "status",
            "current_equity_usdt",
            "daily_loss_stop_pct",
            "monthly_loss_stop_pct",
            "allow_new_entries",
        },
    )
    for key in ("day", "month"):
        period = row.get(key)
        if isinstance(period, dict):
            compact_period = _compact_metric_dict(
                period,
                keys={"period_key", "start_equity_usdt", "return_pct"},
            )
            if compact_period:
                compact[key] = compact_period
    return compact


def build_ops_crypto_market_research_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
    runner: dict[str, Any],
    model: str,
    reasoning_effort: str,
    feature_interval_sec: Any,
    llm_interval_sec: Any,
    max_symbols: Any,
    llm_top_symbols: Any,
    kline_intervals: Any,
    regime_enabled: bool,
    squeeze_guard_enabled: bool,
    auto_universe_enabled: bool,
    auto_universe_limit: Any,
) -> dict[str, Any]:
    intervals = (
        kline_intervals
        if isinstance(kline_intervals, dict)
        else _csv_list(kline_intervals)
    )
    return {
        "enabled": bool(enabled),
        "status": status,
        "runner": runner,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "feature_interval_sec": _safe_int(feature_interval_sec),
        "llm_interval_sec": _safe_int(llm_interval_sec),
        "max_symbols": _safe_int(max_symbols),
        "llm_top_symbols": _safe_int(llm_top_symbols),
        "kline_intervals": intervals,
        "regime_enabled": bool(regime_enabled),
        "squeeze_guard_enabled": bool(squeeze_guard_enabled),
        "auto_universe_enabled": bool(auto_universe_enabled),
        "auto_universe_limit": _safe_int(auto_universe_limit),
    }


def build_ops_crypto_alpha_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
    runner: dict[str, Any],
    model: str,
    reasoning_effort: str,
    crawl_interval_sec: Any,
    outcome_interval_sec: Any,
    context_limit: Any,
    source_ids: Any,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "status": status,
        "runner": runner,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "crawl_interval_sec": _safe_int(crawl_interval_sec),
        "outcome_interval_sec": _safe_int(outcome_interval_sec),
        "context_limit": _safe_int(context_limit),
        "source_ids": _csv_list(source_ids),
    }


def _stored_wiki_publication_age(
    status: dict[str, Any],
    v3: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[int, str]:
    explicit = v3.get("publication_age_sec")
    if explicit is None:
        explicit = status.get("publication_age_sec")
    if explicit is not None:
        try:
            parsed = int(explicit)
        except (TypeError, ValueError):
            return 0, "invalid"
        return (parsed, "ok") if parsed >= 0 else (0, "invalid")
    ops_snapshot = (
        status.get("ops_snapshot")
        if isinstance(status.get("ops_snapshot"), dict)
        else {}
    )
    generated_at = str(ops_snapshot.get("generated_at") or "").strip()
    if not generated_at:
        return 0, "missing"
    try:
        published = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return 0, "invalid"
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(int((current - published).total_seconds()), 0), "ok"


def build_stored_jue_wiki_readiness_status(
    status: dict[str, Any],
    *,
    configured_read_mode: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize an already-persisted Wiki status without running projections."""

    payload = dict(status) if isinstance(status, dict) else {}
    v3 = dict(payload.get("v3")) if isinstance(payload.get("v3"), dict) else {}
    stored_read_mode = str(
        v3.get("active_read_mode") or payload.get("active_read_mode") or ""
    ).strip().lower()
    configured_mode = str(configured_read_mode or "").strip().lower()
    if configured_mode not in {"shadow", "prefer", "required"}:
        configured_mode = ""
    active_read_mode = configured_mode or (
        stored_read_mode
        if stored_read_mode in {"shadow", "prefer", "required"}
        else "shadow"
    )
    active_read_mode_status = (
        "ok"
        if stored_read_mode in {"shadow", "prefer", "required"}
        else "missing"
        if not stored_read_mode
        else "invalid"
    )
    current = now or datetime.now(timezone.utc)
    raw_eligibility = (
        v3.get("mode_eligibility")
        if isinstance(v3.get("mode_eligibility"), dict)
        else {}
    )
    eligibility_by_venue: dict[str, dict[str, Any]] = {}
    eligibility_failures: dict[str, list[str]] = {}
    for venue in ("kis", "binance"):
        raw_row = raw_eligibility.get(venue)
        row = dict(raw_row) if isinstance(raw_row, dict) else {}
        failures: list[str] = []
        if not row:
            failures.append("eligibility_missing")
        else:
            if row.get("version") != "wiki_shadow_eligibility_v1":
                failures.append("eligibility_version_invalid")
            if str(row.get("venue") or "").strip().lower() != venue:
                failures.append("eligibility_venue_mismatch")
            if type(row.get("required_eligible")) is not bool:
                failures.append("eligibility_flag_invalid")
            sample = row.get("complete_sample_count")
            if type(sample) is not int or sample < 0:
                failures.append("eligibility_sample_invalid")
                sample = 0
            elif sample < 500:
                failures.append("eligibility_sample_insufficient")
            blocker_values = row.get("blockers")
            if not isinstance(blocker_values, list):
                failures.append("eligibility_blockers_invalid")
                blocker_values = []
            elif blocker_values:
                failures.extend(
                    f"eligibility_{str(value).strip()}"
                    for value in blocker_values
                    if str(value).strip()
                )
            freshness = wiki_eligibility_freshness_reason(row, now=current)
            if freshness:
                failures.append(freshness)
            if row.get("required_eligible") is not True:
                failures.append("eligibility_not_eligible")
            row["complete_sample_count"] = sample
            row["blockers"] = list(
                dict.fromkeys([*blocker_values, *failures])
            )
            row["required_eligible"] = not failures
        eligibility_failures[venue] = list(dict.fromkeys(failures))
        eligibility_by_venue[venue] = row
    comparison_count_by_venue = {
        venue: row.get("complete_sample_count")
        if type(row.get("complete_sample_count")) is int
        else 0
        for venue, row in eligibility_by_venue.items()
    }
    by_scope = v3.get("by_scope") if isinstance(v3.get("by_scope"), dict) else {}
    publication_age_sec, publication_status = _stored_wiki_publication_age(
        payload,
        v3,
        now=current,
    )
    warnings = list(payload.get("warnings") or [])
    blockers = list(payload.get("blockers") or [])
    advisories = list(payload.get("advisories") or [])
    scope_failures: dict[str, list[str]] = {}
    for venue in ("kis", "binance"):
        row = by_scope.get(venue) if isinstance(by_scope.get(venue), dict) else {}
        failures: list[str] = []
        if not row:
            failures.append("scope_missing")
        else:
            if not str(row.get("snapshot_id") or "").strip():
                failures.append("snapshot_missing")
            created_raw = str(row.get("snapshot_created_at") or "").strip()
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
            if created_at is None or created_at.tzinfo is None:
                failures.append("snapshot_timestamp_invalid")
            else:
                age = (current - created_at).total_seconds()
                if age < 0:
                    failures.append("snapshot_timestamp_future")
                elif age > 3600:
                    failures.append("snapshot_stale")
            for key, label in (
                ("last_ingest_status", "ingest"),
                ("last_compile_status", "compile"),
                ("last_lint_status", "lint"),
                ("last_publish_status", "publish"),
            ):
                value = str(row.get(key) or "missing").lower()
                if value != "ok":
                    failures.append(f"{label}_{value}")
            projection = str(row.get("last_projection_status") or "missing").lower()
            cleanup_only = (
                projection == "warning"
                and row.get("projection_warning_reason") == "cleanup_only"
            )
            if projection != "ok" and not cleanup_only:
                failures.append(f"projection_{projection}")
            index = row.get("index_rebuild")
            index_status = str(
                index.get("status") if isinstance(index, dict) else "missing"
            ).lower()
            if index_status != "ok":
                failures.append(f"index_{index_status}")
            for key, label in (
                ("stale_count", "stale_knowledge"),
                ("conflicted_count", "conflicted_knowledge"),
                ("orphan_page_count", "orphan_pages"),
                ("repair_backlog_count", "repair_backlog"),
            ):
                value = row.get(key)
                if type(value) is not int or value < 0:
                    failures.append(f"{label}_invalid")
                elif value:
                    failures.append(label)
        scope_failures[venue] = list(dict.fromkeys(failures))
    degraded = bool(v3) and (
        any(scope_failures.values()) or any(eligibility_failures.values())
    )
    if degraded:
        target = warnings if active_read_mode in {"prefer", "required"} else advisories
        target.append(f"jue_wiki_{active_read_mode}_knowledge_degraded")
    if active_read_mode == "required":
        root_status = str(payload.get("status") or "missing").lower()
        if root_status != "ok":
            blockers.append(f"jue_wiki_required_status_{root_status}")
        if not v3:
            blockers.append("jue_wiki_required_v3_missing")
        if stored_read_mode != "required":
            stored_reason = (
                f"mismatch_{stored_read_mode}"
                if stored_read_mode in {"shadow", "prefer"}
                else active_read_mode_status
            )
            blockers.append(f"jue_wiki_required_stored_read_mode_{stored_reason}")
        for venue in ("kis", "binance"):
            blockers.extend(
                f"jue_wiki_required_{venue}_{reason}"
                for reason in [
                    *scope_failures[venue],
                    *eligibility_failures[venue],
                ]
            )
    payload.update(
        {
            "v3": v3,
            "active_read_mode": active_read_mode,
            "configured_read_mode": configured_mode,
            "stored_read_mode": stored_read_mode,
            "read_mode_mismatch": bool(
                configured_mode and configured_mode != stored_read_mode
            ),
            "active_read_mode_status": active_read_mode_status,
            "publication_age_sec": publication_age_sec,
            "publication_status": publication_status,
            "scope_health_by_venue": {
                venue: dict(by_scope.get(venue))
                if isinstance(by_scope.get(venue), dict)
                else {}
                for venue in ("kis", "binance")
            },
            "index_rebuild": (
                dict(v3.get("index_rebuild"))
                if isinstance(v3.get("index_rebuild"), dict)
                else {}
            ),
            "comparison_count_by_venue": comparison_count_by_venue,
            "eligibility_by_venue": eligibility_by_venue,
            "warnings": list(dict.fromkeys(str(row) for row in warnings if str(row))),
            "blockers": list(dict.fromkeys(str(row) for row in blockers if str(row))),
            "advisories": list(
                dict.fromkeys(str(row) for row in advisories if str(row))
            ),
        }
    )
    return payload


def build_ops_jue_wiki_payload(
    *,
    enabled: bool,
    status: dict[str, Any],
    runner: dict[str, Any],
    state_path: str,
    interval_sec: Any,
    configured_read_mode: str | None = None,
) -> dict[str, Any]:
    stored_status = build_stored_jue_wiki_readiness_status(
        status,
        configured_read_mode=configured_read_mode,
    )
    v3 = stored_status["v3"]
    runner_state = runner.get("state") if isinstance(runner.get("state"), dict) else {}
    latest_selection = (
        status.get("latest_selection")
        if isinstance(status.get("latest_selection"), dict)
        else runner_state.get("latest_selection")
        if isinstance(runner_state.get("latest_selection"), dict)
        else {}
    )
    wiki_open_lint_count = _safe_int(
        status.get("wiki_open_lint_count")
        if status.get("wiki_open_lint_count") is not None
        else status.get("open_lint_count")
    )
    wiki_stale_page_count = _safe_int(
        status.get("wiki_stale_page_count")
        if status.get("wiki_stale_page_count") is not None
        else status.get("stale_page_count")
    )
    wiki_repair_queue_open_count = _safe_int(
        status.get("wiki_repair_queue_open_count")
        if status.get("wiki_repair_queue_open_count") is not None
        else (status.get("repair_queue") or {}).get("open_count")
        if isinstance(status.get("repair_queue"), dict)
        else 0
    )
    wiki_repair_queue_resolved_count = _safe_int(
        status.get("wiki_repair_queue_resolved_count")
        if status.get("wiki_repair_queue_resolved_count") is not None
        else (status.get("repair_queue") or {}).get("resolved_count")
        if isinstance(status.get("repair_queue"), dict)
        else 0
    )
    raw_repair_queue = (
        status.get("repair_queue")
        if isinstance(status.get("repair_queue"), dict)
        else {
            "open_count": wiki_repair_queue_open_count,
            "resolved_count": wiki_repair_queue_resolved_count,
        }
    )
    repair_queue = _compact_json_value(raw_repair_queue)
    repair_pressure = _wiki_repair_pressure(repair_queue)
    repair_health = (
        _compact_json_value(repair_queue.get("repair_health"))
        if isinstance(repair_queue.get("repair_health"), dict)
        else {}
    )
    repair_lanes = (
        raw_repair_queue.get("by_lane")
        if isinstance(raw_repair_queue.get("by_lane"), dict)
        else {}
    )
    active_page_count = _safe_int(
        status.get("active_page_count")
        if status.get("active_page_count") is not None
        else status.get("page_count")
    )
    wiki_last_selection_at = str(
        status.get("wiki_last_selection_at")
        or status.get("last_selection_at")
        or latest_selection.get("created_at")
        or latest_selection.get("selected_at")
        or runner_state.get("wiki_last_selection_at")
        or runner_state.get("last_selection_at")
        or ""
    )
    repair_state = (
        runner_state.get("repair") if isinstance(runner_state.get("repair"), dict) else {}
    )
    wiki_last_repair_at = str(
        status.get("wiki_last_repair_at")
        or status.get("last_repair_at")
        or repair_state.get("finished_at")
        or repair_state.get("started_at")
        or ""
    )
    prompt_pressure = _wiki_prompt_pressure(status, latest_selection)
    requested_symbol_coverage = _wiki_requested_symbol_coverage(
        status,
        latest_selection,
    )
    research_coverage = (
        _compact_json_value(status.get("research_coverage"))
        if isinstance(status.get("research_coverage"), dict)
        else {"warning_count": 0, "unhealthy_source_ids": []}
    )
    raw_application = (
        status.get("application") if isinstance(status.get("application"), dict) else {}
    )
    application = {
        "effectiveness_count": _safe_int(raw_application.get("effectiveness_count")),
        "degraded_count": _safe_int(raw_application.get("degraded_count")),
        "latest_recommendation": raw_application.get("latest_recommendation")
        if isinstance(raw_application.get("latest_recommendation"), dict)
        else {},
    }
    blockers = list(stored_status["blockers"])
    readiness_signals = _jue_wiki_readiness_signals(
        enabled=enabled,
        runner=runner,
        wiki_open_lint_count=wiki_open_lint_count,
        wiki_stale_page_count=wiki_stale_page_count,
        active_page_count=active_page_count,
        prompt_pressure=prompt_pressure,
        application=application,
        requested_symbol_coverage=requested_symbol_coverage,
        repair_pressure=repair_pressure,
        repair_health=repair_health,
        repair_lanes=repair_lanes,
        research_coverage=research_coverage,
    )
    return {
        "enabled": bool(enabled),
        "status": status,
        "runner": runner,
        "state_path": state_path,
        "interval_sec": max(_safe_int(interval_sec), 300),
        "wiki_open_lint_count": wiki_open_lint_count,
        "wiki_stale_page_count": wiki_stale_page_count,
        "wiki_repair_queue_open_count": wiki_repair_queue_open_count,
        "wiki_repair_queue_resolved_count": wiki_repair_queue_resolved_count,
        "repair_queue": repair_queue,
        "repair_pressure": repair_pressure,
        "repair_health": repair_health,
        "wiki_last_selection_at": wiki_last_selection_at,
        "wiki_last_repair_at": wiki_last_repair_at,
        "wiki_prompt_pressure": prompt_pressure,
        "requested_symbol_coverage": requested_symbol_coverage,
        "research_coverage": research_coverage,
        "application": application,
        "v3": v3,
        "active_read_mode": stored_status["active_read_mode"],
        "configured_read_mode": stored_status["configured_read_mode"],
        "stored_read_mode": stored_status["stored_read_mode"],
        "read_mode_mismatch": stored_status["read_mode_mismatch"],
        "publication_age_sec": stored_status["publication_age_sec"],
        "index_rebuild": stored_status["index_rebuild"],
        "scope_health_by_venue": stored_status["scope_health_by_venue"],
        "comparison_count_by_venue": stored_status[
            "comparison_count_by_venue"
        ],
        "eligibility_by_venue": stored_status["eligibility_by_venue"],
        "blockers": blockers,
        "warnings": list(
            dict.fromkeys(
                [*stored_status["warnings"], *readiness_signals["warnings"]]
            )
        ),
        "advisories": list(
            dict.fromkeys(
                [*stored_status["advisories"], *readiness_signals["advisories"]]
            )
        ),
        "status_endpoint": "/api/wiki/status",
        "rebuild_endpoint": "/api/wiki/rebuild",
        "lint_endpoint": "/api/wiki/lint",
    }


def _wiki_requested_symbol_coverage(
    status: dict[str, Any],
    latest_selection: dict[str, Any],
) -> dict[str, Any]:
    explicit = (
        status.get("requested_symbol_coverage")
        if isinstance(status.get("requested_symbol_coverage"), dict)
        else status.get("wiki_requested_symbol_coverage")
        if isinstance(status.get("wiki_requested_symbol_coverage"), dict)
        else {}
    )
    budget_report = (
        explicit
        if isinstance(explicit, dict) and explicit
        else latest_selection.get("budget_report")
        if isinstance(latest_selection.get("budget_report"), dict)
        else status.get("budget_report")
        if isinstance(status.get("budget_report"), dict)
        else {}
    )
    coverage = {
        "requested_count": _safe_int(
            budget_report.get("requested_count")
            if budget_report.get("requested_count") is not None
            else budget_report.get("requested_symbol_count")
        ),
        "available_summary_count": _safe_int(
            budget_report.get("available_summary_count")
            if budget_report.get("available_summary_count") is not None
            else budget_report.get("requested_symbol_available_summary_count")
        ),
        "available_summary_symbols": _csv_list(
            budget_report.get("available_summary_symbols")
            if budget_report.get("available_summary_symbols") is not None
            else budget_report.get("requested_symbol_available_summary_symbols")
        ),
        "missing_summary_count": _safe_int(
            budget_report.get("missing_summary_count")
            if budget_report.get("missing_summary_count") is not None
            else budget_report.get("requested_symbol_missing_summary_count")
        ),
        "missing_summary_symbols": _csv_list(
            budget_report.get("missing_summary_symbols")
            if budget_report.get("missing_summary_symbols") is not None
            else budget_report.get("requested_symbol_missing_summary_symbols")
        ),
        "prompt_omitted_count": _safe_int(
            budget_report.get("prompt_omitted_count")
            if budget_report.get("prompt_omitted_count") is not None
            else budget_report.get("requested_symbol_prompt_omitted_count")
        ),
        "prompt_omitted_symbols": _csv_list(
            budget_report.get("prompt_omitted_symbols")
            if budget_report.get("prompt_omitted_symbols") is not None
            else budget_report.get("requested_symbol_prompt_omitted_symbols")
        ),
    }
    degraded_count = _safe_int(
        budget_report.get("degraded_summary_count")
        if budget_report.get("degraded_summary_count") is not None
        else budget_report.get("requested_symbol_degraded_summary_count")
    )
    degraded_symbols = _csv_list(
        budget_report.get("degraded_summary_symbols")
        if budget_report.get("degraded_summary_symbols") is not None
        else budget_report.get("requested_symbol_degraded_summary_symbols")
    )
    degraded_reasons = _wiki_degraded_summary_reasons(
        budget_report.get("degraded_summary_reasons")
        if budget_report.get("degraded_summary_reasons") is not None
        else budget_report.get("requested_symbol_degraded_summary_reasons")
    )
    if degraded_count > 0 or degraded_symbols or degraded_reasons:
        coverage["degraded_summary_count"] = degraded_count or len(
            degraded_symbols
        )
        coverage["degraded_summary_symbols"] = degraded_symbols
        if degraded_reasons:
            coverage["degraded_summary_reasons"] = degraded_reasons
    return coverage


def _wiki_degraded_summary_reasons(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:16]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        row: dict[str, Any] = {"symbol": symbol}
        for key in ("freshness", "quality_status"):
            text = str(item.get(key) or "").strip()
            if text:
                row[key] = _short_text(text, limit=80)
        warnings = [
            _short_text(warning, limit=120)
            for warning in list(item.get("quality_warnings") or [])[:6]
            if str(warning).strip()
        ]
        if warnings:
            row["quality_warnings"] = warnings
        rows.append(row)
    return rows


def _count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    rows = {
        str(key).strip(): _safe_int(count)
        for key, count in value.items()
        if str(key).strip() and _safe_int(count) > 0
    }
    return {
        key: rows[key]
        for key in sorted(rows, key=lambda item: (-rows[item], item))
    }


def _wiki_repair_pressure(repair_queue: Any) -> dict[str, Any]:
    queue = repair_queue if isinstance(repair_queue, dict) else {}
    open_by_action_type = _count_map(queue.get("open_by_action_type"))
    open_by_warning = _count_map(queue.get("open_by_warning"))
    open_symbols = _csv_list(queue.get("open_symbols"))[:64]
    return {
        "open_count": _safe_int(queue.get("open_count")),
        "resolved_count": _safe_int(queue.get("resolved_count")),
        "open_symbol_count": len(open_symbols),
        "open_symbols": open_symbols,
        "primary_action_type": next(iter(open_by_action_type), ""),
        "primary_warning": next(iter(open_by_warning), ""),
        "open_by_action_type": open_by_action_type,
        "open_by_warning": open_by_warning,
    }


def _wiki_prompt_pressure(
    status: dict[str, Any],
    latest_selection: dict[str, Any],
) -> dict[str, Any]:
    explicit = status.get("wiki_prompt_pressure") or status.get("prompt_pressure")
    if isinstance(explicit, dict):
        char_count = _safe_int(explicit.get("char_count"))
        max_chars = _safe_int(explicit.get("max_chars"))
    else:
        char_count = _safe_int(
            latest_selection.get("char_count")
            if latest_selection.get("char_count") is not None
            else latest_selection.get("total_chars")
        )
        max_chars = _safe_int(
            latest_selection.get("max_chars")
            if latest_selection.get("max_chars") is not None
            else latest_selection.get("budget")
        )
    ratio = round(char_count / max_chars, 4) if max_chars > 0 else 0.0
    return {
        "char_count": char_count,
        "max_chars": max_chars,
        "ratio": ratio,
    }


def _jue_wiki_runner_active(runner: dict[str, Any]) -> bool:
    if "effective_alive" in runner:
        return bool(runner.get("effective_alive"))
    if "direct_alive" in runner:
        return bool(runner.get("direct_alive"))
    return bool(runner.get("alive"))


def _jue_wiki_readiness_signals(
    *,
    enabled: bool,
    runner: dict[str, Any],
    wiki_open_lint_count: int,
    wiki_stale_page_count: int,
    active_page_count: int,
    prompt_pressure: dict[str, Any],
    application: dict[str, Any] | None = None,
    requested_symbol_coverage: dict[str, Any] | None = None,
    repair_pressure: dict[str, Any] | None = None,
    repair_health: dict[str, Any] | None = None,
    repair_lanes: dict[str, Any] | None = None,
    research_coverage: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    warnings: list[str] = []
    advisories: list[str] = []
    if bool(enabled) and not _jue_wiki_runner_active(runner):
        warnings.append("jue_wiki_runner_stopped")
    if wiki_open_lint_count > 20:
        warnings.append("jue_wiki_lint_findings_open")
    if active_page_count > 0 and wiki_stale_page_count / active_page_count > 0.3:
        warnings.append("jue_wiki_stale_pages_high")
    if (
        _safe_float(prompt_pressure.get("ratio")) > 0.90
        and _safe_int(prompt_pressure.get("max_chars")) >= 100_000
    ):
        warnings.append("jue_wiki_prompt_pressure_high")
    app = application or {}
    effectiveness_count = _safe_int(app.get("effectiveness_count"))
    degraded_count = _safe_int(app.get("degraded_count"))
    degraded_ratio = degraded_count / effectiveness_count if effectiveness_count > 0 else 0.0
    if degraded_count > 10 and degraded_ratio >= 0.5:
        warnings.append("jue_wiki_effectiveness_degraded_high")
    health = repair_health or {}
    warnings.extend(
        str(signal).strip()
        for signal in list(health.get("warning_signals") or [])
        if str(signal).strip()
    )
    advisories.extend(
        str(signal).strip()
        for signal in list(health.get("advisory_signals") or [])
        if str(signal).strip()
    )
    lanes = repair_lanes or {}
    for lane_name in ("evidence", "strategy"):
        lane = lanes.get(lane_name) if isinstance(lanes.get(lane_name), dict) else {}
        lane_health = (
            lane.get("repair_health")
            if isinstance(lane.get("repair_health"), dict)
            else {}
        )
        for signal in list(lane_health.get("warning_signals") or []):
            clean_signal = str(signal).strip()
            if not clean_signal:
                continue
            advisories.append(
                clean_signal.replace(
                    "jue_wiki_repair_",
                    f"jue_wiki_{lane_name}_repair_",
                    1,
                )
            )
    pressure = repair_pressure or {}
    if _safe_int(pressure.get("open_count")) > 0:
        advisories.append("jue_wiki_repair_queue_open")
    pressure_warnings = (
        pressure.get("open_by_warning")
        if isinstance(pressure.get("open_by_warning"), dict)
        else {}
    )
    if (
        _safe_int(pressure_warnings.get("requested_symbol_summary_missing")) > 0
        or _safe_int(pressure_warnings.get("requested_symbol_summary_degraded")) > 0
    ):
        advisories.append("jue_wiki_requested_symbol_repair_pressure_open")
    if _safe_int(pressure_warnings.get("financials_missing")) > 0:
        advisories.append("jue_wiki_financials_repair_pressure_open")
    coverage = requested_symbol_coverage or {}
    if _safe_int(coverage.get("missing_summary_count")) > 0:
        target = (
            advisories
            if str(health.get("status") or "") == "progressing"
            else warnings
        )
        target.append("jue_wiki_requested_symbol_summaries_missing")
    if _safe_int(coverage.get("prompt_omitted_count")) > 0:
        advisories.append("jue_wiki_requested_symbol_summaries_prompt_omitted")
    if _safe_int(coverage.get("degraded_summary_count")) > 0:
        advisories.append("jue_wiki_requested_symbol_summaries_degraded")
    research = research_coverage or {}
    if _safe_int(research.get("warning_count")) > 0:
        warnings.append("jue_wiki_research_coverage_unhealthy")
    return {
        "warnings": list(dict.fromkeys(warnings)),
        "advisories": list(dict.fromkeys(advisories)),
    }


def merge_section_readiness_signals(
    readiness_signals: dict[str, Any],
    section_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(readiness_signals)
    blockers = list(payload.get("blockers") or [])
    warnings = list(payload.get("warnings") or [])
    advisories = list(payload.get("advisories") or [])

    def add_unique(target: list[str], value: Any) -> None:
        clean = str(value or "").strip()
        if clean and clean not in target:
            target.append(clean)

    for blocker in list(section_payload.get("blockers") or []):
        add_unique(blockers, blocker)
    for warning in list(section_payload.get("warnings") or []):
        add_unique(warnings, warning)
    for advisory in list(section_payload.get("advisories") or []):
        add_unique(advisories, advisory)

    payload["blockers"] = blockers
    payload["warnings"] = warnings
    payload["advisories"] = advisories
    payload.update(
        _split_readiness_actions(
            blockers=blockers,
            warnings=warnings,
            advisories=advisories,
            stale_processes=list(payload.get("stale_processes") or []),
            missing_processes=list(payload.get("missing_processes") or []),
            duplicate_processes=list(payload.get("duplicate_processes") or []),
        )
    )
    if blockers:
        payload["status"] = "red"
    elif warnings:
        payload["status"] = "yellow"
    else:
        payload["status"] = "green"
    return payload


def build_ops_remediation_actions(
    *,
    blockers: list[str],
    warnings: list[str],
    stale_processes: list[str],
    missing_processes: list[str],
    duplicate_processes: list[str] | None = None,
) -> list[dict[str, Any]]:
    signals = [*blockers, *warnings]
    signal_set = set(signals)
    actions: list[dict[str, Any]] = []

    def add_action(
        action_id: str,
        *,
        label: str,
        detail: str,
        severity: str,
        endpoint: str = "",
        method: str = "GET",
        matched_signals: list[str] | None = None,
        request_payload: dict[str, Any] | None = None,
        requires_confirmation: bool = False,
        follow_up_actions: list[dict[str, Any]] | None = None,
    ) -> None:
        if any(row.get("id") == action_id for row in actions):
            return
        action = {
            "id": action_id,
            "label": label,
            "detail": detail,
            "severity": severity,
            "endpoint": endpoint,
            "method": method,
            "signals": matched_signals or [],
        }
        if request_payload:
            action["request_payload"] = request_payload
        if requires_confirmation:
            action["requires_confirmation"] = True
        if follow_up_actions:
            action["follow_up_actions"] = follow_up_actions
        actions.append(action)

    stale_validation_signals = [
        signal for signal in signals if signal.startswith("trading_validation_stale")
    ]
    if stale_validation_signals:
        add_action(
            "refresh_trading_validation",
            label="19개 검증 즉시 재실행",
            detail="검증 결과가 오래되어 Live Authority가 최신 성과를 반영하지 못합니다.",
            severity="warn",
            endpoint="/api/trading/validation/run-once",
            method="POST",
            matched_signals=stale_validation_signals,
        )

    revision_validation_signals = [
        signal
        for signal in signals
        if signal.startswith("trading_validation_revision_mismatch")
    ]
    if revision_validation_signals:
        add_action(
            "refresh_trading_validation_revision",
            label="현재 전략 revision 검증 생성",
            detail=(
                "현재 전략 revision의 검증 run이 없어 이전 revision 결과를 표시 중입니다. "
                "최신 전략 기준으로 19개 검증을 다시 실행합니다."
            ),
            severity="warn",
            endpoint="/api/trading/validation/run-once",
            method="POST",
            matched_signals=revision_validation_signals,
        )

    weak_validation_signals = [
        signal
        for signal in signals
        if signal.startswith("trading_validation_blocked")
        or signal.startswith("trading_validation_incomplete")
    ]
    if weak_validation_signals:
        add_action(
            "review_trading_validation_failures",
            label="검증 실패 항목 확인",
            detail="19개 자동매매 검증 중 막힌 항목을 보고 sizing, 진입 방식, 후보군을 조정합니다.",
            severity="blocker",
            endpoint="/api/live/authority",
            method="GET",
            matched_signals=weak_validation_signals,
        )

    strategy_blocked_signals = [
        signal
        for signal in signals
        if signal.startswith("trading_validation_strategy_blocked")
    ]
    if strategy_blocked_signals:
        add_action(
            "review_strategy_validation_blocks",
            label="전략 검증 차단 확인",
            detail=(
                "성과 검증이 특정 venue의 공격 진입이나 스케일업을 막고 있습니다. "
                "시스템 장애가 아니라 live authority 전략 게이트이므로 병목, 비용, "
                "승률, risk-of-ruin을 보고 운용 강도를 조정합니다."
            ),
            severity="warn",
            endpoint="/api/trading/validation/status",
            method="GET",
            matched_signals=strategy_blocked_signals,
        )

    diagnostic_validation_signals = [
        signal
        for signal in signals
        if signal.startswith("trading_validation_diagnostic_failures")
    ]
    if diagnostic_validation_signals:
        add_action(
            "review_trading_validation_diagnostics",
            label="검증 진단 항목 개선",
            detail=(
                "core gate는 통과했지만 진단 fail이 남아 있습니다. "
                "lane별 비용, Kelly, Monte Carlo, PF, 회복력 항목을 줄여 "
                "실거래 권한 확대 조건을 복구합니다."
            ),
            severity="warn",
            endpoint="/api/trading/validation/status",
            method="GET",
            matched_signals=diagnostic_validation_signals,
        )

    lane_authority_signals = [
        signal
        for signal in signals
        if signal.startswith("trading_validation_lane_authority_reduced")
    ]
    if lane_authority_signals:
        add_action(
            "review_lane_authority_reductions",
            label="Lane 탐색/확대 제한 확인",
            detail=(
                "성과·비용·진입품질·검증수리 조건 때문에 확대가 제한된 lane을 확인하고, "
                "대기진입/probe/비용수리 상태가 적극적 표본 축적으로 이어지는지 점검합니다."
            ),
            severity="warn",
            endpoint="/api/trading/validation/status",
            method="GET",
            matched_signals=lane_authority_signals,
        )

    binance_recovery_signals = [
        signal
        for signal in signals
        if signal
        in {
            "binance_activity_pressure_open",
            "binance_manager_contract_replay_recovered",
            "binance_manager_contract_replay_current_error",
        }
    ]
    if binance_recovery_signals:
        add_action(
            "review_binance_activity_pressure",
            label="Binance 활동 압력 확인",
            detail=(
                "현재 코드 replay가 Binance 신규 진입 공백과 후보 심볼을 감지했습니다. "
                "상태 payload에서 후보, 공백 시간, manager 오류, runner stale 여부를 확인합니다."
            ),
            severity="warn",
            endpoint="/api/binance/blocks/status",
            method="GET",
            matched_signals=binance_recovery_signals,
        )
        add_action(
            "refresh_binance_crypto_research_context",
            label="Binance 후보 리서치 갱신",
            detail=(
                "Binance 활동 공백은 현재 후보가 research_only, pattern prior, "
                "confidence/live-authority 근거에서 막혔을 수 있습니다. "
                "기본 crypto universe 리서치를 다시 실행한 뒤 상태 payload의 후보별 "
                "수리 액션을 확인합니다."
            ),
            severity="warn",
            endpoint="/api/crypto/research/run-once",
            method="POST",
            matched_signals=[
                signal
                for signal in binance_recovery_signals
                if signal == "binance_activity_pressure_open"
            ]
            or binance_recovery_signals,
        )
        add_action(
            "restart_binance_recovery_runners",
            label="Binance 복구 러너 재시작",
            detail=(
                "Binance runner와 watchdog만 새 코드로 재기동해 활동 압력 복구 루프를 "
                "적용합니다. Live crypto execution이 켜진 경우 명시 확인이 필요합니다."
            ),
            severity="warn",
            endpoint="/api/ops/restart",
            method="POST",
            matched_signals=binance_recovery_signals,
            request_payload={"keys": ["binance_block_trader", "watchdog"]},
            requires_confirmation=True,
            follow_up_actions=[
                {
                    "id": "check_binance_status_after_restart",
                    "label": "Binance 상태 재확인",
                    "endpoint": "/api/binance/blocks/status",
                    "method": "GET",
                },
                {
                    "id": "run_binance_manager_after_restart",
                    "label": "Binance 매니저 즉시 실행",
                    "endpoint": "/api/binance/blocks/manager/run-once",
                    "method": "POST",
                    "request_payload": {"confirm_live_manager_run": True},
                    "requires_confirmation": True,
                },
                {
                    "id": "run_binance_executor_after_manager",
                    "label": "Binance 실행 틱 확인 실행",
                    "endpoint": "/api/binance/blocks/executor/tick",
                    "method": "POST",
                    "request_payload": {"confirm_live_executor_tick": True},
                    "requires_confirmation": True,
                },
            ],
        )

    jue_wiki_action_reference_signals = [
        signal
        for signal in signals
        if signal.endswith("_jue_wiki_action_reference_gap_unresolved")
    ]
    if jue_wiki_action_reference_signals:
        add_action(
            "run_jue_wiki_action_reference_reflection",
            label="쥬 위키 근거 누락 반성 실행",
            detail=(
                "KIS/Binance 매니저가 위키 기억을 판단 근거로 해소하지 못했습니다. "
                "메모리 반성 루프를 즉시 실행해 다음 판단에 위키 근거 또는 미사용 사유를 강제합니다."
            ),
            severity="warn",
            endpoint="/api/memory/reflections/run-due",
            method="POST",
            matched_signals=jue_wiki_action_reference_signals,
        )

    restart_signals = [
        signal
        for signal in signals
        if signal == "restart_required"
        or signal.endswith("_runner_stopped")
        or signal.endswith("_runner_duplicated")
    ]
    duplicate_processes = list(duplicate_processes or [])
    if restart_signals or stale_processes or missing_processes or duplicate_processes:
        process_names = [*stale_processes, *missing_processes, *duplicate_processes]
        process_detail = (
            f"대상: {', '.join(process_names[:6])}"
            if process_names
            else "오래된 코드로 떠 있는 러너를 새 버전으로 재기동합니다."
        )
        add_action(
            "restart_stale_runners",
            label="러너 재시작",
            detail=process_detail,
            severity="warn",
            endpoint="/api/ops/restart",
            method="POST",
            matched_signals=restart_signals,
        )

    if "memory_not_seeded" in signal_set:
        add_action(
            "seed_investment_memory",
            label="메모리 seed 생성",
            detail="현재 블록, 리서치, 정책을 쥬의 초기 운용 기억으로 압축합니다.",
            severity="warn",
            endpoint="/api/memory/seed-current",
            method="POST",
            matched_signals=["memory_not_seeded"],
        )

    runtime_session_signals = [
        signal
        for signal in ("runtime_sessions_missing", "runtime_sessions_invalid")
        if signal in signal_set
    ]
    if runtime_session_signals:
        add_action(
            "review_runtime_sessions_config",
            label="Runtime 세션 설정 확인",
            detail=(
                "runtime state writer가 safe_default_no_orders로 떠 있습니다. "
                "설정된 세션 파일 경로가 없거나 깨졌는지 확인한 뒤 runtime을 재시작합니다."
            ),
            severity="warn",
            endpoint="/api/runtime/storage",
            method="GET",
            matched_signals=runtime_session_signals,
        )

    if "llm_error_rate_high" in signal_set:
        add_action(
            "review_llm_usage_errors",
            label="LLM 오류율 점검",
            detail="최근 LLM 실패 컴포넌트를 확인해 토큰, 모델, timeout, 프롬프트 크기를 조정합니다.",
            severity="warn",
            endpoint="/api/llm/usage",
            method="GET",
            matched_signals=["llm_error_rate_high"],
        )

    disk_signals = [
        signal
        for signal in ("disk_space_low", "disk_space_critical", "disk_space_status_error")
        if signal in signal_set
    ]
    if disk_signals:
        add_action(
            "cleanup_runtime_storage",
            label="Runtime 저장소 정리",
            detail="디스크 여유 공간이 부족하면 native LLM, SQLite 기록, 러너 상태 갱신이 실패할 수 있습니다.",
            severity="blocker" if "disk_space_critical" in disk_signals else "warn",
            endpoint="/api/runtime/storage/cleanup?dry_run=true",
            method="POST",
            matched_signals=disk_signals,
        )

    if "research_runner_state_stale" in signal_set or "reports_db_stale" in signal_set:
        matched = [
            signal
            for signal in ("research_runner_state_stale", "reports_db_stale")
            if signal in signal_set
        ]
        add_action(
            "refresh_research_pipeline",
            label="리서치 파이프라인 점검",
            detail="리포트/RAG/수집 상태가 오래되었는지 확인하고 최신 판단 근거를 복구합니다.",
            severity="warn",
            endpoint="/api/reports/status",
            method="GET",
            matched_signals=matched,
        )

    return actions
