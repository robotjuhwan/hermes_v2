from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from tradecraft.services.llm_usage import RETIRED_LLM_COMPONENTS


LLM_USAGE_COMPONENT_PROCESS_KEYS: dict[str, str] = {
    "kis_block_manager": "kis_block_trader",
    "binance_block_manager": "binance_block_trader",
    "market_judge": "market_judge",
    "investment_memory": "investment_memory",
    "crypto_market_research": "crypto_market_research",
    "crypto_alpha": "crypto_alpha",
    "research_reports": "research",
    "daily_discovery": "kis_block_trader",
    "symbol_analysis": "kis_block_trader",
}


def enrich_llm_usage_component_recovery(
    summary: dict[str, Any],
    db_path: str,
) -> dict[str, Any]:
    day = str(summary.get("trading_day") or "").strip()
    rows = (summary.get("by_component") if isinstance(summary, dict) else None) or []
    if not day or not isinstance(rows, list) or not rows:
        return summary
    try:
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            latest_rows = conn.execute(
                """
                SELECT component, status, started_at, input_chars
                FROM llm_calls
                WHERE trading_day = ?
                ORDER BY started_at DESC, id DESC
                """,
                (day,),
            ).fetchall()
    except Exception as exc:
        summary["recovery_enrichment_status"] = "error"
        summary["recovery_enrichment_error"] = f"{type(exc).__name__}: {exc}"[:500]
        return summary

    latest_by_component: dict[str, sqlite3.Row] = {}
    latest_error_at: dict[str, str] = {}
    for row in latest_rows:
        component = str(row["component"] or "")
        if not component:
            continue
        latest_by_component.setdefault(component, row)
        if str(row["status"] or "").lower() != "ok" and component not in latest_error_at:
            latest_error_at[component] = str(row["started_at"] or "")

    for row in rows:
        if not isinstance(row, dict):
            continue
        component = str(row.get("component") or "")
        latest = latest_by_component.get(component)
        if latest is not None:
            row["latest_status"] = str(latest["status"] or "")
            row["latest_started_at"] = str(latest["started_at"] or "")
            row["latest_input_chars"] = int(latest["input_chars"] or 0)
        error_at = latest_error_at.get(component, "")
        row["latest_error_at"] = error_at
        if error_at:
            try:
                with sqlite3.connect(db_path, timeout=5.0) as conn:
                    ok_after = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM llm_calls
                        WHERE trading_day = ?
                          AND component = ?
                          AND status = 'ok'
                          AND started_at > ?
                        """,
                        (day, component, error_at),
                    ).fetchone()
                row["ok_after_latest_error_count"] = int(ok_after[0] if ok_after else 0)
            except Exception:
                row["ok_after_latest_error_count"] = 0
        else:
            row["ok_after_latest_error_count"] = 0
    return summary


def build_llm_usage_status_payload(
    *,
    enabled: bool,
    db_path: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    total = summary.get("total") if isinstance(summary.get("total"), dict) else {}
    by_component = (
        summary.get("by_component")
        if isinstance(summary.get("by_component"), list)
        else []
    )
    return {
        "status": "ok",
        "enabled": bool(enabled),
        "db_path": db_path,
        "period": summary.get("period"),
        "trading_day": summary.get("trading_day"),
        "total": total,
        "by_component": by_component,
        "components": by_component,
        "today": summary,
    }


def build_llm_usage_semantic_check(
    llm_usage_payload: dict[str, Any],
    *,
    processes: Mapping[str, Mapping[str, Any]] | None = None,
    component_enabled: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    prompt_large_components: list[dict[str, Any]] = []
    prompt_near_limit_components: list[dict[str, Any]] = []
    stale_prompt_large_components: list[dict[str, Any]] = []
    recovered_prompt_large_components: list[dict[str, Any]] = []
    error_rate_components: list[dict[str, Any]] = []
    inactive_error_components: list[dict[str, Any]] = []
    recovered_error_components: list[dict[str, Any]] = []
    stale_error_components: list[dict[str, Any]] = []
    warnings: list[str] = []
    rows = (llm_usage_payload.get("today") or {}).get("by_component") or []
    for row in list(rows):
        if not isinstance(row, dict):
            continue
        call_count = int(row.get("call_count") or 0)
        error_count = int(row.get("error_count") or 0)
        max_input_chars = int(row.get("max_input_chars") or 0)
        avg_input_chars = int(row.get("avg_input_chars") or 0)
        latest_input_chars = int(row.get("latest_input_chars") or 0)
        latest_status = str(row.get("latest_status") or "").strip().lower()
        near_limit = max_input_chars >= 180_000 or avg_input_chars >= 150_000
        large_payload = {
            "component": row.get("component"),
            "max_input_chars": max_input_chars,
            "avg_input_chars": avg_input_chars,
            "latest_started_at": row.get("latest_started_at"),
            "latest_input_chars": latest_input_chars,
        }
        if near_limit:
            prompt_near_limit_components.append(large_payload)
        if max_input_chars >= 250_000 or avg_input_chars >= 190_000:
            if llm_usage_stale_after_process_restart(row, processes):
                stale_prompt_large_components.append(large_payload)
            elif latest_status == "ok" and 0 < latest_input_chars < 190_000:
                recovered_prompt_large_components.append(large_payload)
            else:
                prompt_large_components.append(large_payload)
        if call_count >= 3 and error_count >= 2 and (error_count / call_count) >= 0.10:
            component = row.get("component")
            error_payload = {
                "component": component,
                "call_count": call_count,
                "error_count": error_count,
                "error_rate": round(error_count / call_count, 4),
            }
            if not llm_usage_component_active(
                component,
                component_enabled=component_enabled,
            ):
                inactive_error_components.append(error_payload)
                continue
            if llm_usage_stale_after_process_restart(row, processes):
                stale_error_components.append(error_payload)
                continue
            ok_after_latest_error = int(row.get("ok_after_latest_error_count") or 0)
            recovery_threshold = llm_usage_recovery_success_threshold(component)
            if latest_status == "ok" and ok_after_latest_error >= recovery_threshold:
                recovered_error_components.append(
                    {
                        **error_payload,
                        "ok_after_latest_error_count": ok_after_latest_error,
                        "recovery_success_threshold": recovery_threshold,
                        "latest_error_at": row.get("latest_error_at"),
                    }
                )
                continue
            error_rate_components.append(error_payload)
    if prompt_large_components:
        warnings.append("llm_prompt_payload_large")
    if error_rate_components:
        warnings.append("llm_error_rate_high")
    return {
        "warnings": warnings,
        "check": {
            "prompt_large_components": prompt_large_components,
            "prompt_near_limit_components": prompt_near_limit_components,
            "stale_prompt_large_components": stale_prompt_large_components,
            "recovered_prompt_large_components": recovered_prompt_large_components,
            "error_rate_components": error_rate_components,
            "inactive_error_components": inactive_error_components,
            "recovered_error_components": recovered_error_components,
            "stale_error_components": stale_error_components,
        },
    }


def llm_usage_component_active(
    component: Any,
    *,
    component_enabled: Mapping[str, bool] | None = None,
) -> bool:
    name = str(component or "").strip()
    if name in RETIRED_LLM_COMPONENTS:
        return False
    if component_enabled is None:
        return True
    if name in component_enabled:
        return bool(component_enabled[name])
    return True


def llm_usage_recovery_success_threshold(component: Any) -> int:
    name = str(component or "").strip()
    if name in {"research_ask", "symbol_analysis"}:
        return 1
    return 3


def llm_usage_component_process_key(component: Any) -> str:
    name = str(component or "").strip()
    return LLM_USAGE_COMPONENT_PROCESS_KEYS.get(name, "")


def llm_usage_stale_after_process_restart(
    row: dict[str, Any],
    processes: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    if not isinstance(row, dict) or not isinstance(processes, Mapping):
        return False
    process_key = llm_usage_component_process_key(row.get("component"))
    process = processes.get(process_key) if process_key else None
    if not isinstance(process, Mapping):
        return False
    latest_started_at = _iso_to_utc(row.get("latest_started_at"))
    started_epoch = process.get("started_at_epoch")
    if latest_started_at is None or started_epoch is None:
        return False
    try:
        process_started_at = datetime.fromtimestamp(
            float(started_epoch),
            tz=timezone.utc,
        )
    except (TypeError, ValueError, OSError):
        return False
    return bool(process_started_at > latest_started_at + timedelta(seconds=1))


def _iso_to_utc(value: Any) -> datetime | None:
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
