from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


RunnerStatusFn = Callable[[str], dict[str, Any]]
CodeStalenessFn = Callable[..., dict[str, Any]]


def runner_status_with_cover(
    status: dict[str, Any],
    *,
    covered_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(status)
    direct_alive = bool(payload.get("alive"))
    payload["direct_alive"] = direct_alive
    payload["effective_alive"] = direct_alive
    if not direct_alive and covered_by and bool(covered_by.get("alive")):
        payload["status"] = "covered"
        payload["effective_alive"] = True
        payload["covered_by"] = str(covered_by.get("key") or "")
        payload["covered_by_label"] = str(covered_by.get("label") or "")
    return payload


def light_runner_process_status(
    key: str,
    status_fn: Callable[..., dict[str, Any]],
    *,
    scan_alive_matches: bool = False,
) -> dict[str, Any]:
    status = status_fn(key, include_matches=False)
    if bool(status.get("alive")) and not scan_alive_matches:
        return status
    return status_fn(key)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_market_judgment_readiness_status(engine: Any) -> dict[str, Any]:
    try:
        latest_rows = engine.repository.recent_runs(limit=1)
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}
    latest = latest_rows[0] if latest_rows else {}
    config = getattr(engine, "config", None)
    return {
        "status": "ok",
        "db_path": str(getattr(engine.repository, "path", "")),
        "latest_run_at": str(latest.get("run_at") or ""),
        "latest_run_status": str(latest.get("status") or "missing"),
        "latest_run_mode": str(latest.get("mode") or ""),
        "config": {
            "quote_interval_sec": _safe_int(
                getattr(config, "quote_interval_sec", 0)
            ),
            "judge_interval_sec": _safe_int(
                getattr(config, "judge_interval_sec", 0)
            ),
            "max_symbols": _safe_int(getattr(config, "max_symbols", 0)),
            "llm_max_symbols": _safe_int(getattr(config, "llm_max_symbols", 0)),
            "use_naver_fallback": bool(getattr(config, "use_naver_fallback", False)),
        },
    }


def build_market_pulse_readiness_status(service: Any) -> dict[str, Any]:
    try:
        repository = getattr(service, "repository")
        latest = repository.latest()
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}
    payload = {
        "status": "ok",
        "db_path": str(getattr(repository, "path", "")),
        "latest": latest if latest.get("status") != "missing" else {},
    }
    payload["enabled"] = bool(
        getattr(getattr(service, "config", None), "enabled", True)
    )
    return payload


def _compact_codex_native_status(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    compact = {
        key: value
        for key, value in payload.items()
        if key
        in {
            "status",
            "mode",
            "model",
            "reasoning_effort",
            "thread_mode",
            "thread_db_path",
            "compact_after_turns",
            "read_turns",
            "developer_instructions_enabled",
            "check_intervals",
        }
        and value not in (None, "", [], {})
    }
    account = payload.get("latest_account_check") or payload.get("account")
    if isinstance(account, dict):
        compact_account = {
            key: value
            for key, value in account.items()
            if key in {"status", "account_label", "error_message", "checked_at"}
            and value not in (None, "", [], {})
        }
        if compact_account:
            compact["latest_account_check"] = compact_account
    models = payload.get("models")
    if isinstance(models, list):
        successful_model_turns = _codex_native_successful_model_turns(
            payload.get("recent_turns")
        )
        compact["models"] = [
            _compact_codex_native_model_row(row, successful_model_turns)
            for row in models[:12]
            if isinstance(row, dict)
        ]
    components = payload.get("components")
    if isinstance(components, list):
        compact["components"] = [
            _compact_codex_native_row(
                row,
                keys={
                    "component",
                    "workflow",
                    "mode",
                    "model",
                    "reasoning_effort",
                    "usage_component",
                },
            )
            for row in components[:24]
            if isinstance(row, dict)
        ]
    last_error = payload.get("last_error")
    if isinstance(last_error, dict):
        compact_error = _compact_codex_native_row(
            last_error,
            keys={"component", "model", "error_message", "created_at", "checked_at"},
        )
        if compact_error:
            compact["last_error"] = compact_error
    last_recovered_error = payload.get("last_recovered_error")
    if isinstance(last_recovered_error, dict):
        compact_recovered_error = _compact_codex_native_row(
            last_recovered_error,
            keys={
                "component",
                "model",
                "error_message",
                "created_at",
                "checked_at",
                "recovered_at",
                "recovery_reason",
            },
        )
        if compact_recovered_error:
            compact["last_recovered_error"] = compact_recovered_error
    events = payload.get("recent_runtime_events")
    if isinstance(events, list):
        compact_events = [
            _compact_codex_native_row(
                row,
                keys={
                    "component",
                    "event",
                    "event_type",
                    "status",
                    "error_message",
                    "created_at",
                    "checked_at",
                    "recovered_at",
                    "recovery_reason",
                },
            )
            for row in events[:5]
            if isinstance(row, dict)
        ]
        if compact_events:
            compact["recent_runtime_events"] = compact_events
    return compact


def _codex_native_successful_model_turns(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    latest: dict[str, str] = {}
    for row in value:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() not in {"ok", "success"}:
            continue
        model = str(row.get("model") or "").strip()
        finished_at = str(row.get("finished_at") or row.get("created_at") or "").strip()
        if not model or not finished_at:
            continue
        if finished_at > latest.get(model, ""):
            latest[model] = finished_at
    return latest


def _compact_codex_native_model_row(
    row: dict[str, Any],
    successful_model_turns: dict[str, str],
) -> dict[str, Any]:
    compact = _compact_codex_native_row(
        row,
        keys={"model", "available", "error_message", "checked_at"},
    )
    model = str(compact.get("model") or "").strip()
    success_at = successful_model_turns.get(model, "")
    if not success_at:
        return compact
    compact["available"] = True
    compact["availability_source"] = "recent_successful_turn"
    compact["last_successful_turn_at"] = success_at
    compact.pop("error_message", None)
    return compact


def _compact_codex_native_row(
    row: dict[str, Any],
    *,
    keys: set[str],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in keys and value not in (None, "", [], {})
    }


def _compact_process_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        in {
            "key",
            "label",
            "status",
            "alive",
            "pid",
            "started_at",
            "pid_file_pid",
            "pid_file_status",
            "matched_count",
            "direct_alive",
            "effective_alive",
            "covered_by",
            "covered_by_label",
            "code_mtime",
            "stale_process",
        }
        and value not in (None, "", [], {})
    }


def _compact_processes(
    processes: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        key: _compact_process_row(row)
        for key, row in processes.items()
        if isinstance(row, dict)
    }


def _compact_llm_usage_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    compact = {
        key: value
        for key, value in payload.items()
        if key in {"enabled", "db_path"} and value not in (None, "", [], {})
    }
    today = payload.get("today")
    if isinstance(today, dict):
        compact_today = _compact_llm_usage_period(today)
        if compact_today:
            compact["today"] = compact_today
    return compact


def _compact_llm_usage_period(period: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in period.items()
        if key in {"status", "period", "trading_day", "start_day", "end_day"}
        and value not in (None, "", [], {})
    }
    total = period.get("total")
    if isinstance(total, dict):
        compact["total"] = {
            key: value
            for key, value in total.items()
            if key
            in {
                "call_count",
                "ok_count",
                "error_count",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "estimated_token_count",
                "missing_token_count",
            }
            and value not in (None, "", [], {})
        }
    rows = period.get("by_component")
    if isinstance(rows, list):
        compact["by_component"] = [
            _compact_llm_usage_component(row)
            for row in rows[:6]
            if isinstance(row, dict)
        ]
    return compact


def _compact_llm_usage_component(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        in {
            "component",
            "label",
            "category",
            "call_count",
            "ok_count",
            "error_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "latest_status",
            "latest_started_at",
            "latest_error_at",
            "ok_after_latest_error_count",
        }
        and value not in (None, "", [], {})
    }


def build_ops_readiness_payload(
    *,
    readiness_signals: dict[str, Any],
    checked_at: str,
    processes: dict[str, dict[str, Any]],
    admin_token_configured: bool,
    kis_ready: bool,
    kis_rate_limit: dict[str, Any],
    llm_ready: bool,
    disk_space: dict[str, Any],
    llm: dict[str, Any],
    llm_usage: dict[str, Any],
    semantic_checks: dict[str, Any],
    codex_native: dict[str, Any],
    telegram_ready: bool,
    live_trading_enabled: bool,
    paper_mode: bool,
    kill_switch: dict[str, Any],
    sections: dict[str, dict[str, Any]],
    next_market_open_at: str,
) -> dict[str, Any]:
    return {
        "status": str(readiness_signals.get("status") or "unknown"),
        "checked_at": str(checked_at),
        "blockers": list(readiness_signals.get("blockers") or []),
        "warnings": list(readiness_signals.get("warnings") or []),
        "advisories": list(readiness_signals.get("advisories") or []),
        "trading_validation_advisories": list(
            readiness_signals.get("trading_validation_advisories") or []
        ),
        "advisory_details": list(readiness_signals.get("advisory_details") or []),
        "trading_validation_advisory_details": list(
            readiness_signals.get("trading_validation_advisory_details") or []
        ),
        "remediation_actions": list(
            readiness_signals.get("remediation_actions") or []
        ),
        "processes": _compact_processes(processes),
        "stale_processes": list(readiness_signals.get("stale_processes") or []),
        "missing_processes": list(readiness_signals.get("missing_processes") or []),
        "duplicate_processes": list(
            readiness_signals.get("duplicate_processes") or []
        ),
        "admin_token_configured": bool(admin_token_configured),
        "kis_ready": bool(kis_ready),
        "kis_rate_limit": kis_rate_limit,
        "llm_ready": bool(llm_ready),
        "disk_space": disk_space,
        "llm": llm,
        "llm_usage": _compact_llm_usage_summary(llm_usage),
        "semantic_checks": semantic_checks,
        "codex_native": _compact_codex_native_status(codex_native),
        "telegram_ready": bool(telegram_ready),
        "live_trading_enabled": bool(live_trading_enabled),
        "paper_mode": bool(paper_mode),
        "kill_switch": kill_switch,
        "memory": sections.get("memory", {}),
        "reports": sections.get("reports", {}),
        "live_evaluator": sections.get("live_evaluator", {}),
        "trading_validation": sections.get("trading_validation", {}),
        "watchdog": sections.get("watchdog", {}),
        "market_judge": sections.get("market_judge", {}),
        "market_pulse": sections.get("market_pulse", {}),
        "kis_block_trader": sections.get("kis_block_trader", {}),
        "binance_block_trader": sections.get("binance_block_trader", {}),
        "crypto_market_research": sections.get("crypto_market_research", {}),
        "crypto_alpha": sections.get("crypto_alpha", {}),
        "next_market_open_at": str(next_market_open_at),
    }


def build_core_runner_processes(
    *,
    base: Path,
    runner_status: RunnerStatusFn,
    apply_code_staleness: CodeStalenessFn,
) -> dict[str, dict[str, Any]]:
    def backend_python_paths(*parts: str) -> list[Path]:
        target = base.joinpath(*parts)
        if target.is_file():
            return [target]
        if not target.is_dir():
            return []
        return sorted(
            path
            for path in target.rglob("*.py")
            if "__pycache__" not in path.parts
        )

    control_code_paths = [
        *backend_python_paths("main.py"),
        *backend_python_paths("config.py"),
        *backend_python_paths("api"),
        *backend_python_paths("services"),
    ]
    process_map = {
        "control": (
            runner_status("control"),
            control_code_paths,
        ),
        "runtime": (
            runner_status("runtime"),
            [base / "runtime" / "runner.py"],
        ),
        "intelligence": (
            runner_status("intelligence"),
            [
                base / "runtime" / "intelligence_runner.py",
                base / "runtime" / "research_runner.py",
                base / "services" / "research_pipeline.py",
            ],
        ),
        "research": (
            runner_status("research"),
            [
                base / "runtime" / "research_runner.py",
                base / "services" / "research_pipeline.py",
                base / "services" / "naver_reports.py",
            ],
        ),
        "naver_reports": (
            runner_status("naver_reports"),
            [
                base / "runtime" / "naver_reports_runner.py",
                base / "services" / "naver_reports.py",
            ],
        ),
        "strategy_insights": (
            runner_status("strategy_insights"),
            [
                base / "runtime" / "strategy_insights_runner.py",
                base / "services" / "intelligence.py",
                base / "services" / "strategy_intelligence.py",
            ],
        ),
        "kis_block_trader": (
            runner_status("kis_block_trader"),
            [
                base / "runtime" / "kis_block_trader_runner.py",
                base / "services" / "kis_block_trader.py",
            ],
        ),
        "binance_block_trader": (
            runner_status("binance_block_trader"),
            [
                base / "runtime" / "binance_block_trader_runner.py",
                base / "services" / "binance_block_trader.py",
            ],
        ),
        "crypto_market_research": (
            runner_status("crypto_market_research"),
            [
                base / "runtime" / "crypto_market_research_runner.py",
                base / "services" / "crypto_market_research.py",
            ],
        ),
        "crypto_pattern_lab": (
            runner_status("crypto_pattern_lab"),
            [
                base / "runtime" / "crypto_pattern_lab_runner.py",
                base / "services" / "crypto_pattern_lab.py",
            ],
        ),
        "crypto_alpha": (
            runner_status("crypto_alpha"),
            [
                base / "runtime" / "crypto_alpha_runner.py",
                base / "services" / "crypto_alpha.py",
            ],
        ),
        "investment_memory": (
            runner_status("investment_memory"),
            [
                base / "runtime" / "investment_memory_runner.py",
                base / "services" / "investment_memory.py",
            ],
        ),
        "live_evaluator": (
            runner_status("live_evaluator"),
            [
                base / "runtime" / "live_evaluator_runner.py",
                base / "services" / "live_authority.py",
                base / "services" / "live_edge.py",
                base / "services" / "live_performance.py",
            ],
        ),
        "market_judge": (
            runner_status("market_judge"),
            [
                base / "runtime" / "market_judge_runner.py",
                base / "services" / "market_judgment.py",
            ],
        ),
        "market_pulse": (
            runner_status("market_pulse"),
            [
                base / "runtime" / "market_pulse_runner.py",
                base / "services" / "market_pulse.py",
            ],
        ),
        "watchdog": (
            runner_status("watchdog"),
            [base / "runtime" / "watchdog_runner.py"],
        ),
    }
    return {
        key: apply_code_staleness(process, code_paths=paths)
        for key, (process, paths) in process_map.items()
    }
