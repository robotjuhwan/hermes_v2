from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from tradecraft.services.jue_wiki import (
    JueWikiService,
    normalize_jue_wiki_quality_status,
)
from tradecraft.services.jue_wiki_application import JueWikiApplicationService
from tradecraft.services.jue_wiki_prompt_quality import (
    canonical_jue_wiki_evidence_quality,
    canonical_jue_wiki_status_counts,
    jue_wiki_quality_status_from_evidence,
)


def _repair_priority_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    source_id = str(row.get("source_id") or "").strip()
    if source_id:
        return ("source_id", source_id)
    return (
        "fallback",
        str(row.get("page_id") or ""),
        str(row.get("priority_type") or ""),
        str(row.get("action_type") or ""),
        tuple(str(symbol) for symbol in list(row.get("symbols") or [])),
    )


def _repair_priority_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        priority_type = str(row.get("priority_type") or "unknown").strip() or "unknown"
        counts[priority_type] = counts.get(priority_type, 0) + 1
    return {
        key: count
        for key, count in sorted(
            counts.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )
    }


def _repair_priority_budget_summary(
    all_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_ids = {_repair_priority_identity(row) for row in selected_rows}
    omitted_rows = [
        row for row in all_rows if _repair_priority_identity(row) not in selected_ids
    ]
    return {
        "repair_priority_total_count": len(all_rows),
        "repair_priority_selected_count": len(selected_rows),
        "repair_priority_omitted_count": len(omitted_rows),
        "repair_priority_type_counts": _repair_priority_type_counts(all_rows),
        "repair_priority_selected_type_counts": _repair_priority_type_counts(
            selected_rows
        ),
        "repair_priority_omitted_type_counts": _repair_priority_type_counts(
            omitted_rows
        ),
        "repair_action_batches": _repair_action_batches_for_priorities(
            all_rows,
            limit=max(len(all_rows), 12),
        ),
    }


def _repair_priority_scope(row: dict[str, Any], *, default_scope: str = "") -> str:
    for key in ("decision_scope", "scope", "source_scope"):
        scope = str(row.get(key) or "").strip().lower()
        if scope:
            return scope
    page_id = str(row.get("page_id") or "").strip()
    if "." in page_id:
        return page_id.split(".", 1)[0].strip().lower()
    return str(default_scope or "").strip().lower()


def _repair_action_batches_for_priorities(
    rows: list[dict[str, Any]],
    *,
    default_scope: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        action_type = str(row.get("action_type") or "").strip()
        if not action_type:
            continue
        scope = _repair_priority_scope(row, default_scope=default_scope)
        key = (scope, action_type)
        batch = grouped.setdefault(
            key,
            {
                "scope": scope,
                "action_type": action_type,
                "count": 0,
                "symbols": set(),
                "warnings": set(),
                "warning_counts": {},
                "recommended_actions": set(),
                "priority_types": set(),
                "max_severity_score": 0.0,
            },
        )
        batch["count"] += 1
        batch["priority_types"].add(str(row.get("priority_type") or "unknown"))
        severity_score = _safe_float(row.get("severity_score"))
        if severity_score > float(batch["max_severity_score"] or 0.0):
            batch["max_severity_score"] = severity_score
        for symbol in [
            *list(row.get("symbols") or []),
            *list(row.get("impacted_symbols") or []),
        ]:
            clean_symbol = str(symbol).strip().upper()
            if clean_symbol:
                batch["symbols"].add(clean_symbol)
        for warning in list(row.get("quality_warnings") or []):
            clean_warning = str(warning).strip()
            if clean_warning:
                batch["warnings"].add(clean_warning)
                warning_counts = batch["warning_counts"]
                warning_counts[clean_warning] = warning_counts.get(clean_warning, 0) + 1
        for target in list(row.get("repair_targets") or []):
            if not isinstance(target, dict):
                continue
            action = str(target.get("recommended_action") or "").strip()
            if action:
                batch["recommended_actions"].add(action)
    out: list[dict[str, Any]] = []
    for batch in grouped.values():
        row = {
            "scope": str(batch["scope"] or ""),
            "action_type": str(batch["action_type"] or ""),
            "count": int(batch["count"] or 0),
            "symbols": sorted(batch["symbols"])[:64],
            "warnings": sorted(batch["warnings"])[:16],
            "recommended_actions": sorted(batch["recommended_actions"])[:16],
            "priority_types": sorted(batch["priority_types"])[:12],
        }
        warning_counts = {
            str(key): int(value)
            for key, value in sorted(
                dict(batch["warning_counts"]).items(),
                key=lambda item: (-int(item[1] or 0), str(item[0])),
            )[:16]
            if str(key).strip() and int(value or 0) > 0
        }
        if warning_counts:
            row["warning_counts"] = warning_counts
        max_severity_score = _safe_float(batch.get("max_severity_score"))
        if max_severity_score > 0:
            row["max_severity_score"] = max_severity_score
        out.append(row)
    return sorted(
        out,
        key=_repair_action_batch_sort_key,
    )[: max(int(limit), 0)]


def _repair_action_batch_sort_key(row: dict[str, Any]) -> tuple[float, int, int, str, str]:
    warning_pressure = 0
    raw_warning_counts = row.get("warning_counts")
    if isinstance(raw_warning_counts, dict):
        warning_pressure = sum(_safe_int(value) for value in raw_warning_counts.values())
    else:
        warning_pressure = len([item for item in list(row.get("warnings") or []) if item])
    return (
        -_safe_float(row.get("max_severity_score")),
        -_safe_int(row.get("count")),
        -warning_pressure,
        str(row.get("scope") or ""),
        str(row.get("action_type") or ""),
    )


def _compact_repair_action_batch(row: dict[str, Any]) -> dict[str, Any]:
    def compact_list(raw: Any, *, limit: int, max_len: int, upper: bool = False) -> list[str]:
        values: list[str] = []
        if raw in (None, "", [], {}):
            raw_items: list[Any] = []
        elif isinstance(raw, (list, tuple, set)):
            raw_items = list(raw)
        else:
            raw_items = [raw]
        for item in raw_items[: max(int(limit), 0)]:
            text = str(item or "").strip()
            if not text:
                continue
            if upper:
                text = text.upper()
            text = text[:max_len]
            if text and text not in values:
                values.append(text)
        return values

    compact: dict[str, Any] = {}
    scope = str(row.get("scope") or "").strip().lower()
    if scope:
        compact["scope"] = scope[:40]
    action_type = str(row.get("action_type") or "").strip()
    if action_type:
        compact["action_type"] = action_type[:120]
    if row.get("count") not in (None, "", [], {}):
        compact["count"] = _safe_int(row.get("count"))
    symbols = compact_list(row.get("symbols"), limit=64, max_len=40, upper=True)
    if symbols:
        compact["symbols"] = symbols
    warnings = compact_list(row.get("warnings"), limit=16, max_len=120)
    if warnings:
        compact["warnings"] = warnings
    warning_counts = row.get("warning_counts")
    if isinstance(warning_counts, dict) and warning_counts:
        compact_warning_counts = {
            str(raw_key).strip()[:120]: _safe_int(raw_value)
            for raw_key, raw_value in warning_counts.items()
            if str(raw_key).strip() and _safe_int(raw_value) > 0
        }
        if compact_warning_counts:
            compact["warning_counts"] = compact_warning_counts
    max_severity_score = _safe_float(row.get("max_severity_score"))
    if max_severity_score > 0:
        compact["max_severity_score"] = max_severity_score
    recommended_actions = compact_list(
        row.get("recommended_actions"),
        limit=16,
        max_len=180,
    )
    if recommended_actions:
        compact["recommended_actions"] = recommended_actions
    priority_types = compact_list(row.get("priority_types"), limit=12, max_len=80)
    if priority_types:
        compact["priority_types"] = priority_types
    return compact


def _first_present_int(*values: Any) -> int:
    for value in values:
        if value not in (None, "", [], {}):
            return int(_safe_int(value))
    return 0


def _more_severe_quality_status(*values: Any) -> str:
    severity = {
        "weak": 4,
        "partial": 3,
        "unknown": 2,
        "strong": 1,
    }
    best = ""
    best_score = 0
    for value in values:
        status = normalize_jue_wiki_quality_status(value)
        score = severity.get(status, 0)
        if score > best_score:
            best = status
            best_score = score
    return best


def _wiki_freshness_signal(value: Any) -> str:
    freshness = str(value or "").strip().lower()
    if freshness in {"fresh", "current", "recent", "live", "up_to_date"}:
        return "fresh"
    if freshness in {"stale", "old", "expired", "outdated"}:
        return "stale"
    return ""


def _parse_wiki_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _wiki_page_freshness_profile(page: dict[str, Any]) -> dict[str, Any]:
    signal = _wiki_freshness_signal(page.get("freshness"))
    updated_at = _parse_wiki_datetime(page.get("updated_at"))
    age_stale = (
        updated_at is not None
        and datetime.now(timezone.utc) - updated_at > timedelta(days=14)
    )
    warnings: list[str] = []
    if signal == "stale":
        status = "stale"
        warnings.append("freshness_label_stale")
    elif age_stale:
        status = "stale"
        warnings.append("updated_at_stale_gt_14d")
    elif signal == "fresh":
        status = "fresh"
    else:
        status = "unknown"
        warnings.append("freshness_unknown")
    return {
        "freshness_status": status,
        "freshness_warnings": warnings,
    }


def _repair_pressure_action_plan(
    *,
    total_count: int,
    top_count: int,
    omitted_count: int,
    omitted_type_counts: dict[str, int] | None,
    action_batches: list[dict[str, Any]] | None = None,
    action_batch_total_count: int | None = None,
    action_batch_omitted_count: int | None = None,
) -> dict[str, Any]:
    batches = [row for row in list(action_batches or []) if isinstance(row, dict)]
    if omitted_count <= 0 and not batches:
        return {}
    plan = {
        "status": "compressed",
        "total_priority_count": int(total_count),
        "top_priority_count": int(top_count),
        "omitted_priority_count": int(omitted_count),
        "omitted_priority_type_counts": dict(omitted_type_counts or {}),
        "required_response": (
            "treat top_priorities as representative, not exhaustive; mention "
            "omitted repair pressure when confidence or sizing depends on wiki "
            "freshness"
        ),
    }
    if batches:
        batch_total = sum(max(_safe_int(row.get("count")), 0) for row in batches)
        if batch_total <= 0:
            batch_total = len(batches)
        full_batch_total = (
            int(action_batch_total_count)
            if action_batch_total_count is not None and action_batch_total_count > 0
            else batch_total
        )
        batch_type_counts: dict[str, int] = {}
        batch_scopes: list[str] = []
        batch_warning_counts: dict[str, int] = {}
        batch_max_severity_score = 0.0
        for row in batches:
            action_type = str(row.get("action_type") or "").strip()
            count = max(_safe_int(row.get("count")), 0)
            if action_type:
                batch_type_counts[action_type] = batch_type_counts.get(action_type, 0) + (
                    count or 1
                )
            scope = str(row.get("scope") or "").strip()
            if scope and scope not in batch_scopes:
                batch_scopes.append(scope)
            warning_counts = row.get("warning_counts")
            if isinstance(warning_counts, dict):
                for raw_key, raw_value in warning_counts.items():
                    warning = str(raw_key).strip()
                    warning_count = max(_safe_int(raw_value), 0)
                    if warning and warning_count > 0:
                        batch_warning_counts[warning] = (
                            batch_warning_counts.get(warning, 0) + warning_count
                        )
            else:
                for warning in list(row.get("warnings") or []):
                    clean_warning = str(warning).strip()
                    if clean_warning:
                        batch_warning_counts[clean_warning] = (
                            batch_warning_counts.get(clean_warning, 0) + 1
                        )
            severity_score = _safe_float(row.get("max_severity_score"))
            if severity_score > batch_max_severity_score:
                batch_max_severity_score = severity_score
        plan["action_batch_count"] = len(batches)
        plan["action_batch_total_count"] = full_batch_total
        plan["action_batch_visible_pressure_count"] = batch_total
        if full_batch_total > 0:
            plan["action_batch_pressure_visibility_ratio"] = round(
                min(max(batch_total / full_batch_total, 0.0), 1.0),
                4,
            )
        omitted_batch_count = max(int(action_batch_omitted_count or 0), 0)
        if omitted_batch_count > 0:
            plan["action_batch_omitted_count"] = omitted_batch_count
        if batch_type_counts:
            plan["action_batch_type_counts"] = batch_type_counts
        if batch_scopes:
            plan["action_batch_scopes"] = batch_scopes[:8]
        if batch_warning_counts:
            plan["action_batch_warning_counts"] = {
                key: value
                for key, value in sorted(
                    batch_warning_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:16]
            }
        if batch_max_severity_score > 0:
            plan["action_batch_max_severity_score"] = batch_max_severity_score
        plan["required_response"] = (
            f"{plan['required_response']}; treat action_batches as grouped repair "
            "work that must be reflected in candidate resolution, hold triggers, "
            "or repair metadata before confidence/sizing"
        )
    return plan


def _repair_priority_budget_slice(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    clean_limit = max(int(limit), 0)
    if clean_limit <= 0:
        return []
    if len(rows) <= clean_limit:
        return rows[:clean_limit]

    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def append(row: dict[str, Any]) -> None:
        if len(selected) >= clean_limit:
            return
        row_identity = _repair_priority_identity(row)
        if row_identity in seen:
            return
        selected.append(row)
        seen.add(row_identity)

    append(rows[0])
    for priority_type in (
        "repair_queue",
        "requested_symbol_coverage",
        "requested_symbol_degraded_summary",
        "evidence_quality",
        "wiki_attention",
        "memory_card_quality",
        "lint",
    ):
        if len(selected) >= clean_limit:
            break
        for row in rows:
            if str(row.get("priority_type") or "") == priority_type:
                append(row)
                break
    for row in rows:
        if len(selected) >= clean_limit:
            break
        append(row)
    return selected


def build_jue_wiki_repair_contract_for_prompt(
    payload: dict[str, Any] | None,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    priorities: list[dict[str, Any]] = []
    source_priorities = [
        row for row in list(source.get("repair_priorities") or []) if isinstance(row, dict)
    ]
    selected_source_priorities = _repair_priority_budget_slice(
        source_priorities,
        limit=limit,
    )
    priority_budget_summary = _repair_priority_budget_summary(
        source_priorities,
        selected_source_priorities,
    )
    budget_report = (
        source.get("budget_report") if isinstance(source.get("budget_report"), dict) else {}
    )
    priority_total_count = _first_present_int(
        budget_report.get("repair_priority_total_count")
        if "repair_priority_total_count" in budget_report
        else None,
        priority_budget_summary.get("repair_priority_total_count"),
        len(source_priorities),
    )
    priority_selected_count = _first_present_int(
        budget_report.get("repair_priority_selected_count")
        if "repair_priority_selected_count" in budget_report
        else None,
        priority_budget_summary.get("repair_priority_selected_count"),
        len(selected_source_priorities),
    )
    priority_omitted_count = _first_present_int(
        budget_report.get("repair_priority_omitted_count")
        if "repair_priority_omitted_count" in budget_report
        else None,
        priority_budget_summary.get("repair_priority_omitted_count"),
        max(priority_total_count - priority_selected_count, 0),
    )
    if (
        "repair_priority_selected_count" in budget_report
        and priority_selected_count <= 0
    ):
        selected_source_priorities = []
    priority_type_counts = (
        budget_report.get("repair_priority_type_counts")
        if isinstance(budget_report.get("repair_priority_type_counts"), dict)
        else priority_budget_summary.get("repair_priority_type_counts")
    )
    selected_priority_type_counts = (
        budget_report.get("repair_priority_selected_type_counts")
        if isinstance(budget_report.get("repair_priority_selected_type_counts"), dict)
        else priority_budget_summary.get("repair_priority_selected_type_counts")
    )
    omitted_priority_type_counts = (
        budget_report.get("repair_priority_omitted_type_counts")
        if isinstance(budget_report.get("repair_priority_omitted_type_counts"), dict)
        else priority_budget_summary.get("repair_priority_omitted_type_counts")
    )
    raw_action_batches = (
        source.get("repair_action_batches")
        or budget_report.get("repair_action_batches")
        or []
    )
    if (
        not raw_action_batches
        and not (
            "repair_priority_selected_count" in budget_report
            and priority_selected_count <= 0
        )
    ):
        raw_action_batches = priority_budget_summary.get("repair_action_batches") or []
    compacted_action_batches = [
        compact
        for compact in (
            _compact_repair_action_batch(row)
            for row in list(raw_action_batches)
            if isinstance(row, dict)
        )
        if compact
    ]
    sorted_action_batches = sorted(
        compacted_action_batches,
        key=_repair_action_batch_sort_key,
    )
    action_batches = sorted_action_batches[:12]
    action_batch_omitted_count = max(
        len(compacted_action_batches) - len(action_batches),
        0,
    )
    action_batch_total_count = sum(
        max(_safe_int(row.get("count")), 0) for row in compacted_action_batches
    )
    if action_batch_total_count <= 0 and compacted_action_batches:
        action_batch_total_count = len(compacted_action_batches)
    action_batch_visible_pressure_count = sum(
        max(_safe_int(row.get("count")), 0) for row in action_batches
    )
    if action_batch_visible_pressure_count <= 0 and action_batches:
        action_batch_visible_pressure_count = len(action_batches)
    action_batch_pressure_visibility_ratio = (
        round(
            min(
                max(action_batch_visible_pressure_count / action_batch_total_count, 0.0),
                1.0,
            ),
            4,
        )
        if action_batch_total_count > 0
        else 0.0
    )
    for row in selected_source_priorities:
        compact = {
            "page_id": str(row.get("page_id") or ""),
            "page_type": str(row.get("page_type") or ""),
            "priority_type": str(row.get("priority_type") or ""),
            "symbols": [
                str(symbol)
                for symbol in list(row.get("symbols") or [])[:6]
                if str(symbol).strip()
            ],
            "symbol_overlap": [
                str(symbol)
                for symbol in list(row.get("symbol_overlap") or [])[:6]
                if str(symbol).strip()
            ],
            "source_type": str(row.get("source_type") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "action_type": str(row.get("action_type") or ""),
            "repair_status": str(row.get("repair_status") or ""),
            "quality_status": normalize_jue_wiki_quality_status(
                row.get("quality_status")
            ),
            "quality_warnings": [
                str(item)[:120]
                for item in list(row.get("quality_warnings") or [])[:5]
                if str(item).strip()
            ],
            "diagnostic_reasons": [
                str(item)[:180]
                for item in list(row.get("diagnostic_reasons") or [])[:8]
                if str(item).strip()
            ],
            "repair_action": str(row.get("repair_action") or "")[:240],
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "missing_fields": _compact_string_list(
                row.get("missing_fields"),
                limit=8,
                max_len=80,
            ),
            "required_checks": _compact_string_list(
                row.get("required_checks"),
                limit=8,
                max_len=160,
            ),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
            "reasons": [
                str(item)[:180]
                for item in list(row.get("reasons") or [])[:8]
                if str(item).strip()
            ],
            "decision_use": _jue_wiki_repair_priority_decision_use(row),
            "repair_loop_status": str(row.get("repair_loop_status") or ""),
            "repair_loop_action_type": str(row.get("repair_loop_action_type") or ""),
            "hard_blocker": False,
            "candidate_resolution_required": True,
        }
        optional_int_fields = (
            ("sample_count", "sample_count"),
            ("repair_loop_sample_count", "repair_loop_sample_count"),
            ("repair_loop_missed_count", "repair_loop_missed_count"),
            ("repair_loop_resolved_count", "repair_loop_resolved_count"),
        )
        for source_key, target_key in optional_int_fields:
            value = row.get(source_key)
            if value not in (None, "", [], {}):
                compact[target_key] = _safe_int(value)
        optional_float_fields = (
            ("win_rate", "win_rate"),
            ("expectancy", "expectancy"),
            ("helpful_score", "helpful_score"),
            ("drawdown_pressure", "drawdown_pressure"),
            ("repair_loop_resolution_rate", "repair_loop_resolution_rate"),
        )
        for source_key, target_key in optional_float_fields:
            value = row.get(source_key)
            if value not in (None, "", [], {}):
                compact[target_key] = _safe_float(value)
        horizon_gap_total = _safe_int(
            row.get("closed_block_outcomes_without_horizon")
        )
        horizon_gap_pct = _safe_float(
            row.get("closed_block_outcomes_without_horizon_pct")
        )
        if horizon_gap_total > 0:
            compact["closed_block_outcomes_without_horizon"] = horizon_gap_total
        if horizon_gap_pct > 0:
            compact["closed_block_outcomes_without_horizon_pct"] = horizon_gap_pct
        priorities.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    repair_loop_effectiveness = _compact_repair_loop_effectiveness(
        source.get("repair_priority_effectiveness")
    )
    component_target_attention_plan = (
        repair_loop_effectiveness.get("repair_component_target_summary", {}).get(
            "component_target_attention_plan"
        )
        if isinstance(repair_loop_effectiveness, dict)
        else {}
    )
    priority_attention_plan = _wiki_attention_priorities_to_attention_plan(priorities)
    if not component_target_attention_plan:
        component_target_attention_plan = priority_attention_plan
    else:
        component_target_attention_plan = _merge_component_target_attention_plan(
            component_target_attention_plan,
            priority_attention_plan,
        )
    attention_plan_response_contract = _component_target_attention_response_contract(
        component_target_attention_plan
    )
    if not priorities and not repair_loop_effectiveness and not action_batches:
        return {}
    return {
        key: value
        for key, value in {
            "version": "jue_wiki_repair_contract_v1",
            "status": "active",
            "repair_priority_count": priority_total_count,
            "top_priority_count": priority_selected_count,
            "omitted_priority_count": priority_omitted_count,
            "priority_type_counts": priority_type_counts,
            "top_priority_type_counts": selected_priority_type_counts,
            "omitted_priority_type_counts": omitted_priority_type_counts,
            "repair_pressure_action_plan": _repair_pressure_action_plan(
                total_count=priority_total_count,
                top_count=priority_selected_count,
                omitted_count=priority_omitted_count,
                omitted_type_counts=omitted_priority_type_counts,
                action_batches=action_batches,
                action_batch_total_count=action_batch_total_count,
                action_batch_omitted_count=action_batch_omitted_count,
            ),
            "top_priorities": priorities,
            "action_batches": action_batches,
            "action_batch_total_count": action_batch_total_count,
            "action_batch_omitted_count": action_batch_omitted_count,
            "action_batch_visible_pressure_count": action_batch_visible_pressure_count,
            "action_batch_pressure_visibility_ratio": action_batch_pressure_visibility_ratio,
            "repair_loop_effectiveness": repair_loop_effectiveness,
            "component_target_attention_plan": component_target_attention_plan,
            "attention_plan_response_contract": attention_plan_response_contract,
            "required_resolution": (
                "Resolve relevant repair priorities as candidate-level execution "
                "checks, safer waiting/probe block designs, or precise reject "
                "conditions. Do not use degraded memory as a standalone no-action "
                "blocker."
            ),
            "allowed_resolutions": [
                "safer_waiting_block",
                "small_probe_block",
                "candidate_level_reject",
                "entry_exit_design_revision",
                "defer_due_to_safety_gate",
            ],
            "hard_blockers_allowed": False,
            "quality_warning_effectiveness_policy": (
                "repair_or_downgrade_warning_bearing_evidence_without_blanket_holds"
            ),
        }.items()
        if value not in ("", [], {}, None)
    }


def compact_jue_wiki_repair_loop_effectiveness_for_prompt(
    source: Any,
) -> dict[str, Any]:
    return _compact_repair_loop_effectiveness(source)


def compact_jue_wiki_validation_repair_effectiveness_for_prompt(
    source: Any,
) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    top_degraded = [
        _compact_validation_repair_effectiveness_row(row)
        for row in list(source.get("top_degraded") or [])[:8]
        if isinstance(row, dict)
    ]
    metrics = [
        _compact_validation_repair_effectiveness_row(row)
        for row in list(source.get("metrics") or [])[:16]
        if isinstance(row, dict)
    ]
    top_degraded = [row for row in top_degraded if row]
    metrics = [row for row in metrics if row]
    compact: dict[str, Any] = {
        "status": str(source.get("status") or ""),
        "top_degraded": top_degraded,
        "metrics": metrics,
        "validation_repair_action_plan": (
            _validation_repair_action_plan_for_prompt(top_degraded)
        ),
    }
    for key in (
        "sample_count",
        "missed_count",
        "resolved_count",
        "metric_count",
        "active_count",
        "probe_count",
        "repair_required_count",
    ):
        value = source.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_int(value)
    value = source.get("resolution_rate")
    if value not in (None, "", [], {}):
        compact["resolution_rate"] = _safe_float(value)
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def build_jue_wiki_validation_repair_contract_for_prompt(
    source: Any,
) -> dict[str, Any]:
    validation = source if isinstance(source, dict) else {}
    if isinstance(validation.get("validation_repair_effectiveness"), dict):
        validation = validation["validation_repair_effectiveness"]
    if "validation_repair_action_plan" not in validation:
        validation = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
            validation
        )
    plan = (
        validation.get("validation_repair_action_plan")
        if isinstance(validation.get("validation_repair_action_plan"), dict)
        else {}
    )
    if not plan:
        return {}
    source_counts = {
        str(source).strip()[:180]: _safe_int(count)
        for source, count in (
            plan.get("source_counts")
            if isinstance(plan.get("source_counts"), dict)
            else {}
        ).items()
        if str(source).strip() and _safe_int(count) > 0
    }
    legacy_source_counts = {
        str(source).strip()[:180]: _safe_int(count)
        for source, count in (
            plan.get("legacy_source_counts")
            if isinstance(plan.get("legacy_source_counts"), dict)
            else {}
        ).items()
        if str(source).strip() and _safe_int(count) > 0
    }
    contract_source_counts = {
        str(source).strip()[:180]: _safe_int(count)
        for source, count in (
            plan.get("contract_source_counts")
            if isinstance(plan.get("contract_source_counts"), dict)
            else {}
        ).items()
        if str(source).strip() and _safe_int(count) > 0
    }
    degraded_metric_evidence = [
        _validation_repair_degraded_metric_evidence_row(row)
        for row in list(plan.get("degraded_metric_evidence") or [])[:4]
        if isinstance(row, dict)
    ]
    degraded_metric_evidence = [row for row in degraded_metric_evidence if row]
    out = {
        key: value
        for key, value in {
            "version": "jue_wiki_validation_repair_contract_v1",
            "status": str(plan.get("status") or ""),
            "hard_blocker": bool(plan.get("hard_blocker") or False),
            "requires_validation_repair_resolution": bool(
                plan.get("requires_validation_repair_resolution") or True
            ),
            "top_disciplines": [
                str(item).strip()[:180]
                for item in list(plan.get("top_disciplines") or [])[:8]
                if str(item).strip()
            ],
            "repair_action_ids": [
                str(item).strip()[:180]
                for item in list(plan.get("repair_action_ids") or [])[:8]
                if str(item).strip()
            ],
            "entry_biases": [
                str(item).strip()[:180]
                for item in list(plan.get("entry_biases") or [])[:8]
                if str(item).strip()
            ],
            "allowed_entry_postures": [
                str(item).strip()[:180]
                for item in list(plan.get("allowed_entry_postures") or [])[:8]
                if str(item).strip()
            ],
            "blocked_entry_patterns": [
                str(item).strip()[:180]
                for item in list(plan.get("blocked_entry_patterns") or [])[:8]
                if str(item).strip()
            ],
            "source_mix_status": str(plan.get("source_mix_status") or "")[:120],
            "source_mix_count_basis": str(
                plan.get("source_mix_count_basis") or ""
            )[:120],
            "contract_basis_pressure_summary": (
                _validation_repair_contract_basis_pressure_summary_from_plan(
                    plan.get("contract_basis_pressure_summary")
                )
            ),
            "contract_feedback_gap": (
                _validation_repair_contract_feedback_gap_from_plan(
                    plan.get("contract_feedback_gap")
                )
            ),
            "degraded_metric_evidence": degraded_metric_evidence,
            "required_response": str(plan.get("required_response") or "")[:480],
            "accepted_resolutions": [
                "smaller_probe_block",
                "waiting_entry_with_validation_repair_resolution",
                "candidate_reject_with_missing_validation_named",
                "regime_confirmed_wait",
                "risk_check_defer",
                "new_watch_with_trigger",
                "no_new_entry_until_required_validation_repair_is_resolved",
            ],
            "safety_gates_still_override": bool(
                plan.get("safety_gates_still_override") or True
            ),
        }.items()
        if value not in ("", [], {}, None)
    }
    for key in ("legacy_sample_count", "contract_sample_count"):
        if _has_prompt_value(plan.get(key)):
            out[key] = _safe_int(plan.get(key))
    if _has_prompt_value(plan.get("risk_budget_multiplier")):
        out["risk_budget_multiplier"] = _safe_float(
            plan.get("risk_budget_multiplier")
        )
    if source_counts:
        out["source_counts"] = source_counts
        out["legacy_source_counts"] = legacy_source_counts
        out["contract_source_counts"] = contract_source_counts
    return out


def _validation_repair_contract_basis_pressure_summary_from_plan(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {
        "status": str(value.get("status") or "").strip()[:120],
    }
    for key in ("sample_count", "missed_count", "resolved_count"):
        if _has_prompt_value(value.get(key)):
            compact[key] = _safe_int(value.get(key))
    for key in ("resolution_rate", "miss_rate", "repair_pressure_score"):
        if _has_prompt_value(value.get(key)):
            compact[key] = _safe_float(value.get(key))
    return {
        key: clean_value
        for key, clean_value in compact.items()
        if clean_value not in ("", [], {}, None)
    }


def _validation_repair_contract_feedback_gap_from_plan(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {
        "status": str(value.get("status") or "").strip()[:120],
        "required_response": str(value.get("required_response") or "")[:360],
    }
    for key in ("legacy_sample_count", "contract_sample_count"):
        if _has_prompt_value(value.get(key)):
            compact[key] = _safe_int(value.get(key))
    return {
        key: clean_value
        for key, clean_value in compact.items()
        if clean_value not in ("", [], {}, None)
    }


def _explicit_or_fallback(
    row: dict[str, Any],
    fallback: dict[str, Any],
    key: str,
) -> Any:
    value = row.get(key)
    if value not in (None, "", [], {}):
        return value
    return fallback.get(key)


def _validation_repair_degraded_metric_evidence_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    contract_basis_summary = _validation_repair_contract_basis_summary(
        row.get("contract_basis_evidence")
    )
    has_contract_basis = bool(contract_basis_summary) or any(
        row.get(key) not in (None, "", [], {})
        for key in (
            "contract_basis_sample_count",
            "contract_basis_missed_count",
            "contract_basis_resolved_count",
            "contract_basis_resolution_rate",
            "contract_basis_miss_rate",
            "contract_basis_repair_pressure_score",
            "contract_basis_status",
        )
    )
    source_counts = {
        str(source).strip()[:180]: _safe_int(count)
        for source, count in (
            row.get("source_counts") if isinstance(row.get("source_counts"), dict) else {}
        ).items()
        if str(source).strip() and _safe_int(count) > 0
    }
    contract_basis_evidence = _validation_repair_contract_basis_evidence_rows(
        row.get("contract_basis_evidence")
    )
    compact: dict[str, Any] = {
        "discipline_id": str(row.get("discipline_id") or "").strip()[:180],
        "repair_action_id": str(row.get("repair_action_id") or "").strip()[:180],
        "entry_bias": str(row.get("entry_bias") or "").strip()[:180],
        "status": str(row.get("status") or "").strip()[:120],
        "source_counts": source_counts,
        "contract_basis_evidence": contract_basis_evidence,
    }
    for key in ("sample_count", "missed_count", "resolved_count"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_int(value)
    value = row.get("resolution_rate")
    if value not in (None, "", [], {}):
        compact["resolution_rate"] = _safe_float(value)
    if has_contract_basis:
        for key in (
            "contract_basis_sample_count",
            "contract_basis_missed_count",
            "contract_basis_resolved_count",
        ):
            value = _explicit_or_fallback(row, contract_basis_summary, key)
            if value not in (None, "", [], {}):
                compact[key] = _safe_int(value)
        for key in (
            "contract_basis_resolution_rate",
            "contract_basis_miss_rate",
            "contract_basis_repair_pressure_score",
        ):
            value = _explicit_or_fallback(row, contract_basis_summary, key)
            if value not in (None, "", [], {}):
                compact[key] = _safe_float(value)
        contract_basis_status = str(
            _explicit_or_fallback(row, contract_basis_summary, "contract_basis_status")
            or ""
        ).strip()[:120]
        if contract_basis_status:
            compact["contract_basis_status"] = contract_basis_status
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _validation_repair_contract_basis_evidence_rows(
    value: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in list(value or [])[:4]:
        if not isinstance(row, dict):
            continue
        source_counts = {
            str(source).strip()[:180]: _safe_int(count)
            for source, count in (
                row.get("source_counts") if isinstance(row.get("source_counts"), dict) else {}
            ).items()
            if str(source).strip() and _safe_int(count) > 0
        }
        compact: dict[str, Any] = {
            "discipline_id": str(row.get("discipline_id") or "").strip()[:180],
            "repair_action_id": str(row.get("repair_action_id") or "").strip()[
                :180
            ],
            "entry_bias": str(row.get("entry_bias") or "").strip()[:180],
            "status": str(row.get("status") or "").strip()[:120],
            "source_counts": source_counts,
        }
        for key in ("sample_count", "missed_count", "resolved_count"):
            metric_value = row.get(key)
            if metric_value not in (None, "", [], {}):
                compact[key] = _safe_int(metric_value)
        metric_value = row.get("resolution_rate")
        if metric_value not in (None, "", [], {}):
            compact["resolution_rate"] = _safe_float(metric_value)
        compact = {
            key: item
            for key, item in compact.items()
            if item not in ("", [], {}, None)
        }
        if compact:
            rows.append(compact)
    return rows


def _validation_repair_contract_basis_summary(
    value: Any,
) -> dict[str, Any]:
    rows = [row for row in list(value or []) if isinstance(row, dict)]
    if not rows:
        return {}
    sample_count = sum(_safe_int(row.get("sample_count")) for row in rows)
    missed_count = sum(_safe_int(row.get("missed_count")) for row in rows)
    resolved_count = sum(_safe_int(row.get("resolved_count")) for row in rows)
    if sample_count <= 0:
        return {}
    resolution_rate = resolved_count / sample_count
    miss_rate = missed_count / sample_count
    statuses = {
        str(row.get("status") or "").strip().lower()
        for row in rows
        if str(row.get("status") or "").strip()
    }
    status = "repair_required"
    if "repair_required" in statuses:
        status = "repair_required"
    elif "probe" in statuses:
        status = "probe"
    elif "active" in statuses:
        status = "active"
    return {
        "contract_basis_sample_count": sample_count,
        "contract_basis_missed_count": missed_count,
        "contract_basis_resolved_count": resolved_count,
        "contract_basis_resolution_rate": resolution_rate,
        "contract_basis_miss_rate": miss_rate,
        "contract_basis_repair_pressure_score": round(missed_count * miss_rate, 6),
        "contract_basis_status": status,
    }


def _validation_repair_action_plan_for_prompt(
    top_degraded: list[dict[str, Any]],
) -> dict[str, Any]:
    if not top_degraded:
        return {}
    statuses = {
        str(row.get("status") or "").strip().lower()
        for row in top_degraded
        if str(row.get("status") or "").strip()
    }
    status = "repair_required" if "repair_required" in statuses else "probe"
    allowed_entry_postures: list[str] = []
    blocked_entry_patterns: list[str] = []
    top_disciplines: list[str] = []
    repair_action_ids: list[str] = []
    entry_biases: list[str] = []
    multipliers: list[float] = []
    risk_budget_multiplier_present = False
    source_counts: dict[str, int] = {}
    for row in top_degraded[:8]:
        for source_key, target in (
            ("allowed_entry_postures", allowed_entry_postures),
            ("blocks_new_entries", blocked_entry_patterns),
        ):
            for item in list(row.get(source_key) or []):
                text = str(item or "").strip()[:180]
                if text and text not in target:
                    target.append(text)
        for source_key, target in (
            ("discipline_id", top_disciplines),
            ("repair_action_id", repair_action_ids),
            ("entry_bias", entry_biases),
        ):
            text = str(row.get(source_key) or "").strip()[:180]
            if text and text not in target:
                target.append(text)
        multiplier_value = row.get("risk_budget_multiplier")
        if _has_prompt_value(multiplier_value):
            risk_budget_multiplier_present = True
        multiplier = _safe_float(multiplier_value)
        if multiplier > 0 or (
            risk_budget_multiplier_present
            and multiplier == 0.0
            and _has_prompt_value(multiplier_value)
        ):
            multipliers.append(multiplier)
        row_source_counts = (
            row.get("source_counts") if isinstance(row.get("source_counts"), dict) else {}
        )
        for source, count in row_source_counts.items():
            clean_source = str(source).strip()[:180]
            clean_count = _safe_int(count)
            if clean_source and clean_count > 0:
                source_counts[clean_source] = (
                    int(source_counts.get(clean_source) or 0) + clean_count
                )
    legacy_source_counts = {
        source: count
        for source, count in source_counts.items()
        if not source.endswith("_validation_repair_contract")
    }
    contract_source_counts = {
        source: count
        for source, count in source_counts.items()
        if source.endswith("_validation_repair_contract")
    }
    legacy_sample_count = sum(legacy_source_counts.values())
    contract_sample_count = sum(contract_source_counts.values())
    if contract_sample_count > 0 and legacy_sample_count > 0:
        source_mix_status = "mixed_contract_and_legacy"
    elif contract_sample_count > 0:
        source_mix_status = "contract_only"
    elif legacy_sample_count > 0:
        source_mix_status = "legacy_only"
    else:
        source_mix_status = ""
    risk_budget_multiplier = (
        min(multipliers)
        if multipliers
        else 0.0
        if risk_budget_multiplier_present
        else None
    )
    contract_basis_pressure_summary = (
        _validation_repair_contract_basis_pressure_summary(top_degraded[:8])
    )
    contract_feedback_gap = _validation_repair_contract_feedback_gap(
        legacy_sample_count=legacy_sample_count,
        contract_sample_count=contract_sample_count,
    )
    out = {
        key: value
        for key, value in {
            "status": status,
            "hard_blocker": False,
            "requires_validation_repair_resolution": True,
            "top_disciplines": top_disciplines[:8],
            "repair_action_ids": repair_action_ids[:8],
            "entry_biases": entry_biases[:8],
            "allowed_entry_postures": allowed_entry_postures[:8],
            "blocked_entry_patterns": blocked_entry_patterns[:8],
            "risk_budget_multiplier": risk_budget_multiplier,
            "source_counts": source_counts,
            "legacy_source_counts": legacy_source_counts,
            "contract_source_counts": contract_source_counts,
            "legacy_sample_count": legacy_sample_count,
            "contract_sample_count": contract_sample_count,
            "source_mix_status": source_mix_status,
            "source_mix_count_basis": "top_degraded_metric_signal_count",
            "contract_basis_pressure_summary": contract_basis_pressure_summary,
            "contract_feedback_gap": contract_feedback_gap,
            "degraded_metric_evidence": [
                _validation_repair_degraded_metric_evidence_row(row)
                for row in top_degraded[:4]
            ],
            "required_response": (
                "For candidates touched by these validation gaps, avoid scale-up "
                "or unvalidated immediate entries; use a smaller probe, waiting "
                "entry, explicit candidate rejection, or safety-gate defer, and "
                "record validation_repair_resolution with resolved_candidates."
            ),
            "accepted_resolutions": [
                "smaller_probe_block",
                "waiting_entry_block",
                "candidate_rejected",
                "updated_price_geometry",
                "regime_confirmed_wait",
                "risk_check_defer",
                "new_watch_with_trigger",
                "safety_gate_defer",
            ],
            "safety_gates_still_override": True,
        }.items()
        if value not in ("", [], {}, None)
        and not (
            isinstance(value, float)
            and value == 0.0
            and key != "risk_budget_multiplier"
        )
    }
    if source_counts:
        out["source_counts"] = source_counts
        out["legacy_source_counts"] = legacy_source_counts
        out["contract_source_counts"] = contract_source_counts
    return out


def _validation_repair_contract_feedback_gap(
    *,
    legacy_sample_count: int,
    contract_sample_count: int,
) -> dict[str, Any]:
    if legacy_sample_count <= 0 or contract_sample_count > 0:
        return {}
    return {
        "status": "missing_contract_outcomes",
        "legacy_sample_count": int(legacy_sample_count),
        "contract_sample_count": int(contract_sample_count),
        "required_response": (
            "record validation_repair_resolution and resolved_candidates so "
            "future wiki updates can measure contract effectiveness"
        ),
    }


def _validation_repair_contract_basis_pressure_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    basis_rows: list[dict[str, Any]] = []
    for row in rows:
        basis_rows.extend(_validation_repair_contract_basis_evidence_rows(
            row.get("contract_basis_evidence")
        ))
    summary = _validation_repair_contract_basis_summary(basis_rows)
    if not summary:
        return {}
    return {
        key: value
        for key, value in {
            "sample_count": summary.get("contract_basis_sample_count"),
            "missed_count": summary.get("contract_basis_missed_count"),
            "resolved_count": summary.get("contract_basis_resolved_count"),
            "resolution_rate": summary.get("contract_basis_resolution_rate"),
            "miss_rate": summary.get("contract_basis_miss_rate"),
            "repair_pressure_score": summary.get(
                "contract_basis_repair_pressure_score"
            ),
            "status": summary.get("contract_basis_status"),
        }.items()
        if value not in ("", [], {}, None)
    }


def compact_jue_wiki_application_coverage_for_prompt(
    source: Any,
) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    coverage_source = (
        source.get("coverage") if isinstance(source.get("coverage"), dict) else {}
    )
    coverage: dict[str, Any] = {}
    decision_scope = str(coverage_source.get("decision_scope") or "")[:80]
    if decision_scope:
        coverage["decision_scope"] = decision_scope
    for key in (
        "decision_link_count",
        "decision_links_with_selected_wiki_pages",
        "selection_outcome_count",
        "selection_outcomes_with_selected_wiki_page",
        "selection_outcomes_with_quality_warnings",
        "selection_outcome_attribution_filtered_count",
        "closed_block_outcomes_without_horizon",
    ):
        value = coverage_source.get(key)
        if value not in (None, "", [], {}):
            coverage[key] = _safe_int(value)
    for key in (
        "decision_links_with_selected_wiki_pages_pct",
        "selection_outcomes_with_selected_wiki_page_pct",
        "closed_block_outcomes_without_horizon_pct",
    ):
        value = coverage_source.get(key)
        if value not in (None, "", [], {}):
            coverage[key] = _safe_float(value)
    alerts = [
        {
            key: value
            for key, value in {
                "severity": str(row.get("severity") or "")[:80],
                "code": str(row.get("code") or "")[:120],
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "message": str(row.get("message") or "")[:260]
                if str(row.get("code") or "") == "wiki_outcome_horizon_missing"
                else "",
                "action": str(row.get("action") or "")[:180],
            }.items()
            if value not in ("", [], {}, None)
        }
        for row in list(source.get("alerts") or [])[:8]
        if isinstance(row, dict)
    ]
    alerts = [row for row in alerts if row]
    return {
        key: value
        for key, value in {
            "status": str(source.get("status") or "")[:80],
            "decision_scope": str(source.get("decision_scope") or "")[:80],
            "coverage": coverage,
            "alerts": alerts,
        }.items()
        if value not in ("", [], {}, None)
    }


def _compact_validation_repair_effectiveness_row(row: dict[str, Any]) -> dict[str, Any]:
    contract_basis_summary = _validation_repair_contract_basis_summary(
        row.get("contract_basis_evidence")
    )
    contract_basis_evidence = _validation_repair_contract_basis_evidence_rows(
        row.get("contract_basis_evidence")
    )
    sources = _compact_string_list(row.get("sources"), limit=8, max_len=160)
    raw_source_counts = (
        row.get("source_counts") if isinstance(row.get("source_counts"), dict) else {}
    )
    source_counts = {
        source: _safe_int(raw_source_counts.get(source))
        for source in sources
        if _safe_int(raw_source_counts.get(source)) > 0
    }
    compact: dict[str, Any] = {
        "decision_scope": str(row.get("decision_scope") or "")[:80],
        "discipline_id": str(row.get("discipline_id") or "")[:160],
        "repair_action_id": str(row.get("repair_action_id") or "")[:180],
        "entry_bias": str(row.get("entry_bias") or "")[:180],
        "status": str(row.get("status") or "")[:80],
        "allowed_entry_postures": _compact_string_list(
            row.get("allowed_entry_postures"),
            limit=8,
            max_len=160,
        ),
        "blocks_new_entries": _compact_string_list(
            row.get("blocks_new_entries"),
            limit=8,
            max_len=180,
        ),
        "sources": sources,
        "source_counts": source_counts,
        "contract_basis_evidence": contract_basis_evidence,
    }
    for key in ("sample_count", "missed_count", "resolved_count"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_int(value)
    for key in ("resolution_rate", "risk_budget_multiplier", "max_budget_multiplier"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_float(value)
    for key in (
        "contract_basis_sample_count",
        "contract_basis_missed_count",
        "contract_basis_resolved_count",
    ):
        value = _explicit_or_fallback(row, contract_basis_summary, key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_int(value)
    for key in (
        "contract_basis_resolution_rate",
        "contract_basis_miss_rate",
        "contract_basis_repair_pressure_score",
    ):
        value = _explicit_or_fallback(row, contract_basis_summary, key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_float(value)
    contract_basis_status = str(
        _explicit_or_fallback(row, contract_basis_summary, "contract_basis_status")
        or ""
    )[:80]
    if contract_basis_status:
        compact["contract_basis_status"] = contract_basis_status
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _wiki_attention_priorities_to_attention_plan(
    priorities: list[dict[str, Any]],
) -> dict[str, Any]:
    for row in priorities:
        priority_type = str(row.get("priority_type") or "").strip()
        if priority_type not in {"wiki_attention", "memory_card_quality"}:
            continue
        repair_now = {
            "component": priority_type,
            "decision_scope": _scope_from_page_id(str(row.get("page_id") or "")),
            "status": "repair_required",
            "target_status": str(row.get("repair_status") or "unresolved"),
            "priority_type": priority_type,
            "action_type": str(row.get("action_type") or "").strip(),
            "recommended_resolution": str(row.get("repair_action") or "").strip(),
            "sample_count": max(int(row.get("sample_count") or 0), 1),
            "missed_count": 1,
            "resolved_count": 0,
            "resolution_rate": 0.0,
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=6,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids") or [row.get("page_id")],
                limit=6,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols") or row.get("symbols"),
                limit=8,
                max_len=40,
            ),
            "missing_fields": _compact_string_list(
                row.get("missing_fields"),
                limit=8,
                max_len=80,
            ),
            "required_checks": _compact_string_list(
                row.get("required_checks"),
                limit=8,
                max_len=160,
            ),
        }
        return {
            "status": "repair_required",
            "repair_now": {
                key: value
                for key, value in repair_now.items()
                if value not in ("", [], {}, None)
            },
        }
    return {}


def _merge_component_target_attention_plan(
    primary: Any,
    secondary: Any,
) -> dict[str, Any]:
    base = dict(primary) if isinstance(primary, dict) else {}
    extra = secondary if isinstance(secondary, dict) else {}
    if not base:
        return dict(extra)
    if not extra:
        return base

    extra_repair_now = extra.get("repair_now")
    extra_probe_next = extra.get("probe_next")
    if "repair_now" not in base and extra_repair_now:
        base["repair_now"] = extra_repair_now
    elif "probe_next" not in base and extra_repair_now:
        base["probe_next"] = extra_repair_now
    elif extra_repair_now:
        additional_attention = list(base.get("additional_attention") or [])
        additional_attention.append(extra_repair_now)
        base["additional_attention"] = additional_attention[:4]
    elif "probe_next" not in base and extra_probe_next:
        base["probe_next"] = extra_probe_next
    elif extra_probe_next:
        additional_attention = list(base.get("additional_attention") or [])
        additional_attention.append(extra_probe_next)
        base["additional_attention"] = additional_attention[:4]
    if not base.get("status"):
        base["status"] = extra.get("status") or "repair_required"
    return base


def _scope_from_page_id(page_id: str) -> str:
    return str(page_id or "").split(".", 1)[0].strip()


def _component_target_attention_response_item(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact = {
        "component": str(row.get("component") or "").strip()[:120],
        "action_type": str(row.get("action_type") or "").strip()[:120],
        "recommended_resolution": str(
            row.get("recommended_resolution") or ""
        ).strip()[:180],
        "quality_warnings": _compact_string_list(
            row.get("quality_warnings"),
            limit=6,
            max_len=120,
        ),
        "impacted_page_ids": _compact_string_list(
            row.get("impacted_page_ids"),
            limit=6,
            max_len=180,
        ),
        "impacted_symbols": _compact_string_list(
            row.get("impacted_symbols"),
            limit=8,
            max_len=40,
        ),
        "missing_fields": _compact_string_list(
            row.get("missing_fields"),
            limit=8,
            max_len=80,
        ),
        "required_checks": _compact_string_list(
            row.get("required_checks"),
            limit=8,
            max_len=160,
        ),
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _component_target_attention_response_contract(source: Any) -> dict[str, Any]:
    plan = source if isinstance(source, dict) else {}
    if not plan:
        return {}
    repair_now = _component_target_attention_response_item(plan.get("repair_now"))
    probe_next = _component_target_attention_response_item(plan.get("probe_next"))
    additional_attention = [
        item
        for item in (
            _component_target_attention_response_item(row)
            for row in list(plan.get("additional_attention") or [])[:4]
        )
        if item
    ]
    must_address = [
        key
        for key, value in (
            ("repair_now", repair_now),
            ("probe_next", probe_next),
            ("additional_attention", additional_attention),
        )
        if value
    ]
    if not must_address:
        return {}
    compact = {
        "status": "active",
        "must_address": must_address,
        "accepted_response_locations": [
            "create_blocks[].metadata.jue_wiki_repair_attention",
            "update_blocks[].metadata.jue_wiki_repair_attention",
            "close_blocks[].metadata.jue_wiki_repair_attention",
            "hold_decision.reasons",
            "hold_decision.data_gaps",
            "hold_decision.next_triggers",
        ],
        "repair_now": repair_now,
        "probe_next": probe_next,
        "additional_attention": additional_attention,
        "accepted_resolutions": [
            "action_metadata_records_repair_attention",
            "hold_decision_names_missing_evidence_and_next_trigger",
            "explicit_candidate_reject_with_repair_reason",
            "defer_due_to_safety_gate_with_repair_context",
        ],
        "hard_blocker": False,
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _compact_string_list(
    source: Any,
    *,
    limit: int,
    max_len: int,
) -> list[str]:
    return [
        str(item).strip()[:max_len]
        for item in list(source or [])[: max(int(limit), 0)]
        if str(item).strip()
    ]


def _compact_repair_targets(source: Any, *, limit: int = 8) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for row in list(source or [])[: max(int(limit), 0)]:
        if not isinstance(row, dict):
            continue
        compact = {
            key: str(row.get(key) or "").strip()[:180]
            for key in ("page_id", "symbol", "recommended_action")
            if str(row.get(key) or "").strip()
        }
        if compact:
            targets.append(compact)
    return targets


def _repair_target_page_id(target: Any) -> str:
    clean = str(target).strip().lower()
    clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in clean)
    clean = "_".join(part for part in clean.split("_") if part)
    return f"repair_target.{clean or 'unknown'}"


def _compact_repair_target_effectiveness(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    reasons = source.get("reasons")
    if reasons is None:
        raw_reasons = source.get("reasons_json")
        if isinstance(raw_reasons, str) and raw_reasons.strip():
            try:
                reasons = json.loads(raw_reasons)
            except json.JSONDecodeError:
                reasons = []
    compact = {
        "page_id": str(source.get("page_id") or ""),
        "status": str(source.get("status") or ""),
        "reasons": [
            str(item)[:180]
            for item in list(reasons or [])[:8]
            if str(item).strip()
        ],
    }
    for key in ("sample_count",):
        value = source.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_int(value)
    for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
        value = source.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _safe_float(value)
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _component_target_attention_item(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact = {
        "component": str(row.get("component") or "").strip()[:120],
        "decision_scope": str(row.get("decision_scope") or "").strip(),
        "status": str(row.get("status") or "").strip(),
        "target_status": str(row.get("target_status") or "").strip(),
        "priority_type": str(row.get("priority_type") or "").strip(),
        "action_type": str(row.get("action_type") or "").strip(),
        "recommended_resolution": str(
            row.get("recommended_resolution") or ""
        ).strip()[:180],
        "impacted_page_ids": _compact_string_list(
            row.get("impacted_page_ids"),
            limit=6,
            max_len=180,
        ),
        "impacted_symbols": _compact_string_list(
            row.get("impacted_symbols"),
            limit=8,
            max_len=40,
        ),
        "repair_targets": _compact_repair_targets(
            row.get("repair_targets"),
            limit=4,
        ),
    }
    for key in ("sample_count", "missed_count", "resolved_count"):
        if key in row and row.get(key) not in (None, ""):
            compact[key] = _safe_int(row.get(key))
    if "resolution_rate" in row and row.get("resolution_rate") not in (None, ""):
        compact["resolution_rate"] = _safe_float(row.get("resolution_rate"))
    compact["quality_warnings"] = _compact_string_list(
        row.get("quality_warnings"),
        limit=6,
        max_len=120,
    )
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _component_target_attention_plan(
    *,
    repair_required_detail: Any,
    probe_detail: Any,
) -> dict[str, Any]:
    repair_now = _component_target_attention_item(repair_required_detail)
    probe_next = _component_target_attention_item(probe_detail)
    status = "repair_required" if repair_now else "probe" if probe_next else ""
    plan = {
        "status": status,
        "repair_now": repair_now,
        "probe_next": probe_next,
    }
    return {
        key: value
        for key, value in plan.items()
        if value not in ("", [], {}, None)
    }


def _add_repair_loop_prompt_metrics(
    compact: dict[str, Any],
    row: dict[str, Any],
    *,
    include_loop_metrics: bool = False,
) -> None:
    for key in ("sample_count", "missed_count", "resolved_count"):
        if _has_prompt_value(row.get(key)):
            compact[key] = _safe_int(row.get(key))
    if _has_prompt_value(row.get("resolution_rate")):
        compact["resolution_rate"] = _safe_float(row.get("resolution_rate"))
    if include_loop_metrics:
        for key in ("loop_sample_count", "loop_missed_count", "loop_resolved_count"):
            if _has_prompt_value(row.get(key)):
                compact[key] = _safe_int(row.get(key))
        if _has_prompt_value(row.get("loop_resolution_rate")):
            compact["loop_resolution_rate"] = _safe_float(
                row.get("loop_resolution_rate")
            )


def _compact_repair_loop_effectiveness(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    top_degraded: list[dict[str, Any]] = []
    top_degraded_rows = [
        row for row in list(source.get("top_degraded") or []) if isinstance(row, dict)
    ]
    top_degraded_rows.sort(key=_repair_loop_effectiveness_row_sort_key)
    for row in top_degraded_rows[:5]:
        if not isinstance(row, dict):
            continue
        compact = {
            "decision_scope": str(row.get("decision_scope") or ""),
            "priority_type": str(row.get("priority_type") or ""),
            "action_type": str(row.get("action_type") or ""),
            "decision_use": str(row.get("decision_use") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "status": str(row.get("status") or ""),
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=8,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
        }
        _add_repair_loop_prompt_metrics(compact, row)
        top_degraded.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    repair_loop_status_metrics: list[dict[str, Any]] = []
    status_metric_rows = [
        row
        for row in list(source.get("repair_loop_status_metrics") or [])
        if isinstance(row, dict)
    ]
    status_metric_rows.sort(key=_repair_loop_effectiveness_row_sort_key)
    repair_loop_status_summary = _repair_loop_status_summary(status_metric_rows)
    for row in status_metric_rows[:5]:
        if not isinstance(row, dict):
            continue
        compact = {
            "decision_scope": str(row.get("decision_scope") or ""),
            "repair_loop_status": str(row.get("repair_loop_status") or ""),
            "priority_type": str(row.get("priority_type") or ""),
            "action_type": str(row.get("action_type") or ""),
            "decision_use": str(row.get("decision_use") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "status": str(row.get("status") or ""),
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=8,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
        }
        _add_repair_loop_prompt_metrics(compact, row, include_loop_metrics=True)
        repair_loop_status_metrics.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    repair_success_criteria_metrics: list[dict[str, Any]] = []
    success_criteria_rows = [
        row
        for row in list(source.get("repair_success_criteria_metrics") or [])
        if isinstance(row, dict)
    ]
    success_criteria_rows.sort(key=_repair_success_criteria_metric_sort_key)
    repair_success_criteria_summary = _repair_success_criteria_summary(
        success_criteria_rows
    )
    for row in success_criteria_rows[:5]:
        compact = {
            "decision_scope": str(row.get("decision_scope") or ""),
            "criterion": str(row.get("criterion") or "")[:180],
            "priority_type": str(row.get("priority_type") or ""),
            "action_type": str(row.get("action_type") or ""),
            "decision_use": str(row.get("decision_use") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "status": str(row.get("status") or ""),
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=8,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
        }
        _add_repair_loop_prompt_metrics(compact, row)
        repair_success_criteria_metrics.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    repair_learning_directive_metrics: list[dict[str, Any]] = []
    directive_metric_rows = [
        row
        for row in list(source.get("repair_learning_directive_metrics") or [])
        if isinstance(row, dict)
    ]
    if not directive_metric_rows:
        directive_metric_rows = (
            _repair_learning_directive_metrics_from_success_criteria_rows(
                success_criteria_rows
            )
        )
    directive_metric_rows.sort(key=_repair_learning_directive_metric_sort_key)
    repair_learning_directive_summary = _repair_learning_directive_summary(
        directive_metric_rows
    )
    for row in directive_metric_rows[:5]:
        compact = {
            "decision_scope": str(row.get("decision_scope") or ""),
            "recommended_action": str(row.get("recommended_action") or "")[:180],
            "priority_type": str(row.get("priority_type") or ""),
            "action_type": str(row.get("action_type") or ""),
            "decision_use": str(row.get("decision_use") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "status": str(row.get("status") or ""),
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=8,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
        }
        _add_repair_loop_prompt_metrics(compact, row)
        repair_learning_directive_metrics.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    repair_learning_step_metrics: list[dict[str, Any]] = []
    step_metric_rows = [
        row
        for row in list(source.get("repair_learning_step_metrics") or [])
        if isinstance(row, dict)
    ]
    if not step_metric_rows:
        source_directive_summary = (
            source.get("repair_learning_directive_summary")
            if isinstance(source.get("repair_learning_directive_summary"), dict)
            else repair_learning_directive_summary
        )
        step_metric_rows = _repair_learning_step_metrics_from_action_targets(
            source_directive_summary
        )
    step_metric_rows.sort(key=_repair_learning_step_metric_sort_key)
    repair_learning_step_summary = _repair_learning_step_summary(step_metric_rows)
    for row in step_metric_rows[:5]:
        compact = {
            "decision_scope": str(row.get("decision_scope") or ""),
            "resolution_step": str(row.get("resolution_step") or "")[:180],
            "priority_type": str(row.get("priority_type") or ""),
            "action_type": str(row.get("action_type") or ""),
            "decision_use": str(row.get("decision_use") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "status": str(row.get("status") or ""),
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=8,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
        }
        _add_repair_loop_prompt_metrics(compact, row)
        repair_learning_step_metrics.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    repair_learning_resolution_metrics: list[dict[str, Any]] = []
    resolution_metric_rows = [
        row
        for row in list(source.get("repair_learning_resolution_metrics") or [])
        if isinstance(row, dict)
    ]
    if not resolution_metric_rows:
        source_step_summary = (
            source.get("repair_learning_step_summary")
            if isinstance(source.get("repair_learning_step_summary"), dict)
            else repair_learning_step_summary
        )
        resolution_metric_rows = _repair_learning_resolution_metrics_from_step_targets(
            source_step_summary
        )
    resolution_metric_rows.sort(key=_repair_learning_resolution_metric_sort_key)
    repair_learning_resolution_summary = _repair_learning_resolution_summary(
        resolution_metric_rows
    )
    for row in resolution_metric_rows[:5]:
        compact = {
            "decision_scope": str(row.get("decision_scope") or ""),
            "recommended_resolution": str(
                row.get("recommended_resolution") or ""
            )[:180],
            "priority_type": str(row.get("priority_type") or ""),
            "action_type": str(row.get("action_type") or ""),
            "decision_use": str(row.get("decision_use") or ""),
            "source_id": str(row.get("source_id") or "")[:180],
            "status": str(row.get("status") or ""),
            "quality_warnings": _compact_string_list(
                row.get("quality_warnings"),
                limit=8,
                max_len=120,
            ),
            "impacted_page_ids": _compact_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_repair_targets(row.get("repair_targets")),
            "repair_target_effectiveness": _compact_repair_target_effectiveness(
                row.get("repair_target_effectiveness")
            ),
        }
        _add_repair_loop_prompt_metrics(compact, row)
        repair_learning_resolution_metrics.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    component_target_metric_rows = [
        row
        for row in list(source.get("repair_component_target_metrics") or [])
        if isinstance(row, dict)
    ]
    component_target_metric_rows.sort(key=_repair_loop_effectiveness_row_sort_key)
    repair_component_target_metrics = _compact_component_targets(
        component_target_metric_rows,
        limit=5,
    )
    repair_component_target_summary = _compact_repair_component_target_summary(
        source.get("repair_component_target_summary")
    ) or _repair_component_target_summary(component_target_metric_rows)
    if (
        repair_component_target_summary
        and repair_component_target_metrics
        and not repair_component_target_summary.get("top_component_target_details")
    ):
        repair_component_target_summary = {
            **repair_component_target_summary,
            "top_component_target_details": repair_component_target_metrics[:3],
        }
    if repair_component_target_summary and repair_component_target_metrics:
        if not repair_component_target_summary.get(
            "repair_required_component_target_details"
        ):
            repair_required_details = [
                row
                for row in repair_component_target_metrics
                if str(row.get("status") or "").strip().lower() == "repair_required"
            ][:3]
            if repair_required_details:
                repair_component_target_summary = {
                    **repair_component_target_summary,
                    "repair_required_component_target_details": (
                        repair_required_details
                    ),
                }
        if not repair_component_target_summary.get("probe_component_target_details"):
            probe_details = [
                row
                for row in repair_component_target_metrics
                if str(row.get("status") or "").strip().lower() == "probe"
            ][:3]
            if probe_details:
                repair_component_target_summary = {
                    **repair_component_target_summary,
                    "probe_component_target_details": probe_details,
                }
        if (
            not repair_component_target_summary.get(
                "primary_repair_required_component_target_detail"
            )
            and repair_component_target_summary.get(
                "repair_required_component_target_details"
            )
        ):
            repair_component_target_summary = {
                **repair_component_target_summary,
                "primary_repair_required_component_target_detail": (
                    repair_component_target_summary[
                        "repair_required_component_target_details"
                    ][0]
                ),
            }
        if (
            not repair_component_target_summary.get(
                "primary_probe_component_target_detail"
            )
            and repair_component_target_summary.get("probe_component_target_details")
        ):
            repair_component_target_summary = {
                **repair_component_target_summary,
                "primary_probe_component_target_detail": (
                    repair_component_target_summary["probe_component_target_details"][0]
                ),
            }
        if not repair_component_target_summary.get("component_target_attention_plan"):
            repair_component_target_summary = {
                **repair_component_target_summary,
                "component_target_attention_plan": _component_target_attention_plan(
                    repair_required_detail=repair_component_target_summary.get(
                        "primary_repair_required_component_target_detail"
                    ),
                    probe_detail=repair_component_target_summary.get(
                        "primary_probe_component_target_detail"
                    ),
                ),
            }
    component_status_summary = _compact_repair_component_status_summary(
        source.get("component_status_summary")
    )
    if not component_status_summary:
        component_status_summary = _compact_repair_component_status_summary(
            _repair_component_status_summary_from_metrics(
                [
                    ("repair_priority_metrics", top_degraded_rows),
                    ("repair_loop_status_metrics", status_metric_rows),
                    (
                        "repair_success_criteria_metrics",
                        success_criteria_rows,
                    ),
                    (
                        "repair_learning_directive_metrics",
                        directive_metric_rows,
                    ),
                    ("repair_learning_step_metrics", step_metric_rows),
                    (
                        "repair_learning_resolution_metrics",
                        resolution_metric_rows,
                    ),
                    (
                        "repair_component_target_metrics",
                        component_target_metric_rows,
                    ),
                ]
            )
        )
    compact_source = {
        "status": _repair_prompt_worst_status(
            str(source.get("status") or ""),
            str(component_status_summary.get("worst_status") or ""),
        ),
        "top_degraded": top_degraded,
        "repair_loop_status_metrics": repair_loop_status_metrics,
        "repair_loop_status_summary": repair_loop_status_summary,
        "repair_success_criteria_metrics": repair_success_criteria_metrics,
        "repair_success_criteria_summary": repair_success_criteria_summary,
        "repair_learning_directive_metrics": repair_learning_directive_metrics,
        "repair_learning_directive_summary": repair_learning_directive_summary,
        "repair_learning_step_metrics": repair_learning_step_metrics,
        "repair_learning_step_summary": repair_learning_step_summary,
        "repair_learning_resolution_metrics": repair_learning_resolution_metrics,
        "repair_learning_resolution_summary": repair_learning_resolution_summary,
        "repair_component_target_metrics": repair_component_target_metrics,
        "repair_component_target_summary": repair_component_target_summary,
        "component_status_summary": component_status_summary,
    }
    _add_repair_loop_prompt_metrics(compact_source, source)
    for key in ("metric_count", "repair_required_count"):
        if _has_prompt_value(source.get(key)):
            compact_source[key] = _safe_int(source.get(key))
    return {
        key: value
        for key, value in compact_source.items()
        if value not in ("", [], {}, None)
    }


def _repair_prompt_worst_status(*statuses: str) -> str:
    ranked = [
        str(status or "").strip().lower()
        for status in statuses
        if str(status or "").strip()
    ]
    if "repair_required" in ranked:
        return "repair_required"
    if "probe" in ranked:
        return "probe"
    if "active" in ranked:
        return "active"
    if ranked:
        return ranked[0][:80]
    return ""


def _compact_repair_component_target_summary(source: Any) -> dict[str, Any]:
    summary = source if isinstance(source, dict) else {}
    if not summary:
        return {}
    primary_repair_required_detail = _compact_component_targets(
        [summary.get("primary_repair_required_component_target_detail")],
        limit=1,
    )
    primary_probe_detail = _compact_component_targets(
        [summary.get("primary_probe_component_target_detail")],
        limit=1,
    )
    attention_plan = (
        summary.get("component_target_attention_plan")
        if isinstance(summary.get("component_target_attention_plan"), dict)
        else {}
    )
    if attention_plan:
        attention_plan = _component_target_attention_plan(
            repair_required_detail=attention_plan.get("repair_now"),
            probe_detail=attention_plan.get("probe_next"),
        )
    if not attention_plan:
        attention_plan = _component_target_attention_plan(
            repair_required_detail=next(iter(primary_repair_required_detail), None),
            probe_detail=next(iter(primary_probe_detail), None),
        )
    compact = {
        "worst_status": str(summary.get("worst_status") or "")[:80],
        "primary_component_target": str(
            summary.get("primary_component_target") or ""
        )[:120],
        "top_component_targets": _compact_string_list(
            summary.get("top_component_targets"),
            limit=8,
            max_len=120,
        ),
        "top_component_target_details": _compact_component_targets(
            summary.get("top_component_target_details"),
            limit=3,
        ),
        "repair_required_component_target_details": _compact_component_targets(
            summary.get("repair_required_component_target_details"),
            limit=3,
        ),
        "primary_repair_required_component_target_detail": next(
            iter(primary_repair_required_detail),
            None,
        ),
        "probe_component_target_details": _compact_component_targets(
            summary.get("probe_component_target_details"),
            limit=3,
        ),
        "primary_probe_component_target_detail": next(
            iter(primary_probe_detail),
            None,
        ),
        "component_target_attention_plan": attention_plan,
    }
    for key in (
        "metric_count",
        "repair_required_count",
        "probe_count",
        "active_count",
        "unknown_count",
        "max_missed_count",
        "max_sample_count",
    ):
        if _has_prompt_value(summary.get(key)):
            compact[key] = _safe_int(summary.get(key))
    if _has_prompt_value(summary.get("min_resolution_rate")):
        compact["min_resolution_rate"] = _safe_float(
            summary.get("min_resolution_rate")
        )
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _repair_component_target_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    sorted_rows = sorted(rows, key=_repair_loop_effectiveness_row_sort_key)
    repair_required_count = sum(
        1
        for row in rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    )
    probe_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "probe"
    )
    active_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "active"
    )
    unknown_count = len(rows) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    top_components: list[str] = []
    for row in sorted_rows:
        component = str(row.get("component") or "").strip()[:120]
        if component and component not in top_components:
            top_components.append(component)
        if len(top_components) >= 5:
            break
    repair_required_rows = [
        row
        for row in sorted_rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    ]
    probe_rows = [
        row
        for row in sorted_rows
        if str(row.get("status") or "").strip().lower() == "probe"
    ]
    top_details = _compact_component_targets(sorted_rows, limit=3)
    repair_required_details = _compact_component_targets(repair_required_rows, limit=3)
    probe_details = _compact_component_targets(probe_rows, limit=3)
    primary_repair_required_detail = next(iter(repair_required_details), None)
    primary_probe_detail = next(iter(probe_details), None)
    compact = {
        "metric_count": len(rows),
        "repair_required_count": repair_required_count,
        "probe_count": probe_count,
        "active_count": active_count,
        "unknown_count": unknown_count,
        "worst_status": worst_status,
        "primary_component_target": next(iter(top_components), None),
        "top_component_targets": top_components,
        "top_component_target_details": top_details,
        "repair_required_component_target_details": repair_required_details,
        "primary_repair_required_component_target_detail": next(
            iter(repair_required_details),
            None,
        ),
        "probe_component_target_details": probe_details,
        "primary_probe_component_target_detail": primary_probe_detail,
        "component_target_attention_plan": _component_target_attention_plan(
            repair_required_detail=primary_repair_required_detail,
            probe_detail=primary_probe_detail,
        ),
        "max_missed_count": _max_present_int(rows, "missed_count"),
        "max_sample_count": _max_present_int(rows, "sample_count"),
        "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in ("", [], {}, None)
    }


def _repair_component_status_summary_from_metrics(
    components: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for component, metrics in components:
        if not metrics:
            continue
        row = _repair_metric_status_counts(metrics)
        row["component"] = component
        component_targets = _repair_component_targets(metrics)
        if component_targets:
            row["component_targets"] = component_targets
        rows.append(row)
    if not rows:
        return {}
    repair_required_components = [
        str(row.get("component") or "")
        for row in rows
        if _safe_int(row.get("repair_required_count")) > 0
    ]
    probe_components = [
        str(row.get("component") or "")
        for row in rows
        if _safe_int(row.get("probe_count")) > 0
    ]
    repair_required_count = sum(
        _safe_int(row.get("repair_required_count")) for row in rows
    )
    probe_count = sum(_safe_int(row.get("probe_count")) for row in rows)
    active_count = sum(_safe_int(row.get("active_count")) for row in rows)
    unknown_count = sum(_safe_int(row.get("unknown_count")) for row in rows)
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    return {
        key: value
        for key, value in {
            "component_count": len(rows),
            "metric_count": sum(_safe_int(row.get("metric_count")) for row in rows),
            "repair_required_count": repair_required_count,
            "probe_count": probe_count,
            "active_count": active_count,
            "unknown_count": unknown_count,
            "worst_status": worst_status,
            "repair_required_components": repair_required_components,
            "probe_components": probe_components,
            "components": rows,
            "top_component_targets": _repair_top_component_targets(rows),
            "repair_required_component_targets": _repair_top_component_targets(
                rows,
                statuses={"repair_required"},
            ),
            "probe_component_targets": _repair_top_component_targets(
                rows,
                statuses={"probe"},
            ),
        }.items()
        if value not in ("", [], {}, None)
    }


def _repair_metric_status_counts(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [str(row.get("status") or "").strip().lower() for row in metrics]
    repair_required_count = sum(1 for status in statuses if status == "repair_required")
    probe_count = sum(1 for status in statuses if status == "probe")
    active_count = sum(1 for status in statuses if status == "active")
    unknown_count = len(statuses) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    return {
        "metric_count": len(metrics),
        "repair_required_count": repair_required_count,
        "probe_count": probe_count,
        "active_count": active_count,
        "unknown_count": unknown_count,
        "worst_status": worst_status,
    }


def _repair_component_targets(
    metrics: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    candidates = [row for row in metrics if isinstance(row, dict)]
    candidates.sort(
        key=lambda row: (
            _repair_loop_status_rank(str(row.get("status") or "")),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("action_type") or ""),
            str(row.get("recommended_action") or ""),
            str(row.get("resolution_step") or ""),
            str(row.get("recommended_resolution") or ""),
            str(row.get("criterion") or ""),
        )
    )
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in candidates:
        target: dict[str, Any] = {
            "decision_scope": str(row.get("decision_scope") or "").strip(),
            "status": str(row.get("status") or "").strip(),
            "target_status": str(row.get("target_status") or "").strip(),
            "priority_type": str(row.get("priority_type") or "").strip(),
            "action_type": str(row.get("action_type") or "").strip(),
            "criterion": str(row.get("criterion") or "").strip()[:180],
            "recommended_action": str(row.get("recommended_action") or "").strip()[
                :180
            ],
            "resolution_step": str(row.get("resolution_step") or "").strip()[:180],
            "recommended_resolution": str(
                row.get("recommended_resolution") or ""
            ).strip()[:180],
            "sample_count": _safe_int(row.get("sample_count")),
            "missed_count": _safe_int(row.get("missed_count")),
            "resolved_count": _safe_int(row.get("resolved_count")),
            "resolution_rate": _safe_float(row.get("resolution_rate")),
        }
        _merge_repair_context_into_target(target, row)
        _finalize_repair_context_target(target)
        compact = {
            key: value
            for key, value in target.items()
            if value not in (None, "", [], {})
        }
        marker = (
            str(compact.get("decision_scope") or ""),
            str(compact.get("status") or ""),
            str(compact.get("action_type") or ""),
            str(compact.get("criterion") or ""),
            str(compact.get("recommended_action") or ""),
            str(compact.get("resolution_step") or ""),
            str(compact.get("recommended_resolution") or ""),
        )
        if not compact or marker in seen:
            continue
        seen.add(marker)
        targets.append(compact)
        if len(targets) >= max(int(limit), 0):
            break
    return targets


def _compact_component_targets(source: Any, *, limit: int = 3) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for row in list(source or [])[: max(int(limit), 0)]:
        if not isinstance(row, dict):
            continue
        compact = {
            "component": str(row.get("component") or "").strip()[:120],
            "decision_scope": str(row.get("decision_scope") or "").strip(),
            "status": str(row.get("status") or "").strip(),
            "target_status": str(row.get("target_status") or "").strip(),
            "priority_type": str(row.get("priority_type") or "").strip(),
            "action_type": str(row.get("action_type") or "").strip(),
            "criterion": str(row.get("criterion") or "").strip()[:180],
            "recommended_action": str(row.get("recommended_action") or "").strip()[
                :180
            ],
            "resolution_step": str(row.get("resolution_step") or "").strip()[:180],
            "recommended_resolution": str(
                row.get("recommended_resolution") or ""
            ).strip()[:180],
        }
        _add_repair_loop_prompt_metrics(compact, row)
        _merge_repair_context_into_target(compact, row)
        _finalize_repair_context_target(compact)
        targets.append(
            {
                key: value
                for key, value in compact.items()
                if value not in (None, "", [], {})
            }
        )
    return targets


def _repair_top_component_targets(
    components: list[dict[str, Any]],
    *,
    limit: int = 5,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    clean_statuses = {
        str(status or "").strip().lower()
        for status in (statuses or set())
        if str(status or "").strip()
    }
    for component in components:
        component_name = str(component.get("component") or "").strip()[:120]
        for target in _compact_component_targets(
            component.get("component_targets"),
            limit=3,
        ):
            target_status = str(target.get("status") or "").strip().lower()
            if clean_statuses and target_status not in clean_statuses:
                continue
            if component_name and "component" not in target:
                target["component"] = component_name
            marker = (
                str(target.get("component") or ""),
                str(target.get("decision_scope") or ""),
                str(target.get("status") or ""),
                str(target.get("action_type") or ""),
                str(target.get("criterion") or ""),
                str(target.get("recommended_action") or ""),
                str(target.get("resolution_step") or ""),
                str(target.get("recommended_resolution") or ""),
            )
            if marker in seen:
                continue
            seen.add(marker)
            targets.append(target)
    targets.sort(
        key=lambda row: (
            _repair_loop_status_rank(str(row.get("status") or "")),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("component") or ""),
        )
    )
    return targets[: max(int(limit), 0)]


def _compact_repair_component_status_summary(source: Any) -> dict[str, Any]:
    summary = source if isinstance(source, dict) else {}
    if not summary:
        return {}
    components: list[dict[str, Any]] = []
    for row in list(summary.get("components") or [])[:5]:
        if not isinstance(row, dict):
            continue
        compact = {
            "component": str(row.get("component") or "")[:120],
            "worst_status": str(row.get("worst_status") or "")[:80],
            "component_targets": _compact_component_targets(
                row.get("component_targets")
            ),
        }
        for key in (
            "metric_count",
            "repair_required_count",
            "probe_count",
            "active_count",
            "unknown_count",
        ):
            if _has_prompt_value(row.get(key)):
                compact[key] = _safe_int(row.get(key))
        components.append(
            {
                key: value
                for key, value in compact.items()
                if value not in ("", [], {}, None)
            }
        )
    compact_summary = {
        "worst_status": str(summary.get("worst_status") or "")[:80],
        "repair_required_components": [
            str(item)[:120]
            for item in list(summary.get("repair_required_components") or [])[:8]
            if str(item).strip()
        ],
        "probe_components": [
            str(item)[:120]
            for item in list(summary.get("probe_components") or [])[:8]
            if str(item).strip()
        ],
        "components": components,
        "top_component_targets": _compact_component_targets(
            summary.get("top_component_targets")
        )
        or _repair_top_component_targets(components),
        "repair_required_component_targets": _compact_component_targets(
            summary.get("repair_required_component_targets")
        )
        or _repair_top_component_targets(components, statuses={"repair_required"}),
        "probe_component_targets": _compact_component_targets(
            summary.get("probe_component_targets")
        )
        or _repair_top_component_targets(components, statuses={"probe"}),
    }
    for key in (
        "component_count",
        "metric_count",
        "repair_required_count",
        "probe_count",
        "active_count",
        "unknown_count",
    ):
        if _has_prompt_value(summary.get(key)):
            compact_summary[key] = _safe_int(summary.get(key))
    return {
        key: value
        for key, value in compact_summary.items()
        if value not in ("", [], {}, None)
    }


def _repair_loop_status_rank(status: str) -> int:
    clean_status = str(status or "").strip().lower()
    if clean_status == "repair_required":
        return 0
    if clean_status == "probe":
        return 1
    if clean_status == "active":
        return 2
    return 3


def _repair_loop_effectiveness_row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _repair_loop_status_rank(str(row.get("status") or "")),
        -_safe_int(row.get("missed_count")),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("resolution_rate")),
        str(row.get("action_type") or ""),
        str(row.get("source_id") or ""),
    )


def _repair_success_criteria_metric_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _repair_loop_status_rank(str(row.get("status") or "")),
        -_safe_int(row.get("missed_count")),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("resolution_rate")),
        str(row.get("criterion") or ""),
        str(row.get("decision_scope") or ""),
    )


def _repair_learning_directive_metric_sort_key(
    row: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        _repair_loop_status_rank(str(row.get("status") or "")),
        -_safe_int(row.get("missed_count")),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("resolution_rate")),
        str(row.get("recommended_action") or ""),
        str(row.get("decision_scope") or ""),
    )


def _repair_learning_step_metric_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _repair_loop_status_rank(str(row.get("status") or "")),
        -_safe_int(row.get("missed_count")),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("resolution_rate")),
        str(row.get("resolution_step") or ""),
        str(row.get("decision_scope") or ""),
    )


def _repair_learning_resolution_metric_sort_key(
    row: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        _repair_loop_status_rank(str(row.get("status") or "")),
        -_safe_int(row.get("missed_count")),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("resolution_rate")),
        str(row.get("recommended_resolution") or ""),
        str(row.get("decision_scope") or ""),
    )


def _max_present_int(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [
        _safe_int(row.get(key))
        for row in rows
        if _has_prompt_value(row.get(key))
    ]
    return max(values) if values else None


def _min_present_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [
        _safe_float(row.get(key))
        for row in rows
        if _has_prompt_value(row.get(key))
    ]
    return min(values) if values else None


def _accumulate_present_int_metric(
    target: dict[str, Any],
    row: dict[str, Any],
    key: str,
    *,
    mode: str = "sum",
) -> None:
    if not _has_prompt_value(row.get(key)):
        return
    incoming = _safe_int(row.get(key))
    if mode == "max":
        if _has_prompt_value(target.get(key)):
            target[key] = max(_safe_int(target.get(key)), incoming)
        else:
            target[key] = incoming
        return
    current = _safe_int(target.get(key)) if _has_prompt_value(target.get(key)) else 0
    target[key] = current + incoming


def _attach_present_target_rates(
    target: dict[str, Any],
    *,
    round_miss_rate: bool = False,
) -> None:
    has_sample = _has_prompt_value(target.get("sample_count"))
    has_missed = _has_prompt_value(target.get("missed_count"))
    has_resolved = _has_prompt_value(target.get("resolved_count"))
    if has_sample and has_resolved:
        target["resolution_rate"] = _ratio(
            _safe_int(target.get("resolved_count")),
            _safe_int(target.get("sample_count")),
        )
    if has_sample and has_missed:
        miss_rate = _ratio(
            _safe_int(target.get("missed_count")),
            _safe_int(target.get("sample_count")),
        )
        target["miss_rate"] = round(miss_rate, 6) if round_miss_rate else miss_rate
        target["repair_pressure_score"] = round(
            _safe_int(target.get("missed_count")) * miss_rate,
            6,
        )


def _repair_learning_directive_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    repair_required_count = sum(
        1
        for row in rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    )
    probe_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "probe"
    )
    active_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "active"
    )
    unknown_count = len(rows) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    repair_required_actions = _repair_learning_directive_actions(
        rows,
        status="repair_required",
    )
    top_missed_actions = _repair_learning_directive_actions(
        rows,
        only_max_missed=True,
    )
    primary_recommended_action = next(
        iter(repair_required_actions or top_missed_actions),
        None,
    )
    return {
        key: value
        for key, value in {
            "metric_count": len(rows),
            "repair_required_count": repair_required_count,
            "probe_count": probe_count,
            "active_count": active_count,
            "unknown_count": unknown_count,
            "worst_status": worst_status,
            "primary_recommended_action": primary_recommended_action,
            "repair_required_actions": repair_required_actions,
            "top_missed_actions": top_missed_actions,
            "action_targets": _repair_learning_directive_action_targets(rows),
            "max_missed_count": _max_present_int(rows, "missed_count"),
            "max_sample_count": _max_present_int(rows, "sample_count"),
            "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
        }.items()
        if value not in (None, "", [], {})
    }


def _repair_learning_directive_actions(
    rows: list[dict[str, Any]],
    *,
    status: str | None = None,
    only_max_missed: bool = False,
    limit: int = 5,
) -> list[str]:
    candidates = [
        row
        for row in rows
        if str(row.get("recommended_action") or "").strip()
        and (
            status is None
            or str(row.get("status") or "").strip().lower()
            == str(status).strip().lower()
        )
    ]
    if only_max_missed and candidates:
        candidates = [
            row for row in candidates if _has_prompt_value(row.get("missed_count"))
        ]
        if not candidates:
            return []
        max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
        candidates = [
            row
            for row in candidates
            if _safe_int(row.get("missed_count")) == max_missed
        ]
    candidates.sort(key=_repair_learning_directive_metric_sort_key)
    actions: list[str] = []
    for row in candidates:
        action = str(row.get("recommended_action") or "").strip()
        if action and action not in actions:
            actions.append(action[:180])
        if len(actions) >= max(int(limit), 0):
            break
    return actions


def _repair_learning_step_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    repair_required_count = sum(
        1
        for row in rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    )
    probe_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "probe"
    )
    active_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "active"
    )
    unknown_count = len(rows) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    repair_required_steps = _repair_learning_resolution_steps(
        rows,
        status="repair_required",
    )
    top_missed_steps = _repair_learning_resolution_steps(
        rows,
        only_max_missed=True,
    )
    primary_resolution_step = next(
        iter(repair_required_steps or top_missed_steps),
        None,
    )
    return {
        key: value
        for key, value in {
            "metric_count": len(rows),
            "repair_required_count": repair_required_count,
            "probe_count": probe_count,
            "active_count": active_count,
            "unknown_count": unknown_count,
            "worst_status": worst_status,
            "primary_resolution_step": primary_resolution_step,
            "repair_required_steps": repair_required_steps,
            "top_missed_steps": top_missed_steps,
            "step_targets": _repair_learning_step_targets(rows),
            "max_missed_count": _max_present_int(rows, "missed_count"),
            "max_sample_count": _max_present_int(rows, "sample_count"),
            "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
        }.items()
        if value not in (None, "", [], {})
    }


def _repair_learning_resolution_steps(
    rows: list[dict[str, Any]],
    *,
    status: str | None = None,
    only_max_missed: bool = False,
    limit: int = 5,
) -> list[str]:
    candidates = [
        row
        for row in rows
        if str(row.get("resolution_step") or "").strip()
        and (
            status is None
            or str(row.get("status") or "").strip().lower()
            == str(status).strip().lower()
        )
    ]
    if only_max_missed and candidates:
        candidates = [
            row for row in candidates if _has_prompt_value(row.get("missed_count"))
        ]
        if not candidates:
            return []
        max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
        candidates = [
            row
            for row in candidates
            if _safe_int(row.get("missed_count")) == max_missed
        ]
    candidates.sort(key=_repair_learning_step_metric_sort_key)
    steps: list[str] = []
    for row in candidates:
        step = str(row.get("resolution_step") or "").strip()
        if step and step not in steps:
            steps.append(step[:180])
        if len(steps) >= max(int(limit), 0):
            break
    return steps


def _repair_learning_step_targets(
    rows: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        step = str(row.get("resolution_step") or "").strip()
        if not step:
            continue
        key = (
            str(row.get("decision_scope") or "").strip(),
            str(row.get("status") or "").strip(),
            step,
        )
        target = grouped.setdefault(
            key,
            {
                "decision_scope": key[0],
                "status": key[1],
                "resolution_step": step,
                "metric_count": 0,
            },
        )
        _merge_repair_context_into_target(target, row)
        for metric_key in ("sample_count", "missed_count", "resolved_count"):
            _accumulate_present_int_metric(target, row, metric_key)
        target["metric_count"] += 1
    targets: list[dict[str, Any]] = []
    for target in grouped.values():
        _attach_present_target_rates(target)
        target["recommended_resolution"] = "revise_repair_step_contract_then_probe"
        target["resolution_steps"] = [
            "inspect_failed_resolution_step_outcomes",
            "revise_repair_step_contract",
            "record_next_outcome_before_reuse",
        ]
        _finalize_repair_context_target(target)
        targets.append(
            {
                key: value
                for key, value in target.items()
                if value not in (None, "", [], {})
            }
        )
    targets.sort(
        key=lambda row: (
            _repair_loop_status_rank(str(row.get("status") or "")),
            -_safe_int(row.get("missed_count")),
            -_safe_float(row.get("repair_pressure_score")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("resolution_step") or ""),
        )
    )
    return targets[: max(int(limit), 0)]


def _repair_learning_resolution_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    repair_required_count = sum(
        1
        for row in rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    )
    probe_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "probe"
    )
    active_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "active"
    )
    unknown_count = len(rows) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    sorted_rows = sorted(rows, key=_repair_learning_resolution_metric_sort_key)
    repair_required_resolutions = _repair_learning_recommended_resolutions(
        sorted_rows,
        status="repair_required",
    )
    top_missed_resolutions = _repair_learning_recommended_resolutions(
        sorted_rows,
        only_max_missed=True,
    )
    primary_recommended_resolution = next(
        iter(repair_required_resolutions or top_missed_resolutions),
        None,
    )
    return {
        key: value
        for key, value in {
            "metric_count": len(rows),
            "repair_required_count": repair_required_count,
            "probe_count": probe_count,
            "active_count": active_count,
            "unknown_count": unknown_count,
            "worst_status": worst_status,
            "primary_recommended_resolution": primary_recommended_resolution,
            "repair_required_resolutions": repair_required_resolutions,
            "top_missed_resolutions": top_missed_resolutions,
            "resolution_targets": _repair_learning_resolution_targets(sorted_rows),
            "max_missed_count": _max_present_int(rows, "missed_count"),
            "max_sample_count": _max_present_int(rows, "sample_count"),
            "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
        }.items()
        if value not in (None, "", [], {})
    }


def _repair_learning_recommended_resolutions(
    rows: list[dict[str, Any]],
    *,
    status: str | None = None,
    only_max_missed: bool = False,
    limit: int = 5,
) -> list[str]:
    candidates = [
        row
        for row in rows
        if str(row.get("recommended_resolution") or "").strip()
        and (
            status is None
            or str(row.get("status") or "").strip().lower()
            == str(status).strip().lower()
        )
    ]
    if only_max_missed and candidates:
        candidates = [
            row for row in candidates if _has_prompt_value(row.get("missed_count"))
        ]
        if not candidates:
            return []
        max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
        candidates = [
            row
            for row in candidates
            if _safe_int(row.get("missed_count")) == max_missed
        ]
    candidates.sort(key=_repair_learning_resolution_metric_sort_key)
    resolutions: list[str] = []
    for row in candidates:
        resolution = str(row.get("recommended_resolution") or "").strip()
        if resolution and resolution not in resolutions:
            resolutions.append(resolution[:180])
        if len(resolutions) >= max(int(limit), 0):
            break
    return resolutions


def _repair_learning_resolution_targets(
    rows: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        resolution = str(row.get("recommended_resolution") or "").strip()
        if not resolution:
            continue
        key = (
            str(row.get("decision_scope") or "").strip(),
            str(row.get("status") or "").strip(),
            resolution,
        )
        target = grouped.setdefault(
            key,
            {
                "decision_scope": key[0],
                "status": key[1],
                "recommended_resolution": resolution,
                "metric_count": 0,
            },
        )
        _merge_repair_context_into_target(target, row)
        for metric_key in ("sample_count", "missed_count", "resolved_count"):
            _accumulate_present_int_metric(target, row, metric_key)
        target["metric_count"] += 1
    targets: list[dict[str, Any]] = []
    for target in grouped.values():
        _attach_present_target_rates(target)
        target["next_review_steps"] = [
            "inspect_failed_resolution_strategy_outcomes",
            "revise_resolution_strategy_contract",
            "record_next_outcome_before_reuse",
        ]
        _finalize_repair_context_target(target)
        targets.append(
            {
                key: value
                for key, value in target.items()
                if value not in (None, "", [], {})
            }
        )
    targets.sort(
        key=lambda row: (
            _repair_loop_status_rank(str(row.get("status") or "")),
            -_safe_int(row.get("missed_count")),
            -_safe_float(row.get("repair_pressure_score")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("recommended_resolution") or ""),
        )
    )
    return targets[: max(int(limit), 0)]


def _merge_repair_context_into_target(
    target: dict[str, Any],
    row: dict[str, Any],
) -> None:
    source_id = str(row.get("source_id") or "").strip()[:180]
    if source_id:
        source_ids = target.setdefault("source_ids", [])
        if source_id not in source_ids:
            source_ids.append(source_id)
    for source_id in _compact_string_list(
        row.get("source_ids"),
        limit=3,
        max_len=180,
    ):
        source_ids = target.setdefault("source_ids", [])
        if source_id not in source_ids:
            source_ids.append(source_id)
    decision_use = str(row.get("decision_use") or "").strip()[:160]
    if decision_use:
        decision_uses = target.setdefault("decision_uses", [])
        if decision_use not in decision_uses:
            decision_uses.append(decision_use)
    primary_decision_use = str(row.get("primary_decision_use") or "").strip()[:160]
    if primary_decision_use:
        decision_uses = target.setdefault("decision_uses", [])
        if primary_decision_use not in decision_uses:
            decision_uses.append(primary_decision_use)
    priority_type = str(row.get("priority_type") or "").strip()[:120]
    if priority_type:
        priority_types = target.setdefault("priority_types", [])
        if priority_type not in priority_types:
            priority_types.append(priority_type)
    for priority_type in _compact_string_list(
        row.get("priority_types"),
        limit=3,
        max_len=120,
    ):
        priority_types = target.setdefault("priority_types", [])
        if priority_type not in priority_types:
            priority_types.append(priority_type)
    for warning in _compact_string_list(
        row.get("quality_warnings"),
        limit=8,
        max_len=120,
    ):
        quality_warnings = target.setdefault("quality_warnings", [])
        if warning not in quality_warnings:
            quality_warnings.append(warning)
    for page_id in _compact_string_list(
        row.get("impacted_page_ids"),
        limit=12,
        max_len=180,
    ):
        impacted_page_ids = target.setdefault("impacted_page_ids", [])
        if page_id not in impacted_page_ids:
            impacted_page_ids.append(page_id)
    for symbol in _compact_string_list(
        row.get("impacted_symbols"),
        limit=24,
        max_len=40,
    ):
        impacted_symbols = target.setdefault("impacted_symbols", [])
        if symbol not in impacted_symbols:
            impacted_symbols.append(symbol)
    for repair_target in _compact_repair_targets(
        row.get("repair_targets"),
        limit=8,
    ):
        repair_targets = target.setdefault("repair_targets", [])
        if repair_target not in repair_targets:
            repair_targets.append(repair_target)
    effectiveness = _compact_repair_target_effectiveness(
        row.get("repair_target_effectiveness")
    )
    effectiveness_rows = target.setdefault("repair_target_effectiveness", [])
    if effectiveness and effectiveness not in effectiveness_rows:
        effectiveness_rows.append(effectiveness)
    for source_effectiveness in list(row.get("repair_target_effectiveness") or []):
        if not isinstance(source_effectiveness, dict):
            continue
        compact_effectiveness = _compact_repair_target_effectiveness(
            source_effectiveness
        )
        if (
            compact_effectiveness
            and compact_effectiveness not in effectiveness_rows
        ):
            effectiveness_rows.append(compact_effectiveness)
    for effectiveness_row in effectiveness_rows:
        status = str(effectiveness_row.get("status") or "").strip()
        if status:
            statuses = target.setdefault("repair_target_effectiveness_statuses", [])
            if status not in statuses:
                statuses.append(status)
    for status in _compact_string_list(
        row.get("repair_target_effectiveness_statuses"),
        limit=8,
        max_len=80,
    ):
        statuses = target.setdefault("repair_target_effectiveness_statuses", [])
        if status not in statuses:
            statuses.append(status)


def _finalize_repair_context_target(target: dict[str, Any]) -> None:
    source_ids = list(target.pop("source_ids", []) or [])
    if len(source_ids) == 1:
        target["source_id"] = source_ids[0]
    elif source_ids:
        target["source_ids"] = source_ids[:3]
    decision_uses = list(target.get("decision_uses") or [])
    if decision_uses:
        target["primary_decision_use"] = decision_uses[0]
        target["decision_uses"] = decision_uses[:3]
    priority_types = list(target.get("priority_types") or [])
    if priority_types:
        target["priority_types"] = priority_types[:3]
    quality_warnings = list(target.get("quality_warnings") or [])
    if quality_warnings:
        target["quality_warnings"] = quality_warnings[:8]
    impacted_page_ids = list(target.get("impacted_page_ids") or [])
    if impacted_page_ids:
        target["impacted_page_ids"] = impacted_page_ids[:12]
    impacted_symbols = list(target.get("impacted_symbols") or [])
    if impacted_symbols:
        target["impacted_symbols"] = impacted_symbols[:24]
    repair_targets = list(target.get("repair_targets") or [])
    if repair_targets:
        target["repair_targets"] = repair_targets[:8]
    repair_target_effectiveness = list(
        target.get("repair_target_effectiveness") or []
    )
    if repair_target_effectiveness:
        target["repair_target_effectiveness"] = repair_target_effectiveness[:8]
    effectiveness_statuses = list(
        target.get("repair_target_effectiveness_statuses") or []
    )
    if effectiveness_statuses:
        target["repair_target_effectiveness_statuses"] = (
            effectiveness_statuses[:8]
        )


def _finalize_repair_context_metric(metric: dict[str, Any]) -> None:
    source_ids = list(metric.pop("source_ids", []) or [])
    if len(source_ids) == 1:
        metric["source_id"] = source_ids[0]
    elif source_ids:
        metric["source_ids"] = source_ids[:3]
    decision_uses = list(metric.pop("decision_uses", []) or [])
    if len(decision_uses) == 1:
        metric["decision_use"] = decision_uses[0]
    elif decision_uses:
        metric["decision_uses"] = decision_uses[:3]
    priority_types = list(metric.pop("priority_types", []) or [])
    if len(priority_types) == 1:
        metric["priority_type"] = priority_types[0]
    elif priority_types:
        metric["priority_types"] = priority_types[:3]
    quality_warnings = list(metric.get("quality_warnings") or [])
    if quality_warnings:
        metric["quality_warnings"] = quality_warnings[:8]
    impacted_page_ids = list(metric.get("impacted_page_ids") or [])
    if impacted_page_ids:
        metric["impacted_page_ids"] = impacted_page_ids[:12]
    impacted_symbols = list(metric.get("impacted_symbols") or [])
    if impacted_symbols:
        metric["impacted_symbols"] = impacted_symbols[:24]
    repair_targets = list(metric.get("repair_targets") or [])
    if repair_targets:
        metric["repair_targets"] = repair_targets[:8]
    repair_target_effectiveness = list(
        metric.get("repair_target_effectiveness") or []
    )
    if len(repair_target_effectiveness) == 1:
        metric["repair_target_effectiveness"] = repair_target_effectiveness[0]
    elif repair_target_effectiveness:
        metric["repair_target_effectiveness"] = repair_target_effectiveness[:8]
    effectiveness_statuses = list(
        metric.get("repair_target_effectiveness_statuses") or []
    )
    if effectiveness_statuses:
        metric["repair_target_effectiveness_statuses"] = (
            effectiveness_statuses[:8]
        )


def _repair_learning_step_metrics_from_action_targets(
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    source = summary if isinstance(summary, dict) else {}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for target in list(source.get("action_targets") or []):
        if not isinstance(target, dict):
            continue
        steps = [
            str(step).strip()
            for step in list(target.get("resolution_steps") or [])
            if str(step).strip()
        ]
        for step in steps:
            key = (
                str(target.get("decision_scope") or "").strip(),
                str(target.get("status") or "").strip(),
                step,
            )
            metric = grouped.setdefault(
                key,
                {
                    "decision_scope": key[0],
                    "status": key[1],
                    "resolution_step": step,
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                },
            )
            _merge_repair_context_into_target(metric, target)
            metric["sample_count"] += _safe_int(target.get("sample_count"))
            metric["missed_count"] += _safe_int(target.get("missed_count"))
            metric["resolved_count"] += _safe_int(target.get("resolved_count"))
    rows: list[dict[str, Any]] = []
    for metric in grouped.values():
        sample_count = _safe_int(metric.get("sample_count"))
        missed_count = _safe_int(metric.get("missed_count"))
        resolved_count = _safe_int(metric.get("resolved_count"))
        resolution_rate = _ratio(resolved_count, sample_count)
        status = str(metric.get("status") or "").strip()
        if not status:
            if sample_count < 3:
                status = "probe"
            elif resolution_rate >= 0.5 and resolved_count >= missed_count:
                status = "active"
            else:
                status = "repair_required"
        _finalize_repair_context_metric(metric)
        rows.append(
            {
                key: value
                for key, value in {
                    **metric,
                    "resolution_rate": resolution_rate,
                    "status": status,
                }.items()
                if value not in (None, "", [], {})
            }
        )
    return rows


def _repair_learning_resolution_metrics_from_step_targets(
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    source = summary if isinstance(summary, dict) else {}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for target in list(source.get("step_targets") or []):
        if not isinstance(target, dict):
            continue
        resolution = str(target.get("recommended_resolution") or "").strip()
        if not resolution:
            continue
        key = (
            str(target.get("decision_scope") or "").strip(),
            str(target.get("status") or "").strip(),
            resolution,
        )
        metric = grouped.setdefault(
            key,
            {
                "decision_scope": key[0],
                "status": key[1],
                "recommended_resolution": resolution,
                "sample_count": 0,
                "missed_count": 0,
                "resolved_count": 0,
            },
        )
        _merge_repair_context_into_target(metric, target)
        metric["sample_count"] = max(
            _safe_int(metric.get("sample_count")),
            _safe_int(target.get("sample_count")),
        )
        metric["missed_count"] = max(
            _safe_int(metric.get("missed_count")),
            _safe_int(target.get("missed_count")),
        )
        metric["resolved_count"] = max(
            _safe_int(metric.get("resolved_count")),
            _safe_int(target.get("resolved_count")),
        )
    rows: list[dict[str, Any]] = []
    for metric in grouped.values():
        sample_count = _safe_int(metric.get("sample_count"))
        missed_count = _safe_int(metric.get("missed_count"))
        resolved_count = _safe_int(metric.get("resolved_count"))
        resolution_rate = _ratio(resolved_count, sample_count)
        status = str(metric.get("status") or "").strip()
        if not status:
            if sample_count < 3:
                status = "probe"
            elif resolution_rate >= 0.5 and resolved_count >= missed_count:
                status = "active"
            else:
                status = "repair_required"
        _finalize_repair_context_metric(metric)
        rows.append(
            {
                key: value
                for key, value in {
                    **metric,
                    "resolution_rate": resolution_rate,
                    "status": status,
                }.items()
                if value not in (None, "", [], {})
            }
        )
    return rows


def _repair_learning_directive_action_targets(
    rows: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        action = str(row.get("recommended_action") or "").strip()
        if not action:
            continue
        key = (
            str(row.get("decision_scope") or "").strip(),
            str(row.get("status") or "").strip(),
            action,
        )
        target = grouped.setdefault(
            key,
            {
                "decision_scope": key[0],
                "status": key[1],
                "recommended_action": action,
                "metric_count": 0,
            },
        )
        _merge_repair_context_into_target(target, row)
        for metric_key in ("sample_count", "missed_count", "resolved_count"):
            _accumulate_present_int_metric(target, row, metric_key)
        target["metric_count"] += 1
    targets: list[dict[str, Any]] = []
    for target in grouped.values():
        _attach_present_target_rates(target)
        target["recommended_resolution"] = "revise_learning_directive_then_probe"
        target["resolution_steps"] = [
            "inspect_failed_repair_directive_outcomes",
            "revise_or_demote_learning_directive",
            "record_next_outcome_before_reuse",
        ]
        _finalize_repair_context_target(target)
        targets.append(
            {
                key: value
                for key, value in target.items()
                if value not in (None, "", [], {})
            }
        )
    targets.sort(
        key=lambda row: (
            _repair_loop_status_rank(str(row.get("status") or "")),
            -_safe_int(row.get("missed_count")),
            -_safe_float(row.get("repair_pressure_score")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("recommended_action") or ""),
        )
    )
    return targets[: max(int(limit), 0)]


def _repair_learning_directive_metrics_from_success_criteria_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        directives = _repair_success_criteria_learning_directives(
            [row],
            limit=8,
        )
        for directive in directives:
            action = str(directive.get("recommended_action") or "").strip()
            if not action:
                continue
            key = (str(row.get("decision_scope") or "").strip(), action)
            metric = grouped.setdefault(
                key,
                {
                    "decision_scope": key[0],
                    "recommended_action": action,
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                },
            )
            _merge_repair_context_into_target(metric, row)
            metric["sample_count"] += _safe_int(row.get("sample_count"))
            metric["missed_count"] += _safe_int(row.get("missed_count"))
            metric["resolved_count"] += _safe_int(row.get("resolved_count"))
    metrics: list[dict[str, Any]] = []
    for metric in grouped.values():
        sample_count = _safe_int(metric.get("sample_count"))
        missed_count = _safe_int(metric.get("missed_count"))
        resolved_count = _safe_int(metric.get("resolved_count"))
        resolution_rate = _ratio(resolved_count, sample_count)
        if sample_count < 3:
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        _finalize_repair_context_metric(metric)
        metrics.append(
            {
                key: value
                for key, value in {
                    **metric,
                    "resolution_rate": resolution_rate,
                    "status": status,
                }.items()
                if value not in (None, "", [], {})
            }
        )
    return metrics


def _repair_success_criteria_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    repair_required_count = sum(
        1
        for row in rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    )
    probe_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "probe"
    )
    active_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "active"
    )
    unknown_count = len(rows) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    sorted_rows = sorted(rows, key=_repair_success_criteria_metric_sort_key)
    top_failed_criteria: list[str] = []
    for row in sorted_rows:
        criterion = str(row.get("criterion") or "").strip()
        if criterion and criterion not in top_failed_criteria:
            top_failed_criteria.append(criterion)
        if len(top_failed_criteria) >= 5:
            break
    repair_learning_directives = _repair_success_criteria_learning_directives(
        sorted_rows
    )
    return {
        key: value
        for key, value in {
            "metric_count": len(rows),
            "repair_required_count": repair_required_count,
            "probe_count": probe_count,
            "active_count": active_count,
            "unknown_count": unknown_count,
            "worst_status": worst_status,
            "primary_failed_criterion": next(iter(top_failed_criteria), None),
            "top_failed_criteria": top_failed_criteria,
            "repair_learning_directives": repair_learning_directives,
            "max_missed_count": _max_present_int(rows, "missed_count"),
            "max_sample_count": _max_present_int(rows, "sample_count"),
            "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
        }.items()
        if value not in (None, "", [], {})
    }


def _repair_success_criteria_learning_directives(
    rows: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    directives: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        criterion = str(row.get("criterion") or "").strip()
        if not criterion or criterion in seen:
            continue
        seen.add(criterion)
        status = str(row.get("status") or "").strip().lower()
        recommended_action = "keep_success_criterion_active"
        if status == "repair_required":
            recommended_action = "repair_or_demote_success_criterion_before_reuse"
        elif status == "probe":
            recommended_action = "collect_more_outcomes_before_policy_shift"
        directive = {
            "criterion": criterion,
            "status": status,
            "recommended_action": recommended_action,
        }
        if _has_prompt_value(row.get("missed_count")):
            directive["missed_count"] = _safe_int(row.get("missed_count"))
        if _has_prompt_value(row.get("resolution_rate")):
            directive["resolution_rate"] = _safe_float(row.get("resolution_rate"))
        directives.append(
            {
                key: value
                for key, value in directive.items()
                if value not in (None, "", [], {})
            }
        )
        if len(directives) >= max(int(limit), 0):
            break
    return directives


def _repair_loop_status_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    repair_required_count = sum(
        1
        for row in rows
        if str(row.get("status") or "").strip().lower() == "repair_required"
    )
    probe_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "probe"
    )
    active_count = sum(
        1 for row in rows if str(row.get("status") or "").strip().lower() == "active"
    )
    unknown_count = len(rows) - repair_required_count - probe_count - active_count
    worst_status = "active"
    if repair_required_count:
        worst_status = "repair_required"
    elif probe_count:
        worst_status = "probe"
    elif unknown_count and not active_count:
        worst_status = "unknown"
    repair_required_action_types = _repair_loop_action_types(
        rows,
        status="repair_required",
    )
    top_missed_action_types = _repair_loop_action_types(
        rows,
        only_max_missed=True,
    )
    primary_repair_action_type = next(
        iter(repair_required_action_types or top_missed_action_types),
        None,
    )
    repair_action_targets = _repair_loop_action_targets(rows)
    return {
        key: value
        for key, value in {
            "metric_count": len(rows),
            "repair_required_count": repair_required_count,
            "probe_count": probe_count,
            "active_count": active_count,
            "unknown_count": unknown_count,
            "worst_status": worst_status,
            "primary_repair_action_type": primary_repair_action_type,
            "repair_required_action_types": repair_required_action_types,
            "top_missed_action_types": top_missed_action_types,
            "repair_action_targets": repair_action_targets,
            "max_missed_count": _max_present_int(rows, "missed_count"),
            "max_sample_count": _max_present_int(rows, "sample_count"),
            "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
        }.items()
        if value not in (None, "", [], {})
    }


def _repair_loop_action_types(
    rows: list[dict[str, Any]],
    *,
    status: str | None = None,
    only_max_missed: bool = False,
    limit: int = 5,
) -> list[str]:
    candidates = [
        row
        for row in rows
        if str(row.get("action_type") or "").strip()
        and (
            status is None
            or str(row.get("status") or "").strip().lower() == status
        )
    ]
    if only_max_missed and candidates:
        candidates = [
            row for row in candidates if _has_prompt_value(row.get("missed_count"))
        ]
        if not candidates:
            return []
        max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
        candidates = [
            row
            for row in candidates
            if _safe_int(row.get("missed_count")) == max_missed
        ]
    candidates.sort(key=_repair_loop_effectiveness_row_sort_key)
    action_types: list[str] = []
    seen: set[str] = set()
    for row in candidates:
        action_type = str(row.get("action_type") or "").strip()
        if not action_type or action_type in seen:
            continue
        seen.add(action_type)
        action_types.append(action_type)
        if len(action_types) >= max(int(limit), 0):
            break
    return action_types


def _repair_loop_action_targets(
    rows: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if str(row.get("action_type") or "").strip()
        and str(row.get("status") or "").strip().lower() == "repair_required"
    ]
    if not candidates:
        candidates = [
            row for row in rows if str(row.get("action_type") or "").strip()
        ]
        metric_candidates = [
            row for row in candidates if _has_prompt_value(row.get("missed_count"))
        ]
        if metric_candidates:
            max_missed = max(
                _safe_int(row.get("missed_count")) for row in metric_candidates
            )
            candidates = [
                row
                for row in metric_candidates
                if _safe_int(row.get("missed_count")) == max_missed
            ]
        else:
            candidates = []
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in candidates:
        action_type = str(row.get("action_type") or "").strip()
        status = str(row.get("status") or "").strip()
        decision_scope = str(row.get("decision_scope") or "").strip()
        key = (decision_scope, status, action_type)
        if not action_type:
            continue
        target = grouped.setdefault(
            key,
            {
                "decision_scope": decision_scope,
                "status": status,
                "action_type": action_type,
                "metric_count": 0,
                "source_ids": [],
                "decision_uses": [],
                "priority_types": [],
                "quality_warnings": [],
                "impacted_page_ids": [],
                "impacted_symbols": [],
                "repair_targets": [],
                "repair_target_effectiveness": [],
                "repair_target_effectiveness_statuses": [],
            },
        )
        for metric_key in ("sample_count", "missed_count", "resolved_count"):
            _accumulate_present_int_metric(target, row, metric_key)
        target["metric_count"] += 1
        source_id = str(row.get("source_id") or "").strip()
        if source_id and source_id[:180] not in target["source_ids"]:
            target["source_ids"].append(source_id[:180])
        decision_use = str(row.get("decision_use") or "").strip()
        if decision_use and decision_use[:160] not in target["decision_uses"]:
            target["decision_uses"].append(decision_use[:160])
        priority_type = str(row.get("priority_type") or "").strip()
        if priority_type and priority_type[:120] not in target["priority_types"]:
            target["priority_types"].append(priority_type[:120])
        for warning in list(row.get("quality_warnings") or []):
            warning_text = str(warning).strip()[:120]
            if warning_text and warning_text not in target["quality_warnings"]:
                target["quality_warnings"].append(warning_text)
        for page_id in _compact_string_list(
            row.get("impacted_page_ids"),
            limit=12,
            max_len=180,
        ):
            if page_id not in target["impacted_page_ids"]:
                target["impacted_page_ids"].append(page_id)
        for symbol in _compact_string_list(
            row.get("impacted_symbols"),
            limit=24,
            max_len=40,
        ):
            if symbol not in target["impacted_symbols"]:
                target["impacted_symbols"].append(symbol)
        for repair_target in _compact_repair_targets(
            row.get("repair_targets"),
            limit=8,
        ):
            if repair_target not in target["repair_targets"]:
                target["repair_targets"].append(repair_target)
        effectiveness = _compact_repair_target_effectiveness(
            row.get("repair_target_effectiveness")
        )
        if effectiveness and effectiveness not in target[
            "repair_target_effectiveness"
        ]:
            target["repair_target_effectiveness"].append(effectiveness)
        effectiveness_status = str(effectiveness.get("status") or "").strip()
        if (
            effectiveness_status
            and effectiveness_status
            not in target["repair_target_effectiveness_statuses"]
        ):
            target["repair_target_effectiveness_statuses"].append(
                effectiveness_status
            )
    targets: list[dict[str, Any]] = []
    grouped_targets = list(grouped.values())
    for target in grouped_targets:
        _attach_present_target_rates(target, round_miss_rate=True)
        target["recommended_resolution"] = _repair_loop_recommended_resolution(
            str(target.get("action_type") or ""),
            str(target.get("decision_scope") or ""),
        )
        target["resolution_steps"] = _repair_loop_resolution_steps(
            str(target.get("action_type") or ""),
            str(target.get("decision_scope") or ""),
        )
        target["resolution_success_criteria"] = (
            _repair_loop_resolution_success_criteria(
                str(target.get("action_type") or ""),
                str(target.get("decision_scope") or ""),
            )
        )
    grouped_targets.sort(key=_repair_loop_action_target_sort_key)
    for target in grouped_targets:
        source_ids = list(target.pop("source_ids", []) or [])
        if len(source_ids) == 1:
            target["source_id"] = source_ids[0]
        elif source_ids:
            target["source_ids"] = source_ids[:3]
        decision_uses = list(target.get("decision_uses") or [])
        if decision_uses:
            target["primary_decision_use"] = decision_uses[0]
            target["decision_uses"] = decision_uses[:3]
        priority_types = list(target.get("priority_types") or [])
        if priority_types:
            target["priority_types"] = priority_types[:3]
        quality_warnings = list(target.get("quality_warnings") or [])
        if quality_warnings:
            target["quality_warnings"] = quality_warnings[:8]
        impacted_page_ids = list(target.get("impacted_page_ids") or [])
        if impacted_page_ids:
            target["impacted_page_ids"] = impacted_page_ids[:12]
        impacted_symbols = list(target.get("impacted_symbols") or [])
        if impacted_symbols:
            target["impacted_symbols"] = impacted_symbols[:24]
        repair_targets = list(target.get("repair_targets") or [])
        if repair_targets:
            target["repair_targets"] = repair_targets[:8]
        repair_target_effectiveness = list(
            target.get("repair_target_effectiveness") or []
        )
        if repair_target_effectiveness:
            target["repair_target_effectiveness"] = repair_target_effectiveness[:8]
        effectiveness_statuses = list(
            target.get("repair_target_effectiveness_statuses") or []
        )
        if effectiveness_statuses:
            target["repair_target_effectiveness_statuses"] = (
                effectiveness_statuses[:8]
            )
        targets.append(
            {
                target_key: value
                for target_key, value in target.items()
                if value not in (None, "", [], {})
            }
        )
        if len(targets) >= max(int(limit), 0):
            break
    return targets


def _repair_loop_action_target_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _repair_loop_status_rank(str(row.get("status") or "")),
        -_safe_int(row.get("missed_count")),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("resolution_rate")),
        str(row.get("action_type") or ""),
        str(row.get("decision_scope") or ""),
    )


def _repair_loop_recommended_resolution(action_type: str, decision_scope: str) -> str:
    clean_action = str(action_type or "").strip().lower()
    clean_scope = str(decision_scope or "").strip().lower()
    if (
        clean_action == "reproject_closed_block_outcome_horizons"
        or "outcome_horizon" in clean_action
    ):
        return "reproject_closed_block_outcomes_then_refresh_effectiveness"
    if clean_action.startswith("refresh_symbol_") or clean_action in {
        "repair_symbol_identity",
        "cross_check_evidence_quality",
    }:
        return "refresh_evidence_then_reprice_entry_exit"
    if "decision_adjustment_audit" in clean_action or "contract" in clean_action:
        return "repair_contract_then_probe_or_downgrade"
    if clean_scope == "binance" or "crypto" in clean_action:
        return "refresh_crypto_context_then_rebuild_executable_price"
    if "market" in clean_action or "regime" in clean_action:
        return "refresh_market_context_then_use_waiting_block"
    return "candidate_level_cross_check_before_reuse"


def _repair_loop_resolution_steps(
    action_type: str,
    decision_scope: str,
) -> list[str]:
    recommended = _repair_loop_recommended_resolution(action_type, decision_scope)
    if recommended == "reproject_closed_block_outcomes_then_refresh_effectiveness":
        return [
            "derive_block_horizon_or_crypto_lane",
            "reproject_selection_outcomes_with_horizon",
            "refresh_page_effectiveness_by_horizon",
        ]
    if recommended == "refresh_evidence_then_reprice_entry_exit":
        return [
            "refresh_required_evidence",
            "reprice_entry_target_stop",
            "downgrade_or_reject_if_still_missing",
        ]
    if recommended == "repair_contract_then_probe_or_downgrade":
        return [
            "repair_audit_contract",
            "use_probe_or_downgrade_until_resolved",
            "record_repair_outcome",
        ]
    if recommended == "refresh_crypto_context_then_rebuild_executable_price":
        return [
            "refresh_crypto_research_and_microstructure",
            "rebuild_executable_price_plan",
            "reject_if_depth_spread_funding_conflict",
        ]
    if recommended == "refresh_market_context_then_use_waiting_block":
        return [
            "refresh_regime_and_flow_context",
            "prefer_waiting_block_until_context_confirms",
            "record_regime_repair_outcome",
        ]
    return [
        "cross_check_candidate_evidence",
        "downgrade_memory_weight_if_unresolved",
        "record_repair_outcome",
    ]


def _repair_loop_resolution_success_criteria(
    action_type: str,
    decision_scope: str,
) -> list[str]:
    recommended = _repair_loop_recommended_resolution(action_type, decision_scope)
    if recommended == "reproject_closed_block_outcomes_then_refresh_effectiveness":
        return [
            "closed_block_outcomes_have_horizon_or_lane",
            "page_effectiveness_separated_by_horizon",
            "future_manager_prompt_uses_horizon_specific_effectiveness",
        ]
    if recommended == "refresh_evidence_then_reprice_entry_exit":
        return [
            "required_evidence_present",
            "entry_target_stop_repriced",
            "unresolved_gap_downgraded_or_rejected",
        ]
    if recommended == "repair_contract_then_probe_or_downgrade":
        return [
            "audit_contract_repaired_or_demoted",
            "probe_or_downgrade_result_recorded",
            "future_reuse_has_outcome_link",
        ]
    if recommended == "refresh_crypto_context_then_rebuild_executable_price":
        return [
            "crypto_context_refreshed",
            "executable_price_plan_present",
            "depth_spread_funding_conflict_checked",
        ]
    if recommended == "refresh_market_context_then_use_waiting_block":
        return [
            "market_regime_context_refreshed",
            "waiting_block_used_when_unconfirmed",
            "regime_repair_outcome_recorded",
        ]
    return [
        "candidate_evidence_cross_checked",
        "memory_weight_downgraded_if_unresolved",
        "repair_outcome_recorded",
    ]


def _jue_wiki_repair_priority_decision_use(row: dict[str, Any]) -> str:
    action_type = str(row.get("action_type") or "").strip()
    priority_type = str(row.get("priority_type") or "").strip()
    page_id = str(row.get("page_id") or "").strip()
    if (
        action_type == "repair_decision_adjustment_audit_contract"
        or priority_type == "decision_adjustment_audit"
    ):
        return "decision_adjustment_audit_repair"
    if action_type == "repair_quality_warning_effectiveness" or page_id.startswith(
        "quality_warning."
    ):
        return "quality_warning_effectiveness_repair"
    if (
        action_type == "repair_usage_guidance_contract"
        or priority_type == "usage_guidance_effectiveness"
        or page_id.startswith("usage_guidance.")
    ):
        return "usage_guidance_effectiveness_repair"
    if action_type == "refresh_requested_symbol_summary":
        return "requested_symbol_summary_repair"
    if (
        action_type == "reproject_closed_block_outcome_horizons"
        or priority_type == "wiki_application_coverage"
    ):
        return "horizon_lane_attribution_repair"
    if priority_type == "evidence_quality":
        return "evidence_quality_cross_check"
    if priority_type == "repair_queue":
        return "repair_queue_execution_check"
    if priority_type == "wiki_attention":
        return "wiki_attention_resolution_check"
    if priority_type == "memory_card_quality":
        return "memory_card_quality_resolution_check"
    return "degraded_memory_repair"


def _quality_warning_metric_prompt_row(
    *,
    warning: str,
    page_id: str,
    metric: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "warning": warning,
        "page_id": page_id,
    }
    status = str(metric.get("status") or "").strip()
    if status:
        row["status"] = status
    if _metric_has_prompt_value(metric, "sample_count"):
        row["sample_count"] = _safe_int(metric.get("sample_count"))
    for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
        if _metric_has_prompt_value(metric, key):
            row[key] = _safe_float(metric.get(key))
    reasons = [
        str(item)[:180]
        for item in JueWikiSelector._metric_reasons(metric)[:8]
        if str(item).strip()
    ]
    if reasons:
        row["reasons"] = reasons
    return {
        key: value
        for key, value in row.items()
        if value not in ("", [], {}, None)
    }


@dataclass(frozen=True)
class JueWikiSelectionRequest:
    target_scope: str
    symbols: list[str] = field(default_factory=list)
    page_types: list[str] = field(default_factory=list)
    lanes: list[str] = field(default_factory=list)
    regimes: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    horizons: list[str] = field(default_factory=list)
    max_chars: int = 24_000
    max_pages: int = 12
    min_confidence: float = 0.15
    exclude_lint_warnings: bool = False
    effectiveness_weight: float = 0.0
    effectiveness_max_adjustment: float = 0.0


@dataclass(frozen=True)
class JueWikiSelectedPage:
    page_id: str
    rank: int
    score: float
    reasons: list[str]
    penalties: list[str]
    char_count: int
    content: str
    source_refs: list[dict[str, Any]]
    effectiveness: dict[str, Any] = field(default_factory=dict)
    evidence_quality: dict[str, Any] = field(default_factory=dict)
    quality_status: str = ""
    quality_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JueWikiSelectionResult:
    status: str
    selection_run_id: str
    target_scope: str
    pages: list[JueWikiSelectedPage]
    rejected_pages: list[dict[str, Any]]
    content: str
    budget_report: dict[str, Any]
    effectiveness_policy: dict[str, Any] = field(default_factory=dict)
    repair_priorities: list[dict[str, Any]] = field(default_factory=list)
    repair_action_batches: list[dict[str, Any]] = field(default_factory=list)
    evidence_quality: dict[str, Any] = field(default_factory=dict)
    requested_symbol_summaries: list[dict[str, Any]] = field(default_factory=list)
    mode_recommendation: dict[str, Any] = field(default_factory=dict)
    trust_profile_effectiveness: dict[str, Any] = field(default_factory=dict)
    repair_priority_effectiveness: dict[str, Any] = field(default_factory=dict)
    validation_repair_effectiveness: dict[str, Any] = field(default_factory=dict)
    wiki_application_coverage: dict[str, Any] = field(default_factory=dict)


_PROMPT_MODES = {"observe", "assist", "primary"}


def resolve_jue_wiki_prompt_mode(
    configured_mode: str,
    mode_recommendation: dict[str, Any] | None,
) -> dict[str, Any]:
    configured = str(configured_mode or "assist").strip().lower()
    if configured not in _PROMPT_MODES:
        configured = "assist"
    recommendation = mode_recommendation if isinstance(mode_recommendation, dict) else {}
    recommended = str(recommendation.get("recommended_mode") or "").strip().lower()
    if recommended not in _PROMPT_MODES:
        return {
            "prompt_mode": configured,
            "configured_prompt_mode": configured,
            "mode_recommendation": recommendation,
            "prompt_mode_policy": {
                "source": "configured",
                "reason": "no_usable_mode_recommendation",
            },
        }
    confidence = _safe_float(recommendation.get("confidence"))
    sample_count = _safe_int(recommendation.get("sample_count"))
    reasons = [
        str(reason)
        for reason in list(recommendation.get("reasons") or [])
        if str(reason).strip()
    ]
    applies = False
    reason = "recommendation_below_application_threshold"
    if recommended == "primary" and confidence >= 0.70 and sample_count >= 20:
        applies = True
        reason = "validated_primary_recommendation"
    elif recommended == "assist" and confidence >= 0.50 and sample_count >= 20:
        applies = True
        reason = "validated_assist_recommendation"
    elif recommended == "observe" and confidence >= 0.65:
        if sample_count >= 20:
            applies = True
            reason = "validated_observe_recommendation"
        elif any(
            marker in reason_text
            for reason_text in reasons
            for marker in (
                "stale_effectiveness_removed",
                "no_attributable_metrics",
                "degraded",
            )
        ):
            applies = True
            reason = "protective_observe_after_cleanup"
    prompt_mode = recommended if applies else configured
    prompt_mode_policy: dict[str, Any] = {
        "source": "mode_recommendation" if applies else "configured",
        "recommended_mode": recommended,
        "reason": reason,
    }
    if _has_prompt_value(recommendation.get("confidence")):
        prompt_mode_policy["confidence"] = confidence
    if _has_prompt_value(recommendation.get("sample_count")):
        prompt_mode_policy["sample_count"] = sample_count
    return {
        "prompt_mode": prompt_mode,
        "configured_prompt_mode": configured,
        "mode_recommendation": recommendation,
        "prompt_mode_policy": prompt_mode_policy,
    }


def build_jue_wiki_trust_profile_for_prompt(
    jue_wiki: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = jue_wiki if isinstance(jue_wiki, dict) else {}
    mode = str(payload.get("prompt_mode") or "").strip().lower()
    if mode not in _PROMPT_MODES:
        mode = "assist"
    recommendation = (
        payload.get("mode_recommendation")
        if isinstance(payload.get("mode_recommendation"), dict)
        else {}
    )
    policy = (
        payload.get("prompt_mode_policy")
        if isinstance(payload.get("prompt_mode_policy"), dict)
        else {}
    )
    configured = str(payload.get("configured_prompt_mode") or "").strip().lower()
    recommended = str(recommendation.get("recommended_mode") or "").strip().lower()
    reasons = _compact_unique_strings(recommendation.get("reasons"), limit=6)
    primary_degraded = any(
        "prompt_mode_effectiveness:primary:degraded" in reason
        for reason in reasons
    )
    authority_by_mode = {
        "primary": "primary_compiled_knowledge",
        "assist": "supporting_evidence",
        "observe": "observation_only",
    }
    trust_by_mode = {"primary": "high", "assist": "medium", "observe": "low"}
    decision_use_by_mode = {
        "primary": (
            "use selected wiki pages as the compiled knowledge spine, then "
            "cross-check live execution data before block changes"
        ),
        "assist": (
            "use selected wiki pages as supporting evidence alongside live "
            "quotes, account state, research, and risk gates"
        ),
        "observe": (
            "inspect selected wiki pages for risk, repair, and audit context; "
            "do not let memory alone drive block changes"
        ),
    }
    posture = "configured_mode"
    if primary_degraded:
        posture = "primary_demoted_after_underperformance"
    elif str(policy.get("source") or "") == "mode_recommendation":
        posture = "validated_mode_recommendation"
    profile = {
        "prompt_mode": mode,
        "authority": authority_by_mode[mode],
        "trust_level": trust_by_mode[mode],
        "decision_use": decision_use_by_mode[mode],
        "posture": posture,
    }
    authority_effectiveness = _matching_trust_authority_effectiveness(
        payload.get("trust_profile_effectiveness"),
        authority=authority_by_mode[mode],
    )
    if authority_effectiveness:
        profile["authority_effectiveness"] = authority_effectiveness
    profile["usage_contract"] = _jue_wiki_usage_contract(
        authority=authority_by_mode[mode],
        trust_level=trust_by_mode[mode],
        effectiveness=authority_effectiveness,
    )
    if configured:
        profile["configured_prompt_mode"] = configured
    if recommended:
        profile["recommended_mode"] = recommended
    recommendation_id = str(recommendation.get("recommendation_id") or "").strip()
    if recommendation_id:
        profile["recommendation_id"] = recommendation_id
    if _has_prompt_value(recommendation.get("sample_count")):
        profile["sample_count"] = _safe_int(recommendation.get("sample_count"))
    if _has_prompt_value(recommendation.get("confidence")):
        profile["confidence"] = _safe_float(recommendation.get("confidence"))
    if reasons:
        profile["reasons"] = reasons
    policy_reason = str(policy.get("reason") or "").strip()
    if policy_reason:
        profile["policy_reason"] = policy_reason
    return profile


def build_jue_wiki_decision_adjustments_for_prompt(
    trust_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    profile = trust_profile if isinstance(trust_profile, dict) else {}
    usage_contract = (
        profile.get("usage_contract")
        if isinstance(profile.get("usage_contract"), dict)
        else {}
    )
    guidance = (
        usage_contract.get("risk_posture_guidance")
        if isinstance(usage_contract.get("risk_posture_guidance"), dict)
        else {}
    )
    adjustment = (
        guidance.get("decision_adjustment")
        if isinstance(guidance.get("decision_adjustment"), dict)
        else {}
    )
    if not adjustment:
        return []
    compact: dict[str, Any] = {
        "source": "usage_contract.risk_posture_guidance",
    }
    for key in ("action", "target_risk_posture", "reason"):
        value = str(adjustment.get(key) or "").strip()
        if value:
            compact[key] = value[:180]
    for key in ("current_risk_posture", "current_status"):
        value = str(guidance.get(key) or "").strip()
        if value:
            compact[key] = value[:120]
    for key in ("recommended_allowed_uses", "deprioritized_allowed_uses"):
        values = _compact_unique_strings(guidance.get(key), limit=8)
        if values:
            compact[key] = values
    effectiveness = _compact_decision_adjustment_effectiveness(
        guidance.get("decision_adjustment_effectiveness")
    )
    if effectiveness:
        compact["decision_adjustment_effectiveness"] = effectiveness
    audit_effectiveness = _compact_decision_adjustment_effectiveness(
        guidance.get("decision_adjustment_audit_effectiveness")
    )
    if audit_effectiveness:
        compact["decision_adjustment_audit_effectiveness"] = audit_effectiveness
    evidence_grade = _decision_adjustment_evidence_grade(
        effectiveness=effectiveness,
        audit_effectiveness=audit_effectiveness,
    )
    if evidence_grade:
        compact["evidence_grade"] = evidence_grade
    audit_policy = (
        guidance.get("decision_adjustment_audit_policy")
        if isinstance(guidance.get("decision_adjustment_audit_policy"), dict)
        else {}
    )
    if audit_policy:
        compact["decision_adjustment_audit_policy"] = {
            key: value
            for key, value in {
                "action": str(audit_policy.get("action") or "")[:180],
                "reason": str(audit_policy.get("reason") or "")[:180],
                "target_risk_posture": str(
                    audit_policy.get("target_risk_posture") or ""
                )[:120],
                "hard_blocker": bool(audit_policy.get("hard_blocker")),
            }.items()
            if value not in (None, "", [], {})
        }
    return [compact] if compact.get("action") else []


def build_jue_wiki_decision_adjustment_audit_contract_for_prompt(
    application: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = application if isinstance(application, dict) else {}
    adjustments = payload.get("decision_adjustments")
    adjustment_rows = adjustments if isinstance(adjustments, list) else []
    audit_adjustments = [
        row
        for row in adjustment_rows
        if isinstance(row, dict)
        and str(row.get("action") or "").strip().lower()
        == "audit_preferred_risk_posture_before_shift"
    ]
    if not audit_adjustments:
        return {}
    target_risk_postures = list(
        dict.fromkeys(
            target
            for target in (
                str(row.get("target_risk_posture") or "").strip()
                for row in audit_adjustments
            )
            if target
        )
    )
    audit_policies = [
        compact
        for compact in (
            _compact_decision_adjustment_audit_policy(
                row.get("decision_adjustment_audit_policy")
            )
            for row in audit_adjustments
        )
        if compact
    ]
    audit_effectiveness = [
        compact
        for compact in (
            _compact_decision_adjustment_effectiveness(
                row.get("decision_adjustment_audit_effectiveness")
            )
            for row in audit_adjustments
        )
        if compact
    ]
    contract = {
        "version": "jue_wiki_decision_adjustment_audit_contract_v1",
        "status": "repair_required" if audit_policies else "active",
        "adjustment_count": len(audit_adjustments),
        "actions": ["audit_preferred_risk_posture_before_shift"],
        "target_risk_postures": target_risk_postures,
        "required_review": [
            "verify why prior shift_to_preferred_risk_posture underperformed",
            "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture",
            "if evidence remains weak, use repair probe, waiting block, or explicit rejection instead of direct escalation",
        ],
        "accepted_resolutions": [
            "adopt target risk posture with explicit live evidence override",
            "create a smaller repair probe or waiting block",
            "keep current posture and record what evidence is missing",
            "reject the shift and create a wiki repair note",
        ],
        "hard_blocker": False,
        "safety_gates_still_override": True,
    }
    if audit_policies:
        contract["audit_policies"] = list(
            {
                json.dumps(policy, sort_keys=True, ensure_ascii=False): policy
                for policy in audit_policies
            }.values()
        )
    if audit_effectiveness:
        contract["audit_effectiveness"] = audit_effectiveness
    return contract


def _compact_decision_adjustment_audit_policy(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact = {
        "action": str(row.get("action") or "").strip()[:180],
        "reason": str(row.get("reason") or "").strip()[:180],
        "target_risk_posture": str(row.get("target_risk_posture") or "").strip()[
            :120
        ],
        "hard_blocker": bool(row.get("hard_blocker")),
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _compact_decision_adjustment_effectiveness(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact: dict[str, Any] = {}
    for key in ("action", "target_risk_posture", "reason", "status"):
        value = str(row.get(key) or "").strip()
        if value:
            compact[key] = value[:180]
    for key in (
        "sample_count",
        "win_rate",
        "avg_return_pct",
        "helpful_score",
        "confidence",
    ):
        value = row.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    for key in ("evidence_grade_counts", "evidence_grade_instruction_counts"):
        counts = _compact_decision_adjustment_grade_counts(row.get(key))
        if counts:
            compact[key] = counts
    grade_performance = _compact_decision_adjustment_grade_performance(
        row.get("evidence_grade_performance")
    )
    if grade_performance:
        compact["evidence_grade_performance"] = grade_performance
    execution_hint = _decision_adjustment_execution_hint(compact)
    if execution_hint:
        compact["execution_hint"] = execution_hint
    return compact


def _compact_decision_adjustment_grade_counts(source: Any) -> dict[str, int]:
    rows = source if isinstance(source, dict) else {}
    counts: dict[str, int] = {}
    for key, value in rows.items():
        clean_key = str(key or "").strip().lower()[:80]
        if not clean_key:
            continue
        clean_value = _safe_int(value)
        if clean_value:
            counts[clean_key] = clean_value
    return dict(sorted(counts.items()))


def _compact_decision_adjustment_grade_performance(
    source: Any,
) -> list[dict[str, Any]]:
    rows = source if isinstance(source, list) else []
    compact_rows: list[dict[str, Any]] = []
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        compact: dict[str, Any] = {}
        for key in ("status", "instruction", "basis"):
            value = str(row.get(key) or "").strip().lower()
            if value:
                compact[key] = value[:120]
        for key in ("sample_count", "win_rate", "avg_return_pct"):
            value = row.get(key)
            if value not in (None, "", [], {}):
                compact[key] = value
        if compact:
            compact_rows.append(compact)
    return compact_rows


def _decision_adjustment_execution_hint(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "").strip().lower()
    avg_return = _safe_float(row.get("avg_return_pct"))
    grade_counts = (
        row.get("evidence_grade_counts")
        if isinstance(row.get("evidence_grade_counts"), dict)
        else {}
    )
    instruction_counts = (
        row.get("evidence_grade_instruction_counts")
        if isinstance(row.get("evidence_grade_instruction_counts"), dict)
        else {}
    )
    negative_count = _safe_int(grade_counts.get("negative"))
    positive_count = _safe_int(grade_counts.get("positive"))
    audit_count = _safe_int(instruction_counts.get("audit_or_repair_probe_only"))
    live_count = _safe_int(instruction_counts.get("usable_with_live_cross_check"))
    if not any((negative_count, positive_count, audit_count, live_count)):
        return ""
    if status == "degraded" or avg_return < 0:
        if negative_count >= positive_count or audit_count >= live_count:
            return "cap_to_audit_or_repair_probe"
        return "reduce_size_and_require_live_cross_check"
    if (
        status == "active"
        and avg_return > 0
        and positive_count >= negative_count
        and (positive_count > 0 or live_count > 0)
    ):
        return "allow_live_cross_checked_execution"
    return ""


def _decision_adjustment_evidence_grade(
    *,
    effectiveness: dict[str, Any],
    audit_effectiveness: dict[str, Any],
) -> dict[str, Any]:
    basis = "decision_adjustment_audit_effectiveness" if audit_effectiveness else (
        "decision_adjustment_effectiveness" if effectiveness else ""
    )
    source = audit_effectiveness or effectiveness
    if not basis or not source:
        return {}
    sample_count = _safe_int(source.get("sample_count"))
    avg_return = _safe_float(source.get("avg_return_pct"))
    confidence = _safe_float(source.get("confidence"))
    source_status = str(source.get("status") or "").strip().lower()
    negative_statuses = {"degraded", "negative", "loss", "underperforming"}
    positive_statuses = {"active", "positive", "profitable", "helpful"}
    if sample_count < 3:
        status = "thin_sample"
        instruction = "probe_only_until_more_samples"
    elif source_status in negative_statuses or avg_return < 0:
        status = "negative"
        instruction = "audit_or_repair_probe_only"
    elif source_status in positive_statuses and avg_return >= 0:
        status = "positive"
        instruction = "usable_with_live_cross_check"
    else:
        status = "unproven"
        instruction = "require_live_cross_check"
    result: dict[str, Any] = {
        "status": status,
        "basis": basis,
        "sample_count": sample_count,
        "avg_return_pct": avg_return,
        "confidence": confidence,
        "instruction": instruction,
    }
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [], {})
    }


def _jue_wiki_usage_contract(
    *,
    authority: str,
    trust_level: str,
    effectiveness: dict[str, Any],
) -> dict[str, Any]:
    status = str(effectiveness.get("status") or "unproven").strip().lower()
    if status in {"degraded", "negative", "loss"}:
        risk_posture = "repair_probe"
        allowed_uses = [
            "repair_candidate_design",
            "small_probe_block",
            "waiting_block",
            "candidate_level_reject",
        ]
    elif authority == "primary_compiled_knowledge" and trust_level == "high":
        risk_posture = "knowledge_spine"
        allowed_uses = [
            "block_thesis_design",
            "target_stop_design",
            "sizing_context",
            "candidate_ranking",
        ]
    elif authority == "supporting_evidence":
        risk_posture = "supporting_evidence"
        allowed_uses = [
            "candidate_ranking",
            "target_stop_context",
            "risk_note_context",
            "follow_up_research",
        ]
    else:
        risk_posture = "observe_repair"
        allowed_uses = [
            "audit_context",
            "repair_candidate_design",
            "follow_up_research",
        ]
    contract = {
        "version": "jue_wiki_usage_contract_v1",
        "decision_role": authority,
        "effectiveness_status": status,
        "risk_posture": risk_posture,
        "standalone_trade_authority": False,
        "requires_live_cross_check": True,
        "hard_blocker": False,
        "allowed_uses": allowed_uses,
        "required_cross_checks": [
            "live_quote",
            "account_state",
            "risk_gate",
            "fresh_research_conflicts",
            "current_price_structure",
        ],
        "conflict_resolution": "prefer_live_execution_data_and_record_wiki_repair",
    }
    guidance = _risk_posture_guidance(
        effectiveness.get("risk_posture_metrics"),
        decision_adjustment_metrics=effectiveness.get("decision_adjustment_metrics"),
        decision_adjustment_audit_metrics=effectiveness.get(
            "decision_adjustment_audit_metrics"
        ),
        current_risk_posture=risk_posture,
    )
    if guidance:
        contract["risk_posture_guidance"] = guidance
    return contract


def _risk_posture_guidance(
    metrics: Any,
    *,
    current_risk_posture: str,
    decision_adjustment_metrics: Any = None,
    decision_adjustment_audit_metrics: Any = None,
) -> dict[str, Any]:
    rows = metrics if isinstance(metrics, list) else []
    active: list[dict[str, Any]] = []
    degraded: list[dict[str, Any]] = []
    current_status = ""
    current = str(current_risk_posture or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        posture = str(row.get("risk_posture") or "").strip().lower()
        if not posture:
            continue
        status = str(row.get("status") or "").strip().lower()
        if posture == current:
            current_status = status
        item = {
            "risk_posture": posture,
            "avg_return_pct": float(row.get("avg_return_pct") or 0.0),
            "sample_count": _safe_int(row.get("sample_count")),
        }
        if status == "active":
            active.append(item)
        elif status == "degraded":
            degraded.append(item)
    preferred = [
        row["risk_posture"]
        for row in sorted(
            active,
            key=lambda row: (
                float(row.get("avg_return_pct") or 0.0),
                int(row.get("sample_count") or 0),
            ),
            reverse=True,
        )[:3]
    ]
    recommended_allowed_uses: list[str] = []
    for posture in preferred:
        for allowed_use in _allowed_uses_for_risk_posture(posture):
            if allowed_use not in recommended_allowed_uses:
                recommended_allowed_uses.append(allowed_use)
    degraded_postures = [
        row["risk_posture"]
        for row in sorted(
            degraded,
            key=lambda row: (
                float(row.get("avg_return_pct") or 0.0),
                int(row.get("sample_count") or 0),
            ),
        )[:3]
    ]
    deprioritized_allowed_uses: list[str] = []
    for posture in degraded_postures:
        for allowed_use in _allowed_uses_for_risk_posture(posture):
            if allowed_use not in deprioritized_allowed_uses:
                deprioritized_allowed_uses.append(allowed_use)
    if not preferred and not degraded_postures:
        return {}
    guidance: dict[str, Any] = {
        "current_risk_posture": current,
        "preferred_risk_postures": preferred,
        "degraded_risk_postures": degraded_postures,
        "guidance": (
            "prefer active risk postures and reduce degraded postures unless "
            "live cross-checks override"
        ),
    }
    if recommended_allowed_uses:
        guidance["recommended_allowed_uses"] = recommended_allowed_uses[:8]
    if deprioritized_allowed_uses:
        guidance["deprioritized_allowed_uses"] = deprioritized_allowed_uses[:8]
    if current_status == "degraded" and preferred:
        adjustment_effectiveness = _matching_decision_adjustment_effectiveness(
            decision_adjustment_metrics,
            action="shift_to_preferred_risk_posture",
            target_risk_posture=preferred[0],
            reason="current_risk_posture_degraded",
        )
        if adjustment_effectiveness:
            guidance["decision_adjustment_effectiveness"] = adjustment_effectiveness
        if str(adjustment_effectiveness.get("status") or "").lower() == "degraded":
            audit_effectiveness = _matching_decision_adjustment_audit_effectiveness(
                decision_adjustment_audit_metrics,
                action="audit_preferred_risk_posture_before_shift",
                target_risk_posture=preferred[0],
            )
            if audit_effectiveness:
                guidance["decision_adjustment_audit_effectiveness"] = (
                    audit_effectiveness
                )
                if (
                    str(audit_effectiveness.get("status") or "").strip().lower()
                    == "degraded"
                ):
                    guidance["decision_adjustment_audit_policy"] = {
                        "action": "repair_audit_contract_before_reuse",
                        "reason": "prior_audit_contract_degraded",
                        "target_risk_posture": preferred[0],
                        "hard_blocker": False,
                    }
            guidance["decision_adjustment"] = {
                "action": "audit_preferred_risk_posture_before_shift",
                "target_risk_posture": preferred[0],
                "reason": "prior_decision_adjustment_degraded",
            }
        else:
            guidance["decision_adjustment"] = {
                "action": "shift_to_preferred_risk_posture",
                "target_risk_posture": preferred[0],
                "reason": "current_risk_posture_degraded",
            }
    if current_status:
        guidance["current_status"] = current_status
    return guidance


def _matching_decision_adjustment_audit_effectiveness(
    metrics: Any,
    *,
    action: str,
    target_risk_posture: str,
) -> dict[str, Any]:
    rows = metrics if isinstance(metrics, list) else []
    clean_action = str(action or "").strip().lower()
    clean_target = str(target_risk_posture or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("action") or "").strip().lower() != clean_action:
            continue
        if (
            str(row.get("target_risk_posture") or "").strip().lower()
            != clean_target
        ):
            continue
        compact: dict[str, Any] = {
            "action": clean_action,
            "target_risk_posture": clean_target,
        }
        status = str(row.get("status") or "").strip().lower()
        if status:
            compact["status"] = status
        for key in (
            "sample_count",
            "win_rate",
            "avg_return_pct",
            "helpful_score",
            "confidence",
        ):
            value = row.get(key)
            if value not in (None, "", [], {}):
                compact[key] = value
        for key in ("evidence_grade_counts", "evidence_grade_instruction_counts"):
            counts = _compact_decision_adjustment_grade_counts(row.get(key))
            if counts:
                compact[key] = counts
        grade_performance = _compact_decision_adjustment_grade_performance(
            row.get("evidence_grade_performance")
        )
        if grade_performance:
            compact["evidence_grade_performance"] = grade_performance
        execution_hint = _decision_adjustment_execution_hint(compact)
        if execution_hint:
            compact["execution_hint"] = execution_hint
        return compact
    return {}


def _matching_decision_adjustment_effectiveness(
    metrics: Any,
    *,
    action: str,
    target_risk_posture: str,
    reason: str,
) -> dict[str, Any]:
    rows = metrics if isinstance(metrics, list) else []
    clean_action = str(action or "").strip().lower()
    clean_target = str(target_risk_posture or "").strip().lower()
    clean_reason = str(reason or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("action") or "").strip().lower() != clean_action:
            continue
        if (
            str(row.get("target_risk_posture") or "").strip().lower()
            != clean_target
        ):
            continue
        if str(row.get("reason") or "").strip().lower() != clean_reason:
            continue
        compact: dict[str, Any] = {
            "action": clean_action,
            "target_risk_posture": clean_target,
            "reason": clean_reason,
        }
        status = str(row.get("status") or "").strip().lower()
        if status:
            compact["status"] = status
        for key in (
            "sample_count",
            "win_rate",
            "avg_return_pct",
            "helpful_score",
            "confidence",
        ):
            value = row.get(key)
            if value not in (None, "", [], {}):
                compact[key] = value
        for key in ("evidence_grade_counts", "evidence_grade_instruction_counts"):
            counts = _compact_decision_adjustment_grade_counts(row.get(key))
            if counts:
                compact[key] = counts
        grade_performance = _compact_decision_adjustment_grade_performance(
            row.get("evidence_grade_performance")
        )
        if grade_performance:
            compact["evidence_grade_performance"] = grade_performance
        execution_hint = _decision_adjustment_execution_hint(compact)
        if execution_hint:
            compact["execution_hint"] = execution_hint
        return compact
    return {}


def _allowed_uses_for_risk_posture(risk_posture: str) -> list[str]:
    return {
        "repair_probe": [
            "repair_candidate_design",
            "small_probe_block",
            "waiting_block",
            "candidate_level_reject",
        ],
        "knowledge_spine": [
            "block_thesis_design",
            "target_stop_design",
            "sizing_context",
            "candidate_ranking",
        ],
        "supporting_evidence": [
            "candidate_ranking",
            "target_stop_context",
            "risk_note_context",
            "follow_up_research",
        ],
        "observe_repair": [
            "audit_context",
            "repair_candidate_design",
            "follow_up_research",
        ],
    }.get(str(risk_posture or "").strip().lower(), [])


def _matching_trust_authority_effectiveness(
    payload: Any,
    *,
    authority: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("trust_profiles")
    if not isinstance(rows, list):
        return {}
    clean_authority = str(authority or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("authority") or "").strip().lower() != clean_authority:
            continue
        compact: dict[str, Any] = {}
        status = str(row.get("status") or "").strip()
        if status:
            compact["status"] = status
        for key in (
            "sample_count",
            "win_rate",
            "avg_return_pct",
            "helpful_score",
            "confidence",
            "usage_contract_counts",
            "risk_posture_metrics",
            "decision_adjustment_metrics",
            "decision_adjustment_audit_metrics",
        ):
            value = row.get(key)
            if value not in (None, "", [], {}):
                compact[key] = value
        reasons = _compact_unique_strings(row.get("reasons"), limit=4, max_length=160)
        if reasons:
            compact["reasons"] = reasons
        return compact
    return {}


def _compact_unique_strings(
    values: Any,
    *,
    limit: int,
    max_length: int | None = None,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = str(value).strip()
        if not text:
            continue
        if max_length is not None:
            text = text[:max_length]
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max(int(limit), 0):
            break
    return result


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_prompt_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _metric_has_prompt_value(metric: dict[str, Any], key: str) -> bool:
    presence = metric.get("metric_presence")
    if isinstance(presence, dict):
        return bool(presence.get(key))
    return _has_prompt_value(metric.get(key))


def _safe_datetime_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _ratio(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return float(part) / float(whole)


class JueWikiSelector:
    LARGE_PROMPT_SOFT_FILL_THRESHOLD = 100_000
    LARGE_PROMPT_SOFT_FILL_RATIO = 0.85

    def __init__(self, service: JueWikiService) -> None:
        self.service = service

    def select(self, request: JueWikiSelectionRequest) -> JueWikiSelectionResult:
        target_scope = str(request.target_scope or "").strip().lower()
        requested_symbols = {
            str(symbol).strip().upper()
            for symbol in request.symbols
            if str(symbol).strip()
        }
        requested_page_types = {
            str(page_type).strip().lower()
            for page_type in request.page_types
            if str(page_type).strip()
        }
        requested_lanes = {
            self._normalize_hint(lane)
            for lane in request.lanes
            if self._normalize_hint(lane)
        }
        requested_regimes = {
            self._normalize_hint(regime)
            for regime in request.regimes
            if self._normalize_hint(regime)
        }
        requested_block_ids = {
            str(block_id).strip()
            for block_id in request.block_ids
            if str(block_id).strip()
        }
        requested_horizons = [
            str(horizon).strip().lower()
            for horizon in request.horizons
            if str(horizon).strip()
        ]
        budget = max(int(request.max_chars), 0)
        selection_budget = budget
        soft_fill_ratio = 1.0
        if budget >= self.LARGE_PROMPT_SOFT_FILL_THRESHOLD:
            soft_fill_ratio = self.LARGE_PROMPT_SOFT_FILL_RATIO
            selection_budget = max(int(budget * soft_fill_ratio), 1)
        max_pages = max(int(request.max_pages), 0)
        pages = self.service.search_pages(include_content=True)
        latest_wiki_attention_states = self._latest_wiki_attention_states(
            pages,
            target_scope=target_scope,
        )
        latest_wiki_memory_card_quality_states = (
            self._latest_wiki_memory_card_quality_states(
                pages,
                target_scope=target_scope,
            )
        )
        application = JueWikiApplicationService(self.service)
        mode_projection = application.project_mode_recommendations()
        projected_mode_recommendations = {
            str(row.get("decision_scope") or ""): row
            for row in list(mode_projection.get("recommendations") or [])
            if isinstance(row, dict) and str(row.get("decision_scope") or "")
        }
        mode_recommendation = projected_mode_recommendations.get(target_scope)
        if not mode_recommendation:
            mode_recommendation = application.latest_mode_recommendations_by_scope(
                refresh=True
            ).get(target_scope, {})
        trust_profile_effectiveness = self._scope_trust_profile_effectiveness(
            application.project_trust_profile_effectiveness(),
            target_scope=target_scope,
        )
        repair_priority_effectiveness = application.project_repair_priority_effectiveness(
            decision_scope=target_scope,
        )
        validation_repair_effectiveness = (
            application.project_validation_repair_effectiveness(
                decision_scope=target_scope,
            )
        )
        wiki_application_coverage = application.project_wiki_application_coverage(
            decision_scope=target_scope,
        )
        effectiveness_by_page = self.service.page_effectiveness_map(
            decision_scope=target_scope,
            horizons=requested_horizons,
        )
        open_lint_page_ids = (
            self._open_lint_page_ids()
            if request.exclude_lint_warnings
            else set()
        )
        open_repair_actions_by_page = self._open_repair_actions_by_page(
            target_scope=target_scope
        )

        scored_pages: list[dict[str, Any]] = []
        rejected_pages: list[dict[str, Any]] = []
        for page in pages:
            page_symbols = {
                str(symbol).strip().upper()
                for symbol in page.get("symbols", [])
                if str(symbol).strip()
            }
            symbol_overlap = page_symbols & requested_symbols
            page_id = str(page.get("page_id") or "")
            char_count = int(page.get("char_count") or len(str(page.get("content") or "")))
            if page.get("scope") != target_scope and not symbol_overlap:
                rejected_pages.append(
                    self._rejected_page(
                        page_id=page_id,
                        reason="scope_or_symbol_mismatch",
                        char_count=char_count,
                    )
                )
                continue
            confidence = float(page.get("confidence") or 0.0)
            if confidence < float(request.min_confidence):
                rejected_pages.append(
                    self._rejected_page(
                        page_id=page_id,
                        reason="below_min_confidence",
                        char_count=char_count,
                    )
                )
                continue
            if page_id in open_lint_page_ids:
                rejected_pages.append(
                    self._rejected_page(
                        page_id=page_id,
                        reason="open_lint_warning",
                        char_count=char_count,
                    )
                )
                continue
            score, reasons, penalties = self._score_page(
                page=page,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
                requested_page_types=requested_page_types,
                requested_lanes=requested_lanes,
                requested_regimes=requested_regimes,
                requested_block_ids=requested_block_ids,
                latest_wiki_attention_states=latest_wiki_attention_states,
                latest_wiki_memory_card_quality_states=(
                    latest_wiki_memory_card_quality_states
                ),
                effectiveness_by_page=effectiveness_by_page,
                effectiveness_weight=float(request.effectiveness_weight),
                effectiveness_max_adjustment=float(
                    request.effectiveness_max_adjustment
                ),
            )
            evidence_quality = canonical_jue_wiki_evidence_quality(
                self.service.source_refs_quality_summary(page.get("source_refs") or [])
            )
            open_repair_actions = list(open_repair_actions_by_page.get(page_id) or [])
            if open_repair_actions:
                evidence_quality = self._evidence_quality_with_open_repair_actions(
                    evidence_quality=evidence_quality,
                    open_repair_actions=open_repair_actions,
                )
                score -= min(len(open_repair_actions) * 2.0, 8.0)
                penalties.append(f"open_repair_queue:{len(open_repair_actions)}")
                reasons.append(
                    "open_repair_queue:"
                    + ",".join(
                        sorted(
                            {
                                str(action.get("action_type") or "unknown")
                                for action in open_repair_actions
                            }
                        )[:3]
                    )
                )
            warning_metrics = self._quality_warning_effectiveness_metrics(
                page=page,
                effectiveness_by_page=effectiveness_by_page,
            )
            if warning_metrics:
                evidence_quality = {
                    **dict(evidence_quality),
                    "warning_effectiveness": self._compact_quality_warning_metrics(
                        warning_metrics
                    ),
                }
            warning_adjustment, warning_reasons, warning_penalties = (
                self._quality_warning_effectiveness_adjustment(
                    warning_metrics,
                    effectiveness_weight=float(request.effectiveness_weight),
                    effectiveness_max_adjustment=float(
                        request.effectiveness_max_adjustment
                    ),
                )
            )
            score += warning_adjustment
            reasons.extend(warning_reasons)
            penalties.extend(warning_penalties)
            quality_status_counts = dict(evidence_quality.get("status_counts") or {})
            weak_count = int(quality_status_counts.get("weak") or 0)
            partial_count = int(quality_status_counts.get("partial") or 0)
            unknown_count = int(quality_status_counts.get("unknown") or 0)
            if weak_count:
                score -= min(weak_count * 1.5, 4.5)
                penalties.append(f"evidence_quality:weak:{weak_count}")
            if partial_count:
                score -= min(partial_count * 0.5, 2.0)
                penalties.append(f"evidence_quality:partial:{partial_count}")
            if unknown_count:
                score -= min(unknown_count * 4.0, 8.0)
                penalties.append(f"evidence_quality:unknown:{unknown_count}")
            metric = effectiveness_by_page.get(page_id)
            if self._degraded_page_is_repair_only(
                page=page,
                metric=metric,
                symbol_overlap=symbol_overlap,
            ):
                rejected_pages.append(
                    self._rejected_page(
                        page_id=page_id,
                        reason="degraded_repair_only",
                        char_count=char_count,
                        score=score,
                        reasons=reasons,
                        penalties=[
                            *penalties,
                            "degraded_repair_only",
                        ],
                    )
                )
                continue
            scored_pages.append(
                {
                    "page": page,
                    "score": score,
                    "priority": self._structural_priority(page),
                    "reasons": reasons,
                    "penalties": penalties,
                    "char_count": char_count,
                    "effectiveness": self._compact_effectiveness_metric(
                        effectiveness_by_page.get(page_id)
                    ),
                    "evidence_quality": evidence_quality,
                }
            )

        scored_pages.sort(
            key=lambda item: (
                -int(item["priority"]),
                -float(item["score"]),
                str(item["page"].get("page_id") or ""),
            )
        )

        selected_pages: list[JueWikiSelectedPage] = []
        chunks: list[str] = []
        used_chars = 0
        for item in scored_pages:
            page = item["page"]
            content = str(page.get("content") or "")
            page_id = str(page.get("page_id") or "")
            penalties = list(item["penalties"])
            reasons = list(item["reasons"])
            if len(selected_pages) >= max_pages:
                rejected_pages.append(
                    self._rejected_page(
                        page_id=page_id,
                        reason="max_pages_exceeded",
                        char_count=int(item["char_count"]),
                        score=float(item["score"]),
                        reasons=reasons,
                        penalties=[*penalties, "max_pages_exceeded"],
                    )
                )
                continue
            separator_len = 2 if chunks else 0
            next_len = separator_len + len(content)
            if selection_budget == 0 or used_chars + next_len > selection_budget:
                remaining = selection_budget - used_chars - separator_len
                compacted = self._compact_for_selection_budget(
                    page=page,
                    content=content,
                    remaining_chars=remaining,
                )
                if compacted:
                    content = compacted
                    next_len = separator_len + len(content)
                    penalties = [*penalties, "compacted_for_selection_budget"]
                    reasons = [*reasons, "selection_budget:compacted"]
                else:
                    rejected_pages.append(
                        self._rejected_page(
                            page_id=page_id,
                            reason=(
                                "soft_fill_limit_exceeded"
                                if selection_budget < budget
                                else "max_chars_exceeded"
                            ),
                            char_count=int(item["char_count"]),
                            score=float(item["score"]),
                            reasons=reasons,
                            penalties=[
                                *penalties,
                                "soft_fill_limit_exceeded"
                                if selection_budget < budget
                                else "max_chars_exceeded",
                            ],
                        )
                    )
                    continue
            if selection_budget == 0 or used_chars + next_len > selection_budget:
                rejected_pages.append(
                    self._rejected_page(
                        page_id=page_id,
                        reason=(
                            "soft_fill_limit_exceeded"
                            if selection_budget < budget
                            else "max_chars_exceeded"
                        ),
                        char_count=int(item["char_count"]),
                        score=float(item["score"]),
                        reasons=reasons,
                        penalties=[
                            *penalties,
                            "soft_fill_limit_exceeded"
                            if selection_budget < budget
                            else "max_chars_exceeded",
                        ],
                    )
                )
                continue
            rank = len(selected_pages) + 1
            selected_pages.append(
                JueWikiSelectedPage(
                    page_id=page_id,
                    rank=rank,
                    score=float(item["score"]),
                    reasons=reasons,
                    penalties=penalties,
                    char_count=int(item["char_count"]),
                    content=content,
                    source_refs=self._source_refs_for_page(page),
                    effectiveness=dict(item.get("effectiveness") or {}),
                    evidence_quality=dict(item.get("evidence_quality") or {}),
                    quality_status=self._requested_symbol_quality_status(
                        dict(item.get("evidence_quality") or {})
                    ),
                    quality_warnings=self._requested_symbol_quality_warnings(
                        dict(item.get("evidence_quality") or {})
                    ),
                )
            )
            chunks.append(content)
            used_chars += next_len

        content = "\n\n".join(chunks)
        status = "ok" if selected_pages else "empty"
        requested_symbol_summaries = self._requested_symbol_summaries(
            pages=pages,
            selected_pages=selected_pages,
            requested_symbols=requested_symbols,
            effectiveness_by_page=effectiveness_by_page,
            open_repair_actions_by_page=open_repair_actions_by_page,
            latest_wiki_attention_states=latest_wiki_attention_states,
            latest_wiki_memory_card_quality_states=(
                latest_wiki_memory_card_quality_states
            ),
        )
        requested_symbol_summary_coverage = self._requested_symbol_summary_coverage(
            requested_symbols=requested_symbols,
            requested_symbol_summaries=requested_symbol_summaries,
            available_summary_symbols=(
                self._requested_symbol_summary_available_symbols(
                    pages=pages,
                    requested_symbols=requested_symbols,
                )
            ),
        )
        repair_priority_budget_summary: dict[str, Any] = {}
        repair_priorities = self._repair_priorities(
            pages=pages,
            effectiveness_by_page=effectiveness_by_page,
            trust_profile_effectiveness=trust_profile_effectiveness,
            repair_priority_effectiveness=repair_priority_effectiveness,
            target_scope=target_scope,
            requested_symbols=requested_symbols,
            requested_symbol_summary_coverage=requested_symbol_summary_coverage,
            wiki_application_coverage=wiki_application_coverage,
            latest_wiki_attention_states=latest_wiki_attention_states,
            latest_wiki_memory_card_quality_states=(
                latest_wiki_memory_card_quality_states
            ),
            budget_summary=repair_priority_budget_summary,
        )
        repair_action_batches = [
            row
            for row in list(
                repair_priority_budget_summary.get("repair_action_batches") or []
            )
            if isinstance(row, dict)
        ]
        budget_report = {
            "char_count": len(content),
            "max_chars": budget,
            "selected_count": len(selected_pages),
            "rejected_count": len(rejected_pages),
            "status": status,
            "selection_budget": selection_budget,
            "soft_fill_ratio": soft_fill_ratio,
            "effectiveness_status_counts": self._effectiveness_status_counts(
                selected_pages
            ),
            "repair_priority_count": len(repair_priorities),
            "repair_action_batch_count": len(repair_action_batches),
            **repair_priority_budget_summary,
            "requested_symbol_summary_count": len(requested_symbol_summaries),
            **requested_symbol_summary_coverage,
        }
        run_id = f"selection:{uuid.uuid4().hex}"
        selected_payloads = [
            {
                "page_id": page.page_id,
                "rank": page.rank,
                "score": page.score,
                "reasons": page.reasons,
                "penalties": page.penalties,
                "char_count": page.char_count,
                "effectiveness": page.effectiveness,
                "evidence_quality": page.evidence_quality,
                "quality_status": page.quality_status,
                "quality_warnings": page.quality_warnings,
            }
            for page in selected_pages
        ]
        evidence_quality = self.service.merge_evidence_quality(
            [page.evidence_quality for page in selected_pages]
        )
        request_trace = {
            **asdict(request),
            "prompt_mode_application": {
                "target_scope": target_scope,
                "mode_recommendation": mode_recommendation,
                "trust_profile_effectiveness": trust_profile_effectiveness,
                "repair_priority_effectiveness": repair_priority_effectiveness,
                "validation_repair_effectiveness": validation_repair_effectiveness,
                "wiki_application_coverage": wiki_application_coverage,
            },
        }
        self.service.record_selection_run(
            run_id=run_id,
            target_scope=target_scope,
            request=request_trace,
            selected_pages=selected_payloads,
            rejected_pages=rejected_pages,
            char_count=len(content),
            max_chars=budget,
            status=status,
            budget_report=budget_report,
        )
        return JueWikiSelectionResult(
            status=status,
            selection_run_id=run_id,
            target_scope=target_scope,
            pages=selected_pages,
            rejected_pages=[
                self._public_rejected_page(page)
                for page in rejected_pages
            ],
            content=content,
            budget_report=budget_report,
            effectiveness_policy=self._effectiveness_policy(),
            repair_priorities=repair_priorities,
            repair_action_batches=repair_action_batches,
            evidence_quality=evidence_quality,
            requested_symbol_summaries=requested_symbol_summaries,
            mode_recommendation=mode_recommendation,
            trust_profile_effectiveness=trust_profile_effectiveness,
            repair_priority_effectiveness=repair_priority_effectiveness,
            validation_repair_effectiveness=validation_repair_effectiveness,
            wiki_application_coverage=wiki_application_coverage,
        )

    @staticmethod
    def _public_rejected_page(page: dict[str, Any]) -> dict[str, Any]:
        row = {
            "page_id": str(page.get("page_id") or ""),
            "reason": str(page.get("reason") or ""),
            "char_count": int(page.get("char_count") or 0),
        }
        evidence_penalties = [
            str(item)
            for item in list(page.get("penalties") or [])
            if str(item) == "evidence_quality:no_sources"
        ]
        if evidence_penalties:
            row["penalties"] = evidence_penalties
        return row

    @staticmethod
    def _scope_trust_profile_effectiveness(
        payload: dict[str, Any],
        *,
        target_scope: str,
    ) -> dict[str, Any]:
        rows = (
            payload.get("trust_profiles")
            if isinstance(payload.get("trust_profiles"), list)
            else []
        )
        clean_scope = str(target_scope or "").strip().lower()
        scoped_rows = [
            {
                key: row.get(key)
                for key in (
                    "decision_scope",
                    "authority",
                    "sample_count",
                    "win_rate",
                    "avg_return_pct",
                    "helpful_score",
                    "confidence",
                    "status",
                    "trust_level_counts",
                    "posture_counts",
                    "prompt_mode_counts",
                    "usage_contract_counts",
                    "risk_posture_metrics",
                    "decision_adjustment_metrics",
                    "decision_adjustment_audit_metrics",
                    "reasons",
                )
                if isinstance(row, dict) and row.get(key) not in (None, "", [], {})
            }
            for row in rows
            if isinstance(row, dict)
            and str(row.get("decision_scope") or "").strip().lower() == clean_scope
        ]
        if not scoped_rows:
            return {}
        return {
            "status": str(payload.get("status") or "ok"),
            "target_scope": clean_scope,
            "trust_profile_count": len(scoped_rows),
            "trust_profiles": scoped_rows,
        }

    def _requested_symbol_summaries(
        self,
        *,
        pages: list[dict[str, Any]],
        selected_pages: list[JueWikiSelectedPage],
        requested_symbols: set[str],
        effectiveness_by_page: dict[str, dict[str, Any]],
        open_repair_actions_by_page: dict[str, list[dict[str, Any]]] | None = None,
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        if not requested_symbols:
            return []
        selected_page_ids = {page.page_id for page in selected_pages}
        candidates_by_symbol: dict[str, dict[str, Any]] = {}
        for page in pages:
            page_id = str(page.get("page_id") or "")
            page_type = str(page.get("page_type") or "").strip().lower()
            if page_type not in self._requested_symbol_summary_page_types():
                continue
            if not self._page_has_requested_symbol_summary_evidence(page):
                continue
            page_symbols = [
                str(symbol).strip().upper()
                for symbol in list(page.get("symbols") or [])
                if str(symbol).strip()
            ]
            overlap = [symbol for symbol in page_symbols if symbol in requested_symbols]
            if not overlap:
                continue
            source_refs = list(page.get("source_refs") or [])
            evidence_quality = self.service.source_refs_quality_summary(source_refs)
            open_repair_actions = list(
                (open_repair_actions_by_page or {}).get(page_id) or []
            )
            if open_repair_actions:
                evidence_quality = self._evidence_quality_with_open_repair_actions(
                    evidence_quality=evidence_quality,
                    open_repair_actions=open_repair_actions,
                )
            quality_status = self._requested_symbol_quality_status(evidence_quality)
            quality_warnings = self._requested_symbol_quality_warnings(
                evidence_quality
            )
            freshness_profile = _wiki_page_freshness_profile(page)
            metric = self._compact_effectiveness_metric(
                effectiveness_by_page.get(page_id)
            )
            summary = self._compact_requested_symbol_summary_text(page_id)
            memory_card = self._requested_symbol_memory_card(
                page_id,
                latest_wiki_attention_states=latest_wiki_attention_states,
                latest_wiki_memory_card_quality_states=(
                    latest_wiki_memory_card_quality_states
                ),
            )
            score = self._requested_symbol_summary_score(
                page=page,
                evidence_quality=evidence_quality,
                effectiveness_metric=metric,
                selected=page_id in selected_page_ids,
            )
            for symbol in overlap:
                previous = candidates_by_symbol.get(symbol)
                if previous and (
                    float(previous.get("summary_score") or 0.0),
                    str(previous.get("page_id") or ""),
                ) >= (score, page_id):
                    continue
                candidates_by_symbol[symbol] = {
                    key: value
                    for key, value in {
                        "symbol": symbol,
                        "page_id": page_id,
                        "page_type": page_type,
                        "title": str(page.get("title") or ""),
                        "selected_as_page": page_id in selected_page_ids,
                        "confidence": float(page.get("confidence") or 0.0),
                        "freshness": str(page.get("freshness") or ""),
                        "freshness_status": freshness_profile.get(
                            "freshness_status"
                        ),
                        "freshness_warnings": freshness_profile.get(
                            "freshness_warnings"
                        ),
                        "quality_status": quality_status,
                        "quality_warnings": quality_warnings,
                        "updated_at": str(page.get("updated_at") or ""),
                        "as_of": str(page.get("as_of") or ""),
                        "summary": summary,
                        "memory_card": memory_card,
                        "evidence_quality": evidence_quality,
                        "effectiveness": metric,
                        "summary_score": score,
                    }.items()
                    if value not in ("", [], {}, None)
                }
        summaries = sorted(
            candidates_by_symbol.values(),
            key=lambda row: (
                -float(row.get("summary_score") or 0.0),
                str(row.get("symbol") or ""),
                str(row.get("page_id") or ""),
            ),
        )
        return summaries[: max(int(limit), 0)]

    @staticmethod
    def _requested_symbol_summary_page_types() -> set[str]:
        return {
            "symbol",
            "research",
            "lesson",
            "playbook",
            "regime",
            "performance",
        }

    @classmethod
    def _requested_symbol_summary_score(
        cls,
        *,
        page: dict[str, Any],
        evidence_quality: dict[str, Any],
        effectiveness_metric: dict[str, Any] | None = None,
        selected: bool,
    ) -> float:
        page_type = str(page.get("page_type") or "").strip().lower()
        freshness = str(page.get("freshness") or "").strip().lower()
        freshness_signal = _wiki_freshness_signal(freshness)
        score = max(min(float(page.get("confidence") or 0.0), 1.0), 0.0) * 40.0
        score += {
            "research": 10.0,
            "symbol": 8.0,
            "lesson": 7.0,
            "playbook": 6.0,
            "regime": 5.0,
            "performance": 4.0,
        }.get(page_type, 0.0)
        if freshness_signal == "fresh":
            score += 18.0
        elif freshness_signal == "stale":
            score -= 15.0
        source_count = int(evidence_quality.get("source_count") or 0)
        score += min(source_count * 2.0, 12.0)
        status_counts = canonical_jue_wiki_status_counts(
            evidence_quality.get("status_counts")
        )
        score += int(status_counts.get("strong") or 0) * 8.0
        score += int(status_counts.get("partial") or 0) * 3.0
        score -= int(status_counts.get("weak") or 0) * 10.0
        if selected:
            score += 4.0
        if effectiveness_metric:
            status = str(effectiveness_metric.get("status") or "").strip().lower()
            confidence = max(
                min(float(effectiveness_metric.get("confidence") or 0.0), 1.0),
                0.0,
            )
            helpful_score = max(
                min(float(effectiveness_metric.get("helpful_score") or 0.0), 10.0),
                -10.0,
            )
            score += helpful_score
            if status == "active":
                score += 8.0 + confidence * 4.0
            elif status == "degraded":
                score -= 10.0 + confidence * 6.0
            elif status == "probe":
                score += confidence
        return score

    @classmethod
    def _requested_symbol_summary_available_symbols(
        cls,
        *,
        pages: list[dict[str, Any]],
        requested_symbols: set[str],
    ) -> set[str]:
        if not requested_symbols:
            return set()
        available: set[str] = set()
        for page in pages:
            page_type = str(page.get("page_type") or "").strip().lower()
            if page_type not in cls._requested_symbol_summary_page_types():
                continue
            if not cls._page_has_requested_symbol_summary_evidence(page):
                continue
            for symbol in list(page.get("symbols") or []):
                clean_symbol = str(symbol).strip().upper()
                if clean_symbol and clean_symbol in requested_symbols:
                    available.add(clean_symbol)
        return available

    @staticmethod
    def _page_has_requested_symbol_summary_evidence(page: dict[str, Any]) -> bool:
        for ref in list(page.get("source_refs") or []):
            if not isinstance(ref, dict):
                continue
            if str(ref.get("source_type") or "") == "wiki_repair_queue":
                continue
            if str(ref.get("source_id") or "").strip():
                return True
        return False

    @staticmethod
    def _requested_symbol_summary_coverage(
        *,
        requested_symbols: set[str],
        requested_symbol_summaries: list[dict[str, Any]],
        available_summary_symbols: set[str] | None = None,
    ) -> dict[str, Any]:
        summarized_symbols = {
            str(row.get("symbol") or "").strip().upper()
            for row in requested_symbol_summaries
            if str(row.get("symbol") or "").strip()
        }
        available_symbols = (
            set(available_summary_symbols)
            if available_summary_symbols is not None
            else set(summarized_symbols)
        )
        unsummarized_symbols = sorted(requested_symbols - summarized_symbols)
        missing_symbols = sorted(requested_symbols - available_symbols)
        prompt_omitted_symbols = sorted(
            (requested_symbols - summarized_symbols) - set(missing_symbols)
        )
        degraded_summary_reasons = (
            JueWikiSelector._requested_symbol_degraded_summary_reasons(
                requested_symbol_summaries
            )
        )
        degraded_summary_symbols = sorted(
            {
                str(row.get("symbol") or "").strip().upper()
                for row in degraded_summary_reasons
                if str(row.get("symbol") or "").strip()
            }
        )
        requested_count = len(requested_symbols)
        available_count = len(available_symbols)
        if requested_count == 0:
            status = "not_requested"
        elif available_count == 0:
            status = "none"
        elif missing_symbols or prompt_omitted_symbols:
            status = "partial"
        else:
            status = "full"
        return {
            "requested_symbol_count": requested_count,
            "requested_symbol_summary_symbols": sorted(summarized_symbols),
            "requested_symbol_available_summary_count": len(available_symbols),
            "requested_symbol_available_summary_symbols": sorted(
                available_symbols
            )[:64],
            "requested_symbol_unsummarized_count": len(unsummarized_symbols),
            "requested_symbol_unsummarized_symbols": unsummarized_symbols[:32],
            "requested_symbol_missing_summary_count": len(missing_symbols),
            "requested_symbol_missing_summary_symbols": missing_symbols[:32],
            "requested_symbol_prompt_omitted_count": len(prompt_omitted_symbols),
            "requested_symbol_prompt_omitted_symbols": prompt_omitted_symbols[:32],
            "requested_symbol_degraded_summary_count": len(
                degraded_summary_symbols
            ),
            "requested_symbol_degraded_summary_symbols": degraded_summary_symbols[:32],
            "requested_symbol_degraded_summary_reasons": degraded_summary_reasons[
                :32
            ],
            "requested_symbol_summary_coverage_status": status,
        }

    @staticmethod
    def _requested_symbol_degraded_summary_reasons(
        requested_symbol_summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for summary in requested_symbol_summaries:
            if not isinstance(summary, dict):
                continue
            symbol = str(summary.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            freshness = str(summary.get("freshness") or "").strip().lower()
            freshness_status = str(
                summary.get("freshness_status") or ""
            ).strip().lower()
            freshness_warnings = [
                str(item).strip()
                for item in list(summary.get("freshness_warnings") or [])
                if str(item).strip()
            ]
            quality_status = normalize_jue_wiki_quality_status(
                summary.get("quality_status")
            )
            quality_warnings = [
                str(item).strip()
                for item in list(summary.get("quality_warnings") or [])
                if str(item).strip()
            ]
            degraded_statuses = {"weak", "partial"}
            is_stale = (
                _wiki_freshness_signal(freshness) == "stale"
                or freshness_status == "stale"
            )
            is_degraded_quality = quality_status in degraded_statuses
            if not is_stale and not is_degraded_quality and not quality_warnings:
                continue
            row: dict[str, Any] = {"symbol": symbol}
            if freshness:
                row["freshness"] = freshness
            if freshness_status:
                row["freshness_status"] = freshness_status
            if freshness_warnings:
                row["freshness_warnings"] = freshness_warnings[:6]
            if quality_status:
                row["quality_status"] = quality_status
            if quality_warnings:
                row["quality_warnings"] = quality_warnings[:6]
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("symbol") or ""))
        return rows

    def _open_repair_actions_by_page(
        self,
        *,
        target_scope: str,
    ) -> dict[str, list[dict[str, Any]]]:
        self.service.initialize()
        with self.service._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        clean_scope = str(target_scope or "").strip().lower()
        by_page: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            page_id = str(row["page_id"] or "").strip()
            details: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["details_json"] or "{}"))
                if isinstance(parsed, dict):
                    details = parsed
            except json.JSONDecodeError:
                details = {}
            decision_scope = str(
                details.get("decision_scope")
                or details.get("scope")
                or details.get("source_scope")
                or ""
            ).strip().lower()
            if clean_scope and not (
                page_id.startswith(f"{clean_scope}.") or decision_scope == clean_scope
            ):
                continue
            warnings = _compact_string_list(
                details.get("quality_warnings"),
                limit=8,
                max_len=120,
            )
            action = {
                "action_id": str(row["action_id"] or ""),
                "finding_id": str(row["finding_id"] or ""),
                "page_id": page_id,
                "action_type": str(row["action_type"] or ""),
                "status": str(row["status"] or ""),
                "quality_warnings": warnings,
                "repair_action": str(details.get("repair_action") or "")[:240],
            }
            by_page.setdefault(page_id, []).append(action)
        return by_page

    def _evidence_quality_with_open_repair_actions(
        self,
        *,
        evidence_quality: dict[str, Any],
        open_repair_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        warning_counts: dict[str, int] = {"open_repair_queue": len(open_repair_actions)}
        for action in open_repair_actions:
            for warning in list(action.get("quality_warnings") or []):
                clean_warning = str(warning or "").strip()
                if clean_warning:
                    warning_counts[clean_warning] = (
                        warning_counts.get(clean_warning, 0) + 1
                    )
        repair_quality = {
            "source_count": len(open_repair_actions),
            "status_counts": {"partial": len(open_repair_actions)},
            "warning_counts": warning_counts,
            "source_type_counts": {"wiki_repair_actions": len(open_repair_actions)},
        }
        merged = self.service.merge_evidence_quality([evidence_quality, repair_quality])
        for key in ("warning_effectiveness",):
            if key in evidence_quality:
                merged[key] = evidence_quality[key]
        merged["repair_queue"] = {
            "open_count": len(open_repair_actions),
            "actions": [
                self._compact_open_repair_action_for_evidence_quality(action)
                for action in open_repair_actions[:6]
            ],
        }
        return canonical_jue_wiki_evidence_quality(merged)

    @staticmethod
    def _compact_open_repair_action_for_evidence_quality(
        action: dict[str, Any],
    ) -> dict[str, Any]:
        row = {
            "action_type": str(action.get("action_type") or "")[:120],
            "status": str(action.get("status") or "")[:40],
        }
        quality_warnings = _compact_string_list(
            action.get("quality_warnings"),
            limit=6,
            max_len=120,
        )
        if quality_warnings:
            row["quality_warnings"] = quality_warnings
        return row

    @staticmethod
    def _requested_symbol_quality_status(
        evidence_quality: dict[str, Any],
    ) -> str:
        return jue_wiki_quality_status_from_evidence(evidence_quality)

    @staticmethod
    def _requested_symbol_quality_warnings(
        evidence_quality: dict[str, Any],
        *,
        limit: int = 6,
    ) -> list[str]:
        warnings: list[str] = []
        for item in list(evidence_quality.get("top_warnings") or []):
            if isinstance(item, dict):
                warning = str(item.get("warning") or "").strip()
            else:
                warning = str(item).strip()
            if warning and warning not in warnings:
                warnings.append(warning)
            if len(warnings) >= max(int(limit), 0):
                break
        return warnings

    def _compact_requested_symbol_summary_text(self, page_id: str) -> str:
        try:
            summary = self.service._summary_text(page_id)
        except Exception:
            summary = ""
        return str(summary or "").strip()[:900]

    def _requested_symbol_memory_card(
        self,
        page_id: str,
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> dict[str, Any]:
        try:
            page = self.service.read_page(page_id)
        except Exception:
            return {}
        content = str(page.get("content") or "")
        sections = self._markdown_sections(content)
        wanted = {
            "stance": "Current Stance",
            "durable_facts": "Durable Facts",
            "trading_history": "Trading History",
            "lessons": "Lessons",
            "contradictions": "Contradictions",
            "open_questions": "Open Questions",
        }
        limits = {
            "stance": 360,
            "durable_facts": 420,
            "trading_history": 700,
            "lessons": 420,
            "contradictions": 320,
            "open_questions": 520,
        }
        card: dict[str, Any] = {}
        for key, section_name in wanted.items():
            section_text = sections.get(section_name, "")
            if section_name == "Trading History":
                section_text = self._filter_superseded_wiki_attention_lines(
                    section_text,
                    page=page,
                    latest_wiki_attention_states=latest_wiki_attention_states,
                )
                section_text = self._filter_superseded_wiki_memory_card_quality_lines(
                    section_text,
                    page=page,
                    latest_wiki_memory_card_quality_states=(
                        latest_wiki_memory_card_quality_states
                    ),
                )
            value = self._compact_section_text(
                section_text,
                limit=limits[key],
            )
            if value:
                card[key] = value
        return card

    @classmethod
    def _filter_superseded_wiki_attention_lines(
        cls,
        value: str,
        *,
        page: dict[str, Any],
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> str:
        if not latest_wiki_attention_states or "Jue Wiki Attention" not in str(value):
            return value
        page_id = str(page.get("page_id") or "").strip()
        page_symbols = [
            str(symbol).strip().upper()
            for symbol in list(page.get("symbols") or [])
            if str(symbol).strip()
        ]
        lines: list[str] = []
        attention_heading_index: int | None = None
        attention_rows_kept = False
        for line in str(value or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                if (
                    attention_heading_index is not None
                    and not attention_rows_kept
                    and 0 <= attention_heading_index < len(lines)
                ):
                    lines.pop(attention_heading_index)
                attention_heading_index = (
                    len(lines) if stripped == "### Jue Wiki Attention" else None
                )
                attention_rows_kept = False
                lines.append(line)
                continue
            ref = cls._wiki_attention_ref_from_line(
                line,
                page_id=page_id,
                page_symbols=page_symbols,
            )
            if ref:
                key = ref.get("attention_key")
                latest = (
                    latest_wiki_attention_states.get(key)
                    if isinstance(key, tuple)
                    else None
                )
                if latest and cls._wiki_attention_state_sort_key(
                    ref
                ) < cls._wiki_attention_state_sort_key(latest):
                    continue
                if attention_heading_index is not None:
                    attention_rows_kept = True
            lines.append(line)
        if (
            attention_heading_index is not None
            and not attention_rows_kept
            and 0 <= attention_heading_index < len(lines)
        ):
            lines.pop(attention_heading_index)
        return "\n".join(lines).strip()

    @classmethod
    def _filter_superseded_wiki_memory_card_quality_lines(
        cls,
        value: str,
        *,
        page: dict[str, Any],
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> str:
        if (
            not latest_wiki_memory_card_quality_states
            or "Jue Wiki Memory Card Quality" not in str(value)
        ):
            return value
        page_id = str(page.get("page_id") or "").strip()
        page_symbols = [
            str(symbol).strip().upper()
            for symbol in list(page.get("symbols") or [])
            if str(symbol).strip()
        ]
        lines: list[str] = []
        quality_heading_index: int | None = None
        quality_rows_kept = False
        for line in str(value or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                if (
                    quality_heading_index is not None
                    and not quality_rows_kept
                    and 0 <= quality_heading_index < len(lines)
                ):
                    lines.pop(quality_heading_index)
                quality_heading_index = (
                    len(lines)
                    if stripped == "### Jue Wiki Memory Card Quality"
                    else None
                )
                quality_rows_kept = False
                lines.append(line)
                continue
            ref = cls._wiki_memory_card_quality_ref_from_line(
                line,
                page_id=page_id,
                page_symbols=page_symbols,
            )
            if ref:
                key = ref.get("quality_key")
                latest = (
                    latest_wiki_memory_card_quality_states.get(key)
                    if isinstance(key, tuple)
                    else None
                )
                if latest and cls._wiki_memory_card_quality_state_sort_key(
                    ref
                ) < cls._wiki_memory_card_quality_state_sort_key(latest):
                    continue
                if quality_heading_index is not None:
                    quality_rows_kept = True
            lines.append(line)
        if (
            quality_heading_index is not None
            and not quality_rows_kept
            and 0 <= quality_heading_index < len(lines)
        ):
            lines.pop(quality_heading_index)
        return "\n".join(lines).strip()

    @staticmethod
    def _markdown_sections(content: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current = ""
        for line in str(content or "").splitlines():
            if line.startswith("## "):
                current = line.removeprefix("## ").strip()
                sections.setdefault(current, [])
                continue
            if current:
                sections.setdefault(current, []).append(line)
        return {key: "\n".join(value).strip() for key, value in sections.items()}

    @staticmethod
    def _compact_section_text(value: str, *, limit: int) -> str:
        text = "\n".join(
            line.strip()
            for line in str(value or "").splitlines()
            if line.strip()
        ).strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        suffix = "\n...[trimmed]"
        keep = max(int(limit) - len(suffix), 0)
        return f"{text[:keep].rstrip()}{suffix}" if keep else suffix.strip()

    @staticmethod
    def _compact_for_selection_budget(
        *,
        page: dict[str, Any],
        content: str,
        remaining_chars: int,
    ) -> str:
        page_id = str(page.get("page_id") or "")
        page_type = str(page.get("page_type") or "").strip().lower()
        is_operational = (
            (page_type == "risk" and page_id.endswith(".risk.trading_validation"))
            or (page_type == "ops" and page_id.endswith(".ops.action_pressure"))
            or (page_type == "ops" and page_id.endswith(".ops.opportunity_pipeline"))
            or (page_type == "ops" and page_id.endswith(".ops.manager_runs"))
            or (
                page_type == "performance"
                and page_id.endswith(".performance.live_outcomes")
            )
            or (page_type == "research" and page_id.endswith(".research.coverage"))
            or (
                page_type == "research"
                and page_id.endswith(".research.evidence_quality")
            )
            or (page_type == "research" and page_id.endswith(".research.repair_queue"))
        )
        if not is_operational:
            return ""
        compact_cap = 3_200
        if page_type == "risk":
            compact_cap = 4_500
        elif page_type == "performance":
            compact_cap = 3_800
        elif page_type == "research":
            compact_cap = 2_800
        limit = min(max(int(remaining_chars), 0), compact_cap)
        if limit < 900:
            return ""
        suffix = "\n\n...[compacted_for_selection_budget]"
        keep = max(limit - len(suffix), 0)
        if keep < 600:
            return ""
        if len(content) <= limit:
            return content
        return f"{content[:keep].rstrip()}{suffix}"

    @staticmethod
    def _structural_priority(page: dict[str, Any]) -> int:
        page_id = str(page.get("page_id") or "")
        page_type = str(page.get("page_type") or "").strip().lower()
        if page_type == "risk" and page_id.endswith(".risk.trading_validation"):
            return 100
        if JueWikiSelector._page_has_manager_contract_recovery_priority(page):
            return 96
        if page_type == "ops" and page_id.endswith(".ops.action_pressure"):
            return 88
        if page_type == "ops" and page_id.endswith(".ops.opportunity_pipeline"):
            return 84
        if page_type == "ops" and page_id.endswith(".ops.manager_runs"):
            return 80
        if (
            page_type == "performance"
            and page_id.endswith(".performance.live_outcomes")
        ):
            return 70
        if page_type == "research" and page_id.endswith(".research.repair_queue"):
            return 64
        if page_type == "research" and page_id.endswith(".research.evidence_quality"):
            return 62
        if page_type == "research" and page_id.endswith(".research.coverage"):
            return 60
        if page_type == "risk":
            return 50
        return 0

    @staticmethod
    def _page_has_manager_contract_recovery_priority(page: dict[str, Any]) -> bool:
        page_id = str(page.get("page_id") or "")
        page_type = str(page.get("page_type") or "").strip().lower()
        if page_type != "ops" or not page_id.endswith(".ops.manager_runs"):
            return False
        haystack = "\n".join(
            [
                str(page.get("title") or ""),
                str(page.get("content") or ""),
            ]
        ).lower()
        return (
            "priority=manager_contract_recovery" in haystack
            or "priority_reason=manager_contract_recovery" in haystack
        )

    @classmethod
    def _has_unresolved_wiki_attention(
        cls,
        page: dict[str, Any],
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> bool:
        return bool(
            cls._wiki_attention_refs_for_page(
                page,
                latest_wiki_attention_states=latest_wiki_attention_states,
            )
        )

    def _score_page(
        self,
        *,
        page: dict[str, Any],
        target_scope: str,
        requested_symbols: set[str],
        requested_page_types: set[str],
        requested_lanes: set[str],
        requested_regimes: set[str],
        requested_block_ids: set[str],
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        effectiveness_by_page: dict[str, dict[str, Any]] | None = None,
        effectiveness_weight: float = 0.0,
        effectiveness_max_adjustment: float = 0.0,
    ) -> tuple[float, list[str], list[str]]:
        score = 0.0
        reasons: list[str] = []
        penalties: list[str] = []
        page_symbols = {
            str(symbol).strip().upper()
            for symbol in page.get("symbols", [])
            if str(symbol).strip()
        }
        symbol_overlap = page_symbols & requested_symbols
        if page["scope"] == target_scope:
            score += 35.0
            reasons.append(f"scope_match:{target_scope}")
        if symbol_overlap:
            score += 30.0
            reasons.append(f"symbol_overlap:{','.join(sorted(symbol_overlap))}")
        if page["page_type"] in requested_page_types:
            score += 12.0
            reasons.append(f"page_type_match:{page['page_type']}")
        source_refs = self._source_refs_for_page(page)
        source_ref_ids = {
            str(ref.get("source_id") or "").strip()
            for ref in source_refs
            if isinstance(ref, dict) and str(ref.get("source_id") or "").strip()
        }
        block_overlap = source_ref_ids & requested_block_ids
        if block_overlap:
            score += 28.0
            reasons.append(f"block_ref_overlap:{','.join(sorted(block_overlap))}")
        lane_overlap = self._hint_overlap(page, requested_lanes)
        if lane_overlap:
            score += min(len(lane_overlap) * 10.0, 20.0)
            reasons.append(f"lane_overlap:{','.join(lane_overlap)}")
        regime_overlap = self._hint_overlap(page, requested_regimes)
        if regime_overlap:
            score += min(len(regime_overlap) * 8.0, 16.0)
            reasons.append(f"regime_overlap:{','.join(regime_overlap)}")
        if (
            page["page_type"] == "risk"
            and str(page.get("page_id") or "").endswith(".risk.trading_validation")
        ):
            score += 90.0
            reasons.append("operational_memory:trading_validation")
        if (
            page["page_type"] == "ops"
            and str(page.get("page_id") or "").endswith(".ops.action_pressure")
        ):
            score += 70.0
            reasons.append("operational_memory:action_pressure")
        if (
            page["page_type"] == "ops"
            and str(page.get("page_id") or "").endswith(".ops.opportunity_pipeline")
        ):
            score += 65.0
            reasons.append("operational_memory:opportunity_pipeline")
        if (
            page["page_type"] == "ops"
            and str(page.get("page_id") or "").endswith(".ops.manager_runs")
        ):
            score += 60.0
            reasons.append("operational_memory:manager_runs")
        if self._page_has_manager_contract_recovery_priority(page):
            score += 35.0
            reasons.append("operational_memory:manager_contract_recovery")
        if (
            page["page_type"] == "performance"
            and str(page.get("page_id") or "").endswith(".performance.live_outcomes")
        ):
            score += 55.0
            reasons.append("operational_memory:live_performance")
        if (
            page["page_type"] == "research"
            and str(page.get("page_id") or "").endswith(".research.coverage")
        ):
            score += 45.0
            reasons.append("operational_memory:research_coverage")
        if (
            page["page_type"] == "research"
            and str(page.get("page_id") or "").endswith(".research.evidence_quality")
        ):
            score += 48.0
            reasons.append("operational_memory:evidence_quality")
        if (
            page["page_type"] == "research"
            and str(page.get("page_id") or "").endswith(".research.repair_queue")
        ):
            score += 52.0
            reasons.append("operational_memory:repair_queue")
        if self._has_unresolved_wiki_attention(
            page,
            latest_wiki_attention_states=latest_wiki_attention_states,
        ):
            score += 24.0
            reasons.append("operational_memory:wiki_attention")
        if self._has_unresolved_wiki_memory_card_quality(
            page,
            latest_wiki_memory_card_quality_states=(
                latest_wiki_memory_card_quality_states
            ),
        ):
            score += 22.0
            reasons.append("operational_memory:memory_card_quality")
        freshness = str(page.get("freshness") or "").strip().lower()
        freshness_signal = _wiki_freshness_signal(freshness)
        if freshness_signal == "fresh":
            score += 10.0
            reasons.append(f"freshness:{freshness or 'fresh'}")
        elif freshness_signal == "stale":
            score -= 15.0
            penalties.append(f"freshness:{freshness or 'stale'}")
        confidence_score = min(float(page["confidence"]) * 10.0, 10.0)
        score += confidence_score
        reasons.append(f"confidence:{float(page['confidence']):.4f}")
        source_score = min(len(source_refs) * 1.5, 9.0)
        score += source_score
        if source_refs:
            reasons.append(f"source_refs:{len(source_refs)}")
        elif self._source_backing_required_for_page(page):
            score -= 8.0
            penalties.append("evidence_quality:no_sources")
        metric = (effectiveness_by_page or {}).get(str(page.get("page_id") or ""))
        if metric:
            status = str(metric.get("status") or "unknown").strip().lower()
            confidence = max(min(float(metric.get("confidence") or 0.0), 1.0), 0.0)
            if status == "active":
                status_adjustment = 3.0 + confidence * 2.0
                score += status_adjustment
                reasons.append("effectiveness_status:active")
                reasons.append(
                    f"effectiveness_status_adjustment:{status_adjustment:.4f}"
                )
            elif status == "degraded":
                status_adjustment = -(5.0 + confidence * 3.0)
                score += status_adjustment
                penalties.append("effectiveness_status:degraded")
                penalties.append(
                    f"effectiveness_status_adjustment:{status_adjustment:.4f}"
                )
            elif status == "probe":
                status_adjustment = 0.5 * confidence
                score += status_adjustment
                reasons.append("effectiveness_status:probe")
                reasons.append(
                    f"effectiveness_status_adjustment:{status_adjustment:.4f}"
                )
            raw_adjustment = float(metric.get("helpful_score") or 0.0) * float(
                effectiveness_weight
            )
            cap = abs(float(effectiveness_max_adjustment))
            adjustment = max(min(raw_adjustment, cap), -cap) if cap else 0.0
            score += adjustment
            reasons.append(f"effectiveness:{metric.get('status') or 'unknown'}")
            reasons.append(f"effectiveness_adjustment:{adjustment:.4f}")
        return score, reasons, penalties

    @staticmethod
    def _source_backing_required_for_page(page: dict[str, Any]) -> bool:
        page_type = str(page.get("page_type") or "").strip().lower()
        return page_type in {"symbol", "playbook", "regime", "lesson"}

    def _quality_warning_effectiveness_metrics(
        self,
        *,
        page: dict[str, Any],
        effectiveness_by_page: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = []
        for warning in self._page_quality_warnings(page):
            metric = effectiveness_by_page.get(self._quality_warning_page_id(warning))
            if not metric:
                continue
            metrics.append(
                {
                    **metric,
                    "warning": warning,
                }
            )
        return metrics

    @staticmethod
    def _compact_quality_warning_metrics(
        metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for metric in metrics[:8]:
            warning = str(metric.get("warning") or "").strip()
            page_id = str(metric.get("page_id") or "").strip()
            if not warning or not page_id:
                continue
            rows.append(
                _quality_warning_metric_prompt_row(
                    warning=warning,
                    page_id=page_id,
                    metric=metric,
                )
            )
        return rows

    @staticmethod
    def _page_quality_warnings(page: dict[str, Any]) -> list[str]:
        warnings: list[str] = []

        def add_warning(value: Any) -> None:
            warning = str(value).strip()
            if warning and warning not in warnings:
                warnings.append(warning)

        def add_warnings_from_evidence_quality(evidence_quality: Any) -> None:
            if not isinstance(evidence_quality, dict):
                return
            for item in list(evidence_quality.get("top_warnings") or []):
                if isinstance(item, dict):
                    add_warning(item.get("warning"))
                else:
                    add_warning(item)

        for item in list(page.get("quality_warnings") or []):
            add_warning(item)
        add_warnings_from_evidence_quality(page.get("evidence_quality"))
        for ref in JueWikiSelector._source_refs_for_page(page):
            if not isinstance(ref, dict):
                continue
            for item in list(ref.get("quality_warnings") or []):
                add_warning(item)
            add_warnings_from_evidence_quality(ref.get("evidence_quality"))
        return warnings

    @staticmethod
    def _quality_warning_page_id(warning: str) -> str:
        clean = str(warning).strip().lower()
        clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in clean)
        clean = "_".join(part for part in clean.split("_") if part)
        return f"quality_warning.{clean or 'unknown'}"

    def _quality_warning_effectiveness_adjustment(
        self,
        metrics: list[dict[str, Any]],
        *,
        effectiveness_weight: float,
        effectiveness_max_adjustment: float,
    ) -> tuple[float, list[str], list[str]]:
        if not metrics:
            return 0.0, [], []
        total = 0.0
        reasons: list[str] = []
        penalties: list[str] = []
        cap = abs(float(effectiveness_max_adjustment))
        for metric in metrics:
            warning = str(metric.get("warning") or "").strip()
            status = str(metric.get("status") or "unknown").strip().lower()
            confidence = max(min(float(metric.get("confidence") or 0.0), 1.0), 0.0)
            raw = float(metric.get("helpful_score") or 0.0) * float(
                effectiveness_weight
            )
            adjustment = max(min(raw, cap), -cap) if cap else 0.0
            if status == "degraded":
                adjustment -= 2.0 + confidence
                penalties.append(
                    f"quality_warning_effectiveness:{warning}:degraded"
                )
            elif status == "active":
                adjustment += 1.0 + confidence
                reasons.append(f"quality_warning_effectiveness:{warning}:active")
            elif status == "probe":
                reasons.append(f"quality_warning_effectiveness:{warning}:probe")
            total += adjustment
            reasons.append(
                f"quality_warning_effectiveness_adjustment:{warning}:{adjustment:.4f}"
            )
        total_cap = cap * max(len(metrics), 1) if cap else 0.0
        if total_cap:
            total = max(min(total, total_cap), -total_cap)
        return total, reasons, penalties

    @classmethod
    def _hint_overlap(
        cls,
        page: dict[str, Any],
        requested_hints: set[str],
    ) -> list[str]:
        if not requested_hints:
            return []
        haystack_parts = [
            str(page.get("page_id") or ""),
            str(page.get("title") or ""),
            str(page.get("content") or ""),
        ]
        for ref in cls._source_refs_for_page(page):
            if not isinstance(ref, dict):
                continue
            haystack_parts.extend(
                [
                    str(ref.get("source_id") or ""),
                    str(ref.get("source_type") or ""),
                    str(ref.get("source_scope") or ""),
                ]
            )
        haystack = cls._normalize_hint(" ".join(haystack_parts))
        return sorted(
            hint
            for hint in requested_hints
            if hint and hint in haystack
        )

    @staticmethod
    def _source_refs_for_page(
        page: dict[str, Any],
        *,
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        refs = page.get("source_refs") if isinstance(page.get("source_refs"), list) else []
        rows: list[dict[str, Any]] = []

        def visit(source_refs: list[Any], *, depth: int) -> None:
            if depth > max(int(max_depth), 0):
                return
            for ref in source_refs:
                if not isinstance(ref, dict):
                    continue
                rows.append(ref)
                nested_refs = ref.get("source_refs")
                if isinstance(nested_refs, list):
                    visit(nested_refs, depth=depth + 1)

        visit(refs, depth=0)
        return rows

    @staticmethod
    def _normalize_hint(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().replace(":", " ").split())

    @staticmethod
    def _compact_effectiveness_metric(
        metric: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not metric:
            return {}
        keys = (
            "status",
            "venue",
            "horizon",
        )
        compact: dict[str, Any] = {
            key: metric.get(key)
            for key in keys
            if metric.get(key) not in (None, "", [], {})
        }
        metric_keys = (
            "sample_count",
            "win_rate",
            "expectancy",
            "avg_return_pct",
            "median_mae_pct",
            "drawdown_pressure",
            "helpful_score",
            "confidence",
        )
        for key in metric_keys:
            if _metric_has_prompt_value(metric, key):
                compact[key] = metric.get(key)
        reasons = metric.get("reasons")
        if reasons is None:
            raw_reasons = metric.get("reasons_json")
            if isinstance(raw_reasons, str) and raw_reasons.strip():
                try:
                    reasons = json.loads(raw_reasons)
                except json.JSONDecodeError:
                    reasons = []
        if isinstance(reasons, list):
            compact["reasons"] = [str(item)[:180] for item in reasons[:8]]
        return compact

    @staticmethod
    def _degraded_page_is_repair_only(
        *,
        page: dict[str, Any],
        metric: dict[str, Any] | None,
        symbol_overlap: set[str],
    ) -> bool:
        if not metric:
            return False
        if str(metric.get("status") or "").strip().lower() != "degraded":
            return False
        page_type = str(page.get("page_type") or "").strip().lower()
        if page_type == "risk":
            return False
        if page_type == "symbol" and symbol_overlap:
            return False
        return True

    @staticmethod
    def _effectiveness_status_counts(
        pages: list[JueWikiSelectedPage],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for page in pages:
            status = str(page.effectiveness.get("status") or "unscored").strip()
            counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _effectiveness_policy() -> dict[str, Any]:
        return {
            "active": (
                "Prefer active pages when the current symbol, lane, and gate context "
                "matches their evidence."
            ),
            "probe": (
                "Use probe pages as candidates for small executable tests, not as "
                "final conclusions."
            ),
            "degraded": (
                "Treat degraded pages as repair evidence. They are not standalone "
                "no-action blockers; either name the exact candidate-level failure "
                "condition or design a safer waiting/probe block."
            ),
            "missed_action_pressure": (
                "If proactive pressure is action_required and gates are open, resolve "
                "at least one strong candidate into an executable block or a precise "
                "candidate-level rejection."
            ),
        }

    def _repair_priorities(
        self,
        *,
        pages: list[dict[str, Any]],
        effectiveness_by_page: dict[str, dict[str, Any]],
        trust_profile_effectiveness: dict[str, Any],
        repair_priority_effectiveness: dict[str, Any],
        target_scope: str,
        requested_symbols: set[str],
        requested_symbol_summary_coverage: dict[str, Any] | None = None,
        wiki_application_coverage: dict[str, Any] | None = None,
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        budget_summary: dict[str, Any] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        page_by_id = {str(page.get("page_id") or ""): page for page in pages}
        rows: list[dict[str, Any]] = []
        for page_id, metric in effectiveness_by_page.items():
            status = str(metric.get("status") or "").strip().lower()
            if status != "degraded":
                continue
            page = page_by_id.get(str(page_id))
            if not page or str(page.get("scope") or "") != target_scope:
                continue
            page_symbols = [
                str(symbol).strip().upper()
                for symbol in list(page.get("symbols") or [])[:8]
                if str(symbol).strip()
            ]
            symbol_overlap = sorted(set(page_symbols) & requested_symbols)
            row: dict[str, Any] = {
                "page_id": str(page_id),
                "page_type": str(page.get("page_type") or ""),
                "symbols": page_symbols,
                "symbol_overlap": symbol_overlap,
                "status": status,
                "reasons": self._metric_reasons(metric),
                "repair_action": self._repair_action_for_metric(
                    metric=metric,
                    page_type=str(page.get("page_type") or ""),
                ),
            }
            if _metric_has_prompt_value(metric, "sample_count"):
                row["sample_count"] = _safe_int(metric.get("sample_count"))
            for key in (
                "win_rate",
                "expectancy",
                "avg_return_pct",
                "median_mae_pct",
                "drawdown_pressure",
                "helpful_score",
                "confidence",
            ):
                if _metric_has_prompt_value(metric, key):
                    row[key] = _safe_float(metric.get(key))
            rows.append(
                {
                    key: value
                    for key, value in row.items()
                    if value not in ("", [], {}, None)
                }
            )
        rows.sort(
            key=lambda row: (
                self._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                -int(row.get("sample_count") or 0),
                float(row.get("helpful_score") or 0.0),
                -float(row.get("drawdown_pressure") or 0.0),
                str(row.get("page_id") or ""),
            )
        )
        rows.extend(
            self._requested_symbol_coverage_repair_priorities(
                pages=pages,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
                requested_symbol_summary_coverage=(
                    requested_symbol_summary_coverage or {}
                ),
            )
        )
        rows.extend(
            self._wiki_application_coverage_repair_priorities(
                target_scope=target_scope,
                wiki_application_coverage=wiki_application_coverage or {},
            )
        )
        rows.extend(
            self._repair_queue_priorities(
                pages=pages,
                effectiveness_by_page=effectiveness_by_page,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
            )
        )
        rows.extend(
            self._decision_adjustment_audit_repair_priorities(
                trust_profile_effectiveness=trust_profile_effectiveness,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
            )
        )
        rows.extend(
            self._usage_guidance_effectiveness_repair_priorities(
                effectiveness_by_page=effectiveness_by_page,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
            )
        )
        rows.extend(
            self._evidence_quality_repair_priorities(
                pages=pages,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
            )
        )
        rows.extend(
            self._wiki_attention_repair_priorities(
                pages=pages,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
                latest_wiki_attention_states=latest_wiki_attention_states,
            )
        )
        rows.extend(
            self._wiki_memory_card_quality_repair_priorities(
                pages=pages,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
                latest_wiki_memory_card_quality_states=(
                    latest_wiki_memory_card_quality_states
                ),
            )
        )
        rows.extend(
            self._lint_repair_priorities(
                pages=pages,
                target_scope=target_scope,
                requested_symbols=requested_symbols,
            )
        )
        self._attach_repair_loop_effectiveness(
            rows,
            repair_priority_effectiveness=repair_priority_effectiveness,
        )
        rows.sort(
            key=lambda row: (
                self._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                -int(row.get("sample_count") or 0),
                float(row.get("helpful_score") or 0.0),
                -float(row.get("drawdown_pressure") or 0.0),
                str(row.get("page_id") or ""),
                str(row.get("source_id") or ""),
            )
        )
        selected_rows = self._repair_priority_budget_slice(rows, limit=limit)
        if budget_summary is not None:
            budget_summary.update(
                _repair_priority_budget_summary(rows, selected_rows)
            )
        return selected_rows

    @classmethod
    def _wiki_application_coverage_repair_priorities(
        cls,
        *,
        target_scope: str,
        wiki_application_coverage: dict[str, Any],
    ) -> list[dict[str, Any]]:
        coverage = (
            wiki_application_coverage.get("coverage")
            if isinstance(wiki_application_coverage.get("coverage"), dict)
            else {}
        )
        missing_count = _safe_int(
            coverage.get("closed_block_outcomes_without_horizon")
        )
        missing_pct = _safe_float(
            coverage.get("closed_block_outcomes_without_horizon_pct")
        )
        has_alert = any(
            isinstance(row, dict)
            and str(row.get("code") or "") == "wiki_outcome_horizon_missing"
            for row in list(wiki_application_coverage.get("alerts") or [])
        )
        if missing_count <= 0 and missing_pct <= 0.0 and not has_alert:
            return []

        clean_scope = str(target_scope or "").strip().lower()
        page_id = f"{clean_scope}.application.closed_block_outcomes"
        action_type = "reproject_closed_block_outcome_horizons"
        repair_target = {
            "page_id": page_id,
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
        return [
            {
                "page_id": page_id,
                "page_type": "application_coverage",
                "priority_type": "wiki_application_coverage",
                "symbols": [],
                "symbol_overlap": [],
                "status": "repair_queue",
                "sample_count": missing_count,
                "win_rate": 0.0,
                "expectancy": 0.0,
                "avg_return_pct": 0.0,
                "median_mae_pct": 0.0,
                "drawdown_pressure": 0.0,
                "helpful_score": -96.0,
                "confidence": 1.0,
                "severity_score": 96.0,
                "source_type": "wiki_application_coverage",
                "source_id": f"repair:outcome_horizon:{clean_scope}",
                "action_type": action_type,
                "repair_status": "scheduled",
                "quality_warnings": ["closed_block_outcome_horizon_missing"],
                "impacted_page_ids": [page_id],
                "impacted_symbols": [],
                "repair_targets": [repair_target],
                "decision_use": _jue_wiki_repair_priority_decision_use(
                    {
                        "page_id": page_id,
                        "priority_type": "wiki_application_coverage",
                        "action_type": action_type,
                    }
                ),
                "hard_blocker": False,
                "candidate_resolution_required": True,
                "reasons": [
                    f"closed_block_outcomes_without_horizon:{missing_count}",
                    f"closed_block_outcomes_without_horizon_pct:{missing_pct:.1f}",
                ],
                "repair_action": (
                    "reproject closed block outcomes so page effectiveness is "
                    "credited to the block horizon or crypto lane"
                ),
            }
        ]

    @classmethod
    def _requested_symbol_coverage_repair_priorities(
        cls,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
        requested_symbol_summary_coverage: dict[str, Any],
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        coverage_status = str(
            requested_symbol_summary_coverage.get(
                "requested_symbol_summary_coverage_status"
            )
            or ""
        ).strip().lower()
        symbols = [
            str(symbol).strip().upper()
            for symbol in list(
                requested_symbol_summary_coverage.get(
                    "requested_symbol_missing_summary_symbols"
                )
                if "requested_symbol_missing_summary_symbols"
                in requested_symbol_summary_coverage
                else requested_symbol_summary_coverage.get(
                    "requested_symbol_unsummarized_symbols"
                )
                or []
            )
            if str(symbol).strip()
        ]
        degraded_reasons = [
            row
            for row in list(
                requested_symbol_summary_coverage.get(
                    "requested_symbol_degraded_summary_reasons"
                )
                or []
            )
            if isinstance(row, dict)
            and str(row.get("symbol") or "").strip()
        ]
        degraded_symbols = [
            str(symbol).strip().upper()
            for symbol in list(
                requested_symbol_summary_coverage.get(
                    "requested_symbol_degraded_summary_symbols"
                )
                or []
            )
            if str(symbol).strip()
        ]
        if not degraded_symbols:
            degraded_symbols = [
                str(row.get("symbol") or "").strip().upper()
                for row in degraded_reasons
                if str(row.get("symbol") or "").strip()
            ]
        if coverage_status not in {"partial", "none"} and not degraded_symbols:
            return []
        queued_source_ids = cls._open_repair_queue_source_ids(
            pages=pages,
            target_scope=target_scope,
        )
        rows: list[dict[str, Any]] = []
        for symbol in symbols[: max(int(limit), 0)]:
            source_id = f"repair:coverage:{target_scope}:{symbol}"
            if source_id in queued_source_ids:
                continue
            page_id = f"{target_scope}.symbol.{symbol}"
            repair_target = {
                "page_id": page_id,
                "symbol": symbol,
                "recommended_action": (
                    "collect_or_rebuild_requested_symbol_wiki_summary"
                ),
            }
            action_type = "refresh_requested_symbol_summary"
            rows.append(
                {
                    "page_id": page_id,
                    "page_type": "symbol",
                    "priority_type": "requested_symbol_coverage",
                    "symbols": [symbol],
                    "symbol_overlap": [symbol] if symbol in requested_symbols else [],
                    "status": "repair_queue",
                    "sample_count": 0,
                    "win_rate": 0.0,
                    "expectancy": 0.0,
                    "avg_return_pct": 0.0,
                    "median_mae_pct": 0.0,
                    "drawdown_pressure": 0.0,
                    "helpful_score": -88.0,
                    "confidence": 1.0,
                    "severity_score": 88.0,
                    "source_type": "selection_budget_report",
                    "source_id": source_id,
                    "action_type": action_type,
                    "repair_status": "scheduled",
                    "quality_warnings": ["requested_symbol_summary_missing"],
                    "impacted_page_ids": [page_id],
                    "impacted_symbols": [symbol],
                    "repair_targets": [repair_target],
                    "decision_use": _jue_wiki_repair_priority_decision_use(
                        {
                            "page_id": page_id,
                            "priority_type": "requested_symbol_coverage",
                            "action_type": action_type,
                        }
                    ),
                    "hard_blocker": False,
                    "candidate_resolution_required": True,
                    "reasons": [
                        "requested_symbol_summary_missing",
                        f"coverage_status:{coverage_status}",
                    ],
                    "repair_action": (
                        "collect_or_rebuild_requested_symbol_wiki_summary"
                    ),
                }
            )
        for reason in degraded_reasons[: max(int(limit), 0)]:
            symbol = str(reason.get("symbol") or "").strip().upper()
            if not symbol or symbol not in degraded_symbols:
                continue
            source_id = f"repair:degraded_summary:{target_scope}:{symbol}"
            if source_id in queued_source_ids:
                continue
            page_id = f"{target_scope}.symbol.{symbol}"
            quality_status = normalize_jue_wiki_quality_status(
                reason.get("quality_status")
            )
            quality_warnings = ["requested_symbol_summary_degraded"]
            for warning in list(reason.get("quality_warnings") or []):
                clean_warning = str(warning).strip()
                if clean_warning and clean_warning not in quality_warnings:
                    quality_warnings.append(clean_warning)
            for warning in list(reason.get("freshness_warnings") or []):
                clean_warning = str(warning).strip()
                if clean_warning and clean_warning not in quality_warnings:
                    quality_warnings.append(clean_warning)
            repair_target = {
                "page_id": page_id,
                "symbol": symbol,
                "recommended_action": (
                    "refresh_stale_or_weak_requested_symbol_wiki_summary"
                ),
            }
            action_type = "refresh_requested_symbol_summary"
            rows.append(
                {
                    "page_id": page_id,
                    "page_type": "symbol",
                    "priority_type": "requested_symbol_degraded_summary",
                    "symbols": [symbol],
                    "symbol_overlap": [symbol] if symbol in requested_symbols else [],
                    "status": "repair_queue",
                    "sample_count": 0,
                    "win_rate": 0.0,
                    "expectancy": 0.0,
                    "avg_return_pct": 0.0,
                    "median_mae_pct": 0.0,
                    "drawdown_pressure": 0.0,
                    "helpful_score": -70.0,
                    "confidence": 1.0,
                    "severity_score": 70.0,
                    "source_type": "selection_budget_report",
                    "source_id": source_id,
                    "action_type": action_type,
                    "repair_status": "scheduled",
                    "quality_status": quality_status,
                    "quality_warnings": quality_warnings[:8],
                    "impacted_page_ids": [page_id],
                    "impacted_symbols": [symbol],
                    "repair_targets": [repair_target],
                    "decision_use": _jue_wiki_repair_priority_decision_use(
                        {
                            "page_id": page_id,
                            "priority_type": "requested_symbol_degraded_summary",
                            "action_type": action_type,
                        }
                    ),
                    "hard_blocker": False,
                    "candidate_resolution_required": True,
                    "reasons": [
                        "requested_symbol_summary_degraded",
                        f"freshness:{reason.get('freshness') or 'unknown'}",
                        (
                            "freshness_status:"
                            f"{reason.get('freshness_status') or 'unknown'}"
                        ),
                        f"quality_status:{quality_status or 'unknown'}",
                    ],
                    "repair_action": (
                        "refresh_stale_or_weak_requested_symbol_wiki_summary"
                    ),
                }
            )
        return rows

    @classmethod
    def _open_repair_queue_source_ids(
        cls,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
    ) -> set[str]:
        source_ids: set[str] = set()
        for page in pages:
            if str(page.get("scope") or "") != target_scope:
                continue
            for ref in cls._repair_queue_refs_for_page(page):
                repair_status = str(ref.get("status") or "").strip().lower()
                if repair_status not in {"scheduled", "unresolved"}:
                    continue
                source_id = str(ref.get("source_id") or "").strip()
                if source_id:
                    source_ids.add(source_id)
        return source_ids

    @classmethod
    def _open_repair_queue_symbols(
        cls,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
    ) -> set[str]:
        symbols: set[str] = set()
        for page in pages:
            if str(page.get("scope") or "") != target_scope:
                continue
            for ref in cls._repair_queue_refs_for_page(page):
                repair_status = str(ref.get("status") or "").strip().lower()
                if repair_status not in {"scheduled", "unresolved"}:
                    continue
                for symbol in list(ref.get("symbols") or []):
                    clean_symbol = str(symbol).strip().upper()
                    if clean_symbol:
                        symbols.add(clean_symbol)
                for symbol in list(ref.get("impacted_symbols") or []):
                    clean_symbol = str(symbol).strip().upper()
                    if clean_symbol:
                        symbols.add(clean_symbol)
                for target in list(ref.get("repair_targets") or []):
                    if not isinstance(target, dict):
                        continue
                    clean_symbol = str(target.get("symbol") or "").strip().upper()
                    if clean_symbol:
                        symbols.add(clean_symbol)
        return symbols

    @classmethod
    def _wiki_attention_repair_priorities(
        cls,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in pages:
            if str(page.get("scope") or "") != target_scope:
                continue
            for ref in cls._wiki_attention_refs_for_page(
                page,
                latest_wiki_attention_states=latest_wiki_attention_states,
            ):
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(ref.get("symbols") or page.get("symbols") or [])[:8]
                    if str(symbol).strip()
                ]
                symbol_overlap = sorted(set(symbols) & requested_symbols)
                page_id = str(page.get("page_id") or "")
                manager_run = str(ref.get("manager_run") or "").strip()
                component = str(ref.get("component") or "").strip()
                action_type = str(ref.get("action_type") or "").strip()
                recommended = str(ref.get("recommended") or "").strip()
                targets = [
                    str(item).strip()
                    for item in list(ref.get("targets") or [])[:8]
                    if str(item).strip()
                ]
                repair_action = ": ".join(
                    part
                    for part in (
                        component or "resolve_jue_wiki_attention",
                        recommended
                        or "resolve, probe, or explicitly reject this wiki attention target",
                    )
                    if part
                )
                rows.append(
                    {
                        "page_id": page_id,
                        "page_type": str(page.get("page_type") or ""),
                        "priority_type": "wiki_attention",
                        "source_type": "jue_wiki_attention",
                        "source_id": cls._wiki_attention_source_id(
                            page_id=page_id,
                            manager_run=manager_run,
                            component=component,
                            action_type=action_type,
                            targets=targets,
                            symbols=symbols,
                        ),
                        "status": "wiki_attention",
                        "repair_status": str(ref.get("resolution") or "unresolved"),
                        "symbols": symbols,
                        "symbol_overlap": symbol_overlap,
                        "sample_count": 1,
                        "win_rate": 0.0,
                        "expectancy": 0.0,
                        "drawdown_pressure": 0.0,
                        "helpful_score": -32.0,
                        "confidence": 1.0,
                        "severity_score": 32.0 + (8.0 if symbol_overlap else 0.0),
                        "action_type": action_type or "resolve_wiki_attention",
                        "decision_use": "wiki_attention_resolution_check",
                        "quality_warnings": ["wiki_attention_unresolved"],
                        "impacted_page_ids": targets or [page_id],
                        "impacted_symbols": symbols,
                        "repair_targets": [
                            {
                                key: value
                                for key, value in {
                                    "page_id": target or page_id,
                                    "symbol": symbols[0] if symbols else "",
                                    "recommended_action": action_type
                                    or "resolve_wiki_attention",
                                }.items()
                                if value
                            }
                            for target in (targets or [page_id])
                        ][:8],
                        "candidate_resolution_required": True,
                        "hard_blocker": False,
                        "reasons": [
                            "wiki_attention:unresolved",
                            *(
                                [f"component:{component}"]
                                if component
                                else []
                            ),
                            *(
                                [f"action_type:{action_type}"]
                                if action_type
                                else []
                            ),
                        ],
                        "repair_action": repair_action,
                    }
                )
        rows.sort(
            key=lambda row: (
                cls._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                str(row.get("source_id") or ""),
            )
        )
        return rows[: max(int(limit), 0)]

    @staticmethod
    def _wiki_attention_source_id(
        *,
        page_id: str,
        manager_run: str,
        component: str,
        action_type: str,
        targets: list[str],
        symbols: list[str],
    ) -> str:
        def clean(value: Any, *, fallback: str = "") -> str:
            text = str(value or "").strip() or fallback
            text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
            return text.strip("_")[:100]

        identity_tail = [
            clean(component, fallback="component"),
            clean(action_type, fallback="action"),
            *[clean(item) for item in (targets or symbols)[:3]],
        ]
        parts = [
            clean(page_id, fallback="page"),
            "manager_run",
            clean(manager_run, fallback="unknown"),
            "attention",
            *[part for part in identity_tail if part],
        ]
        return ":".join(part for part in parts if part)

    @classmethod
    def _latest_wiki_attention_states(
        cls,
        pages: list[dict[str, Any]],
        *,
        target_scope: str,
    ) -> dict[tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]]:
        states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ] = {}
        for page in pages:
            if str(page.get("scope") or "").strip().lower() != target_scope:
                continue
            for ref in cls._wiki_attention_rows_for_page(page, include_handled=True):
                key = ref.get("attention_key")
                if not isinstance(key, tuple):
                    continue
                previous = states.get(key)
                if previous is None or cls._wiki_attention_state_sort_key(
                    ref
                ) > cls._wiki_attention_state_sort_key(previous):
                    states[key] = ref
        return states

    @staticmethod
    def _wiki_attention_state_sort_key(ref: dict[str, Any]) -> tuple[float, int, int]:
        resolution = str(ref.get("resolution") or "").strip().lower()
        handled_rank = 1 if resolution and resolution != "unresolved" else 0
        return (
            _safe_datetime_timestamp(ref.get("observed_at")),
            _safe_int(ref.get("manager_run")),
            handled_rank,
        )

    @classmethod
    def _wiki_attention_key(
        cls,
        *,
        component: str,
        action_type: str,
        targets: list[str],
        page_id: str,
        symbols: list[str],
    ) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
        symbol_key = [] if targets else symbols
        return (
            str(component or "").strip().lower(),
            str(action_type or "").strip().lower(),
            tuple(str(target).strip().lower() for target in (targets or [page_id])),
            tuple(
                str(symbol).strip().upper()
                for symbol in symbol_key
                if str(symbol).strip()
            ),
        )

    @classmethod
    def _wiki_attention_ref_from_line(
        cls,
        line: str,
        *,
        page_id: str,
        page_symbols: list[str],
    ) -> dict[str, Any] | None:
        if "resolution=unresolved" not in line and "status=active" not in line:
            return None
        if "manager_run=" not in line:
            return None
        pairs = {
            match.group(1): match.group(2).strip()
            for match in re.finditer(r"([A-Za-z_]+)=([^,]+)", line)
        }
        resolution = str(pairs.get("resolution") or "").strip().lower()
        targets = [
            item.strip()
            for item in str(pairs.get("targets") or "").split("|")
            if item.strip()
        ]
        if not targets and pairs.get("target"):
            targets = [str(pairs.get("target") or "").strip()]
        key = cls._wiki_attention_key(
            component=str(pairs.get("component") or ""),
            action_type=str(pairs.get("action") or ""),
            targets=targets,
            page_id=page_id,
            symbols=page_symbols,
        )
        return {
            "manager_run": pairs.get("manager_run", ""),
            "observed_at": pairs.get("observed_at", ""),
            "resolution": resolution or "unresolved",
            "component": pairs.get("component", ""),
            "action_type": pairs.get("action", ""),
            "recommended": pairs.get("recommended", ""),
            "targets": targets,
            "symbols": page_symbols,
            "attention_key": key,
        }

    @classmethod
    def _wiki_attention_rows_for_page(
        cls,
        page: dict[str, Any],
        *,
        include_handled: bool,
    ) -> list[dict[str, Any]]:
        content = str(page.get("content") or "")
        if "Jue Wiki Attention" not in content:
            return []
        rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
        page_symbols = [
            str(symbol).strip().upper()
            for symbol in list(page.get("symbols") or [])
            if str(symbol).strip()
        ]
        page_id = str(page.get("page_id") or "").strip()
        for line in content.splitlines():
            ref = cls._wiki_attention_ref_from_line(
                line,
                page_id=page_id,
                page_symbols=page_symbols,
            )
            if not ref:
                continue
            resolution = str(ref.get("resolution") or "").strip().lower()
            key = ref.get("attention_key")
            if not isinstance(key, tuple):
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if not include_handled and resolution and resolution != "unresolved":
                continue
            rows.append(ref)
        return rows

    @classmethod
    def _wiki_attention_refs_for_page(
        cls,
        page: dict[str, Any],
        latest_wiki_attention_states: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> list[dict[str, Any]]:
        refs = cls._wiki_attention_rows_for_page(page, include_handled=False)
        if not latest_wiki_attention_states:
            return refs
        rows: list[dict[str, Any]] = []
        for ref in refs:
            key = ref.get("attention_key")
            latest = latest_wiki_attention_states.get(key) if isinstance(key, tuple) else None
            if latest is None:
                rows.append(ref)
                continue
            if cls._wiki_attention_state_sort_key(ref) < cls._wiki_attention_state_sort_key(
                latest
            ):
                continue
            latest_resolution = str(latest.get("resolution") or "").strip().lower()
            if latest_resolution and latest_resolution != "unresolved":
                continue
            rows.append(ref)
        return rows

    @classmethod
    def _wiki_memory_card_quality_repair_priorities(
        cls,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in pages:
            if str(page.get("scope") or "") != target_scope:
                continue
            page_id = str(page.get("page_id") or "")
            for ref in cls._wiki_memory_card_quality_refs_for_page(
                page,
                latest_wiki_memory_card_quality_states=(
                    latest_wiki_memory_card_quality_states
                ),
            ):
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(ref.get("symbols") or page.get("symbols") or [])[:8]
                    if str(symbol).strip()
                ]
                symbol_overlap = sorted(set(symbols) & requested_symbols)
                manager_run = str(ref.get("manager_run") or "").strip()
                required = str(ref.get("required") or "").strip()
                missing_fields = [
                    str(item).strip()
                    for item in list(ref.get("missing_fields") or [])[:8]
                    if str(item).strip()
                ]
                required_checks = [
                    str(item).strip()
                    for item in list(ref.get("required_checks") or [])[:8]
                    if str(item).strip()
                ]
                missing_field_weights = {
                    "stance": 8.0,
                    "durable_facts": 10.0,
                    "lessons": 8.0,
                    "open_questions": 3.0,
                }
                missing_field_severity = sum(
                    missing_field_weights.get(field, 2.0)
                    for field in missing_fields
                )
                core_missing_fields = [
                    field
                    for field in missing_fields
                    if field in {"stance", "durable_facts", "lessons"}
                ]
                repair_action = required or (
                    "cross-check weak wiki memory card with current research, "
                    "market data, and block history before high-confidence action"
                )
                rows.append(
                    {
                        "page_id": page_id,
                        "page_type": str(page.get("page_type") or ""),
                        "priority_type": "memory_card_quality",
                        "source_type": "jue_wiki_memory_card_quality",
                        "source_id": cls._wiki_memory_card_quality_source_id(
                            page_id=page_id,
                            manager_run=manager_run,
                            required=required,
                            symbols=symbols,
                            missing_fields=missing_fields,
                        ),
                        "status": "memory_card_quality",
                        "repair_status": str(ref.get("resolution") or "unresolved"),
                        "symbols": symbols,
                        "symbol_overlap": symbol_overlap,
                        "sample_count": 1,
                        "win_rate": 0.0,
                        "expectancy": 0.0,
                        "drawdown_pressure": 0.0,
                        "helpful_score": -30.0,
                        "confidence": 1.0,
                        "severity_score": (
                            30.0
                            + (8.0 if symbol_overlap else 0.0)
                            + missing_field_severity
                        ),
                        "action_type": "cross_check_memory_card_quality",
                        "decision_use": "memory_card_quality_resolution_check",
                        "quality_warnings": ["memory_card_quality_unresolved"],
                        "impacted_page_ids": [page_id],
                        "impacted_symbols": symbols,
                        "missing_fields": missing_fields,
                        "required_checks": required_checks,
                        "repair_targets": [
                            {
                                key: value
                                for key, value in {
                                    "page_id": page_id,
                                    "symbol": symbols[0] if symbols else "",
                                    "recommended_action": (
                                        "cross_check_memory_card_quality"
                                    ),
                                }.items()
                                if value
                            }
                        ],
                        "candidate_resolution_required": True,
                        "hard_blocker": False,
                        "reasons": [
                            "memory_card_quality:unresolved",
                            *([f"required:{required}"] if required else []),
                            *(
                                [f"missing_fields:{'|'.join(missing_fields)}"]
                                if missing_fields
                                else []
                            ),
                            *(
                                [
                                    "core_missing_fields:"
                                    + "|".join(core_missing_fields)
                                ]
                                if core_missing_fields
                                else []
                            ),
                        ],
                        "repair_action": repair_action,
                    }
                )
        rows.sort(
            key=lambda row: (
                cls._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                str(row.get("source_id") or ""),
            )
        )
        return rows[: max(int(limit), 0)]

    @staticmethod
    def _wiki_memory_card_quality_source_id(
        *,
        page_id: str,
        manager_run: str,
        required: str,
        symbols: list[str],
        missing_fields: list[str] | None = None,
    ) -> str:
        def clean(value: Any, *, fallback: str = "") -> str:
            text = str(value or "").strip() or fallback
            text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
            return text.strip("_")[:100]

        parts = [
            clean(page_id, fallback="page"),
            "manager_run",
            clean(manager_run, fallback="unknown"),
            "memory_card_quality",
            clean(required, fallback="required"),
            *[clean(symbol) for symbol in symbols[:3]],
        ]
        field_parts = [clean(field) for field in list(missing_fields or [])[:4]]
        if field_parts:
            parts.extend(["fields", *field_parts])
        return ":".join(part for part in parts if part)

    @classmethod
    def _latest_wiki_memory_card_quality_states(
        cls,
        pages: list[dict[str, Any]],
        *,
        target_scope: str,
    ) -> dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]]:
        states: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}
        for page in pages:
            if str(page.get("scope") or "").strip().lower() != target_scope:
                continue
            for ref in cls._wiki_memory_card_quality_rows_for_page(
                page,
                include_handled=True,
            ):
                key = ref.get("quality_key")
                if not isinstance(key, tuple):
                    continue
                previous = states.get(key)
                if previous is None or cls._wiki_memory_card_quality_state_sort_key(
                    ref
                ) > cls._wiki_memory_card_quality_state_sort_key(previous):
                    states[key] = ref
        return states

    @staticmethod
    def _wiki_memory_card_quality_state_sort_key(
        ref: dict[str, Any],
    ) -> tuple[float, int, int]:
        resolution = str(ref.get("resolution") or "").strip().lower()
        handled_rank = 1 if resolution and resolution != "unresolved" else 0
        return (
            _safe_datetime_timestamp(ref.get("observed_at")),
            _safe_int(ref.get("manager_run")),
            handled_rank,
        )

    @classmethod
    def _has_unresolved_wiki_memory_card_quality(
        cls,
        page: dict[str, Any],
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> bool:
        return bool(
            cls._wiki_memory_card_quality_refs_for_page(
                page,
                latest_wiki_memory_card_quality_states=(
                    latest_wiki_memory_card_quality_states
                ),
            )
        )

    @classmethod
    def _wiki_memory_card_quality_refs_for_page(
        cls,
        page: dict[str, Any],
        latest_wiki_memory_card_quality_states: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
        ]
        | None = None,
    ) -> list[dict[str, Any]]:
        refs = cls._wiki_memory_card_quality_rows_for_page(
            page,
            include_handled=False,
        )
        if not latest_wiki_memory_card_quality_states:
            return refs
        rows: list[dict[str, Any]] = []
        for ref in refs:
            key = ref.get("quality_key")
            latest = (
                latest_wiki_memory_card_quality_states.get(key)
                if isinstance(key, tuple)
                else None
            )
            if latest is None:
                rows.append(ref)
                continue
            if cls._wiki_memory_card_quality_state_sort_key(
                ref
            ) < cls._wiki_memory_card_quality_state_sort_key(latest):
                continue
            latest_resolution = str(latest.get("resolution") or "").strip().lower()
            if latest_resolution and latest_resolution != "unresolved":
                continue
            rows.append(ref)
        return rows

    @classmethod
    def _wiki_memory_card_quality_rows_for_page(
        cls,
        page: dict[str, Any],
        *,
        include_handled: bool,
    ) -> list[dict[str, Any]]:
        content = str(page.get("content") or "")
        if "Jue Wiki Memory Card Quality" not in content:
            return []
        rows: list[dict[str, Any]] = []
        page_symbols = [
            str(symbol).strip().upper()
            for symbol in list(page.get("symbols") or [])
            if str(symbol).strip()
        ]
        for line in content.splitlines():
            ref = cls._wiki_memory_card_quality_ref_from_line(
                line,
                page_id=str(page.get("page_id") or ""),
                page_symbols=page_symbols,
            )
            if not ref:
                continue
            resolution = str(ref.get("resolution") or "").strip().lower()
            if not include_handled and resolution and resolution != "unresolved":
                continue
            rows.append(ref)
        return rows

    @classmethod
    def _wiki_memory_card_quality_ref_from_line(
        cls,
        line: str,
        *,
        page_id: str,
        page_symbols: list[str],
    ) -> dict[str, Any] | None:
        if "manager_run=" not in line:
            return None
        pairs = {
            match.group(1): match.group(2).strip()
            for match in re.finditer(r"([A-Za-z_]+)=([^,]+)", line)
        }
        resolution = str(pairs.get("resolution") or "").strip().lower()
        if not resolution:
            return None
        symbols = [
            symbol.strip().upper()
            for symbol in re.split(r"[|/ ]+", str(pairs.get("symbols") or ""))
            if symbol.strip()
        ]
        symbols = symbols or page_symbols
        missing_fields = [
            item.strip()
            for item in re.split(r"[|/ ]+", str(pairs.get("missing_fields") or ""))
            if item.strip()
        ]
        required_checks = [
            item.strip()
            for item in str(pairs.get("required_checks") or "").split("|")
            if item.strip()
        ]
        quality_key = cls._wiki_memory_card_quality_key(
            required=str(pairs.get("required") or ""),
            symbols=symbols,
            page_id=page_id,
            missing_fields=missing_fields,
        )
        return {
            "manager_run": pairs.get("manager_run", ""),
            "observed_at": pairs.get("observed_at", ""),
            "status": pairs.get("status", ""),
            "resolution": resolution,
            "required": pairs.get("required", ""),
            "missing_fields": missing_fields,
            "required_checks": required_checks,
            "symbols": symbols,
            "quality_key": quality_key,
        }

    @staticmethod
    def _wiki_memory_card_quality_key(
        *,
        required: str,
        symbols: list[str],
        page_id: str,
        missing_fields: list[str] | None = None,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        symbol_key = tuple(
            sorted(
                str(symbol).strip().upper()
                for symbol in symbols
                if str(symbol).strip()
            )
        )
        if not symbol_key:
            symbol_key = (str(page_id or "").strip().lower(),)
        missing_field_key = tuple(
            sorted(
                str(field).strip().lower()
                for field in list(missing_fields or [])
                if str(field).strip()
            )
        )
        return (str(required or "").strip().lower(), symbol_key, missing_field_key)

    def _lint_repair_priorities(
        self,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
    ) -> list[dict[str, Any]]:
        pages_by_id = {
            str(page.get("page_id") or ""): page
            for page in pages
            if str(page.get("page_id") or "").strip()
        }
        rows: list[dict[str, Any]] = []
        for finding in self.service.list_lint_findings(
            scope=target_scope,
            status="open",
        ):
            page_id = str(finding.get("page_id") or "")
            page = pages_by_id.get(page_id, {})
            page_symbols = [
                str(symbol).strip().upper()
                for symbol in list(page.get("symbols") or [])
                if str(symbol).strip()
            ]
            symbol_overlap = sorted(set(page_symbols) & requested_symbols)
            finding_type = str(finding.get("finding_type") or "unknown").strip()
            action_type = f"repair_{finding_type or 'wiki_lint_finding'}"
            evidence = (
                finding.get("evidence")
                if isinstance(finding.get("evidence"), dict)
                else {}
            )
            rows.append(
                {
                    "page_id": page_id,
                    "page_type": str(page.get("page_type") or ""),
                    "priority_type": "lint",
                    "source_type": "wiki_lint_findings",
                    "source_id": str(finding.get("finding_id") or ""),
                    "finding_type": finding_type,
                    "status": "lint_warning",
                    "symbols": page_symbols,
                    "symbol_overlap": symbol_overlap,
                    "sample_count": int(evidence.get("gap_count") or 1),
                    "severity_score": self._lint_finding_severity_score(
                        finding_type
                    ),
                    "action_type": action_type,
                    "decision_use": "wiki_lint_repair",
                    "quality_warnings": [finding_type] if finding_type else [],
                    "reasons": self._lint_repair_reasons(
                        finding_type=finding_type,
                        evidence=evidence,
                    ),
                    "repair_action": self._repair_action_for_lint_finding(
                        finding_type
                    ),
                    "repair_targets": [
                        {
                            key: value
                            for key, value in {
                                "page_id": page_id,
                                "symbol": symbol_overlap[0]
                                if symbol_overlap
                                else (page_symbols[0] if page_symbols else ""),
                                "recommended_action": action_type,
                            }.items()
                            if value
                        }
                    ],
                }
            )
        rows.sort(
            key=lambda row: (
                self._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                str(row.get("page_id") or ""),
                str(row.get("source_id") or ""),
            )
        )
        return rows

    @staticmethod
    def _lint_finding_severity_score(finding_type: str) -> float:
        scores = {
            "scope_leakage": 12.0,
            "source_ref_identity_gap": 10.0,
            "missing_sources": 9.0,
            "oversized_page": 6.0,
            "stale_page": 4.0,
        }
        return scores.get(str(finding_type or "").strip(), 5.0)

    @staticmethod
    def _lint_repair_reasons(
        *,
        finding_type: str,
        evidence: dict[str, Any],
    ) -> list[str]:
        reasons = [f"lint_finding:{finding_type or 'unknown'}"]
        gap_count = evidence.get("gap_count")
        if gap_count not in (None, "", 0):
            reasons.append(f"source_ref_identity_gap_count:{int(gap_count)}")
        return reasons

    @staticmethod
    def _repair_action_for_lint_finding(finding_type: str) -> str:
        actions = {
            "source_ref_identity_gap": (
                "repair wiki source reference identity gaps before reusing this memory"
            ),
            "missing_sources": "attach audit-ready source refs before reusing this memory",
            "scope_leakage": "remove cross-scope leakage before reusing this memory",
            "oversized_page": "compact oversized wiki page before prompt injection",
            "stale_page": "refresh stale wiki page before strong reuse",
        }
        return actions.get(
            str(finding_type or "").strip(),
            "repair open wiki lint finding before strong reuse",
        )

    @staticmethod
    def _repair_priority_scope_rank(row: dict[str, Any]) -> int:
        if str(row.get("action_type") or "") == "repair_decision_adjustment_audit_contract":
            return 0
        if str(row.get("action_type") or "") == "repair_quality_warning_effectiveness":
            return 0
        if str(row.get("action_type") or "") == "repair_usage_guidance_contract":
            return 0
        if row.get("symbol_overlap"):
            return 1
        return 2

    @classmethod
    def _repair_priority_budget_slice(
        cls,
        rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return _repair_priority_budget_slice(rows, limit=limit)

    @classmethod
    def _attach_repair_loop_effectiveness(
        cls,
        rows: list[dict[str, Any]],
        *,
        repair_priority_effectiveness: dict[str, Any],
    ) -> None:
        metrics = [
            row
            for row in list(repair_priority_effectiveness.get("top_degraded") or [])
            if isinstance(row, dict)
        ]
        action_level_metrics = [
            row
            for row in list(
                repair_priority_effectiveness.get("repair_loop_status_metrics") or []
            )
            if isinstance(row, dict)
        ]
        if not metrics and not action_level_metrics:
            return
        for row in rows:
            metric = cls._matching_repair_loop_effectiveness(row, metrics)
            reason_prefix = "repair_loop"
            if not metric:
                metric = cls._matching_action_level_repair_loop_status_metric(
                    row,
                    action_level_metrics,
                )
                reason_prefix = "repair_loop_status_metric"
            if not metric:
                continue
            status = str(metric.get("status") or "").strip()
            sample_count = _safe_int(metric.get("sample_count"))
            missed_count = _safe_int(metric.get("missed_count"))
            resolved_count = _safe_int(metric.get("resolved_count"))
            resolution_rate = _safe_float(metric.get("resolution_rate"))
            row["repair_loop_status"] = status
            row["repair_loop_sample_count"] = sample_count
            row["repair_loop_missed_count"] = missed_count
            row["repair_loop_resolved_count"] = resolved_count
            row["repair_loop_resolution_rate"] = resolution_rate
            action_type = str(metric.get("action_type") or "").strip()
            decision_use = str(metric.get("decision_use") or "").strip()
            if action_type:
                row["repair_loop_action_type"] = action_type
                row.setdefault("action_type", action_type)
            if decision_use:
                row["repair_loop_decision_use"] = decision_use
            loop_severity = cls._repair_loop_severity_score(
                status=status,
                sample_count=sample_count,
                missed_count=missed_count,
                resolution_rate=resolution_rate,
            )
            row["severity_score"] = _safe_float(row.get("severity_score")) + (
                loop_severity
            )
            metric_reason = f"{reason_prefix}:{status}"
            row["reasons"] = cls._merge_repair_loop_reasons(
                base_reasons=[str(item) for item in list(row.get("reasons") or [])],
                repair_reasons=[
                    metric_reason,
                    f"repair_loop:{status}",
                    f"repair_resolution_rate:{resolution_rate:.4f}",
                ],
                limit=7,
            )

    @classmethod
    def _matching_repair_loop_effectiveness(
        cls,
        row: dict[str, Any],
        metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        row_priority_type = str(row.get("priority_type") or "").strip()
        row_source_id = str(row.get("source_id") or "").strip()
        row_action_type = str(row.get("action_type") or "").strip()
        row_decision_use = _jue_wiki_repair_priority_decision_use(row)
        best: tuple[int, dict[str, Any]] | None = None
        for metric in metrics:
            metric_priority_type = str(metric.get("priority_type") or "").strip()
            metric_source_id = str(metric.get("source_id") or "").strip()
            metric_action_type = str(metric.get("action_type") or "").strip()
            metric_decision_use = str(metric.get("decision_use") or "").strip()
            score = 0
            if row_priority_type and row_priority_type == metric_priority_type:
                score += 3
            if row_source_id and row_source_id == metric_source_id:
                score += 5
            if row_action_type and row_action_type == metric_action_type:
                score += 2
            if row_decision_use and row_decision_use == metric_decision_use:
                score += 1
            if score < 6:
                continue
            if best is None or score > best[0]:
                best = (score, metric)
        return dict(best[1]) if best else {}

    @staticmethod
    def _merge_repair_loop_reasons(
        *,
        base_reasons: list[str],
        repair_reasons: list[str],
        limit: int,
    ) -> list[str]:
        merged: list[str] = []
        for reason in [*repair_reasons, *base_reasons]:
            clean = str(reason or "").strip()
            if not clean or clean in merged:
                continue
            merged.append(clean)
        return merged[: max(int(limit), 0)]

    @classmethod
    def _matching_action_level_repair_loop_status_metric(
        cls,
        row: dict[str, Any],
        metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        row_action_type = str(row.get("action_type") or "").strip()
        if not row_action_type:
            return {}
        for metric in metrics:
            metric_action_type = str(metric.get("action_type") or "").strip()
            if row_action_type and row_action_type == metric_action_type:
                return dict(metric)
        return {}

    @staticmethod
    def _repair_loop_severity_score(
        *,
        status: str,
        sample_count: int,
        missed_count: int,
        resolution_rate: float,
    ) -> float:
        if str(status or "").strip().lower() != "repair_required":
            return 0.0
        score = 18.0
        score += min(max(int(sample_count), 0) * 1.5, 12.0)
        score += min(max(int(missed_count), 0) * 3.0, 15.0)
        score += min(max(1.0 - float(resolution_rate), 0.0) * 10.0, 10.0)
        return score

    @classmethod
    def _repair_queue_priorities(
        cls,
        *,
        pages: list[dict[str, Any]],
        effectiveness_by_page: dict[str, dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        rows_by_action_id: dict[str, dict[str, Any]] = {}
        open_statuses = {"scheduled", "unresolved"}
        for page in pages:
            if str(page.get("scope") or "") != target_scope:
                continue
            page_id = str(page.get("page_id") or "")
            for ref in cls._repair_queue_refs_for_page(page):
                repair_status = str(ref.get("status") or "").strip().lower()
                if repair_status not in open_statuses:
                    continue
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(ref.get("symbols") or page.get("symbols") or [])[:8]
                    if str(symbol).strip()
                ]
                source_id = str(ref.get("source_id") or "")
                if not source_id:
                    source_id = cls._generated_repair_queue_source_id(
                        page_id=page_id,
                        symbols=symbols,
                        action_type=str(ref.get("action_type") or ""),
                    )
                symbol_overlap = sorted(set(symbols) & requested_symbols)
                warnings = [
                    str(item).strip()
                    for item in list(ref.get("quality_warnings") or [])
                    if str(item).strip()
                ]
                diagnostic_reasons = _compact_string_list(
                    ref.get("diagnostic_reasons"),
                    limit=8,
                    max_len=180,
                )
                action_type = str(ref.get("action_type") or "")
                closed_block_outcome_horizon_gap = _safe_int(
                    ref.get("closed_block_outcomes_without_horizon")
                )
                closed_block_outcome_horizon_gap_pct = _safe_float(
                    ref.get("closed_block_outcomes_without_horizon_pct")
                )
                sample_count = max(
                    _safe_int(ref.get("sample_count")),
                    closed_block_outcome_horizon_gap,
                )
                repair_targets = _compact_repair_targets(ref.get("repair_targets"))
                repair_target_effectiveness = (
                    cls._repair_target_effectiveness_for_targets(
                        repair_targets=repair_targets,
                        effectiveness_by_page=effectiveness_by_page,
                    )
                )
                severity = cls._repair_queue_severity_score(
                    action_type=action_type,
                    repair_status=repair_status,
                    warnings=warnings,
                    closed_block_outcomes_without_horizon=(
                        closed_block_outcome_horizon_gap
                    ),
                )
                severity += cls._repair_target_effectiveness_severity_score(
                    repair_target_effectiveness
                )
                rows_by_action_id[source_id] = {
                    "page_id": page_id,
                    "page_type": str(page.get("page_type") or ""),
                    "priority_type": "repair_queue",
                    "symbols": symbols,
                    "symbol_overlap": symbol_overlap,
                    "status": "repair_queue",
                    "sample_count": sample_count,
                    "win_rate": 0.0,
                    "expectancy": 0.0,
                    "avg_return_pct": 0.0,
                    "median_mae_pct": 0.0,
                    "drawdown_pressure": 0.0,
                    "helpful_score": -severity,
                    "confidence": 1.0,
                    "severity_score": severity,
                    "source_type": "wiki_repair_queue",
                    "source_id": source_id,
                    "action_type": action_type,
                    "repair_status": repair_status,
                    "quality_warnings": warnings[:8],
                    "diagnostic_reasons": diagnostic_reasons,
                    "impacted_page_ids": _compact_string_list(
                        ref.get("impacted_page_ids"),
                        limit=12,
                        max_len=180,
                    ),
                    "impacted_symbols": _compact_string_list(
                        ref.get("impacted_symbols"),
                        limit=24,
                        max_len=40,
                    ),
                    "repair_targets": repair_targets,
                    "closed_block_outcomes_without_horizon": (
                        closed_block_outcome_horizon_gap
                    ),
                    "closed_block_outcomes_without_horizon_pct": (
                        closed_block_outcome_horizon_gap_pct
                    ),
                    "repair_target_effectiveness": repair_target_effectiveness,
                    "decision_use": _jue_wiki_repair_priority_decision_use(
                        {
                            "page_id": page_id,
                            "priority_type": "repair_queue",
                            "action_type": action_type,
                        }
                    ),
                    "hard_blocker": False,
                    "candidate_resolution_required": True,
                    "reasons": cls._repair_queue_reasons(
                        action_type=action_type,
                        repair_status=repair_status,
                        warnings=warnings,
                        repair_target_effectiveness=repair_target_effectiveness,
                        closed_block_outcomes_without_horizon=(
                            closed_block_outcome_horizon_gap
                        ),
                        closed_block_outcomes_without_horizon_pct=(
                            closed_block_outcome_horizon_gap_pct
                        ),
                        diagnostic_reasons=diagnostic_reasons,
                    ),
                    "repair_action": str(ref.get("repair_action") or ""),
                }
        rows = list(rows_by_action_id.values())
        rows.sort(
            key=lambda row: (
                cls._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                str(row.get("source_id") or ""),
            )
        )
        return rows[: max(int(limit), 0)]

    @classmethod
    def _repair_queue_refs_for_page(cls, page: dict[str, Any]) -> list[dict[str, Any]]:
        refs = page.get("source_refs") if isinstance(page.get("source_refs"), list) else []
        rows: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            for row in cls._repair_queue_refs_from_source_ref(ref):
                source_id = str(row.get("source_id") or "")
                action_type = str(row.get("action_type") or "")
                status = str(row.get("status") or "")
                symbols = tuple(
                    str(symbol).strip().upper()
                    for symbol in list(row.get("symbols") or [])
                    if str(symbol).strip()
                )
                repair_targets = tuple(
                    (
                        str(target.get("page_id") or ""),
                        str(target.get("symbol") or "").upper(),
                        str(target.get("recommended_action") or ""),
                    )
                    for target in list(row.get("repair_targets") or [])
                    if isinstance(target, dict)
                )
                marker = (source_id, action_type, status, symbols, repair_targets)
                if marker in seen:
                    continue
                seen.add(marker)
                rows.append(row)
        return rows

    @classmethod
    def _repair_queue_refs_from_source_ref(
        cls,
        ref: dict[str, Any],
        *,
        depth: int = 0,
    ) -> list[dict[str, Any]]:
        if depth > 3:
            return []
        rows: list[dict[str, Any]] = []
        source_type = str(ref.get("source_type") or "").strip()
        if source_type == "wiki_repair_queue":
            rows.append(dict(ref))
        for key in ("repair_queue", "wiki_repair_queue"):
            nested = ref.get(key)
            nested_rows = nested if isinstance(nested, list) else [nested]
            for nested_row in nested_rows:
                if not isinstance(nested_row, dict):
                    continue
                row = dict(nested_row)
                row.setdefault("source_type", "wiki_repair_queue")
                rows.extend(
                    cls._repair_queue_refs_from_source_ref(
                        row,
                        depth=depth + 1,
                    )
                )
        nested_refs = ref.get("source_refs")
        if isinstance(nested_refs, list):
            for nested_ref in nested_refs:
                if not isinstance(nested_ref, dict):
                    continue
                rows.extend(
                    cls._repair_queue_refs_from_source_ref(
                        nested_ref,
                        depth=depth + 1,
                    )
                )
        return rows

    @staticmethod
    def _generated_repair_queue_source_id(
        *,
        page_id: str,
        symbols: list[str],
        action_type: str,
    ) -> str:
        symbol_part = "_".join(
            part
            for part in (
                str(symbol).strip().upper()
                for symbol in symbols[:4]
            )
            if part
        )
        action_part = str(action_type or "").strip() or "unknown_action"
        return ":".join(
            part
            for part in (
                str(page_id or "").strip() or "unknown_page",
                "repair_queue",
                symbol_part or "unknown_symbol",
                action_part,
            )
            if part
        )

    @staticmethod
    def _repair_target_effectiveness_for_targets(
        *,
        repair_targets: list[dict[str, str]],
        effectiveness_by_page: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        best: dict[str, Any] = {}
        for target in repair_targets:
            action = str(target.get("recommended_action") or "").strip()
            if not action:
                action = str(target.get("page_id") or "").strip()
            page_id = _repair_target_page_id(action)
            metric = dict(effectiveness_by_page.get(page_id) or {})
            if not metric:
                continue
            metric.setdefault("page_id", page_id)
            compact = _compact_repair_target_effectiveness(metric)
            if not compact:
                continue
            if not best:
                best = compact
                continue
            if _repair_loop_effectiveness_row_sort_key(compact) < (
                _repair_loop_effectiveness_row_sort_key(best)
            ):
                best = compact
        return best

    @staticmethod
    def _repair_queue_severity_score(
        *,
        action_type: str,
        repair_status: str,
        warnings: list[str],
        closed_block_outcomes_without_horizon: int = 0,
    ) -> float:
        score = 24.0
        if repair_status == "unresolved":
            score += 4.0
        if action_type == "repair_quality_warning_effectiveness":
            score += 18.0
        if action_type == "repair_usage_guidance_contract":
            score += 16.0
        if action_type == "reproject_closed_block_outcome_horizons":
            score += 18.0
            score += min(max(int(closed_block_outcomes_without_horizon), 0) * 2.0, 16.0)
        if action_type == "refresh_symbol_financials":
            score += 8.0
        elif action_type == "refresh_symbol_quote":
            score += 7.0
        elif action_type == "refresh_symbol_fundamentals":
            score += 5.0
        score += min(len(warnings) * 2.0, 8.0)
        return score

    @staticmethod
    def _repair_target_effectiveness_severity_score(effectiveness: dict[str, Any]) -> float:
        status = str(effectiveness.get("status") or "").strip().lower()
        if status == "degraded":
            sample_count = _safe_int(effectiveness.get("sample_count"))
            helpful_score = _safe_float(effectiveness.get("helpful_score"))
            return 12.0 + min(sample_count * 1.5, 9.0) + min(abs(helpful_score), 10.0)
        if status == "probe":
            return 2.0
        return 0.0

    @staticmethod
    def _repair_queue_reasons(
        *,
        action_type: str,
        repair_status: str,
        warnings: list[str],
        repair_target_effectiveness: dict[str, Any] | None = None,
        closed_block_outcomes_without_horizon: int = 0,
        closed_block_outcomes_without_horizon_pct: float = 0.0,
        diagnostic_reasons: list[str] | None = None,
    ) -> list[str]:
        reasons = ["repair_queue:open"]
        if repair_status:
            reasons.append(f"repair_status:{repair_status}")
        if action_type:
            reasons.append(f"action_type:{action_type}")
        if action_type == "reproject_closed_block_outcome_horizons":
            reasons.append("horizon_lane_effectiveness_reprojection")
            if closed_block_outcomes_without_horizon > 0:
                reasons.append(
                    "closed_block_outcomes_without_horizon:"
                    f"{int(closed_block_outcomes_without_horizon)}"
                )
            if closed_block_outcomes_without_horizon_pct > 0:
                reasons.append(
                    "closed_block_outcomes_without_horizon_pct:"
                    f"{float(closed_block_outcomes_without_horizon_pct):.1f}"
                )
        for diagnostic_reason in diagnostic_reasons or []:
            clean_reason = str(diagnostic_reason or "").strip()
            if not clean_reason or clean_reason in reasons:
                continue
            tagged_reason = f"diagnostic:{clean_reason}"
            if tagged_reason not in reasons:
                reasons.append(tagged_reason)
        if action_type == "repair_quality_warning_effectiveness":
            reasons.append("global_quality_warning_effectiveness")
        if action_type == "repair_usage_guidance_contract":
            reasons.append("usage_guidance_repair_contract")
        effectiveness = repair_target_effectiveness or {}
        target_status = str(effectiveness.get("status") or "").strip().lower()
        if target_status:
            reasons.append(f"repair_target_effectiveness:{target_status}")
            page_id = str(effectiveness.get("page_id") or "")
            target_id = page_id.removeprefix("repair_target.")
            if target_id:
                reasons.append(
                    f"repair_target_effectiveness:{target_id}:{target_status}"
                )
        reasons.extend(f"warning:{warning}" for warning in warnings[:3])
        return reasons[:8]

    @classmethod
    def _decision_adjustment_audit_repair_priorities(
        cls,
        *,
        trust_profile_effectiveness: dict[str, Any],
        target_scope: str,
        requested_symbols: set[str],
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        clean_scope = str(target_scope or "").strip().lower()
        symbols = sorted(str(symbol).strip().upper() for symbol in requested_symbols if str(symbol).strip())[:8]
        for profile in list(trust_profile_effectiveness.get("trust_profiles") or []):
            if not isinstance(profile, dict):
                continue
            if str(profile.get("decision_scope") or "").strip().lower() != clean_scope:
                continue
            authority = str(profile.get("authority") or "").strip().lower()
            for metric in list(profile.get("decision_adjustment_audit_metrics") or []):
                if not isinstance(metric, dict):
                    continue
                status = str(metric.get("status") or "").strip().lower()
                contract_status = str(metric.get("contract_status") or "").strip().lower()
                if status != "degraded" and contract_status != "repair_required":
                    continue
                action = str(metric.get("action") or "").strip().lower()
                target_risk_posture = str(
                    metric.get("target_risk_posture") or ""
                ).strip().lower()
                source_id = ":".join(
                    part
                    for part in (
                        clean_scope,
                        authority,
                        action,
                        target_risk_posture,
                    )
                    if part
                )
                sample_count = _safe_int(metric.get("sample_count"))
                avg_return_pct = _safe_float(metric.get("avg_return_pct"))
                helpful_score = _safe_float(metric.get("helpful_score"))
                severity = cls._decision_adjustment_audit_repair_severity_score(
                    status=status,
                    contract_status=contract_status,
                    sample_count=sample_count,
                    avg_return_pct=avg_return_pct,
                    helpful_score=helpful_score,
                )
                posture_label = target_risk_posture or "target risk posture"
                rows.append(
                    {
                        "page_id": (
                            "decision_adjustment_audit."
                            f"{source_id or clean_scope or 'unknown'}"
                        ),
                        "page_type": "policy",
                        "priority_type": "decision_adjustment_audit",
                        "symbols": symbols,
                        "symbol_overlap": symbols,
                        "status": "decision_adjustment_audit",
                        "sample_count": sample_count,
                        "win_rate": _safe_float(metric.get("win_rate")),
                        "expectancy": avg_return_pct,
                        "avg_return_pct": avg_return_pct,
                        "median_mae_pct": 0.0,
                        "drawdown_pressure": max(0.0, -avg_return_pct),
                        "helpful_score": helpful_score,
                        "confidence": _safe_float(metric.get("confidence")),
                        "severity_score": severity,
                        "source_type": "jue_wiki_decision_adjustment_audit_metric",
                        "source_id": source_id or "decision_adjustment_audit",
                        "action_type": "repair_decision_adjustment_audit_contract",
                        "repair_status": (
                            "repair_required"
                            if contract_status == "repair_required"
                            else "scheduled"
                        ),
                        "quality_status": normalize_jue_wiki_quality_status(
                            status or contract_status or "unknown"
                        ),
                        "quality_warnings": [
                            "decision_adjustment_audit_degraded",
                        ],
                        "decision_use": "decision_adjustment_audit_repair",
                        "hard_blocker": False,
                        "candidate_resolution_required": True,
                        "reasons": cls._decision_adjustment_audit_repair_reasons(
                            action=action,
                            target_risk_posture=target_risk_posture,
                            status=status,
                            contract_status=contract_status,
                            sample_count=sample_count,
                            avg_return_pct=avg_return_pct,
                        ),
                        "repair_action": (
                            "repair degraded decision adjustment audit contract "
                            f"before reusing {posture_label} escalation"
                        ),
                    }
                )
        rows.sort(
            key=lambda row: (
                cls._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                str(row.get("source_id") or ""),
            )
        )
        return rows[: max(int(limit), 0)]

    @staticmethod
    def _decision_adjustment_audit_repair_severity_score(
        *,
        status: str,
        contract_status: str,
        sample_count: int,
        avg_return_pct: float,
        helpful_score: float,
    ) -> float:
        score = 22.0
        if contract_status == "repair_required":
            score += 12.0
        if status == "degraded":
            score += 10.0
        score += min(max(int(sample_count), 0) * 1.5, 18.0)
        score += min(max(-float(avg_return_pct), 0.0) * 4.0, 16.0)
        score += min(max(-float(helpful_score), 0.0), 10.0)
        return score

    @staticmethod
    def _decision_adjustment_audit_repair_reasons(
        *,
        action: str,
        target_risk_posture: str,
        status: str,
        contract_status: str,
        sample_count: int,
        avg_return_pct: float,
    ) -> list[str]:
        reasons = ["decision_adjustment_audit:repair_required"]
        if action:
            reasons.append(f"action:{action}")
        if target_risk_posture:
            reasons.append(f"target_risk_posture:{target_risk_posture}")
        if status:
            reasons.append(f"status:{status}")
        if contract_status:
            reasons.append(f"contract_status:{contract_status}")
        reasons.append(f"samples:{max(int(sample_count), 0)}")
        reasons.append(f"avg_return_pct:{float(avg_return_pct):.4f}")
        return reasons[:6]

    @classmethod
    def _usage_guidance_effectiveness_repair_priorities(
        cls,
        *,
        effectiveness_by_page: dict[str, dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        clean_scope = str(target_scope or "").strip().lower()
        symbols = sorted(
            str(symbol).strip().upper()
            for symbol in requested_symbols
            if str(symbol).strip()
        )[:8]
        for page_id, metric in effectiveness_by_page.items():
            clean_page_id = str(page_id or "").strip()
            if not clean_page_id.startswith("usage_guidance."):
                continue
            metric_scope = str(metric.get("decision_scope") or "").strip().lower()
            if metric_scope and metric_scope != clean_scope:
                continue
            status = str(metric.get("status") or "").strip().lower()
            if status not in {"degraded", "repair_required"}:
                continue
            sample_count = _safe_int(metric.get("sample_count"))
            avg_return_pct = _safe_float(metric.get("avg_return_pct"))
            if avg_return_pct == 0.0:
                avg_return_pct = _safe_float(metric.get("expectancy"))
            helpful_score = _safe_float(metric.get("helpful_score"))
            drawdown_pressure = _safe_float(metric.get("drawdown_pressure"))
            severity = cls._usage_guidance_effectiveness_severity_score(
                status=status,
                sample_count=sample_count,
                avg_return_pct=avg_return_pct,
                helpful_score=helpful_score,
                drawdown_pressure=drawdown_pressure,
            )
            rows.append(
                {
                    "page_id": clean_page_id,
                    "page_type": "policy",
                    "priority_type": "usage_guidance_effectiveness",
                    "symbols": symbols,
                    "symbol_overlap": symbols,
                    "status": "usage_guidance_effectiveness",
                    "sample_count": sample_count,
                    "win_rate": _safe_float(metric.get("win_rate")),
                    "expectancy": avg_return_pct,
                    "avg_return_pct": avg_return_pct,
                    "median_mae_pct": _safe_float(metric.get("median_mae_pct")),
                    "drawdown_pressure": drawdown_pressure,
                    "helpful_score": helpful_score,
                    "confidence": _safe_float(metric.get("confidence")),
                    "severity_score": severity,
                    "source_type": "jue_wiki_usage_guidance_metric",
                    "source_id": clean_page_id.removeprefix("usage_guidance."),
                    "action_type": "repair_usage_guidance_contract",
                    "repair_status": "repair_required",
                    "quality_status": normalize_jue_wiki_quality_status(status),
                    "quality_warnings": ["usage_guidance_degraded"],
                    "decision_use": "usage_guidance_effectiveness_repair",
                    "hard_blocker": False,
                    "candidate_resolution_required": True,
                    "reasons": cls._usage_guidance_effectiveness_reasons(
                        page_id=clean_page_id,
                        status=status,
                        sample_count=sample_count,
                        avg_return_pct=avg_return_pct,
                        metric=metric,
                    ),
                    "repair_action": (
                        "repair degraded wiki usage guidance before reusing this "
                        "page usage pattern"
                    ),
                }
            )
        rows.sort(
            key=lambda row: (
                cls._repair_priority_scope_rank(row),
                -float(row.get("severity_score") or 0.0),
                str(row.get("source_id") or ""),
            )
        )
        return rows[: max(int(limit), 0)]

    @staticmethod
    def _usage_guidance_effectiveness_severity_score(
        *,
        status: str,
        sample_count: int,
        avg_return_pct: float,
        helpful_score: float,
        drawdown_pressure: float,
    ) -> float:
        score = 20.0
        if str(status or "").strip().lower() == "repair_required":
            score += 8.0
        score += min(max(int(sample_count), 0) * 1.25, 14.0)
        score += min(max(-float(avg_return_pct), 0.0) * 4.0, 14.0)
        score += min(max(-float(helpful_score), 0.0), 10.0)
        score += min(max(float(drawdown_pressure), 0.0) * 2.0, 8.0)
        return score

    @staticmethod
    def _usage_guidance_effectiveness_reasons(
        *,
        page_id: str,
        status: str,
        sample_count: int,
        avg_return_pct: float,
        metric: dict[str, Any],
    ) -> list[str]:
        reasons = [f"usage_guidance_effectiveness:{status or 'unknown'}"]
        guidance_id = str(page_id or "").removeprefix("usage_guidance.")
        if guidance_id:
            reasons.append(f"usage_guidance:{guidance_id}")
        reasons.append(f"samples:{max(int(sample_count), 0)}")
        reasons.append(f"avg_return_pct:{float(avg_return_pct):.4f}")
        for reason in JueWikiSelector._metric_reasons(metric):
            if reason and reason not in reasons:
                reasons.append(reason)
            if len(reasons) >= 8:
                break
        return reasons[:8]

    @classmethod
    def _evidence_quality_repair_priorities(
        cls,
        *,
        pages: list[dict[str, Any]],
        target_scope: str,
        requested_symbols: set[str],
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        rows_by_key: dict[
            tuple[str, str, str, tuple[str, ...]],
            dict[str, Any],
        ] = {}
        for page in pages:
            if str(page.get("scope") or "") != target_scope:
                continue
            page_id = str(page.get("page_id") or "")
            if page_id.endswith(".research.evidence_quality"):
                continue
            page_symbols = [
                str(symbol).strip().upper()
                for symbol in list(page.get("symbols") or [])[:8]
                if str(symbol).strip()
            ]
            symbol_overlap = sorted(set(page_symbols) & requested_symbols)
            if requested_symbols and page_symbols and not symbol_overlap:
                continue
            for source_ref in list(page.get("source_refs") or []):
                if not isinstance(source_ref, dict):
                    continue
                if str(source_ref.get("source_type") or "") == "wiki_repair_queue":
                    continue
                for ref in cls._evidence_quality_refs_for_source_ref(source_ref):
                    if str(ref.get("source_type") or "") == "wiki_repair_queue":
                        continue
                    status = normalize_jue_wiki_quality_status(
                        ref.get("quality_status")
                    )
                    warnings = [
                        str(item).strip()
                        for item in list(ref.get("quality_warnings") or [])
                        if str(item).strip()
                    ]
                    if status not in {"weak", "partial"} and not warnings:
                        continue
                    source_type = str(ref.get("source_type") or "")
                    source_id = str(ref.get("source_id") or "")
                    dedupe_key = (
                        page_id,
                        source_type,
                        status or "unknown",
                        tuple(warnings),
                    )
                    current = rows_by_key.get(dedupe_key)
                    if current and source_id <= str(current.get("source_id") or ""):
                        continue
                    severity = cls._evidence_quality_severity_score(
                        status=status,
                        warnings=warnings,
                    )
                    rows_by_key[dedupe_key] = {
                        "page_id": page_id,
                        "page_type": str(page.get("page_type") or ""),
                        "priority_type": "evidence_quality",
                        "symbols": page_symbols,
                        "symbol_overlap": symbol_overlap,
                        "status": "evidence_quality",
                        "sample_count": 0,
                        "win_rate": 0.0,
                        "expectancy": 0.0,
                        "avg_return_pct": 0.0,
                        "median_mae_pct": 0.0,
                        "drawdown_pressure": 0.0,
                        "helpful_score": -severity,
                        "confidence": 1.0 if status == "weak" else 0.7,
                        "severity_score": severity,
                        "source_type": source_type,
                        "source_id": source_id,
                        "action_type": cls._repair_action_type_for_evidence_quality(
                            warnings=warnings,
                        ),
                        "decision_use": "evidence_quality_cross_check",
                        "quality_status": status or "unknown",
                        "quality_warnings": warnings[:8],
                        "reasons": cls._evidence_quality_repair_reasons(
                            status=status,
                            source_type=source_type,
                            warnings=warnings,
                        ),
                        "repair_action": cls._repair_action_for_evidence_quality(
                            status=status,
                            warnings=warnings,
                        ),
                    }
        rows = list(rows_by_key.values())
        rows.sort(
            key=lambda row: (
                0 if row.get("symbol_overlap") else 1,
                -float(row.get("severity_score") or 0.0),
                str(row.get("page_id") or ""),
                str(row.get("source_id") or ""),
            )
        )
        return rows[: max(int(limit), 0)]

    @classmethod
    def _evidence_quality_refs_for_source_ref(
        cls,
        ref: dict[str, Any],
    ) -> list[dict[str, Any]]:
        status = normalize_jue_wiki_quality_status(ref.get("quality_status"))
        warnings = [
            str(item).strip()
            for item in list(ref.get("quality_warnings") or [])
            if str(item).strip()
        ]
        nested = (
            ref.get("evidence_quality")
            if isinstance(ref.get("evidence_quality"), dict)
            else {}
        )
        if not nested:
            return [dict(ref)] if status or warnings else []
        nested_status = cls._status_from_nested_evidence_quality(nested)
        nested_warnings = cls._warnings_from_nested_evidence_quality(nested)
        if not nested_status and not nested_warnings:
            return [dict(ref)] if status or warnings else []
        merged_status = _more_severe_quality_status(status, nested_status)
        nested_source_type = cls._source_type_from_nested_evidence_quality(
            nested,
            fallback=str(ref.get("source_type") or ""),
        )
        rows = [
            {
                "source_type": nested_source_type,
                "source_id": str(ref.get("source_id") or ""),
                "quality_status": merged_status,
                "quality_warnings": nested_warnings,
            }
        ]
        direct_only_warnings = [
            warning for warning in warnings if warning not in nested_warnings
        ]
        if direct_only_warnings:
            direct_ref = dict(ref)
            direct_ref["quality_warnings"] = direct_only_warnings
            direct_ref["quality_status"] = status or "unknown"
            rows.append(direct_ref)
        return rows

    @staticmethod
    def _status_from_nested_evidence_quality(evidence_quality: dict[str, Any]) -> str:
        return jue_wiki_quality_status_from_evidence(evidence_quality)

    @staticmethod
    def _warnings_from_nested_evidence_quality(
        evidence_quality: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[str]:
        warnings: list[str] = []
        for item in list(evidence_quality.get("top_warnings") or []):
            if isinstance(item, dict):
                warning = str(item.get("warning") or "").strip()
            else:
                warning = str(item).strip()
            if warning and warning not in warnings:
                warnings.append(warning)
            if len(warnings) >= max(int(limit), 0):
                return warnings
        warning_counts = (
            evidence_quality.get("warning_counts")
            if isinstance(evidence_quality.get("warning_counts"), dict)
            else {}
        )
        for warning, _count in sorted(
            warning_counts.items(),
            key=lambda item: (-_safe_int(item[1]), str(item[0])),
        ):
            clean = str(warning).strip()
            if clean and clean not in warnings:
                warnings.append(clean)
            if len(warnings) >= max(int(limit), 0):
                break
        return warnings

    @staticmethod
    def _source_type_from_nested_evidence_quality(
        evidence_quality: dict[str, Any],
        *,
        fallback: str,
    ) -> str:
        counts = (
            evidence_quality.get("source_type_counts")
            if isinstance(evidence_quality.get("source_type_counts"), dict)
            else {}
        )
        ranked = sorted(
            (
                (str(source_type).strip(), _safe_int(count))
                for source_type, count in counts.items()
                if str(source_type).strip()
            ),
            key=lambda item: (-item[1], item[0]),
        )
        if ranked:
            return ranked[0][0]
        return fallback

    @staticmethod
    def _evidence_quality_severity_score(
        *,
        status: str,
        warnings: list[str],
    ) -> float:
        score = 0.0
        if status == "weak":
            score += 12.0
        elif status == "partial":
            score += 6.0
        elif status:
            score += 2.0
        severity_by_warning = {
            "identity_name_missing": 10.0,
            "price_missing": 9.0,
            "financials_missing": 7.0,
            "valuation_metrics_sparse": 6.0,
            "financial_metrics_sparse": 5.0,
            "financial_rows_rejected_credit_rating": 5.0,
            "financial_rows_rejected_empty": 4.0,
            "valuation_stale_gt_30d": 4.0,
            "valuation_aging_gt_7d": 2.0,
        }
        for warning in warnings:
            score += severity_by_warning.get(warning, 1.0)
        return score

    @staticmethod
    def _evidence_quality_repair_reasons(
        *,
        status: str,
        source_type: str,
        warnings: list[str],
    ) -> list[str]:
        reasons = [f"evidence_quality:{status or 'unknown'}"]
        if source_type:
            reasons.append(f"source_type:{source_type}")
        reasons.extend(f"warning:{warning}" for warning in warnings[:4])
        return reasons[:5]

    @staticmethod
    def _repair_action_for_evidence_quality(
        *,
        status: str,
        warnings: list[str],
    ) -> str:
        warning_set = set(warnings)
        if "identity_name_missing" in warning_set:
            return "repair symbol identity before using this page for block sizing"
        if "price_missing" in warning_set:
            return "refresh price and quote evidence before designing an executable block"
        if "financials_missing" in warning_set:
            return "collect or cross-check financial statements before mid/long sizing"
        if "valuation_metrics_sparse" in warning_set:
            return "refresh valuation metrics and keep valuation as a weak secondary signal"
        if "financial_rows_rejected_credit_rating" in warning_set:
            return "repair fundamentals parser noise and cross-check WiseReport financial rows"
        if any(warning.startswith("valuation_stale") for warning in warning_set):
            return "refresh stale valuation before relying on discount or premium labels"
        if status == "weak":
            return "treat weak evidence as repair-only unless live structure is independently strong"
        return "cross-check partial evidence before increasing block size"

    @staticmethod
    def _repair_action_type_for_evidence_quality(
        *,
        warnings: list[str],
    ) -> str:
        warning_set = set(warnings)
        if "price_missing" in warning_set:
            return "refresh_symbol_quote"
        if "financials_missing" in warning_set:
            return "refresh_symbol_financials"
        if "financial_rows_rejected_credit_rating" in warning_set:
            return "refresh_symbol_financials"
        if "financial_rows_rejected_empty" in warning_set:
            return "refresh_symbol_financials"
        if "valuation_metrics_sparse" in warning_set:
            return "refresh_symbol_fundamentals"
        if any(warning.startswith("valuation_stale") for warning in warning_set):
            return "refresh_symbol_fundamentals"
        if "identity_name_missing" in warning_set:
            return "repair_symbol_identity"
        return "cross_check_evidence_quality"

    @staticmethod
    def _metric_reasons(metric: dict[str, Any]) -> list[str]:
        reasons = metric.get("reasons")
        if reasons is None:
            raw_reasons = metric.get("reasons_json")
            if isinstance(raw_reasons, str) and raw_reasons.strip():
                try:
                    reasons = json.loads(raw_reasons)
                except json.JSONDecodeError:
                    reasons = []
        if not isinstance(reasons, list):
            return []
        return [str(item)[:180] for item in reasons[:8]]

    @staticmethod
    def _repair_action_for_metric(
        *,
        metric: dict[str, Any],
        page_type: str,
    ) -> str:
        sample_count = int(metric.get("sample_count") or 0)
        win_rate = float(metric.get("win_rate") or 0.0)
        expectancy = float(metric.get("expectancy") or 0.0)
        drawdown = float(metric.get("drawdown_pressure") or 0.0)
        if page_type == "risk":
            return "turn risk finding into candidate-level executable gate checks"
        if sample_count >= 5 and expectancy < 0:
            return "revise entry/exit design before reusing this memory"
        if win_rate < 0.45 and sample_count >= 5:
            return "probe with smaller sizing or waiting-entry only"
        if drawdown > 1.0:
            return "widen validation with stop-distance and wick-risk checks"
        return "treat as repair evidence, not as a no-action blocker"

    def _open_lint_page_ids(self) -> set[str]:
        self.service.initialize()
        with self.service._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT page_id
                FROM wiki_lint_findings
                WHERE status = 'open'
                """
            ).fetchall()
        return {str(row["page_id"]) for row in rows}

    @staticmethod
    def _rejected_page(
        *,
        page_id: str,
        reason: str,
        char_count: int,
        score: float = 0.0,
        reasons: list[str] | None = None,
        penalties: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "page_id": page_id,
            "reason": reason,
            "rank": 0,
            "score": score,
            "reasons": reasons or [],
            "penalties": penalties or [reason],
            "char_count": char_count,
        }
