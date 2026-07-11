from __future__ import annotations

import json
import gzip
import re
import sqlite3
import statistics
import uuid
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from tradecraft.services.jue_wiki import (
    JueWikiService,
    normalize_jue_wiki_quality_status,
)
from tradecraft.services.jue_wiki_prompt_quality import (
    canonical_jue_wiki_status_counts,
    jue_wiki_quality_status_from_evidence,
)
from tradecraft.services.jue_wiki_context import wiki_eligibility_freshness_reason
from tradecraft.services.ops_section_snapshot import (
    OPS_SECTION_SNAPSHOT_VERSION,
    OpsSectionSnapshotV1,
    persist_ops_section_snapshot,
    read_ops_section_snapshot,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or _json_dumps(default))
    except (TypeError, json.JSONDecodeError):
        return default


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(min(value, upper), lower)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _has_prompt_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _metric_value_is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _metric_presence_for(
    metric: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, bool]:
    presence = {"__tracked__": True}
    presence.update(
        {
            key: True
            for key in keys
            if key in metric and _metric_value_is_present(metric.get(key))
        }
    )
    return presence


def _add_prompt_int(
    target: dict[str, Any],
    source: dict[str, Any],
    key: str,
    *,
    output_key: str | None = None,
) -> None:
    if _has_prompt_value(source.get(key)):
        target[output_key or key] = _safe_int(source.get(key))


def _add_prompt_float(
    target: dict[str, Any],
    source: dict[str, Any],
    key: str,
    *,
    output_key: str | None = None,
) -> None:
    if _has_prompt_value(source.get(key)):
        target[output_key or key] = _safe_float(source.get(key))


def _add_prompt_bool(
    target: dict[str, Any],
    source: dict[str, Any],
    key: str,
    *,
    output_key: str | None = None,
) -> None:
    if key in source and source.get(key) not in (None, ""):
        target[output_key or key] = bool(source.get(key))


def _max_present_int(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [_safe_int(row.get(key)) for row in rows if _has_prompt_value(row.get(key))]
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
) -> None:
    if not _has_prompt_value(row.get(key)):
        return
    current = _safe_int(target.get(key)) if _has_prompt_value(target.get(key)) else 0
    target[key] = current + _safe_int(row.get(key))


def _attach_present_target_rates(target: dict[str, Any]) -> None:
    has_sample = _has_prompt_value(target.get("sample_count"))
    has_missed = _has_prompt_value(target.get("missed_count"))
    has_resolved = _has_prompt_value(target.get("resolved_count"))
    if has_sample and has_resolved:
        target["resolution_rate"] = JueWikiApplicationService._ratio(
            _safe_int(target.get("resolved_count")),
            _safe_int(target.get("sample_count")),
        )
    if has_sample and has_missed:
        miss_rate = JueWikiApplicationService._ratio(
            _safe_int(target.get("missed_count")),
            _safe_int(target.get("sample_count")),
        )
        target["miss_rate"] = miss_rate
        target["repair_pressure_score"] = round(
            _safe_int(target.get("missed_count")) * miss_rate,
            6,
        )


def _compact_prompt_string_list(
    source: Any,
    *,
    limit: int = 8,
    max_len: int = 120,
) -> list[str]:
    values: list[str] = []
    for item in _prompt_value_items(source)[: max(int(limit), 0)]:
        value = _clean_prompt_semantic_text(item, max_len=max_len)
        if value and value not in values:
            values.append(value)
    return values


def _prompt_value_items(source: Any) -> list[Any]:
    if source in (None, "", [], {}):
        return []
    if isinstance(source, str):
        return [source]
    if isinstance(source, dict):
        return list(source)
    if isinstance(source, (list, tuple, set)):
        return list(source)
    try:
        return list(source)
    except TypeError:
        return [source]


def _clean_prompt_semantic_text(value: Any, *, max_len: int = 180) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null"}:
        return ""
    return text[: max(int(max_len), 0)]


def _compact_prompt_repair_component_targets(
    source: Any,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for row in list(source or [])[: max(int(limit), 0)]:
        if not isinstance(row, dict):
            continue
        compact = {
            "component": str(row.get("component") or "").strip()[:120],
            "decision_scope": str(row.get("decision_scope") or "").strip()[:80],
            "status": str(row.get("status") or "").strip()[:80],
            "priority_type": str(row.get("priority_type") or "").strip()[:120],
            "priority_types": _compact_prompt_string_list(
                row.get("priority_types"),
                limit=5,
            ),
            "action_type": str(row.get("action_type") or "").strip()[:120],
            "target_status": str(row.get("target_status") or "").strip()[:80],
            "criterion": str(row.get("criterion") or "").strip()[:180],
            "recommended_action": str(row.get("recommended_action") or "").strip()[
                :180
            ],
            "resolution_step": str(row.get("resolution_step") or "").strip()[
                :180
            ],
            "recommended_resolution": str(
                row.get("recommended_resolution") or ""
            ).strip()[:180],
            "primary_decision_use": str(
                row.get("primary_decision_use") or ""
            ).strip()[:180],
            "decision_uses": _compact_prompt_string_list(
                row.get("decision_uses"),
                limit=5,
                max_len=180,
            ),
            "source_id": str(row.get("source_id") or "").strip()[:180],
            "quality_warnings": _compact_prompt_string_list(
                row.get("quality_warnings"),
                limit=6,
            ),
            "impacted_page_ids": _compact_prompt_string_list(
                row.get("impacted_page_ids"),
                limit=12,
                max_len=180,
            ),
            "impacted_symbols": _compact_prompt_string_list(
                row.get("impacted_symbols"),
                limit=24,
                max_len=40,
            ),
            "repair_targets": _compact_prompt_repair_targets(
                row.get("repair_targets")
            ),
            "repair_target_effectiveness": (
                _compact_prompt_repair_target_effectiveness(
                    row.get("repair_target_effectiveness")
                )
            ),
            "repair_target_effectiveness_statuses": _compact_prompt_string_list(
                row.get("repair_target_effectiveness_statuses"),
                limit=8,
                max_len=80,
            ),
        }
        for key in ("sample_count", "missed_count", "resolved_count"):
            if row.get(key) not in (None, "", [], {}):
                compact[key] = _safe_int(row.get(key))
        if row.get("resolution_rate") not in (None, "", [], {}):
            compact["resolution_rate"] = _safe_float(row.get("resolution_rate"))
        targets.append(
            {
                key: value
                for key, value in compact.items()
                if value not in (None, "", [], {})
            }
        )
    return targets


def _compact_prompt_repair_targets(
    source: Any,
    *,
    limit: int = 8,
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for row in list(source or [])[: max(int(limit), 0)]:
        if not isinstance(row, dict):
            continue
        compact = {
            key: _clean_prompt_semantic_text(row.get(key), max_len=180)
            for key in ("page_id", "symbol", "recommended_action")
            if _clean_prompt_semantic_text(row.get(key), max_len=180)
        }
        if compact and compact not in targets:
            targets.append(compact)
    return targets


def _compact_prompt_repair_target_effectiveness(source: Any) -> list[dict[str, Any]]:
    rows = source if isinstance(source, list) else [source]
    compact_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact = {
            "page_id": str(row.get("page_id") or "").strip()[:180],
            "status": str(row.get("status") or "").strip()[:80],
            "reasons": _compact_prompt_string_list(
                row.get("reasons"),
                limit=8,
                max_len=180,
            ),
        }
        if row.get("sample_count") not in (None, "", [], {}):
            compact["sample_count"] = _safe_int(row.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if row.get(key) not in (None, "", [], {}):
                compact[key] = _safe_float(row.get(key))
        compact = {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }
        if compact and compact not in compact_rows:
            compact_rows.append(compact)
    return compact_rows


def _prompt_component_target_attention_item(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact = {
        "component": str(row.get("component") or "").strip()[:120],
        "decision_scope": str(row.get("decision_scope") or "").strip()[:80],
        "status": str(row.get("status") or "").strip()[:80],
        "target_status": str(row.get("target_status") or "").strip()[:80],
        "priority_type": str(row.get("priority_type") or "").strip()[:120],
        "action_type": str(row.get("action_type") or "").strip()[:120],
        "recommended_resolution": str(
            row.get("recommended_resolution") or ""
        ).strip()[:180],
        "impacted_page_ids": _compact_prompt_string_list(
            row.get("impacted_page_ids"),
            limit=6,
            max_len=180,
        ),
        "impacted_symbols": _compact_prompt_string_list(
            row.get("impacted_symbols"),
            limit=8,
            max_len=40,
        ),
        "repair_targets": _compact_prompt_repair_targets(
            row.get("repair_targets"),
            limit=4,
        ),
    }
    for key in ("sample_count", "missed_count", "resolved_count"):
        if key in row and row.get(key) not in (None, ""):
            compact[key] = _safe_int(row.get(key))
    if "resolution_rate" in row and row.get("resolution_rate") not in (None, ""):
        compact["resolution_rate"] = _safe_float(row.get("resolution_rate"))
    compact["quality_warnings"] = _compact_prompt_string_list(
        row.get("quality_warnings"),
        limit=6,
        max_len=120,
    )
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _prompt_component_target_attention_plan(
    *,
    repair_required_detail: Any,
    probe_detail: Any,
) -> dict[str, Any]:
    repair_now = _prompt_component_target_attention_item(repair_required_detail)
    probe_next = _prompt_component_target_attention_item(probe_detail)
    status = "repair_required" if repair_now else "probe" if probe_next else ""
    plan = {
        "status": status,
        "repair_now": repair_now,
        "probe_next": probe_next,
    }
    return {
        key: value
        for key, value in plan.items()
        if value not in (None, "", [], {})
    }


def _quality_status_from_evidence(evidence_quality: Any) -> str:
    return jue_wiki_quality_status_from_evidence(evidence_quality)


def _quality_warnings_from_evidence(evidence_quality: Any) -> list[str]:
    if not isinstance(evidence_quality, dict):
        return []
    warnings: list[str] = []
    for item in list(evidence_quality.get("top_warnings") or []):
        if isinstance(item, dict):
            warning = str(item.get("warning") or "").strip()
        else:
            warning = str(item).strip()
        if warning and warning not in warnings:
            warnings.append(warning)
    return warnings


def _compact_quality_warning_effectiveness_for_prompt(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    warning = str(source.get("warning") or "").strip()
    if not warning:
        return {}
    compact = {
        "warning": warning[:160],
        "page_id": str(source.get("page_id") or "")[:180],
        "status": str(source.get("status") or "")[:80],
        "reasons": [
            str(item)[:160]
            for item in list(source.get("reasons") or [])[:8]
            if str(item).strip()
        ],
    }
    if source.get("sample_count") not in (None, "", [], {}):
        compact["sample_count"] = _safe_int(source.get("sample_count"))
    for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
        if source.get(key) not in (None, "", [], {}):
            compact[key] = source.get(key)
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def _quality_warning_effectiveness_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    status_rank = {
        "degraded": 0,
        "repair_required": 1,
        "probe": 2,
        "active": 3,
    }
    status = str(row.get("status") or "").strip().lower()
    return (
        status_rank.get(status, 4),
        -_safe_int(row.get("sample_count")),
        _safe_float(row.get("expectancy")),
        _safe_float(row.get("helpful_score")),
        str(row.get("warning") or ""),
    )


def summarize_jue_wiki_quality_pressure_for_prompt(
    rows: list[Any],
    *,
    top_warning_limit: int = 6,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    warning_page_ids: dict[str, list[str]] = {}
    weak_page_ids: list[str] = []
    caution_page_ids: list[str] = []
    warning_effectiveness: dict[str, dict[str, Any]] = {}
    row_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = str(row.get("page_id") or "").strip()
        evidence_quality = row.get("evidence_quality")
        status = normalize_jue_wiki_quality_status(row.get("quality_status"))
        if not status:
            status = _quality_status_from_evidence(evidence_quality)

        raw_warnings = row.get("quality_warnings")
        warnings: list[str] = []
        if isinstance(raw_warnings, list):
            warnings = [
                str(item).strip()
                for item in raw_warnings
                if str(item).strip()
            ]
        if not warnings:
            warnings = _quality_warnings_from_evidence(evidence_quality)
        freshness_warnings = [
            str(item).strip()
            for item in list(row.get("freshness_warnings") or [])
            if str(item).strip()
        ]
        if (
            not freshness_warnings
            and str(row.get("freshness_status") or "").strip().lower() == "stale"
        ):
            freshness_warnings = ["freshness_status_stale"]
        for warning in freshness_warnings:
            if warning not in warnings:
                warnings.append(warning)
        if isinstance(evidence_quality, dict):
            for metric in list(evidence_quality.get("warning_effectiveness") or []):
                compact_metric = _compact_quality_warning_effectiveness_for_prompt(
                    metric
                )
                warning = str(compact_metric.get("warning") or "").strip()
                if not warning:
                    continue
                existing = warning_effectiveness.get(warning)
                if existing is None or _quality_warning_effectiveness_sort_key(
                    compact_metric
                ) < _quality_warning_effectiveness_sort_key(existing):
                    warning_effectiveness[warning] = compact_metric

        if not status and not warnings:
            continue

        row_count += 1
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        if status == "weak" and page_id and page_id not in weak_page_ids:
            weak_page_ids.append(page_id)
        if status in {"weak", "partial", "unknown"} and page_id:
            if page_id not in caution_page_ids:
                caution_page_ids.append(page_id)
        for warning in warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
            if page_id:
                page_ids = warning_page_ids.setdefault(warning, [])
                if page_id not in page_ids:
                    page_ids.append(page_id)

    if row_count <= 0:
        return {}

    sorted_status_counts = {
        key: status_counts[key]
        for key in sorted(status_counts)
    }
    sorted_warning_counts = {
        key: warning_counts[key]
        for key in sorted(warning_counts)
    }
    top_warnings = [
        {
            key: value
            for key, value in {
                "warning": warning,
                "count": count,
                "effectiveness": warning_effectiveness.get(warning),
            }.items()
            if value not in (None, "", [], {})
        }
        for warning, count in sorted(
            warning_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(int(top_warning_limit), 0)]
    ]
    summary: dict[str, Any] = {
        "row_count": row_count,
        "status_counts": sorted_status_counts,
    }
    if sorted_warning_counts:
        summary["warning_counts"] = sorted_warning_counts
    if top_warnings:
        summary["top_warnings"] = top_warnings
        top_warning_keys = [
            str(item.get("warning") or "").strip()
            for item in top_warnings
            if isinstance(item, dict) and str(item.get("warning") or "").strip()
        ]
        compact_warning_page_ids = {
            warning: warning_page_ids[warning][:8]
            for warning in top_warning_keys
            if warning_page_ids.get(warning)
        }
        if compact_warning_page_ids:
            summary["warning_page_ids"] = compact_warning_page_ids
    if weak_page_ids:
        summary["weak_page_ids"] = weak_page_ids
    if caution_page_ids:
        summary["caution_page_ids"] = caution_page_ids
    return summary


def build_jue_wiki_quality_pressure_action_plan_for_prompt(
    quality_summary: Any,
    *,
    top_warning_limit: int = 3,
) -> dict[str, Any]:
    summary = quality_summary if isinstance(quality_summary, dict) else {}
    if not summary:
        return {}

    status_counts = canonical_jue_wiki_status_counts(summary.get("status_counts"))
    weak_count = _safe_int(status_counts.get("weak"))
    partial_count = _safe_int(status_counts.get("partial"))
    unknown_count = _safe_int(status_counts.get("unknown"))
    weak_page_ids = [
        str(item).strip()
        for item in list(summary.get("weak_page_ids") or [])[:8]
        if str(item).strip()
    ]
    caution_page_ids = [
        str(item).strip()
        for item in list(summary.get("caution_page_ids") or [])[:12]
        if str(item).strip()
    ]
    warning_page_ids_raw = (
        summary.get("warning_page_ids")
        if isinstance(summary.get("warning_page_ids"), dict)
        else {}
    )
    warning_page_ids: dict[str, list[str]] = {}
    for warning, page_ids in warning_page_ids_raw.items():
        warning_key = str(warning).strip()
        if not warning_key or not isinstance(page_ids, list):
            continue
        compact_page_ids = [
            str(page_id).strip()
            for page_id in page_ids[:8]
            if str(page_id).strip()
        ]
        if compact_page_ids:
            warning_page_ids[warning_key] = compact_page_ids
    top_warnings: list[dict[str, Any]] = []
    for item in list(summary.get("top_warnings") or [])[: max(int(top_warning_limit), 0)]:
        if not isinstance(item, dict):
            continue
        warning = str(item.get("warning") or "").strip()
        if not warning:
            continue
        row = {"warning": warning, "count": _safe_int(item.get("count"))}
        page_ids = [
            str(page_id).strip()
            for page_id in list(item.get("page_ids") or warning_page_ids.get(warning) or [])[:8]
            if str(page_id).strip()
        ]
        if page_ids:
            row["page_ids"] = page_ids
        effectiveness = item.get("effectiveness")
        if isinstance(effectiveness, dict):
            row["effectiveness"] = _compact_quality_warning_effectiveness_for_prompt(
                effectiveness
            )
        top_warnings.append(row)

    if not (weak_count or partial_count or unknown_count or weak_page_ids or top_warnings):
        return {}

    degraded_warnings = [
        str(row.get("warning") or "")
        for row in top_warnings
        if str((row.get("effectiveness") or {}).get("status") or "").lower()
        in {"degraded", "repair_required"}
    ]
    probe_warnings = [
        str(row.get("warning") or "")
        for row in top_warnings
        if str((row.get("effectiveness") or {}).get("status") or "").lower()
        == "probe"
    ]
    active_warnings = [
        str(row.get("warning") or "")
        for row in top_warnings
        if str((row.get("effectiveness") or {}).get("status") or "").lower()
        == "active"
    ]
    status = (
        "repair_required"
        if weak_count or weak_page_ids or degraded_warnings
        else "probe"
    )
    required_adjustments: list[dict[str, Any]] = []
    if weak_page_ids:
        required_adjustments.append(
            {
                "adjustment_type": "candidate_level_cross_check",
                "reason": "weak_wiki_pages",
                "page_ids": weak_page_ids,
                "resolution": "refresh_or_cross_check_before_sizing",
            }
        )
    for row in top_warnings:
        adjustment = {
            "adjustment_type": "quality_warning_resolution",
            "warning": row["warning"],
            "count": row["count"],
            "resolution": (
                "repair_or_cross_check_before_sizing"
                if row["warning"] in degraded_warnings
                else "refresh_or_cross_check_before_sizing"
            ),
        }
        if isinstance(row.get("effectiveness"), dict):
            adjustment["effectiveness"] = row["effectiveness"]
        if row.get("page_ids"):
            adjustment["page_ids"] = row["page_ids"]
        required_adjustments.append(adjustment)

    repair_focus = []
    for row in top_warnings:
        focus = {
            "priority_type": "evidence_quality",
            "warning": row["warning"],
            "count": row["count"],
            "decision_use": "evidence_quality_cross_check",
        }
        if isinstance(row.get("effectiveness"), dict):
            effectiveness_status = str(row["effectiveness"].get("status") or "").strip()
            if effectiveness_status:
                focus["effectiveness_status"] = effectiveness_status
            focus["effectiveness"] = row["effectiveness"]
        if row.get("page_ids"):
            focus["page_ids"] = row["page_ids"]
        repair_focus.append(focus)
    plan: dict[str, Any] = {
        "status": status,
        "hard_blocker": False,
        "decision_policy": (
            "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
        ),
    }
    if required_adjustments:
        plan["required_adjustments"] = required_adjustments
    if repair_focus:
        plan["repair_focus"] = repair_focus
    if caution_page_ids:
        plan["caution_page_ids"] = caution_page_ids
    if degraded_warnings or probe_warnings or active_warnings:
        plan["quality_effectiveness_pressure"] = {
            "status": "repair_required" if degraded_warnings else "probe",
            "degraded_warnings": degraded_warnings,
            "probe_warnings": probe_warnings,
            "active_warnings": active_warnings,
        }
    return plan


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:32]


class _ShadowEligibilityReader(Protocol):
    def eligibility(self, venue: str) -> dict[str, Any]: ...


class JueWikiApplicationService:
    OPS_SNAPSHOT_SECTION = "jue_wiki_application"

    def __init__(
        self,
        wiki: JueWikiService,
        *,
        shadow_eligibility_reader: _ShadowEligibilityReader | None = None,
    ) -> None:
        self.wiki = wiki
        self.shadow_eligibility_reader = shadow_eligibility_reader
        self._page_summary_cache: dict[str, dict[str, Any]] | None = None
        self._effectiveness_map_cache: dict[
            tuple[str, tuple[str, ...]],
            dict[str, dict[str, Any]],
        ] = {}

    def record_decision_link(
        self,
        *,
        selection_run_id: str,
        manager_run_id: str,
        decision_scope: str,
        decision_type: str,
        selected_pages: list[str],
        symbol: str = "",
        block_id: str = "",
        venue: str = "",
        horizon: str = "",
        action: str = "",
        prompt_mode: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        if not str(selection_run_id).strip():
            return {
                "status": "error",
                "error_message": "selection_run_id is required",
            }
        link_id = f"wiki-link:{uuid.uuid4().hex}"
        now = _utc_now_iso()
        clean_pages = self._clean_page_ids(selected_pages)
        clean_symbol = self._decision_link_symbol(
            symbol=symbol,
            selected_pages=clean_pages,
            metadata=metadata or {},
        )
        with self.wiki._connect() as conn:
            conn.execute(
                """
                INSERT INTO wiki_decision_links (
                    link_id, selection_run_id, manager_run_id, decision_scope,
                    decision_type, symbol, block_id, venue, horizon, action,
                    prompt_mode, selected_pages_json, metadata_json, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    str(selection_run_id),
                    str(manager_run_id),
                    str(decision_scope).strip().lower(),
                    str(decision_type),
                    clean_symbol,
                    str(block_id),
                    str(venue).strip().lower(),
                    str(horizon).strip().lower(),
                    str(action),
                    str(prompt_mode).strip().lower(),
                    _json_dumps(clean_pages),
                    _json_dumps(metadata or {}),
                    now,
                ),
            )
        return {"status": "ok", "link_id": link_id, "linked_at": now}

    def list_decision_links(
        self,
        *,
        selection_run_id: str | None = None,
        decision_scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.wiki.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if selection_run_id:
            clauses.append("selection_run_id = ?")
            params.append(str(selection_run_id))
        if decision_scope:
            clauses.append("decision_scope = ?")
            params.append(str(decision_scope).strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(int(limit), 1))
        with self.wiki._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_decision_links
                {where}
                ORDER BY linked_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._decision_link_from_row(row) for row in rows]

    def backfill_decision_link_selected_wiki_pages(
        self,
        *,
        limit: int = 50_000,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        now = _utc_now_iso()
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT link_id, selection_run_id, selected_pages_json, metadata_json,
                       horizon
                FROM wiki_decision_links
                WHERE selected_pages_json NOT IN ('[]', '')
                  AND metadata_json NOT LIKE '%selected_wiki_pages%'
                ORDER BY linked_at DESC, link_id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
            updated = 0
            skipped = 0
            for row in rows:
                selected_pages = _json_loads(str(row["selected_pages_json"]), [])
                if not isinstance(selected_pages, list):
                    skipped += 1
                    continue
                selected_pages = [
                    str(page_id).strip()
                    for page_id in selected_pages
                    if str(page_id).strip()
                ]
                if not selected_pages:
                    skipped += 1
                    continue
                metadata = _json_loads(str(row["metadata_json"]), {})
                if not isinstance(metadata, dict):
                    metadata = {}
                prompt: dict[str, Any] = {}
                source = "current_wiki_pages"
                selection_pages = self._selection_run_prompt_pages(
                    conn,
                    selection_run_id=str(row["selection_run_id"] or ""),
                )
                if selection_pages:
                    prompt = {"jue_wiki": {"pages": selection_pages}}
                    source = "selection_run_pages"
                summary = self._prompt_selected_wiki_pages_summary(
                    prompt,
                    selected_pages,
                    horizons=[str(row["horizon"] or "")],
                )
                if not summary.get("pages"):
                    skipped += 1
                    continue
                metadata["selected_wiki_pages"] = summary
                metadata["selected_wiki_pages_backfilled_at"] = now
                metadata["selected_wiki_pages_backfill_source"] = source
                conn.execute(
                    """
                    UPDATE wiki_decision_links
                    SET metadata_json = ?
                    WHERE link_id = ?
                    """,
                    (_json_dumps(metadata), str(row["link_id"])),
                )
                updated += 1
        return {
            "status": "ok",
            "updated_count": updated,
            "skipped_count": skipped,
        }

    def _selection_run_prompt_pages(
        self,
        conn: sqlite3.Connection,
        *,
        selection_run_id: str,
    ) -> list[dict[str, Any]]:
        run_id = str(selection_run_id or "").strip()
        if not run_id or not self.wiki._table_exists(conn, "wiki_selection_pages"):
            return []
        rows = self.wiki.selection_audit_store().included_pages(conn, run_id)
        prompt_pages: list[dict[str, Any]] = []
        for row in rows:
            page_id = str(row["page_id"] or "").strip()
            if not page_id:
                continue
            prompt_pages.append(
                {
                    key: value
                    for key, value in {
                        "page_id": page_id,
                        "rank": _safe_int(row["rank"]),
                        "score": _safe_float(row["score"]),
                        "selection_reasons": _compact_prompt_string_list(
                            _json_loads(str(row["reasons_json"]), []),
                            limit=8,
                            max_len=180,
                        ),
                        "selection_penalties": _compact_prompt_string_list(
                            _json_loads(str(row["penalties_json"]), []),
                            limit=8,
                            max_len=180,
                        ),
                        "char_count": _safe_int(row["char_count"]),
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        return prompt_pages

    def record_selection_outcomes(
        self,
        *,
        link_id: str,
        outcome_kind: str,
        outcome_status: str,
        pnl_value: float = 0.0,
        pnl_currency: str = "",
        return_pct: float = 0.0,
        mfe_pct: float = 0.0,
        mae_pct: float = 0.0,
        holding_minutes: float = 0.0,
        horizon: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        link = self._decision_link(link_id)
        if link is None:
            return {
                "status": "error",
                "error_message": f"decision link not found: {link_id}",
                "outcome_count": 0,
            }
        raw_pages = [str(page) for page in link.get("selected_pages", []) if str(page)]
        selected_page_summaries = self._selected_page_summaries_from_link(link)
        link_metadata = (
            link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        )
        outcome_symbol = self._outcome_symbol(link=link, evidence=evidence or {})
        pages = self._outcome_attributable_pages(
            raw_pages,
            selected_page_summaries=selected_page_summaries,
            link=link,
            evidence=evidence or {},
        )
        now = _utc_now_iso()
        with self.wiki._connect() as conn:
            for page_id in pages:
                outcome_evidence = self._outcome_evidence_with_selected_page(
                    evidence or {},
                    selected_page_summaries.get(page_id),
                )
                outcome_evidence = self._outcome_evidence_with_quality_pressure_metadata(
                    outcome_evidence,
                    link_metadata,
                )
                outcome_id = (
                    f"wiki-outcome:"
                    f"{_stable_id(link_id, page_id, outcome_kind, outcome_evidence)}"
                )
                outcome_evidence_json = _json_dumps(outcome_evidence)
                outcome_horizon = self._clean_outcome_horizon(horizon) or str(
                    link["horizon"] or ""
                ).strip().lower()
                conn.execute(
                    """
                    DELETE FROM wiki_selection_outcomes
                    WHERE link_id = ?
                      AND page_id = ?
                      AND outcome_kind = ?
                      AND evidence_json = ?
                      AND outcome_id <> ?
                    """,
                    (
                        link_id,
                        page_id,
                        str(outcome_kind),
                        outcome_evidence_json,
                        outcome_id,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wiki_selection_outcomes (
                        outcome_id, link_id, selection_run_id, page_id,
                        decision_scope, venue, symbol, block_id, horizon,
                        outcome_kind, outcome_status, pnl_value, pnl_currency,
                        return_pct, mfe_pct, mae_pct, holding_minutes,
                        evidence_json, computed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome_id,
                        link_id,
                        link["selection_run_id"],
                        page_id,
                        link["decision_scope"],
                        link["venue"],
                        outcome_symbol,
                        link["block_id"],
                        outcome_horizon,
                        str(outcome_kind),
                        str(outcome_status),
                        float(pnl_value),
                        str(pnl_currency),
                        float(return_pct),
                        float(mfe_pct),
                        float(mae_pct),
                        float(holding_minutes),
                        outcome_evidence_json,
                        now,
                    ),
                )
        return {
            "status": "ok",
            "outcome_count": len(pages),
            "skipped_page_count": max(len(raw_pages) - len(pages), 0),
        }

    def list_selection_outcomes(
        self,
        *,
        selection_run_id: str | None = None,
        page_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.wiki.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if selection_run_id:
            clauses.append("selection_run_id = ?")
            params.append(str(selection_run_id))
        if page_id:
            clauses.append("page_id = ?")
            params.append(str(page_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(int(limit), 1))
        with self.wiki._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_selection_outcomes
                {where}
                ORDER BY computed_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                **dict(row),
                "evidence": _json_loads(str(row["evidence_json"]), {}),
            }
            for row in rows
        ]

    def archive_selection_outcomes(
        self,
        *,
        retention_days: int = 30,
        now_iso: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Compress old raw outcome evidence after aggregate projection."""

        self.wiki.initialize()
        now_text = str(now_iso or _utc_now_iso())
        try:
            now = datetime.fromisoformat(now_text.replace("Z", "+00:00"))
        except ValueError:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = now.astimezone(timezone.utc) - timedelta(
            days=max(int(retention_days), 1)
        )
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_selection_outcomes
                WHERE computed_at < ?
                ORDER BY computed_at, outcome_id
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            if dry_run:
                return {
                    "status": "ok",
                    "dry_run": True,
                    "retention_days": max(int(retention_days), 1),
                    "candidate_count": len(rows),
                    "archived_count": 0,
                }
            compressed_bytes = 0
            for row in rows:
                evidence = str(row["evidence_json"] or "{}").encode("utf-8")
                compressed = gzip.compress(evidence)
                compressed_bytes += len(compressed)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wiki_selection_outcomes_archive (
                        outcome_id, link_id, selection_run_id, page_id,
                        decision_scope, venue, symbol, block_id, horizon,
                        outcome_kind, outcome_status, pnl_value, pnl_currency,
                        return_pct, mfe_pct, mae_pct, holding_minutes,
                        evidence_gzip, evidence_sha256, computed_at, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["outcome_id"]),
                        str(row["link_id"]),
                        str(row["selection_run_id"]),
                        str(row["page_id"]),
                        str(row["decision_scope"]),
                        str(row["venue"]),
                        str(row["symbol"]),
                        str(row["block_id"]),
                        str(row["horizon"]),
                        str(row["outcome_kind"]),
                        str(row["outcome_status"]),
                        float(row["pnl_value"] or 0.0),
                        str(row["pnl_currency"]),
                        float(row["return_pct"] or 0.0),
                        float(row["mfe_pct"] or 0.0),
                        float(row["mae_pct"] or 0.0),
                        float(row["holding_minutes"] or 0.0),
                        compressed,
                        sha256(evidence).hexdigest(),
                        str(row["computed_at"]),
                        now.astimezone(timezone.utc).isoformat(),
                    ),
                )
            if rows:
                conn.executemany(
                    "DELETE FROM wiki_selection_outcomes WHERE outcome_id = ?",
                    [(str(row["outcome_id"]),) for row in rows],
                )
        return {
            "status": "ok",
            "dry_run": False,
            "retention_days": max(int(retention_days), 1),
            "candidate_count": len(rows),
            "archived_count": len(rows),
            "compressed_evidence_bytes": compressed_bytes,
        }

    @classmethod
    def _selected_page_summaries_from_link(
        cls,
        link: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        summary = (
            metadata.get("selected_wiki_pages")
            if isinstance(metadata.get("selected_wiki_pages"), dict)
            else {}
        )
        rows = summary.get("pages") if isinstance(summary.get("pages"), list) else []
        result: dict[str, dict[str, Any]] = {}
        for row in cls._merge_selected_wiki_page_rows([], rows):
            page_ids = cls._clean_page_ids([row.get("page_id")])
            if not page_ids:
                continue
            result[page_ids[0]] = dict(row)
        return result

    @classmethod
    def _decision_link_symbol(
        cls,
        *,
        symbol: Any,
        selected_pages: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        clean_symbol = cls._normalize_selected_page_symbol(symbol)
        if clean_symbol:
            return clean_symbol
        inferred_symbols: list[str] = []

        def add_inferred_symbol(value: Any) -> None:
            page_symbol = cls._normalize_selected_page_symbol(value)
            if page_symbol and page_symbol not in inferred_symbols:
                inferred_symbols.append(page_symbol)

        for page_id in selected_pages[:24]:
            for page_symbol in cls._selected_page_symbol_list(
                page_id=str(page_id),
                page={},
                limit=4,
            ):
                add_inferred_symbol(page_symbol)
        metadata_source = metadata if isinstance(metadata, dict) else {}
        selected_summary = (
            metadata_source.get("selected_wiki_pages")
            if isinstance(metadata_source.get("selected_wiki_pages"), dict)
            else {}
        )
        for row in list(selected_summary.get("pages") or [])[:24]:
            if not isinstance(row, dict):
                continue
            page_id = str(row.get("page_id") or "").strip()
            for page_symbol in cls._selected_page_symbol_list(
                page_id=page_id,
                page=row,
                limit=4,
            ):
                add_inferred_symbol(page_symbol)
        requested_summaries = (
            selected_summary.get("requested_symbol_summaries")
            if isinstance(selected_summary.get("requested_symbol_summaries"), list)
            else []
        )
        for row in requested_summaries[:24]:
            if not isinstance(row, dict):
                continue
            page_id = str(row.get("page_id") or "").strip()
            for page_symbol in cls._selected_page_symbol_list(
                page_id=page_id,
                page=row,
                limit=4,
            ):
                add_inferred_symbol(page_symbol)
        fallback_page_symbols = (
            selected_summary.get("effectiveness_fallback_page_symbols")
            if isinstance(
                selected_summary.get("effectiveness_fallback_page_symbols"),
                list,
            )
            else []
        )
        for row in fallback_page_symbols[:24]:
            if not isinstance(row, dict):
                continue
            page_id = str(row.get("page_id") or "").strip()
            for page_symbol in cls._selected_page_symbol_list(
                page_id=page_id,
                page=row,
                limit=4,
            ):
                add_inferred_symbol(page_symbol)
        for page_symbol in _compact_prompt_string_list(
            selected_summary.get("effectiveness_fallback_symbols"),
            limit=24,
            max_len=40,
        ):
            add_inferred_symbol(page_symbol)
        return inferred_symbols[0] if len(inferred_symbols) == 1 else ""

    @classmethod
    def _outcome_attributable_pages(
        cls,
        pages: list[str],
        *,
        selected_page_summaries: dict[str, dict[str, Any]],
        link: dict[str, Any],
        evidence: dict[str, Any],
    ) -> list[str]:
        outcome_symbol = cls._outcome_symbol(link=link, evidence=evidence)
        if not outcome_symbol:
            return pages
        out: list[str] = []
        for page_id in pages:
            page = selected_page_summaries.get(page_id, {})
            page_symbols = cls._selected_page_symbols(page_id=page_id, page=page)
            if page_symbols and outcome_symbol not in page_symbols:
                continue
            out.append(page_id)
        return out

    @classmethod
    def _outcome_symbol(cls, *, link: dict[str, Any], evidence: dict[str, Any]) -> str:
        for value in (
            evidence.get("symbol"),
            evidence.get("ticker"),
            link.get("symbol"),
            cls._outcome_symbol_from_market_field(evidence.get("market")),
        ):
            symbol = cls._normalize_selected_page_symbol(value)
            if symbol:
                return symbol
        return ""

    @classmethod
    def _selected_page_symbols(
        cls,
        *,
        page_id: str,
        page: dict[str, Any],
    ) -> set[str]:
        symbols: set[str] = set()
        for value in list(page.get("symbols") or []):
            symbol = cls._normalize_selected_page_symbol(value)
            if symbol:
                symbols.add(symbol)
        for key in ("symbol", "ticker"):
            symbol = cls._normalize_selected_page_symbol(page.get(key))
            if symbol:
                symbols.add(symbol)
        market_symbol = cls._outcome_symbol_from_market_field(page.get("market"))
        if market_symbol:
            symbols.add(market_symbol)
        page_type = str(page.get("page_type") or "").strip().lower()
        if page_type == "symbol" or ".symbol." in page_id:
            suffix = page_id.split(".symbol.", 1)[-1]
            symbol = cls._normalize_outcome_symbol(suffix)
            if symbol:
                symbols.add(symbol)
        return symbols

    @classmethod
    def _selected_page_symbol_list(
        cls,
        *,
        page_id: str,
        page: dict[str, Any],
        limit: int = 8,
    ) -> list[str]:
        symbols: list[str] = []

        def add(value: Any) -> None:
            symbol = cls._normalize_selected_page_symbol(value)
            if symbol and symbol not in symbols:
                symbols.append(symbol)

        for value in list(page.get("symbols") or []):
            add(value)
        for key in ("symbol", "ticker"):
            add(page.get(key))
        add(cls._outcome_symbol_from_market_field(page.get("market")))
        page_type = str(page.get("page_type") or "").strip().lower()
        if page_type == "symbol" or ".symbol." in page_id:
            add(page_id.split(".symbol.", 1)[-1])
        return symbols[: max(int(limit), 0)]

    @classmethod
    def _normalize_selected_page_symbol(cls, value: Any) -> str:
        symbol = cls._normalize_outcome_symbol(value)
        if cls._is_tradable_symbol_identifier(symbol):
            return symbol
        return ""

    @staticmethod
    def _is_tradable_symbol_identifier(symbol: str) -> bool:
        return bool(
            re.fullmatch(r"\d{6}", str(symbol or ""))
            or re.fullmatch(r"KRW-[A-Z0-9]{1,24}", str(symbol or ""))
            or re.fullmatch(
                r"[A-Z0-9]{1,24}(USDT|USDC|FDUSD|BUSD|BTC|ETH|BNB)",
                str(symbol or ""),
            )
        )

    @staticmethod
    def _normalize_outcome_symbol(value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        return "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_"})

    @classmethod
    def _outcome_symbol_from_market_field(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        lane_values = {
            "spot",
            "futures",
            "future",
            "margin",
            "upbit",
            "upbit_spot",
            "binance",
            "kis",
            "krx",
            "domestic",
            "usdt-m",
            "coin-m",
        }
        if text.lower() in lane_values:
            return ""
        symbol = cls._normalize_outcome_symbol(text)
        if cls._is_tradable_symbol_identifier(symbol):
            return symbol
        return ""

    @staticmethod
    def _outcome_evidence_with_selected_page(
        evidence: dict[str, Any],
        selected_page: dict[str, Any] | None,
    ) -> dict[str, Any]:
        service_cls = JueWikiApplicationService
        result = service_cls._clean_outcome_evidence(evidence)
        if selected_page:
            result["selected_wiki_page"] = dict(selected_page)
            result = service_cls._outcome_evidence_with_selected_page_usage_guidance(
                result,
                selected_page,
            )
            result = service_cls._outcome_evidence_with_selected_page_usage_guidance_effectiveness(
                result,
                selected_page,
            )
            result = service_cls._outcome_evidence_with_selected_page_memory_card_quality(
                result,
                selected_page,
            )
            result = service_cls._outcome_evidence_with_selected_page_quality_warning_effectiveness(
                result,
                selected_page,
            )
            result = service_cls._outcome_evidence_with_selected_page_quality_warning_source_effectiveness(
                result,
                selected_page,
            )
            result = service_cls._outcome_evidence_with_selected_page_repair_target_effectiveness(
                result,
                selected_page,
            )
            result = service_cls._outcome_evidence_with_selected_page_repair_queue(
                result,
                selected_page,
            )
        return result

    @staticmethod
    def _outcome_evidence_with_selected_page_repair_queue(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        service_cls = JueWikiApplicationService
        selected_queue = service_cls._selected_page_evidence_repair_queue(
            selected_page
        )
        if not selected_queue:
            return evidence
        existing_queue = (
            evidence.get("jue_wiki_repair_queue")
            if isinstance(evidence.get("jue_wiki_repair_queue"), dict)
            else {}
        )
        merged_queue = service_cls._merge_outcome_repair_queues(
            existing_queue,
            selected_queue,
        )
        if not merged_queue:
            return evidence
        result = dict(evidence)
        result["jue_wiki_repair_queue"] = merged_queue
        return service_cls._clean_outcome_evidence(result)

    @staticmethod
    def _selected_page_evidence_repair_queue(
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_quality = (
            selected_page.get("evidence_quality")
            if isinstance(selected_page.get("evidence_quality"), dict)
            else {}
        )
        raw_queue = (
            evidence_quality.get("repair_queue")
            if isinstance(evidence_quality.get("repair_queue"), dict)
            else {}
        )
        if not raw_queue:
            return {}
        actions = JueWikiApplicationService._selected_page_repair_queue_action_batches(
            raw_queue.get("actions")
        )
        open_batches = JueWikiApplicationService._application_repair_queue_action_batches(
            raw_queue.get("open_action_batches")
        )
        open_count = _safe_int(raw_queue.get("open_count"))
        if open_count <= 0:
            open_count = sum(_safe_int(batch.get("count")) for batch in actions)
        queue = {
            "open_count": open_count,
            "open_action_batches": [
                *open_batches,
                *[
                    batch
                    for batch in actions
                    if batch not in open_batches
                ],
            ],
        }
        return {
            key: value
            for key, value in queue.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _selected_page_repair_queue_action_batches(
        raw_actions: Any,
    ) -> list[dict[str, Any]]:
        batches_by_action: dict[str, dict[str, Any]] = {}
        for item in list(raw_actions or [])[:12]:
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("action_type") or "").strip()
            if not action_type:
                continue
            batch = batches_by_action.setdefault(
                action_type,
                {
                    "action_type": action_type[:120],
                    "count": 0,
                    "warnings": [],
                },
            )
            batch["count"] = _safe_int(batch.get("count")) + 1
            warnings = list(batch.get("warnings") or [])
            for warning in _compact_prompt_string_list(
                item.get("quality_warnings"),
                limit=16,
                max_len=120,
            ):
                if warning not in warnings:
                    warnings.append(warning)
            if warnings:
                batch["warnings"] = warnings[:16]
        return [
            {
                key: value
                for key, value in batch.items()
                if value not in (None, "", [], {})
            }
            for batch in batches_by_action.values()
            if batch.get("action_type")
        ]

    @staticmethod
    def _merge_outcome_repair_queues(
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> dict[str, Any]:
        batches: list[dict[str, Any]] = []
        batch_counts_by_marker: dict[tuple[Any, ...], int] = {}
        for queue in (first, second):
            for batch in list(queue.get("open_action_batches") or []):
                if not isinstance(batch, dict):
                    continue
                compact = {
                    key: value
                    for key, value in {
                        "scope": str(batch.get("scope") or "").strip().lower()[:40],
                        "action_type": str(batch.get("action_type") or "").strip()[:120],
                        "count": _safe_int(batch.get("count")),
                        "symbols": _compact_prompt_string_list(
                            batch.get("symbols"),
                            limit=64,
                            max_len=40,
                        ),
                        "warnings": _compact_prompt_string_list(
                            batch.get("warnings"),
                            limit=16,
                            max_len=120,
                        ),
                    }.items()
                    if value not in (None, "", [], {}, 0)
                }
                marker = (
                    compact.get("scope", ""),
                    compact.get("action_type", ""),
                    tuple(compact.get("symbols") or []),
                    tuple(compact.get("warnings") or []),
                )
                if not compact:
                    continue
                count = _safe_int(compact.get("count"))
                existing_count = batch_counts_by_marker.get(marker)
                if existing_count is None:
                    batch_counts_by_marker[marker] = count
                    batches.append(compact)
                    continue
                if count > existing_count:
                    batch_counts_by_marker[marker] = count
                    compact["count"] = count
                    for index, existing in enumerate(batches):
                        existing_marker = (
                            existing.get("scope", ""),
                            existing.get("action_type", ""),
                            tuple(existing.get("symbols") or []),
                            tuple(existing.get("warnings") or []),
                        )
                        if existing_marker == marker:
                            batches[index] = compact
                            break
        explicit_open_count = max(
            _safe_int(first.get("open_count")),
            _safe_int(second.get("open_count")),
        )
        open_count = max(
            explicit_open_count,
            sum(batch_counts_by_marker.values()),
        )
        queue = {
            "open_count": open_count,
            "open_action_batches": batches[:12],
        }
        return {
            key: value
            for key, value in queue.items()
            if value not in (None, "", [], {}, 0)
        }

    @staticmethod
    def _outcome_evidence_with_quality_pressure_metadata(
        evidence: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        service_cls = JueWikiApplicationService
        application = (
            metadata.get("jue_wiki_application")
            if isinstance(metadata.get("jue_wiki_application"), dict)
            else {}
        )
        quality_summary = (
            metadata.get("jue_wiki_quality_summary")
            if isinstance(metadata.get("jue_wiki_quality_summary"), dict)
            else application.get("quality_summary")
            if isinstance(application.get("quality_summary"), dict)
            else metadata.get("quality_summary")
            if isinstance(metadata.get("quality_summary"), dict)
            else {}
        )
        action_plan = (
            metadata.get("jue_wiki_quality_pressure_action_plan")
            if isinstance(metadata.get("jue_wiki_quality_pressure_action_plan"), dict)
            else application.get("quality_pressure_action_plan")
            if isinstance(application.get("quality_pressure_action_plan"), dict)
            else metadata.get("quality_pressure_action_plan")
            if isinstance(metadata.get("quality_pressure_action_plan"), dict)
            else {}
        )
        source_summary_source = (
            metadata.get("jue_wiki_quality_warning_source_summary")
            if isinstance(
                metadata.get("jue_wiki_quality_warning_source_summary"),
                dict,
            )
            else application.get("quality_warning_source_summary")
            if isinstance(application.get("quality_warning_source_summary"), dict)
            else metadata.get("quality_warning_source_summary")
            if isinstance(metadata.get("quality_warning_source_summary"), dict)
            else {}
        )
        source_summary = service_cls._prompt_wiki_quality_warning_source_summary(
            {"quality_warning_source_summary": source_summary_source}
        )
        repair_queue = service_cls._application_repair_queue_for_outcome_metadata(
            metadata=metadata,
            application=application,
        )
        if not quality_summary and not action_plan and not source_summary and not repair_queue:
            return evidence

        result = dict(evidence)
        selected_page = (
            result.get("selected_wiki_page")
            if isinstance(result.get("selected_wiki_page"), dict)
            else {}
        )
        selected_page_id = str(selected_page.get("page_id") or "").strip()
        selected_page_ids = {selected_page_id} if selected_page_id else set()
        metadata_warning_candidates = (
            service_cls._quality_pressure_warning_candidates(
                quality_summary=quality_summary,
                action_plan=action_plan,
                source_summary=source_summary,
            )
        )
        source_summary = service_cls._quality_warning_source_summary_for_pages(
            source_summary,
            selected_page_ids=selected_page_ids,
        )
        warnings: list[str] = []

        def add_warning(value: Any) -> str:
            warning = str(value or "").strip()
            if warning and warning not in warnings:
                warnings.append(warning)
            return warning

        existing_warning_page_ids = (
            result.get("warning_page_ids")
            if isinstance(result.get("warning_page_ids"), dict)
            else {}
        )
        warning_page_ids: dict[str, list[str]] = {}
        for warning, page_ids in existing_warning_page_ids.items():
            clean_page_ids = service_cls._quality_pressure_page_ids_for_selected_page(
                page_ids,
                selected_page_ids=selected_page_ids,
                limit=8,
            )
            warning_key = str(warning or "").strip()
            if warning_key and clean_page_ids:
                add_warning(warning_key)
                warning_page_ids[warning_key] = clean_page_ids

        for row in list(quality_summary.get("top_warnings") or []):
            if isinstance(row, dict):
                if not selected_page_ids:
                    add_warning(row.get("warning"))
        source_rows = [
            *list(source_summary.get("top_degraded_sources") or []),
            *list(source_summary.get("top_active_sources") or []),
        ]
        if selected_page_ids:
            source_rows = sorted(
                source_rows,
                key=lambda row: (
                    0
                    if isinstance(row, dict)
                    and str(row.get("page_id") or "").strip() in selected_page_ids
                    else 1
                ),
            )
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            for warning in list(row.get("quality_warnings") or []):
                warning_key = add_warning(warning)
                page_ids = service_cls._clean_page_ids([row.get("page_id")])[:1]
                if warning_key and page_ids:
                    warning_page_ids[warning_key] = list(
                        dict.fromkeys(
                            [
                                *warning_page_ids.get(warning_key, []),
                                *page_ids,
                            ]
                        )
                    )[:8]
        summary_warning_page_ids = (
            quality_summary.get("warning_page_ids")
            if isinstance(quality_summary.get("warning_page_ids"), dict)
            else {}
        )
        for warning, page_ids in summary_warning_page_ids.items():
            clean_page_ids = service_cls._quality_pressure_page_ids_for_selected_page(
                page_ids,
                selected_page_ids=selected_page_ids,
                limit=8,
            )
            warning_key = str(warning or "").strip()
            if warning_key and clean_page_ids:
                add_warning(warning_key)
                warning_page_ids[warning_key] = list(
                    dict.fromkeys(
                        [
                            *warning_page_ids.get(warning_key, []),
                            *clean_page_ids,
                        ]
                    )
                )[:8]

        repair_focus_page_ids = service_cls._clean_page_ids(
            result.get("repair_focus_page_ids")
        )[:12]
        repair_focus_page_ids = service_cls._quality_pressure_page_ids_for_selected_page(
            repair_focus_page_ids,
            selected_page_ids=selected_page_ids,
            limit=12,
        )
        for row in list(action_plan.get("repair_focus") or []):
            if not isinstance(row, dict):
                continue
            warning = str(row.get("warning") or "").strip()
            page_ids = service_cls._quality_pressure_page_ids_for_selected_page(
                row.get("page_ids"),
                selected_page_ids=selected_page_ids,
                limit=8,
            )
            if warning and page_ids:
                add_warning(warning)
            if page_ids:
                repair_focus_page_ids = list(
                    dict.fromkeys([*repair_focus_page_ids, *page_ids])
                )[:12]
            if warning and page_ids:
                warning_page_ids[warning] = list(
                    dict.fromkeys(
                        [
                            *warning_page_ids.get(warning, []),
                            *page_ids,
                        ]
                    )
                )[:8]
        for row in list(action_plan.get("required_adjustments") or []):
            if not isinstance(row, dict):
                continue
            warning = str(row.get("warning") or "").strip()
            page_ids = service_cls._quality_pressure_page_ids_for_selected_page(
                row.get("page_ids"),
                selected_page_ids=selected_page_ids,
                limit=8,
            )
            if warning and page_ids:
                add_warning(warning)
                warning_page_ids[warning] = list(
                    dict.fromkeys(
                        [
                            *warning_page_ids.get(warning, []),
                            *page_ids,
                        ]
                    )
                )[:8]

        existing_quality_warnings = [
            str(item).strip()
            for item in list(result.get("quality_warnings") or [])
            if str(item).strip()
        ]
        if selected_page_ids and existing_warning_page_ids:
            existing_quality_warnings = [
                warning
                for warning in existing_quality_warnings
                if warning in warnings or warning not in existing_warning_page_ids
            ]
        if selected_page_ids and metadata_warning_candidates:
            existing_quality_warnings = [
                warning
                for warning in existing_quality_warnings
                if warning in warnings or warning not in metadata_warning_candidates
            ]
        quality_warnings = list(
            dict.fromkeys([*existing_quality_warnings, *warnings])
        )
        caution_page_ids = list(
            dict.fromkeys(
                [
                    *service_cls._clean_page_ids(result.get("caution_page_ids")),
                    *service_cls._clean_page_ids(action_plan.get("caution_page_ids")),
                ]
            )
        )[:12]
        caution_page_ids = service_cls._quality_pressure_page_ids_for_selected_page(
            caution_page_ids,
            selected_page_ids=selected_page_ids,
            limit=12,
        )
        for key in (
            "warning_page_ids",
            "repair_focus_page_ids",
            "quality_warnings",
            "caution_page_ids",
            "quality_warning_source_summary",
        ):
            result.pop(key, None)
        if warning_page_ids:
            result["warning_page_ids"] = warning_page_ids
        if repair_focus_page_ids:
            result["repair_focus_page_ids"] = repair_focus_page_ids
        if quality_warnings:
            result["quality_warnings"] = quality_warnings[:8]
        if caution_page_ids:
            result["caution_page_ids"] = caution_page_ids
        if source_summary:
            result["quality_warning_source_summary"] = source_summary
        if repair_queue:
            result["jue_wiki_repair_queue"] = repair_queue
        return service_cls._clean_outcome_evidence(result)

    @staticmethod
    def _application_repair_queue_for_outcome_metadata(
        *,
        metadata: dict[str, Any],
        application: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(metadata.get("jue_wiki_repair_queue"), dict):
            raw_queue = metadata.get("jue_wiki_repair_queue")
        elif isinstance(application.get("repair_queue"), dict):
            raw_queue = application.get("repair_queue")
        elif isinstance(metadata.get("repair_queue"), dict):
            raw_queue = metadata.get("repair_queue")
        else:
            raw_queue = {}
        if not isinstance(raw_queue, dict):
            return {}
        queue: dict[str, Any] = {}
        for key in ("open_count", "resolved_count"):
            count = _safe_int(raw_queue.get(key))
            if count > 0:
                queue[key] = count
        open_symbols = [
            str(symbol).strip().upper()[:40]
            for symbol in _compact_prompt_string_list(
                raw_queue.get("open_symbols"),
                limit=64,
                max_len=40,
            )
            if str(symbol).strip()
        ]
        if open_symbols:
            queue["open_symbols"] = list(dict.fromkeys(open_symbols))
        action_batches = (
            JueWikiApplicationService._application_repair_queue_action_batches(
                raw_queue.get("open_action_batches")
            )
        )
        if action_batches:
            queue["open_action_batches"] = action_batches
        return {
            key: value
            for key, value in queue.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _application_repair_queue_action_batches(
        raw_batches: Any,
    ) -> list[dict[str, Any]]:
        batches: list[dict[str, Any]] = []
        for item in list(raw_batches or [])[:12]:
            if not isinstance(item, dict):
                continue
            batch: dict[str, Any] = {}
            scope = str(item.get("scope") or "").strip().lower()
            if scope:
                batch["scope"] = scope[:40]
            action_type = str(item.get("action_type") or "").strip()
            if action_type:
                batch["action_type"] = action_type[:120]
            count = _safe_int(item.get("count"))
            if count > 0:
                batch["count"] = count
            symbols = [
                str(symbol).strip().upper()[:40]
                for symbol in _compact_prompt_string_list(
                    item.get("symbols"),
                    limit=64,
                    max_len=40,
                )
                if str(symbol).strip()
            ]
            if symbols:
                batch["symbols"] = list(dict.fromkeys(symbols))
            warnings = _compact_prompt_string_list(
                item.get("warnings"),
                limit=16,
                max_len=120,
            )
            if warnings:
                batch["warnings"] = list(dict.fromkeys(warnings))
            if batch:
                batches.append(batch)
        return batches

    @staticmethod
    def _quality_pressure_warning_candidates(
        *,
        quality_summary: dict[str, Any],
        action_plan: dict[str, Any],
        source_summary: dict[str, Any],
    ) -> set[str]:
        warnings: set[str] = set()

        def add(value: Any) -> None:
            warning = str(value or "").strip()
            if warning:
                warnings.add(warning)

        for row in list(quality_summary.get("top_warnings") or []):
            if isinstance(row, dict):
                add(row.get("warning"))
        summary_warning_page_ids = (
            quality_summary.get("warning_page_ids")
            if isinstance(quality_summary.get("warning_page_ids"), dict)
            else {}
        )
        for warning in summary_warning_page_ids:
            add(warning)
        source_rows = [
            *list(source_summary.get("top_degraded_sources") or []),
            *list(source_summary.get("top_active_sources") or []),
        ]
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            for warning in list(row.get("quality_warnings") or []):
                add(warning)
        for key in ("repair_focus", "required_adjustments"):
            for row in list(action_plan.get(key) or []):
                if isinstance(row, dict):
                    add(row.get("warning"))
        return warnings

    @staticmethod
    def _quality_pressure_page_ids_for_selected_page(
        page_ids: Any,
        *,
        selected_page_ids: set[str],
        limit: int,
    ) -> list[str]:
        clean_page_ids = JueWikiApplicationService._clean_page_ids(page_ids)
        if selected_page_ids:
            clean_page_ids = [
                page_id
                for page_id in clean_page_ids
                if page_id in selected_page_ids
            ]
        return clean_page_ids[: max(int(limit), 0)]

    @staticmethod
    def _quality_warning_source_summary_for_pages(
        source_summary: dict[str, Any],
        *,
        selected_page_ids: set[str],
    ) -> dict[str, Any]:
        if not source_summary or not selected_page_ids:
            return source_summary

        def matching_rows(rows: Any) -> list[dict[str, Any]]:
            matched: list[dict[str, Any]] = []
            for row in list(rows or []):
                if not isinstance(row, dict):
                    continue
                page_id = str(row.get("page_id") or "").strip()
                if page_id in selected_page_ids:
                    matched.append(dict(row))
            return matched

        degraded_rows = matching_rows(source_summary.get("top_degraded_sources"))
        active_rows = matching_rows(source_summary.get("top_active_sources"))
        if not degraded_rows and not active_rows:
            return {}

        result = {
            key: value
            for key, value in source_summary.items()
            if key not in {"degraded_count", "active_count"}
        }
        result["source_count"] = len(degraded_rows) + len(active_rows)
        if degraded_rows:
            result["degraded_count"] = len(degraded_rows)
        if active_rows:
            result["active_count"] = len(active_rows)
        result["top_degraded_sources"] = degraded_rows
        result["top_active_sources"] = active_rows
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _outcome_evidence_with_selected_page_usage_guidance(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        guidance = (
            selected_page.get("usage_guidance")
            if isinstance(selected_page.get("usage_guidance"), dict)
            else {}
        )
        if not guidance:
            return evidence
        result = dict(evidence)
        result["usage_guidance"] = dict(guidance)
        for key in (
            "usage_guidance_required_cross_checks",
            "usage_guidance_risk_posture",
            "usage_guidance_trust_level",
        ):
            result.pop(key, None)
        required_cross_checks = [
            str(item).strip()
            for item in list(guidance.get("required_cross_checks") or [])[:8]
            if str(item).strip()
        ]
        if required_cross_checks:
            result["usage_guidance_required_cross_checks"] = required_cross_checks
        risk_posture = str(guidance.get("risk_posture") or "").strip()
        if risk_posture:
            result["usage_guidance_risk_posture"] = risk_posture
        trust_level = str(guidance.get("trust_level") or "").strip()
        if trust_level:
            result["usage_guidance_trust_level"] = trust_level
        return JueWikiApplicationService._clean_outcome_evidence(result)

    @staticmethod
    def _outcome_evidence_with_selected_page_usage_guidance_effectiveness(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        effectiveness = (
            selected_page.get("usage_guidance_effectiveness")
            if isinstance(selected_page.get("usage_guidance_effectiveness"), dict)
            else {}
        )
        if not effectiveness:
            return evidence
        result = dict(evidence)
        result["usage_guidance_effectiveness"] = dict(effectiveness)
        result.pop("usage_guidance_effectiveness_statuses", None)
        statuses: list[str] = []
        status = str(effectiveness.get("status") or "").strip().lower()
        if status:
            statuses.append(status)
        for metric in list(effectiveness.get("metrics") or []):
            if not isinstance(metric, dict):
                continue
            metric_status = str(metric.get("status") or "").strip().lower()
            if metric_status and metric_status not in statuses:
                statuses.append(metric_status)
        if statuses:
            result["usage_guidance_effectiveness_statuses"] = statuses
        return JueWikiApplicationService._clean_outcome_evidence(result)

    @staticmethod
    def _outcome_evidence_with_selected_page_memory_card_quality(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        memory_card_quality = (
            selected_page.get("memory_card_quality")
            if isinstance(selected_page.get("memory_card_quality"), dict)
            else {}
        )
        if not memory_card_quality:
            return evidence
        result = dict(evidence)
        result["memory_card_quality"] = dict(memory_card_quality)
        result.pop("memory_card_quality_statuses", None)
        statuses: list[str] = []
        status = str(memory_card_quality.get("status") or "").strip().lower()
        if status:
            statuses.append(status)
        for item in list(memory_card_quality.get("items") or []):
            if not isinstance(item, dict):
                continue
            item_status = str(item.get("status") or "").strip().lower()
            if item_status and item_status not in statuses:
                statuses.append(item_status)
        if statuses:
            result["memory_card_quality_statuses"] = statuses

        effectiveness = (
            selected_page.get("memory_card_quality_effectiveness")
            if isinstance(selected_page.get("memory_card_quality_effectiveness"), dict)
            else {}
        )
        effectiveness = (
            JueWikiApplicationService
            ._memory_card_quality_effectiveness_for_selected_page(
                effectiveness,
                memory_card_quality=memory_card_quality,
            )
        )
        if effectiveness:
            result["memory_card_quality_effectiveness"] = dict(effectiveness)
            result.pop("memory_card_quality_effectiveness_statuses", None)
            effectiveness_statuses: list[str] = []
            effectiveness_status = str(effectiveness.get("status") or "").strip().lower()
            if effectiveness_status:
                effectiveness_statuses.append(effectiveness_status)
            for metric in list(effectiveness.get("metrics") or []):
                if not isinstance(metric, dict):
                    continue
                metric_status = str(metric.get("status") or "").strip().lower()
                if metric_status and metric_status not in effectiveness_statuses:
                    effectiveness_statuses.append(metric_status)
            if effectiveness_statuses:
                result["memory_card_quality_effectiveness_statuses"] = (
                    effectiveness_statuses
                )

        resolution = str(memory_card_quality.get("resolution") or "").strip().lower()
        candidate_resolution_required = bool(
            memory_card_quality.get("candidate_resolution_required")
        )
        if candidate_resolution_required or (
            resolution and resolution != "resolved"
        ):
            quality_warnings = [
                str(item).strip()
                for item in list(result.get("quality_warnings") or [])
                if str(item).strip()
            ]
            if "memory_card_quality_unresolved" not in quality_warnings:
                quality_warnings.append("memory_card_quality_unresolved")
            result["quality_warnings"] = quality_warnings
        return JueWikiApplicationService._clean_outcome_evidence(result)

    @staticmethod
    def _memory_card_quality_effectiveness_for_selected_page(
        effectiveness: dict[str, Any],
        *,
        memory_card_quality: dict[str, Any],
    ) -> dict[str, Any]:
        if not effectiveness:
            return {}
        allowed_page_ids = {
            *[
                JueWikiApplicationService._memory_card_quality_page_id(
                    category="missing_field",
                    value=field,
                )
                for field in list(memory_card_quality.get("missing_fields") or [])[:8]
                if str(field).strip()
            ],
            *[
                JueWikiApplicationService._memory_card_quality_page_id(
                    category="required_check",
                    value=check,
                )
                for check in list(memory_card_quality.get("required_checks") or [])[:8]
                if str(check).strip()
            ],
        }
        if not allowed_page_ids:
            return {}
        metrics = [
            dict(metric)
            for metric in list(effectiveness.get("metrics") or [])
            if isinstance(metric, dict)
            and str(metric.get("page_id") or "").strip() in allowed_page_ids
        ]
        if not metrics:
            return {}
        result = {
            key: value
            for key, value in effectiveness.items()
            if key not in {"metrics", "status"}
        }
        result["metrics"] = metrics
        statuses = [
            str(metric.get("status") or "").strip().lower()
            for metric in metrics
            if str(metric.get("status") or "").strip()
        ]
        for preferred_status in ("degraded", "weak", "caution", "active", "probe"):
            if preferred_status in statuses:
                result["status"] = preferred_status
                break
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _outcome_evidence_with_selected_page_quality_warning_effectiveness(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        rows = JueWikiApplicationService._selected_page_quality_warning_effectiveness_for_evidence(
            selected_page
        )
        if not rows:
            return evidence
        result = dict(evidence)
        result["quality_warning_effectiveness"] = rows
        result.pop("quality_warning_effectiveness_statuses", None)
        statuses: list[str] = []
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status and status not in statuses:
                statuses.append(status)
        if statuses:
            result["quality_warning_effectiveness_statuses"] = statuses
        return JueWikiApplicationService._clean_outcome_evidence(result)

    @staticmethod
    def _selected_page_quality_warning_effectiveness_for_evidence(
        selected_page: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        target_limit = max(int(limit), 0)
        selected_page_id = str(selected_page.get("page_id") or "").strip()

        def add_rows_from_evidence_quality(evidence_quality: Any) -> None:
            if not isinstance(evidence_quality, dict):
                return
            for item in list(evidence_quality.get("warning_effectiveness") or []):
                if not isinstance(item, dict):
                    continue
                compact = _compact_quality_warning_effectiveness_for_prompt(item)
                row_page_id = str(compact.get("page_id") or "").strip()
                if (
                    selected_page_id
                    and row_page_id
                    and not row_page_id.startswith("quality_warning.")
                    and row_page_id != selected_page_id
                ):
                    continue
                marker = (
                    str(compact.get("warning") or ""),
                    str(compact.get("page_id") or ""),
                    str(compact.get("status") or ""),
                )
                if not compact or marker in seen:
                    continue
                seen.add(marker)
                rows.append(compact)
                if len(rows) >= target_limit:
                    return

        add_rows_from_evidence_quality(selected_page.get("evidence_quality"))
        if len(rows) >= target_limit:
            return rows

        direct_effectiveness = selected_page.get("quality_warning_effectiveness")
        if isinstance(direct_effectiveness, list):
            for item in direct_effectiveness:
                if not isinstance(item, dict):
                    continue
                compact = _compact_quality_warning_effectiveness_for_prompt(item)
                if (
                    compact
                    and selected_page_id
                    and not str(compact.get("page_id") or "").strip()
                ):
                    compact["page_id"] = selected_page_id
                row_page_id = str(compact.get("page_id") or "").strip()
                if (
                    selected_page_id
                    and row_page_id
                    and not row_page_id.startswith("quality_warning.")
                    and row_page_id != selected_page_id
                ):
                    continue
                marker = (
                    str(compact.get("warning") or ""),
                    str(compact.get("page_id") or ""),
                    str(compact.get("status") or ""),
                )
                if not compact or marker in seen:
                    continue
                seen.add(marker)
                rows.append(compact)
                if len(rows) >= target_limit:
                    return rows

        refs = JueWikiApplicationService._selected_page_source_refs(selected_page)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            add_rows_from_evidence_quality(ref.get("evidence_quality"))
            if len(rows) >= target_limit:
                return rows
        return rows

    @staticmethod
    def _outcome_evidence_with_selected_page_quality_warning_source_effectiveness(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        source_effectiveness = (
            selected_page.get("quality_warning_source_effectiveness")
            if isinstance(
                selected_page.get("quality_warning_source_effectiveness"),
                dict,
            )
            else {}
        )
        if not source_effectiveness:
            return evidence
        source_effectiveness = (
            JueWikiApplicationService._quality_warning_source_effectiveness_for_selected_page(
                source_effectiveness,
                selected_page=selected_page,
            )
        )
        if not source_effectiveness:
            return evidence
        result = dict(evidence)
        result["quality_warning_source_effectiveness"] = dict(source_effectiveness)
        result.pop("quality_warning_source_effectiveness_statuses", None)
        status = str(source_effectiveness.get("status") or "").strip().lower()
        statuses = [status] if status else []
        if not statuses:
            for row in list(source_effectiveness.get("metrics") or []):
                if not isinstance(row, dict):
                    continue
                row_status = str(row.get("status") or "").strip().lower()
                if row_status and row_status not in statuses:
                    statuses.append(row_status)
        if statuses:
            result["quality_warning_source_effectiveness_statuses"] = statuses
        return JueWikiApplicationService._clean_outcome_evidence(result)

    @staticmethod
    def _quality_warning_source_effectiveness_for_selected_page(
        source_effectiveness: dict[str, Any],
        *,
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        selected_page_id = str(selected_page.get("page_id") or "").strip()
        if not selected_page_id:
            return dict(source_effectiveness)

        metrics = [
            dict(row)
            for row in list(source_effectiveness.get("metrics") or [])
            if isinstance(row, dict)
            and str(row.get("page_id") or "").strip() == selected_page_id
        ]
        if not metrics:
            return {}

        result = dict(source_effectiveness)
        result["metrics"] = metrics
        statuses = [
            str(row.get("status") or "").strip().lower()
            for row in metrics
            if str(row.get("status") or "").strip()
        ]
        for preferred_status in ("degraded", "weak", "caution", "active"):
            if preferred_status in statuses:
                result["status"] = preferred_status
                break
        else:
            result.pop("status", None)
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _outcome_evidence_with_selected_page_repair_target_effectiveness(
        evidence: dict[str, Any],
        selected_page: dict[str, Any],
    ) -> dict[str, Any]:
        service_cls = JueWikiApplicationService
        targets = service_cls._selected_page_repair_targets_for_evidence(
            selected_page
        )
        effectiveness = service_cls._selected_page_repair_target_effectiveness_for_evidence(
            selected_page
        )
        if not targets and not effectiveness:
            return evidence
        result = dict(evidence)
        if targets:
            result["repair_targets"] = targets
        if effectiveness:
            result["repair_target_effectiveness"] = effectiveness
            result.pop("repair_target_effectiveness_statuses", None)
            statuses: list[str] = []
            for row in effectiveness:
                status = str(row.get("status") or "").strip().lower()
                if status and status not in statuses:
                    statuses.append(status)
            if statuses:
                result["repair_target_effectiveness_statuses"] = statuses
        return JueWikiApplicationService._clean_outcome_evidence(result)

    @staticmethod
    def _selected_page_repair_targets_for_evidence(
        selected_page: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        target_limit = max(int(limit), 0)
        selected_page_id = str(selected_page.get("page_id") or "").strip()
        selected_symbols = {
            str(symbol).strip().upper()
            for symbol in list(selected_page.get("symbols") or [])
            if str(symbol).strip()
        }

        def add_target(source: dict[str, Any]) -> None:
            compact = {
                key: str(source.get(key) or "").strip()[:180]
                for key in ("page_id", "symbol", "recommended_action")
                if str(source.get(key) or "").strip()
            }
            page_id = str(compact.get("page_id") or "").strip()
            if selected_page_id and page_id and page_id != selected_page_id:
                return
            symbol = str(compact.get("symbol") or "").strip().upper()
            if selected_symbols and symbol and symbol not in selected_symbols:
                return
            marker = (
                compact.get("page_id", ""),
                compact.get("symbol", ""),
                compact.get("recommended_action", ""),
            )
            if not compact or marker in seen:
                return
            seen.add(marker)
            targets.append(compact)

        for target in list(selected_page.get("repair_targets") or []):
            if isinstance(target, dict):
                add_target(target)
            if len(targets) >= target_limit:
                return targets

        refs = JueWikiApplicationService._selected_page_source_refs(selected_page)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            for target in list(ref.get("repair_targets") or []):
                if isinstance(target, dict):
                    add_target(target)
                if len(targets) >= target_limit:
                    return targets

        repair_queue = (
            selected_page.get("repair_queue")
            if isinstance(selected_page.get("repair_queue"), dict)
            else {}
        )
        action = str(repair_queue.get("action_type") or "").strip()
        if action:
            symbols = [
                str(symbol).strip().upper()
                for symbol in list(repair_queue.get("symbols") or [])
                if str(symbol).strip()
            ]
            if not symbols:
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(selected_page.get("symbols") or [])
                    if str(symbol).strip()
                ]
            for symbol in symbols[:target_limit]:
                add_target(
                    {
                        "page_id": str(selected_page.get("page_id") or ""),
                        "symbol": symbol,
                        "recommended_action": action,
                    }
                )
                if len(targets) >= target_limit:
                    return targets
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            source_type = str(ref.get("source_type") or "").strip().lower()
            action = str(ref.get("action_type") or "").strip()
            if source_type != "wiki_repair_queue" or not action:
                continue
            symbols = [
                str(symbol).strip().upper()
                for symbol in list(ref.get("symbols") or [])
                if str(symbol).strip()
            ]
            if not symbols:
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(selected_page.get("symbols") or [])
                    if str(symbol).strip()
                ]
            for symbol in symbols[:target_limit]:
                add_target(
                    {
                        "page_id": str(selected_page.get("page_id") or ""),
                        "symbol": symbol,
                        "recommended_action": action,
                    }
                )
                if len(targets) >= target_limit:
                    return targets
        return targets

    @staticmethod
    def _selected_page_source_refs(
        selected_page: dict[str, Any],
        *,
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        refs = (
            selected_page.get("source_refs")
            if isinstance(selected_page.get("source_refs"), list)
            else []
        )
        rows: list[dict[str, Any]] = []

        def visit(source_refs: list[Any], *, depth: int) -> None:
            if depth > max(int(max_depth), 0):
                return
            for ref in source_refs:
                if not isinstance(ref, dict):
                    continue
                rows.append(ref)
                nested = ref.get("source_refs")
                if isinstance(nested, list):
                    visit(nested, depth=depth + 1)

        visit(refs, depth=0)
        return rows

    @staticmethod
    def _selected_page_repair_target_effectiveness_for_evidence(
        selected_page: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        target_limit = max(int(limit), 0)
        allowed_actions = {
            str(target.get("recommended_action") or "").strip()
            for target in JueWikiApplicationService._selected_page_repair_targets_for_evidence(
                selected_page,
                limit=limit,
            )
            if str(target.get("recommended_action") or "").strip()
        }

        def compact_actions(compact: dict[str, Any]) -> set[str]:
            actions: set[str] = set()
            page_id = str(compact.get("page_id") or "").strip()
            if page_id.startswith("repair_target."):
                actions.add(page_id.removeprefix("repair_target."))
            for reason in list(compact.get("reasons") or []):
                reason_text = str(reason or "").strip()
                if reason_text.startswith("repair_target:"):
                    actions.add(reason_text.removeprefix("repair_target:"))
            return {action for action in actions if action}

        def add_raw_source(raw_source: Any) -> None:
            if isinstance(raw_source, dict):
                raw_rows = [raw_source]
            elif isinstance(raw_source, list):
                raw_rows = [row for row in raw_source if isinstance(row, dict)]
            else:
                raw_rows = []
            for row in raw_rows:
                compact = (
                    JueWikiApplicationService
                    ._prompt_repair_target_effectiveness_summary(row)
                )
                if allowed_actions and not (
                    compact_actions(compact) & allowed_actions
                ):
                    continue
                marker = (
                    str(compact.get("page_id") or ""),
                    str(compact.get("status") or ""),
                )
                if not compact or marker in seen:
                    continue
                seen.add(marker)
                rows.append(compact)
                if len(rows) >= target_limit:
                    return

        add_raw_source(selected_page.get("repair_target_effectiveness"))
        if len(rows) >= target_limit:
            return rows
        refs = JueWikiApplicationService._selected_page_source_refs(selected_page)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            add_raw_source(ref.get("repair_target_effectiveness"))
            if len(rows) >= target_limit:
                return rows
        return rows

    @staticmethod
    def _clean_outcome_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        return {
            str(key): value
            for key, value in evidence.items()
            if value not in (None, "", [], {})
        }

    def project_page_effectiveness(self, *, min_samples: int = 5) -> dict[str, Any]:
        self.wiki.initialize()
        selected_page_backfilled = self.backfill_outcome_selected_page_evidence()
        outcomes = self.list_selection_outcomes(limit=50_000)
        attributable_outcomes = [
            row for row in outcomes if self._outcome_row_is_attributable(row)
        ]
        final_outcomes = [row for row in outcomes if self._is_final_outcome(row)]
        filtered_final_outcomes = [
            row for row in final_outcomes if not self._outcome_row_is_attributable(row)
        ]
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in attributable_outcomes:
            if not self._is_final_outcome(row):
                continue
            key = self._selection_outcome_metric_key(row)
            if key[0] and key[1]:
                groups.setdefault(key, []).append(row)

        updated = 0
        for (page_id, scope, venue, horizon), rows in groups.items():
            metric = self._effectiveness_metric(
                page_id=page_id,
                decision_scope=scope,
                venue=venue,
                horizon=horizon,
                rows=rows,
                min_samples=min_samples,
            )
            self.wiki.upsert_page_effectiveness(metric)
            updated += 1
        warning_updated = self._project_quality_warning_effectiveness(
            outcomes=attributable_outcomes,
            min_samples=min_samples,
        )
        warning_source_updated = (
            self._project_quality_warning_source_page_effectiveness(
                outcomes=attributable_outcomes,
                min_samples=min_samples,
            )
        )
        repair_target_updated = self._project_repair_target_effectiveness(
            outcomes=attributable_outcomes,
            min_samples=min_samples,
        )
        usage_guidance_updated = self._project_usage_guidance_effectiveness(
            outcomes=attributable_outcomes,
            min_samples=min_samples,
        )
        memory_card_quality_updated = self._project_memory_card_quality_effectiveness(
            outcomes=attributable_outcomes,
            min_samples=min_samples,
        )
        active_metric_keys = set(groups)
        active_metric_keys.update(self._quality_warning_metric_keys(attributable_outcomes))
        active_metric_keys.update(
            self._quality_warning_source_metric_keys(attributable_outcomes)
        )
        active_metric_keys.update(self._repair_target_metric_keys(attributable_outcomes))
        active_metric_keys.update(self._usage_guidance_metric_keys(attributable_outcomes))
        active_metric_keys.update(
            self._memory_card_quality_metric_keys(attributable_outcomes)
        )
        stale_metric_keys = self._stale_effectiveness_metric_keys(
            filtered_outcomes=filtered_final_outcomes,
            active_metric_keys=active_metric_keys,
        )
        stale_removed = self._delete_page_effectiveness_metrics(stale_metric_keys)
        result = {"status": "ok", "updated_count": updated}
        attribution_filtered_count = len(outcomes) - len(attributable_outcomes)
        if attribution_filtered_count:
            result["attribution_filtered_count"] = attribution_filtered_count
        if stale_removed:
            result["stale_effectiveness_removed_count"] = stale_removed
            result["stale_effectiveness_removed_scopes"] = sorted(
                {
                    scope
                    for _, scope, _, _ in stale_metric_keys
                    if scope
                }
            )
        if selected_page_backfilled:
            result["selected_page_evidence_backfilled_count"] = selected_page_backfilled
        if warning_updated:
            result["quality_warning_updated_count"] = warning_updated
        if warning_source_updated:
            result["quality_warning_source_page_updated_count"] = (
                warning_source_updated
            )
        if repair_target_updated:
            result["repair_target_updated_count"] = repair_target_updated
        if usage_guidance_updated:
            result["usage_guidance_updated_count"] = usage_guidance_updated
        if memory_card_quality_updated:
            result["memory_card_quality_updated_count"] = (
                memory_card_quality_updated
            )
        return result

    def project_prompt_mode_effectiveness(
        self,
        *,
        min_samples: int = 5,
        limit: int = 50_000,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    o.*,
                    l.prompt_mode AS link_prompt_mode,
                    l.metadata_json AS link_metadata_json,
                    r.request_json AS selection_request_json
                FROM wiki_selection_outcomes AS o
                LEFT JOIN wiki_decision_links AS l
                  ON l.link_id = o.link_id
                LEFT JOIN wiki_selection_runs AS r
                  ON r.run_id = o.selection_run_id
                ORDER BY o.computed_at DESC, o.outcome_id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        decision_rows = self._dedupe_prompt_mode_outcomes(
            [
                {
                    **dict(row),
                    "evidence": _json_loads(str(row["evidence_json"]), {}),
                    "selection_request": _json_loads(
                        str(row["selection_request_json"] or "{}"),
                        {},
                    ),
                }
                for row in rows
                if self._is_final_outcome(dict(row))
            ]
        )
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in decision_rows:
            prompt_mode = self._prompt_mode_from_outcome_row(row)
            scope = str(row.get("decision_scope") or "").strip().lower()
            if not scope or not prompt_mode:
                continue
            groups.setdefault((scope, prompt_mode), []).append(row)
        modes = [
            self._prompt_mode_effectiveness_metric(
                decision_scope=scope,
                prompt_mode=prompt_mode,
                rows=group_rows,
                min_samples=min_samples,
            )
            for (scope, prompt_mode), group_rows in sorted(groups.items())
        ]
        return {
            "status": "ok",
            "decision_sample_count": len(decision_rows),
            "mode_count": len(modes),
            "modes": modes,
        }

    def project_trust_profile_effectiveness(
        self,
        *,
        min_samples: int = 5,
        limit: int = 50_000,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    o.*,
                    l.prompt_mode AS link_prompt_mode,
                    l.metadata_json AS link_metadata_json
                FROM wiki_selection_outcomes AS o
                LEFT JOIN wiki_decision_links AS l
                  ON l.link_id = o.link_id
                ORDER BY o.computed_at DESC, o.outcome_id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        decision_rows = self._dedupe_prompt_mode_outcomes(
            [
                {
                    **dict(row),
                    "evidence": _json_loads(str(row["evidence_json"]), {}),
                    "link_metadata": _json_loads(
                        str(row["link_metadata_json"] or "{}"),
                        {},
                    ),
                }
                for row in rows
                if self._is_final_outcome(dict(row))
            ]
        )
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in decision_rows:
            scope = str(row.get("decision_scope") or "").strip().lower()
            profile = self._trust_profile_from_outcome_row(row)
            authority = str(profile.get("authority") or "").strip().lower()
            if not scope or not authority:
                continue
            row["jue_wiki_trust_profile"] = profile
            groups.setdefault((scope, authority), []).append(row)
        trust_profiles = [
            self._trust_profile_effectiveness_metric(
                decision_scope=scope,
                authority=authority,
                rows=group_rows,
                min_samples=min_samples,
            )
            for (scope, authority), group_rows in sorted(groups.items())
        ]
        return {
            "status": "ok",
            "decision_sample_count": len(decision_rows),
            "trust_profile_count": len(trust_profiles),
            "trust_profiles": trust_profiles,
        }

    @staticmethod
    def _dedupe_prompt_mode_outcomes(
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            key = (
                str(row.get("link_id") or ""),
                str(row.get("outcome_kind") or ""),
                str(row.get("decision_scope") or ""),
                str(row.get("symbol") or ""),
                str(row.get("block_id") or evidence.get("block_id") or ""),
                str(row.get("horizon") or ""),
                str(row.get("outcome_status") or ""),
                float(row.get("return_pct") or 0.0),
                float(row.get("pnl_value") or 0.0),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    @staticmethod
    def _prompt_mode_from_outcome_row(row: dict[str, Any]) -> str:
        mode = str(row.get("link_prompt_mode") or "").strip().lower()
        if mode in {"observe", "assist", "primary"}:
            return mode
        request = row.get("selection_request")
        if isinstance(request, dict):
            application = request.get("prompt_mode_application")
            if isinstance(application, dict):
                mode = str(application.get("prompt_mode") or "").strip().lower()
                if mode in {"observe", "assist", "primary"}:
                    return mode
        return ""

    @staticmethod
    def _trust_profile_from_outcome_row(row: dict[str, Any]) -> dict[str, Any]:
        metadata = (
            row.get("link_metadata")
            if isinstance(row.get("link_metadata"), dict)
            else {}
        )
        profile = metadata.get("jue_wiki_trust_profile")
        if not isinstance(profile, dict):
            profile = metadata.get("trust_profile")
        return dict(profile) if isinstance(profile, dict) else {}

    def _trust_profile_effectiveness_metric(
        self,
        *,
        decision_scope: str,
        authority: str,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        sample_count = len(rows)
        returns = [float(row.get("return_pct") or 0.0) for row in rows]
        wins = sum(1 for value in returns if value > 0)
        win_rate = wins / sample_count if sample_count else 0.0
        avg_return = sum(returns) / sample_count if sample_count else 0.0
        confidence = min(sample_count / max(int(min_samples), 1), 1.0)
        helpful_score = _clamp(
            avg_return * 10.0 + (win_rate - 0.5) * 10.0,
            -10.0,
            10.0,
        )
        if sample_count < int(min_samples):
            status = "probe"
        elif avg_return > 0 and win_rate >= 0.5:
            status = "active"
        elif avg_return < 0 or win_rate < 0.4:
            status = "degraded"
        else:
            status = "probe"
        trust_level_counts: dict[str, int] = {}
        posture_counts: dict[str, int] = {}
        prompt_mode_counts: dict[str, int] = {}
        usage_contract_counts: dict[str, dict[str, int]] = {
            "risk_posture": {},
            "allowed_uses": {},
            "required_cross_checks": {},
        }
        recommendation_ids: set[str] = set()
        for row in rows:
            profile = (
                row.get("jue_wiki_trust_profile")
                if isinstance(row.get("jue_wiki_trust_profile"), dict)
                else {}
            )
            for key, bucket in (
                ("trust_level", trust_level_counts),
                ("posture", posture_counts),
                ("prompt_mode", prompt_mode_counts),
            ):
                value = str(profile.get(key) or "").strip().lower()
                if value:
                    bucket[value] = bucket.get(value, 0) + 1
            recommendation_id = str(profile.get("recommendation_id") or "").strip()
            if recommendation_id:
                recommendation_ids.add(recommendation_id)
            usage_contract = (
                profile.get("usage_contract")
                if isinstance(profile.get("usage_contract"), dict)
                else {}
            )
            risk_posture = (
                str(usage_contract.get("risk_posture") or "").strip().lower()
            )
            if risk_posture:
                bucket = usage_contract_counts["risk_posture"]
                bucket[risk_posture] = bucket.get(risk_posture, 0) + 1
            for key in ("allowed_uses", "required_cross_checks"):
                bucket = usage_contract_counts[key]
                for item in list(usage_contract.get(key) or [])[:12]:
                    value = str(item).strip().lower()
                    if value:
                        bucket[value] = bucket.get(value, 0) + 1
        compact_usage_contract_counts = {
            key: dict(sorted(bucket.items()))
            for key, bucket in usage_contract_counts.items()
            if bucket
        }
        risk_posture_metrics = self._usage_contract_risk_posture_metrics(
            rows,
            min_samples=min_samples,
        )
        decision_adjustment_metrics = self._decision_adjustment_metrics(
            rows,
            min_samples=min_samples,
        )
        decision_adjustment_audit_metrics = (
            self._decision_adjustment_audit_metrics(
                rows,
                min_samples=min_samples,
            )
        )
        return {
            "decision_scope": decision_scope,
            "authority": authority,
            "sample_count": sample_count,
            "win_rate": win_rate,
            "avg_return_pct": avg_return,
            "helpful_score": helpful_score,
            "confidence": confidence,
            "status": status,
            "trust_level_counts": dict(sorted(trust_level_counts.items())),
            "posture_counts": dict(sorted(posture_counts.items())),
            "prompt_mode_counts": dict(sorted(prompt_mode_counts.items())),
            "usage_contract_counts": compact_usage_contract_counts,
            "risk_posture_metrics": risk_posture_metrics,
            "decision_adjustment_metrics": decision_adjustment_metrics,
            "decision_adjustment_audit_metrics": decision_adjustment_audit_metrics,
            "recommendation_ids": sorted(recommendation_ids)[:10],
            "reasons": [
                f"samples:{sample_count}",
                f"win_rate:{win_rate:.4f}",
                f"avg_return_pct:{avg_return:.4f}",
                f"helpful_score:{helpful_score:.4f}",
            ],
        }

    @staticmethod
    def _usage_contract_risk_posture_metrics(
        rows: list[dict[str, Any]],
        *,
        min_samples: int,
    ) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            profile = (
                row.get("jue_wiki_trust_profile")
                if isinstance(row.get("jue_wiki_trust_profile"), dict)
                else {}
            )
            usage_contract = (
                profile.get("usage_contract")
                if isinstance(profile.get("usage_contract"), dict)
                else {}
            )
            risk_posture = (
                str(usage_contract.get("risk_posture") or "").strip().lower()
            )
            if risk_posture:
                groups.setdefault(risk_posture, []).append(row)
        metrics: list[dict[str, Any]] = []
        for risk_posture, group_rows in sorted(groups.items()):
            sample_count = len(group_rows)
            returns = [float(row.get("return_pct") or 0.0) for row in group_rows]
            wins = sum(1 for value in returns if value > 0)
            win_rate = wins / sample_count if sample_count else 0.0
            avg_return = sum(returns) / sample_count if sample_count else 0.0
            confidence = min(sample_count / max(int(min_samples), 1), 1.0)
            helpful_score = _clamp(
                avg_return * 10.0 + (win_rate - 0.5) * 10.0,
                -10.0,
                10.0,
            )
            if sample_count < int(min_samples):
                status = "probe"
            elif avg_return > 0 and win_rate >= 0.5:
                status = "active"
            elif avg_return < 0 or win_rate < 0.4:
                status = "degraded"
            else:
                status = "probe"
            metrics.append(
                {
                    "risk_posture": risk_posture,
                    "sample_count": sample_count,
                    "win_rate": win_rate,
                    "avg_return_pct": avg_return,
                    "helpful_score": helpful_score,
                    "confidence": confidence,
                    "status": status,
                }
            )
        return metrics

    @staticmethod
    def _decision_adjustment_metrics(
        rows: list[dict[str, Any]],
        *,
        min_samples: int,
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        adjustment_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            metadata = (
                row.get("link_metadata")
                if isinstance(row.get("link_metadata"), dict)
                else {}
            )
            adjustments = (
                metadata.get("jue_wiki_decision_adjustments")
                if isinstance(metadata.get("jue_wiki_decision_adjustments"), list)
                else []
            )
            for adjustment in adjustments[:6]:
                if not isinstance(adjustment, dict):
                    continue
                action = str(adjustment.get("action") or "").strip().lower()
                target = (
                    str(adjustment.get("target_risk_posture") or "")
                    .strip()
                    .lower()
                )
                reason = str(adjustment.get("reason") or "").strip().lower()
                current = (
                    str(adjustment.get("current_risk_posture") or "")
                    .strip()
                    .lower()
                )
                current_status = (
                    str(adjustment.get("current_status") or "").strip().lower()
                )
                if not action:
                    continue
                key = (action, target, reason, current, current_status)
                groups.setdefault(key, []).append(row)
                adjustment_by_key.setdefault(key, adjustment)
        metrics: list[dict[str, Any]] = []
        for key, group_rows in sorted(groups.items()):
            action, target, reason, current, current_status = key
            sample_count = len(group_rows)
            returns = [float(row.get("return_pct") or 0.0) for row in group_rows]
            wins = sum(1 for value in returns if value > 0)
            win_rate = wins / sample_count if sample_count else 0.0
            avg_return = sum(returns) / sample_count if sample_count else 0.0
            confidence = min(sample_count / max(int(min_samples), 1), 1.0)
            helpful_score = _clamp(
                avg_return * 10.0 + (win_rate - 0.5) * 10.0,
                -10.0,
                10.0,
            )
            if sample_count < int(min_samples):
                status = "probe"
            elif avg_return > 0 and win_rate >= 0.5:
                status = "active"
            elif avg_return < 0 or win_rate < 0.4:
                status = "degraded"
            else:
                status = "probe"
            adjustment = adjustment_by_key.get(key, {})
            recommended_counts: dict[str, int] = {}
            deprioritized_counts: dict[str, int] = {}
            evidence_grade_counts: dict[str, int] = {}
            evidence_grade_instruction_counts: dict[str, int] = {}
            evidence_grade_groups: dict[
                tuple[str, str, str],
                list[dict[str, Any]],
            ] = {}
            for row in group_rows:
                metadata = (
                    row.get("link_metadata")
                    if isinstance(row.get("link_metadata"), dict)
                    else {}
                )
                for candidate in list(
                    metadata.get("jue_wiki_decision_adjustments") or []
                )[:6]:
                    if not isinstance(candidate, dict):
                        continue
                    candidate_key = (
                        str(candidate.get("action") or "").strip().lower(),
                        str(candidate.get("target_risk_posture") or "")
                        .strip()
                        .lower(),
                        str(candidate.get("reason") or "").strip().lower(),
                        str(candidate.get("current_risk_posture") or "")
                        .strip()
                        .lower(),
                        str(candidate.get("current_status") or "").strip().lower(),
                    )
                    if candidate_key != key:
                        continue
                    for item in list(
                        candidate.get("recommended_allowed_uses") or []
                    )[:8]:
                        value = str(item).strip().lower()
                        if value:
                            recommended_counts[value] = (
                                recommended_counts.get(value, 0) + 1
                            )
                    for item in list(
                        candidate.get("deprioritized_allowed_uses") or []
                    )[:8]:
                        value = str(item).strip().lower()
                        if value:
                            deprioritized_counts[value] = (
                                deprioritized_counts.get(value, 0) + 1
                            )
                    evidence_grade = (
                        candidate.get("evidence_grade")
                        if isinstance(candidate.get("evidence_grade"), dict)
                        else {}
                    )
                    if evidence_grade:
                        grade_status = (
                            str(evidence_grade.get("status") or "")
                            .strip()
                            .lower()
                        )
                        instruction = (
                            str(evidence_grade.get("instruction") or "")
                            .strip()
                            .lower()
                        )
                        basis = (
                            str(evidence_grade.get("basis") or "")
                            .strip()
                            .lower()
                        )
                        if grade_status:
                            evidence_grade_counts[grade_status] = (
                                evidence_grade_counts.get(grade_status, 0) + 1
                            )
                        if instruction:
                            evidence_grade_instruction_counts[instruction] = (
                                evidence_grade_instruction_counts.get(
                                    instruction,
                                    0,
                                )
                                + 1
                            )
                        if grade_status or instruction or basis:
                            evidence_grade_groups.setdefault(
                                (grade_status, instruction, basis),
                                [],
                            ).append(row)
            metric: dict[str, Any] = {
                "action": action,
                "sample_count": sample_count,
                "win_rate": win_rate,
                "avg_return_pct": avg_return,
                "helpful_score": helpful_score,
                "confidence": confidence,
                "status": status,
            }
            if target:
                metric["target_risk_posture"] = target
            if reason:
                metric["reason"] = reason
            if current:
                metric["current_risk_posture"] = current
            if current_status:
                metric["current_status"] = current_status
            source = str(adjustment.get("source") or "").strip()
            if source:
                metric["source"] = source[:180]
            if recommended_counts:
                metric["recommended_allowed_uses_counts"] = dict(
                    sorted(recommended_counts.items())
                )
            if deprioritized_counts:
                metric["deprioritized_allowed_uses_counts"] = dict(
                    sorted(deprioritized_counts.items())
                )
            if evidence_grade_counts:
                metric["evidence_grade_counts"] = dict(
                    sorted(evidence_grade_counts.items())
                )
            if evidence_grade_instruction_counts:
                metric["evidence_grade_instruction_counts"] = dict(
                    sorted(evidence_grade_instruction_counts.items())
                )
            if evidence_grade_groups:
                grade_performance: list[dict[str, Any]] = []
                for grade_key, grade_rows in sorted(evidence_grade_groups.items()):
                    grade_status, instruction, basis = grade_key
                    grade_sample_count = len(grade_rows)
                    grade_returns = [
                        float(row.get("return_pct") or 0.0) for row in grade_rows
                    ]
                    grade_wins = sum(1 for value in grade_returns if value > 0)
                    grade_metric: dict[str, Any] = {
                        "sample_count": grade_sample_count,
                        "win_rate": (
                            grade_wins / grade_sample_count
                            if grade_sample_count
                            else 0.0
                        ),
                        "avg_return_pct": (
                            sum(grade_returns) / grade_sample_count
                            if grade_sample_count
                            else 0.0
                        ),
                    }
                    if grade_status:
                        grade_metric["status"] = grade_status
                    if instruction:
                        grade_metric["instruction"] = instruction
                    if basis:
                        grade_metric["basis"] = basis
                    grade_performance.append(grade_metric)
                metric["evidence_grade_performance"] = grade_performance
            metrics.append(metric)
        return metrics

    @staticmethod
    def _decision_adjustment_audit_metrics(
        rows: list[dict[str, Any]],
        *,
        min_samples: int,
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        contract_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            metadata = (
                row.get("link_metadata")
                if isinstance(row.get("link_metadata"), dict)
                else {}
            )
            contract = (
                metadata.get("jue_wiki_decision_adjustment_audit_contract")
                if isinstance(
                    metadata.get("jue_wiki_decision_adjustment_audit_contract"),
                    dict,
                )
                else {}
            )
            if not contract:
                continue
            actions = [
                str(item).strip().lower()
                for item in list(contract.get("actions") or [])[:6]
                if str(item).strip()
            ] or ["unknown"]
            targets = [
                str(item).strip().lower()
                for item in list(contract.get("target_risk_postures") or [])[:6]
                if str(item).strip()
            ] or [""]
            status = str(contract.get("status") or "").strip().lower()
            for action in actions:
                for target in targets:
                    key = (action, target, status)
                    groups.setdefault(key, []).append(row)
                    contract_by_key.setdefault(key, contract)
        metrics: list[dict[str, Any]] = []
        for key, group_rows in sorted(groups.items()):
            action, target, contract_status = key
            sample_count = len(group_rows)
            returns = [float(row.get("return_pct") or 0.0) for row in group_rows]
            wins = sum(1 for value in returns if value > 0)
            win_rate = wins / sample_count if sample_count else 0.0
            avg_return = sum(returns) / sample_count if sample_count else 0.0
            confidence = min(sample_count / max(int(min_samples), 1), 1.0)
            helpful_score = _clamp(
                avg_return * 10.0 + (win_rate - 0.5) * 10.0,
                -10.0,
                10.0,
            )
            if sample_count < int(min_samples):
                status = "probe"
            elif avg_return > 0 and win_rate >= 0.5:
                status = "active"
            elif avg_return < 0 or win_rate < 0.4:
                status = "degraded"
            else:
                status = "probe"
            required_review_counts: dict[str, int] = {}
            accepted_resolution_counts: dict[str, int] = {}
            for row in group_rows:
                metadata = (
                    row.get("link_metadata")
                    if isinstance(row.get("link_metadata"), dict)
                    else {}
                )
                contract = (
                    metadata.get("jue_wiki_decision_adjustment_audit_contract")
                    if isinstance(
                        metadata.get("jue_wiki_decision_adjustment_audit_contract"),
                        dict,
                    )
                    else {}
                )
                for item in list(contract.get("required_review") or [])[:8]:
                    value = str(item).strip()
                    if value:
                        required_review_counts[value] = (
                            required_review_counts.get(value, 0) + 1
                        )
                for item in list(contract.get("accepted_resolutions") or [])[:8]:
                    value = str(item).strip()
                    if value:
                        accepted_resolution_counts[value] = (
                            accepted_resolution_counts.get(value, 0) + 1
                        )
            source_contract = contract_by_key.get(key, {})
            metric: dict[str, Any] = {
                "action": action,
                "sample_count": sample_count,
                "win_rate": win_rate,
                "avg_return_pct": avg_return,
                "helpful_score": helpful_score,
                "confidence": confidence,
                "status": status,
            }
            if target:
                metric["target_risk_posture"] = target
            if contract_status:
                metric["contract_status"] = contract_status
            version = str(source_contract.get("version") or "").strip()
            if version:
                metric["version"] = version[:160]
            if required_review_counts:
                metric["required_review_counts"] = dict(
                    sorted(required_review_counts.items())
                )
            if accepted_resolution_counts:
                metric["accepted_resolution_counts"] = dict(
                    sorted(accepted_resolution_counts.items())
                )
            metrics.append(metric)
        return metrics

    def _prompt_mode_effectiveness_metric(
        self,
        *,
        decision_scope: str,
        prompt_mode: str,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        sample_count = len(rows)
        returns = [float(row.get("return_pct") or 0.0) for row in rows]
        wins = sum(1 for value in returns if value > 0)
        win_rate = wins / sample_count if sample_count else 0.0
        avg_return = sum(returns) / sample_count if sample_count else 0.0
        confidence = min(sample_count / max(int(min_samples), 1), 1.0)
        helpful_score = _clamp(
            avg_return * 10.0 + (win_rate - 0.5) * 10.0,
            -10.0,
            10.0,
        )
        if sample_count < int(min_samples):
            status = "probe"
        elif avg_return > 0 and win_rate >= 0.5:
            status = "active"
        elif avg_return < 0 or win_rate < 0.4:
            status = "degraded"
        else:
            status = "probe"
        recommended_mode_counts: dict[str, int] = {}
        recommendation_ids: set[str] = set()
        for row in rows:
            request = row.get("selection_request")
            application = (
                request.get("prompt_mode_application")
                if isinstance(request, dict)
                else {}
            )
            recommendation = (
                application.get("mode_recommendation")
                if isinstance(application, dict)
                else {}
            )
            if not isinstance(recommendation, dict):
                continue
            recommended_mode = str(
                recommendation.get("recommended_mode") or ""
            ).strip().lower()
            if recommended_mode:
                recommended_mode_counts[recommended_mode] = (
                    recommended_mode_counts.get(recommended_mode, 0) + 1
                )
            recommendation_id = str(recommendation.get("recommendation_id") or "")
            if recommendation_id:
                recommendation_ids.add(recommendation_id)
        return {
            "decision_scope": decision_scope,
            "prompt_mode": prompt_mode,
            "sample_count": sample_count,
            "win_rate": win_rate,
            "avg_return_pct": avg_return,
            "helpful_score": helpful_score,
            "confidence": confidence,
            "status": status,
            "recommended_mode_counts": dict(sorted(recommended_mode_counts.items())),
            "recommendation_ids": sorted(recommendation_ids)[:10],
            "reasons": [
                f"samples:{sample_count}",
                f"win_rate:{win_rate:.4f}",
                f"avg_return_pct:{avg_return:.4f}",
                f"helpful_score:{helpful_score:.4f}",
            ],
        }

    @staticmethod
    def _is_final_outcome(row: dict[str, Any]) -> bool:
        return str(row.get("outcome_status") or "").lower() in {"win", "loss", "flat"}

    @staticmethod
    def _selection_outcome_metric_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("page_id") or ""),
            str(row.get("decision_scope") or ""),
            str(row.get("venue") or ""),
            str(row.get("horizon") or ""),
        )

    def _quality_warning_metric_keys(
        self,
        outcomes: list[dict[str, Any]],
    ) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._quality_warning_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    keys.add(key)
        return keys

    def _quality_warning_source_metric_keys(
        self,
        outcomes: list[dict[str, Any]],
    ) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._quality_warning_source_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    keys.add(key)
        return keys

    def _quality_warning_page_ids_for_outcome(self, row: dict[str, Any]) -> list[str]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        page = (
            evidence.get("selected_wiki_page")
            if isinstance(evidence.get("selected_wiki_page"), dict)
            else {}
        )
        warnings = [
            self._quality_warning_page_id(warning)
            for warning in [
                *self._selected_wiki_page_quality_warnings(page),
                *list(evidence.get("quality_warnings") or []),
            ]
            if str(warning).strip()
        ]
        return list(dict.fromkeys(warnings))

    def _quality_warning_source_page_ids_for_outcome(
        self,
        row: dict[str, Any],
    ) -> list[str]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        page_ids: list[str] = []
        warning_page_ids = (
            evidence.get("warning_page_ids")
            if isinstance(evidence.get("warning_page_ids"), dict)
            else {}
        )
        for value in warning_page_ids.values():
            page_ids.extend(self._clean_page_ids(value))
        page_ids.extend(self._clean_page_ids(evidence.get("repair_focus_page_ids")))
        source_effectiveness = (
            evidence.get("quality_warning_source_effectiveness")
            if isinstance(evidence.get("quality_warning_source_effectiveness"), dict)
            else {}
        )
        for metric in list(source_effectiveness.get("metrics") or []):
            if not isinstance(metric, dict):
                continue
            page_ids.extend(self._clean_page_ids([metric.get("page_id")]))
        source_summary = (
            evidence.get("quality_warning_source_summary")
            if isinstance(evidence.get("quality_warning_source_summary"), dict)
            else {}
        )
        source_rows = [
            *list(source_summary.get("top_degraded_sources") or []),
            *list(source_summary.get("top_active_sources") or []),
        ]
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            page_ids.extend(self._clean_page_ids([row.get("page_id")]))
        return list(dict.fromkeys(page_ids))

    def _repair_target_metric_keys(
        self,
        outcomes: list[dict[str, Any]],
    ) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._repair_target_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    keys.add(key)
        return keys

    def _repair_target_page_ids_for_outcome(self, row: dict[str, Any]) -> list[str]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        page = (
            evidence.get("selected_wiki_page")
            if isinstance(evidence.get("selected_wiki_page"), dict)
            else {}
        )
        page_ids: list[str] = []
        targets = [
            *self._selected_page_repair_targets_for_evidence(page),
            *list(evidence.get("repair_targets") or []),
        ]
        for target in targets:
            if not isinstance(target, dict):
                continue
            action = str(target.get("recommended_action") or "").strip()
            if not action:
                action = str(target.get("page_id") or "").strip()
            page_id = self._repair_target_page_id(action)
            if page_id and page_id not in page_ids:
                page_ids.append(page_id)
        return page_ids

    def _usage_guidance_metric_keys(
        self,
        outcomes: list[dict[str, Any]],
    ) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._usage_guidance_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    keys.add(key)
        return keys

    def _usage_guidance_page_ids_for_outcome(self, row: dict[str, Any]) -> list[str]:
        guidance = self._usage_guidance_for_outcome(row)
        if not guidance:
            return []
        page_ids: list[str] = []
        risk_posture = str(guidance.get("risk_posture") or "").strip()
        if risk_posture:
            page_ids.append(
                self._usage_guidance_page_id(
                    category="risk_posture",
                    value=risk_posture,
                )
            )
        for item in list(guidance.get("required_cross_checks") or [])[:8]:
            cross_check = str(item).strip()
            if not cross_check:
                continue
            page_ids.append(
                self._usage_guidance_page_id(
                    category="cross_check",
                    value=cross_check,
                )
            )
        return list(dict.fromkeys(page_ids))

    @staticmethod
    def _usage_guidance_for_outcome(row: dict[str, Any]) -> dict[str, Any]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        guidance = (
            evidence.get("usage_guidance")
            if isinstance(evidence.get("usage_guidance"), dict)
            else {}
        )
        if guidance:
            return dict(guidance)
        selected_page = (
            evidence.get("selected_wiki_page")
            if isinstance(evidence.get("selected_wiki_page"), dict)
            else {}
        )
        guidance = (
            selected_page.get("usage_guidance")
            if isinstance(selected_page.get("usage_guidance"), dict)
            else {}
        )
        return dict(guidance) if guidance else {}

    def _memory_card_quality_metric_keys(
        self,
        outcomes: list[dict[str, Any]],
    ) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._memory_card_quality_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    keys.add(key)
        return keys

    def _memory_card_quality_page_ids_for_outcome(
        self,
        row: dict[str, Any],
    ) -> list[str]:
        quality = self._memory_card_quality_for_outcome(row)
        if not quality:
            return []
        page_ids: list[str] = []
        for field in list(quality.get("missing_fields") or [])[:8]:
            clean_field = str(field).strip()
            if clean_field:
                page_ids.append(
                    self._memory_card_quality_page_id(
                        category="missing_field",
                        value=clean_field,
                    )
                )
        for check in list(quality.get("required_checks") or [])[:8]:
            clean_check = str(check).strip()
            if clean_check:
                page_ids.append(
                    self._memory_card_quality_page_id(
                        category="required_check",
                        value=clean_check,
                    )
                )
        return list(dict.fromkeys(page_ids))

    @staticmethod
    def _memory_card_quality_for_outcome(row: dict[str, Any]) -> dict[str, Any]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        quality = (
            evidence.get("memory_card_quality")
            if isinstance(evidence.get("memory_card_quality"), dict)
            else {}
        )
        if quality:
            return dict(quality)
        selected_page = (
            evidence.get("selected_wiki_page")
            if isinstance(evidence.get("selected_wiki_page"), dict)
            else {}
        )
        quality = (
            selected_page.get("memory_card_quality")
            if isinstance(selected_page.get("memory_card_quality"), dict)
            else {}
        )
        return dict(quality) if quality else {}

    def _stale_effectiveness_metric_keys(
        self,
        *,
        filtered_outcomes: list[dict[str, Any]],
        active_metric_keys: set[tuple[str, str, str, str]],
    ) -> set[tuple[str, str, str, str]]:
        stale_keys: set[tuple[str, str, str, str]] = set()
        for row in filtered_outcomes:
            key = self._selection_outcome_metric_key(row)
            if key[0] and key[1]:
                stale_keys.add(key)
        stale_keys.update(self._quality_warning_metric_keys(filtered_outcomes))
        stale_keys.update(self._quality_warning_source_metric_keys(filtered_outcomes))
        stale_keys.update(self._repair_target_metric_keys(filtered_outcomes))
        stale_keys.update(self._usage_guidance_metric_keys(filtered_outcomes))
        stale_keys.update(self._memory_card_quality_metric_keys(filtered_outcomes))
        return stale_keys - active_metric_keys

    def _delete_page_effectiveness_metrics(
        self,
        keys: set[tuple[str, str, str, str]],
    ) -> int:
        if not keys:
            return 0
        self.wiki.initialize()
        deleted = 0
        with self.wiki._connect() as conn:
            for page_id, scope, venue, horizon in sorted(keys):
                cursor = conn.execute(
                    """
                    DELETE FROM wiki_page_effectiveness
                    WHERE page_id = ?
                      AND decision_scope = ?
                      AND venue = ?
                      AND horizon = ?
                    """,
                    (page_id, scope, venue, horizon),
                )
                deleted += int(cursor.rowcount or 0)
        return deleted

    def _prune_stale_page_effectiveness_metrics(self) -> dict[str, Any]:
        self.wiki.initialize()
        selected_page_backfilled = self.backfill_outcome_selected_page_evidence()
        outcomes = self.list_selection_outcomes(limit=50_000)
        attributable_outcomes = [
            row for row in outcomes if self._outcome_row_is_attributable(row)
        ]
        final_outcomes = [row for row in outcomes if self._is_final_outcome(row)]
        filtered_final_outcomes = [
            row for row in final_outcomes if not self._outcome_row_is_attributable(row)
        ]
        active_metric_keys: set[tuple[str, str, str, str]] = set()
        for row in attributable_outcomes:
            if not self._is_final_outcome(row):
                continue
            key = self._selection_outcome_metric_key(row)
            if key[0] and key[1]:
                active_metric_keys.add(key)
        active_metric_keys.update(self._quality_warning_metric_keys(attributable_outcomes))
        active_metric_keys.update(
            self._quality_warning_source_metric_keys(attributable_outcomes)
        )
        active_metric_keys.update(self._repair_target_metric_keys(attributable_outcomes))
        active_metric_keys.update(self._usage_guidance_metric_keys(attributable_outcomes))
        active_metric_keys.update(
            self._memory_card_quality_metric_keys(attributable_outcomes)
        )
        stale_metric_keys = self._stale_effectiveness_metric_keys(
            filtered_outcomes=filtered_final_outcomes,
            active_metric_keys=active_metric_keys,
        )
        stale_removed = self._delete_page_effectiveness_metrics(stale_metric_keys)
        result: dict[str, Any] = {"status": "ok"}
        if selected_page_backfilled:
            result["selected_page_evidence_backfilled_count"] = selected_page_backfilled
        if stale_removed:
            result["stale_effectiveness_removed_count"] = stale_removed
            result["stale_effectiveness_removed_scopes"] = sorted(
                {
                    scope
                    for _, scope, _, _ in stale_metric_keys
                    if scope
                }
            )
        return result

    def _outcome_row_is_attributable(self, row: dict[str, Any]) -> bool:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        selected_page = (
            evidence.get("selected_wiki_page")
            if isinstance(evidence.get("selected_wiki_page"), dict)
            else {}
        )
        page_type = str(selected_page.get("page_type") or "").strip().lower()
        if page_type and page_type != "symbol":
            return True
        outcome_symbol = ""
        for value in (
            row.get("symbol"),
            evidence.get("symbol"),
            evidence.get("ticker"),
            self._outcome_symbol_from_market_field(evidence.get("market")),
        ):
            outcome_symbol = self._normalize_selected_page_symbol(value)
            if outcome_symbol:
                break
        if not outcome_symbol:
            return True
        page_symbols = self._selected_page_symbols(
            page_id=str(row.get("page_id") or ""),
            page=selected_page,
        )
        return not page_symbols or outcome_symbol in page_symbols

    def backfill_outcome_selected_page_evidence(self, *, limit: int = 50_000) -> int:
        self.wiki.initialize()
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    o.outcome_id, o.link_id, o.page_id, o.evidence_json,
                    l.metadata_json
                FROM wiki_selection_outcomes AS o
                JOIN wiki_decision_links AS l
                  ON l.link_id = o.link_id
                WHERE o.evidence_json NOT LIKE '%selected_wiki_page%'
                   OR (
                        l.metadata_json LIKE '%selected_wiki_pages%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%repair_targets%'
                        AND o.evidence_json NOT LIKE '%repair_targets%'
                   )
                   OR (
                        l.metadata_json LIKE '%repair_targets%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%repair_target_effectiveness%'
                        AND o.evidence_json NOT LIKE '%repair_target_effectiveness%'
                   )
                   OR (
                        l.metadata_json LIKE '%repair_target_effectiveness%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%warning_effectiveness%'
                        AND o.evidence_json NOT LIKE '%quality_warning_effectiveness%'
                   )
                   OR (
                        l.metadata_json LIKE '%warning_effectiveness%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%quality_warning_source_effectiveness%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%usage_guidance%'
                        AND o.evidence_json NOT LIKE '%usage_guidance%'
                   )
                   OR (
                        l.metadata_json LIKE '%usage_guidance%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%jue_wiki_quality_summary%'
                   )
                   OR (
                        l.metadata_json LIKE '%jue_wiki_quality_pressure_action_plan%'
                   )
                   OR (
                        l.metadata_json LIKE '%quality_summary%'
                   )
                   OR (
                        l.metadata_json LIKE '%quality_pressure_action_plan%'
                   )
                   OR (
                        l.metadata_json LIKE '%quality_warning_source_summary%'
                        AND o.evidence_json NOT LIKE '%quality_warning_source_summary%'
                   )
                   OR (
                        l.metadata_json LIKE '%quality_warning_source_summary%'
                        AND o.evidence_json LIKE '%selected_wiki_page%'
                   )
                   OR (
                        l.metadata_json LIKE '%top_active_sources%'
                        AND o.evidence_json NOT LIKE '%top_active_sources%'
                   )
                   OR (
                        l.metadata_json LIKE '%top_active_quality_warning_sources%'
                        AND o.evidence_json NOT LIKE '%top_active_sources%'
                   )
                   OR (
                        l.metadata_json LIKE '%top_degraded_quality_warning_sources%'
                        AND o.evidence_json NOT LIKE '%top_degraded_sources%'
                   )
                   OR (
                        l.metadata_json LIKE '%source_refs%'
                        AND (
                            o.evidence_json NOT LIKE '%repair_targets%'
                            OR o.evidence_json NOT LIKE '%repair_target_effectiveness%'
                            OR o.evidence_json NOT LIKE '%quality_warnings%'
                            OR o.evidence_json NOT LIKE '%quality_warning_effectiveness%'
                        )
                   )
                ORDER BY o.computed_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
            updated = 0
            for row in rows:
                evidence = _json_loads(str(row["evidence_json"]), {})
                if not isinstance(evidence, dict):
                    evidence = {}
                metadata = _json_loads(str(row["metadata_json"]), {})
                if not isinstance(metadata, dict):
                    metadata = {}
                selected_page = self._selected_page_summary_from_metadata(
                    metadata,
                    page_id=str(row["page_id"] or ""),
                )
                if selected_page:
                    evidence = self._outcome_evidence_with_selected_page(
                        evidence,
                        selected_page,
                    )
                evidence = self._outcome_evidence_with_quality_pressure_metadata(
                    evidence,
                    metadata,
                )
                evidence_json = _json_dumps(evidence)
                if evidence_json == str(row["evidence_json"] or ""):
                    continue
                duplicate_rows = conn.execute(
                    """
                    SELECT outcome_id
                    FROM wiki_selection_outcomes
                    WHERE link_id = ?
                      AND page_id = ?
                      AND outcome_kind = (
                          SELECT outcome_kind
                          FROM wiki_selection_outcomes
                          WHERE outcome_id = ?
                      )
                      AND evidence_json = ?
                      AND outcome_id <> ?
                    ORDER BY computed_at DESC, outcome_id DESC
                    """,
                    (
                        str(row["link_id"]),
                        str(row["page_id"]),
                        str(row["outcome_id"]),
                        evidence_json,
                        str(row["outcome_id"]),
                    ),
                ).fetchall()
                if duplicate_rows:
                    keep_outcome_id = str(duplicate_rows[0]["outcome_id"])
                    conn.execute(
                        """
                        DELETE FROM wiki_selection_outcomes
                        WHERE outcome_id = ?
                        """,
                        (str(row["outcome_id"]),),
                    )
                    for duplicate in duplicate_rows[1:]:
                        conn.execute(
                            """
                            DELETE FROM wiki_selection_outcomes
                            WHERE outcome_id = ?
                            """,
                            (str(duplicate["outcome_id"]),),
                        )
                    if keep_outcome_id:
                        updated += 1
                    continue
                conn.execute(
                    """
                    UPDATE wiki_selection_outcomes
                    SET evidence_json = ?
                    WHERE outcome_id = ?
                    """,
                    (evidence_json, str(row["outcome_id"])),
                )
                updated += 1
        return updated

    @staticmethod
    def _selected_page_summary_from_metadata(
        metadata: dict[str, Any],
        *,
        page_id: str,
    ) -> dict[str, Any]:
        summary = (
            metadata.get("selected_wiki_pages")
            if isinstance(metadata.get("selected_wiki_pages"), dict)
            else {}
        )
        rows = summary.get("pages") if isinstance(summary.get("pages"), list) else []
        clean_page_id = str(page_id).strip()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("page_id") or "").strip() == clean_page_id:
                return dict(row)
        return {}

    def _project_quality_warning_effectiveness(
        self,
        *,
        outcomes: list[dict[str, Any]],
        min_samples: int,
    ) -> int:
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._quality_warning_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(row)

        updated = 0
        for (page_id, scope, venue, horizon), rows in groups.items():
            warning = page_id.removeprefix("quality_warning.")
            metric = self._effectiveness_metric(
                page_id=page_id,
                decision_scope=scope,
                venue=venue,
                horizon=horizon,
                rows=rows,
                min_samples=min_samples,
            )
            prior_status_reasons = self._quality_warning_prior_status_reasons(rows)
            metric["reasons"] = [
                *list(metric.get("reasons") or []),
                f"quality_warning:{warning}",
                *prior_status_reasons,
            ]
            self.wiki.upsert_page_effectiveness(metric)
            updated += 1
        return updated

    def _project_quality_warning_source_page_effectiveness(
        self,
        *,
        outcomes: list[dict[str, Any]],
        min_samples: int,
    ) -> int:
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._quality_warning_source_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(row)

        updated = 0
        for (page_id, scope, venue, horizon), rows in groups.items():
            metric = self._effectiveness_metric(
                page_id=page_id,
                decision_scope=scope,
                venue=venue,
                horizon=horizon,
                rows=rows,
                min_samples=min_samples,
            )
            warning_reasons: list[str] = []
            for row in rows:
                evidence = (
                    row.get("evidence")
                    if isinstance(row.get("evidence"), dict)
                    else {}
                )
                for warning in list(evidence.get("quality_warnings") or []):
                    clean_warning = str(warning).strip()
                    if clean_warning:
                        reason = f"quality_warning:{clean_warning}"
                        if reason not in warning_reasons:
                            warning_reasons.append(reason)
            metric["reasons"] = [
                *list(metric.get("reasons") or []),
                "quality_warning_source_page",
                *warning_reasons[:6],
                *self._quality_warning_source_prior_status_reasons(rows),
            ]
            self.wiki.upsert_page_effectiveness(metric)
            updated += 1
        return updated

    @staticmethod
    def _quality_warning_source_prior_status_reasons(
        rows: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> list[str]:
        reasons: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            statuses = list(
                evidence.get("quality_warning_source_effectiveness_statuses") or []
            )
            source_effectiveness = (
                evidence.get("quality_warning_source_effectiveness")
                if isinstance(
                    evidence.get("quality_warning_source_effectiveness"),
                    dict,
                )
                else {}
            )
            if not statuses:
                status = str(source_effectiveness.get("status") or "").strip()
                statuses = [status] if status else []
            if not statuses:
                statuses = [
                    item.get("status")
                    for item in list(source_effectiveness.get("metrics") or [])
                    if isinstance(item, dict)
                ]
            if not statuses:
                source_summary = (
                    evidence.get("quality_warning_source_summary")
                    if isinstance(evidence.get("quality_warning_source_summary"), dict)
                    else {}
                )
                source_rows = [
                    *list(source_summary.get("top_degraded_sources") or []),
                    *list(source_summary.get("top_active_sources") or []),
                ]
                for source_row in source_rows:
                    if not isinstance(source_row, dict):
                        continue
                    statuses.extend(list(source_row.get("prior_statuses") or []))
                    row_status = str(source_row.get("status") or "").strip()
                    if row_status:
                        statuses.append(row_status)
            for status in statuses:
                clean = str(status or "").strip().lower()
                if not clean:
                    continue
                reason = f"quality_warning_source_prior_status:{clean}"
                if reason not in reasons:
                    reasons.append(reason)
                if len(reasons) >= max(int(limit), 0):
                    return reasons
        return reasons

    @staticmethod
    def _quality_warning_prior_status_reasons(
        rows: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> list[str]:
        reasons: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            statuses = list(evidence.get("quality_warning_effectiveness_statuses") or [])
            if not statuses:
                statuses = [
                    item.get("status")
                    for item in list(evidence.get("quality_warning_effectiveness") or [])
                    if isinstance(item, dict)
                ]
            for status in statuses:
                clean = str(status or "").strip().lower()
                if not clean:
                    continue
                reason = f"quality_warning_prior_status:{clean}"
                if reason not in reasons:
                    reasons.append(reason)
                if len(reasons) >= max(int(limit), 0):
                    return reasons
        return reasons

    def _project_repair_target_effectiveness(
        self,
        *,
        outcomes: list[dict[str, Any]],
        min_samples: int,
    ) -> int:
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._repair_target_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(row)

        updated = 0
        for (page_id, scope, venue, horizon), rows in groups.items():
            target = page_id.removeprefix("repair_target.")
            metric = self._effectiveness_metric(
                page_id=page_id,
                decision_scope=scope,
                venue=venue,
                horizon=horizon,
                rows=rows,
                min_samples=min_samples,
            )
            prior_status_reasons = self._repair_target_prior_status_reasons(rows)
            metric["reasons"] = [
                *list(metric.get("reasons") or []),
                f"repair_target:{target}",
                *prior_status_reasons,
            ]
            self.wiki.upsert_page_effectiveness(metric)
            updated += 1
        return updated

    def _project_usage_guidance_effectiveness(
        self,
        *,
        outcomes: list[dict[str, Any]],
        min_samples: int,
    ) -> int:
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._usage_guidance_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(row)

        updated = 0
        for (page_id, scope, venue, horizon), rows in groups.items():
            metric = self._effectiveness_metric(
                page_id=page_id,
                decision_scope=scope,
                venue=venue,
                horizon=horizon,
                rows=rows,
                min_samples=min_samples,
            )
            metric["reasons"] = [
                *list(metric.get("reasons") or []),
                *self._usage_guidance_reasons(
                    page_id=page_id,
                    rows=rows,
                ),
                *self._usage_guidance_prior_status_reasons(rows),
            ]
            self.wiki.upsert_page_effectiveness(metric)
            updated += 1
        return updated

    def _usage_guidance_reasons(
        self,
        *,
        page_id: str,
        rows: list[dict[str, Any]],
        limit: int = 8,
    ) -> list[str]:
        reasons: list[str] = []
        if page_id.startswith("usage_guidance.risk_posture."):
            posture = page_id.removeprefix("usage_guidance.risk_posture.")
            if posture:
                reasons.append(f"usage_guidance:risk_posture:{posture}")
        elif page_id.startswith("usage_guidance.cross_check."):
            cross_check = page_id.removeprefix("usage_guidance.cross_check.")
            if cross_check:
                reasons.append(f"usage_guidance:cross_check:{cross_check}")
        for row in rows:
            guidance = self._usage_guidance_for_outcome(row)
            trust_level = str(guidance.get("trust_level") or "").strip().lower()
            if trust_level:
                reason = f"usage_guidance_trust_level:{trust_level}"
                if reason not in reasons:
                    reasons.append(reason)
            if len(reasons) >= max(int(limit), 0):
                return reasons[:limit]
        return reasons[: max(int(limit), 0)]

    @staticmethod
    def _usage_guidance_prior_status_reasons(
        rows: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> list[str]:
        reasons: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            statuses = list(
                evidence.get("usage_guidance_effectiveness_statuses") or []
            )
            effectiveness = (
                evidence.get("usage_guidance_effectiveness")
                if isinstance(evidence.get("usage_guidance_effectiveness"), dict)
                else {}
            )
            if not statuses:
                status = str(effectiveness.get("status") or "").strip()
                statuses = [status] if status else []
            if not statuses:
                statuses = [
                    item.get("status")
                    for item in list(effectiveness.get("metrics") or [])
                    if isinstance(item, dict)
                ]
            if not statuses:
                selected_page = (
                    evidence.get("selected_wiki_page")
                    if isinstance(evidence.get("selected_wiki_page"), dict)
                    else {}
                )
                selected_effectiveness = (
                    selected_page.get("usage_guidance_effectiveness")
                    if isinstance(
                        selected_page.get("usage_guidance_effectiveness"),
                        dict,
                    )
                    else {}
                )
                status = str(selected_effectiveness.get("status") or "").strip()
                statuses = [status] if status else []
                if not statuses:
                    statuses = [
                        item.get("status")
                        for item in list(selected_effectiveness.get("metrics") or [])
                        if isinstance(item, dict)
                    ]
            for status in statuses:
                clean = str(status or "").strip().lower()
                if not clean:
                    continue
                reason = f"usage_guidance_prior_status:{clean}"
                if reason not in reasons:
                    reasons.append(reason)
                if len(reasons) >= max(int(limit), 0):
                    return reasons
        return reasons

    def _project_memory_card_quality_effectiveness(
        self,
        *,
        outcomes: list[dict[str, Any]],
        min_samples: int,
    ) -> int:
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            if not self._is_final_outcome(row):
                continue
            for page_id in self._memory_card_quality_page_ids_for_outcome(row):
                key = (
                    page_id,
                    str(row.get("decision_scope") or ""),
                    str(row.get("venue") or ""),
                    str(row.get("horizon") or ""),
                )
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(row)

        updated = 0
        for (page_id, scope, venue, horizon), rows in groups.items():
            metric = self._effectiveness_metric(
                page_id=page_id,
                decision_scope=scope,
                venue=venue,
                horizon=horizon,
                rows=rows,
                min_samples=min_samples,
            )
            metric["reasons"] = [
                *list(metric.get("reasons") or []),
                *self._memory_card_quality_reasons(
                    page_id=page_id,
                    rows=rows,
                ),
                *self._memory_card_quality_prior_status_reasons(rows),
            ]
            self.wiki.upsert_page_effectiveness(metric)
            updated += 1
        return updated

    def _memory_card_quality_reasons(
        self,
        *,
        page_id: str,
        rows: list[dict[str, Any]],
        limit: int = 8,
    ) -> list[str]:
        reasons: list[str] = []
        if page_id.startswith("memory_card_quality.missing_field."):
            field = page_id.removeprefix("memory_card_quality.missing_field.")
            if field:
                reasons.append(f"memory_card_quality:missing_field:{field}")
        elif page_id.startswith("memory_card_quality.required_check."):
            check = page_id.removeprefix("memory_card_quality.required_check.")
            if check:
                reasons.append(f"memory_card_quality:required_check:{check}")
        for row in rows:
            quality = self._memory_card_quality_for_outcome(row)
            status = str(quality.get("status") or "").strip().lower()
            if status:
                reason = f"memory_card_quality_status:{status}"
                if reason not in reasons:
                    reasons.append(reason)
            resolution = str(quality.get("resolution") or "").strip().lower()
            if resolution:
                reason = f"memory_card_quality_resolution:{resolution}"
                if reason not in reasons:
                    reasons.append(reason)
            if len(reasons) >= max(int(limit), 0):
                return reasons[:limit]
        return reasons[: max(int(limit), 0)]

    @staticmethod
    def _memory_card_quality_prior_status_reasons(
        rows: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> list[str]:
        reasons: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            statuses = list(
                evidence.get("memory_card_quality_effectiveness_statuses") or []
            )
            effectiveness = (
                evidence.get("memory_card_quality_effectiveness")
                if isinstance(evidence.get("memory_card_quality_effectiveness"), dict)
                else {}
            )
            if not statuses:
                status = str(effectiveness.get("status") or "").strip()
                statuses = [status] if status else []
            if not statuses:
                statuses = [
                    item.get("status")
                    for item in list(effectiveness.get("metrics") or [])
                    if isinstance(item, dict)
                ]
            if not statuses:
                selected_page = (
                    evidence.get("selected_wiki_page")
                    if isinstance(evidence.get("selected_wiki_page"), dict)
                    else {}
                )
                selected_effectiveness = (
                    selected_page.get("memory_card_quality_effectiveness")
                    if isinstance(
                        selected_page.get("memory_card_quality_effectiveness"),
                        dict,
                    )
                    else {}
                )
                status = str(selected_effectiveness.get("status") or "").strip()
                statuses = [status] if status else []
                if not statuses:
                    statuses = [
                        item.get("status")
                        for item in list(selected_effectiveness.get("metrics") or [])
                        if isinstance(item, dict)
                    ]
            for status in statuses:
                clean = str(status or "").strip().lower()
                if not clean:
                    continue
                reason = f"memory_card_quality_prior_status:{clean}"
                if reason not in reasons:
                    reasons.append(reason)
                if len(reasons) >= max(int(limit), 0):
                    return reasons
        return reasons

    @staticmethod
    def _repair_target_prior_status_reasons(
        rows: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> list[str]:
        reasons: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            statuses = list(evidence.get("repair_target_effectiveness_statuses") or [])
            if not statuses:
                statuses = [
                    item.get("status")
                    for item in list(evidence.get("repair_target_effectiveness") or [])
                    if isinstance(item, dict)
                ]
            if not statuses:
                selected_page = (
                    evidence.get("selected_wiki_page")
                    if isinstance(evidence.get("selected_wiki_page"), dict)
                    else {}
                )
                statuses = [
                    item.get("status")
                    for item in (
                        JueWikiApplicationService
                        ._selected_page_repair_target_effectiveness_for_evidence(
                            selected_page
                        )
                    )
                    if isinstance(item, dict)
                ]
            for status in statuses:
                clean = str(status or "").strip().lower()
                if not clean:
                    continue
                reason = f"repair_target_prior_status:{clean}"
                if reason not in reasons:
                    reasons.append(reason)
                if len(reasons) >= max(int(limit), 0):
                    return reasons
        return reasons

    @staticmethod
    def _quality_warning_page_id(warning: Any) -> str:
        clean = str(warning).strip().lower()
        clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in clean)
        clean = "_".join(part for part in clean.split("_") if part)
        return f"quality_warning.{clean or 'unknown'}"

    @staticmethod
    def _repair_target_page_id(target: Any) -> str:
        clean = str(target).strip().lower()
        clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in clean)
        clean = "_".join(part for part in clean.split("_") if part)
        return f"repair_target.{clean or 'unknown'}"

    @staticmethod
    def _usage_guidance_page_id(*, category: str, value: Any) -> str:
        clean_category = str(category or "").strip().lower()
        clean_value = str(value).strip().lower()
        clean_value = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in clean_value
        )
        clean_value = "_".join(part for part in clean_value.split("_") if part)
        return f"usage_guidance.{clean_category or 'unknown'}.{clean_value or 'unknown'}"

    @staticmethod
    def _memory_card_quality_page_id(*, category: str, value: Any) -> str:
        clean_category = str(category or "").strip().lower()
        clean_value = str(value).strip().lower()
        clean_value = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in clean_value
        )
        clean_value = "_".join(part for part in clean_value.split("_") if part)
        return (
            "memory_card_quality."
            f"{clean_category or 'unknown'}."
            f"{clean_value or 'unknown'}"
        )

    def project_selection_outcomes(
        self,
        *,
        kis_db_path: str | Path | None = None,
        binance_db_path: str | Path | None = None,
        market_judgment_db_path: str | Path | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        sources = [
            {
                "scope": "kis",
                "venue": "kis",
                "path": Path(kis_db_path)
                if kis_db_path is not None
                else self.wiki.config.kis_blocks_db_path,
            },
            {
                "scope": "binance",
                "venue": "binance",
                "path": Path(binance_db_path)
                if binance_db_path is not None
                else self.wiki.config.binance_blocks_db_path,
            },
        ]
        projected = 0
        backfilled = 0
        skipped = 0
        warnings: list[str] = []
        errors: list[str] = []
        for source in sources:
            path = source["path"]
            if path is None:
                continue
            path = Path(path)
            if not path.exists():
                warnings.append(f"missing_source_db:{source['scope']}:{path}")
                continue
            links = self.list_decision_links(
                decision_scope=str(source["scope"]),
                limit=limit,
            )
            for link in links:
                if str(link.get("decision_type") or "") != "block_manager":
                    continue
                try:
                    related_block_count = self._block_count_for_decision_link(
                        path=path,
                        scope=str(source["scope"]),
                        link=link,
                    )
                except sqlite3.Error as exc:
                    errors.append(f"{source['scope']}:{path}:block_count:{exc}")
                    break
                try:
                    blocks = self._blocks_for_decision_link(
                        path=path,
                        scope=str(source["scope"]),
                        link=link,
                    )
                except sqlite3.Error as exc:
                    errors.append(f"{source['scope']}:{path}:{exc}")
                    break
                for block in blocks:
                    outcome = self._outcome_from_block(
                        scope=str(source["scope"]),
                        venue=str(source["venue"]),
                        block=block,
                    )
                    if not outcome:
                        skipped += 1
                        continue
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind="closed_block",
                        outcome_status=str(outcome["outcome_status"]),
                        pnl_value=float(outcome["pnl_value"]),
                        pnl_currency=str(outcome["pnl_currency"]),
                        return_pct=float(outcome["return_pct"]),
                        mfe_pct=float(outcome.get("mfe_pct") or 0.0),
                        mae_pct=float(outcome.get("mae_pct") or 0.0),
                        holding_minutes=float(outcome.get("holding_minutes") or 0.0),
                        horizon=str(outcome.get("horizon") or ""),
                        evidence={
                            "source": f"{source['scope']}_blocks",
                            "block_id": block.get("block_id"),
                            "symbol": block.get("symbol"),
                            "manager_run_id": block.get("manager_run_id"),
                            "entry_price": outcome.get("entry_price"),
                            "exit_price": outcome.get("exit_price"),
                            "horizon": outcome.get("horizon"),
                            "status": block.get("status"),
                        },
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
                contract_outcome = self._manager_contract_error_outcome(
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    link=link,
                    related_block_count=related_block_count,
                )
                if contract_outcome:
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(contract_outcome["outcome_kind"]),
                        outcome_status=str(contract_outcome["outcome_status"]),
                        pnl_value=0.0,
                        pnl_currency=str(contract_outcome["pnl_currency"]),
                        return_pct=float(contract_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=contract_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
                    continue
                pressure_outcome = self._manager_pressure_outcome(
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    link=link,
                    related_block_count=related_block_count,
                )
                if pressure_outcome:
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(pressure_outcome["outcome_kind"]),
                        outcome_status=str(pressure_outcome["outcome_status"]),
                        pnl_value=0.0,
                        pnl_currency=str(pressure_outcome["pnl_currency"]),
                        return_pct=float(pressure_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=pressure_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
                wiki_pressure_outcome = self._manager_wiki_action_pressure_outcome(
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    link=link,
                    related_block_count=related_block_count,
                )
                if wiki_pressure_outcome:
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(wiki_pressure_outcome["outcome_kind"]),
                        outcome_status=str(wiki_pressure_outcome["outcome_status"]),
                        pnl_value=0.0,
                        pnl_currency=str(wiki_pressure_outcome["pnl_currency"]),
                        return_pct=float(wiki_pressure_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=wiki_pressure_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
                quality_pressure_outcome = (
                    self._manager_wiki_quality_pressure_outcome(
                        scope=str(source["scope"]),
                        venue=str(source["venue"]),
                        link=link,
                        related_block_count=related_block_count,
                    )
                )
                if quality_pressure_outcome:
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(quality_pressure_outcome["outcome_kind"]),
                        outcome_status=str(
                            quality_pressure_outcome["outcome_status"]
                        ),
                        pnl_value=0.0,
                        pnl_currency=str(quality_pressure_outcome["pnl_currency"]),
                        return_pct=float(quality_pressure_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=quality_pressure_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
                repair_outcome = self._manager_repair_outcome(
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    link=link,
                    related_block_count=related_block_count,
                )
                if repair_outcome:
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(repair_outcome["outcome_kind"]),
                        outcome_status=str(repair_outcome["outcome_status"]),
                        pnl_value=0.0,
                        pnl_currency=str(repair_outcome["pnl_currency"]),
                        return_pct=float(repair_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=repair_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
                validation_outcome = self._manager_validation_repair_outcome(
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    link=link,
                    related_block_count=related_block_count,
                )
                if validation_outcome:
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(validation_outcome["outcome_kind"]),
                        outcome_status=str(validation_outcome["outcome_status"]),
                        pnl_value=0.0,
                        pnl_currency=str(validation_outcome["pnl_currency"]),
                        return_pct=float(validation_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=validation_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
            try:
                backfill = self._project_closed_block_backfill_outcomes(
                    path=path,
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    limit=limit,
                )
            except sqlite3.Error as exc:
                errors.append(f"{source['scope']}:{path}:backfill:{exc}")
                continue
            backfilled += int(backfill.get("projected_count") or 0)
            skipped += int(backfill.get("skipped_count") or 0)
        market_path = Path(market_judgment_db_path) if market_judgment_db_path is not None else None
        if market_path is not None:
            if not market_path.exists():
                warnings.append(f"missing_source_db:market_judgment:{market_path}")
            else:
                links = self.list_decision_links(decision_scope="kis", limit=limit)
                for link in links:
                    if str(link.get("decision_type") or "") != "market_judgment":
                        continue
                    contract_outcome = self._manager_contract_error_outcome(
                        scope="kis",
                        venue="kis",
                        link=link,
                        related_block_count=0,
                    )
                    if contract_outcome:
                        result = self.record_selection_outcomes(
                            link_id=str(link["link_id"]),
                            outcome_kind=str(contract_outcome["outcome_kind"]),
                            outcome_status=str(contract_outcome["outcome_status"]),
                            pnl_value=0.0,
                            pnl_currency=str(contract_outcome["pnl_currency"]),
                            return_pct=float(contract_outcome["return_pct"]),
                            mfe_pct=0.0,
                            mae_pct=0.0,
                            holding_minutes=0.0,
                            evidence=contract_outcome["evidence"],
                        )
                        if result.get("status") == "ok":
                            projected += int(result.get("outcome_count") or 0)
                        continue
                    validation_outcome = self._manager_validation_repair_outcome(
                        scope="kis",
                        venue="kis",
                        link=link,
                        related_block_count=0,
                    )
                    if not validation_outcome:
                        skipped += 1
                        continue
                    result = self.record_selection_outcomes(
                        link_id=str(link["link_id"]),
                        outcome_kind=str(validation_outcome["outcome_kind"]),
                        outcome_status=str(validation_outcome["outcome_status"]),
                        pnl_value=0.0,
                        pnl_currency=str(validation_outcome["pnl_currency"]),
                        return_pct=float(validation_outcome["return_pct"]),
                        mfe_pct=0.0,
                        mae_pct=0.0,
                        holding_minutes=0.0,
                        evidence=validation_outcome["evidence"],
                    )
                    if result.get("status") == "ok":
                        projected += int(result.get("outcome_count") or 0)
        return {
            "status": "error" if errors else "ok",
            "projected_count": projected,
            "backfilled_count": backfilled,
            "skipped_count": skipped,
            "warnings": warnings,
            "errors": errors,
            "error_message": "; ".join(errors),
        }

    def project_decision_links(
        self,
        *,
        kis_db_path: str | Path | None = None,
        binance_db_path: str | Path | None = None,
        market_judgment_db_path: str | Path | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        sources = [
            {
                "scope": "kis",
                "venue": "kis",
                "decision_type": "block_manager",
                "path": Path(kis_db_path)
                if kis_db_path is not None
                else self.wiki.config.kis_blocks_db_path,
                "table": "manager_runs",
            },
            {
                "scope": "binance",
                "venue": "binance",
                "decision_type": "block_manager",
                "path": Path(binance_db_path)
                if binance_db_path is not None
                else self.wiki.config.binance_blocks_db_path,
                "table": "manager_runs",
            },
            {
                "scope": "kis",
                "venue": "kis",
                "decision_type": "market_judgment",
                "path": Path(market_judgment_db_path)
                if market_judgment_db_path is not None
                else None,
                "table": "judgment_runs",
            },
        ]
        scanned = 0
        inserted = 0
        warnings: list[str] = []
        errors: list[str] = []
        for source in sources:
            path = source["path"]
            if path is None:
                continue
            path = Path(path)
            if not path.exists():
                warnings.append(f"missing_source_db:{source['scope']}:{path}")
                continue
            try:
                result = self._project_decision_links_from_db(
                    path=path,
                    scope=str(source["scope"]),
                    venue=str(source["venue"]),
                    decision_type=str(source["decision_type"]),
                    table=str(source["table"]),
                    limit=limit,
                )
            except sqlite3.Error as exc:
                errors.append(f"{source['scope']}:{path}:{exc}")
                continue
            scanned += int(result.get("scanned_count") or 0)
            inserted += int(result.get("inserted_count") or 0)
        return {
            "status": "error" if errors else "ok",
            "scanned_count": scanned,
            "inserted_count": inserted,
            "warnings": warnings,
            "errors": errors,
            "error_message": "; ".join(errors),
        }

    def page_effectiveness(
        self,
        *,
        page_id: str,
        decision_scope: str,
        venue: str = "",
        horizon: str = "",
        refresh: bool = True,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        if refresh:
            self._prune_stale_page_effectiveness_metrics()
        clean_horizon = str(horizon).strip().lower()
        fallback_reason = ""
        requested_horizon = clean_horizon
        with self.wiki._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wiki_page_effectiveness
                WHERE page_id = ? AND decision_scope = ? AND venue = ? AND horizon = ?
                """,
                (
                    str(page_id),
                    str(decision_scope).strip().lower(),
                    str(venue).strip().lower(),
                    clean_horizon,
                ),
            ).fetchone()
            if row is None and clean_horizon:
                row = conn.execute(
                    """
                    SELECT *
                    FROM wiki_page_effectiveness
                    WHERE page_id = ? AND decision_scope = ? AND venue = ?
                      AND horizon = ''
                    """,
                    (
                        str(page_id),
                        str(decision_scope).strip().lower(),
                        str(venue).strip().lower(),
                    ),
                ).fetchone()
                if row is not None:
                    fallback_reason = "general_horizon_metric"
            if row is None and not clean_horizon:
                fallback_rows = conn.execute(
                    """
                    SELECT *
                    FROM wiki_page_effectiveness
                    WHERE page_id = ? AND decision_scope = ? AND venue = ?
                    ORDER BY updated_at DESC, helpful_score DESC, horizon ASC
                    LIMIT 2
                    """,
                    (
                        str(page_id),
                        str(decision_scope).strip().lower(),
                        str(venue).strip().lower(),
                    ),
                ).fetchall()
                if len(fallback_rows) == 1:
                    row = fallback_rows[0]
        if row is None:
            return {"status": "missing", "page_id": page_id}
        result = self._effectiveness_from_row(row)
        if fallback_reason:
            result["requested_horizon"] = requested_horizon
            result["fallback_reason"] = fallback_reason
        return result

    def list_page_effectiveness(
        self,
        *,
        decision_scope: str = "",
        limit: int = 100,
        refresh: bool = True,
    ) -> list[dict[str, Any]]:
        self.wiki.initialize()
        if refresh:
            self._prune_stale_page_effectiveness_metrics()
        clean_scope = str(decision_scope or "").strip().lower()
        params: list[Any] = []
        where = ""
        if clean_scope:
            where = "WHERE decision_scope = ?"
            params.append(clean_scope)
        params.append(max(int(limit), 1))
        with self.wiki._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_page_effectiveness
                {where}
                ORDER BY updated_at DESC, helpful_score DESC, page_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._effectiveness_from_row(row) for row in rows]

    def project_wiki_mode_recommendations(self) -> dict[str, Any]:
        recommendations: list[dict[str, Any]] = []
        for venue in ("binance", "kis"):
            if self.shadow_eligibility_reader is None:
                result: dict[str, Any] = {
                    "venue": venue,
                    "required_eligible": False,
                    "complete_sample_count": 0,
                    "blockers": ["eligibility_unavailable"],
                    "reason": "eligibility_unavailable",
                }
            else:
                try:
                    result = self.shadow_eligibility_reader.eligibility(venue)
                except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                    result = {
                        "venue": venue,
                        "required_eligible": False,
                        "complete_sample_count": 0,
                        "blockers": ["eligibility_unavailable"],
                        "reason": "eligibility_unavailable",
                    }
            invalid = not isinstance(result, dict)
            if invalid:
                result = {}
            sample_value = result.get("complete_sample_count")
            if type(sample_value) is not int or sample_value < 0:
                invalid = True
                sample_count = 0
            else:
                sample_count = sample_value
            blocker_value = result.get("blockers")
            if not isinstance(blocker_value, list):
                invalid = True
                blocker_value = []
            if type(result.get("required_eligible")) is not bool:
                invalid = True
            version = str(result.get("version") or "")
            if version != "wiki_shadow_eligibility_v1":
                invalid = True
            result_venue = str(result.get("venue") or "").strip().lower()
            if invalid:
                blockers = [
                    "eligibility_version_invalid"
                    if version != "wiki_shadow_eligibility_v1"
                    else "eligibility_invalid"
                ]
            else:
                blockers = sorted(
                    str(value)
                    for value in blocker_value
                    if str(value)
                )
            if result_venue != venue:
                blockers.append("eligibility_venue_mismatch")
            if not invalid and not blockers:
                freshness_reason = wiki_eligibility_freshness_reason(
                    result,
                    now=datetime.now(timezone.utc),
                )
                if freshness_reason:
                    blockers.append(freshness_reason)
            eligible = (
                not invalid
                and result_venue == venue
                and result.get("required_eligible") is True
                and sample_count >= 500
                and not blockers
            )
            recommendations.append(
                {
                    "version": version,
                    "venue": venue,
                    "recommended_mode": "required_eligible" if eligible else "prefer",
                    "required_eligible": eligible,
                    "sample_count": sample_count,
                    "blockers": sorted(set(blockers)),
                    "reason": blockers[0]
                    if blockers
                    else str(result.get("reason") or "required_acceptance_gates_passed"),
                    "evaluated_at": str(result.get("evaluated_at") or ""),
                    "evaluated_through": str(
                        result.get("evaluated_through") or ""
                    ),
                }
            )
        return {
            "status": "ok",
            "read_only": True,
            "recommendations": recommendations,
        }

    def project_mode_recommendations(
        self,
        *,
        min_samples: int = 20,
        current_modes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        effectiveness_projection = self.project_page_effectiveness()
        metrics = self.list_page_effectiveness(limit=50_000, refresh=False)
        prompt_mode_effectiveness = self.project_prompt_mode_effectiveness(
            min_samples=min_samples
        )
        prompt_mode_effectiveness_by_scope_mode = (
            self._prompt_mode_effectiveness_by_scope_mode(prompt_mode_effectiveness)
        )
        trust_profile_effectiveness = self.project_trust_profile_effectiveness(
            min_samples=min_samples
        )
        trust_profile_effectiveness_by_scope_authority = (
            self._trust_profile_effectiveness_by_scope_authority(
                trust_profile_effectiveness
            )
        )
        by_scope: dict[str, list[dict[str, Any]]] = {}
        for metric in metrics:
            scope = str(metric.get("decision_scope") or "")
            if scope:
                by_scope.setdefault(scope, []).append(metric)
        stale_scopes = {
            str(scope)
            for scope in effectiveness_projection.get(
                "stale_effectiveness_removed_scopes",
                [],
            )
            if str(scope)
        }

        recommendations: list[dict[str, Any]] = []
        now = _utc_now_iso()
        deleted_recommendation_scopes: set[str] = set()
        with self.wiki._connect() as conn:
            for scope, rows in sorted(by_scope.items()):
                if scope in stale_scopes:
                    self._delete_mode_recommendations_for_scope(conn, scope=scope)
                    deleted_recommendation_scopes.add(scope)
                sample_count = sum(int(row.get("sample_count") or 0) for row in rows)
                active_count = sum(1 for row in rows if row.get("status") == "active")
                degraded_count = sum(
                    1 for row in rows if row.get("status") == "degraded"
                )
                avg_helpful = (
                    sum(float(row.get("helpful_score") or 0.0) for row in rows)
                    / len(rows)
                    if rows
                    else 0.0
                )
                current = str((current_modes or {}).get(scope) or "")
                if sample_count < int(min_samples):
                    mode = "observe"
                    confidence = min(sample_count / max(int(min_samples), 1), 1.0)
                elif degraded_count > active_count and active_count <= 0:
                    mode = "observe"
                    confidence = 0.65
                elif avg_helpful > 4.0 and active_count >= max(degraded_count * 2, 1):
                    mode = "primary"
                    confidence = 0.75
                else:
                    mode = "assist"
                    confidence = 0.65
                reasons = [
                    f"samples:{sample_count}",
                    f"active:{active_count}",
                    f"degraded:{degraded_count}",
                    f"avg_helpful:{avg_helpful:.4f}",
                ]
                mode, confidence, reasons = (
                    self._adjust_mode_recommendation_with_prompt_mode_effectiveness(
                        scope=scope,
                        mode=mode,
                        confidence=confidence,
                        reasons=reasons,
                        prompt_mode_effectiveness_by_scope_mode=(
                            prompt_mode_effectiveness_by_scope_mode
                        ),
                    )
                )
                mode, confidence, reasons = (
                    self._adjust_mode_recommendation_with_trust_profile_effectiveness(
                        scope=scope,
                        mode=mode,
                        confidence=confidence,
                        reasons=reasons,
                        trust_profile_effectiveness_by_scope_authority=(
                            trust_profile_effectiveness_by_scope_authority
                        ),
                    )
                )
                recommendations.append(
                    self._insert_or_reuse_mode_recommendation(
                        conn,
                        scope=scope,
                        mode=mode,
                        current_mode=current,
                        sample_count=sample_count,
                        confidence=confidence,
                        reasons=reasons,
                        created_at=now,
                    )
                )
            stale_scopes = {
                str(scope)
                for scope in effectiveness_projection.get(
                    "stale_effectiveness_removed_scopes",
                    [],
                )
                if str(scope)
            }
            recommendation_scopes = {
                str(row.get("decision_scope") or "") for row in recommendations
            }
            stale_removed = int(
                effectiveness_projection.get("stale_effectiveness_removed_count") or 0
            )
            for scope in sorted(stale_scopes - recommendation_scopes):
                if scope not in deleted_recommendation_scopes:
                    self._delete_mode_recommendations_for_scope(conn, scope=scope)
                current = str((current_modes or {}).get(scope) or "")
                remaining_rows = [
                    row
                    for row in self.list_page_effectiveness(
                        decision_scope=scope,
                        limit=50_000,
                        refresh=False,
                    )
                ]
                recommendations.append(
                    self._record_mode_recommendation_for_scope(
                        conn,
                        scope=scope,
                        rows=remaining_rows,
                        current_mode=current,
                        min_samples=min_samples,
                        created_at=now,
                        prompt_mode_effectiveness_by_scope_mode=(
                            prompt_mode_effectiveness_by_scope_mode
                        ),
                        trust_profile_effectiveness_by_scope_authority=(
                            trust_profile_effectiveness_by_scope_authority
                        ),
                        fallback_reasons=[
                            f"stale_effectiveness_removed:{stale_removed}",
                            "no_attributable_metrics_after_cleanup",
                        ],
                    )
                )
        result: dict[str, Any] = {"status": "ok", "recommendations": recommendations}
        stale_removed = int(
            effectiveness_projection.get("stale_effectiveness_removed_count") or 0
        )
        if stale_removed:
            result["stale_effectiveness_removed_count"] = stale_removed
        if prompt_mode_effectiveness.get("mode_count"):
            result["prompt_mode_effectiveness"] = prompt_mode_effectiveness
        if trust_profile_effectiveness.get("trust_profile_count"):
            result["trust_profile_effectiveness"] = trust_profile_effectiveness
        return result

    def _record_stale_effectiveness_observe_recommendation(
        self,
        conn: sqlite3.Connection,
        *,
        scope: str,
        current_mode: str = "",
        stale_removed: int = 0,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        now = str(created_at or _utc_now_iso())
        recommendation_id = f"wiki-mode:{uuid.uuid4().hex}"
        reasons = [
            f"stale_effectiveness_removed:{int(stale_removed)}",
            "no_attributable_metrics_after_cleanup",
        ]
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type,
                horizon, recommended_mode, current_mode, sample_count,
                confidence, reasons_json, metric_presence_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recommendation_id,
                scope,
                "observe",
                current_mode,
                0,
                0.65,
                _json_dumps(reasons),
                _json_dumps(
                    _metric_presence_for(
                        {"sample_count": 0, "confidence": 0.65},
                        ("sample_count", "confidence"),
                    )
                ),
                now,
            ),
        )
        return {
            "recommendation_id": recommendation_id,
            "decision_scope": scope,
            "recommended_mode": "observe",
            "current_mode": current_mode,
            "sample_count": 0,
            "confidence": 0.65,
            "reasons": reasons,
            "created_at": now,
        }

    def _record_mode_recommendation_for_scope(
        self,
        conn: sqlite3.Connection,
        *,
        scope: str,
        rows: list[dict[str, Any]],
        current_mode: str = "",
        min_samples: int = 20,
        created_at: str | None = None,
        fallback_reasons: list[str] | None = None,
        prompt_mode_effectiveness_by_scope_mode: dict[
            tuple[str, str], dict[str, Any]
        ] | None = None,
        trust_profile_effectiveness_by_scope_authority: dict[
            tuple[str, str], dict[str, Any]
        ] | None = None,
    ) -> dict[str, Any]:
        now = str(created_at or _utc_now_iso())
        sample_count = sum(int(row.get("sample_count") or 0) for row in rows)
        active_count = sum(1 for row in rows if row.get("status") == "active")
        degraded_count = sum(1 for row in rows if row.get("status") == "degraded")
        avg_helpful = (
            sum(float(row.get("helpful_score") or 0.0) for row in rows) / len(rows)
            if rows
            else 0.0
        )
        if not rows:
            mode = "observe"
            confidence = 0.65
            reasons = list(fallback_reasons or ["no_attributable_metrics"])
        else:
            if sample_count < int(min_samples):
                mode = "observe"
                confidence = min(sample_count / max(int(min_samples), 1), 1.0)
            elif degraded_count > active_count and active_count <= 0:
                mode = "observe"
                confidence = 0.65
            elif avg_helpful > 4.0 and active_count >= max(degraded_count * 2, 1):
                mode = "primary"
                confidence = 0.75
            else:
                mode = "assist"
                confidence = 0.65
            reasons = [
                f"samples:{sample_count}",
                f"active:{active_count}",
                f"degraded:{degraded_count}",
                f"avg_helpful:{avg_helpful:.4f}",
            ]
        mode, confidence, reasons = (
            self._adjust_mode_recommendation_with_prompt_mode_effectiveness(
                scope=scope,
                mode=mode,
                confidence=confidence,
                reasons=reasons,
                prompt_mode_effectiveness_by_scope_mode=(
                    prompt_mode_effectiveness_by_scope_mode or {}
                ),
            )
        )
        mode, confidence, reasons = (
            self._adjust_mode_recommendation_with_trust_profile_effectiveness(
                scope=scope,
                mode=mode,
                confidence=confidence,
                reasons=reasons,
                trust_profile_effectiveness_by_scope_authority=(
                    trust_profile_effectiveness_by_scope_authority or {}
                ),
            )
        )
        return self._insert_or_reuse_mode_recommendation(
            conn,
            scope=scope,
            mode=mode,
            current_mode=current_mode,
            sample_count=sample_count,
            confidence=confidence,
            reasons=reasons,
            created_at=now,
        )

    def _insert_or_reuse_mode_recommendation(
        self,
        conn: sqlite3.Connection,
        *,
        scope: str,
        mode: str,
        current_mode: str,
        sample_count: int,
        confidence: float,
        reasons: list[str],
        created_at: str,
    ) -> dict[str, Any]:
        clean_scope = str(scope or "").strip().lower()
        clean_mode = str(mode or "").strip().lower()
        clean_current = str(current_mode or "").strip().lower()
        clean_reasons = [str(reason) for reason in reasons if str(reason).strip()]
        row = conn.execute(
            """
            SELECT *
            FROM wiki_mode_recommendations
            WHERE decision_scope = ?
            ORDER BY created_at DESC, recommendation_id DESC
            LIMIT 1
            """,
            (clean_scope,),
        ).fetchone()
        if row is not None:
            existing = self._mode_recommendation_from_row(row)
            if (
                str(existing.get("recommended_mode") or "") == clean_mode
                and str(existing.get("current_mode") or "") == clean_current
                and int(existing.get("sample_count") or 0) == int(sample_count)
                and abs(float(existing.get("confidence") or 0.0) - float(confidence))
                < 1e-9
                and list(existing.get("reasons") or []) == clean_reasons
            ):
                return {
                    "recommendation_id": str(existing.get("recommendation_id") or ""),
                    "decision_scope": clean_scope,
                    "recommended_mode": clean_mode,
                    "current_mode": clean_current,
                    "sample_count": int(sample_count),
                    "confidence": float(confidence),
                    "reasons": clean_reasons,
                    "created_at": str(existing.get("created_at") or ""),
                    "reused": True,
                }
        recommendation_id = f"wiki-mode:{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type,
                horizon, recommended_mode, current_mode, sample_count,
                confidence, reasons_json, metric_presence_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recommendation_id,
                clean_scope,
                clean_mode,
                clean_current,
                int(sample_count),
                float(confidence),
                _json_dumps(clean_reasons),
                _json_dumps(
                    _metric_presence_for(
                        {
                            "sample_count": int(sample_count),
                            "confidence": float(confidence),
                        },
                        ("sample_count", "confidence"),
                    )
                ),
                str(created_at),
            ),
        )
        return {
            "recommendation_id": recommendation_id,
            "decision_scope": clean_scope,
            "recommended_mode": clean_mode,
            "current_mode": clean_current,
            "sample_count": int(sample_count),
            "confidence": float(confidence),
            "reasons": clean_reasons,
            "created_at": str(created_at),
            "reused": False,
        }

    @staticmethod
    def _prompt_mode_effectiveness_by_scope_mode(
        payload: dict[str, Any],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        rows = payload.get("modes") if isinstance(payload.get("modes"), list) else []
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("decision_scope") or "").strip().lower()
            mode = str(row.get("prompt_mode") or "").strip().lower()
            if scope and mode:
                out[(scope, mode)] = row
        return out

    @staticmethod
    def _trust_profile_effectiveness_by_scope_authority(
        payload: dict[str, Any],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        rows = (
            payload.get("trust_profiles")
            if isinstance(payload.get("trust_profiles"), list)
            else []
        )
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("decision_scope") or "").strip().lower()
            authority = str(row.get("authority") or "").strip().lower()
            if scope and authority:
                out[(scope, authority)] = row
        return out

    @staticmethod
    def _adjust_mode_recommendation_with_prompt_mode_effectiveness(
        *,
        scope: str,
        mode: str,
        confidence: float,
        reasons: list[str],
        prompt_mode_effectiveness_by_scope_mode: dict[
            tuple[str, str], dict[str, Any]
        ],
    ) -> tuple[str, float, list[str]]:
        clean_scope = str(scope or "").strip().lower()
        clean_mode = str(mode or "").strip().lower()
        adjusted_reasons = list(reasons)
        primary_effectiveness = prompt_mode_effectiveness_by_scope_mode.get(
            (clean_scope, "primary"),
            {},
        )
        if clean_mode == "primary" and primary_effectiveness:
            primary_status = str(primary_effectiveness.get("status") or "").lower()
            primary_confidence = float(primary_effectiveness.get("confidence") or 0.0)
            if primary_status == "degraded" and primary_confidence >= 0.5:
                adjusted_reasons.extend(
                    JueWikiApplicationService._prompt_mode_degraded_reasons(
                        primary_effectiveness
                    )
                )
                return "assist", min(float(confidence), 0.65), adjusted_reasons
        if clean_mode == "assist" and primary_effectiveness:
            primary_status = str(primary_effectiveness.get("status") or "").lower()
            primary_confidence = float(primary_effectiveness.get("confidence") or 0.0)
            if primary_status == "degraded" and primary_confidence >= 0.5:
                adjusted_reasons.extend(
                    reason
                    for reason in JueWikiApplicationService._prompt_mode_degraded_reasons(
                        primary_effectiveness
                    )
                    if reason not in adjusted_reasons
                )
        return clean_mode, float(confidence), adjusted_reasons

    @staticmethod
    def _prompt_mode_degraded_reasons(
        primary_effectiveness: dict[str, Any],
    ) -> list[str]:
        return [
            "prompt_mode_effectiveness:primary:degraded",
            "primary_avg_return_pct:"
            f"{float(primary_effectiveness.get('avg_return_pct') or 0.0):.4f}",
        ]

    @staticmethod
    def _adjust_mode_recommendation_with_trust_profile_effectiveness(
        *,
        scope: str,
        mode: str,
        confidence: float,
        reasons: list[str],
        trust_profile_effectiveness_by_scope_authority: dict[
            tuple[str, str], dict[str, Any]
        ],
    ) -> tuple[str, float, list[str]]:
        clean_scope = str(scope or "").strip().lower()
        clean_mode = str(mode or "").strip().lower()
        adjusted_reasons = list(reasons)
        primary_trust_effectiveness = (
            trust_profile_effectiveness_by_scope_authority.get(
                (clean_scope, "primary_compiled_knowledge"),
                {},
            )
        )
        if not primary_trust_effectiveness:
            return clean_mode, float(confidence), adjusted_reasons
        primary_status = str(primary_trust_effectiveness.get("status") or "").lower()
        primary_confidence = float(
            primary_trust_effectiveness.get("confidence") or 0.0
        )
        if primary_status != "degraded" or primary_confidence < 0.5:
            return clean_mode, float(confidence), adjusted_reasons
        for reason in JueWikiApplicationService._trust_profile_degraded_reasons(
            primary_trust_effectiveness
        ):
            if reason not in adjusted_reasons:
                adjusted_reasons.append(reason)
        if clean_mode == "primary":
            return "assist", min(float(confidence), 0.65), adjusted_reasons
        return clean_mode, float(confidence), adjusted_reasons

    @staticmethod
    def _trust_profile_degraded_reasons(
        primary_trust_effectiveness: dict[str, Any],
    ) -> list[str]:
        return [
            "trust_profile_effectiveness:primary_compiled_knowledge:degraded",
            "primary_trust_avg_return_pct:"
            f"{float(primary_trust_effectiveness.get('avg_return_pct') or 0.0):.4f}",
        ]

    @staticmethod
    def _delete_mode_recommendations_for_scope(
        conn: sqlite3.Connection,
        *,
        scope: str,
    ) -> int:
        cursor = conn.execute(
            """
            DELETE FROM wiki_mode_recommendations
            WHERE decision_scope = ?
            """,
            (scope,),
        )
        return int(cursor.rowcount or 0)

    def latest_mode_recommendation(self, *, refresh: bool = True) -> dict[str, Any]:
        self.wiki.initialize()
        if refresh:
            self.project_mode_recommendations()
        with self.wiki._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wiki_mode_recommendations
                ORDER BY created_at DESC, recommendation_id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {}
        return self._mode_recommendation_from_row(row)

    def list_mode_recommendations(
        self,
        *,
        limit: int = 20,
        refresh: bool = True,
    ) -> list[dict[str, Any]]:
        self.wiki.initialize()
        if refresh:
            self.project_mode_recommendations()
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_mode_recommendations
                ORDER BY created_at DESC, recommendation_id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._mode_recommendation_from_row(row) for row in rows]

    def _refresh_stale_mode_recommendations(self) -> dict[str, Any]:
        projection = self._prune_stale_page_effectiveness_metrics()
        stale_scopes = [
            str(scope)
            for scope in projection.get("stale_effectiveness_removed_scopes", [])
            if str(scope)
        ]
        if stale_scopes:
            stale_removed = int(projection.get("stale_effectiveness_removed_count") or 0)
            now = _utc_now_iso()
            with self.wiki._connect() as conn:
                for scope in sorted(set(stale_scopes)):
                    self._delete_mode_recommendations_for_scope(conn, scope=scope)
                    remaining_rows = self.list_page_effectiveness(
                        decision_scope=scope,
                        limit=50_000,
                        refresh=False,
                    )
                    self._record_mode_recommendation_for_scope(
                        conn,
                        scope=scope,
                        rows=remaining_rows,
                        created_at=now,
                        fallback_reasons=[
                            f"stale_effectiveness_removed:{stale_removed}",
                            "no_attributable_metrics_after_cleanup",
                        ],
                    )
        return projection

    def status(self) -> dict[str, Any]:
        snapshot = read_ops_section_snapshot(
            self.wiki.config.db_path,
            section=self.OPS_SNAPSHOT_SECTION,
        )
        if snapshot is None:
            return {
                "status": "unavailable",
                "snapshot_version": OPS_SECTION_SNAPSHOT_VERSION,
                "snapshot_section": self.OPS_SNAPSHOT_SECTION,
                "reason": "ops_snapshot_missing",
            }
        return dict(snapshot.payload)

    def project_status_snapshot(self) -> dict[str, Any]:
        effectiveness_projection = self.project_page_effectiveness()
        self.project_mode_recommendations()
        cleanup_payload = {
            key: value
            for key, value in effectiveness_projection.items()
            if key != "status" and value not in (None, "", [], {}, 0)
        }
        stale_scopes = [
            str(scope)
            for scope in effectiveness_projection.get(
                "stale_effectiveness_removed_scopes",
                [],
            )
            if str(scope)
        ]
        if stale_scopes:
            stale_removed = int(
                effectiveness_projection.get("stale_effectiveness_removed_count") or 0
            )
            now = _utc_now_iso()
            with self.wiki._connect() as conn:
                for scope in sorted(set(stale_scopes)):
                    self._delete_mode_recommendations_for_scope(conn, scope=scope)
                    remaining_rows = self.list_page_effectiveness(
                        decision_scope=scope,
                        limit=50_000,
                        refresh=False,
                    )
                    self._record_mode_recommendation_for_scope(
                        conn,
                        scope=scope,
                        rows=remaining_rows,
                        created_at=now,
                        fallback_reasons=[
                            f"stale_effectiveness_removed:{stale_removed}",
                            "no_attributable_metrics_after_cleanup",
                        ],
                    )
        effectiveness = self.list_page_effectiveness(limit=1_000, refresh=False)
        degraded_count = sum(
            1 for row in effectiveness if str(row.get("status") or "") == "degraded"
        )
        warning_effectiveness = [
            row
            for row in effectiveness
            if str(row.get("page_id") or "").startswith("quality_warning.")
        ]
        source_warning_effectiveness = [
            row
            for row in effectiveness
            if "quality_warning_source_page"
            in {str(item).strip() for item in list(row.get("reasons") or [])}
        ]
        degraded_warnings = [
            row
            for row in warning_effectiveness
            if str(row.get("status") or "") == "degraded"
        ]
        degraded_source_warnings = [
            row
            for row in source_warning_effectiveness
            if str(row.get("status") or "") == "degraded"
        ]
        active_source_warnings = [
            row
            for row in source_warning_effectiveness
            if str(row.get("status") or "") == "active"
        ]
        prompt_mode_effectiveness = self.project_prompt_mode_effectiveness()
        trust_profile_effectiveness = self.project_trust_profile_effectiveness()
        status_payload = {
            "status": "ok",
            "effectiveness_count": len(effectiveness),
            "degraded_count": degraded_count,
            "quality_warning_effectiveness_count": len(warning_effectiveness),
            "quality_warning_degraded_count": len(degraded_warnings),
            "quality_warning_source_effectiveness_count": len(
                source_warning_effectiveness
            ),
            "quality_warning_source_degraded_count": len(degraded_source_warnings),
            "quality_warning_source_active_count": len(active_source_warnings),
            "top_degraded_quality_warnings": self._quality_warning_status_rows(
                degraded_warnings,
                limit=8,
            ),
            "top_degraded_quality_warning_sources": (
                self._quality_warning_source_status_rows(
                    degraded_source_warnings,
                    limit=8,
                )
            ),
            "top_active_quality_warning_sources": (
                self._quality_warning_source_status_rows(
                    active_source_warnings,
                    limit=8,
                    strongest_first=True,
                )
            ),
            **self._wiki_application_status(),
            "latest_recommendation": self.latest_mode_recommendation(refresh=False),
            "mode_recommendations_by_scope": (
                self.latest_mode_recommendations_by_scope(refresh=False)
            ),
            "prompt_mode_effectiveness": prompt_mode_effectiveness,
            "trust_profile_effectiveness": trust_profile_effectiveness,
        }
        audit_status = self._decision_adjustment_audit_status(
            trust_profile_effectiveness
        )
        if audit_status:
            status_payload["decision_adjustment_audit_status"] = audit_status
        repair_priority_effectiveness = self.project_repair_priority_effectiveness()
        if repair_priority_effectiveness:
            status_payload["repair_priority_effectiveness"] = (
                repair_priority_effectiveness
            )
        validation_repair_effectiveness = (
            self.project_validation_repair_effectiveness()
        )
        if validation_repair_effectiveness:
            status_payload["validation_repair_effectiveness"] = (
                validation_repair_effectiveness
            )
        if cleanup_payload:
            status_payload["effectiveness_cleanup"] = cleanup_payload
        generated_at = _utc_now_iso()
        status_payload["ops_snapshot"] = {
            "version": OPS_SECTION_SNAPSHOT_VERSION,
            "section": self.OPS_SNAPSHOT_SECTION,
            "generated_at": generated_at,
        }
        snapshot = OpsSectionSnapshotV1(
            section=self.OPS_SNAPSHOT_SECTION,
            generated_at=generated_at,
            payload=status_payload,
        )
        self.wiki.initialize()
        with self.wiki._connect() as conn:
            persist_ops_section_snapshot(conn, snapshot)
        return status_payload

    def project_validation_repair_effectiveness(
        self,
        *,
        decision_scope: str | None = None,
        min_samples: int = 3,
        limit: int = 50_000,
    ) -> dict[str, Any]:
        outcomes = self.list_selection_outcomes(limit=limit)
        clean_scope = str(decision_scope or "").strip().lower()
        validation_outcomes = [
            row
            for row in outcomes
            if str(row.get("outcome_kind") or "")
            in {"missed_validation_probe", "resolved_validation_probe"}
            and (
                not clean_scope
                or str(row.get("decision_scope") or "").strip().lower()
                == clean_scope
            )
        ]
        if not validation_outcomes:
            return {}
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        meta_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in validation_outcomes:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            entries = self._validation_repair_effectiveness_entries(
                decision_scope=str(row.get("decision_scope") or "").strip().lower(),
                evidence=evidence,
            )
            for key, meta in entries:
                grouped.setdefault(key, []).append(row)
                existing = meta_by_key.setdefault(
                    key,
                    {
                        "allowed_entry_postures": [],
                        "blocks_new_entries": [],
                        "contract_basis_evidence": [],
                        "source_counts": {},
                    },
                )
                self._extend_unique(existing, "allowed_entry_postures", meta)
                self._extend_unique(existing, "blocks_new_entries", meta)
                self._extend_unique_dicts(
                    existing,
                    "contract_basis_evidence",
                    meta,
                    identity_keys=(
                        "discipline_id",
                        "repair_action_id",
                        "entry_bias",
                        "sample_count",
                        "missed_count",
                    ),
                )
                source = str(meta.get("source") or "").strip()
                if source:
                    source_counts = existing.setdefault("source_counts", {})
                    if isinstance(source_counts, dict):
                        source_counts[source] = int(source_counts.get(source) or 0) + 1
                for number_key in ("risk_budget_multiplier", "max_budget_multiplier"):
                    if number_key not in meta:
                        continue
                    value = _safe_float(meta.get(number_key))
                    if number_key not in existing:
                        existing[number_key] = value
                        continue
                    current = _safe_float(existing.get(number_key))
                    if value < current:
                        existing[number_key] = value
        metrics = [
            self._validation_repair_effectiveness_metric(
                key=key,
                rows=rows,
                min_samples=min_samples,
                meta=meta_by_key.get(key, {}),
            )
            for key, rows in sorted(grouped.items())
        ]
        sample_count = len(validation_outcomes)
        resolved_count = sum(
            1
            for row in validation_outcomes
            if str(row.get("outcome_kind") or "") == "resolved_validation_probe"
        )
        missed_count = sum(
            1
            for row in validation_outcomes
            if str(row.get("outcome_kind") or "") == "missed_validation_probe"
        )
        repair_required = [
            row for row in metrics if str(row.get("status") or "") == "repair_required"
        ]
        probe_count = sum(1 for row in metrics if str(row.get("status") or "") == "probe")
        active_count = sum(
            1 for row in metrics if str(row.get("status") or "") == "active"
        )
        top_degraded = sorted(
            repair_required,
            key=lambda row: (
                -int(row.get("missed_count") or 0),
                -int(row.get("sample_count") or 0),
                float(row.get("resolution_rate") or 0.0),
                str(row.get("discipline_id") or ""),
                str(row.get("repair_action_id") or ""),
            ),
        )[:8]
        status = "active"
        if repair_required:
            status = "repair_required"
        elif probe_count:
            status = "probe"
        return {
            "status": status,
            "sample_count": sample_count,
            "missed_count": missed_count,
            "resolved_count": resolved_count,
            "resolution_rate": self._ratio(resolved_count, sample_count),
            "metric_count": len(metrics),
            "repair_priority_metrics": metrics,
            "active_count": active_count,
            "probe_count": probe_count,
            "repair_required_count": len(repair_required),
            "top_degraded": top_degraded,
            "metrics": metrics[:24],
        }

    @classmethod
    def _validation_repair_effectiveness_entries(
        cls,
        *,
        decision_scope: str,
        evidence: dict[str, Any],
    ) -> list[tuple[tuple[str, str, str, str], dict[str, Any]]]:
        discipline_ids = cls._evidence_values(evidence, "discipline_ids")
        repair_action_ids = cls._evidence_values(evidence, "repair_action_ids")
        entry_biases = cls._evidence_values(evidence, "entry_biases")
        max_len = max(len(discipline_ids), len(repair_action_ids), len(entry_biases))
        if max_len <= 0:
            return []
        meta = {
            "allowed_entry_postures": cls._evidence_values(
                evidence,
                "allowed_entry_postures",
            ),
            "blocks_new_entries": cls._evidence_values(
                evidence,
                "blocks_new_entries",
            ),
            "source": str(evidence.get("source") or "").strip(),
            "contract_basis_evidence": (
                cls._compact_validation_repair_degraded_metric_evidence(
                    evidence.get("degraded_metric_evidence")
                )
            ),
        }
        if evidence.get("risk_budget_multiplier") not in (None, ""):
            meta["risk_budget_multiplier"] = _safe_float(
                evidence.get("risk_budget_multiplier")
            )
        if evidence.get("max_budget_multiplier") not in (None, ""):
            meta["max_budget_multiplier"] = _safe_float(
                evidence.get("max_budget_multiplier")
            )
        entries: list[tuple[tuple[str, str, str, str], dict[str, Any]]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for index in range(max_len):
            key = (
                str(decision_scope or ""),
                cls._value_at(discipline_ids, index)[:160],
                cls._value_at(repair_action_ids, index)[:160],
                cls._value_at(entry_biases, index)[:160],
            )
            if not any(key[1:]) or key in seen:
                continue
            seen.add(key)
            entries.append((key, meta))
        return entries

    @classmethod
    def _validation_repair_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str, str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        decision_scope, discipline_id, repair_action_id, entry_bias = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_validation_probe"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_validation_probe"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        source_counts = (
            meta.get("source_counts")
            if isinstance(meta.get("source_counts"), dict)
            else {}
        )
        clean_source_counts = {
            str(source): int(count)
            for source, count in source_counts.items()
            if str(source).strip() and int(count) > 0
        }
        sources = [
            source
            for source, _count in sorted(
                clean_source_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        contract_basis_summary = cls._validation_repair_contract_basis_summary(
            meta.get("contract_basis_evidence") or []
        )
        metric: dict[str, Any] = {}
        for field, value in (
            ("decision_scope", decision_scope),
            ("discipline_id", discipline_id),
            ("repair_action_id", repair_action_id),
            ("entry_bias", entry_bias),
        ):
            if value not in (None, "", [], {}):
                metric[field] = value
        metric.update(
            {
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
            }
        )
        allowed_entry_postures = meta.get("allowed_entry_postures") or []
        if allowed_entry_postures:
            metric["allowed_entry_postures"] = allowed_entry_postures
        blocks_new_entries = meta.get("blocks_new_entries") or []
        if blocks_new_entries:
            metric["blocks_new_entries"] = blocks_new_entries
        if meta.get("risk_budget_multiplier") not in (None, ""):
            metric["risk_budget_multiplier"] = _safe_float(
                meta.get("risk_budget_multiplier")
            )
        if meta.get("max_budget_multiplier") not in (None, ""):
            metric["max_budget_multiplier"] = _safe_float(
                meta.get("max_budget_multiplier")
            )
        if sources:
            metric["sources"] = sources
            metric["source_counts"] = {
                source: clean_source_counts[source] for source in sources
            }
        for field, value in contract_basis_summary.items():
            if value not in (None, "", [], {}):
                metric[field] = value
        contract_basis_evidence = meta.get("contract_basis_evidence") or []
        if contract_basis_evidence:
            metric["contract_basis_evidence"] = contract_basis_evidence
        return metric

    @classmethod
    def _validation_repair_contract_basis_summary(
        cls,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        clean_rows = [row for row in rows if isinstance(row, dict)]
        if not clean_rows:
            return {}
        sample_count = sum(_safe_int(row.get("sample_count")) for row in clean_rows)
        missed_count = sum(_safe_int(row.get("missed_count")) for row in clean_rows)
        resolved_count = sum(_safe_int(row.get("resolved_count")) for row in clean_rows)
        if sample_count <= 0:
            return {}
        resolution_rate = cls._ratio(resolved_count, sample_count)
        miss_rate = cls._ratio(missed_count, sample_count)
        statuses = {
            str(row.get("status") or "").strip().lower()
            for row in clean_rows
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
            "contract_basis_repair_pressure_score": round(
                missed_count * miss_rate,
                6,
            ),
            "contract_basis_status": status,
        }

    @staticmethod
    def _decision_adjustment_audit_status(
        trust_profile_effectiveness: dict[str, Any],
    ) -> dict[str, Any]:
        profiles = (
            trust_profile_effectiveness.get("trust_profiles")
            if isinstance(trust_profile_effectiveness.get("trust_profiles"), list)
            else []
        )
        metrics: list[dict[str, Any]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            scope = str(profile.get("decision_scope") or "").strip()
            authority = str(profile.get("authority") or "").strip()
            for metric in list(
                profile.get("decision_adjustment_audit_metrics") or []
            ):
                if not isinstance(metric, dict):
                    continue
                row = {
                    "decision_scope": scope,
                    "authority": authority,
                    "action": str(metric.get("action") or "").strip(),
                    "target_risk_posture": str(
                        metric.get("target_risk_posture") or ""
                    ).strip(),
                    "sample_count": _safe_int(metric.get("sample_count")),
                    "avg_return_pct": _safe_float(metric.get("avg_return_pct")),
                    "status": str(metric.get("status") or "").strip(),
                    "contract_status": str(metric.get("contract_status") or "").strip(),
                }
                metrics.append(
                    {
                        key: value
                        for key, value in row.items()
                        if value not in (None, "", [], {})
                    }
                )
        if not metrics:
            return {}
        active_count = sum(
            1 for row in metrics if str(row.get("status") or "") == "active"
        )
        degraded_count = sum(
            1 for row in metrics if str(row.get("status") or "") == "degraded"
        )
        repair_required_count = sum(
            1
            for row in metrics
            if str(row.get("contract_status") or "") == "repair_required"
        )
        top_degraded = sorted(
            [
                row
                for row in metrics
                if str(row.get("status") or "") == "degraded"
                or str(row.get("contract_status") or "") == "repair_required"
            ],
            key=lambda row: (
                float(row.get("avg_return_pct") or 0.0),
                -int(row.get("sample_count") or 0),
            ),
        )[:8]
        return {
            "status": "repair_required"
            if degraded_count or repair_required_count
            else "active",
            "metric_count": len(metrics),
            "active_count": active_count,
            "degraded_count": degraded_count,
            "repair_required_count": repair_required_count,
            "top_degraded": top_degraded,
        }

    def project_repair_priority_effectiveness(
        self,
        *,
        decision_scope: str | None = None,
        min_samples: int = 3,
        limit: int = 50_000,
    ) -> dict[str, Any]:
        outcomes = self.list_selection_outcomes(limit=limit)
        clean_scope = str(decision_scope or "").strip().lower()
        repair_outcomes = [
            row
            for row in outcomes
            if str(row.get("outcome_kind") or "")
            in {"missed_repair_priority", "resolved_repair_priority"}
            and (
                not clean_scope
                or str(row.get("decision_scope") or "").strip().lower()
                == clean_scope
            )
        ]
        if not repair_outcomes:
            return {}
        grouped: dict[
            tuple[str, str, str, str, str, str, str, str],
            list[dict[str, Any]],
        ] = {}
        for row in repair_outcomes:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            keys = self._repair_priority_effectiveness_keys(
                decision_scope=str(row.get("decision_scope") or "").strip().lower(),
                evidence=evidence,
            )
            for key in keys:
                grouped.setdefault(key, []).append(row)
        metrics = [
            self._repair_priority_effectiveness_metric(
                key=key,
                rows=rows,
                min_samples=min_samples,
            )
            for key, rows in sorted(grouped.items())
        ]
        repair_loop_status_metrics = self._repair_loop_status_effectiveness_metrics(
            rows=repair_outcomes,
            min_samples=min_samples,
        )
        repair_success_criteria_metrics = (
            self._repair_success_criteria_effectiveness_metrics(
                rows=repair_outcomes,
                min_samples=min_samples,
            )
        )
        repair_success_criteria_summary = (
            self._repair_success_criteria_effectiveness_summary(
                repair_success_criteria_metrics
            )
        )
        repair_learning_directive_metrics = (
            self._repair_learning_directive_effectiveness_metrics(
                rows=repair_outcomes,
                min_samples=min_samples,
            )
        )
        repair_learning_directive_summary = (
            self._repair_learning_directive_effectiveness_summary(
                repair_learning_directive_metrics
            )
        )
        repair_learning_step_metrics = (
            self._repair_learning_step_effectiveness_metrics(
                rows=repair_outcomes,
                min_samples=min_samples,
            )
        )
        repair_learning_step_summary = (
            self._repair_learning_step_effectiveness_summary(
                repair_learning_step_metrics
            )
        )
        repair_learning_resolution_metrics = (
            self._repair_learning_resolution_effectiveness_metrics(
                rows=repair_outcomes,
                min_samples=min_samples,
            )
        )
        repair_learning_resolution_summary = (
            self._repair_learning_resolution_effectiveness_summary(
                repair_learning_resolution_metrics
            )
        )
        repair_component_target_metrics = (
            self._repair_component_target_effectiveness_metrics(
                rows=repair_outcomes,
                min_samples=min_samples,
            )
        )
        repair_component_target_summary = (
            self._repair_component_target_effectiveness_summary(
                repair_component_target_metrics
            )
        )
        memory_card_quality_gap_summary = (
            self._repair_priority_memory_card_quality_gap_summary(repair_outcomes)
        )
        sample_count = len(repair_outcomes)
        resolved_count = sum(
            1
            for row in repair_outcomes
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in repair_outcomes
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        repair_required = [
            row for row in metrics if str(row.get("status") or "") == "repair_required"
        ]
        probe_count = sum(1 for row in metrics if str(row.get("status") or "") == "probe")
        active_count = sum(
            1 for row in metrics if str(row.get("status") or "") == "active"
        )
        top_degraded = sorted(
            repair_required,
            key=lambda row: (
                -int(row.get("missed_count") or 0),
                -int(row.get("sample_count") or 0),
                float(row.get("resolution_rate") or 0.0),
                str(row.get("source_id") or ""),
            ),
        )[:8]
        component_status_summary = self._repair_priority_component_status_summary(
            components=[
                ("repair_priority_metrics", metrics),
                ("repair_loop_status_metrics", repair_loop_status_metrics),
                (
                    "repair_success_criteria_metrics",
                    repair_success_criteria_metrics,
                ),
                (
                    "repair_learning_directive_metrics",
                    repair_learning_directive_metrics,
                ),
                ("repair_learning_step_metrics", repair_learning_step_metrics),
                (
                    "repair_learning_resolution_metrics",
                    repair_learning_resolution_metrics,
                ),
                (
                    "repair_component_target_metrics",
                    repair_component_target_metrics,
                ),
            ],
        )
        status = str(component_status_summary.get("worst_status") or "active")
        return {
            "status": status,
            "sample_count": sample_count,
            "missed_count": missed_count,
            "resolved_count": resolved_count,
            "resolution_rate": self._ratio(resolved_count, sample_count),
            "metric_count": len(metrics),
            "active_count": active_count,
            "probe_count": probe_count,
            "repair_required_count": len(repair_required),
            "top_degraded": top_degraded,
            "repair_priority_metrics": metrics,
            "repair_loop_status_metrics": repair_loop_status_metrics,
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
            "memory_card_quality_gap_summary": memory_card_quality_gap_summary,
            "component_status_summary": component_status_summary,
        }

    @classmethod
    def _repair_priority_memory_card_quality_gap_summary(
        cls,
        rows: list[dict[str, Any]],
        *,
        limit: int = 8,
    ) -> dict[str, Any]:
        missing_stats: dict[str, dict[str, int]] = {}
        check_stats: dict[str, dict[str, int]] = {}

        def add(
            bucket: dict[str, dict[str, int]],
            key: str,
            *,
            missed: bool,
        ) -> None:
            clean = str(key or "").strip()
            if not clean:
                return
            stats = bucket.setdefault(clean, {"sample_count": 0, "missed_count": 0})
            stats["sample_count"] += 1
            if missed:
                stats["missed_count"] += 1

        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            missing_fields = cls._evidence_values(evidence, "repair_missing_fields")
            required_checks = cls._evidence_values(evidence, "repair_required_checks")
            priority_types = cls._evidence_values(evidence, "repair_priority_types")
            if (
                priority_types
                and "memory_card_quality" not in priority_types
                and not missing_fields
                and not required_checks
            ):
                continue
            if not missing_fields and not required_checks:
                continue
            missed = str(row.get("outcome_kind") or "") == "missed_repair_priority"
            for field in missing_fields:
                add(missing_stats, field, missed=missed)
            for check in required_checks:
                add(check_stats, check, missed=missed)

        if not missing_stats and not check_stats:
            return {}

        def counts(
            bucket: dict[str, dict[str, int]],
            *,
            key: str,
        ) -> dict[str, int]:
            return {
                item: int(values.get(key) or 0)
                for item, values in sorted(bucket.items())
            }

        def top_rows(
            bucket: dict[str, dict[str, int]],
            *,
            label: str,
        ) -> list[dict[str, int | str]]:
            rows = sorted(
                bucket.items(),
                key=lambda item: (
                    -int(item[1].get("missed_count") or 0),
                    -int(item[1].get("sample_count") or 0),
                    item[0],
                ),
            )
            return [
                {
                    label: item,
                    "sample_count": int(values.get("sample_count") or 0),
                    "missed_count": int(values.get("missed_count") or 0),
                }
                for item, values in rows[: max(int(limit), 0)]
            ]

        def priority_terms(bucket: dict[str, dict[str, int]]) -> list[str]:
            rows = sorted(
                (
                    (item, values)
                    for item, values in bucket.items()
                    if int(values.get("missed_count") or 0) > 0
                ),
                key=lambda item: (
                    -int(item[1].get("missed_count") or 0),
                    -int(item[1].get("sample_count") or 0),
                    item[0],
                ),
            )
            return [item for item, _values in rows[: max(int(limit), 0)]]

        def priority_row(
            bucket: dict[str, dict[str, int]],
        ) -> tuple[str, dict[str, int]] | None:
            rows = sorted(
                (
                    (item, values)
                    for item, values in bucket.items()
                    if int(values.get("missed_count") or 0) > 0
                ),
                key=lambda item: (
                    -int(item[1].get("missed_count") or 0),
                    -int(item[1].get("sample_count") or 0),
                    item[0],
                ),
            )
            return rows[0] if rows else None

        def priority_focus() -> dict[str, Any]:
            missing = priority_row(missing_stats)
            check = priority_row(check_stats)
            focus: dict[str, Any] = {}
            if missing:
                field, values = missing
                focus.update(
                    {
                        "missing_field": field,
                        "missing_field_sample_count": int(
                            values.get("sample_count") or 0
                        ),
                        "missing_field_missed_count": int(
                            values.get("missed_count") or 0
                        ),
                    }
                )
            if check:
                check_name, values = check
                focus.update(
                    {
                        "required_check": check_name,
                        "required_check_sample_count": int(
                            values.get("sample_count") or 0
                        ),
                        "required_check_missed_count": int(
                            values.get("missed_count") or 0
                        ),
                    }
                )
            if focus:
                focus["instruction"] = "resolve_priority_memory_card_quality_gap_first"
            return focus

        any_missed = any(
            int(values.get("missed_count") or 0) > 0
            for values in [*missing_stats.values(), *check_stats.values()]
        )
        return {
            key: value
            for key, value in {
                "status": "repair_required" if any_missed else "active",
                "missing_field_counts": counts(
                    missing_stats,
                    key="sample_count",
                ),
                "missing_field_missed_counts": counts(
                    missing_stats,
                    key="missed_count",
                ),
                "required_check_counts": counts(
                    check_stats,
                    key="sample_count",
                ),
                "required_check_missed_counts": counts(
                    check_stats,
                    key="missed_count",
                ),
                "priority_missing_fields": priority_terms(missing_stats),
                "priority_required_checks": priority_terms(check_stats),
                "priority_focus": priority_focus(),
                "top_missing_fields": top_rows(missing_stats, label="field"),
                "top_required_checks": top_rows(check_stats, label="check"),
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_loop_status_effectiveness_metrics(
        cls,
        *,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        loop_stats_by_key: dict[tuple[str, str, str], dict[str, int | float]] = {}
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            entries = cls._repair_loop_status_effectiveness_entries(
                decision_scope=str(row.get("decision_scope") or "").strip().lower(),
                evidence=evidence,
            )
            for key, loop_stats in entries:
                grouped.setdefault(key, []).append(row)
                loop_stats_by_key[key] = loop_stats
        return [
            cls._repair_loop_status_effectiveness_metric(
                key=key,
                rows=metric_rows,
                min_samples=min_samples,
                loop_stats=loop_stats_by_key.get(key, {}),
            )
            for key, metric_rows in sorted(grouped.items())
        ]

    @classmethod
    def _repair_success_criteria_effectiveness_metrics(
        cls,
        *,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            decision_scope = str(row.get("decision_scope") or "").strip().lower()
            criteria = cls._evidence_values(
                evidence,
                "repair_resolution_success_criteria",
            )
            for criterion in criteria:
                grouped.setdefault((decision_scope, criterion), []).append(row)
        return [
            cls._repair_success_criteria_effectiveness_metric(
                key=key,
                rows=metric_rows,
                min_samples=min_samples,
            )
            for key, metric_rows in sorted(grouped.items())
        ]

    @classmethod
    def _repair_learning_directive_effectiveness_metrics(
        cls,
        *,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            decision_scope = str(row.get("decision_scope") or "").strip().lower()
            actions = cls._evidence_values(
                evidence,
                "repair_learning_recommended_actions",
            )
            for action in actions:
                grouped.setdefault((decision_scope, action), []).append(row)
        return [
            cls._repair_learning_directive_effectiveness_metric(
                key=key,
                rows=metric_rows,
                min_samples=min_samples,
            )
            for key, metric_rows in sorted(grouped.items())
        ]

    @classmethod
    def _repair_learning_directive_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        decision_scope, recommended_action = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "recommended_action": recommended_action,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_learning_step_effectiveness_metrics(
        cls,
        *,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            decision_scope = str(row.get("decision_scope") or "").strip().lower()
            steps = cls._evidence_values(
                evidence,
                "repair_learning_resolution_steps",
            )
            for step in steps:
                grouped.setdefault((decision_scope, step), []).append(row)
        return [
            cls._repair_learning_step_effectiveness_metric(
                key=key,
                rows=metric_rows,
                min_samples=min_samples,
            )
            for key, metric_rows in sorted(grouped.items())
        ]

    @classmethod
    def _repair_learning_step_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        decision_scope, resolution_step = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "resolution_step": resolution_step,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_learning_resolution_effectiveness_metrics(
        cls,
        *,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            decision_scope = str(row.get("decision_scope") or "").strip().lower()
            contexts = cls._repair_learning_resolution_contexts(evidence)
            for resolution, market, side, horizon in contexts:
                grouped.setdefault(
                    (decision_scope, resolution, market, side, horizon),
                    [],
                ).append(row)
        return [
            cls._repair_learning_resolution_effectiveness_metric(
                key=key,
                rows=metric_rows,
                min_samples=min_samples,
            )
            for key, metric_rows in sorted(grouped.items())
        ]

    @classmethod
    def _repair_learning_resolution_names(
        cls,
        evidence: dict[str, Any],
    ) -> list[str]:
        return [
            resolution
            for resolution, _, _, _ in cls._repair_learning_resolution_contexts(
                evidence
            )
        ]

    @classmethod
    def _repair_learning_resolution_contexts(
        cls,
        evidence: dict[str, Any],
    ) -> list[tuple[str, str, str, str]]:
        contexts: list[tuple[str, str, str, str]] = []

        def add_context(
            resolution: Any,
            *,
            market: Any = "",
            side: Any = "",
            horizon: Any = "",
        ) -> None:
            clean_resolution = str(resolution or "").strip()[:180]
            if not clean_resolution:
                return
            item = (
                clean_resolution,
                str(market or "").strip().lower()[:40],
                str(side or "").strip().lower()[:40],
                str(horizon or "").strip().lower()[:60],
            )
            if item not in contexts:
                contexts.append(item)

        values = cls._evidence_values(
            evidence,
            "repair_learning_step_recommended_resolutions",
        )
        for value in values:
            add_context(value)
        for target in list(evidence.get("repair_learning_resolution_targets") or []):
            if not isinstance(target, dict):
                continue
            add_context(
                target.get("recommended_resolution"),
                market=target.get("market"),
                side=target.get("side"),
                horizon=target.get("horizon"),
            )
        resolution_summary = (
            evidence.get("resolution")
            if isinstance(evidence.get("resolution"), dict)
            else {}
        )
        for row in list(resolution_summary.get("resolved_candidates") or []):
            if not isinstance(row, dict):
                continue
            add_context(
                row.get("resolution"),
                market=row.get("market"),
                side=row.get("side"),
                horizon=row.get("horizon"),
            )
        return contexts

    @classmethod
    def _repair_learning_resolution_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str, str, str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        decision_scope, recommended_resolution, market, side, horizon = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "recommended_resolution": recommended_resolution,
                "market": market,
                "side": side,
                "horizon": horizon,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_component_target_effectiveness_metrics(
        cls,
        *,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        meta_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            decision_scope = str(row.get("decision_scope") or "").strip().lower()
            seen_keys: set[tuple[str, ...]] = set()
            for key, meta in cls._repair_component_target_entries(
                decision_scope=decision_scope,
                evidence=evidence,
            ):
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                grouped.setdefault(key, []).append(row)
                existing = meta_by_key.setdefault(
                    key,
                    {
                        "quality_warnings": [],
                        "impacted_page_ids": [],
                        "impacted_symbols": [],
                        "repair_targets": [],
                        "repair_target_effectiveness": [],
                        "repair_target_effectiveness_statuses": [],
                    },
                )
                cls._extend_unique(existing, "quality_warnings", meta)
                cls._extend_unique(existing, "impacted_page_ids", meta)
                cls._extend_unique(existing, "impacted_symbols", meta)
                for target in list(meta.get("repair_targets") or []):
                    if isinstance(target, dict) and target not in existing[
                        "repair_targets"
                    ]:
                        existing["repair_targets"].append(target)
                for effectiveness in list(
                    meta.get("repair_target_effectiveness") or []
                ):
                    if (
                        isinstance(effectiveness, dict)
                        and effectiveness
                        not in existing["repair_target_effectiveness"]
                    ):
                        existing["repair_target_effectiveness"].append(effectiveness)
                        status = str(effectiveness.get("status") or "").strip()
                        if (
                            status
                            and status
                            not in existing["repair_target_effectiveness_statuses"]
                        ):
                            existing["repair_target_effectiveness_statuses"].append(
                                status[:80]
                            )
                cls._extend_unique(
                    existing,
                    "repair_target_effectiveness_statuses",
                    meta,
                )
        return [
            cls._repair_component_target_effectiveness_metric(
                key=key,
                rows=metric_rows,
                min_samples=min_samples,
                meta=meta_by_key.get(key, {}),
            )
            for key, metric_rows in sorted(grouped.items())
        ]

    @classmethod
    def _repair_component_target_entries(
        cls,
        *,
        decision_scope: str,
        evidence: dict[str, Any],
    ) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
        entries: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        for field in (
            "repair_component_targets",
            "repair_required_component_targets",
            "repair_probe_component_targets",
        ):
            for target in list(evidence.get(field) or []):
                if not isinstance(target, dict):
                    continue
                target_scope = (
                    str(target.get("decision_scope") or "").strip().lower()
                    or decision_scope
                )
                component = str(target.get("component") or "").strip()[:120]
                target_status = str(target.get("status") or "").strip().lower()[:80]
                priority_type = str(target.get("priority_type") or "").strip()[:120]
                action_type = str(target.get("action_type") or "").strip()[:120]
                criterion = str(target.get("criterion") or "").strip()[:180]
                recommended_action = str(
                    target.get("recommended_action") or ""
                ).strip()[:180]
                resolution_step = str(
                    target.get("resolution_step") or ""
                ).strip()[:180]
                recommended_resolution = str(
                    target.get("recommended_resolution") or ""
                ).strip()[:180]
                if not component:
                    continue
                if not any(
                    (
                        action_type,
                        criterion,
                        recommended_action,
                        resolution_step,
                        recommended_resolution,
                    )
                ):
                    continue
                key = (
                    target_scope,
                    component,
                    target_status,
                    priority_type,
                    action_type,
                    criterion,
                    recommended_action,
                    resolution_step,
                    recommended_resolution,
                )
                entries.append(
                    (
                        key,
                        {
                            "impacted_symbols": _compact_prompt_string_list(
                                target.get("impacted_symbols"),
                                limit=24,
                                max_len=40,
                            ),
                            "quality_warnings": _compact_prompt_string_list(
                                target.get("quality_warnings"),
                                limit=6,
                                max_len=120,
                            ),
                            "impacted_page_ids": _compact_prompt_string_list(
                                target.get("impacted_page_ids"),
                                limit=12,
                                max_len=180,
                            ),
                            "repair_targets": _compact_prompt_repair_targets(
                                target.get("repair_targets")
                            ),
                            "repair_target_effectiveness": (
                                _compact_prompt_repair_target_effectiveness(
                                    target.get("repair_target_effectiveness")
                                )
                            ),
                            "repair_target_effectiveness_statuses": (
                                _compact_prompt_string_list(
                                    target.get(
                                        "repair_target_effectiveness_statuses"
                                    ),
                                    limit=8,
                                    max_len=80,
                                )
                            ),
                        },
                    )
                )
        return entries

    @staticmethod
    def _extend_unique(
        target: dict[str, Any],
        key: str,
        source: dict[str, Any],
    ) -> None:
        values = target.setdefault(key, [])
        for item in list(source.get(key) or []):
            clean = str(item or "").strip()
            if clean and clean not in values:
                values.append(clean[:180])

    @staticmethod
    def _extend_unique_dicts(
        target: dict[str, Any],
        key: str,
        source: dict[str, Any],
        *,
        identity_keys: tuple[str, ...],
    ) -> None:
        values = target.setdefault(key, [])
        if not isinstance(values, list):
            values = []
            target[key] = values
        seen = {
            tuple(str(row.get(identity_key) or "") for identity_key in identity_keys)
            for row in values
            if isinstance(row, dict)
        }
        for item in list(source.get(key) or []):
            if not isinstance(item, dict):
                continue
            identity = tuple(
                str(item.get(identity_key) or "") for identity_key in identity_keys
            )
            if identity in seen:
                continue
            values.append(item)
            seen.add(identity)

    @classmethod
    def _repair_component_target_effectiveness_metric(
        cls,
        *,
        key: tuple[str, ...],
        rows: list[dict[str, Any]],
        min_samples: int,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        (
            decision_scope,
            component,
            target_status,
            priority_type,
            action_type,
            criterion,
            recommended_action,
            resolution_step,
            recommended_resolution,
        ) = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "component": component,
                "target_status": target_status,
                "priority_type": priority_type,
                "action_type": action_type,
                "criterion": criterion,
                "recommended_action": recommended_action,
                "resolution_step": resolution_step,
                "recommended_resolution": recommended_resolution,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
                "quality_warnings": list(meta.get("quality_warnings") or [])[:6],
                "impacted_page_ids": list(meta.get("impacted_page_ids") or [])[:12],
                "impacted_symbols": list(meta.get("impacted_symbols") or [])[:24],
                "repair_targets": list(meta.get("repair_targets") or [])[:8],
                "repair_target_effectiveness": list(
                    meta.get("repair_target_effectiveness") or []
                )[:8],
                "repair_target_effectiveness_statuses": list(
                    meta.get("repair_target_effectiveness_statuses") or []
                )[:8],
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_component_target_effectiveness_summary(
        cls,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {}
        sorted_rows = sorted(rows, key=cls._repair_loop_effectiveness_row_sort_key)
        repair_required_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "repair_required"
        )
        probe_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "probe"
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
            component = str(row.get("component") or "").strip()
            if component and component not in top_components:
                top_components.append(component[:120])
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
        top_details = _compact_prompt_repair_component_targets(
            sorted_rows,
            limit=3,
        )
        repair_required_details = _compact_prompt_repair_component_targets(
            repair_required_rows,
            limit=3,
        )
        probe_details = _compact_prompt_repair_component_targets(
            probe_rows,
            limit=3,
        )
        primary_repair_required_detail = next(iter(repair_required_details), None)
        primary_probe_detail = next(iter(probe_details), None)
        return {
            key: value
            for key, value in {
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
                "primary_repair_required_component_target_detail": (
                    primary_repair_required_detail
                ),
                "probe_component_target_details": probe_details,
                "primary_probe_component_target_detail": primary_probe_detail,
                "component_target_attention_plan": (
                    _prompt_component_target_attention_plan(
                        repair_required_detail=primary_repair_required_detail,
                        probe_detail=primary_probe_detail,
                    )
                ),
                "max_missed_count": _max_present_int(rows, "missed_count"),
                "max_sample_count": _max_present_int(rows, "sample_count"),
                "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_success_criteria_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        decision_scope, criterion = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "criterion": criterion,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_success_criteria_effectiveness_summary(
        cls,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {}
        repair_required_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "repair_required"
        )
        probe_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "probe"
        )
        active_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "active"
        )
        unknown_count = len(rows) - repair_required_count - probe_count - active_count
        worst_status = "active"
        if repair_required_count:
            worst_status = "repair_required"
        elif probe_count:
            worst_status = "probe"
        elif unknown_count and not active_count:
            worst_status = "unknown"
        sorted_rows = sorted(
            rows,
            key=cls._repair_success_criteria_metric_sort_key,
        )
        top_failed_criteria: list[str] = []
        for row in sorted_rows:
            criterion = str(row.get("criterion") or "").strip()
            if criterion and criterion not in top_failed_criteria:
                top_failed_criteria.append(criterion)
            if len(top_failed_criteria) >= 5:
                break
        repair_learning_directives = (
            cls._repair_success_criteria_learning_directives(sorted_rows)
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

    @classmethod
    def _repair_success_criteria_learning_directives(
        cls,
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
            _add_prompt_int(directive, row, "missed_count")
            _add_prompt_float(directive, row, "resolution_rate")
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

    @classmethod
    def _repair_learning_directive_effectiveness_summary(
        cls,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {}
        repair_required_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "repair_required"
        )
        probe_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "probe"
        )
        active_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "active"
        )
        unknown_count = len(rows) - repair_required_count - probe_count - active_count
        worst_status = "active"
        if repair_required_count:
            worst_status = "repair_required"
        elif probe_count:
            worst_status = "probe"
        elif unknown_count and not active_count:
            worst_status = "unknown"
        sorted_rows = sorted(
            rows,
            key=cls._repair_learning_directive_metric_sort_key,
        )
        repair_required_actions = cls._repair_learning_directive_actions(
            rows,
            status="repair_required",
        )
        top_missed_actions = cls._repair_learning_directive_actions(
            rows,
            only_max_missed=True,
        )
        primary_recommended_action = next(
            iter(repair_required_actions or top_missed_actions),
            None,
        )
        action_targets = cls._repair_learning_directive_action_targets(sorted_rows)
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
                "action_targets": action_targets,
                "max_missed_count": _max_present_int(rows, "missed_count"),
                "max_sample_count": _max_present_int(rows, "sample_count"),
                "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
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
                row
                for row in candidates
                if _has_prompt_value(row.get("missed_count"))
            ]
            if not candidates:
                return []
            max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
            candidates = [
                row
                for row in candidates
                if _safe_int(row.get("missed_count")) == max_missed
            ]
        candidates.sort(
            key=(
                JueWikiApplicationService
                ._repair_learning_directive_metric_sort_key
            )
        )
        actions: list[str] = []
        for row in candidates:
            action = str(row.get("recommended_action") or "").strip()
            if action and action not in actions:
                actions.append(action[:180])
            if len(actions) >= max(int(limit), 0):
                break
        return actions

    @classmethod
    def _repair_learning_directive_action_targets(
        cls,
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
            for metric_key in ("sample_count", "missed_count", "resolved_count"):
                _accumulate_present_int_metric(target, row, metric_key)
            target["metric_count"] += 1
        targets: list[dict[str, Any]] = []
        for target in grouped.values():
            _attach_present_target_rates(target)
            target["recommended_resolution"] = (
                "revise_learning_directive_then_probe"
            )
            target["resolution_steps"] = [
                "inspect_failed_repair_directive_outcomes",
                "revise_or_demote_learning_directive",
                "record_next_outcome_before_reuse",
            ]
            targets.append(
                {
                    key: value
                    for key, value in target.items()
                    if value not in (None, "", [], {})
                }
            )
        targets.sort(
            key=lambda row: (
                cls._repair_loop_status_metric_rank(str(row.get("status") or "")),
                -_safe_int(row.get("missed_count")),
                -_safe_float(row.get("repair_pressure_score")),
                -_safe_int(row.get("sample_count")),
                _safe_float(row.get("resolution_rate")),
                str(row.get("recommended_action") or ""),
            )
        )
        return targets[: max(int(limit), 0)]

    @classmethod
    def _repair_learning_step_effectiveness_summary(
        cls,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {}
        repair_required_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "repair_required"
        )
        probe_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "probe"
        )
        active_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "active"
        )
        unknown_count = len(rows) - repair_required_count - probe_count - active_count
        worst_status = "active"
        if repair_required_count:
            worst_status = "repair_required"
        elif probe_count:
            worst_status = "probe"
        elif unknown_count and not active_count:
            worst_status = "unknown"
        sorted_rows = sorted(rows, key=cls._repair_learning_step_metric_sort_key)
        repair_required_steps = cls._repair_learning_resolution_steps(
            rows,
            status="repair_required",
        )
        top_missed_steps = cls._repair_learning_resolution_steps(
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
                "step_targets": cls._repair_learning_step_targets(sorted_rows),
                "max_missed_count": _max_present_int(rows, "missed_count"),
                "max_sample_count": _max_present_int(rows, "sample_count"),
                "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
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
                row
                for row in candidates
                if _has_prompt_value(row.get("missed_count"))
            ]
            if not candidates:
                return []
            max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
            candidates = [
                row
                for row in candidates
                if _safe_int(row.get("missed_count")) == max_missed
            ]
        candidates.sort(
            key=JueWikiApplicationService._repair_learning_step_metric_sort_key
        )
        steps: list[str] = []
        for row in candidates:
            step = str(row.get("resolution_step") or "").strip()
            if step and step not in steps:
                steps.append(step[:180])
            if len(steps) >= max(int(limit), 0):
                break
        return steps

    @classmethod
    def _repair_learning_step_targets(
        cls,
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
            targets.append(
                {
                    key: value
                    for key, value in target.items()
                    if value not in (None, "", [], {})
                }
            )
        targets.sort(
            key=lambda row: (
                cls._repair_loop_status_metric_rank(str(row.get("status") or "")),
                -_safe_int(row.get("missed_count")),
                -_safe_float(row.get("repair_pressure_score")),
                -_safe_int(row.get("sample_count")),
                _safe_float(row.get("resolution_rate")),
                str(row.get("resolution_step") or ""),
            )
        )
        return targets[: max(int(limit), 0)]

    @classmethod
    def _repair_learning_resolution_effectiveness_summary(
        cls,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {}
        repair_required_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "repair_required"
        )
        probe_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "probe"
        )
        active_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "active"
        )
        unknown_count = len(rows) - repair_required_count - probe_count - active_count
        worst_status = "active"
        if repair_required_count:
            worst_status = "repair_required"
        elif probe_count:
            worst_status = "probe"
        elif unknown_count and not active_count:
            worst_status = "unknown"
        sorted_rows = sorted(rows, key=cls._repair_learning_resolution_metric_sort_key)
        repair_required_resolutions = cls._repair_learning_recommended_resolutions(
            rows,
            status="repair_required",
        )
        top_missed_resolutions = cls._repair_learning_recommended_resolutions(
            rows,
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
                "resolution_targets": (
                    cls._repair_learning_resolution_targets(sorted_rows)
                ),
                "max_missed_count": _max_present_int(rows, "missed_count"),
                "max_sample_count": _max_present_int(rows, "sample_count"),
                "min_resolution_rate": _min_present_float(rows, "resolution_rate"),
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
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
                row
                for row in candidates
                if _has_prompt_value(row.get("missed_count"))
            ]
            if not candidates:
                return []
            max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
            candidates = [
                row
                for row in candidates
                if _safe_int(row.get("missed_count")) == max_missed
            ]
        candidates.sort(
            key=JueWikiApplicationService._repair_learning_resolution_metric_sort_key
        )
        resolutions: list[str] = []
        for row in candidates:
            resolution = str(row.get("recommended_resolution") or "").strip()
            if resolution and resolution not in resolutions:
                resolutions.append(resolution[:180])
            if len(resolutions) >= max(int(limit), 0):
                break
        return resolutions

    @classmethod
    def _repair_learning_resolution_targets(
        cls,
        rows: list[dict[str, Any]],
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            resolution = str(row.get("recommended_resolution") or "").strip()
            if not resolution:
                continue
            key = (
                str(row.get("decision_scope") or "").strip(),
                str(row.get("status") or "").strip(),
                resolution,
                str(row.get("market") or "").strip(),
                str(row.get("side") or "").strip(),
                str(row.get("horizon") or "").strip(),
            )
            target = grouped.setdefault(
                key,
                {
                    "decision_scope": key[0],
                    "status": key[1],
                    "recommended_resolution": resolution,
                    "market": key[3],
                    "side": key[4],
                    "horizon": key[5],
                    "metric_count": 0,
                },
            )
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
            targets.append(
                {
                    key: value
                    for key, value in target.items()
                    if value not in (None, "", [], {})
                }
            )
        targets.sort(
            key=lambda row: (
                cls._repair_loop_status_metric_rank(str(row.get("status") or "")),
                -_safe_int(row.get("missed_count")),
                -_safe_float(row.get("repair_pressure_score")),
                -_safe_int(row.get("sample_count")),
                _safe_float(row.get("resolution_rate")),
                str(row.get("recommended_resolution") or ""),
            )
        )
        return targets[: max(int(limit), 0)]

    @classmethod
    def _repair_learning_step_metrics_from_action_targets(
        cls,
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
                metric["sample_count"] += _safe_int(target.get("sample_count"))
                metric["missed_count"] += _safe_int(target.get("missed_count"))
                metric["resolved_count"] += _safe_int(target.get("resolved_count"))
        rows: list[dict[str, Any]] = []
        for metric in grouped.values():
            sample_count = _safe_int(metric.get("sample_count"))
            missed_count = _safe_int(metric.get("missed_count"))
            resolved_count = _safe_int(metric.get("resolved_count"))
            resolution_rate = cls._ratio(resolved_count, sample_count)
            status = str(metric.get("status") or "").strip()
            if not status:
                if sample_count < 3:
                    status = "probe"
                elif resolution_rate >= 0.5 and resolved_count >= missed_count:
                    status = "active"
                else:
                    status = "repair_required"
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

    @classmethod
    def _repair_learning_resolution_metrics_from_step_targets(
        cls,
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
            resolution_rate = cls._ratio(resolved_count, sample_count)
            status = str(metric.get("status") or "").strip()
            if not status:
                if sample_count < 3:
                    status = "probe"
                elif resolution_rate >= 0.5 and resolved_count >= missed_count:
                    status = "active"
                else:
                    status = "repair_required"
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

    @classmethod
    def _repair_learning_directive_metrics_from_success_criteria_rows(
        cls,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            directives = cls._repair_success_criteria_learning_directives(
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
                metric["sample_count"] += _safe_int(row.get("sample_count"))
                metric["missed_count"] += _safe_int(row.get("missed_count"))
                metric["resolved_count"] += _safe_int(row.get("resolved_count"))
        metrics: list[dict[str, Any]] = []
        for metric in grouped.values():
            sample_count = _safe_int(metric.get("sample_count"))
            missed_count = _safe_int(metric.get("missed_count"))
            resolved_count = _safe_int(metric.get("resolved_count"))
            resolution_rate = cls._ratio(resolved_count, sample_count)
            if sample_count < 3:
                status = "probe"
            elif resolution_rate >= 0.5 and resolved_count >= missed_count:
                status = "active"
            else:
                status = "repair_required"
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

    @staticmethod
    def _repair_success_criteria_metric_sort_key(
        row: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            JueWikiApplicationService._repair_loop_status_metric_rank(
                str(row.get("status") or "")
            ),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("criterion") or ""),
            str(row.get("decision_scope") or ""),
        )

    @staticmethod
    def _repair_learning_directive_metric_sort_key(
        row: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            JueWikiApplicationService._repair_loop_status_metric_rank(
                str(row.get("status") or "")
            ),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("recommended_action") or ""),
            str(row.get("decision_scope") or ""),
        )

    @staticmethod
    def _repair_learning_step_metric_sort_key(
        row: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            JueWikiApplicationService._repair_loop_status_metric_rank(
                str(row.get("status") or "")
            ),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("resolution_step") or ""),
            str(row.get("decision_scope") or ""),
        )

    @staticmethod
    def _repair_learning_resolution_metric_sort_key(
        row: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            JueWikiApplicationService._repair_loop_status_metric_rank(
                str(row.get("status") or "")
            ),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("recommended_resolution") or ""),
            str(row.get("decision_scope") or ""),
            str(row.get("market") or ""),
            str(row.get("side") or ""),
            str(row.get("horizon") or ""),
        )

    @classmethod
    def _repair_loop_status_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
        loop_stats: dict[str, int | float],
    ) -> dict[str, Any]:
        decision_scope, repair_loop_status, action_type = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "repair_loop_status": repair_loop_status,
                "action_type": action_type,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
                "loop_sample_count": loop_stats.get("loop_sample_count"),
                "loop_missed_count": loop_stats.get("loop_missed_count"),
                "loop_resolved_count": loop_stats.get("loop_resolved_count"),
                "loop_resolution_rate": loop_stats.get("loop_resolution_rate"),
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_loop_status_effectiveness_entries(
        cls,
        *,
        decision_scope: str,
        evidence: dict[str, Any],
    ) -> list[tuple[tuple[str, str, str], dict[str, int | float]]]:
        statuses = cls._evidence_values(evidence, "repair_loop_statuses")
        action_types = cls._evidence_values(evidence, "repair_loop_action_types")
        sample_counts = cls._evidence_int_values(evidence, "repair_loop_sample_counts")
        missed_counts = cls._evidence_int_values(evidence, "repair_loop_missed_counts")
        resolved_counts = cls._evidence_int_values(
            evidence,
            "repair_loop_resolved_counts",
        )
        resolution_rates = cls._evidence_float_values(
            evidence,
            "repair_loop_resolution_rates",
        )
        max_len = max(
            len(statuses),
            len(action_types),
            len(sample_counts),
            len(missed_counts),
            len(resolved_counts),
            len(resolution_rates),
            1,
        )
        entries: list[tuple[tuple[str, str, str], dict[str, int | float]]] = []
        for idx in range(max_len):
            status = cls._value_at(statuses, idx)
            action_type = cls._value_at(action_types, idx)
            if not status and not action_type:
                continue
            loop_stats = {
                key: value
                for key, value in {
                    "loop_sample_count": cls._numeric_value_at(sample_counts, idx),
                    "loop_missed_count": cls._numeric_value_at(missed_counts, idx),
                    "loop_resolved_count": cls._numeric_value_at(
                        resolved_counts,
                        idx,
                    ),
                    "loop_resolution_rate": cls._numeric_value_at(
                        resolution_rates,
                        idx,
                    ),
                }.items()
                if value is not None
            }
            entries.append(((decision_scope, status, action_type), loop_stats))
        deduped: dict[tuple[str, str, str], dict[str, int | float]] = {}
        for key, loop_stats in entries:
            deduped[key] = loop_stats
        return list(deduped.items())

    @classmethod
    def _repair_loop_status_effectiveness_keys(
        cls,
        *,
        decision_scope: str,
        evidence: dict[str, Any],
    ) -> list[tuple[str, str, str]]:
        statuses = cls._evidence_values(evidence, "repair_loop_statuses")
        action_types = cls._evidence_values(evidence, "repair_loop_action_types")
        max_len = max(len(statuses), len(action_types), 1)
        keys: list[tuple[str, str, str]] = []
        for idx in range(max_len):
            status = cls._value_at(statuses, idx)
            action_type = cls._value_at(action_types, idx)
            if not status and not action_type:
                continue
            keys.append((decision_scope, status, action_type))
        return list(dict.fromkeys(keys))

    @staticmethod
    def _numeric_value_at(
        values: list[int] | list[float],
        index: int,
    ) -> int | float | None:
        if not values:
            return None
        if index < len(values):
            return values[index]
        return values[-1]

    @staticmethod
    def _evidence_int_values(evidence: dict[str, Any], key: str) -> list[int]:
        values = evidence.get(key)
        if isinstance(values, list):
            items = values
        else:
            items = [values]
        parsed: list[int] = []
        for item in items:
            if item in (None, ""):
                continue
            try:
                parsed.append(int(item))
            except (TypeError, ValueError):
                continue
        return parsed

    @staticmethod
    def _evidence_float_values(evidence: dict[str, Any], key: str) -> list[float]:
        values = evidence.get(key)
        if isinstance(values, list):
            items = values
        else:
            items = [values]
        parsed: list[float] = []
        for item in items:
            if item in (None, ""):
                continue
            try:
                parsed.append(float(item))
            except (TypeError, ValueError):
                continue
        return parsed

    @classmethod
    def _repair_priority_effectiveness_metric(
        cls,
        *,
        key: tuple[str, str, str, str, str, str, str, str],
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        (
            decision_scope,
            priority_type,
            action_type,
            decision_use,
            source_id,
            market,
            side,
            horizon,
        ) = key
        sample_count = len(rows)
        resolved_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "resolved_repair_priority"
        )
        missed_count = sum(
            1
            for row in rows
            if str(row.get("outcome_kind") or "") == "missed_repair_priority"
        )
        resolution_rate = cls._ratio(resolved_count, sample_count)
        if sample_count < max(int(min_samples), 1):
            status = "probe"
        elif resolution_rate >= 0.5 and resolved_count >= missed_count:
            status = "active"
        else:
            status = "repair_required"
        quality_warnings = cls._repair_priority_effectiveness_quality_warnings(rows)
        repair_missing_fields = cls._repair_priority_effectiveness_evidence_values(
            rows,
            keys=("repair_missing_fields",),
        )
        repair_required_checks = cls._repair_priority_effectiveness_evidence_values(
            rows,
            keys=("repair_required_checks",),
        )
        return {
            key: value
            for key, value in {
                "decision_scope": decision_scope,
                "priority_type": priority_type,
                "action_type": action_type,
                "decision_use": decision_use,
                "source_id": source_id,
                "market": market,
                "side": side,
                "horizon": horizon,
                "sample_count": sample_count,
                "missed_count": missed_count,
                "resolved_count": resolved_count,
                "resolution_rate": resolution_rate,
                "status": status,
                "quality_warnings": quality_warnings,
                "repair_missing_fields": repair_missing_fields,
                "repair_required_checks": repair_required_checks,
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_priority_effectiveness_evidence_values(
        cls,
        rows: list[dict[str, Any]],
        *,
        keys: tuple[str, ...],
        limit: int = 12,
    ) -> list[str]:
        values: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            for key in keys:
                for item in cls._evidence_values(evidence, key):
                    value = str(item).strip()
                    if value and value not in values:
                        values.append(value)
                    if len(values) >= max(int(limit), 0):
                        return values
        return values

    @classmethod
    def _repair_priority_effectiveness_quality_warnings(
        cls,
        rows: list[dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        for row in rows:
            evidence = (
                row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            )
            for key in ("quality_warnings", "repair_quality_warnings"):
                for item in cls._evidence_values(evidence, key):
                    warning = str(item).strip()
                    if warning and warning not in warnings:
                        warnings.append(warning)
                    if len(warnings) >= 12:
                        return warnings
        return warnings

    @classmethod
    def _repair_priority_effectiveness_keys(
        cls,
        *,
        decision_scope: str,
        evidence: dict[str, Any],
    ) -> list[tuple[str, str, str, str, str, str, str, str]]:
        priority_types = cls._evidence_values(evidence, "repair_priority_types")
        action_types = cls._evidence_values(evidence, "repair_action_types")
        decision_uses = cls._evidence_values(evidence, "repair_decision_uses")
        source_ids = cls._evidence_values(evidence, "repair_source_ids")
        contexts = cls._repair_priority_lane_contexts(evidence)
        max_len = max(
            len(priority_types),
            len(action_types),
            len(decision_uses),
            len(source_ids),
        )
        keys: list[tuple[str, str, str, str, str, str, str, str]] = []
        for idx in range(max_len):
            for market, side, horizon in contexts:
                keys.append(
                    (
                        decision_scope,
                        cls._value_at(priority_types, idx),
                        cls._value_at(action_types, idx),
                        cls._value_at(decision_uses, idx),
                        cls._value_at(source_ids, idx),
                        market,
                        side,
                        horizon,
                    )
                )
        return list(dict.fromkeys(keys))

    @staticmethod
    def _repair_priority_lane_contexts(
        evidence: dict[str, Any],
    ) -> list[tuple[str, str, str]]:
        contexts: list[tuple[str, str, str]] = []

        def add_context(market: Any, side: Any, horizon: Any) -> None:
            item = (
                str(market or "").strip().lower()[:40],
                str(side or "").strip().lower()[:40],
                str(horizon or "").strip().lower()[:60],
            )
            if item != ("", "", "") and item not in contexts:
                contexts.append(item)

        def values_from(
            scalar: Any,
            *plural_keys: str,
            max_len: int,
        ) -> list[str]:
            raw_values: list[Any] = []
            if scalar not in (None, "", [], {}):
                raw_values.append(scalar)
            for key in plural_keys:
                value = evidence.get(key)
                if isinstance(value, list):
                    raw_values.extend(value)
                elif value not in (None, "", [], {}):
                    raw_values.append(value)
            values: list[str] = []
            for value in raw_values:
                clean = str(value or "").strip().lower()[:max_len]
                if clean and clean not in values:
                    values.append(clean)
            return values

        markets = values_from(evidence.get("market"), "repair_markets", max_len=40)
        sides = values_from(evidence.get("side"), "repair_sides", max_len=40)
        horizons = values_from(
            evidence.get("horizon"),
            "repair_horizons",
            "repair_requested_horizons",
            max_len=60,
        )
        for market in markets or [""]:
            for side in sides or [""]:
                for horizon in horizons or [""]:
                    add_context(market, side, horizon)
        resolution = (
            evidence.get("resolution")
            if isinstance(evidence.get("resolution"), dict)
            else {}
        )
        for row in list(resolution.get("resolved_candidates") or []):
            if not isinstance(row, dict):
                continue
            add_context(
                row.get("market"),
                row.get("side"),
                row.get("horizon"),
            )
        return contexts or [("", "", "")]

    @classmethod
    def _repair_priority_component_status_summary(
        cls,
        *,
        components: list[tuple[str, list[dict[str, Any]]]],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for component, metrics in components:
            if not metrics:
                continue
            row = cls._repair_priority_metric_status_counts(metrics)
            row["component"] = component
            component_targets = cls._repair_priority_component_targets(metrics)
            if component_targets:
                row["component_targets"] = component_targets
            rows.append(row)
        if not rows:
            return {
                "component_count": 0,
                "metric_count": 0,
                "repair_required_count": 0,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "active",
            }
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
                "top_component_targets": cls._repair_priority_top_component_targets(
                    rows
                ),
                "repair_required_component_targets": (
                    cls._repair_priority_top_component_targets(
                        rows,
                        statuses={"repair_required"},
                    )
                ),
                "probe_component_targets": (
                    cls._repair_priority_top_component_targets(
                        rows,
                        statuses={"probe"},
                    )
                ),
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _repair_priority_component_targets(
        cls,
        metrics: list[dict[str, Any]],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        candidates = [row for row in metrics if isinstance(row, dict)]
        candidates.sort(key=cls._repair_loop_effectiveness_row_sort_key)
        return _compact_prompt_repair_component_targets(candidates, limit=limit)

    @classmethod
    def _repair_priority_top_component_targets(
        cls,
        components: list[dict[str, Any]],
        *,
        statuses: set[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        clean_statuses = {
            str(status or "").strip().lower()
            for status in (statuses or set())
            if str(status or "").strip()
        }
        targets: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for component in components:
            component_name = str(component.get("component") or "").strip()[:120]
            for target in _compact_prompt_repair_component_targets(
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
        targets.sort(key=cls._repair_loop_effectiveness_row_sort_key)
        return targets[: max(int(limit), 0)]

    @staticmethod
    def _repair_priority_metric_status_counts(
        metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        statuses: list[str] = []
        for row in metrics:
            statuses.append(str(row.get("status") or "").strip().lower())
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

    @staticmethod
    def _evidence_values(evidence: dict[str, Any], key: str) -> list[str]:
        values = evidence.get(key)
        if isinstance(values, list):
            return [str(item).strip() for item in values if str(item).strip()]
        value = str(values or "").strip()
        return [value] if value else []

    @staticmethod
    def _value_at(values: list[str], index: int) -> str:
        if not values:
            return ""
        if index < len(values):
            return values[index]
        return values[-1]

    @staticmethod
    def _ratio(part: int, whole: int) -> float:
        if whole <= 0:
            return 0.0
        return float(part) / float(whole)

    def latest_mode_recommendations_by_scope(
        self,
        *,
        refresh: bool = True,
    ) -> dict[str, dict[str, Any]]:
        self.wiki.initialize()
        if refresh:
            self.project_mode_recommendations()
        by_scope: dict[str, dict[str, Any]] = {}
        with self.wiki._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_mode_recommendations
                WHERE decision_scope != ''
                ORDER BY created_at DESC, recommendation_id DESC
                """
            ).fetchall()
        for row in rows:
            recommendation = self._mode_recommendation_from_row(row)
            scope = str(recommendation.get("decision_scope") or "")
            if scope and scope not in by_scope:
                by_scope[scope] = recommendation
        return by_scope

    def project_wiki_application_coverage(
        self,
        *,
        decision_scope: str | None = None,
    ) -> dict[str, Any]:
        status = self._wiki_application_status()
        clean_scope = str(decision_scope or "").strip().lower()
        if not clean_scope:
            return {
                "status": str(status.get("wiki_application_health") or "missing"),
                "decision_scope": "all",
                "coverage": dict(status.get("wiki_application_coverage") or {}),
                "alerts": list(status.get("wiki_application_alerts") or []),
            }
        scope_rows = [
            dict(row)
            for row in list(status.get("wiki_application_scopes") or [])
            if isinstance(row, dict)
            and str(row.get("decision_scope") or "").strip().lower() == clean_scope
        ]
        alerts = [
            dict(alert)
            for alert in list(status.get("wiki_application_alerts") or [])
            if isinstance(alert, dict)
            and str(alert.get("decision_scope") or "").strip().lower() == clean_scope
        ]
        if not scope_rows:
            return {
                "status": "missing",
                "decision_scope": clean_scope,
                "coverage": {
                    "decision_scope": clean_scope,
                    "decision_link_count": 0,
                    "decision_links_with_selected_wiki_pages": 0,
                    "decision_links_with_selected_wiki_pages_pct": 0.0,
                    "selection_outcome_count": 0,
                    "selection_outcomes_with_selected_wiki_page": 0,
                    "selection_outcomes_with_selected_wiki_page_pct": 0.0,
                    "selection_outcomes_with_quality_warnings": 0,
                    "selection_outcome_attribution_filtered_count": 0,
                    "closed_block_outcomes_without_horizon": 0,
                    "closed_block_outcomes_without_horizon_pct": 0.0,
                },
                "alerts": [],
            }
        return {
            "status": "warning" if alerts else "ok",
            "decision_scope": clean_scope,
            "coverage": scope_rows[0],
            "alerts": alerts,
        }

    def _wiki_application_status(self) -> dict[str, Any]:
        self.wiki.initialize()
        with self.wiki._connect() as conn:
            link_rows = conn.execute(
                """
                SELECT
                    decision_scope,
                    COUNT(*) AS decision_link_count,
                    SUM(
                        CASE
                            WHEN metadata_json LIKE '%selected_wiki_pages%'
                            THEN 1 ELSE 0
                        END
                    ) AS selected_page_link_count
                FROM wiki_decision_links
                GROUP BY decision_scope
                ORDER BY decision_scope
                """
            ).fetchall()
            raw_outcome_rows = conn.execute(
                """
                SELECT
                    decision_scope, page_id, venue, symbol, block_id, horizon,
                    outcome_kind, outcome_status, evidence_json
                FROM wiki_selection_outcomes
                ORDER BY decision_scope
                """
            ).fetchall()

        outcomes_by_scope = self._wiki_application_outcome_counts(raw_outcome_rows)
        scope_rows: list[dict[str, Any]] = []
        total_links = 0
        total_selected_links = 0
        total_outcomes = 0
        total_selected_outcomes = 0
        total_quality_warning_outcomes = 0
        total_attribution_filtered = 0
        total_closed_block_outcomes = 0
        total_closed_block_without_horizon = 0
        alerts: list[dict[str, Any]] = []
        for row in link_rows:
            scope = str(row["decision_scope"] or "")
            link_count = int(row["decision_link_count"] or 0)
            selected_link_count = int(row["selected_page_link_count"] or 0)
            outcome_row = outcomes_by_scope.get(scope)
            outcome_count = (
                int(outcome_row["selection_outcome_count"] or 0)
                if outcome_row is not None
                else 0
            )
            selected_outcome_count = (
                int(outcome_row["selected_page_outcome_count"] or 0)
                if outcome_row is not None
                else 0
            )
            quality_warning_outcome_count = (
                int(outcome_row["quality_warning_outcome_count"] or 0)
                if outcome_row is not None
                else 0
            )
            attribution_filtered_count = (
                int(outcome_row["attribution_filtered_count"] or 0)
                if outcome_row is not None
                else 0
            )
            closed_block_count = (
                int(outcome_row["closed_block_outcome_count"] or 0)
                if outcome_row is not None
                else 0
            )
            closed_block_without_horizon_count = (
                int(outcome_row["closed_block_without_horizon_count"] or 0)
                if outcome_row is not None
                else 0
            )
            total_links += link_count
            total_selected_links += selected_link_count
            total_outcomes += outcome_count
            total_selected_outcomes += selected_outcome_count
            total_quality_warning_outcomes += quality_warning_outcome_count
            total_attribution_filtered += attribution_filtered_count
            total_closed_block_outcomes += closed_block_count
            total_closed_block_without_horizon += closed_block_without_horizon_count
            selected_link_pct = self._pct(selected_link_count, link_count)
            selected_outcome_pct = self._pct(selected_outcome_count, outcome_count)
            closed_block_without_horizon_pct = self._pct(
                closed_block_without_horizon_count,
                closed_block_count,
            )
            scope_rows.append(
                {
                    "decision_scope": scope,
                    "decision_link_count": link_count,
                    "decision_links_with_selected_wiki_pages": selected_link_count,
                    "decision_links_with_selected_wiki_pages_pct": selected_link_pct,
                    "selection_outcome_count": outcome_count,
                    "selection_outcomes_with_selected_wiki_page": selected_outcome_count,
                    "selection_outcomes_with_selected_wiki_page_pct": (
                        selected_outcome_pct
                    ),
                    "selection_outcomes_with_quality_warnings": (
                        quality_warning_outcome_count
                    ),
                    "selection_outcome_attribution_filtered_count": (
                        attribution_filtered_count
                    ),
                    "closed_block_outcomes_without_horizon": (
                        closed_block_without_horizon_count
                    ),
                    "closed_block_outcomes_without_horizon_pct": (
                        closed_block_without_horizon_pct
                    ),
                }
            )
            alerts.extend(
                self._wiki_application_alerts_for_scope(
                    decision_scope=scope,
                    selected_link_pct=selected_link_pct,
                    selected_outcome_pct=selected_outcome_pct,
                    closed_block_without_horizon_pct=(
                        closed_block_without_horizon_pct
                    ),
                )
            )

        return {
            "wiki_application_coverage": {
                "decision_link_count": total_links,
                "decision_links_with_selected_wiki_pages": total_selected_links,
                "decision_links_with_selected_wiki_pages_pct": self._pct(
                    total_selected_links,
                    total_links,
                ),
                "selection_outcome_count": total_outcomes,
                "selection_outcomes_with_selected_wiki_page": total_selected_outcomes,
                "selection_outcomes_with_selected_wiki_page_pct": self._pct(
                    total_selected_outcomes,
                    total_outcomes,
                ),
                "selection_outcomes_with_quality_warnings": (
                    total_quality_warning_outcomes
                ),
                "selection_outcome_attribution_filtered_count": (
                    total_attribution_filtered
                ),
                "closed_block_outcomes_without_horizon": (
                    total_closed_block_without_horizon
                ),
                "closed_block_outcomes_without_horizon_pct": self._pct(
                    total_closed_block_without_horizon,
                    total_closed_block_outcomes,
                ),
            },
            "wiki_application_scopes": scope_rows,
            "wiki_application_health": "warning" if alerts else "ok",
            "wiki_application_alerts": alerts,
        }

    def _wiki_application_outcome_counts(self, rows: list[Any]) -> dict[str, dict[str, int]]:
        by_scope: dict[str, dict[str, int]] = {}
        for row in rows:
            evidence = _json_loads(str(row["evidence_json"]), {})
            if not isinstance(evidence, dict):
                evidence = {}
            scope = str(row["decision_scope"] or "")
            counts = by_scope.setdefault(
                scope,
                {
                    "selection_outcome_count": 0,
                    "selected_page_outcome_count": 0,
                    "quality_warning_outcome_count": 0,
                    "attribution_filtered_count": 0,
                    "closed_block_outcome_count": 0,
                    "closed_block_without_horizon_count": 0,
                },
            )
            counts["selection_outcome_count"] += 1
            if not evidence.get("selected_wiki_page"):
                continue
            outcome_row = {
                **dict(row),
                "evidence": evidence,
            }
            if not self._outcome_row_is_attributable(outcome_row):
                counts["attribution_filtered_count"] += 1
                continue
            counts["selected_page_outcome_count"] += 1
            if str(row["outcome_kind"] or "") == "closed_block":
                counts["closed_block_outcome_count"] += 1
                if not str(row["horizon"] or "").strip():
                    counts["closed_block_without_horizon_count"] += 1
            if evidence.get("quality_warnings") or (
                isinstance(evidence.get("selected_wiki_page"), dict)
                and evidence["selected_wiki_page"].get("quality_warnings")
            ):
                counts["quality_warning_outcome_count"] += 1
        return by_scope

    @staticmethod
    def _pct(part: int, whole: int) -> float:
        if whole <= 0:
            return 0.0
        return round(float(part) * 100.0 / float(whole), 1)

    @classmethod
    def _wiki_application_alerts_for_scope(
        cls,
        *,
        decision_scope: str,
        selected_link_pct: float,
        selected_outcome_pct: float,
        closed_block_without_horizon_pct: float = 0.0,
    ) -> list[dict[str, Any]]:
        scope = str(decision_scope or "unknown")
        alerts: list[dict[str, Any]] = []
        if selected_link_pct < 80.0:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "wiki_selected_pages_missing",
                    "decision_scope": scope,
                    "message": (
                        f"{scope} wiki selected page trace coverage is "
                        f"{selected_link_pct:.1f}%; manager prompts need "
                        "selected_wiki_pages metadata."
                    ),
                    "action": "project_decision_links_or_restart_stale_runner",
                }
            )
        if selected_outcome_pct < 60.0:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "wiki_outcome_feedback_missing",
                    "decision_scope": scope,
                    "message": (
                        f"{scope} wiki outcome feedback coverage is "
                        f"{selected_outcome_pct:.1f}%; closed/error block outcomes "
                        "need selected_wiki_page evidence."
                    ),
                    "action": "project_selection_outcomes_and_page_effectiveness",
                }
            )
        if closed_block_without_horizon_pct > 0.0:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "wiki_outcome_horizon_missing",
                    "decision_scope": scope,
                    "message": (
                        f"{scope} wiki closed block outcomes without horizon are "
                        f"{closed_block_without_horizon_pct:.1f}%; closed block "
                        "feedback must be attributed to a horizon/lane."
                    ),
                    "action": "project_selection_outcomes_and_page_effectiveness",
                }
            )
        return alerts

    @staticmethod
    def _quality_warning_status_rows(
        rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                float(row.get("helpful_score") or 0.0),
                -int(row.get("sample_count") or 0),
                str(row.get("page_id") or ""),
            ),
        )
        result: list[dict[str, Any]] = []
        for row in sorted_rows[: max(int(limit), 0)]:
            page_id = str(row.get("page_id") or "")
            result.append(
                {
                    key: value
                    for key, value in {
                        "warning": page_id.removeprefix("quality_warning."),
                        "page_id": page_id,
                        "decision_scope": str(row.get("decision_scope") or ""),
                        "venue": str(row.get("venue") or ""),
                        "horizon": str(row.get("horizon") or ""),
                        "sample_count": int(row.get("sample_count") or 0),
                        "win_rate": float(row.get("win_rate") or 0.0),
                        "expectancy": float(row.get("expectancy") or 0.0),
                        "helpful_score": float(row.get("helpful_score") or 0.0),
                        "confidence": float(row.get("confidence") or 0.0),
                        "reasons": [
                            str(item)[:180]
                            for item in list(row.get("reasons") or [])[:4]
                            if str(item).strip()
                        ],
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        return result

    @staticmethod
    def _quality_warning_source_status_rows(
        rows: list[dict[str, Any]],
        *,
        limit: int,
        strongest_first: bool = False,
    ) -> list[dict[str, Any]]:
        helpful_direction = -1 if strongest_first else 1
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                helpful_direction * float(row.get("helpful_score") or 0.0),
                -int(row.get("sample_count") or 0),
                str(row.get("page_id") or ""),
            ),
        )
        result: list[dict[str, Any]] = []
        for row in sorted_rows[: max(int(limit), 0)]:
            reasons = [
                str(item)[:180]
                for item in list(row.get("reasons") or [])[:6]
                if str(item).strip()
            ]
            quality_warnings: list[str] = []
            prior_statuses: list[str] = []
            for reason in reasons:
                if reason.startswith("quality_warning:"):
                    warning = reason.removeprefix("quality_warning:").strip()
                    if warning and warning not in quality_warnings:
                        quality_warnings.append(warning)
                if reason.startswith("quality_warning_source_prior_status:"):
                    status = reason.removeprefix(
                        "quality_warning_source_prior_status:"
                    ).strip()
                    if status and status not in prior_statuses:
                        prior_statuses.append(status)
            result.append(
                {
                    key: value
                    for key, value in {
                        "page_id": str(row.get("page_id") or ""),
                        "decision_scope": str(row.get("decision_scope") or ""),
                        "venue": str(row.get("venue") or ""),
                        "horizon": str(row.get("horizon") or ""),
                        "status": str(row.get("status") or "").strip().lower(),
                        "sample_count": int(row.get("sample_count") or 0),
                        "win_rate": float(row.get("win_rate") or 0.0),
                        "expectancy": float(row.get("expectancy") or 0.0),
                        "helpful_score": float(row.get("helpful_score") or 0.0),
                        "confidence": float(row.get("confidence") or 0.0),
                        "quality_warnings": quality_warnings,
                        "prior_statuses": prior_statuses,
                        "reasons": reasons,
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        return result

    def _decision_link(self, link_id: str) -> dict[str, Any] | None:
        with self.wiki._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wiki_decision_links
                WHERE link_id = ?
                """,
                (str(link_id),),
            ).fetchone()
        return self._decision_link_from_row(row) if row is not None else None

    def _project_decision_links_from_db(
        self,
        *,
        path: Path,
        scope: str,
        venue: str,
        decision_type: str,
        table: str,
        limit: int,
    ) -> dict[str, int]:
        with sqlite3.connect(path) as source_conn:
            source_conn.row_factory = sqlite3.Row
            if not self._source_table_exists(source_conn, table):
                raise sqlite3.OperationalError(f"missing table: {table}")
            columns = self._source_table_columns(source_conn, table)
            response_select = (
                "response_json"
                if "response_json" in columns
                else "'{}' AS response_json"
            )
            actions_select = (
                "actions_json"
                if "actions_json" in columns
                else "'{}' AS actions_json"
            )
            error_select = (
                "error_message"
                if "error_message" in columns
                else "'' AS error_message"
            )
            market_session_select = (
                "market_session"
                if "market_session" in columns
                else "'' AS market_session"
            )
            query_select = "query" if "query" in columns else "'' AS query"
            source_snapshot_select = (
                "source_snapshot_json"
                if "source_snapshot_json" in columns
                else "'{}' AS source_snapshot_json"
            )
            rows = source_conn.execute(
                f"""
                SELECT id, run_at, status, mode, model, prompt_json,
                       {response_select}, {actions_select}, {error_select},
                       {market_session_select}, {query_select},
                       {source_snapshot_select}
                FROM {table}
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()

        inserted = 0
        with self.wiki._connect() as conn:
            for row in rows:
                prompt = _json_loads(str(row["prompt_json"]), {})
                if not isinstance(prompt, dict):
                    continue
                response = _json_loads(str(row["response_json"]), {})
                if not isinstance(response, dict):
                    response = {}
                actions = _json_loads(str(row["actions_json"]), {})
                if not isinstance(actions, dict):
                    actions = {}
                metadata = self._prompt_wiki_application_metadata(prompt)
                selection_run_id = str(metadata.get("selection_run_id") or "").strip()
                selected_page_ids = self._applied_wiki_page_ids(metadata)
                if not selection_run_id or not selected_page_ids:
                    continue
                link_id = f"wiki-link:{scope}:{decision_type}:{row['id']}:{selection_run_id}"
                metadata_json = self._projected_decision_link_metadata(
                    path=path,
                    table=table,
                    row=row,
                    prompt=prompt,
                    response=response,
                    actions=actions,
                    selected_page_ids=selected_page_ids,
                )
                existing_row = conn.execute(
                    """
                    SELECT metadata_json
                    FROM wiki_decision_links
                    WHERE link_id = ?
                    """,
                    (link_id,),
                ).fetchone()
                if existing_row is not None:
                    existing_metadata = _json_loads(
                        str(existing_row["metadata_json"]),
                        {},
                    )
                    if isinstance(existing_metadata, dict):
                        metadata_json = self._preserve_richer_selected_wiki_pages(
                            new_metadata_json=metadata_json,
                            existing_metadata=existing_metadata,
                        )
                horizon = (
                    self._projected_market_judgment_horizon(row)
                    if decision_type == "market_judgment"
                    else ""
                )
                cursor = conn.execute(
                    """
                    INSERT INTO wiki_decision_links (
                        link_id, selection_run_id, manager_run_id, decision_scope,
                        decision_type, symbol, block_id, venue, horizon, action,
                        prompt_mode, selected_pages_json, metadata_json, linked_at
                    ) VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(link_id) DO UPDATE SET
                        selection_run_id = excluded.selection_run_id,
                        manager_run_id = excluded.manager_run_id,
                        decision_scope = excluded.decision_scope,
                        decision_type = excluded.decision_type,
                        venue = excluded.venue,
                        horizon = excluded.horizon,
                        action = excluded.action,
                        prompt_mode = excluded.prompt_mode,
                        selected_pages_json = excluded.selected_pages_json,
                        metadata_json = excluded.metadata_json,
                        linked_at = excluded.linked_at
                    """,
                    (
                        link_id,
                        selection_run_id,
                        f"{scope}:{decision_type}:{row['id']}",
                        scope,
                        decision_type,
                        venue,
                        horizon,
                        "manager_run",
                        str(metadata.get("prompt_mode") or ""),
                        _json_dumps(selected_page_ids),
                        metadata_json,
                        str(row["run_at"] or _utc_now_iso()),
                    ),
                )
                inserted += int(cursor.rowcount or 0)
        return {"scanned_count": len(rows), "inserted_count": inserted}

    def _projected_decision_link_metadata(
        self,
        *,
        path: Path,
        table: str,
        row: sqlite3.Row,
        prompt: dict[str, Any],
        response: dict[str, Any],
        actions: dict[str, Any],
        selected_page_ids: list[str],
    ) -> str:
        metadata = self._prompt_wiki_application_metadata(prompt)
        selected_page_summary_horizons: list[str] = []
        if table == "judgment_runs":
            selected_page_summary_horizons.append(
                self._projected_market_judgment_horizon(row)
            )
        projected = {
            "source_db_path": str(path),
            "source_table": table,
            "source_row_id": int(row["id"]),
            "run_at": str(row["run_at"] or ""),
            "run_status": str(row["status"] or ""),
            "mode": str(row["mode"] or ""),
            "model": str(row["model"] or ""),
            "error_message": str(row["error_message"] or "")[:360],
            "decision_inputs": [
                str(item) for item in list(prompt.get("decision_inputs") or [])[:40]
            ],
            "opportunity_research_brief": self._prompt_opportunity_summary(prompt),
            "proactive_decision_pressure": self._prompt_proactive_pressure_summary(
                prompt
            ),
            "validation_repair": self._prompt_validation_repair_summary(prompt),
            "jue_wiki_repair_contract": self._prompt_repair_contract_summary(prompt),
            "jue_wiki_action_pressure_contract": (
                self._prompt_action_pressure_contract_summary(prompt)
            ),
            "jue_wiki_validation_repair_contract": (
                self._prompt_validation_repair_contract_summary(prompt)
            ),
            "jue_wiki_decision_adjustment_audit_contract": (
                self._prompt_decision_adjustment_audit_contract_summary(prompt)
            ),
            "jue_wiki_trust_profile": (
                self._prompt_wiki_trust_profile_summary(metadata)
            ),
            "jue_wiki_trust_profile_effectiveness": (
                self._prompt_wiki_trust_profile_effectiveness_summary(metadata)
            ),
            "jue_wiki_quality_summary": self._prompt_wiki_quality_summary(metadata),
            "jue_wiki_quality_pressure_action_plan": (
                self._prompt_wiki_quality_pressure_action_plan(metadata)
            ),
            "jue_wiki_quality_warning_source_summary": (
                self._prompt_wiki_quality_warning_source_summary(metadata)
            ),
            "selected_wiki_pages": self._prompt_selected_wiki_pages_summary(
                prompt,
                selected_page_ids,
                horizons=selected_page_summary_horizons,
            ),
            "manager_response": self._manager_response_summary(response, actions),
            "execution_gate": self._prompt_execution_gate_summary(prompt),
            "budget_report": metadata.get("budget_report") or {},
        }
        decision_adjustments = self._prompt_wiki_decision_adjustments_summary(
            metadata
        )
        if decision_adjustments:
            projected["jue_wiki_decision_adjustments"] = decision_adjustments
        market_context = self._projected_market_judgment_context(row)
        if market_context:
            projected["market_judgment_context"] = market_context
        return _json_dumps(projected)

    @classmethod
    def _projected_market_judgment_horizon(cls, row: sqlite3.Row) -> str:
        market_session = str(cls._sqlite_row_value(row, "market_session") or "").strip()
        if not market_session:
            return ""
        clean = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in market_session.lower()
        )
        clean = "_".join(part for part in clean.split("_") if part)
        return f"market_session:{clean}" if clean else ""

    @classmethod
    def _projected_market_judgment_context(
        cls,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        market_session = cls._sqlite_row_value(row, "market_session")
        query = cls._sqlite_row_value(row, "query")
        snapshot = _json_loads(cls._sqlite_row_value(row, "source_snapshot_json"), {})
        snapshot_summary = cls._compact_market_judgment_source_snapshot(snapshot)
        return {
            key: value
            for key, value in {
                "market_session": str(market_session or "")[:120],
                "query": str(query or "")[:240],
                "source_snapshot": snapshot_summary,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _sqlite_row_value(row: sqlite3.Row, key: str) -> Any:
        try:
            if key not in row.keys():
                return ""
            return row[key]
        except (KeyError, IndexError):
            return ""

    @classmethod
    def _compact_market_judgment_source_snapshot(
        cls,
        snapshot: Any,
    ) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {}
        focus_symbols = cls._compact_snapshot_symbols(
            snapshot.get("focus_symbols")
            or snapshot.get("target_symbols")
            or snapshot.get("symbols")
        )
        clock = cls._compact_market_snapshot_clock(
            snapshot.get("clock") or snapshot.get("market_clock")
        )
        account = cls._compact_market_snapshot_account(snapshot.get("account"))
        quote_summary = cls._compact_market_snapshot_quotes(snapshot.get("quotes"))
        return {
            key: value
            for key, value in {
                "focus_symbols": focus_symbols,
                "clock": clock,
                "account": account,
                **quote_summary,
            }.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _compact_snapshot_symbols(
        cls,
        values: Any,
        *,
        limit: int = 12,
    ) -> list[str]:
        if isinstance(values, dict):
            source_values = list(values.keys())
        elif isinstance(values, list):
            source_values = values
        else:
            source_values = []
        symbols: list[str] = []
        for value in source_values:
            if isinstance(value, dict):
                raw = value.get("symbol") or value.get("code") or value.get("ticker")
            else:
                raw = value
            symbol = cls._normalize_outcome_symbol(raw)
            if symbol and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= int(limit):
                break
        return symbols

    @staticmethod
    def _compact_market_snapshot_clock(clock: Any) -> dict[str, Any]:
        if not isinstance(clock, dict):
            return {}
        return {
            key: value
            for key, value in {
                "session": str(clock.get("session") or "")[:80],
                "phase": str(clock.get("phase") or "")[:80],
                "is_open": clock.get("is_open")
                if isinstance(clock.get("is_open"), bool)
                else None,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _compact_market_snapshot_account(account: Any) -> dict[str, Any]:
        if not isinstance(account, dict):
            return {}
        positions = account.get("positions")
        position_count = len(positions) if isinstance(positions, list) else 0
        compact: dict[str, Any] = {
            "status": str(account.get("status") or "")[:80],
            "position_count": position_count,
        }
        for key in ("cash_krw", "orderable_cash_krw", "total_equity_krw"):
            value = account.get(key)
            if value not in (None, "", [], {}):
                compact[key] = _safe_float(value)
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _compact_market_snapshot_quotes(cls, quotes: Any) -> dict[str, Any]:
        if isinstance(quotes, dict):
            quote_rows = list(quotes.values())
            quote_symbols = cls._compact_snapshot_symbols(list(quotes.keys()))
        elif isinstance(quotes, list):
            quote_rows = quotes
            quote_symbols = cls._compact_snapshot_symbols(quotes)
        else:
            return {}
        return {
            key: value
            for key, value in {
                "quote_count": len(quote_rows),
                "quote_symbols": quote_symbols,
            }.items()
            if value not in (None, "", {})
        }

    @classmethod
    def _preserve_richer_selected_wiki_pages(
        cls,
        *,
        new_metadata_json: str,
        existing_metadata: dict[str, Any],
    ) -> str:
        new_metadata = _json_loads(new_metadata_json, {})
        if not isinstance(new_metadata, dict):
            return new_metadata_json
        existing_summary = (
            existing_metadata.get("selected_wiki_pages")
            if isinstance(existing_metadata.get("selected_wiki_pages"), dict)
            else {}
        )
        new_summary = (
            new_metadata.get("selected_wiki_pages")
            if isinstance(new_metadata.get("selected_wiki_pages"), dict)
            else {}
        )
        existing_score = cls._selected_wiki_pages_summary_score(existing_summary)
        new_score = cls._selected_wiki_pages_summary_score(new_summary)
        if existing_score >= new_score and not cls._selected_wiki_pages_has_complementary_metadata(
            new_summary=new_summary,
            existing_summary=existing_summary,
        ):
            new_metadata["selected_wiki_pages"] = dict(existing_summary)
            for key in (
                "selected_wiki_pages_backfilled_at",
                "selected_wiki_pages_backfill_source",
            ):
                if existing_metadata.get(key) not in (None, "", [], {}):
                    new_metadata[key] = existing_metadata[key]
        else:
            merged_summary = cls._merge_selected_wiki_pages_summary(
                new_summary=new_summary,
                existing_summary=existing_summary,
            )
            if merged_summary:
                new_metadata["selected_wiki_pages"] = merged_summary
        return _json_dumps(new_metadata)

    @staticmethod
    def _selected_wiki_pages_has_complementary_metadata(
        *,
        new_summary: dict[str, Any],
        existing_summary: dict[str, Any],
    ) -> bool:
        if not isinstance(new_summary, dict):
            return False
        existing = existing_summary if isinstance(existing_summary, dict) else {}
        material_summary_keys = {
            "quality_warning_count",
            "repair_queue_count",
            "repair_action_types",
            "repair_decision_uses",
            "repair_quality_warnings",
            "repair_diagnostic_reasons",
            "repair_targets",
            "repair_horizon_gap_total",
            "repair_horizon_gap_max_pct",
            "usage_guidance_effectiveness_status_counts",
            "memory_card_quality_effectiveness_status_counts",
            "quality_warning_source_effectiveness_status_counts",
            "quality_warning_effectiveness_status_counts",
            "effectiveness_fallback_counts",
            "effectiveness_fallback_page_ids",
            "effectiveness_fallback_symbols",
            "effectiveness_fallback_page_symbols",
            "effectiveness_fallback_markets",
            "effectiveness_fallback_sides",
            "effectiveness_fallback_requested_horizons",
            "effectiveness_attention_page_ids",
            "effectiveness_attention_items",
        }
        for key in material_summary_keys:
            new_value = new_summary.get(key)
            existing_value = existing.get(key)
            if JueWikiApplicationService._selected_wiki_pages_has_material_value(
                new_value
            ) and (
                not JueWikiApplicationService._selected_wiki_pages_has_material_value(
                    existing_value
                )
                or JueWikiApplicationService._selected_wiki_pages_has_added_material(
                    new_value,
                    existing_value,
                )
            ):
                return True
        material_page_keys = {
            "repair_queue",
            "usage_guidance",
            "usage_guidance_effectiveness",
            "memory_card_quality",
            "memory_card_quality_effectiveness",
            "quality_warning_source_effectiveness",
            "quality_warning_effectiveness",
            "quality_warning_effectiveness_statuses",
            "evidence_quality",
        }
        existing_by_page_id = {
            page_ids[0]: row
            for row in list(existing.get("pages") or [])
            if isinstance(row, dict)
            for page_ids in [JueWikiApplicationService._clean_page_ids([row.get("page_id")])]
            if page_ids
        }
        for row in list(new_summary.get("pages") or []):
            if not isinstance(row, dict):
                continue
            clean_page_ids = JueWikiApplicationService._clean_page_ids(
                [row.get("page_id")]
            )
            if not clean_page_ids:
                continue
            existing_row = existing_by_page_id.get(clean_page_ids[0])
            if not isinstance(existing_row, dict):
                existing_row = {}
            for key in material_page_keys:
                new_value = row.get(key)
                existing_value = existing_row.get(key)
                if JueWikiApplicationService._selected_wiki_pages_has_material_value(
                    new_value
                ) and (
                    not JueWikiApplicationService._selected_wiki_pages_has_material_value(
                        existing_value
                    )
                    or JueWikiApplicationService._selected_wiki_pages_has_added_material(
                        new_value,
                        existing_value,
                    )
                ):
                    return True
        return False

    @staticmethod
    def _selected_wiki_pages_has_material_value(value: Any) -> bool:
        value = JueWikiApplicationService._clean_prompt_merge_value(value)
        if value in (None, "", [], {}):
            return False
        if isinstance(value, (int, float)) and value <= 0:
            return False
        return True

    @staticmethod
    def _selected_wiki_pages_has_added_material(
        new_value: Any,
        existing_value: Any,
    ) -> bool:
        new_value = JueWikiApplicationService._clean_prompt_merge_value(new_value)
        existing_value = JueWikiApplicationService._clean_prompt_merge_value(
            existing_value
        )
        if new_value in (None, "", [], {}):
            return False
        if isinstance(new_value, list) and isinstance(existing_value, list):
            return any(
                JueWikiApplicationService._selected_wiki_pages_has_material_value(item)
                and item not in existing_value
                for item in new_value
            )
        if isinstance(new_value, dict) and isinstance(existing_value, dict):
            for key, value in new_value.items():
                clean_key = _clean_prompt_semantic_text(key)
                if not clean_key:
                    continue
                value = JueWikiApplicationService._clean_prompt_merge_value(value)
                if value in (None, "", [], {}):
                    continue
                existing_item = existing_value.get(clean_key)
                if clean_key not in existing_value or existing_item in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return True
                if isinstance(value, (int, float)) or isinstance(
                    existing_item,
                    (int, float),
                ):
                    if _safe_float(value) > _safe_float(existing_item):
                        return True
                    continue
                if isinstance(value, (list, dict)) and isinstance(
                    existing_item,
                    type(value),
                ):
                    if JueWikiApplicationService._selected_wiki_pages_has_added_material(
                        value,
                        existing_item,
                    ):
                        return True
        return False

    @classmethod
    def _merge_selected_wiki_pages_summary(
        cls,
        *,
        new_summary: dict[str, Any],
        existing_summary: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(new_summary, dict) and not isinstance(
            existing_summary,
            dict,
        ):
            return {}
        merged = (
            cls._clean_selected_wiki_pages_summary(new_summary)
            if isinstance(new_summary, dict)
            else {}
        )
        existing = existing_summary if isinstance(existing_summary, dict) else {}
        for key, value in existing.items():
            if value in (None, "", [], {}):
                continue
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
                continue
            if key == "pages":
                merged[key] = cls._merge_selected_wiki_page_rows(
                    merged.get(key),
                    value,
                )
            elif key == "effectiveness_fallback_page_symbols":
                merged[key] = cls._merge_fallback_page_symbol_rows(
                    merged.get(key),
                    value,
                )
            elif isinstance(merged.get(key), list) and isinstance(value, list):
                merged[key] = cls._merge_unique_prompt_list(merged.get(key), value)
            elif isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = cls._merge_count_dicts(merged.get(key), value)
            elif isinstance(merged.get(key), (int, float)) or isinstance(
                value,
                (int, float),
            ):
                merged[key] = max(_safe_float(merged.get(key)), _safe_float(value))
        return {
            key: value
            for key, value in cls._clean_selected_wiki_pages_summary(merged).items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _clean_selected_wiki_pages_summary(
        cls,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in summary.items():
            if value in (None, "", [], {}):
                continue
            if key == "pages":
                value = cls._merge_selected_wiki_page_rows(value, [])
            elif key == "effectiveness_fallback_page_symbols":
                value = cls._merge_fallback_page_symbol_rows(value, [])
            elif isinstance(value, list):
                value = cls._merge_unique_prompt_list([], value)
            elif isinstance(value, dict):
                value = cls._merge_count_dicts({}, value)
            else:
                value = cls._clean_prompt_merge_value(value)
            if value not in (None, "", [], {}):
                clean[key] = value
        return clean

    @classmethod
    def _merge_selected_wiki_page_rows(cls, current: Any, incoming: Any) -> list[Any]:
        by_page_id: dict[str, dict[str, Any]] = {}
        for source in (current, incoming):
            if not isinstance(source, list):
                continue
            for row in source:
                if not isinstance(row, dict):
                    continue
                clean_page_ids = cls._clean_page_ids([row.get("page_id")])
                if not clean_page_ids:
                    continue
                page_id = clean_page_ids[0]
                clean_row = cls._clean_prompt_merge_dict(row)
                clean_row["page_id"] = page_id
                existing = by_page_id.get(page_id)
                by_page_id[page_id] = (
                    cls._merge_selected_wiki_page_row(existing, clean_row)
                    if existing
                    else clean_row
                )
        seen_page_ids: set[str] = set()
        ordered: list[Any] = []
        for source in (current, incoming):
            if not isinstance(source, list):
                continue
            for row in source:
                if not isinstance(row, dict):
                    continue
                clean_page_ids = cls._clean_page_ids([row.get("page_id")])
                if not clean_page_ids:
                    continue
                page_id = clean_page_ids[0]
                if page_id in by_page_id and page_id not in seen_page_ids:
                    ordered.append(by_page_id[page_id])
                    seen_page_ids.add(page_id)
        return ordered

    @classmethod
    def _merge_fallback_page_symbol_rows(cls, current: Any, incoming: Any) -> list[Any]:
        symbols_by_page_id: dict[str, list[str]] = {}
        row_by_page_id: dict[str, dict[str, Any]] = {}
        for source in (current, incoming):
            if not isinstance(source, list):
                continue
            for row in source:
                if not isinstance(row, dict):
                    continue
                clean_page_ids = cls._clean_page_ids([row.get("page_id")])
                if not clean_page_ids:
                    continue
                page_id = clean_page_ids[0]
                merged_row = row_by_page_id.setdefault(page_id, {"page_id": page_id})
                for key, value in row.items():
                    if key in {"page_id", "symbols"} or value in (None, "", [], {}):
                        continue
                    value = cls._clean_prompt_merge_value(value)
                    if value in (None, "", [], {}):
                        continue
                    if key not in merged_row or merged_row.get(key) in (
                        None,
                        "",
                        [],
                        {},
                    ):
                        merged_row[key] = value
                page_symbols = symbols_by_page_id.setdefault(page_id, [])
                for symbol in _compact_prompt_string_list(
                    row.get("symbols"),
                    limit=8,
                    max_len=40,
                ):
                    clean_symbol = cls._normalize_selected_page_symbol(symbol)
                    if clean_symbol and clean_symbol not in page_symbols:
                        page_symbols.append(clean_symbol)
        seen_page_ids: set[str] = set()
        ordered: list[Any] = []
        for source in (current, incoming):
            if not isinstance(source, list):
                continue
            for row in source:
                if not isinstance(row, dict):
                    continue
                clean_page_ids = cls._clean_page_ids([row.get("page_id")])
                if not clean_page_ids:
                    continue
                page_id = clean_page_ids[0]
                if page_id in row_by_page_id and page_id not in seen_page_ids:
                    merged_row = dict(row_by_page_id[page_id])
                    symbols = symbols_by_page_id.get(page_id) or []
                    if symbols:
                        merged_row["symbols"] = symbols[:8]
                    ordered.append(
                        {
                            key: value
                            for key, value in merged_row.items()
                            if value not in (None, "", [], {})
                        }
                    )
                    seen_page_ids.add(page_id)
        return ordered

    @classmethod
    def _merge_selected_wiki_page_row(
        cls,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = cls._clean_prompt_merge_dict(current)
        for key, raw_value in incoming.items():
            value = cls._clean_prompt_merge_value(raw_value)
            if value in (None, "", [], {}):
                continue
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
            elif isinstance(merged.get(key), list) and isinstance(value, list):
                merged[key] = cls._merge_unique_prompt_list(merged.get(key), value)
            elif isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = cls._merge_prompt_dict(merged.get(key), value)
            elif isinstance(merged.get(key), (int, float)) or isinstance(
                value,
                (int, float),
            ):
                merged[key] = max(_safe_float(merged.get(key)), _safe_float(value))
        return {
            key: value
            for key, value in cls._clean_prompt_merge_dict(merged).items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _merge_prompt_dict(
        cls,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = cls._clean_prompt_merge_dict(current)
        for key, raw_value in incoming.items():
            value = cls._clean_prompt_merge_value(raw_value)
            if value in (None, "", [], {}):
                continue
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
            elif isinstance(merged.get(key), list) and isinstance(value, list):
                merged[key] = cls._merge_unique_prompt_list(merged.get(key), value)
            elif isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = cls._merge_prompt_dict(merged.get(key), value)
            elif isinstance(merged.get(key), (int, float)) or isinstance(
                value,
                (int, float),
            ):
                merged[key] = max(_safe_float(merged.get(key)), _safe_float(value))
        return cls._clean_prompt_merge_dict(merged)

    @classmethod
    def _clean_prompt_merge_dict(cls, source: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in source.items():
            clean_key = _clean_prompt_semantic_text(key)
            if not clean_key:
                continue
            clean_value = cls._clean_prompt_merge_value(value)
            if clean_value in (None, "", [], {}):
                continue
            clean[clean_key] = clean_value
        return clean

    @classmethod
    def _clean_prompt_merge_value(cls, value: Any) -> Any:
        if value in (None, "", [], {}):
            return None
        if isinstance(value, str):
            clean = value.strip()
            if not clean or clean.lower() in {"none", "null"}:
                return None
            return clean
        if isinstance(value, list):
            clean_values: list[Any] = []
            for item in value:
                clean_item = cls._clean_prompt_merge_value(item)
                if clean_item in (None, "", [], {}):
                    continue
                if clean_item not in clean_values:
                    clean_values.append(clean_item)
            return clean_values
        if isinstance(value, dict):
            return cls._clean_prompt_merge_dict(value)
        return value

    @staticmethod
    def _merge_count_dicts(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for key, value in current.items():
            clean_key = _clean_prompt_semantic_text(key)
            if not clean_key:
                continue
            clean_value = JueWikiApplicationService._clean_prompt_merge_value(value)
            if clean_value in (None, "", [], {}):
                continue
            merged[clean_key] = clean_value
        for key, raw_value in incoming.items():
            clean_key = _clean_prompt_semantic_text(key)
            if not clean_key:
                continue
            value = JueWikiApplicationService._clean_prompt_merge_value(raw_value)
            if value in (None, "", [], {}):
                continue
            if isinstance(merged.get(clean_key), (int, float)) or isinstance(
                value,
                (int, float),
            ):
                merged[clean_key] = max(
                    _safe_int(merged.get(clean_key)),
                    _safe_int(value),
                )
            elif clean_key not in merged or merged.get(clean_key) in (None, "", [], {}):
                merged[clean_key] = value
        return merged

    @staticmethod
    def _merge_unique_prompt_list(current: Any, incoming: Any) -> list[Any]:
        merged: list[Any] = []
        for value in [
            *(current if isinstance(current, list) else []),
            *(incoming if isinstance(incoming, list) else []),
        ]:
            if value in (None, "", [], {}):
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value or value.lower() in {"none", "null"}:
                    continue
            if value not in merged:
                merged.append(value)
        return merged

    @staticmethod
    def _selected_wiki_pages_effectiveness_score(effectiveness: Any) -> int:
        def meaningful_metric(row: dict[str, Any]) -> bool:
            return bool(
                JueWikiApplicationService._clean_page_ids([row.get("page_id")])
                or _clean_prompt_semantic_text(row.get("status"))
                or _clean_prompt_semantic_text(row.get("warning"))
                or _compact_prompt_string_list(row.get("reasons"), limit=4)
            )

        if isinstance(effectiveness, dict):
            effectiveness = JueWikiApplicationService._clean_prompt_merge_dict(
                effectiveness
            )
            if not effectiveness:
                return 0
            clean_status = _clean_prompt_semantic_text(
                effectiveness.get("status")
            ).lower()
            score = 3 if clean_status else 0
            if clean_status:
                score += 3 if clean_status == "degraded" else 2
            metrics = [
                JueWikiApplicationService._clean_prompt_merge_dict(item)
                for item in list(effectiveness.get("metrics") or [])
                if isinstance(item, dict) and item
            ]
            metrics = [item for item in metrics if meaningful_metric(item)]
            score += len(metrics) * 2
            for metric in metrics:
                if JueWikiApplicationService._clean_page_ids([metric.get("page_id")]):
                    score += 1
                metric_status = _clean_prompt_semantic_text(
                    metric.get("status")
                ).lower()
                if metric_status:
                    score += 2 if metric_status == "degraded" else 1
                score += min(_safe_int(metric.get("sample_count")), 5)
            return score
        if isinstance(effectiveness, list):
            rows = [
                JueWikiApplicationService._clean_prompt_merge_dict(item)
                for item in effectiveness
                if isinstance(item, dict) and item
            ]
            rows = [item for item in rows if meaningful_metric(item)]
            score = len(rows) * 2
            for row in rows:
                clean_status = _clean_prompt_semantic_text(
                    row.get("status")
                ).lower()
                if clean_status:
                    score += 3 if clean_status == "degraded" else 1
                if _clean_prompt_semantic_text(
                    row.get("warning")
                ) or JueWikiApplicationService._clean_page_ids([row.get("page_id")]):
                    score += 1
                score += min(_safe_int(row.get("sample_count")), 5)
            return score
        return 0

    @staticmethod
    def _selected_wiki_pages_summary_score(summary: dict[str, Any]) -> int:
        if not isinstance(summary, dict):
            return 0
        summary = JueWikiApplicationService._clean_selected_wiki_pages_summary(summary)
        score = 0
        raw_rows = summary.get("pages") if isinstance(summary.get("pages"), list) else []
        rows = JueWikiApplicationService._merge_selected_wiki_page_rows([], raw_rows)
        score += len(rows)
        score += _safe_int(summary.get("quality_warning_count")) * 3
        score += _safe_int(summary.get("repair_queue_count")) * 2
        score += len(
            [
                item
                for item in list(summary.get("repair_action_types") or [])
                if _clean_prompt_semantic_text(item)
            ]
        ) * 2
        score += len(
            [
                item
                for item in list(summary.get("repair_decision_uses") or [])
                if _clean_prompt_semantic_text(item)
            ]
        ) * 3
        score += len(
            [
                item
                for item in list(summary.get("repair_quality_warnings") or [])
                if _clean_prompt_semantic_text(item)
            ]
        ) * 2
        score += len(
            [
                item
                for item in list(summary.get("repair_diagnostic_reasons") or [])
                if _clean_prompt_semantic_text(item)
            ]
        ) * 2
        score += len(_compact_prompt_repair_targets(summary.get("repair_targets"))) * 2
        score += min(_safe_int(summary.get("repair_horizon_gap_total")), 12)
        score += min(int(_safe_float(summary.get("repair_horizon_gap_max_pct")) // 25), 4)
        for key in (
            "usage_guidance_effectiveness_status_counts",
            "memory_card_quality_effectiveness_status_counts",
            "quality_warning_source_effectiveness_status_counts",
            "quality_warning_effectiveness_status_counts",
        ):
            counts = summary.get(key) if isinstance(summary.get(key), dict) else {}
            for status, count in counts.items():
                clean_status = _clean_prompt_semantic_text(status).lower()
                if not clean_status:
                    continue
                weight = 3 if clean_status == "degraded" else 2
                score += min(_safe_int(count), 12) * weight
        fallback_counts = (
            summary.get("effectiveness_fallback_counts")
            if isinstance(summary.get("effectiveness_fallback_counts"), dict)
            else {}
        )
        for reason, count in fallback_counts.items():
            if str(reason or "").strip():
                score += min(_safe_int(count), 12) * 3
        score += len(
            JueWikiApplicationService._clean_page_ids(
                list(summary.get("effectiveness_fallback_page_ids") or [])
            )
        ) * 2
        score += len(
            JueWikiApplicationService._summary_fallback_symbols_by_page_id(summary)
        ) * 2
        for key, weight in (
            ("effectiveness_fallback_symbols", 2),
            ("effectiveness_fallback_markets", 1),
            ("effectiveness_fallback_sides", 1),
            ("effectiveness_fallback_requested_horizons", 2),
        ):
            if key == "effectiveness_fallback_symbols":
                item_count = len(
                    [
                        item
                        for item in _prompt_value_items(summary.get(key))
                        if JueWikiApplicationService._normalize_selected_page_symbol(
                            item
                        )
                    ]
                )
            else:
                item_count = len(
                    [
                        item
                        for item in _prompt_value_items(summary.get(key))
                        if _clean_prompt_semantic_text(item)
                    ]
                )
            score += item_count * weight
        score += len(
            JueWikiApplicationService._clean_page_ids(
                list(summary.get("effectiveness_attention_page_ids") or [])
            )
        ) * 2
        score += len(
            [
                item
                for item in list(summary.get("effectiveness_attention_items") or [])
                if isinstance(item, dict) and item
                and JueWikiApplicationService._clean_page_ids([item.get("page_id")])
                and any(
                    _clean_prompt_semantic_text(item.get(key))
                    for key in ("kind", "status", "evidence_id", "warning")
                )
            ]
        ) * 3
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("page_type") or "").strip():
                score += 2
            if _safe_float(row.get("confidence")) > 0:
                score += 2
            score += len(
                [
                    item
                    for item in list(row.get("symbols") or [])
                    if JueWikiApplicationService._normalize_selected_page_symbol(item)
                ]
            )
            score += len(
                [
                    item
                    for item in list(row.get("quality_warnings") or [])
                    if _clean_prompt_semantic_text(item)
                ]
            ) * 3
            if isinstance(row.get("repair_queue"), dict) and row.get("repair_queue"):
                score += 3
                repair_queue = row.get("repair_queue")
                if isinstance(repair_queue, dict):
                    score += len(
                        [
                            item
                            for item in list(
                                repair_queue.get("diagnostic_reasons") or []
                            )
                            if _clean_prompt_semantic_text(item)
                        ]
                    ) * 2
                    score += len(
                        _compact_prompt_repair_targets(
                            repair_queue.get("repair_targets")
                        )
                    ) * 2
                    score += min(
                        _safe_int(
                            repair_queue.get(
                                "closed_block_outcomes_without_horizon"
                            )
                        ),
                        12,
                    )
                    score += min(
                        int(
                            _safe_float(
                                repair_queue.get(
                                    "closed_block_outcomes_without_horizon_pct"
                                )
                            )
                            // 25
                        ),
                        4,
                    )
            for effectiveness_key in (
                "usage_guidance_effectiveness",
                "memory_card_quality_effectiveness",
                "quality_warning_source_effectiveness",
                "quality_warning_effectiveness",
            ):
                score += JueWikiApplicationService._selected_wiki_pages_effectiveness_score(
                    row.get(effectiveness_key)
                )
        return score

    def _prompt_repair_contract_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        contract = (
            prompt.get("jue_wiki_repair_contract")
            if isinstance(prompt.get("jue_wiki_repair_contract"), dict)
            else {}
        )
        if not contract:
            return {}
        priorities: list[dict[str, Any]] = []
        for row in list(contract.get("top_priorities") or [])[:8]:
            if not isinstance(row, dict):
                continue
            compact = {
                "page_id": str(row.get("page_id") or ""),
                "page_type": str(row.get("page_type") or ""),
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
                "priority_type": str(row.get("priority_type") or ""),
                "source_type": str(row.get("source_type") or ""),
                "source_id": str(row.get("source_id") or "")[:180],
                "action_type": str(row.get("action_type") or ""),
                "decision_use": str(row.get("decision_use") or "")[:120],
                "repair_status": str(row.get("repair_status") or ""),
                "quality_status": normalize_jue_wiki_quality_status(
                    row.get("quality_status")
                ),
                "quality_warnings": [
                    str(item)[:120]
                    for item in list(row.get("quality_warnings") or [])[:8]
                    if str(item).strip()
                ],
                "impacted_page_ids": [
                    str(item)[:180]
                    for item in list(row.get("impacted_page_ids") or [])[:12]
                    if str(item).strip()
                ],
                "impacted_symbols": [
                    str(item)[:40]
                    for item in list(row.get("impacted_symbols") or [])[:24]
                    if str(item).strip()
                ],
                "repair_targets": [
                    {
                        key: str(target.get(key) or "")[:180]
                        for key in (
                            "page_id",
                            "symbol",
                            "recommended_action",
                        )
                        if str(target.get(key) or "").strip()
                    }
                    for target in list(row.get("repair_targets") or [])[:8]
                    if isinstance(target, dict)
                ],
                "repair_target_effectiveness": (
                    self._prompt_repair_target_effectiveness_summary(
                        row.get("repair_target_effectiveness")
                    )
                ),
                "repair_action": str(row.get("repair_action") or "")[:240],
                "repair_loop_status": str(row.get("repair_loop_status") or ""),
                "repair_loop_action_type": str(
                    row.get("repair_loop_action_type") or ""
                )[:160],
            }
            for key in ("sample_count",):
                if row.get(key) not in (None, "", [], {}):
                    compact[key] = _safe_int(row.get(key))
            for key in (
                "win_rate",
                "expectancy",
                "helpful_score",
                "drawdown_pressure",
            ):
                if row.get(key) not in (None, "", [], {}):
                    compact[key] = _safe_float(row.get(key))
            for key in (
                "repair_loop_sample_count",
                "repair_loop_missed_count",
                "repair_loop_resolved_count",
            ):
                if row.get(key) not in (None, "", [], {}):
                    compact[key] = _safe_int(row.get(key))
            if row.get("repair_loop_resolution_rate") not in (None, "", [], {}):
                compact["repair_loop_resolution_rate"] = _safe_float(
                    row.get("repair_loop_resolution_rate")
                )
            priorities.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
        repair_loop_effectiveness = self._prompt_repair_loop_effectiveness_summary(
            contract.get("repair_loop_effectiveness")
            if isinstance(contract.get("repair_loop_effectiveness"), dict)
            else {}
        )
        repair_priority_count_explicit = contract.get("repair_priority_count") not in (
            None,
            "",
            [],
            {},
        )
        repair_priority_count = (
            _safe_int(contract.get("repair_priority_count"))
            if repair_priority_count_explicit
            else len(priorities)
        )
        if repair_priority_count_explicit and repair_priority_count <= 0:
            priorities = []
        return {
            key: value
            for key, value in {
                "status": str(contract.get("status") or ""),
                "repair_priority_count": repair_priority_count,
                "top_priorities": priorities,
                "repair_loop_effectiveness": repair_loop_effectiveness,
                "required_resolution": str(
                    contract.get("required_resolution") or ""
                )[:360],
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _prompt_repair_target_effectiveness_summary(source: Any) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}
        compact = {
            "page_id": str(source.get("page_id") or "")[:180],
            "status": str(source.get("status") or "")[:80],
            "reasons": [
                str(item)[:180]
                for item in list(source.get("reasons") or [])[:8]
                if str(item).strip()
            ],
        }
        for key in ("sample_count",):
            if source.get(key) not in (None, "", [], {}):
                compact[key] = _safe_int(source.get(key))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if source.get(key) not in (None, "", [], {}):
                compact[key] = _safe_float(source.get(key))
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _prompt_memory_card_quality_gap_summary(source: Any) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}

        def compact_counts(key: str) -> dict[str, int]:
            source_counts = (
                source.get(key) if isinstance(source.get(key), dict) else {}
            )
            return {
                str(item).strip()[:120]: _safe_int(count)
                for item, count in sorted(source_counts.items())
                if str(item).strip()
            }

        def compact_top(key: str, *, label: str) -> list[dict[str, int | str]]:
            rows: list[dict[str, int | str]] = []
            for row in list(source.get(key) or [])[:6]:
                if not isinstance(row, dict):
                    continue
                item = str(row.get(label) or "").strip()[:140]
                if not item:
                    continue
                compact: dict[str, int | str] = {label: item}
                for metric_key in ("sample_count", "missed_count"):
                    _add_prompt_int(compact, row, metric_key)
                rows.append(compact)
            return rows

        def compact_priority_terms(
            *,
            source_key: str,
            missed_counts_key: str,
            top_key: str,
            label: str,
        ) -> list[str]:
            terms: list[str] = []

            def add(value: Any) -> None:
                text = str(value or "").strip()[:140]
                if text and text not in terms:
                    terms.append(text)

            for item in list(source.get(source_key) or [])[:6]:
                add(item)
            if terms:
                return terms
            missed_counts = (
                source.get(missed_counts_key)
                if isinstance(source.get(missed_counts_key), dict)
                else {}
            )
            for item, count in sorted(
                missed_counts.items(),
                key=lambda row: (-_safe_int(row[1]), str(row[0])),
            ):
                if _safe_int(count) > 0:
                    add(item)
            if terms:
                return terms[:6]
            for row in list(source.get(top_key) or [])[:6]:
                if not isinstance(row, dict):
                    continue
                if _safe_int(row.get("missed_count")) > 0:
                    add(row.get(label))
            return terms[:6]

        def priority_metric(
            *,
            top_key: str,
            label: str,
            sample_counts_key: str,
            missed_counts_key: str,
        ) -> tuple[str, int, int] | None:
            for row in list(source.get(top_key) or [])[:6]:
                if not isinstance(row, dict):
                    continue
                item = str(row.get(label) or "").strip()[:140]
                missed_count = _safe_int(row.get("missed_count"))
                if item and missed_count > 0:
                    return item, _safe_int(row.get("sample_count")), missed_count
            missed_counts = (
                source.get(missed_counts_key)
                if isinstance(source.get(missed_counts_key), dict)
                else {}
            )
            sample_counts = (
                source.get(sample_counts_key)
                if isinstance(source.get(sample_counts_key), dict)
                else {}
            )
            for item, missed_count in sorted(
                missed_counts.items(),
                key=lambda row: (-_safe_int(row[1]), str(row[0])),
            ):
                clean = str(item or "").strip()[:140]
                if clean and _safe_int(missed_count) > 0:
                    return (
                        clean,
                        _safe_int(sample_counts.get(item)),
                        _safe_int(missed_count),
                    )
            return None

        def compact_priority_focus() -> dict[str, Any]:
            source_focus = (
                source.get("priority_focus")
                if isinstance(source.get("priority_focus"), dict)
                else {}
            )
            if source_focus:
                compact_focus: dict[str, Any] = {}
                for key in ("missing_field", "required_check", "instruction"):
                    text = str(source_focus.get(key) or "").strip()[:180]
                    if text:
                        compact_focus[key] = text
                for key in (
                    "missing_field_sample_count",
                    "missing_field_missed_count",
                    "required_check_sample_count",
                    "required_check_missed_count",
                ):
                    if source_focus.get(key) not in (None, "", [], {}):
                        compact_focus[key] = _safe_int(source_focus.get(key))
                return compact_focus
            focus: dict[str, Any] = {}
            missing = priority_metric(
                top_key="top_missing_fields",
                label="field",
                sample_counts_key="missing_field_counts",
                missed_counts_key="missing_field_missed_counts",
            )
            check = priority_metric(
                top_key="top_required_checks",
                label="check",
                sample_counts_key="required_check_counts",
                missed_counts_key="required_check_missed_counts",
            )
            if missing:
                field, sample_count, missed_count = missing
                focus.update(
                    {
                        "missing_field": field,
                        "missing_field_sample_count": sample_count,
                        "missing_field_missed_count": missed_count,
                    }
                )
            if check:
                check_name, sample_count, missed_count = check
                focus.update(
                    {
                        "required_check": check_name,
                        "required_check_sample_count": sample_count,
                        "required_check_missed_count": missed_count,
                    }
                )
            if focus:
                focus["instruction"] = "resolve_priority_memory_card_quality_gap_first"
            return focus

        compact = {
            "status": str(source.get("status") or "")[:80],
            "missing_field_counts": compact_counts("missing_field_counts"),
            "missing_field_missed_counts": compact_counts(
                "missing_field_missed_counts"
            ),
            "required_check_counts": compact_counts("required_check_counts"),
            "required_check_missed_counts": compact_counts(
                "required_check_missed_counts"
            ),
            "priority_missing_fields": compact_priority_terms(
                source_key="priority_missing_fields",
                missed_counts_key="missing_field_missed_counts",
                top_key="top_missing_fields",
                label="field",
            ),
            "priority_required_checks": compact_priority_terms(
                source_key="priority_required_checks",
                missed_counts_key="required_check_missed_counts",
                top_key="top_required_checks",
                label="check",
            ),
            "priority_focus": compact_priority_focus(),
            "top_missing_fields": compact_top(
                "top_missing_fields",
                label="field",
            ),
            "top_required_checks": compact_top(
                "top_required_checks",
                label="check",
            ),
        }
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _prompt_repair_loop_effectiveness_summary(
        source: dict[str, Any],
    ) -> dict[str, Any]:
        if not source:
            return {}
        top_degraded: list[dict[str, Any]] = []
        top_degraded_rows = [
            row for row in list(source.get("top_degraded") or []) if isinstance(row, dict)
        ]
        top_degraded_rows.sort(
            key=JueWikiApplicationService._repair_loop_effectiveness_row_sort_key
        )
        for row in top_degraded_rows[:6]:
            if not isinstance(row, dict):
                continue
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "priority_type": str(row.get("priority_type") or "")[:120],
                "action_type": str(row.get("action_type") or "")[:160],
                "decision_use": str(row.get("decision_use") or "")[:160],
                "source_id": str(row.get("source_id") or "")[:180],
                "status": str(row.get("status") or "")[:80],
            }
            for key in ("sample_count", "missed_count", "resolved_count"):
                _add_prompt_int(compact, row, key)
            _add_prompt_float(compact, row, "resolution_rate")
            for source_key, target_key in (
                ("repair_missing_fields", "repair_missing_fields"),
                ("repair_required_checks", "repair_required_checks"),
            ):
                values = _compact_prompt_string_list(
                    row.get(source_key) or [],
                    limit=6,
                    max_len=140,
                )
                if values:
                    compact[target_key] = values
            top_degraded.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
        repair_loop_status_metrics: list[dict[str, Any]] = []
        repair_loop_status_rows = [
            row
            for row in list(source.get("repair_loop_status_metrics") or [])
            if isinstance(row, dict)
        ]
        repair_loop_status_rows.sort(
            key=JueWikiApplicationService._repair_loop_effectiveness_row_sort_key
        )
        repair_loop_status_summary = (
            JueWikiApplicationService._repair_loop_status_summary(
                repair_loop_status_rows
            )
        )
        for row in repair_loop_status_rows[:6]:
            if not isinstance(row, dict):
                continue
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "repair_loop_status": str(
                    row.get("repair_loop_status") or ""
                )[:80],
                "action_type": str(row.get("action_type") or "")[:160],
                "status": str(row.get("status") or "")[:80],
            }
            for key in (
                "sample_count",
                "missed_count",
                "resolved_count",
                "loop_sample_count",
                "loop_missed_count",
                "loop_resolved_count",
            ):
                _add_prompt_int(compact, row, key)
            for key in ("resolution_rate", "loop_resolution_rate"):
                _add_prompt_float(compact, row, key)
            repair_loop_status_metrics.append(
                {
                    key: value
                    for key, value in compact.items()
                if value not in (None, "", [], {})
            }
        )
        repair_success_criteria_metrics: list[dict[str, Any]] = []
        success_criteria_rows = [
            row
            for row in list(source.get("repair_success_criteria_metrics") or [])
            if isinstance(row, dict)
        ]
        success_criteria_rows.sort(
            key=JueWikiApplicationService._repair_success_criteria_metric_sort_key
        )
        repair_success_criteria_summary = (
            JueWikiApplicationService._repair_success_criteria_effectiveness_summary(
                success_criteria_rows
            )
        )
        for row in success_criteria_rows[:6]:
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "criterion": str(row.get("criterion") or "")[:180],
                "status": str(row.get("status") or "")[:80],
            }
            for key in ("sample_count", "missed_count", "resolved_count"):
                _add_prompt_int(compact, row, key)
            _add_prompt_float(compact, row, "resolution_rate")
            repair_success_criteria_metrics.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
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
                JueWikiApplicationService
                ._repair_learning_directive_metrics_from_success_criteria_rows(
                    success_criteria_rows
                )
            )
        directive_metric_rows.sort(
            key=(
                JueWikiApplicationService
                ._repair_learning_directive_metric_sort_key
            )
        )
        repair_learning_directive_summary = (
            JueWikiApplicationService
            ._repair_learning_directive_effectiveness_summary(
                directive_metric_rows
            )
        )
        for row in directive_metric_rows[:6]:
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "recommended_action": str(
                    row.get("recommended_action") or ""
                )[:180],
                "status": str(row.get("status") or "")[:80],
            }
            for key in ("sample_count", "missed_count", "resolved_count"):
                _add_prompt_int(compact, row, key)
            _add_prompt_float(compact, row, "resolution_rate")
            repair_learning_directive_metrics.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
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
            step_metric_rows = (
                JueWikiApplicationService
                ._repair_learning_step_metrics_from_action_targets(
                    source_directive_summary
                )
            )
        step_metric_rows.sort(
            key=JueWikiApplicationService._repair_learning_step_metric_sort_key
        )
        repair_learning_step_summary = (
            JueWikiApplicationService._repair_learning_step_effectiveness_summary(
                step_metric_rows
            )
        )
        for row in step_metric_rows[:6]:
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "resolution_step": str(row.get("resolution_step") or "")[:180],
                "status": str(row.get("status") or "")[:80],
            }
            for key in ("sample_count", "missed_count", "resolved_count"):
                _add_prompt_int(compact, row, key)
            _add_prompt_float(compact, row, "resolution_rate")
            repair_learning_step_metrics.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
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
            resolution_metric_rows = (
                JueWikiApplicationService
                ._repair_learning_resolution_metrics_from_step_targets(
                    source_step_summary
                )
            )
        resolution_metric_rows.sort(
            key=(
                JueWikiApplicationService
                ._repair_learning_resolution_metric_sort_key
            )
        )
        repair_learning_resolution_summary = (
            JueWikiApplicationService
            ._repair_learning_resolution_effectiveness_summary(
                resolution_metric_rows
            )
        )
        for row in resolution_metric_rows[:6]:
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "recommended_resolution": str(
                    row.get("recommended_resolution") or ""
                )[:180],
                "status": str(row.get("status") or "")[:80],
            }
            for key in ("sample_count", "missed_count", "resolved_count"):
                _add_prompt_int(compact, row, key)
            _add_prompt_float(compact, row, "resolution_rate")
            repair_learning_resolution_metrics.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
        component_target_metric_rows = [
            row
            for row in list(source.get("repair_component_target_metrics") or [])
            if isinstance(row, dict)
        ]
        component_target_metric_rows.sort(
            key=JueWikiApplicationService._repair_loop_effectiveness_row_sort_key
        )
        repair_component_target_metrics = _compact_prompt_repair_component_targets(
            component_target_metric_rows,
            limit=6,
        )
        repair_component_target_summary = (
            JueWikiApplicationService._prompt_repair_component_target_summary(
                source.get("repair_component_target_summary")
            )
            or JueWikiApplicationService._repair_component_target_effectiveness_summary(
                component_target_metric_rows
            )
        )
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
                    if str(row.get("status") or "").strip().lower()
                    == "repair_required"
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
                and repair_component_target_summary.get(
                    "probe_component_target_details"
                )
            ):
                repair_component_target_summary = {
                    **repair_component_target_summary,
                    "primary_probe_component_target_detail": (
                        repair_component_target_summary[
                            "probe_component_target_details"
                        ][0]
                    ),
                }
            if not repair_component_target_summary.get(
                "component_target_attention_plan"
            ):
                repair_component_target_summary = {
                    **repair_component_target_summary,
                    "component_target_attention_plan": (
                        _prompt_component_target_attention_plan(
                            repair_required_detail=repair_component_target_summary.get(
                                "primary_repair_required_component_target_detail"
                            ),
                            probe_detail=repair_component_target_summary.get(
                                "primary_probe_component_target_detail"
                            ),
                        )
                    ),
                }
        component_status_summary = (
            JueWikiApplicationService._prompt_repair_component_status_summary(
                source.get("component_status_summary")
            )
        )
        if not component_status_summary:
            component_status_summary = (
                JueWikiApplicationService
                ._repair_priority_component_status_summary(
                    components=[
                        ("repair_priority_metrics", top_degraded_rows),
                        (
                            "repair_loop_status_metrics",
                            repair_loop_status_rows,
                        ),
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
            component_status_summary = (
                JueWikiApplicationService
                ._prompt_repair_component_status_summary(
                    component_status_summary
                )
            )
        compact_source = {
            "status": (
                JueWikiApplicationService._repair_prompt_worst_status(
                    str(source.get("status") or ""),
                    str(component_status_summary.get("worst_status") or ""),
                )
            ),
            "top_degraded": top_degraded,
            "repair_loop_status_metrics": repair_loop_status_metrics,
            "repair_loop_status_summary": repair_loop_status_summary,
            "repair_success_criteria_metrics": repair_success_criteria_metrics,
            "repair_success_criteria_summary": repair_success_criteria_summary,
            "repair_learning_directive_metrics": (
                repair_learning_directive_metrics
            ),
            "repair_learning_directive_summary": repair_learning_directive_summary,
            "repair_learning_step_metrics": repair_learning_step_metrics,
            "repair_learning_step_summary": repair_learning_step_summary,
            "repair_learning_resolution_metrics": (
                repair_learning_resolution_metrics
            ),
            "repair_learning_resolution_summary": (
                repair_learning_resolution_summary
            ),
            "repair_component_target_metrics": repair_component_target_metrics,
            "repair_component_target_summary": repair_component_target_summary,
            "memory_card_quality_gap_summary": (
                JueWikiApplicationService._prompt_memory_card_quality_gap_summary(
                    source.get("memory_card_quality_gap_summary")
                )
            ),
            "component_status_summary": component_status_summary,
        }
        for key in (
            "sample_count",
            "missed_count",
            "resolved_count",
            "metric_count",
            "repair_required_count",
        ):
            _add_prompt_int(compact_source, source, key)
        _add_prompt_float(compact_source, source, "resolution_rate")
        return {
            key: value
            for key, value in compact_source.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
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

    @staticmethod
    def _prompt_repair_component_target_summary(source: Any) -> dict[str, Any]:
        summary = source if isinstance(source, dict) else {}
        if not summary:
            return {}
        primary_repair_required_detail = _compact_prompt_repair_component_targets(
            [summary.get("primary_repair_required_component_target_detail")],
            limit=1,
        )
        primary_probe_detail = _compact_prompt_repair_component_targets(
            [summary.get("primary_probe_component_target_detail")],
            limit=1,
        )
        attention_plan = (
            summary.get("component_target_attention_plan")
            if isinstance(summary.get("component_target_attention_plan"), dict)
            else {}
        )
        if attention_plan:
            attention_plan = _prompt_component_target_attention_plan(
                repair_required_detail=attention_plan.get("repair_now"),
                probe_detail=attention_plan.get("probe_next"),
            )
        if not attention_plan:
            attention_plan = _prompt_component_target_attention_plan(
                repair_required_detail=next(
                    iter(primary_repair_required_detail),
                    None,
                ),
                probe_detail=next(iter(primary_probe_detail), None),
            )
        compact = {
            "worst_status": str(summary.get("worst_status") or "")[:80],
            "primary_component_target": str(
                summary.get("primary_component_target") or ""
            )[:120],
            "top_component_targets": [
                str(item)[:120]
                for item in list(summary.get("top_component_targets") or [])[:8]
                if str(item).strip()
            ],
            "top_component_target_details": (
                _compact_prompt_repair_component_targets(
                    summary.get("top_component_target_details"),
                    limit=3,
                )
            ),
            "repair_required_component_target_details": (
                _compact_prompt_repair_component_targets(
                    summary.get("repair_required_component_target_details"),
                    limit=3,
                )
            ),
            "primary_repair_required_component_target_detail": next(
                iter(primary_repair_required_detail),
                None,
            ),
            "probe_component_target_details": (
                _compact_prompt_repair_component_targets(
                    summary.get("probe_component_target_details"),
                    limit=3,
                )
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
            _add_prompt_int(compact, summary, key)
        _add_prompt_float(compact, summary, "min_resolution_rate")
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _prompt_repair_component_status_summary(source: Any) -> dict[str, Any]:
        summary = source if isinstance(source, dict) else {}
        if not summary:
            return {}
        components: list[dict[str, Any]] = []
        for row in list(summary.get("components") or [])[:6]:
            if not isinstance(row, dict):
                continue
            compact = {
                "component": str(row.get("component") or "")[:120],
                "worst_status": str(row.get("worst_status") or "")[:80],
                "component_targets": _compact_prompt_repair_component_targets(
                    row.get("component_targets"),
                    limit=3,
                ),
            }
            for key in (
                "metric_count",
                "repair_required_count",
                "probe_count",
                "active_count",
                "unknown_count",
            ):
                _add_prompt_int(compact, row, key)
            components.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
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
            "top_component_targets": (
                _compact_prompt_repair_component_targets(
                    summary.get("top_component_targets")
                )
                or JueWikiApplicationService._repair_priority_top_component_targets(
                    components
                )
            ),
            "repair_required_component_targets": (
                _compact_prompt_repair_component_targets(
                    summary.get("repair_required_component_targets")
                )
            ),
            "probe_component_targets": (
                _compact_prompt_repair_component_targets(
                    summary.get("probe_component_targets")
                )
            ),
        }
        for key in (
            "component_count",
            "metric_count",
            "repair_required_count",
            "probe_count",
            "active_count",
            "unknown_count",
        ):
            _add_prompt_int(compact_summary, summary, key)
        return {
            key: value
            for key, value in compact_summary.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _repair_loop_status_metric_rank(status: str) -> int:
        clean_status = str(status or "").strip().lower()
        if clean_status == "repair_required":
            return 0
        if clean_status == "probe":
            return 1
        if clean_status == "active":
            return 2
        return 3

    @staticmethod
    def _repair_loop_effectiveness_row_sort_key(
        row: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            JueWikiApplicationService._repair_loop_status_metric_rank(
                str(row.get("status") or "")
            ),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("action_type") or ""),
            str(row.get("source_id") or ""),
        )

    @staticmethod
    def _repair_loop_status_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {}
        repair_required_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "repair_required"
        )
        probe_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "probe"
        )
        active_count = sum(
            1
            for row in rows
            if str(row.get("status") or "").strip().lower() == "active"
        )
        unknown_count = len(rows) - repair_required_count - probe_count - active_count
        worst_status = "active"
        if repair_required_count:
            worst_status = "repair_required"
        elif probe_count:
            worst_status = "probe"
        elif unknown_count and not active_count:
            worst_status = "unknown"
        repair_required_action_types = (
            JueWikiApplicationService._repair_loop_action_types(
                rows,
                status="repair_required",
            )
        )
        top_missed_action_types = (
            JueWikiApplicationService._repair_loop_action_types(
                rows,
                only_max_missed=True,
            )
        )
        primary_repair_action_type = next(
            iter(repair_required_action_types or top_missed_action_types),
            None,
        )
        repair_action_targets = JueWikiApplicationService._repair_loop_action_targets(
            rows
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

    @staticmethod
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
                row
                for row in candidates
                if _has_prompt_value(row.get("missed_count"))
            ]
            if not candidates:
                return []
            max_missed = max(_safe_int(row.get("missed_count")) for row in candidates)
            candidates = [
                row
                for row in candidates
                if _safe_int(row.get("missed_count")) == max_missed
            ]
        candidates.sort(
            key=JueWikiApplicationService._repair_loop_effectiveness_row_sort_key
        )
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

    @staticmethod
    def _repair_loop_action_targets(
        rows: list[dict[str, Any]],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        candidates = [
            row
            for row in rows
            if str(row.get("action_type") or "").strip()
            and str(row.get("status") or "").strip().lower()
            == "repair_required"
        ]
        if not candidates:
            candidates = [
                row for row in rows if str(row.get("action_type") or "").strip()
            ]
            metric_candidates = [
                row
                for row in candidates
                if _has_prompt_value(row.get("missed_count"))
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
                if (
                    warning_text
                    and warning_text not in target["quality_warnings"]
                ):
                    target["quality_warnings"].append(warning_text)
            for page_id in JueWikiApplicationService._repair_priority_nested_values(
                [row],
                key="impacted_page_ids",
                limit=12,
            ):
                if page_id not in target["impacted_page_ids"]:
                    target["impacted_page_ids"].append(page_id)
            for symbol in JueWikiApplicationService._repair_priority_nested_values(
                [row],
                key="impacted_symbols",
                limit=24,
            ):
                if symbol not in target["impacted_symbols"]:
                    target["impacted_symbols"].append(symbol)
            for repair_target in (
                JueWikiApplicationService._repair_priority_repair_targets(
                    [row],
                    limit=8,
                )
            ):
                if repair_target not in target["repair_targets"]:
                    target["repair_targets"].append(repair_target)
            effectiveness = (
                JueWikiApplicationService._prompt_repair_target_effectiveness_summary(
                    row.get("repair_target_effectiveness")
                )
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
            _attach_present_target_rates(target)
            target["recommended_resolution"] = (
                JueWikiApplicationService._repair_loop_recommended_resolution(
                    str(target.get("action_type") or ""),
                    str(target.get("decision_scope") or ""),
                )
            )
            target["resolution_steps"] = (
                JueWikiApplicationService._repair_loop_resolution_steps(
                    str(target.get("action_type") or ""),
                    str(target.get("decision_scope") or ""),
                )
            )
            target["resolution_success_criteria"] = (
                JueWikiApplicationService._repair_loop_resolution_success_criteria(
                    str(target.get("action_type") or ""),
                    str(target.get("decision_scope") or ""),
                )
            )
        grouped_targets.sort(
            key=JueWikiApplicationService._repair_loop_action_target_sort_key
        )
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
                target["repair_target_effectiveness"] = (
                    repair_target_effectiveness[:8]
                )
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

    @staticmethod
    def _repair_loop_action_target_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            JueWikiApplicationService._repair_loop_status_metric_rank(
                str(row.get("status") or "")
            ),
            -_safe_int(row.get("missed_count")),
            -_safe_int(row.get("sample_count")),
            _safe_float(row.get("resolution_rate")),
            str(row.get("action_type") or ""),
            str(row.get("decision_scope") or ""),
        )

    @staticmethod
    def _repair_loop_recommended_resolution(
        action_type: str,
        decision_scope: str,
    ) -> str:
        clean_action = str(action_type or "").strip().lower()
        clean_scope = str(decision_scope or "").strip().lower()
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

    @staticmethod
    def _repair_loop_resolution_steps(
        action_type: str,
        decision_scope: str,
    ) -> list[str]:
        recommended = JueWikiApplicationService._repair_loop_recommended_resolution(
            action_type,
            decision_scope,
        )
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

    @staticmethod
    def _repair_loop_resolution_success_criteria(
        action_type: str,
        decision_scope: str,
    ) -> list[str]:
        recommended = JueWikiApplicationService._repair_loop_recommended_resolution(
            action_type,
            decision_scope,
        )
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

    def _prompt_selected_wiki_pages_summary(
        self,
        prompt: dict[str, Any],
        selected_page_ids: list[str],
        *,
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        source = prompt.get("jue_wiki")
        if not isinstance(source, dict):
            source = prompt.get("jue_wiki_selection_observation")
        pages = source.get("pages") if isinstance(source, dict) else []
        by_page_id = {
            str(row.get("page_id") or "").strip(): row
            for row in pages
            if isinstance(row, dict) and str(row.get("page_id") or "").strip()
        }
        requested_summaries = (
            source.get("requested_symbol_summaries")
            if isinstance(source, dict)
            and isinstance(source.get("requested_symbol_summaries"), list)
            else []
        )
        for row in requested_summaries:
            if not isinstance(row, dict):
                continue
            page_id = str(row.get("page_id") or "").strip()
            if not page_id:
                continue
            prompt_row = dict(row)
            prompt_row.setdefault("page_type", "symbol")
            symbol = str(prompt_row.get("symbol") or "").strip().upper()
            if symbol and prompt_row.get("symbols") in (None, "", [], {}):
                prompt_row["symbols"] = [symbol]
            prompt_row["applied_via"] = "requested_symbol_summary"
            existing = by_page_id.get(page_id)
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(
                    {
                        key: value
                        for key, value in prompt_row.items()
                        if value not in (None, "", [], {})
                    }
                )
                if existing.get("applied_via") and existing.get("applied_via") != (
                    "requested_symbol_summary"
                ):
                    merged["applied_via"] = (
                        f"{existing.get('applied_via')}+requested_symbol_summary"
                    )
                by_page_id[page_id] = merged
            else:
                by_page_id[page_id] = prompt_row
        selected = self._clean_page_ids(selected_page_ids)
        effectiveness_horizons = self._prompt_context_horizons(prompt)
        for horizon in list(horizons or []):
            clean_horizon = _clean_prompt_semantic_text(horizon).lower()
            if clean_horizon and clean_horizon not in effectiveness_horizons:
                effectiveness_horizons.append(clean_horizon)
        rows: list[dict[str, Any]] = []
        warning_counts: dict[str, int] = {}
        repair_queue_count = 0
        repair_action_types: list[str] = []
        repair_decision_uses: list[str] = []
        repair_quality_warnings: list[str] = []
        repair_diagnostic_reasons: list[str] = []
        repair_targets: list[dict[str, str]] = []
        repair_horizon_gap_total = 0
        repair_horizon_gap_max_pct = 0.0
        usage_guidance_effectiveness_status_counts: dict[str, int] = {}
        memory_card_quality_effectiveness_status_counts: dict[str, int] = {}
        quality_warning_source_effectiveness_status_counts: dict[str, int] = {}
        quality_warning_effectiveness_status_counts: dict[str, int] = {}
        effectiveness_fallback_counts: dict[str, int] = {}
        effectiveness_fallback_page_ids: list[str] = []
        effectiveness_fallback_symbols: list[str] = []
        effectiveness_fallback_page_symbols: list[dict[str, Any]] = []
        effectiveness_fallback_markets: list[str] = []
        effectiveness_fallback_sides: list[str] = []
        effectiveness_fallback_requested_horizons: list[str] = []
        effectiveness_attention_page_ids: list[str] = []
        effectiveness_attention_items: list[dict[str, Any]] = []
        for page_id in selected[:12]:
            page = self._enriched_selected_wiki_page(
                page_id=page_id,
                prompt_page=by_page_id.get(page_id, {}),
            )
            row = self._selected_wiki_page_row(
                page_id=page_id,
                page=page,
                horizons=effectiveness_horizons,
            )
            effectiveness_status_found = False
            for status in self._selected_page_effectiveness_statuses(
                row.get("usage_guidance_effectiveness")
            ):
                usage_guidance_effectiveness_status_counts[status] = (
                    usage_guidance_effectiveness_status_counts.get(status, 0) + 1
                )
                effectiveness_status_found = True
            for status in self._selected_page_effectiveness_statuses(
                row.get("memory_card_quality_effectiveness")
            ):
                memory_card_quality_effectiveness_status_counts[status] = (
                    memory_card_quality_effectiveness_status_counts.get(status, 0)
                    + 1
                )
                effectiveness_status_found = True
            for status in self._selected_page_effectiveness_statuses(
                row.get("quality_warning_source_effectiveness")
            ):
                quality_warning_source_effectiveness_status_counts[status] = (
                    quality_warning_source_effectiveness_status_counts.get(status, 0)
                    + 1
                )
                effectiveness_status_found = True
            quality_warning_effectiveness_statuses = set(
                self._selected_page_effectiveness_statuses(
                    row.get("quality_warning_effectiveness")
                )
            )
            for status in list(row.get("quality_warning_effectiveness_statuses") or []):
                clean_status = _clean_prompt_semantic_text(status).lower()
                if clean_status:
                    quality_warning_effectiveness_statuses.add(clean_status)
            for status in sorted(quality_warning_effectiveness_statuses):
                quality_warning_effectiveness_status_counts[status] = (
                    quality_warning_effectiveness_status_counts.get(status, 0)
                    + 1
                )
                effectiveness_status_found = True
            if effectiveness_status_found and page_id not in (
                effectiveness_attention_page_ids
            ):
                effectiveness_attention_page_ids.append(page_id)
            for fallback_reason in self._selected_page_effectiveness_fallback_reasons(
                row
            ):
                effectiveness_fallback_counts[fallback_reason] = (
                    effectiveness_fallback_counts.get(fallback_reason, 0) + 1
                )
                if page_id not in effectiveness_fallback_page_ids:
                    effectiveness_fallback_page_ids.append(page_id)
                row_symbols = self._selected_page_symbol_list(
                    page_id=page_id,
                    page=row,
                    limit=8,
                )
                for clean_symbol in row_symbols:
                    if clean_symbol not in effectiveness_fallback_symbols:
                        effectiveness_fallback_symbols.append(clean_symbol)
                page_symbol_row = {
                    "page_id": page_id,
                    "symbols": row_symbols,
                }
                if row_symbols and page_symbol_row not in (
                    effectiveness_fallback_page_symbols
                ):
                    effectiveness_fallback_page_symbols.append(page_symbol_row)
                lane_metadata = self._selected_wiki_page_lane_metadata(row)
                for target, value in (
                    (effectiveness_fallback_markets, lane_metadata.get("market")),
                    (effectiveness_fallback_sides, lane_metadata.get("side")),
                ):
                    if value and value not in target:
                        target.append(value)
                for horizon in (
                    self._selected_page_effectiveness_requested_horizons(row)
                ):
                    if horizon not in effectiveness_fallback_requested_horizons:
                        effectiveness_fallback_requested_horizons.append(horizon)
            for item in self._selected_page_effectiveness_attention_items(row):
                if item not in effectiveness_attention_items:
                    effectiveness_attention_items.append(item)
            for warning in row.get("quality_warnings", []):
                warning_text = _clean_prompt_semantic_text(warning)
                if warning_text:
                    warning_counts[warning_text] = (
                        warning_counts.get(warning_text, 0) + 1
                    )
            if row.get("repair_queue"):
                repair_queue_count += 1
                repair_queue = (
                    row.get("repair_queue")
                    if isinstance(row.get("repair_queue"), dict)
                    else {}
                )
                action_type = _clean_prompt_semantic_text(
                    repair_queue.get("action_type")
                )
                if action_type and action_type not in repair_action_types:
                    repair_action_types.append(action_type)
                decision_use = _clean_prompt_semantic_text(
                    repair_queue.get("decision_use")
                )
                if decision_use and decision_use not in repair_decision_uses:
                    repair_decision_uses.append(decision_use)
                for warning in list(repair_queue.get("quality_warnings") or []):
                    warning_text = _clean_prompt_semantic_text(warning)
                    if warning_text and warning_text not in repair_quality_warnings:
                        repair_quality_warnings.append(warning_text)
                for reason in list(repair_queue.get("diagnostic_reasons") or []):
                    reason_text = _clean_prompt_semantic_text(reason)
                    if reason_text and reason_text not in repair_diagnostic_reasons:
                        repair_diagnostic_reasons.append(reason_text)
                for target in _compact_prompt_repair_targets(
                    repair_queue.get("repair_targets")
                ):
                    if target not in repair_targets:
                        repair_targets.append(target)
                repair_horizon_gap_total += _safe_int(
                    repair_queue.get("closed_block_outcomes_without_horizon")
                )
                repair_horizon_gap_max_pct = max(
                    repair_horizon_gap_max_pct,
                    _safe_float(
                        repair_queue.get("closed_block_outcomes_without_horizon_pct")
                    ),
                )
            rows.append(row)
        summary = {
            key: value
            for key, value in {
                "page_count": len(selected),
                "reported_page_count": len(rows),
                "repair_queue_count": repair_queue_count,
                "repair_action_types": repair_action_types[:8],
                "repair_decision_uses": repair_decision_uses[:8],
                "repair_quality_warnings": repair_quality_warnings[:12],
                "repair_diagnostic_reasons": repair_diagnostic_reasons[:12],
                "repair_targets": repair_targets[:12],
                "repair_horizon_gap_total": (
                    repair_horizon_gap_total if repair_horizon_gap_total > 0 else None
                ),
                "repair_horizon_gap_max_pct": (
                    round(repair_horizon_gap_max_pct, 4)
                    if repair_horizon_gap_max_pct > 0
                    else None
                ),
                "quality_warning_count": sum(warning_counts.values()),
                "warning_counts": dict(sorted(warning_counts.items())),
                "usage_guidance_effectiveness_status_counts": dict(
                    sorted(usage_guidance_effectiveness_status_counts.items())
                ),
                "memory_card_quality_effectiveness_status_counts": dict(
                    sorted(memory_card_quality_effectiveness_status_counts.items())
                ),
                "quality_warning_source_effectiveness_status_counts": dict(
                    sorted(quality_warning_source_effectiveness_status_counts.items())
                ),
                "quality_warning_effectiveness_status_counts": dict(
                    sorted(quality_warning_effectiveness_status_counts.items())
                ),
                "effectiveness_fallback_counts": dict(
                    sorted(effectiveness_fallback_counts.items())
                ),
                "effectiveness_fallback_page_ids": effectiveness_fallback_page_ids,
                "effectiveness_fallback_symbols": effectiveness_fallback_symbols,
                "effectiveness_fallback_page_symbols": (
                    effectiveness_fallback_page_symbols
                ),
                "effectiveness_fallback_markets": effectiveness_fallback_markets,
                "effectiveness_fallback_sides": effectiveness_fallback_sides,
                "effectiveness_fallback_requested_horizons": (
                    effectiveness_fallback_requested_horizons
                ),
                "effectiveness_attention_page_ids": effectiveness_attention_page_ids,
                "effectiveness_attention_items": effectiveness_attention_items[:24],
                "pages": rows,
            }.items()
            if value not in (None, "", [], {})
        }
        return summary

    @classmethod
    def _selected_page_effectiveness_fallback_reasons(
        cls,
        row: dict[str, Any],
    ) -> list[str]:
        reasons: list[str] = []
        for key in (
            "usage_guidance_effectiveness",
            "memory_card_quality_effectiveness",
            "quality_warning_source_effectiveness",
            "quality_warning_effectiveness",
        ):
            for reason in cls._effectiveness_fallback_reasons_for_value(row.get(key)):
                if reason not in reasons:
                    reasons.append(reason)
        return reasons

    @staticmethod
    def _effectiveness_fallback_reasons_for_value(value: Any) -> list[str]:
        rows = value if isinstance(value, list) else [value]
        reasons: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sources = [
                metric
                for metric in list(row.get("metrics") or [])
                if isinstance(metric, dict)
            ] or [row]
            for source in sources:
                reason = _clean_prompt_semantic_text(source.get("fallback_reason"))
                if reason and reason not in reasons:
                    reasons.append(reason)
        return reasons

    @classmethod
    def _selected_page_effectiveness_requested_horizons(
        cls,
        row: dict[str, Any],
    ) -> list[str]:
        horizons: list[str] = []
        for key in (
            "usage_guidance_effectiveness",
            "memory_card_quality_effectiveness",
            "quality_warning_source_effectiveness",
            "quality_warning_effectiveness",
        ):
            for horizon in cls._effectiveness_requested_horizons_for_value(
                row.get(key)
            ):
                if horizon not in horizons:
                    horizons.append(horizon)
        return horizons

    @staticmethod
    def _effectiveness_requested_horizons_for_value(value: Any) -> list[str]:
        rows = value if isinstance(value, list) else [value]
        horizons: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sources = [
                metric
                for metric in list(row.get("metrics") or [])
                if isinstance(metric, dict)
            ] or [row]
            for source in sources:
                requested = source.get("requested_horizons")
                requested_values = requested if isinstance(requested, list) else []
                requested_horizon = source.get("requested_horizon")
                if requested_horizon not in (None, "", [], {}):
                    requested_values = [*requested_values, requested_horizon]
                for horizon in requested_values:
                    clean = _clean_prompt_semantic_text(horizon).lower()
                    if clean and clean not in horizons:
                        horizons.append(clean)
        return horizons

    @staticmethod
    def _prompt_context_horizons(prompt: dict[str, Any]) -> list[str]:
        horizons: list[str] = []

        def add(value: Any) -> None:
            clean = _clean_prompt_semantic_text(value).lower()
            if clean and clean not in horizons:
                horizons.append(clean)

        for key in (
            "horizon",
            "decision_horizon",
            "target_horizon",
            "selected_horizon",
        ):
            add(prompt.get(key))
        for container_key in (
            "decision_context",
            "market_context",
            "block",
            "candidate",
            "jue_workflow",
        ):
            container = prompt.get(container_key)
            if not isinstance(container, dict):
                continue
            for key in (
                "horizon",
                "decision_horizon",
                "target_horizon",
                "selected_horizon",
            ):
                add(container.get(key))
        return horizons

    @classmethod
    def _selected_page_effectiveness_statuses(cls, value: Any) -> list[str]:
        rows = value if isinstance(value, list) else [value]
        statuses: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            for raw_status in [
                row.get("status"),
                *[
                    metric.get("status")
                    for metric in list(row.get("metrics") or [])
                    if isinstance(metric, dict)
                ],
            ]:
                status = _clean_prompt_semantic_text(raw_status).lower()
                if status and status not in statuses:
                    statuses.append(status)
        return statuses

    @classmethod
    def _selected_page_effectiveness_attention_items(
        cls,
        row: dict[str, Any],
    ) -> list[dict[str, Any]]:
        page_ids = cls._clean_page_ids([row.get("page_id")])
        if not page_ids:
            return []
        page_id = page_ids[0]
        items: list[dict[str, Any]] = []
        for kind, key in (
            ("usage_guidance", "usage_guidance_effectiveness"),
            ("memory_card_quality", "memory_card_quality_effectiveness"),
            ("quality_warning_source", "quality_warning_source_effectiveness"),
            ("quality_warning", "quality_warning_effectiveness"),
        ):
            for item in cls._effectiveness_attention_items_for_value(
                page_id=page_id,
                kind=kind,
                value=row.get(key),
            ):
                if item not in items:
                    items.append(item)
        return items

    @staticmethod
    def _effectiveness_attention_items_for_value(
        *,
        page_id: str,
        kind: str,
        value: Any,
    ) -> list[dict[str, Any]]:
        rows = value if isinstance(value, list) else [value]
        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metrics = [
                metric
                for metric in list(row.get("metrics") or [])
                if isinstance(metric, dict)
            ]
            source_rows = metrics or [row]
            for source in source_rows:
                status = _clean_prompt_semantic_text(
                    source.get("status") or row.get("status")
                ).lower()
                evidence_id = (
                    ""
                    if kind == "quality_warning"
                    else _clean_prompt_semantic_text(
                        source.get("page_id")
                        or source.get("source_id")
                        or source.get("rule_id")
                    )
                )
                warning = _clean_prompt_semantic_text(
                    source.get("warning") or row.get("warning")
                )
                if not status and not evidence_id and not warning:
                    continue
                item: dict[str, Any] = {
                    "page_id": page_id,
                    "kind": kind,
                }
                if status:
                    item["status"] = status
                if evidence_id:
                    item["evidence_id"] = evidence_id
                if warning:
                    item["warning"] = warning
                if item not in items:
                    items.append(item)
        return items

    def _enriched_selected_wiki_page(
        self,
        *,
        page_id: str,
        prompt_page: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._current_wiki_page_summary(page_id)
        if not fallback:
            return dict(prompt_page)
        result = dict(fallback)
        result.update(
            {
                key: value
                for key, value in prompt_page.items()
                if value not in (None, "", [], {})
            }
        )
        if _safe_float(result.get("confidence")) <= 0 and _safe_float(
            fallback.get("confidence")
        ) > 0:
            result["confidence"] = fallback.get("confidence")
        for key in ("page_type", "symbols", "source_refs", "quality_warnings"):
            if result.get(key) in (None, "", [], {}):
                result[key] = fallback.get(key)
        return result

    def _current_wiki_page_summary(self, page_id: str) -> dict[str, Any]:
        clean_page_id = str(page_id).strip()
        if not clean_page_id:
            return {}
        if self._page_summary_cache is None:
            self._page_summary_cache = {
                str(page.get("page_id") or ""): {
                    "page_id": str(page.get("page_id") or ""),
                    "page_type": str(page.get("page_type") or ""),
                    "symbols": [
                        str(symbol).strip().upper()
                        for symbol in list(page.get("symbols") or [])[:8]
                        if str(symbol).strip()
                    ],
                    "confidence": _safe_float(page.get("confidence")),
                    "source_refs": [
                        ref
                        for ref in self._selected_page_source_refs(page)[:12]
                        if isinstance(ref, dict)
                    ],
                }
                for page in self.wiki.search_pages(include_content=False)
                if str(page.get("page_id") or "").strip()
            }
        return dict(self._page_summary_cache.get(clean_page_id) or {})

    def _selected_wiki_page_row(
        self,
        *,
        page_id: str,
        page: dict[str, Any],
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        warnings = self._selected_wiki_page_quality_warnings(page)
        quality_status = self._selected_wiki_page_quality_status(page)
        usage_guidance = self._selected_wiki_page_usage_guidance(
            quality_status=quality_status,
            quality_warnings=warnings,
        )
        if not usage_guidance and isinstance(page.get("usage_guidance"), dict):
            usage_guidance = dict(page.get("usage_guidance") or {})
        repair_targets = self._selected_page_repair_targets_for_evidence(page)
        repair_effectiveness_rows = (
            self._selected_page_repair_target_effectiveness_for_evidence(page)
        )
        repair_effectiveness: dict[str, Any] | list[dict[str, Any]] = (
            repair_effectiveness_rows[0]
            if len(repair_effectiveness_rows) == 1
            else repair_effectiveness_rows
        )
        repair_queue = self._selected_wiki_page_repair_queue(
            page_id=page_id,
            page=page,
        )
        evidence_quality = self._selected_wiki_page_evidence_quality(
            page_id=page_id,
            page=page,
        )
        quality_warning_effectiveness_rows = (
            self._selected_page_quality_warning_effectiveness_for_evidence(page)
        )
        quality_warning_effectiveness_statuses: list[str] = []
        for row in quality_warning_effectiveness_rows:
            status = str(row.get("status") or "").strip().lower()
            if status and status not in quality_warning_effectiveness_statuses:
                quality_warning_effectiveness_statuses.append(status)
        memory_card = (
            page.get("memory_card")
            if isinstance(page.get("memory_card"), dict)
            else {}
        )
        memory_card_quality = self._selected_wiki_page_memory_card_quality(
            memory_card=memory_card,
            page_symbols=page.get("symbols"),
        )
        if (
            not memory_card_quality
            and isinstance(page.get("memory_card_quality"), dict)
        ):
            memory_card_quality = dict(page.get("memory_card_quality") or {})
        usage_guidance_effectiveness = (
            self._selected_wiki_page_usage_guidance_effectiveness(
                page_id=page_id,
                usage_guidance=usage_guidance,
                horizons=horizons,
            )
        )
        if not usage_guidance_effectiveness and isinstance(
            page.get("usage_guidance_effectiveness"),
            dict,
        ):
            usage_guidance_effectiveness = dict(
                page.get("usage_guidance_effectiveness") or {}
            )
        memory_card_quality_effectiveness = (
            self._selected_wiki_page_memory_card_quality_effectiveness(
                page_id=page_id,
                memory_card_quality=memory_card_quality,
                horizons=horizons,
            )
        )
        if not memory_card_quality_effectiveness and isinstance(
            page.get("memory_card_quality_effectiveness"),
            dict,
        ):
            memory_card_quality_effectiveness = dict(
                page.get("memory_card_quality_effectiveness") or {}
            )
        quality_warning_source_effectiveness = (
            self._selected_wiki_page_quality_warning_source_effectiveness(
                page_id=page_id,
                horizons=horizons,
            )
        )
        if not quality_warning_source_effectiveness and isinstance(
            page.get("quality_warning_source_effectiveness"),
            dict,
        ):
            quality_warning_source_effectiveness = (
                self._quality_warning_source_effectiveness_for_selected_page(
                    dict(page.get("quality_warning_source_effectiveness") or {}),
                    selected_page={**page, "page_id": page_id},
                )
            )
        selection_rank = _safe_int(
            page.get("selection_rank")
            if page.get("selection_rank") not in (None, "")
            else page.get("rank")
        )
        selection_score = _safe_float(
            page.get("selection_score")
            if page.get("selection_score") not in (None, "")
            else page.get("score")
        )
        selection_char_count = _safe_int(
            page.get("selection_char_count")
            if page.get("selection_char_count") not in (None, "")
            else page.get("char_count")
        )
        compact = {
            "page_id": page_id,
            "page_type": str(page.get("page_type") or ""),
            "symbols": self._selected_page_symbol_list(
                page_id=page_id,
                page=page,
                limit=8,
            ),
            **self._selected_wiki_page_lane_metadata(page),
            "quality_status": quality_status,
            "quality_warnings": warnings,
            "evidence_quality": evidence_quality,
            "quality_warning_effectiveness": quality_warning_effectiveness_rows,
            "quality_warning_effectiveness_statuses": (
                quality_warning_effectiveness_statuses
            ),
            "usage_guidance": usage_guidance,
            "usage_guidance_effectiveness": usage_guidance_effectiveness,
            "quality_warning_source_effectiveness": quality_warning_source_effectiveness,
            "repair_targets": repair_targets,
            "repair_target_effectiveness": repair_effectiveness,
            "repair_queue": repair_queue,
            "applied_via": str(page.get("applied_via") or ""),
            "selection_rank": selection_rank if selection_rank > 0 else None,
            "selection_score": round(selection_score, 4)
            if selection_score
            else None,
            "selection_reasons": _compact_prompt_string_list(
                page.get("selection_reasons") or page.get("reasons"),
                limit=8,
                max_len=180,
            ),
            "selection_penalties": _compact_prompt_string_list(
                page.get("selection_penalties") or page.get("penalties"),
                limit=8,
                max_len=180,
            ),
            "selection_char_count": (
                selection_char_count if selection_char_count > 0 else None
            ),
            "prompt_summary_present": True
            if str(page.get("summary") or "").strip()
            else None,
            "memory_card_keys": sorted(
                str(key)
                for key in memory_card.keys()
                if str(key).strip()
            )[:8],
            "memory_card_quality": memory_card_quality,
            "memory_card_quality_effectiveness": memory_card_quality_effectiveness,
        }
        if _has_prompt_value(page.get("confidence")):
            compact["confidence"] = round(_safe_float(page.get("confidence")), 4)
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    def _selected_wiki_page_memory_card_quality_effectiveness(
        self,
        *,
        page_id: str,
        memory_card_quality: dict[str, Any],
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        quality = memory_card_quality if isinstance(memory_card_quality, dict) else {}
        if not quality:
            return {}
        scope = self._scope_from_page_id(page_id)
        if not scope:
            return {}
        metrics_by_page = self._effectiveness_map_for_scope(
            scope,
            horizons=horizons,
        )
        metric_page_ids: list[str] = []
        for field in list(quality.get("missing_fields") or [])[:8]:
            value = str(field).strip()
            if value:
                metric_page_ids.append(
                    self._memory_card_quality_page_id(
                        category="missing_field",
                        value=value,
                    )
                )
        for check in list(quality.get("required_checks") or [])[:8]:
            value = str(check).strip()
            if value:
                metric_page_ids.append(
                    self._memory_card_quality_page_id(
                        category="required_check",
                        value=value,
                    )
                )
        metrics = [
            self._compact_usage_guidance_effectiveness_metric(
                metrics_by_page.get(metric_page_id)
            )
            for metric_page_id in metric_page_ids
        ]
        metrics = [metric for metric in metrics if metric]
        if not metrics:
            return {}
        statuses = [str(metric.get("status") or "").strip().lower() for metric in metrics]
        if "degraded" in statuses:
            status = "degraded"
            decision_use = (
                "prior memory card quality evidence is degraded; resolve missing "
                "memory fields and required checks before confident block design"
            )
        elif "active" in statuses:
            status = "active"
            decision_use = (
                "prior memory card quality evidence is positive; still keep the "
                "listed memory checks explicit before increasing conviction"
            )
        else:
            status = "probe"
            decision_use = (
                "memory card quality evidence is still in probe; keep memory "
                "field and check gaps visible in block design"
            )
        return {
            "status": status,
            "metrics": metrics[:8],
            "decision_use": decision_use,
        }

    def _selected_wiki_page_quality_warning_source_effectiveness(
        self,
        *,
        page_id: str,
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        scope = self._scope_from_page_id(page_id)
        if not scope:
            return {}
        metric = self._compact_usage_guidance_effectiveness_metric(
            self._effectiveness_map_for_scope(scope, horizons=horizons).get(page_id)
        )
        if not metric:
            return {}
        reasons = [
            str(reason).strip()
            for reason in list(metric.get("reasons") or [])
            if str(reason).strip()
        ]
        if "quality_warning_source_page" not in reasons:
            return {}
        status = str(metric.get("status") or "").strip().lower()
        if status == "degraded":
            decision_use = (
                "this selected page previously contributed to unresolved quality "
                "pressure; require warning-specific repair or live cross-checks "
                "before using it as a block thesis"
            )
        elif status == "active":
            decision_use = (
                "this selected page has positive quality-warning source evidence; "
                "still keep warning-specific live checks explicit"
            )
        else:
            decision_use = (
                "this selected page has limited quality-warning source evidence; "
                "treat it as a probe until outcomes accumulate"
            )
        return {
            "status": status or "probe",
            "metrics": [metric],
            "decision_use": decision_use,
        }

    @staticmethod
    def _selected_wiki_page_memory_card_quality(
        *,
        memory_card: dict[str, Any],
        page_symbols: Any,
    ) -> dict[str, Any]:
        trading_history = str(memory_card.get("trading_history") or "")
        if "Jue Wiki Memory Card Quality" not in trading_history:
            return {}
        fallback_symbols = [
            str(symbol).strip().upper()
            for symbol in list(page_symbols or [])
            if str(symbol).strip()
        ]
        items: list[dict[str, Any]] = []
        for line in trading_history.splitlines():
            if "manager_run=" not in line or "resolution=unresolved" not in line:
                continue
            pairs = {
                match.group(1): match.group(2).strip()
                for match in re.finditer(r"([A-Za-z_]+)=([^,]+)", line)
            }
            symbols = [
                symbol.strip().upper()
                for symbol in re.split(r"[|/ ]+", str(pairs.get("symbols") or ""))
                if symbol.strip()
            ]
            missing_fields = [
                item.strip()
                for item in re.split(
                    r"[|/ ]+",
                    str(pairs.get("missing_fields") or ""),
                )
                if item.strip()
            ]
            required_checks = [
                item.strip()
                for item in str(pairs.get("required_checks") or "").split("|")
                if item.strip()
            ]
            items.append(
                {
                    key: value
                    for key, value in {
                        "status": str(pairs.get("status") or "").strip(),
                        "resolution": str(pairs.get("resolution") or "").strip(),
                        "symbols": symbols or fallback_symbols,
                        "required_action": str(
                            pairs.get("required") or ""
                        ).strip(),
                        "missing_fields": missing_fields,
                        "required_checks": required_checks,
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        if not items:
            return {}

        def unique_values(rows: list[dict[str, Any]], key: str) -> list[str]:
            values: list[str] = []
            for row in rows:
                for item in list(row.get(key) or []):
                    value = str(item).strip()
                    if value and value not in values:
                        values.append(value)
            return values

        primary = items[0]
        missing_fields = unique_values(items, "missing_fields")
        required_checks = unique_values(items, "required_checks")
        include_items = len(items) > 1 or bool(missing_fields or required_checks)
        return {
            key: value
            for key, value in {
                "status": str(primary.get("status") or "").strip(),
                "resolution": str(primary.get("resolution") or "").strip(),
                "symbols": primary.get("symbols") or fallback_symbols,
                "required_action": str(primary.get("required_action") or "").strip(),
                "missing_fields": missing_fields,
                "required_checks": required_checks,
                "items": items if include_items else [],
                "decision_use": "memory_card_quality_resolution_check",
                "candidate_resolution_required": True,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _selected_wiki_page_evidence_quality(
        *,
        page_id: str,
        page: dict[str, Any],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        direct_quality = (
            page.get("evidence_quality")
            if isinstance(page.get("evidence_quality"), dict)
            else {}
        )
        if direct_quality:
            rows.append(
                {
                    "page_id": page_id,
                    "quality_status": normalize_jue_wiki_quality_status(
                        page.get("quality_status")
                    ),
                    "quality_warnings": [
                        str(item).strip()
                        for item in list(page.get("quality_warnings") or [])
                        if str(item).strip()
                    ],
                    "evidence_quality": direct_quality,
                }
            )
        refs = JueWikiApplicationService._selected_page_source_refs(page)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            row = dict(ref)
            row.setdefault("page_id", page_id)
            rows.append(row)
        return summarize_jue_wiki_quality_pressure_for_prompt(rows)

    @staticmethod
    def _selected_wiki_page_usage_guidance(
        *,
        quality_status: str,
        quality_warnings: list[str],
    ) -> dict[str, Any]:
        status = normalize_jue_wiki_quality_status(quality_status)
        warnings = [
            str(warning).strip()
            for warning in list(quality_warnings or [])
            if str(warning).strip()
        ]
        if status not in {"weak", "partial", "unknown"} and not warnings:
            return {}

        if status == "weak":
            trust_level = "low"
            risk_posture = "repair_cross_check"
            decision_use = (
                "use this page to design repair, waiting, or small probe blocks only "
                "after live cross-checks"
            )
            allowed_uses = [
                "repair_candidate_design",
                "waiting_block",
                "small_probe_block",
                "candidate_level_reject",
            ]
        else:
            trust_level = "medium" if status == "partial" else "low"
            risk_posture = "supporting_cross_check"
            decision_use = (
                "use this page as supporting context after resolving candidate-level "
                "evidence gaps"
            )
            allowed_uses = [
                "supporting_context",
                "follow_up_research",
                "target_stop_context",
                "risk_note_context",
            ]

        required_cross_checks = (
            JueWikiApplicationService._cross_checks_for_quality_warnings(warnings)
        )
        if not required_cross_checks:
            required_cross_checks = ["live_quote", "fresh_research_conflicts"]
        elif "live_quote" not in required_cross_checks:
            required_cross_checks.insert(0, "live_quote")
        return {
            "trust_level": trust_level,
            "risk_posture": risk_posture,
            "decision_use": decision_use,
            "allowed_uses": allowed_uses,
            "required_cross_checks": required_cross_checks,
            "hard_blocker": False,
        }

    def _selected_wiki_page_usage_guidance_effectiveness(
        self,
        *,
        page_id: str,
        usage_guidance: dict[str, Any],
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        guidance = usage_guidance if isinstance(usage_guidance, dict) else {}
        if not guidance:
            return {}
        scope = self._scope_from_page_id(page_id)
        if not scope:
            return {}
        metrics_by_page = self._effectiveness_map_for_scope(
            scope,
            horizons=horizons,
        )
        metric_page_ids: list[str] = []
        risk_posture = str(guidance.get("risk_posture") or "").strip()
        if risk_posture:
            metric_page_ids.append(
                self._usage_guidance_page_id(
                    category="risk_posture",
                    value=risk_posture,
                )
            )
        for item in list(guidance.get("required_cross_checks") or [])[:8]:
            cross_check = str(item).strip()
            if not cross_check:
                continue
            metric_page_ids.append(
                self._usage_guidance_page_id(
                    category="cross_check",
                    value=cross_check,
                )
            )
        metrics = [
            self._compact_usage_guidance_effectiveness_metric(
                metrics_by_page.get(metric_page_id)
            )
            for metric_page_id in metric_page_ids
        ]
        metrics = [metric for metric in metrics if metric]
        if not metrics:
            return {}
        statuses = [str(metric.get("status") or "").strip().lower() for metric in metrics]
        if "degraded" in statuses:
            status = "degraded"
            decision_use = (
                "prior usage guidance is degraded; audit this page usage before "
                "letting it shape block design"
            )
        elif "active" in statuses:
            status = "active"
            decision_use = "prior usage guidance has positive evidence; still cross-check live execution data"
        else:
            status = "probe"
            decision_use = "prior usage guidance evidence is still in probe; keep candidate-level checks explicit"
        return {
            "status": status,
            "metrics": metrics[:8],
            "decision_use": decision_use,
        }

    def _effectiveness_map_for_scope(
        self,
        scope: str,
        *,
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        if not clean_scope:
            return {}
        clean_horizons = tuple(
            dict.fromkeys(
                str(horizon).strip().lower()
                for horizon in list(horizons or [])
                if str(horizon).strip()
            )
        )
        cache_key = (clean_scope, clean_horizons)
        if cache_key not in self._effectiveness_map_cache:
            self._effectiveness_map_cache[cache_key] = self.wiki.page_effectiveness_map(
                decision_scope=clean_scope,
                horizons=clean_horizons,
            )
        return self._effectiveness_map_cache[cache_key]

    @staticmethod
    def _scope_from_page_id(page_id: str) -> str:
        clean = str(page_id or "").strip()
        if "." not in clean:
            return ""
        return clean.split(".", 1)[0].strip().lower()

    @staticmethod
    def _compact_usage_guidance_effectiveness_metric(
        source: Any,
    ) -> dict[str, Any]:
        if not isinstance(source, dict) or not source:
            return {}
        reasons = _json_loads(str(source.get("reasons_json") or "[]"), [])
        compact = {
            "page_id": str(source.get("page_id") or ""),
            "venue": str(source.get("venue") or ""),
            "horizon": str(source.get("horizon") or ""),
            "requested_horizons": [
                str(item).strip()
                for item in list(source.get("requested_horizons") or [])[:4]
                if str(item).strip()
            ],
            "fallback_reason": str(source.get("fallback_reason") or ""),
            "status": str(source.get("status") or ""),
            "reasons": [
                str(item)[:180]
                for item in list(reasons or [])[:6]
                if str(item).strip()
            ],
        }
        _add_prompt_int(compact, source, "sample_count")
        for key in ("win_rate", "helpful_score", "confidence"):
            _add_prompt_float(compact, source, key)
        if _has_prompt_value(source.get("expectancy")):
            compact["expectancy"] = _safe_float(source.get("expectancy"))
        elif _has_prompt_value(source.get("avg_return_pct")):
            compact["expectancy"] = _safe_float(source.get("avg_return_pct"))
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _cross_checks_for_quality_warnings(warnings: list[str]) -> list[str]:
        checks: list[str] = []
        for warning in warnings:
            if warning in {"price_missing", "quote_missing"}:
                candidate_checks = ["live_quote"]
            elif warning in {
                "financials_missing",
                "financial_rows_rejected_credit_rating",
            }:
                candidate_checks = ["fresh_financials_or_valuation_cross_check"]
            elif warning in {
                "valuation_metrics_sparse",
                "valuation_stale_gt_7d",
                "valuation_stale_gt_30d",
            } or warning.startswith("valuation_stale") or warning.startswith(
                "valuation_aging"
            ):
                candidate_checks = ["fresh_valuation_cross_check"]
            elif warning == "identity_name_missing":
                candidate_checks = ["symbol_identity_cross_check"]
            else:
                candidate_checks = ["fresh_research_conflicts"]
            for check in candidate_checks:
                if check not in checks:
                    checks.append(check)
        preferred_order = [
            "live_quote",
            "symbol_identity_cross_check",
            "fresh_financials_or_valuation_cross_check",
            "fresh_valuation_cross_check",
            "fresh_research_conflicts",
        ]
        checks.sort(
            key=lambda item: (
                preferred_order.index(item)
                if item in preferred_order
                else len(preferred_order),
                item,
            )
        )
        return checks[:8]

    @staticmethod
    def _selected_wiki_page_quality_status(page: dict[str, Any]) -> str:
        direct_status = normalize_jue_wiki_quality_status(page.get("quality_status"))
        if direct_status:
            return direct_status
        evidence_status = _quality_status_from_evidence(page.get("evidence_quality"))
        if evidence_status:
            return evidence_status
        refs = JueWikiApplicationService._selected_page_source_refs(page)
        status_counts: dict[str, int] = {}
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            status = normalize_jue_wiki_quality_status(ref.get("quality_status"))
            if not status:
                status = _quality_status_from_evidence(ref.get("evidence_quality"))
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
        for status in ("weak", "partial", "unknown", "strong"):
            if status_counts.get(status):
                return status
        return ""

    @staticmethod
    def _selected_wiki_page_quality_warnings(page: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        for item in list(page.get("quality_warnings") or []):
            warning = _clean_prompt_semantic_text(item)
            if warning and warning not in warnings:
                warnings.append(warning)
        for warning in _quality_warnings_from_evidence(page.get("evidence_quality")):
            clean_warning = _clean_prompt_semantic_text(warning)
            if clean_warning and clean_warning not in warnings:
                warnings.append(clean_warning)
        refs = JueWikiApplicationService._selected_page_source_refs(page)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            for warning in _quality_warnings_from_evidence(ref.get("evidence_quality")):
                clean_warning = _clean_prompt_semantic_text(warning)
                if clean_warning and clean_warning not in warnings:
                    warnings.append(clean_warning)
            for item in list(ref.get("quality_warnings") or []):
                warning = _clean_prompt_semantic_text(item)
                if warning and warning not in warnings:
                    warnings.append(warning)
        return warnings[:12]

    @staticmethod
    def _selected_wiki_page_repair_queue(
        *,
        page_id: str,
        page: dict[str, Any],
    ) -> dict[str, Any]:
        refs = JueWikiApplicationService._selected_page_source_refs(page)
        queue_ref: dict[str, Any] = {}
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            if str(ref.get("source_type") or "").strip().lower() == "wiki_repair_queue":
                queue_ref = ref
                break
        if not queue_ref and not page_id.endswith(".research.repair_queue"):
            return {}
        symbols = queue_ref.get("symbols") if queue_ref else page.get("symbols")
        action_type = _clean_prompt_semantic_text(queue_ref.get("action_type"))
        decision_use = ""
        if action_type == "repair_usage_guidance_contract":
            decision_use = "usage_guidance_effectiveness_repair"
        elif action_type == "reproject_closed_block_outcome_horizons":
            decision_use = "horizon_lane_attribution_repair"
        closed_block_outcome_horizon_gap = _safe_int(
            queue_ref.get("closed_block_outcomes_without_horizon")
        )
        closed_block_outcome_horizon_gap_pct = _safe_float(
            queue_ref.get("closed_block_outcomes_without_horizon_pct")
        )
        return {
            key: value
            for key, value in {
                "status": _clean_prompt_semantic_text(queue_ref.get("status")),
                "source_id": _clean_prompt_semantic_text(queue_ref.get("source_id")),
                "action_type": action_type,
                "symbols": [
                    str(symbol).strip().upper()
                    for symbol in list(symbols or [])[:8]
                    if str(symbol).strip()
                ],
                "quality_warnings": _compact_prompt_string_list(
                    queue_ref.get("quality_warnings"),
                    limit=8,
                    max_len=120,
                ),
                "diagnostic_reasons": _compact_prompt_string_list(
                    queue_ref.get("diagnostic_reasons"),
                    limit=8,
                    max_len=180,
                ),
                "closed_block_outcomes_without_horizon": (
                    closed_block_outcome_horizon_gap
                    if closed_block_outcome_horizon_gap > 0
                    else None
                ),
                "closed_block_outcomes_without_horizon_pct": (
                    closed_block_outcome_horizon_gap_pct
                    if closed_block_outcome_horizon_gap_pct > 0
                    else None
                ),
                "repair_action": _clean_prompt_semantic_text(
                    queue_ref.get("repair_action")
                ),
                "repair_targets": _compact_prompt_repair_targets(
                    queue_ref.get("repair_targets")
                ),
                "decision_use": decision_use,
                "hard_blocker": False if decision_use else None,
                "candidate_resolution_required": True if decision_use else None,
            }.items()
            if value not in (None, "", [], {})
        }

    def _prompt_action_pressure_contract_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        contract = (
            prompt.get("jue_wiki_action_pressure_contract")
            if isinstance(prompt.get("jue_wiki_action_pressure_contract"), dict)
            else {}
        )
        if not contract:
            return {}
        page_ids = [
            str(page_id).strip()
            for page_id in list(contract.get("page_ids") or [])[:8]
            if str(page_id).strip()
        ]
        accepted = [
            str(item)[:220]
            for item in list(contract.get("accepted_resolutions") or [])[:8]
            if str(item).strip()
        ]
        return {
            key: value
            for key, value in {
                "status": str(contract.get("status") or ""),
                "page_ids": page_ids,
                "required_when": str(contract.get("required_when") or "")[:240],
                "core_rule": str(contract.get("core_rule") or "")[:360],
                "accepted_resolutions": accepted,
                "hold_only_contract": str(
                    contract.get("hold_only_contract") or ""
                )[:360],
            }.items()
            if value not in (None, "", [], {})
        }

    def _prompt_validation_repair_contract_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        contract = (
            prompt.get("jue_wiki_validation_repair_contract")
            if isinstance(prompt.get("jue_wiki_validation_repair_contract"), dict)
            else {}
        )
        if not contract:
            return {}
        compact = {
            "version": str(contract.get("version") or "")[:160],
            "status": str(contract.get("status") or "")[:80],
            "top_disciplines": [
                str(item)[:160]
                for item in list(contract.get("top_disciplines") or [])[:8]
                if str(item).strip()
            ],
            "repair_action_ids": [
                str(item)[:160]
                for item in list(contract.get("repair_action_ids") or [])[:8]
                if str(item).strip()
            ],
            "entry_biases": [
                str(item)[:160]
                for item in list(contract.get("entry_biases") or [])[:8]
                if str(item).strip()
            ],
            "allowed_entry_postures": [
                str(item)[:180]
                for item in list(contract.get("allowed_entry_postures") or [])[:8]
                if str(item).strip()
            ],
            "blocked_entry_patterns": [
                str(item)[:180]
                for item in list(contract.get("blocked_entry_patterns") or [])[:8]
                if str(item).strip()
            ],
            "source_counts": {
                str(source).strip()[:180]: _safe_int(count)
                for source, count in (
                    contract.get("source_counts")
                    if isinstance(contract.get("source_counts"), dict)
                    else {}
                ).items()
                if str(source).strip() and _safe_int(count) > 0
            },
            "legacy_source_counts": {
                str(source).strip()[:180]: _safe_int(count)
                for source, count in (
                    contract.get("legacy_source_counts")
                    if isinstance(contract.get("legacy_source_counts"), dict)
                    else {}
                ).items()
                if str(source).strip() and _safe_int(count) > 0
            },
            "contract_source_counts": {
                str(source).strip()[:180]: _safe_int(count)
                for source, count in (
                    contract.get("contract_source_counts")
                    if isinstance(contract.get("contract_source_counts"), dict)
                    else {}
                ).items()
                if str(source).strip() and _safe_int(count) > 0
            },
            "source_mix_status": str(contract.get("source_mix_status") or "")[
                :120
            ],
            "source_mix_count_basis": str(
                contract.get("source_mix_count_basis") or ""
            )[:120],
            "contract_basis_pressure_summary": (
                self._prompt_validation_repair_contract_basis_pressure_summary(
                    contract
                )
            ),
            "contract_feedback_gap": (
                self._prompt_validation_repair_contract_feedback_gap(contract)
            ),
            "degraded_metric_evidence": (
                self._prompt_validation_repair_degraded_metric_evidence(contract)
            ),
            "required_response": str(contract.get("required_response") or "")[
                :480
            ],
            "accepted_resolutions": [
                str(item)[:220]
                for item in list(contract.get("accepted_resolutions") or [])[:8]
                if str(item).strip()
            ],
        }
        for key in (
            "hard_blocker",
            "requires_validation_repair_resolution",
            "safety_gates_still_override",
        ):
            _add_prompt_bool(compact, contract, key)
        for key in ("legacy_sample_count", "contract_sample_count"):
            _add_prompt_int(compact, contract, key)
        _add_prompt_float(compact, contract, "risk_budget_multiplier")
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    def _prompt_validation_repair_contract_feedback_gap(
        self,
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        gap = (
            contract.get("contract_feedback_gap")
            if isinstance(contract.get("contract_feedback_gap"), dict)
            else {}
        )
        if not gap:
            return {}
        compact = {
            "status": str(gap.get("status") or "")[:120],
            "required_response": str(gap.get("required_response") or "")[:360],
        }
        for key in ("legacy_sample_count", "contract_sample_count"):
            _add_prompt_int(compact, gap, key)
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    def _prompt_validation_repair_contract_basis_pressure_summary(
        self,
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        summary = (
            contract.get("contract_basis_pressure_summary")
            if isinstance(contract.get("contract_basis_pressure_summary"), dict)
            else {}
        )
        if not summary:
            return {}
        compact = {
            "status": str(summary.get("status") or "")[:80],
        }
        for key in ("sample_count", "missed_count", "resolved_count"):
            _add_prompt_int(compact, summary, key)
        for key in ("resolution_rate", "miss_rate", "repair_pressure_score"):
            _add_prompt_float(compact, summary, key)
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    def _prompt_validation_repair_degraded_metric_evidence(
        self,
        contract: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return self._compact_validation_repair_degraded_metric_evidence(
            contract.get("degraded_metric_evidence")
        )

    def _prompt_decision_adjustment_audit_contract_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        contract = (
            prompt.get("jue_wiki_decision_adjustment_audit_contract")
            if isinstance(
                prompt.get("jue_wiki_decision_adjustment_audit_contract"),
                dict,
            )
            else {}
        )
        if not contract:
            return {}
        compact = {
            "version": str(contract.get("version") or "")[:160],
            "status": str(contract.get("status") or "")[:80],
            "actions": [
                str(item)[:160]
                for item in list(contract.get("actions") or [])[:6]
                if str(item).strip()
            ],
            "target_risk_postures": [
                str(item)[:120]
                for item in list(contract.get("target_risk_postures") or [])[:6]
                if str(item).strip()
            ],
            "required_review": [
                str(item)[:240]
                for item in list(contract.get("required_review") or [])[:8]
                if str(item).strip()
            ],
            "accepted_resolutions": [
                str(item)[:240]
                for item in list(contract.get("accepted_resolutions") or [])[:8]
                if str(item).strip()
            ],
            "audit_policies": [
                self._compact_decision_adjustment_audit_policy(row)
                for row in list(contract.get("audit_policies") or [])[:6]
                if isinstance(row, dict)
                and self._compact_decision_adjustment_audit_policy(row)
            ],
            "audit_effectiveness": [
                self._prompt_decision_adjustment_effectiveness_summary(row)
                for row in list(contract.get("audit_effectiveness") or [])[:6]
                if isinstance(row, dict)
                and self._prompt_decision_adjustment_effectiveness_summary(row)
            ],
        }
        _add_prompt_int(compact, contract, "adjustment_count")
        for key in ("hard_blocker", "safety_gates_still_override"):
            _add_prompt_bool(compact, contract, key)
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _compact_decision_adjustment_audit_policy(
        source: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "action": str(source.get("action") or "")[:180],
                "reason": str(source.get("reason") or "")[:180],
                "target_risk_posture": str(
                    source.get("target_risk_posture") or ""
                )[:120],
                "hard_blocker": bool(source.get("hard_blocker")),
            }.items()
            if value not in (None, "", [], {})
        }

    def _manager_response_summary(
        self,
        response: dict[str, Any],
        actions: dict[str, Any],
    ) -> dict[str, Any]:
        action_counts = self._manager_action_counts(response=response, actions=actions)
        action_count = sum(action_counts.values())
        final_action_count = (
            _safe_int(response.get("final_action_count"))
            if response.get("final_action_count") not in (None, "", [], {})
            else action_count
        )
        repair_resolution = (
            response.get("validation_repair_resolution")
            if isinstance(response.get("validation_repair_resolution"), dict)
            else {}
        )
        hold_decision = (
            response.get("hold_decision")
            if isinstance(response.get("hold_decision"), dict)
            else {}
        )
        no_action_watch = (
            response.get("no_action_watch")
            if isinstance(response.get("no_action_watch"), dict)
            else {}
        )
        summary = {
            key: value
            for key, value in {
                "final_action_count": final_action_count,
                "action_counts": {
                    key: value for key, value in action_counts.items() if value > 0
                },
                "validation_repair_resolution": (
                    self._validation_repair_resolution_summary(repair_resolution)
                ),
                "hold_decision": self._response_hold_decision_summary(hold_decision),
                "no_action_watch": self._response_no_action_watch_summary(
                    no_action_watch
                ),
                "jue_wiki_repair_action_metadata": (
                    self._response_jue_wiki_repair_action_metadata_summary(
                        response=response,
                        actions=actions,
                    )
                ),
                "contract_error": str(response.get("contract_error") or "")[:220],
            }.items()
            if value not in (None, "", [], {})
        }
        return summary

    @staticmethod
    def _manager_action_counts(
        *,
        response: dict[str, Any],
        actions: dict[str, Any],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in (
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        ):
            value = actions.get(key)
            if not isinstance(value, list):
                value = response.get(key)
            if isinstance(value, list):
                counts[key] = len(value)
        return counts

    @staticmethod
    def _response_jue_wiki_repair_action_metadata_summary(
        *,
        response: dict[str, Any],
        actions: dict[str, Any],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        action_types = (
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        )

        def metadata_text(value: Any) -> str:
            if value in (None, "", [], {}):
                return ""
            if isinstance(value, str):
                return value.strip()[:600]
            if isinstance(value, (int, float, bool)):
                return str(value)[:600]
            if isinstance(value, dict):
                compact = {
                    str(key): clean_value
                    for key, raw in value.items()
                    for clean_value in [raw]
                    if clean_value not in (None, "", [], {})
                }
                return _json_dumps(compact)[:600] if compact else ""
            if isinstance(value, list):
                compact_items = [
                    item for item in value[:8] if item not in (None, "", [], {})
                ]
                return _json_dumps(compact_items)[:600] if compact_items else ""
            return str(value).strip()[:600]

        def row_metadata_value(row: dict[str, Any], key: str) -> Any:
            if row.get(key) not in (None, "", [], {}):
                return row.get(key)
            for metadata_key in ("metadata", "metadata_summary", "context_summary"):
                metadata = (
                    row.get(metadata_key)
                    if isinstance(row.get(metadata_key), dict)
                    else {}
                )
                if metadata.get(key) not in (None, "", [], {}):
                    return metadata.get(key)
            return None

        seen_action_symbols: set[tuple[str, str]] = set()

        def collect_row(
            action_type: str,
            row: Any,
            *,
            source_kind: str = "action",
        ) -> None:
            if len(rows) >= 12 or not isinstance(row, dict):
                return
            symbol = str(row.get("symbol") or "").strip().upper()[:40]
            action_symbol_key = (action_type, symbol)
            if source_kind == "applied" and symbol and action_symbol_key in seen_action_symbols:
                return
            repair_pressure = metadata_text(
                row_metadata_value(row, "jue_wiki_repair_pressure")
            )
            repair_resolution = metadata_text(
                row_metadata_value(row, "jue_wiki_repair_resolution")
            )
            memory_card_quality = metadata_text(
                row_metadata_value(row, "jue_wiki_memory_card_quality")
            )
            memory_card_cross_check = metadata_text(
                row_metadata_value(row, "jue_wiki_memory_card_cross_check")
            )
            if not (
                repair_pressure
                or repair_resolution
                or memory_card_quality
                or memory_card_cross_check
            ):
                return
            rows.append(
                {
                    key: value
                    for key, value in {
                        "action_type": action_type,
                        "symbol": symbol,
                        "market": str(
                            row.get("market")
                            or row_metadata_value(row, "market")
                            or ""
                        ).strip().lower()[:40],
                        "side": str(
                            row.get("side")
                            or row_metadata_value(row, "side")
                            or ""
                        ).strip().lower()[:40],
                        "horizon": str(
                            row.get("horizon")
                            or row_metadata_value(row, "horizon")
                            or ""
                        ).strip().lower()[:60],
                        "block_id": str(row.get("block_id") or "")[:120],
                        "jue_wiki_repair_pressure": repair_pressure,
                        "jue_wiki_repair_resolution": repair_resolution,
                        "jue_wiki_memory_card_quality": memory_card_quality,
                        "jue_wiki_memory_card_cross_check": (
                            memory_card_cross_check
                        ),
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
            if source_kind == "action" and symbol:
                seen_action_symbols.add(action_symbol_key)

        for action_type in action_types:
            source = actions.get(action_type)
            if not isinstance(source, list):
                source = response.get(action_type)
            if not isinstance(source, list):
                continue
            for row in source:
                collect_row(action_type, row)
                if len(rows) >= 12:
                    break
            if len(rows) >= 12:
                break
        applied = actions.get("_applied") if isinstance(actions.get("_applied"), dict) else {}
        for applied_key, action_type in (
            ("created", "create_blocks"),
            ("updated", "update_blocks"),
            ("closed", "close_blocks"),
            ("paused", "pause_blocks"),
        ):
            if len(rows) >= 12:
                break
            raw = applied.get(applied_key)
            if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                source_rows = raw.get("items") or []
            elif isinstance(raw, list):
                source_rows = raw
            else:
                source_rows = []
            for row in source_rows:
                collect_row(action_type, row, source_kind="applied")
                if len(rows) >= 12:
                    break
        if not rows:
            return {}
        return {"count": len(rows), "actions": rows}

    def _validation_repair_resolution_summary(
        self,
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        if not resolution:
            return {}
        rows: list[dict[str, Any]] = []
        for row in list(resolution.get("resolved_candidates") or [])[:8]:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    key: value
                    for key, value in {
                        "symbol": str(row.get("symbol") or "").strip().upper(),
                        "market": str(row.get("market") or "").strip().lower(),
                        "horizon": str(row.get("horizon") or "").strip().lower(),
                        "resolution": str(row.get("resolution") or "")[:120],
                        "next_trigger": str(row.get("next_trigger") or "")[:240],
                        "evidence_gap": str(row.get("evidence_gap") or "")[:240],
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        return {
            key: value
            for key, value in {
                "required": str(resolution.get("required") or "")[:220],
                "blanket_hold_allowed": resolution.get("blanket_hold_allowed"),
                "resolved_count": len(rows),
                "resolved_candidates": rows,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _response_hold_decision_summary(hold_decision: dict[str, Any]) -> dict[str, Any]:
        if not hold_decision:
            return {}
        triggers: list[dict[str, Any]] = []
        for row in list(hold_decision.get("next_triggers") or [])[:8]:
            if not isinstance(row, dict):
                continue
            triggers.append(
                {
                    key: value
                    for key, value in {
                        "symbol": str(row.get("symbol") or "").strip().upper(),
                        "market": str(row.get("market") or "").strip().lower(),
                        "horizon": str(row.get("horizon") or "").strip().lower(),
                        "condition": str(row.get("condition") or "")[:220],
                        "trigger": str(row.get("trigger") or "")[:220],
                        "price": row.get("price"),
                        "reason": str(row.get("reason") or "")[:220],
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        return {
            key: value
            for key, value in {
                "summary": str(hold_decision.get("summary") or "")[:320],
                "watch_symbols": [
                    str(symbol).strip().upper()
                    for symbol in list(hold_decision.get("watch_symbols") or [])[:12]
                    if str(symbol).strip()
                ],
                "next_triggers": triggers,
                "data_gaps": [
                    str(row)[:220]
                    for row in list(hold_decision.get("data_gaps") or [])[:8]
                    if str(row).strip()
                ],
                "risk_notes": [
                    str(row)[:220]
                    for row in list(hold_decision.get("risk_notes") or [])[:8]
                    if str(row).strip()
                ],
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _response_no_action_watch_summary(no_action_watch: dict[str, Any]) -> dict[str, Any]:
        if not no_action_watch:
            return {}
        return {
            key: value
            for key, value in {
                "status": str(no_action_watch.get("status") or "")[:120],
                "zero_action_streak": _safe_int(
                    no_action_watch.get("zero_action_streak")
                ),
                "pressure_level": str(no_action_watch.get("pressure_level") or "")[:120],
                "top_symbols": [
                    str(symbol).strip().upper()
                    for symbol in list(no_action_watch.get("top_symbols") or [])[:12]
                    if str(symbol).strip()
                ],
            }.items()
            if value not in (None, "", [], {})
        }

    def _prompt_validation_repair_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        repair = (
            prompt.get("validation_repair")
            if isinstance(prompt.get("validation_repair"), dict)
            else {}
        )
        if not repair:
            return {}
        backlog = [
            row
            for row in list(repair.get("repair_backlog") or [])[:6]
            if isinstance(row, dict)
        ]
        constraints = [
            row
            for row in list(repair.get("block_design_constraints") or [])[:6]
            if isinstance(row, dict)
        ]
        repair_item_count_explicit = repair.get("repair_item_count") not in (
            None,
            "",
            [],
            {},
        )
        constraint_count_explicit = repair.get("constraint_count") not in (
            None,
            "",
            [],
            {},
        )
        repair_item_count = (
            _safe_int(repair.get("repair_item_count"))
            if repair_item_count_explicit
            else len(backlog)
        )
        constraint_count = (
            _safe_int(repair.get("constraint_count"))
            if constraint_count_explicit
            else len(constraints)
        )
        if repair_item_count_explicit and repair_item_count <= 0:
            backlog = []
        if constraint_count_explicit and constraint_count <= 0:
            constraints = []
        rows = [*backlog, *constraints]
        return {
            key: value
            for key, value in {
                "version": str(repair.get("version") or ""),
                "scope": str(repair.get("scope") or ""),
                "status": str(repair.get("status") or ""),
                "repair_item_count": repair_item_count,
                "constraint_count": constraint_count,
                "discipline_ids": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="discipline_ids",
                    row_keys=("discipline_id",),
                ),
                "repair_action_ids": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="repair_action_ids",
                    row_keys=("repair_action_id",),
                ),
                "scale_blockers": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="scale_blockers",
                    row_keys=("scale_blocker",),
                ),
                "entry_biases": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="entry_biases",
                    row_keys=("entry_bias",),
                ),
                "allowed_entry_postures": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="allowed_entry_postures",
                    row_keys=("allowed_entry_posture",),
                ),
                "blocks_new_entries": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="blocks_new_entries",
                    row_keys=("blocks_new_entries",),
                ),
                "sizing_policies": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="sizing_policies",
                    row_keys=("sizing_policy",),
                ),
                "blocks_scaling": self._validation_repair_values(
                    repair,
                    rows,
                    top_key="blocks_scaling",
                    row_keys=("blocks_scaling",),
                ),
                "risk_budget_multiplier": self._validation_repair_float(
                    repair,
                    rows,
                    "risk_budget_multiplier",
                ),
                "max_budget_multiplier": self._validation_repair_float(
                    repair,
                    rows,
                    "max_budget_multiplier",
                ),
                "min_reward_risk": self._validation_repair_float(
                    repair,
                    rows,
                    "min_reward_risk",
                ),
                "max_stop_risk_pct": self._validation_repair_float(
                    repair,
                    rows,
                    "max_stop_risk_pct",
                ),
                "scale_up_blocked": bool(repair.get("scale_up_blocked"))
                or any(bool(row.get("scale_up_blocked")) for row in rows),
                "hard_filter": bool(repair.get("hard_filter")),
            }.items()
            if value not in (None, "", [], {})
        }

    def _prompt_proactive_pressure_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        pressure = (
            prompt.get("proactive_decision_pressure")
            if isinstance(prompt.get("proactive_decision_pressure"), dict)
            else {}
        )
        if not pressure:
            return {}
        return {
            "status": str(pressure.get("status") or ""),
            "pressure_level": str(pressure.get("pressure_level") or ""),
            "zero_action_streak": _safe_int(pressure.get("zero_action_streak")),
            "candidate_count": _safe_int(pressure.get("candidate_count")),
            "strong_candidate_count": _safe_int(
                pressure.get("strong_candidate_count")
            ),
            "top_symbols": self._prompt_pressure_top_symbols(pressure),
            "required_resolution": str(
                pressure.get("required_resolution") or ""
            )[:280],
        }

    def _prompt_execution_gate_summary(
        self,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        gate = (
            prompt.get("execution_gate")
            if isinstance(prompt.get("execution_gate"), dict)
            else {}
        )
        if not gate:
            return {}
        kill_switch = (
            gate.get("kill_switch")
            if isinstance(gate.get("kill_switch"), dict)
            else {}
        )
        cash = (
            gate.get("cash_available")
            if isinstance(gate.get("cash_available"), dict)
            else {}
        )
        duplicate_guard = (
            gate.get("duplicate_order_guard")
            if isinstance(gate.get("duplicate_order_guard"), dict)
            else {}
        )
        return {
            key: value
            for key, value in {
                "status": str(gate.get("status") or ""),
                "execution_mode": str(gate.get("execution_mode") or ""),
                "execute_orders": gate.get("execute_orders"),
                "live_venues": [
                    str(item)
                    for item in list(gate.get("live_venues") or [])[:8]
                    if str(item).strip()
                ],
                "kill_switch_enabled": bool(kill_switch.get("enabled")),
                "market_session": str(gate.get("market_session") or ""),
                "new_entry_allowed_by_session": gate.get(
                    "new_entry_allowed_by_session"
                ),
                "cash_available": {
                    str(cash_key): cash_value
                    for cash_key, cash_value in cash.items()
                    if str(cash_key).strip()
                    and cash_key
                    in {
                        "cash_krw",
                        "orderable_cash_krw",
                        "spot_cash_usdt",
                        "futures_cash_usdt",
                        "upbit_cash_krw",
                        "upbit_cash_usdt",
                        "total_equity_usdt",
                    }
                },
                "active_block_count": _safe_int(gate.get("active_block_count")),
                "waiting_entry_block_count": _safe_int(
                    gate.get("waiting_entry_block_count")
                ),
                "pending_order_block_count": _safe_int(
                    gate.get("pending_order_block_count")
                ),
                "duplicate_order_guard_status": str(
                    duplicate_guard.get("status") or ""
                ),
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _prompt_pressure_top_symbols(pressure: dict[str, Any]) -> list[str]:
        symbols: list[str] = []
        for row in list(pressure.get("top_candidates") or []):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= 12:
                break
        return symbols

    def _prompt_opportunity_summary(self, prompt: dict[str, Any]) -> dict[str, Any]:
        brief = (
            prompt.get("opportunity_research_brief")
            if isinstance(prompt.get("opportunity_research_brief"), dict)
            else {}
        )
        if not brief:
            return {}
        source_status = (
            brief.get("source_status")
            if isinstance(brief.get("source_status"), dict)
            else {}
        )
        return {
            "status": str(brief.get("status") or ""),
            "role": str(brief.get("role") or ""),
            "source_status": {
                str(key): str(value)
                for key, value in source_status.items()
                if str(key).strip()
            },
            "counts": {
                "pre_surge": len(list(brief.get("pre_surge_candidates") or [])),
                "block": len(list(brief.get("block_candidates") or [])),
                "daily_discovery": len(
                    list(brief.get("daily_discovery_candidates") or [])
                ),
                "aggressive": len(list(brief.get("aggressive_candidates") or [])),
            },
            "top_symbols": self._prompt_opportunity_top_symbols(brief),
        }

    @staticmethod
    def _prompt_opportunity_top_symbols(brief: dict[str, Any]) -> list[str]:
        symbols: list[str] = []
        for key in (
            "pre_surge_candidates",
            "block_candidates",
            "daily_discovery_candidates",
            "aggressive_candidates",
        ):
            for row in list(brief.get(key) or []):
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
                if len(symbols) >= 12:
                    return symbols
        return symbols

    def _blocks_for_decision_link(
        self,
        *,
        path: Path,
        scope: str,
        link: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if scope == "kis":
            return self._kis_blocks_for_decision_link(path=path, link=link)
        if scope == "binance":
            return self._binance_blocks_for_decision_link(path=path, link=link)
        return []

    def _block_count_for_decision_link(
        self,
        *,
        path: Path,
        scope: str,
        link: dict[str, Any],
    ) -> int:
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        manager_run_id = _safe_int(metadata.get("source_row_id"))
        block_id = str(link.get("block_id") or "").strip()
        with sqlite3.connect(path) as conn:
            if not self._source_table_exists(conn, "blocks"):
                raise sqlite3.OperationalError("missing table: blocks")
            columns = self._source_table_columns(conn, "blocks")
            clauses: list[str] = []
            params: list[Any] = []
            if block_id and "block_id" in columns:
                clauses.append("block_id = ?")
                params.append(block_id)
            elif manager_run_id and "manager_run_id" in columns:
                clauses.append("manager_run_id = ?")
                params.append(manager_run_id)
            else:
                return 0
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM blocks
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchone()
        return int(row[0] if row else 0)

    def _manager_pressure_outcome(
        self,
        *,
        scope: str,
        venue: str,
        link: dict[str, Any],
        related_block_count: int,
    ) -> dict[str, Any] | None:
        if related_block_count > 0:
            return None
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        pressure = (
            metadata.get("proactive_decision_pressure")
            if isinstance(metadata.get("proactive_decision_pressure"), dict)
            else {}
        )
        if str(pressure.get("status") or "") != "action_required":
            return None
        gate = (
            metadata.get("execution_gate")
            if isinstance(metadata.get("execution_gate"), dict)
            else {}
        )
        if self._execution_gate_blocks_pressure_outcome(gate):
            return None
        return {
            "outcome_kind": "missed_action_pressure",
            "outcome_status": "loss",
            "pnl_currency": "KRW" if scope == "kis" else "USDT",
            "return_pct": -0.1,
            "evidence": {
                "source": f"{scope}_manager_pressure",
                "reason": "action_required_without_block",
                "manager_run_id": metadata.get("source_row_id"),
                "zero_action_streak": pressure.get("zero_action_streak"),
                "pressure_level": pressure.get("pressure_level"),
                "candidate_count": pressure.get("candidate_count"),
                "strong_candidate_count": pressure.get("strong_candidate_count"),
                "top_symbols": pressure.get("top_symbols") or [],
                "execution_gate_status": gate.get("status"),
                "venue": venue,
            },
        }

    def _manager_wiki_action_pressure_outcome(
        self,
        *,
        scope: str,
        venue: str,
        link: dict[str, Any],
        related_block_count: int,
    ) -> dict[str, Any] | None:
        if related_block_count > 0:
            return None
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        contract = (
            metadata.get("jue_wiki_action_pressure_contract")
            if isinstance(metadata.get("jue_wiki_action_pressure_contract"), dict)
            else {}
        )
        page_ids = [
            str(page_id).strip()
            for page_id in list(contract.get("page_ids") or [])
            if str(page_id).strip()
        ]
        if str(contract.get("status") or "").strip().lower() != "active" and not page_ids:
            return None
        gate = (
            metadata.get("execution_gate")
            if isinstance(metadata.get("execution_gate"), dict)
            else {}
        )
        if self._execution_gate_blocks_pressure_outcome(gate):
            return None
        response = (
            metadata.get("manager_response")
            if isinstance(metadata.get("manager_response"), dict)
            else {}
        )
        hold = (
            response.get("hold_decision")
            if isinstance(response.get("hold_decision"), dict)
            else {}
        )
        if self._hold_decision_has_concrete_next_step(hold):
            return {
                "outcome_kind": "resolved_wiki_action_pressure",
                "outcome_status": "flat",
                "pnl_currency": "KRW" if scope == "kis" else "USDT",
                "return_pct": 0.0,
                "evidence": {
                    "source": f"{scope}_jue_wiki_action_pressure_contract",
                    "reason": "wiki_action_pressure_resolved_without_block",
                    "manager_run_id": metadata.get("source_row_id"),
                    "page_ids": page_ids,
                    "next_triggers": hold.get("next_triggers") or [],
                    "watch_symbols": hold.get("watch_symbols") or [],
                    "execution_gate_status": gate.get("status"),
                    "venue": venue,
                },
            }
        return {
            "outcome_kind": "missed_wiki_action_pressure",
            "outcome_status": "loss",
            "pnl_currency": "KRW" if scope == "kis" else "USDT",
            "return_pct": -0.09,
            "evidence": {
                "source": f"{scope}_jue_wiki_action_pressure_contract",
                "reason": "wiki_action_pressure_without_resolution",
                "manager_run_id": metadata.get("source_row_id"),
                "page_ids": page_ids,
                "core_rule": contract.get("core_rule"),
                "accepted_resolutions": contract.get("accepted_resolutions") or [],
                "execution_gate_status": gate.get("status"),
                "venue": venue,
            },
        }

    def _manager_wiki_quality_pressure_outcome(
        self,
        *,
        scope: str,
        venue: str,
        link: dict[str, Any],
        related_block_count: int,
    ) -> dict[str, Any] | None:
        if related_block_count > 0:
            return None
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        plan = (
            metadata.get("jue_wiki_quality_pressure_action_plan")
            if isinstance(metadata.get("jue_wiki_quality_pressure_action_plan"), dict)
            else {}
        )
        if not plan:
            return None
        status = str(plan.get("status") or "").strip().lower()
        if status not in {"repair_required", "probe"}:
            return None
        gate = (
            metadata.get("execution_gate")
            if isinstance(metadata.get("execution_gate"), dict)
            else {}
        )
        if self._execution_gate_blocks_pressure_outcome(gate):
            return None
        warnings = [
            str(row.get("warning") or "").strip()
            for row in list(plan.get("repair_focus") or [])
            if isinstance(row, dict) and str(row.get("warning") or "").strip()
        ]
        if not warnings:
            warnings = [
                str(row.get("warning") or "").strip()
                for row in list(plan.get("required_adjustments") or [])
                if isinstance(row, dict) and str(row.get("warning") or "").strip()
            ]
        warnings = list(dict.fromkeys(warnings))
        warning_page_ids: dict[str, list[str]] = {}
        summary = (
            metadata.get("jue_wiki_quality_summary")
            if isinstance(metadata.get("jue_wiki_quality_summary"), dict)
            else {}
        )
        summary_warning_page_ids = (
            summary.get("warning_page_ids")
            if isinstance(summary.get("warning_page_ids"), dict)
            else {}
        )
        for warning, page_ids in summary_warning_page_ids.items():
            warning_key = str(warning).strip()
            clean_ids = self._clean_page_ids(page_ids)[:8]
            if warning_key and clean_ids:
                warning_page_ids[warning_key] = clean_ids
        repair_focus_rows = [
            row for row in list(plan.get("repair_focus") or []) if isinstance(row, dict)
        ]
        required_adjustment_rows = [
            row
            for row in list(plan.get("required_adjustments") or [])
            if isinstance(row, dict)
        ]
        repair_focus_page_ids: list[str] = []
        for row in repair_focus_rows:
            page_ids = self._clean_page_ids(row.get("page_ids"))[:8]
            if not page_ids:
                continue
            warning = str(row.get("warning") or "").strip()
            if warning:
                warning_page_ids[warning] = list(
                    dict.fromkeys(
                        [
                            *warning_page_ids.get(warning, []),
                            *page_ids,
                        ]
                    )
                )[:8]
            repair_focus_page_ids = list(
                dict.fromkeys([*repair_focus_page_ids, *page_ids])
            )[:12]
        for row in required_adjustment_rows:
            page_ids = self._clean_page_ids(row.get("page_ids"))[:8]
            if not page_ids:
                continue
            warning = str(row.get("warning") or "").strip()
            if warning:
                warning_page_ids[warning] = list(
                    dict.fromkeys(
                        [
                            *warning_page_ids.get(warning, []),
                            *page_ids,
                        ]
                    )
                )[:8]
        caution_page_ids = [
            str(page_id).strip()
            for page_id in list(plan.get("caution_page_ids") or [])
            if str(page_id).strip()
        ]
        quality_provenance = {
            key: value
            for key, value in {
                "warning_page_ids": warning_page_ids,
                "repair_focus_page_ids": repair_focus_page_ids,
            }.items()
            if value not in (None, "", [], {})
        }
        response = (
            metadata.get("manager_response")
            if isinstance(metadata.get("manager_response"), dict)
            else {}
        )
        hold = (
            response.get("hold_decision")
            if isinstance(response.get("hold_decision"), dict)
            else {}
        )
        if self._hold_decision_has_concrete_next_step(hold):
            return {
                "outcome_kind": "resolved_wiki_quality_pressure",
                "outcome_status": "flat",
                "pnl_currency": "KRW" if scope == "kis" else "USDT",
                "return_pct": 0.0,
                "evidence": {
                    "source": f"{scope}_jue_wiki_quality_pressure_action_plan",
                    "reason": "wiki_quality_pressure_resolved_without_block",
                    "manager_run_id": metadata.get("source_row_id"),
                    "warnings": warnings,
                    "quality_warnings": warnings,
                    **quality_provenance,
                    "caution_page_ids": caution_page_ids,
                    "next_triggers": hold.get("next_triggers") or [],
                    "watch_symbols": hold.get("watch_symbols") or [],
                    "execution_gate_status": gate.get("status"),
                    "venue": venue,
                },
            }
        return {
            "outcome_kind": "missed_wiki_quality_pressure",
            "outcome_status": "loss",
            "pnl_currency": "KRW" if scope == "kis" else "USDT",
            "return_pct": -0.07,
            "evidence": {
                "source": f"{scope}_jue_wiki_quality_pressure_action_plan",
                "reason": "wiki_quality_pressure_without_resolution",
                "manager_run_id": metadata.get("source_row_id"),
                "warnings": warnings,
                "quality_warnings": warnings,
                **quality_provenance,
                "caution_page_ids": caution_page_ids,
                "decision_policy": plan.get("decision_policy"),
                "execution_gate_status": gate.get("status"),
                "venue": venue,
            },
        }

    @staticmethod
    def _hold_decision_has_concrete_next_step(hold_decision: dict[str, Any]) -> bool:
        hold = hold_decision if isinstance(hold_decision, dict) else {}
        for row in list(hold.get("next_triggers") or []):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            condition = str(
                row.get("condition") or row.get("trigger") or row.get("reason") or ""
            ).strip()
            price = _safe_float(row.get("price"))
            if symbol and condition:
                return True
            if price > 0 and condition:
                return True
        data_gaps = [str(row).strip() for row in list(hold.get("data_gaps") or [])]
        watch_symbols = [
            str(row).strip() for row in list(hold.get("watch_symbols") or [])
        ]
        return bool([row for row in data_gaps if row]) and bool(
            [row for row in watch_symbols if row]
        )

    def _manager_contract_error_outcome(
        self,
        *,
        scope: str,
        venue: str,
        link: dict[str, Any],
        related_block_count: int,
    ) -> dict[str, Any] | None:
        if related_block_count > 0:
            return None
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        response = (
            metadata.get("manager_response")
            if isinstance(metadata.get("manager_response"), dict)
            else {}
        )
        error_message = str(metadata.get("error_message") or "").strip()
        contract_error = str(response.get("contract_error") or "").strip()
        if not contract_error:
            if str(metadata.get("mode") or "").strip().lower() == "contract_error":
                contract_error = error_message
            elif error_message in {
                "validation_repair_resolution_missing_from_model",
                "hold_decision_missing_concrete_trigger",
            }:
                contract_error = error_message
        if not contract_error:
            return None
        gate = (
            metadata.get("execution_gate")
            if isinstance(metadata.get("execution_gate"), dict)
            else {}
        )
        if self._execution_gate_blocks_pressure_outcome(gate):
            return None
        pressure = (
            metadata.get("proactive_decision_pressure")
            if isinstance(metadata.get("proactive_decision_pressure"), dict)
            else {}
        )
        validation_repair = (
            metadata.get("validation_repair")
            if isinstance(metadata.get("validation_repair"), dict)
            else {}
        )
        repair_contract = (
            metadata.get("jue_wiki_repair_contract")
            if isinstance(metadata.get("jue_wiki_repair_contract"), dict)
            else {}
        )
        decision_type = str(link.get("decision_type") or "").strip().lower()
        evidence_source = (
            f"{scope}_{decision_type}_contract_error"
            if decision_type and decision_type != "block_manager"
            else f"{scope}_manager_contract_error"
        )
        return {
            "outcome_kind": "manager_contract_error",
            "outcome_status": "loss",
            "pnl_currency": "KRW" if scope == "kis" else "USDT",
            "return_pct": -0.12,
            "evidence": {
                "source": evidence_source,
                "reason": contract_error,
                "manager_run_id": metadata.get("source_row_id"),
                "run_status": metadata.get("run_status"),
                "mode": metadata.get("mode"),
                "final_action_count": response.get("final_action_count"),
                "proactive_pressure_status": pressure.get("status"),
                "pressure_level": pressure.get("pressure_level"),
                "top_symbols": pressure.get("top_symbols") or [],
                "validation_repair_item_count": validation_repair.get(
                    "repair_item_count"
                ),
                "validation_repair_constraint_count": validation_repair.get(
                    "constraint_count"
                ),
                "repair_priority_count": repair_contract.get(
                    "repair_priority_count"
                ),
                **self._market_judgment_context_evidence(metadata),
                "execution_gate_status": gate.get("status"),
                "venue": venue,
            },
        }

    def _manager_repair_outcome(
        self,
        *,
        scope: str,
        venue: str,
        link: dict[str, Any],
        related_block_count: int,
    ) -> dict[str, Any] | None:
        if related_block_count > 0:
            return None
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        repair = (
            metadata.get("jue_wiki_repair_contract")
            if isinstance(metadata.get("jue_wiki_repair_contract"), dict)
            else {}
        )
        repair_source = f"{scope}_jue_wiki_repair_contract"
        if (
            repair.get("repair_priority_count") not in (None, "", [], {})
            and _safe_int(repair.get("repair_priority_count")) <= 0
        ):
            return None
        priorities = [
            row
            for row in list(repair.get("top_priorities") or [])
            if isinstance(row, dict)
        ]
        if not priorities:
            priorities = self._selected_wiki_pages_repair_priorities(metadata)
            if priorities:
                repair = {
                    "repair_priority_count": len(priorities),
                }
                repair_source = f"{scope}_selected_wiki_pages_repair_queue"
        if not priorities:
            return None
        gate = (
            metadata.get("execution_gate")
            if isinstance(metadata.get("execution_gate"), dict)
            else {}
        )
        if self._execution_gate_blocks_pressure_outcome(gate):
            return None
        resolution = self._manager_response_repair_resolution(metadata)
        component_target_evidence = self._repair_contract_component_target_evidence(
            repair
        )
        lane_evidence = self._repair_priority_lane_evidence(priorities)
        if self._repair_resolution_is_concrete(resolution):
            return {
                "outcome_kind": "resolved_repair_priority",
                "outcome_status": "flat",
                "pnl_currency": "KRW" if scope == "kis" else "USDT",
                "return_pct": 0.0,
                "evidence": {
                    "source": repair_source,
                    "reason": "repair_priority_resolved_without_block",
                    "manager_run_id": metadata.get("source_row_id"),
                    "repair_priority_count": repair.get("repair_priority_count")
                    or len(priorities),
                    "top_page_ids": [
                        str(row.get("page_id") or "")
                        for row in priorities[:5]
                        if str(row.get("page_id") or "").strip()
                    ],
                    "top_symbols": self._repair_priority_symbols(priorities),
                    "repair_priority_types": self._repair_priority_values(
                        priorities,
                        keys=("priority_type",),
                    ),
                    "repair_action_types": self._repair_priority_values(
                        priorities,
                        keys=("action_type",),
                    ),
                    "repair_source_ids": self._repair_priority_values(
                        priorities,
                        keys=("source_id",),
                    ),
                    "repair_decision_uses": self._repair_priority_values(
                        priorities,
                        keys=("decision_use",),
                    ),
                    "repair_diagnostic_reasons": (
                        self._repair_priority_nested_values(
                            priorities,
                            key="diagnostic_reasons",
                        )
                    ),
                    "repair_missing_fields": self._repair_priority_nested_values(
                        priorities,
                        key="missing_fields",
                    ),
                    "repair_required_checks": self._repair_priority_nested_values(
                        priorities,
                        key="required_checks",
                    ),
                    "repair_horizon_gap_totals": self._repair_priority_int_values(
                        priorities,
                        key="closed_block_outcomes_without_horizon",
                    ),
                    "repair_horizon_gap_pcts": self._repair_priority_float_values(
                        priorities,
                        key="closed_block_outcomes_without_horizon_pct",
                    ),
                    "repair_targets": self._repair_priority_repair_targets(
                        priorities
                    ),
                    "repair_target_effectiveness": (
                        self._repair_priority_repair_target_effectiveness(
                            priorities
                        )
                    ),
                    "repair_target_effectiveness_statuses": (
                        self._repair_priority_repair_target_effectiveness_statuses(
                            priorities
                        )
                    ),
                    "impacted_page_ids": self._repair_priority_nested_values(
                        priorities,
                        key="impacted_page_ids",
                    ),
                    "repair_loop_statuses": self._repair_priority_values(
                        priorities,
                        keys=("repair_loop_status",),
                    ),
                    "repair_loop_action_types": self._repair_priority_values(
                        priorities,
                        keys=("repair_loop_action_type",),
                    ),
                    "repair_loop_sample_counts": self._repair_priority_int_values(
                        priorities,
                        key="repair_loop_sample_count",
                    ),
                    "repair_loop_missed_counts": self._repair_priority_int_values(
                        priorities,
                        key="repair_loop_missed_count",
                    ),
                    "repair_loop_resolved_counts": self._repair_priority_int_values(
                        priorities,
                        key="repair_loop_resolved_count",
                    ),
                    "repair_loop_resolution_rates": (
                        self._repair_priority_float_values(
                            priorities,
                            key="repair_loop_resolution_rate",
                        )
                    ),
                    "repair_resolution_success_criteria": (
                        self._repair_contract_resolution_success_criteria(
                            repair,
                            priorities,
                        )
                    ),
                    "repair_learning_recommended_actions": (
                        self._repair_contract_learning_recommended_actions(
                            repair
                        )
                    ),
                    "repair_learning_action_targets": (
                        self._repair_contract_learning_action_targets(repair)
                    ),
                    "repair_learning_resolution_steps": (
                        self._repair_contract_learning_resolution_steps(repair)
                    ),
                    "repair_learning_step_targets": (
                        self._repair_contract_learning_step_targets(repair)
                    ),
                    "repair_learning_step_recommended_resolutions": (
                        self._repair_contract_learning_step_recommended_resolutions(
                            repair
                        )
                    ),
                    "repair_learning_resolution_targets": (
                        self._repair_contract_learning_resolution_targets(repair)
                    ),
                    **component_target_evidence,
                    **lane_evidence,
                    "quality_warnings": self._repair_priority_quality_warnings(
                        priorities
                    ),
                    "resolution": resolution,
                    "execution_gate_status": gate.get("status"),
                    "venue": venue,
                },
            }
        return {
            "outcome_kind": "missed_repair_priority",
            "outcome_status": "loss",
            "pnl_currency": "KRW" if scope == "kis" else "USDT",
            "return_pct": -0.08,
            "evidence": {
                "source": repair_source,
                "reason": "repair_priority_without_block_or_candidate_resolution",
                "manager_run_id": metadata.get("source_row_id"),
                "repair_priority_count": repair.get("repair_priority_count")
                or len(priorities),
                "top_page_ids": [
                    str(row.get("page_id") or "")
                    for row in priorities[:5]
                    if str(row.get("page_id") or "").strip()
                ],
                "top_symbols": self._repair_priority_symbols(priorities),
                "repair_priority_types": self._repair_priority_values(
                    priorities,
                    keys=("priority_type",),
                ),
                "repair_action_types": self._repair_priority_values(
                    priorities,
                    keys=("action_type",),
                ),
                "repair_source_ids": self._repair_priority_values(
                    priorities,
                    keys=("source_id",),
                ),
                "repair_decision_uses": self._repair_priority_values(
                    priorities,
                    keys=("decision_use",),
                ),
                "repair_diagnostic_reasons": self._repair_priority_nested_values(
                    priorities,
                    key="diagnostic_reasons",
                ),
                "repair_missing_fields": self._repair_priority_nested_values(
                    priorities,
                    key="missing_fields",
                ),
                "repair_required_checks": self._repair_priority_nested_values(
                    priorities,
                    key="required_checks",
                ),
                "repair_horizon_gap_totals": self._repair_priority_int_values(
                    priorities,
                    key="closed_block_outcomes_without_horizon",
                ),
                "repair_horizon_gap_pcts": self._repair_priority_float_values(
                    priorities,
                    key="closed_block_outcomes_without_horizon_pct",
                ),
                "repair_targets": self._repair_priority_repair_targets(priorities),
                "repair_target_effectiveness": (
                    self._repair_priority_repair_target_effectiveness(priorities)
                ),
                "repair_target_effectiveness_statuses": (
                    self._repair_priority_repair_target_effectiveness_statuses(
                        priorities
                    )
                ),
                "impacted_page_ids": self._repair_priority_nested_values(
                    priorities,
                    key="impacted_page_ids",
                ),
                "repair_loop_statuses": self._repair_priority_values(
                    priorities,
                    keys=("repair_loop_status",),
                ),
                "repair_loop_action_types": self._repair_priority_values(
                    priorities,
                    keys=("repair_loop_action_type",),
                ),
                "repair_loop_sample_counts": self._repair_priority_int_values(
                    priorities,
                    key="repair_loop_sample_count",
                ),
                "repair_loop_missed_counts": self._repair_priority_int_values(
                    priorities,
                    key="repair_loop_missed_count",
                ),
                "repair_loop_resolved_counts": self._repair_priority_int_values(
                    priorities,
                    key="repair_loop_resolved_count",
                ),
                "repair_loop_resolution_rates": self._repair_priority_float_values(
                    priorities,
                    key="repair_loop_resolution_rate",
                ),
                "repair_resolution_success_criteria": (
                    self._repair_contract_resolution_success_criteria(
                        repair,
                        priorities,
                    )
                ),
                "repair_learning_recommended_actions": (
                    self._repair_contract_learning_recommended_actions(repair)
                ),
                "repair_learning_action_targets": (
                    self._repair_contract_learning_action_targets(repair)
                ),
                "repair_learning_resolution_steps": (
                    self._repair_contract_learning_resolution_steps(repair)
                ),
                "repair_learning_step_targets": (
                    self._repair_contract_learning_step_targets(repair)
                ),
                "repair_learning_step_recommended_resolutions": (
                    self._repair_contract_learning_step_recommended_resolutions(
                        repair
                    )
                ),
                "repair_learning_resolution_targets": (
                    self._repair_contract_learning_resolution_targets(repair)
                ),
                **component_target_evidence,
                **lane_evidence,
                "quality_warnings": self._repair_priority_quality_warnings(priorities),
                "execution_gate_status": gate.get("status"),
                "venue": venue,
            },
        }

    @staticmethod
    def _selected_wiki_pages_repair_priorities(
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        summary = (
            metadata.get("selected_wiki_pages")
            if isinstance(metadata.get("selected_wiki_pages"), dict)
            else {}
        )
        pages = summary.get("pages") if isinstance(summary.get("pages"), list) else []
        priorities: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            repair_queue = (
                page.get("repair_queue")
                if isinstance(page.get("repair_queue"), dict)
                else {}
            )
            memory_card_quality = (
                page.get("memory_card_quality")
                if isinstance(page.get("memory_card_quality"), dict)
                else {}
            )
            page_lane_metadata = (
                JueWikiApplicationService._selected_wiki_page_lane_metadata(page)
            )
            fallback_reasons = (
                JueWikiApplicationService
                ._selected_page_effectiveness_fallback_reasons(page)
            )
            if fallback_reasons:
                page_id = str(page.get("page_id") or "")
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(page.get("symbols") or [])[:8]
                    if str(symbol).strip()
                ]
                requested_horizons = (
                    JueWikiApplicationService
                    ._selected_page_effectiveness_requested_horizons(page)
                )
                diagnostic_reasons = [
                    *[
                        f"fallback_reason:{reason}"
                        for reason in fallback_reasons
                    ],
                    *[
                        f"requested_horizon:{horizon}"
                        for horizon in requested_horizons
                    ],
                ]
                target_horizons = requested_horizons or ["specific_horizon"]
                priority: dict[str, Any] = {
                    "page_id": page_id,
                    "page_type": str(page.get("page_type") or ""),
                    "priority_type": "horizon_effectiveness_fallback",
                    "symbols": symbols,
                    "symbol_overlap": symbols,
                    "source_type": "selected_wiki_pages_effectiveness_fallback",
                    "source_id": (
                        f"{page_id or 'selected_wiki_page'}:"
                        "horizon_effectiveness_fallback"
                    ),
                    "action_type": "collect_horizon_specific_wiki_effectiveness",
                    "decision_use": "horizon_specific_effectiveness_repair",
                    "repair_status": "fallback_metric",
                    **page_lane_metadata,
                    "horizon": (
                        requested_horizons[0]
                        if len(requested_horizons) == 1
                        else ""
                    ),
                    "requested_horizons": requested_horizons,
                    "quality_warnings": [
                        f"{reason}_fallback" for reason in fallback_reasons
                    ],
                    "diagnostic_reasons": _compact_prompt_string_list(
                        diagnostic_reasons,
                        limit=8,
                        max_len=180,
                    ),
                    "required_checks": _compact_prompt_string_list(
                        [
                            "project_selected_page_outcomes_by_requested_horizon",
                            "collect_page_effectiveness_for_specific_horizon",
                        ],
                        limit=4,
                        max_len=160,
                    ),
                    "repair_action": (
                        "collect horizon-specific selected wiki effectiveness "
                        "before relying on general page metrics"
                    ),
                    "repair_targets": [
                        {
                            key: value
                            for key, value in {
                                "page_id": page_id,
                                "symbol": symbols[0] if symbols else "",
                                "recommended_action": (
                                    "collect_horizon_specific_wiki_effectiveness:"
                                    f"{horizon}"
                                ),
                            }.items()
                            if value
                        }
                        for horizon in target_horizons[:4]
                    ],
                    "hard_blocker": False,
                    "candidate_resolution_required": True,
                }
                priorities.append(
                    {
                        key: value
                        for key, value in priority.items()
                        if value not in (None, "", [], {})
                    }
                )
            if not repair_queue and memory_card_quality:
                symbols = [
                    str(symbol).strip().upper()
                    for symbol in list(
                        memory_card_quality.get("symbols")
                        or page.get("symbols")
                        or []
                    )[:8]
                    if str(symbol).strip()
                ]
                page_id = str(page.get("page_id") or "")
                priority: dict[str, Any] = {
                    "page_id": page_id,
                    "page_type": str(page.get("page_type") or ""),
                    "priority_type": "memory_card_quality",
                    "symbols": symbols,
                    "symbol_overlap": symbols,
                    "source_type": "selected_wiki_pages_memory_card_quality",
                    "source_id": f"{page_id or 'selected_wiki_page'}:memory_card_quality",
                    "action_type": "cross_check_memory_card_quality",
                    "decision_use": str(
                        memory_card_quality.get("decision_use")
                        or "memory_card_quality_resolution_check"
                    ),
                    "repair_status": str(
                        memory_card_quality.get("resolution") or "unresolved"
                    ),
                    **JueWikiApplicationService._selected_wiki_page_lane_metadata(
                        page,
                        payload=memory_card_quality,
                    ),
                    "quality_warnings": ["memory_card_quality_unresolved"],
                    "missing_fields": _compact_prompt_string_list(
                        memory_card_quality.get("missing_fields") or [],
                        limit=8,
                        max_len=120,
                    ),
                    "required_checks": _compact_prompt_string_list(
                        memory_card_quality.get("required_checks") or [],
                        limit=8,
                        max_len=160,
                    ),
                    "diagnostic_reasons": _compact_prompt_string_list(
                        [
                            *[
                                f"missing_field:{field}"
                                for field in list(
                                    memory_card_quality.get("missing_fields") or []
                                )
                            ],
                            *[
                                f"required_check:{check}"
                                for check in list(
                                    memory_card_quality.get("required_checks") or []
                                )
                            ],
                        ],
                        limit=8,
                        max_len=180,
                    ),
                    "repair_action": str(
                        memory_card_quality.get("required_action")
                        or "cross_check_memory_card_quality"
                    ),
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
                    "hard_blocker": False,
                    "candidate_resolution_required": True,
                }
                priorities.append(
                    {
                        key: value
                        for key, value in priority.items()
                        if value not in (None, "", [], {})
                    }
                )
                continue
            if not repair_queue:
                continue
            action_type = str(repair_queue.get("action_type") or "").strip()
            source_id = str(repair_queue.get("source_id") or "").strip()
            quality_warnings = [
                str(item).strip()
                for item in list(repair_queue.get("quality_warnings") or [])
                if str(item).strip()
            ]
            if not action_type and not source_id and not quality_warnings:
                continue
            symbols = [
                str(symbol).strip().upper()
                for symbol in list(
                    repair_queue.get("symbols") or page.get("symbols") or []
                )[:8]
                if str(symbol).strip()
            ]
            priority: dict[str, Any] = {
                "page_id": str(page.get("page_id") or ""),
                "page_type": str(page.get("page_type") or ""),
                "priority_type": "repair_queue",
                "symbols": symbols,
                "symbol_overlap": symbols,
                "source_type": "selected_wiki_pages_repair_queue",
                "source_id": source_id
                or str(page.get("page_id") or "selected_wiki_pages"),
                "action_type": action_type,
                "decision_use": str(repair_queue.get("decision_use") or ""),
                "repair_status": str(repair_queue.get("status") or ""),
                **JueWikiApplicationService._selected_wiki_page_lane_metadata(
                    page,
                    payload=repair_queue,
                ),
                "quality_warnings": quality_warnings,
                "diagnostic_reasons": _compact_prompt_string_list(
                    repair_queue.get("diagnostic_reasons"),
                    limit=8,
                    max_len=180,
                ),
                "repair_action": str(repair_queue.get("repair_action") or ""),
                "repair_targets": _compact_prompt_repair_targets(
                    repair_queue.get("repair_targets")
                ),
                "hard_blocker": False,
                "candidate_resolution_required": True,
            }
            horizon_gap_total = _safe_int(
                repair_queue.get("closed_block_outcomes_without_horizon")
            )
            horizon_gap_pct = _safe_float(
                repair_queue.get("closed_block_outcomes_without_horizon_pct")
            )
            if horizon_gap_total > 0:
                priority["closed_block_outcomes_without_horizon"] = horizon_gap_total
            if horizon_gap_pct > 0:
                priority["closed_block_outcomes_without_horizon_pct"] = horizon_gap_pct
            priorities.append(
                {
                    key: value
                    for key, value in priority.items()
                    if value not in (None, "", [], {})
                }
            )
        if priorities:
            return priorities
        fallback_counts = (
            summary.get("effectiveness_fallback_counts")
            if isinstance(summary.get("effectiveness_fallback_counts"), dict)
            else {}
        )
        fallback_reasons = _compact_prompt_string_list(
            [
                reason
                for reason, count in fallback_counts.items()
                if str(reason).strip() and _safe_int(count) > 0
            ],
            limit=8,
            max_len=120,
        )
        fallback_page_ids = _compact_prompt_string_list(
            summary.get("effectiveness_fallback_page_ids"),
            limit=8,
            max_len=180,
        )
        requested_horizons = _compact_prompt_string_list(
            [
                *_prompt_value_items(
                    summary.get("effectiveness_fallback_requested_horizons")
                ),
                *_prompt_value_items(summary.get("requested_horizons")),
                *_prompt_value_items(summary.get("repair_requested_horizons")),
                summary.get("horizon"),
            ],
            limit=8,
            max_len=60,
        )
        if fallback_reasons:
            fallback_symbols_by_page_id = (
                JueWikiApplicationService._symbols_by_selected_wiki_page_id(
                    fallback_page_ids
                )
            )
            fallback_symbols_by_page_id.update(
                JueWikiApplicationService._summary_fallback_symbols_by_page_id(
                    summary
                )
            )
            fallback_summary_symbols = _compact_prompt_string_list(
                summary.get("effectiveness_fallback_symbols"),
                limit=8,
                max_len=40,
            )
            fallback_symbols = list(
                dict.fromkeys(
                    [
                        *[
                            str(symbol).strip().upper()
                            for symbol in fallback_summary_symbols
                            if str(symbol).strip()
                        ],
                        *[
                            symbol
                            for symbols in fallback_symbols_by_page_id.values()
                            for symbol in symbols
                            if symbol
                        ],
                    ]
                )
            )[:8]
            diagnostic_reasons = [
                *[
                    f"fallback_reason:{reason}"
                    for reason in fallback_reasons
                ],
                *[
                    f"fallback_page_id:{page_id}"
                    for page_id in fallback_page_ids
                ],
                *[
                    f"requested_horizon:{horizon}"
                    for horizon in requested_horizons
                ],
            ]
            target_horizons = requested_horizons or ["specific_horizon"]
            target_page_ids = fallback_page_ids or [
                "selected_wiki_pages.effectiveness_fallback_summary"
            ]
            return [
                {
                    key: value
                    for key, value in {
                        "page_id": (
                            "selected_wiki_pages.effectiveness_fallback_summary"
                        ),
                        "page_type": "summary",
                        "priority_type": "horizon_effectiveness_fallback",
                        "symbols": fallback_symbols,
                        "symbol_overlap": fallback_symbols,
                        "source_type": (
                            "selected_wiki_pages_effectiveness_fallback_summary"
                        ),
                        "source_id": (
                            "selected_wiki_pages:"
                            "horizon_effectiveness_fallback_summary"
                        ),
                        "action_type": (
                            "collect_horizon_specific_wiki_effectiveness"
                        ),
                        "decision_use": (
                            "horizon_specific_effectiveness_repair"
                        ),
                        "repair_status": "summary_only_fallback_metric",
                        **JueWikiApplicationService._selected_wiki_page_lane_metadata(
                            summary
                        ),
                        "horizon": (
                            requested_horizons[0]
                            if len(requested_horizons) == 1
                            else ""
                        ),
                        "requested_horizons": requested_horizons,
                        "quality_warnings": [
                            f"{reason}_fallback" for reason in fallback_reasons
                        ],
                        "diagnostic_reasons": _compact_prompt_string_list(
                            diagnostic_reasons,
                            limit=12,
                            max_len=180,
                        ),
                        "required_checks": [
                            "project_selected_summary_outcomes_by_requested_horizon",
                            "collect_page_effectiveness_for_specific_horizon",
                        ],
                        "repair_action": (
                            "collect horizon-specific selected wiki effectiveness "
                            "from summary fallback signals"
                        ),
                        "repair_targets": [
                            {
                                key: value
                                for key, value in {
                                    "page_id": page_id,
                                    "symbol": (
                                        fallback_symbols_by_page_id.get(page_id)
                                        or fallback_symbols[:1]
                                        or [""]
                                    )[0],
                                    "recommended_action": (
                                        "collect_horizon_specific_wiki_effectiveness:"
                                        f"{horizon}"
                                    ),
                                }.items()
                                if value
                            }
                            for page_id in target_page_ids[:4]
                            for horizon in target_horizons[:2]
                        ],
                        "hard_blocker": False,
                        "candidate_resolution_required": True,
                    }.items()
                    if value not in (None, "", [], {})
                }
            ]
        action_types = _compact_prompt_string_list(
            summary.get("repair_action_types"),
            limit=8,
            max_len=120,
        )
        decision_uses = _compact_prompt_string_list(
            summary.get("repair_decision_uses"),
            limit=8,
            max_len=180,
        )
        quality_warnings = _compact_prompt_string_list(
            summary.get("repair_quality_warnings"),
            limit=8,
            max_len=120,
        )
        diagnostic_reasons = _compact_prompt_string_list(
            summary.get("repair_diagnostic_reasons"),
            limit=8,
            max_len=180,
        )
        repair_targets = _compact_prompt_repair_targets(summary.get("repair_targets"))
        if not (
            _safe_int(summary.get("repair_queue_count")) > 0
            or action_types
            or decision_uses
            or quality_warnings
            or diagnostic_reasons
            or repair_targets
        ):
            return []
        horizon_gap_total = _safe_int(summary.get("repair_horizon_gap_total"))
        horizon_gap_pct = _safe_float(summary.get("repair_horizon_gap_max_pct"))
        priority_count = max(
            len(action_types),
            len(decision_uses),
            len(quality_warnings),
            1,
        )
        summary_priorities: list[dict[str, Any]] = []
        for idx in range(priority_count):
            action_type = action_types[idx] if idx < len(action_types) else ""
            decision_use = decision_uses[idx] if idx < len(decision_uses) else ""
            row_warnings = (
                [quality_warnings[idx]]
                if idx < len(quality_warnings)
                else quality_warnings
            )
            priority: dict[str, Any] = {
                "page_id": "selected_wiki_pages.repair_summary",
                "page_type": "summary",
                "priority_type": "repair_queue",
                "symbols": [],
                "symbol_overlap": [],
                "source_type": "selected_wiki_pages_repair_summary",
                "source_id": f"selected_wiki_pages:repair_summary:{idx + 1}",
                "action_type": action_type,
                "decision_use": decision_use,
                "repair_status": "summary_only",
                "quality_warnings": row_warnings,
                "diagnostic_reasons": diagnostic_reasons,
                "repair_targets": repair_targets,
                "hard_blocker": False,
                "candidate_resolution_required": True,
            }
            if horizon_gap_total > 0:
                priority["closed_block_outcomes_without_horizon"] = horizon_gap_total
            if horizon_gap_pct > 0:
                priority["closed_block_outcomes_without_horizon_pct"] = horizon_gap_pct
            summary_priorities.append(
                {
                    key: value
                    for key, value in priority.items()
                    if value not in (None, "", [], {})
                }
            )
        return summary_priorities

    @staticmethod
    def _selected_wiki_page_lane_metadata(
        page: dict[str, Any],
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        metadata: dict[str, Any] = {}
        for key, plural_key, fallback_plural_key, max_len in (
            ("market", "repair_markets", "effectiveness_fallback_markets", 40),
            ("side", "repair_sides", "effectiveness_fallback_sides", 40),
            (
                "horizon",
                "repair_horizons",
                "effectiveness_fallback_requested_horizons",
                60,
            ),
        ):
            value = payload.get(key)
            if value in (None, "", [], {}):
                value = page.get(key)
            clean = str(value or "").strip().lower()[:max_len]
            if clean:
                metadata[key] = clean
                continue
            plural_values = _compact_prompt_string_list(
                [
                    *_prompt_value_items(payload.get(plural_key)),
                    *_prompt_value_items(payload.get(fallback_plural_key)),
                    *_prompt_value_items(page.get(plural_key)),
                    *_prompt_value_items(page.get(fallback_plural_key)),
                ],
                limit=8,
                max_len=max_len,
            )
            if len(plural_values) == 1:
                metadata[key] = plural_values[0]
            elif plural_values:
                metadata[plural_key] = plural_values
        return metadata

    @classmethod
    def _summary_fallback_symbols_by_page_id(
        cls,
        summary: dict[str, Any],
    ) -> dict[str, list[str]]:
        rows = (
            summary.get("effectiveness_fallback_page_symbols")
            if isinstance(summary.get("effectiveness_fallback_page_symbols"), list)
            else []
        )
        symbols_by_page_id: dict[str, list[str]] = {}
        for row in rows[:12]:
            if not isinstance(row, dict):
                continue
            page_ids = cls._clean_page_ids([row.get("page_id")])
            if not page_ids:
                continue
            page_id = page_ids[0]
            symbols: list[str] = []
            for symbol in _compact_prompt_string_list(
                row.get("symbols"),
                limit=8,
                max_len=40,
            ):
                clean_symbol = cls._normalize_selected_page_symbol(symbol)
                if clean_symbol:
                    symbols.append(clean_symbol)
            if symbols:
                current_symbols = symbols_by_page_id.setdefault(page_id, [])
                for symbol in symbols:
                    if symbol not in current_symbols:
                        current_symbols.append(symbol)
                symbols_by_page_id[page_id] = current_symbols[:4]
        return symbols_by_page_id

    @classmethod
    def _symbols_by_selected_wiki_page_id(
        cls,
        page_ids: list[str],
    ) -> dict[str, list[str]]:
        symbols_by_page_id: dict[str, list[str]] = {}
        for page_id in page_ids[:12]:
            clean_page_id = str(page_id or "").strip()
            if not clean_page_id:
                continue
            symbols = sorted(
                cls._selected_page_symbols(
                    page_id=clean_page_id,
                    page={},
                )
            )
            if symbols:
                symbols_by_page_id[clean_page_id] = symbols[:4]
        return symbols_by_page_id

    @staticmethod
    def _repair_priority_quality_warnings(
        priorities: list[dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        for row in priorities:
            for item in list(row.get("quality_warnings") or []):
                warning = str(item).strip()
                if warning and warning not in warnings:
                    warnings.append(warning)
                if len(warnings) >= 12:
                    return warnings
        return warnings

    def _manager_validation_repair_outcome(
        self,
        *,
        scope: str,
        venue: str,
        link: dict[str, Any],
        related_block_count: int,
    ) -> dict[str, Any] | None:
        if related_block_count > 0:
            return None
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        repair = (
            metadata.get("validation_repair")
            if isinstance(metadata.get("validation_repair"), dict)
            else {}
        )
        decision_type = str(link.get("decision_type") or "").strip().lower()
        source_prefix = (
            f"{scope}_{decision_type}"
            if decision_type and decision_type != "block_manager"
            else scope
        )
        repair_source = f"{source_prefix}_validation_repair"
        if not self._validation_repair_requests_probe(repair):
            contract_repair = self._validation_repair_contract_as_repair(
                metadata.get("jue_wiki_validation_repair_contract")
                if isinstance(
                    metadata.get("jue_wiki_validation_repair_contract"),
                    dict,
                )
                else {}
            )
            if self._validation_repair_requests_probe(contract_repair):
                repair = contract_repair
                repair_source = f"{source_prefix}_validation_repair_contract"
        if not self._validation_repair_requests_probe(repair):
            return None
        gate = (
            metadata.get("execution_gate")
            if isinstance(metadata.get("execution_gate"), dict)
            else {}
        )
        if self._execution_gate_blocks_pressure_outcome(gate):
            return None
        resolution = self._manager_response_repair_resolution(metadata)
        if self._repair_resolution_is_concrete(resolution):
            return {
                "outcome_kind": "resolved_validation_probe",
                "outcome_status": "flat",
                "pnl_currency": "KRW" if scope == "kis" else "USDT",
                "return_pct": 0.0,
                "evidence": {
                    "source": repair_source,
                    "reason": "validation_repair_probe_contract_resolved_without_block",
                    "manager_run_id": metadata.get("source_row_id"),
                    "discipline_ids": repair.get("discipline_ids") or [],
                    "repair_action_ids": repair.get("repair_action_ids") or [],
                    "entry_biases": repair.get("entry_biases") or [],
                    **(
                        {
                            "degraded_metric_evidence": repair[
                                "degraded_metric_evidence"
                            ]
                        }
                        if repair.get("degraded_metric_evidence")
                        else {}
                    ),
                    **(
                        {
                            "contract_basis_pressure_summary": repair[
                                "contract_basis_pressure_summary"
                            ]
                        }
                        if repair.get("contract_basis_pressure_summary")
                        else {}
                    ),
                    **(
                        {"contract_feedback_gap": repair["contract_feedback_gap"]}
                        if repair.get("contract_feedback_gap")
                        else {}
                    ),
                    **self._market_judgment_context_evidence(metadata),
                    "resolution": resolution,
                    "execution_gate_status": gate.get("status"),
                    "venue": venue,
                },
            }
        return {
            "outcome_kind": "missed_validation_probe",
            "outcome_status": "loss",
            "pnl_currency": "KRW" if scope == "kis" else "USDT",
            "return_pct": -0.06,
            "evidence": {
                "source": repair_source,
                "reason": "validation_repair_probe_contract_without_block",
                "manager_run_id": metadata.get("source_row_id"),
                "discipline_ids": repair.get("discipline_ids") or [],
                "repair_action_ids": repair.get("repair_action_ids") or [],
                "entry_biases": repair.get("entry_biases") or [],
                "allowed_entry_postures": repair.get("allowed_entry_postures") or [],
                "blocks_new_entries": repair.get("blocks_new_entries") or [],
                "risk_budget_multiplier": repair.get("risk_budget_multiplier"),
                "max_budget_multiplier": repair.get("max_budget_multiplier"),
                **(
                    {
                        "degraded_metric_evidence": repair[
                            "degraded_metric_evidence"
                        ]
                    }
                    if repair.get("degraded_metric_evidence")
                    else {}
                ),
                **(
                    {
                        "contract_basis_pressure_summary": repair[
                            "contract_basis_pressure_summary"
                        ]
                    }
                    if repair.get("contract_basis_pressure_summary")
                    else {}
                ),
                **(
                    {"contract_feedback_gap": repair["contract_feedback_gap"]}
                    if repair.get("contract_feedback_gap")
                    else {}
                ),
                **self._market_judgment_context_evidence(metadata),
                "execution_gate_status": gate.get("status"),
                "venue": venue,
                },
            }

    @staticmethod
    def _market_judgment_context_evidence(
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        context = (
            metadata.get("market_judgment_context")
            if isinstance(metadata.get("market_judgment_context"), dict)
            else {}
        )
        if not context:
            return {}
        return {"market_judgment_context": context}

    @staticmethod
    def _validation_repair_contract_as_repair(
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        if not contract:
            return {}
        blocked_entry_patterns = [
            str(item).strip()[:160]
            for item in list(contract.get("blocked_entry_patterns") or [])[:10]
            if str(item).strip()
        ]
        repair = {
            "version": str(contract.get("version") or ""),
            "scope": str(contract.get("scope") or ""),
            "status": str(contract.get("status") or ""),
            "hard_filter": bool(contract.get("hard_blocker")),
            "repair_item_count": len(
                [
                    item
                    for item in list(contract.get("top_disciplines") or [])
                    if str(item).strip()
                ]
            ),
            "constraint_count": len(blocked_entry_patterns),
            "discipline_ids": [
                str(item).strip()[:160]
                for item in list(contract.get("top_disciplines") or [])[:10]
                if str(item).strip()
            ],
            "repair_action_ids": [
                str(item).strip()[:160]
                for item in list(contract.get("repair_action_ids") or [])[:10]
                if str(item).strip()
            ],
            "entry_biases": [
                str(item).strip()[:160]
                for item in list(contract.get("entry_biases") or [])[:10]
                if str(item).strip()
            ],
            "allowed_entry_postures": [
                str(item).strip()[:180]
                for item in list(contract.get("allowed_entry_postures") or [])[:10]
                if str(item).strip()
            ],
            "blocks_new_entries": blocked_entry_patterns,
            "risk_budget_multiplier": _safe_float(
                contract.get("risk_budget_multiplier")
            ),
            "degraded_metric_evidence": (
                JueWikiApplicationService._compact_validation_repair_degraded_metric_evidence(
                    contract.get("degraded_metric_evidence")
                )
            ),
            "contract_basis_pressure_summary": (
                JueWikiApplicationService._compact_contract_basis_pressure_summary(
                    contract.get("contract_basis_pressure_summary")
                )
            ),
            "contract_feedback_gap": (
                JueWikiApplicationService._compact_contract_feedback_gap(
                    contract.get("contract_feedback_gap")
                )
            ),
        }
        return {
            key: value
            for key, value in repair.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _compact_contract_feedback_gap(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        compact = {
            "status": str(value.get("status") or "")[:120],
            "required_response": str(value.get("required_response") or "")[:360],
        }
        for key in ("legacy_sample_count", "contract_sample_count"):
            _add_prompt_int(compact, value, key)
        return {
            key: clean_value
            for key, clean_value in compact.items()
            if clean_value not in (None, "", [], {})
        }

    @staticmethod
    def _compact_contract_basis_pressure_summary(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        compact = {
            "status": str(value.get("status") or "")[:80],
        }
        for key in ("sample_count", "missed_count", "resolved_count"):
            _add_prompt_int(compact, value, key)
        for key in ("resolution_rate", "miss_rate", "repair_pressure_score"):
            _add_prompt_float(compact, value, key)
        return {
            key: clean_value
            for key, clean_value in compact.items()
            if clean_value not in (None, "", [], {})
        }

    @staticmethod
    def _compact_validation_repair_degraded_metric_evidence(
        value: Any,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in list(value or [])[:4]:
            if not isinstance(row, dict):
                continue
            source_counts = {
                str(source).strip()[:180]: _safe_int(count)
                for source, count in (
                    row.get("source_counts")
                    if isinstance(row.get("source_counts"), dict)
                    else {}
                ).items()
                if str(source).strip() and _safe_int(count) > 0
            }
            compact = {
                "discipline_id": str(row.get("discipline_id") or "")[:160],
                "repair_action_id": str(row.get("repair_action_id") or "")[
                    :160
                ],
                "entry_bias": str(row.get("entry_bias") or "")[:160],
                "status": str(row.get("status") or "")[:80],
                "source_counts": source_counts,
            }
            for key in ("sample_count", "missed_count", "resolved_count"):
                _add_prompt_int(compact, row, key)
            _add_prompt_float(compact, row, "resolution_rate")
            compact = {
                key: value
                for key, value in compact.items()
                if value not in (None, "", [], {})
            }
            if compact:
                rows.append(compact)
        return rows

    @classmethod
    def _manager_response_repair_resolution(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        response = (
            metadata.get("manager_response")
            if isinstance(metadata.get("manager_response"), dict)
            else {}
        )
        resolution = (
            response.get("validation_repair_resolution")
            if isinstance(response.get("validation_repair_resolution"), dict)
            else {}
        )
        if resolution:
            return resolution
        repair_action_metadata = (
            response.get("jue_wiki_repair_action_metadata")
            if isinstance(response.get("jue_wiki_repair_action_metadata"), dict)
            else {}
        )
        return cls._repair_action_metadata_resolution(repair_action_metadata)

    @staticmethod
    def _repair_action_metadata_resolution(
        repair_action_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for row in list(repair_action_metadata.get("actions") or [])[:8]:
            if not isinstance(row, dict):
                continue
            repair_pressure = str(row.get("jue_wiki_repair_pressure") or "").strip()
            repair_resolution = str(
                row.get("jue_wiki_repair_resolution") or ""
            ).strip()
            if not repair_pressure and not repair_resolution:
                continue
            rows.append(
                {
                    key: value
                    for key, value in {
                        "symbol": str(row.get("symbol") or "").strip().upper(),
                        "market": str(row.get("market") or "")
                        .strip()
                        .lower(),
                        "side": str(row.get("side") or "")
                        .strip()
                        .lower(),
                        "horizon": str(row.get("horizon") or "")
                        .strip()
                        .lower(),
                        "resolution": "action_metadata_resolution",
                        "next_trigger": repair_resolution[:240],
                        "evidence_gap": repair_pressure[:240],
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
        if not rows:
            return {}
        return {
            "required": "jue_wiki_repair_action_metadata",
            "blanket_hold_allowed": False,
            "resolved_count": len(rows),
            "resolved_candidates": rows,
        }

    @staticmethod
    def _repair_resolution_is_concrete(resolution: dict[str, Any]) -> bool:
        rows = [
            row
            for row in list(resolution.get("resolved_candidates") or [])
            if isinstance(row, dict)
        ]
        if not rows:
            return False
        for row in rows:
            kind = str(row.get("resolution") or "").strip().lower()
            trigger = str(row.get("next_trigger") or "").strip()
            gap = str(row.get("evidence_gap") or "").strip()
            if kind in {
                "candidate_rejected",
                "safety_gate_defer",
                "action_metadata_resolution",
            } and (gap or trigger):
                return True
            if kind in {
                "small_waiting_block",
                "one_share_probe",
                "probe_waiting_block",
                "updated_price_geometry",
                "regime_confirmed_wait",
                "risk_check_defer",
                "new_watch_with_trigger",
            } and trigger:
                return True
        return False

    @staticmethod
    def _repair_priority_symbols(priorities: list[dict[str, Any]]) -> list[str]:
        symbols: list[str] = []
        for row in priorities:
            for key in ("symbol_overlap", "symbols"):
                for symbol in list(row.get(key) or []):
                    clean = str(symbol).strip().upper()
                    if clean and clean not in symbols:
                        symbols.append(clean)
                    if len(symbols) >= 12:
                        return symbols
        return symbols

    @staticmethod
    def _repair_priority_values(
        priorities: list[dict[str, Any]],
        *,
        keys: tuple[str, ...],
        limit: int = 12,
    ) -> list[str]:
        values: list[str] = []
        for row in priorities:
            for key in keys:
                value = str(row.get(key) or "").strip()
                if value and value not in values:
                    values.append(value)
                if len(values) >= max(int(limit), 0):
                    return values
        return values

    @classmethod
    def _repair_priority_lane_evidence(
        cls,
        priorities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        for key, plural_key in (
            ("market", "repair_markets"),
            ("side", "repair_sides"),
            ("horizon", "repair_horizons"),
        ):
            values = cls._repair_priority_values(
                priorities,
                keys=(key,),
                limit=6,
            )
            if len(values) == 1:
                evidence[key] = values[0]
            elif values:
                evidence[plural_key] = values
        requested_horizons = cls._repair_priority_nested_values(
            priorities,
            key="requested_horizons",
            limit=8,
        )
        if requested_horizons:
            evidence["repair_requested_horizons"] = requested_horizons
        return evidence

    @staticmethod
    def _repair_priority_nested_values(
        priorities: list[dict[str, Any]],
        *,
        key: str,
        limit: int = 12,
    ) -> list[str]:
        values: list[str] = []
        for row in priorities:
            for item in list(row.get(key) or []):
                value = str(item).strip()
                if value and value not in values:
                    values.append(value[:180])
                if len(values) >= max(int(limit), 0):
                    return values
        return values

    @staticmethod
    def _repair_priority_repair_targets(
        priorities: list[dict[str, Any]],
        *,
        limit: int = 12,
    ) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in priorities:
            for target in list(row.get("repair_targets") or []):
                if not isinstance(target, dict):
                    continue
                compact = {
                    key: str(target.get(key) or "").strip()[:180]
                    for key in ("page_id", "symbol", "recommended_action")
                    if str(target.get(key) or "").strip()
                }
                marker = (
                    compact.get("page_id", ""),
                    compact.get("symbol", ""),
                    compact.get("recommended_action", ""),
                )
                if not compact or marker in seen:
                    continue
                seen.add(marker)
                targets.append(compact)
                if len(targets) >= max(int(limit), 0):
                    return targets
        return targets

    @staticmethod
    def _repair_priority_repair_target_effectiveness(
        priorities: list[dict[str, Any]],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in priorities:
            compact = (
                JueWikiApplicationService._prompt_repair_target_effectiveness_summary(
                    row.get("repair_target_effectiveness")
                )
            )
            marker = (
                str(compact.get("page_id") or ""),
                str(compact.get("status") or ""),
            )
            if not compact or marker in seen:
                continue
            seen.add(marker)
            rows.append(compact)
            if len(rows) >= max(int(limit), 0):
                return rows
        return rows

    @classmethod
    def _repair_priority_repair_target_effectiveness_statuses(
        cls,
        priorities: list[dict[str, Any]],
        *,
        limit: int = 8,
    ) -> list[str]:
        statuses: list[str] = []
        for row in cls._repair_priority_repair_target_effectiveness(
            priorities,
            limit=limit,
        ):
            status = str(row.get("status") or "").strip()
            if status and status not in statuses:
                statuses.append(status)
            if len(statuses) >= max(int(limit), 0):
                return statuses
        return statuses

    @staticmethod
    def _repair_priority_int_values(
        priorities: list[dict[str, Any]],
        *,
        key: str,
        limit: int = 12,
    ) -> list[int]:
        values: list[int] = []
        for row in priorities:
            if row.get(key) in (None, "", [], {}):
                continue
            value = _safe_int(row.get(key))
            if value not in values:
                values.append(value)
            if len(values) >= max(int(limit), 0):
                return values
        return values

    @staticmethod
    def _repair_priority_float_values(
        priorities: list[dict[str, Any]],
        *,
        key: str,
        limit: int = 12,
    ) -> list[float]:
        values: list[float] = []
        for row in priorities:
            if row.get(key) in (None, "", [], {}):
                continue
            value = _safe_float(row.get(key))
            if value not in values:
                values.append(value)
            if len(values) >= max(int(limit), 0):
                return values
        return values

    @staticmethod
    def _repair_contract_resolution_success_criteria(
        repair: dict[str, Any],
        priorities: list[dict[str, Any]],
        *,
        limit: int = 12,
    ) -> list[str]:
        values: list[str] = []

        def add(raw: Any) -> None:
            if isinstance(raw, (list, tuple, set)):
                for item in raw:
                    add(item)
                return
            clean = str(raw or "").strip()
            if clean and clean not in values:
                values.append(clean[:180])

        for row in priorities:
            if not isinstance(row, dict):
                continue
            add(row.get("resolution_success_criteria"))
            add(row.get("repair_resolution_success_criteria"))
            if len(values) >= max(int(limit), 0):
                return values[:limit]
        effectiveness = (
            repair.get("repair_loop_effectiveness")
            if isinstance(repair.get("repair_loop_effectiveness"), dict)
            else {}
        )
        summary = (
            effectiveness.get("repair_loop_status_summary")
            if isinstance(effectiveness.get("repair_loop_status_summary"), dict)
            else {}
        )
        for target in list(summary.get("repair_action_targets") or []):
            if not isinstance(target, dict):
                continue
            add(target.get("resolution_success_criteria"))
            if len(values) >= max(int(limit), 0):
                return values[:limit]
        return values[:limit]

    @classmethod
    def _repair_contract_component_target_evidence(
        cls,
        repair: dict[str, Any],
    ) -> dict[str, Any]:
        effectiveness = (
            repair.get("repair_loop_effectiveness")
            if isinstance(repair.get("repair_loop_effectiveness"), dict)
            else {}
        )
        component_summary = (
            effectiveness.get("component_status_summary")
            if isinstance(effectiveness.get("component_status_summary"), dict)
            else {}
        )
        components = [
            row
            for row in list(component_summary.get("components") or [])
            if isinstance(row, dict)
        ]
        top_targets = _compact_prompt_repair_component_targets(
            component_summary.get("top_component_targets")
        ) or cls._repair_priority_top_component_targets(components)
        repair_required_targets = _compact_prompt_repair_component_targets(
            component_summary.get("repair_required_component_targets")
        ) or cls._repair_priority_top_component_targets(
            components,
            statuses={"repair_required"},
        )
        probe_targets = _compact_prompt_repair_component_targets(
            component_summary.get("probe_component_targets")
        ) or cls._repair_priority_top_component_targets(
            components,
            statuses={"probe"},
        )
        return {
            key: value
            for key, value in {
                "repair_component_targets": top_targets,
                "repair_required_component_targets": repair_required_targets,
                "repair_probe_component_targets": probe_targets,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _repair_contract_learning_recommended_actions(
        repair: dict[str, Any],
        *,
        limit: int = 12,
    ) -> list[str]:
        values: list[str] = []

        def add(raw: Any) -> None:
            if isinstance(raw, (list, tuple, set)):
                for item in raw:
                    add(item)
                return
            if isinstance(raw, dict):
                add(raw.get("recommended_action"))
                return
            clean = str(raw or "").strip()
            if clean and clean not in values:
                values.append(clean[:180])

        add(repair.get("repair_learning_directives"))
        effectiveness = (
            repair.get("repair_loop_effectiveness")
            if isinstance(repair.get("repair_loop_effectiveness"), dict)
            else {}
        )
        add(effectiveness.get("repair_learning_directives"))
        summary = (
            effectiveness.get("repair_success_criteria_summary")
            if isinstance(effectiveness.get("repair_success_criteria_summary"), dict)
            else {}
        )
        add(summary.get("repair_learning_directives"))
        metrics = [
            row
            for row in list(effectiveness.get("repair_learning_directive_metrics") or [])
            if isinstance(row, dict)
        ]
        for row in metrics:
            add(row.get("recommended_action"))
            if len(values) >= max(int(limit), 0):
                return values[:limit]
        return values[:limit]

    @staticmethod
    def _repair_contract_learning_action_targets(
        repair: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        effectiveness = (
            repair.get("repair_loop_effectiveness")
            if isinstance(repair.get("repair_loop_effectiveness"), dict)
            else {}
        )
        summary = (
            effectiveness.get("repair_learning_directive_summary")
            if isinstance(effectiveness.get("repair_learning_directive_summary"), dict)
            else {}
        )
        targets: list[dict[str, Any]] = []
        for row in list(summary.get("action_targets") or []):
            if not isinstance(row, dict):
                continue
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "status": str(row.get("status") or "")[:80],
                "recommended_action": str(
                    row.get("recommended_action") or ""
                )[:180],
                "recommended_resolution": str(
                    row.get("recommended_resolution") or ""
                )[:180],
                "resolution_steps": [
                    str(item)[:180]
                    for item in list(row.get("resolution_steps") or [])[:6]
                    if str(item).strip()
                ],
                "sample_count": _safe_int(row.get("sample_count")),
                "missed_count": _safe_int(row.get("missed_count")),
                "resolved_count": _safe_int(row.get("resolved_count")),
                "resolution_rate": _safe_float(row.get("resolution_rate")),
                "miss_rate": _safe_float(row.get("miss_rate")),
                "repair_pressure_score": _safe_float(
                    row.get("repair_pressure_score")
                ),
                "metric_count": _safe_int(row.get("metric_count")),
            }
            targets.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
            if len(targets) >= max(int(limit), 0):
                break
        return targets

    @classmethod
    def _repair_contract_learning_resolution_steps(
        cls,
        repair: dict[str, Any],
        *,
        limit: int = 12,
    ) -> list[str]:
        values: list[str] = []
        for target in cls._repair_contract_learning_action_targets(
            repair,
            limit=limit,
        ):
            for item in list(target.get("resolution_steps") or []):
                step = str(item).strip()
                if step and step not in values:
                    values.append(step[:180])
                if len(values) >= max(int(limit), 0):
                    return values
        return values

    @staticmethod
    def _repair_contract_learning_step_targets(
        repair: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        effectiveness = (
            repair.get("repair_loop_effectiveness")
            if isinstance(repair.get("repair_loop_effectiveness"), dict)
            else {}
        )
        summary = (
            effectiveness.get("repair_learning_step_summary")
            if isinstance(effectiveness.get("repair_learning_step_summary"), dict)
            else {}
        )
        targets: list[dict[str, Any]] = []
        for row in list(summary.get("step_targets") or []):
            if not isinstance(row, dict):
                continue
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "status": str(row.get("status") or "")[:80],
                "resolution_step": str(row.get("resolution_step") or "")[:180],
                "recommended_resolution": str(
                    row.get("recommended_resolution") or ""
                )[:180],
                "resolution_steps": [
                    str(item)[:180]
                    for item in list(row.get("resolution_steps") or [])[:6]
                    if str(item).strip()
                ],
                "sample_count": _safe_int(row.get("sample_count")),
                "missed_count": _safe_int(row.get("missed_count")),
                "resolved_count": _safe_int(row.get("resolved_count")),
                "resolution_rate": _safe_float(row.get("resolution_rate")),
                "miss_rate": _safe_float(row.get("miss_rate")),
                "repair_pressure_score": _safe_float(
                    row.get("repair_pressure_score")
                ),
                "metric_count": _safe_int(row.get("metric_count")),
            }
            targets.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
            if len(targets) >= max(int(limit), 0):
                break
        return targets

    @classmethod
    def _repair_contract_learning_step_recommended_resolutions(
        cls,
        repair: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[str]:
        values: list[str] = []
        for target in cls._repair_contract_learning_step_targets(
            repair,
            limit=limit,
        ):
            resolution = str(target.get("recommended_resolution") or "").strip()
            if resolution and resolution not in values:
                values.append(resolution[:180])
            if len(values) >= max(int(limit), 0):
                break
        return values

    @staticmethod
    def _repair_contract_learning_resolution_targets(
        repair: dict[str, Any],
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        effectiveness = (
            repair.get("repair_loop_effectiveness")
            if isinstance(repair.get("repair_loop_effectiveness"), dict)
            else {}
        )
        summary = (
            effectiveness.get("repair_learning_resolution_summary")
            if isinstance(effectiveness.get("repair_learning_resolution_summary"), dict)
            else {}
        )
        targets: list[dict[str, Any]] = []
        for row in list(summary.get("resolution_targets") or []):
            if not isinstance(row, dict):
                continue
            compact = {
                "decision_scope": str(row.get("decision_scope") or "")[:80],
                "status": str(row.get("status") or "")[:80],
                "recommended_resolution": str(
                    row.get("recommended_resolution") or ""
                )[:180],
                "next_review_steps": [
                    str(item)[:180]
                    for item in list(row.get("next_review_steps") or [])[:6]
                    if str(item).strip()
                ],
                "sample_count": _safe_int(row.get("sample_count")),
                "missed_count": _safe_int(row.get("missed_count")),
                "resolved_count": _safe_int(row.get("resolved_count")),
                "resolution_rate": _safe_float(row.get("resolution_rate")),
                "miss_rate": _safe_float(row.get("miss_rate")),
                "repair_pressure_score": _safe_float(
                    row.get("repair_pressure_score")
                ),
                "metric_count": _safe_int(row.get("metric_count")),
            }
            targets.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
            if len(targets) >= max(int(limit), 0):
                break
        return targets

    @staticmethod
    def _validation_repair_values(
        repair: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        top_key: str,
        row_keys: tuple[str, ...],
        limit: int = 10,
    ) -> list[str]:
        values: list[str] = []

        def add(raw: Any) -> None:
            if isinstance(raw, (list, tuple, set)):
                for item in raw:
                    add(item)
                return
            clean = str(raw or "").strip()
            if clean and clean not in values:
                values.append(clean[:160])

        add(repair.get(top_key))
        for row in rows:
            for key in row_keys:
                add(row.get(key))
                if len(values) >= limit:
                    return values[:limit]
        return values[:limit]

    @staticmethod
    def _validation_repair_float(
        repair: dict[str, Any],
        rows: list[dict[str, Any]],
        key: str,
    ) -> float:
        value = _safe_float(repair.get(key))
        if value > 0:
            return value
        candidates = [_safe_float(row.get(key)) for row in rows]
        positives = [candidate for candidate in candidates if candidate > 0]
        return min(positives) if positives else 0.0

    @staticmethod
    def _validation_repair_requests_probe(repair: dict[str, Any]) -> bool:
        if not repair or bool(repair.get("hard_filter")):
            return False
        status = str(repair.get("status") or "").strip().lower()
        if status in {"", "missing", "clear", "disabled", "blocked"}:
            return False
        item_count = _safe_int(repair.get("repair_item_count"))
        constraint_count = _safe_int(repair.get("constraint_count"))
        text_values: list[str] = []
        for key in (
            "entry_biases",
            "allowed_entry_postures",
            "blocks_new_entries",
            "sizing_policies",
            "blocks_scaling",
            "scale_blockers",
        ):
            for value in list(repair.get(key) or []):
                text_values.append(str(value or "").strip().lower())
        if not text_values and item_count <= 0 and constraint_count <= 0:
            return False
        if JueWikiApplicationService._validation_repair_hard_blocks_entries(
            text_values
        ):
            return False
        joined = " ".join(text_values)
        if any(token in joined for token in ("probe", "waiting", "shadow")):
            return True
        for key in ("risk_budget_multiplier", "max_budget_multiplier"):
            value = _safe_float(repair.get(key))
            if 0 < value < 1:
                return True
        return False

    @staticmethod
    def _validation_repair_hard_blocks_entries(values: list[str]) -> bool:
        for value in values:
            if not value:
                continue
            if any(
                token in value
                for token in (
                    "scale_up",
                    "unvalidated",
                    "immediate",
                    "unsafe",
                    "full_size",
                    "probe",
                    "waiting",
                    "shadow",
                )
            ):
                continue
            if value in {
                "all_new_entries",
                "new_entries",
                "all_entries",
                "live_entries",
                "true",
                "blocked",
                "no_new_entries",
            }:
                return True
        return False

    @staticmethod
    def _execution_gate_blocks_pressure_outcome(gate: dict[str, Any]) -> bool:
        if not gate:
            return False
        if bool(gate.get("kill_switch_enabled")):
            return True
        if gate.get("new_entry_allowed_by_session") is False:
            return True
        status = str(gate.get("status") or "").strip().lower()
        return status in {"blocked", "disabled", "error", "halted"}

    def _kis_blocks_for_decision_link(
        self,
        *,
        path: Path,
        link: dict[str, Any],
    ) -> list[dict[str, Any]]:
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        manager_run_id = _safe_int(metadata.get("source_row_id"))
        block_id = str(link.get("block_id") or "").strip()
        clauses: list[str] = ["b.status = 'closed'"]
        params: list[Any] = []
        if block_id:
            clauses.append("b.block_id = ?")
            params.append(block_id)
        elif manager_run_id:
            clauses.append("b.manager_run_id = ?")
            params.append(manager_run_id)
        else:
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if not self._source_table_exists(conn, "blocks"):
                raise sqlite3.OperationalError("missing table: blocks")
            columns = self._source_table_columns(conn, "blocks")
            if "status" not in columns:
                return []
            order_by = self._block_order_expr("b", columns)
            exit_price_select = self._kis_exit_price_select(conn)
            rows = conn.execute(
                f"""
                SELECT b.*,
                       {exit_price_select} AS exit_price
                FROM blocks AS b
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_by}
                LIMIT 200
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _project_closed_block_backfill_outcomes(
        self,
        *,
        path: Path,
        scope: str,
        venue: str,
        limit: int,
    ) -> dict[str, int]:
        if scope == "kis":
            blocks = self._kis_closed_blocks_for_backfill(path=path, limit=limit)
        elif scope == "binance":
            blocks = self._binance_closed_blocks_for_backfill(path=path, limit=limit)
        else:
            return {"projected_count": 0, "skipped_count": 0}

        projected = 0
        skipped = 0
        for block in blocks:
            pages = self._backfill_pages_for_block(
                scope=scope,
                block=block,
            )
            if not pages:
                skipped += 1
                continue
            outcome = self._outcome_from_block(
                scope=scope,
                venue=venue,
                block=block,
            )
            if not outcome:
                skipped += 1
                continue
            link = self._ensure_backfill_decision_link(
                scope=scope,
                venue=venue,
                block=block,
                selected_pages=pages,
            )
            result = self.record_selection_outcomes(
                link_id=str(link["link_id"]),
                outcome_kind="closed_block",
                outcome_status=str(outcome["outcome_status"]),
                pnl_value=float(outcome["pnl_value"]),
                pnl_currency=str(outcome["pnl_currency"]),
                return_pct=float(outcome["return_pct"]),
                mfe_pct=float(outcome.get("mfe_pct") or 0.0),
                mae_pct=float(outcome.get("mae_pct") or 0.0),
                holding_minutes=float(outcome.get("holding_minutes") or 0.0),
                horizon=str(outcome.get("horizon") or ""),
                evidence={
                    "source": f"{scope}_closed_block_backfill",
                    "block_id": block.get("block_id"),
                    "symbol": block.get("symbol"),
                    "manager_run_id": block.get("manager_run_id"),
                    "entry_price": outcome.get("entry_price"),
                    "exit_price": outcome.get("exit_price"),
                    "horizon": outcome.get("horizon"),
                    "status": block.get("status"),
                },
            )
            if result.get("status") == "ok":
                projected += int(result.get("outcome_count") or 0)
        return {"projected_count": projected, "skipped_count": skipped}

    def _kis_closed_blocks_for_backfill(
        self,
        *,
        path: Path,
        limit: int,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if not self._source_table_exists(conn, "blocks"):
                raise sqlite3.OperationalError("missing table: blocks")
            columns = self._source_table_columns(conn, "blocks")
            if "status" not in columns:
                return []
            order_by = self._block_order_expr("b", columns)
            exit_price_select = self._kis_exit_price_select(conn)
            rows = conn.execute(
                f"""
                SELECT b.*,
                       {exit_price_select} AS exit_price
                FROM blocks AS b
                WHERE b.status = 'closed'
                ORDER BY {order_by}
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _binance_closed_blocks_for_backfill(
        self,
        *,
        path: Path,
        limit: int,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if not self._source_table_exists(conn, "blocks"):
                raise sqlite3.OperationalError("missing table: blocks")
            columns = self._source_table_columns(conn, "blocks")
            if "status" not in columns:
                return []
            order_by = self._block_order_expr("b", columns)
            reflection_select = """
                       r.entry_price AS reflected_entry_price,
                       r.exit_price AS reflected_exit_price,
                       r.net_pnl_usdt,
                       r.pnl_usdt,
                       r.mfe_r_multiple,
                       r.mae_r_multiple
            """
            reflection_join = """
                LEFT JOIN block_performance_reflections AS r
                  ON r.block_id = b.block_id
            """
            if not (
                self._source_table_exists(conn, "block_performance_reflections")
                and "block_id" in columns
            ):
                reflection_select = """
                       NULL AS reflected_entry_price,
                       NULL AS reflected_exit_price,
                       NULL AS net_pnl_usdt,
                       NULL AS pnl_usdt,
                       NULL AS mfe_r_multiple,
                       NULL AS mae_r_multiple
                """
                reflection_join = ""
            rows = conn.execute(
                f"""
                SELECT b.*,
                       {reflection_select}
                FROM blocks AS b
                {reflection_join}
                WHERE b.status = 'closed'
                ORDER BY {order_by}
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _backfill_pages_for_block(
        self,
        *,
        scope: str,
        block: dict[str, Any],
    ) -> list[str]:
        symbol = str(block.get("symbol") or "").strip().upper()
        metadata = _json_loads(str(block.get("metadata_json") or "{}"), {})
        candidates: list[str] = []
        if symbol:
            candidates.append(self.wiki.page_id(scope=scope, page_type="symbol", key=symbol))
            if scope == "binance" and symbol.startswith("KRW-"):
                base = symbol.removeprefix("KRW-")
                if base:
                    candidates.append(
                        self.wiki.page_id(
                            scope=scope,
                            page_type="symbol",
                            key=f"{base}USDT",
                        )
                    )
        candidates.append(
            self.wiki.page_id(
                scope=scope,
                page_type="playbook",
                key="reflection_lessons",
            )
        )
        if isinstance(metadata, dict) and metadata.get("applied_policy_versions"):
            candidates.append(
                self.wiki.page_id(
                    scope=scope,
                    page_type="risk",
                    key="trading_validation",
                )
            )
        return self._existing_active_pages(candidates)

    def _existing_active_pages(self, page_ids: list[str]) -> list[str]:
        clean = []
        seen: set[str] = set()
        for page_id in page_ids:
            page_id = str(page_id).strip()
            if page_id and page_id not in seen:
                clean.append(page_id)
                seen.add(page_id)
        if not clean:
            return []
        placeholders = ",".join(["?"] * len(clean))
        with self.wiki._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT page_id
                FROM wiki_pages
                WHERE status = 'active' AND page_id IN ({placeholders})
                """,
                clean,
            ).fetchall()
        found = {str(row["page_id"]) for row in rows}
        return [page_id for page_id in clean if page_id in found]

    def _ensure_backfill_decision_link(
        self,
        *,
        scope: str,
        venue: str,
        block: dict[str, Any],
        selected_pages: list[str],
    ) -> dict[str, Any]:
        block_id = str(block.get("block_id") or "").strip()
        symbol = str(block.get("symbol") or "").strip().upper()
        metadata = _json_loads(str(block.get("metadata_json") or "{}"), {})
        horizon = self._block_outcome_horizon(scope=scope, block=block)
        manager_run_id = _safe_int(block.get("manager_run_id"))
        link_id = f"wiki-link:{scope}:closed_block_backfill:{block_id}"
        selection_run_id = f"backfill:{scope}:{block_id}"
        linked_at = str(block.get("closed_at") or block.get("updated_at") or _utc_now_iso())
        link_metadata: dict[str, Any] = {
            "source": "closed_block_backfill",
            "source_row_id": manager_run_id,
            "block_id": block_id,
            "created_by": block.get("created_by"),
            "closed_at": block.get("closed_at"),
            "applied_policy_versions": metadata.get("applied_policy_versions")
            if isinstance(metadata, dict)
            else [],
        }
        if isinstance(metadata, dict):
            for key in (
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ):
                value = str(metadata.get(key) or "").strip()
                if value:
                    link_metadata[key] = value[:600]
        selected_page_summary = self._prompt_selected_wiki_pages_summary(
            {},
            selected_pages,
            horizons=[horizon],
        )
        if selected_page_summary.get("pages"):
            link_metadata["selected_wiki_pages"] = selected_page_summary
            link_metadata["selected_wiki_pages_backfill_source"] = (
                "closed_block_backfill"
            )
        with self.wiki._connect() as conn:
            conn.execute(
                """
                INSERT INTO wiki_decision_links (
                    link_id, selection_run_id, manager_run_id, decision_scope,
                    decision_type, symbol, block_id, venue, horizon, action,
                    prompt_mode, selected_pages_json, metadata_json, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link_id) DO UPDATE SET
                    selection_run_id = excluded.selection_run_id,
                    manager_run_id = excluded.manager_run_id,
                    decision_scope = excluded.decision_scope,
                    decision_type = excluded.decision_type,
                    symbol = excluded.symbol,
                    block_id = excluded.block_id,
                    venue = excluded.venue,
                    horizon = excluded.horizon,
                    action = excluded.action,
                    prompt_mode = excluded.prompt_mode,
                    selected_pages_json = excluded.selected_pages_json,
                    metadata_json = excluded.metadata_json,
                    linked_at = excluded.linked_at
                """,
                (
                    link_id,
                    selection_run_id,
                    f"{scope}:block_backfill:{manager_run_id or 'unknown'}:{block_id}",
                    scope,
                    "block_manager",
                    symbol,
                    block_id,
                    venue,
                    horizon,
                    "closed_block_backfill",
                    "backfill",
                    _json_dumps(selected_pages),
                    _json_dumps(link_metadata),
                    linked_at,
                ),
            )
        return {
            "link_id": link_id,
            "selection_run_id": selection_run_id,
            "selected_pages": selected_pages,
        }

    def _binance_blocks_for_decision_link(
        self,
        *,
        path: Path,
        link: dict[str, Any],
    ) -> list[dict[str, Any]]:
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        manager_run_id = _safe_int(metadata.get("source_row_id"))
        block_id = str(link.get("block_id") or "").strip()
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if not self._source_table_exists(conn, "blocks"):
                raise sqlite3.OperationalError("missing table: blocks")
            columns = self._source_table_columns(conn, "blocks")
            if "status" not in columns:
                return []
            clauses: list[str] = ["b.status = 'closed'"]
            params: list[Any] = []
            if block_id and "block_id" in columns:
                clauses.append("b.block_id = ?")
                params.append(block_id)
            elif manager_run_id and "manager_run_id" in columns:
                clauses.append("b.manager_run_id = ?")
                params.append(manager_run_id)
            else:
                return []
            order_by = self._block_order_expr("b", columns)
            reflection_select = """
                       r.entry_price AS reflected_entry_price,
                       r.exit_price AS reflected_exit_price,
                       r.net_pnl_usdt,
                       r.pnl_usdt,
                       r.mfe_r_multiple,
                       r.mae_r_multiple
            """
            reflection_join = """
                LEFT JOIN block_performance_reflections AS r
                  ON r.block_id = b.block_id
            """
            if not (
                self._source_table_exists(conn, "block_performance_reflections")
                and "block_id" in columns
            ):
                reflection_select = """
                       NULL AS reflected_entry_price,
                       NULL AS reflected_exit_price,
                       NULL AS net_pnl_usdt,
                       NULL AS pnl_usdt,
                       NULL AS mfe_r_multiple,
                       NULL AS mae_r_multiple
                """
                reflection_join = ""
            rows = conn.execute(
                f"""
                SELECT b.*,
                       {reflection_select}
                FROM blocks AS b
                {reflection_join}
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_by}
                LIMIT 200
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _outcome_from_block(
        self,
        *,
        scope: str,
        venue: str,
        block: dict[str, Any],
    ) -> dict[str, Any] | None:
        if scope == "kis":
            entry_price = _safe_float(block.get("entry_price"))
            exit_price = _safe_float(block.get("exit_price"))
            qty = _safe_float(block.get("qty_initial") or block.get("qty_open"))
            if entry_price <= 0 or exit_price <= 0 or qty <= 0:
                return None
            return_pct = (exit_price - entry_price) / entry_price * 100.0
            pnl_value = (exit_price - entry_price) * qty
            currency = "KRW"
        elif scope == "binance":
            entry_price = _safe_float(
                block.get("reflected_entry_price") or block.get("entry_price")
            )
            exit_price = _safe_float(block.get("reflected_exit_price"))
            if entry_price <= 0 or exit_price <= 0:
                return None
            side = str(block.get("side") or "long").lower()
            direction = -1.0 if side == "short" else 1.0
            return_pct = (exit_price - entry_price) / entry_price * 100.0 * direction
            pnl_value = _safe_float(block.get("net_pnl_usdt") or block.get("pnl_usdt"))
            currency = "USDT"
        else:
            return None
        outcome_status = "win" if return_pct > 0 else "loss" if return_pct < 0 else "flat"
        return {
            "outcome_status": outcome_status,
            "pnl_value": pnl_value,
            "pnl_currency": currency,
            "return_pct": return_pct,
            "mfe_pct": _safe_float(block.get("mfe_r_multiple")),
            "mae_pct": _safe_float(block.get("mae_r_multiple")),
            "holding_minutes": self._holding_minutes(block),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "horizon": self._block_outcome_horizon(scope=scope, block=block),
            "venue": venue,
        }

    @classmethod
    def _block_outcome_horizon(cls, *, scope: str, block: dict[str, Any]) -> str:
        metadata = _json_loads(str(block.get("metadata_json") or "{}"), {})
        if not isinstance(metadata, dict):
            metadata = {}
        clean_scope = str(scope or "").strip().lower()
        if clean_scope == "kis":
            candidates = (
                block.get("horizon"),
                metadata.get("horizon"),
                metadata.get("block_color"),
                metadata.get("user_preferred_horizon"),
            )
        elif clean_scope == "binance":
            candidates = (
                block.get("lane"),
                metadata.get("lane"),
                block.get("horizon"),
                metadata.get("horizon"),
                metadata.get("block_color"),
                block.get("market"),
                metadata.get("market"),
            )
        else:
            candidates = (block.get("horizon"), metadata.get("horizon"))
        for value in candidates:
            horizon = cls._clean_outcome_horizon(value)
            if not horizon:
                continue
            if clean_scope == "binance":
                return cls._binance_outcome_horizon(horizon)
            if clean_scope == "kis":
                return cls._kis_outcome_horizon(horizon)
            return horizon
        return ""

    @staticmethod
    def _clean_outcome_horizon(value: Any) -> str:
        clean = str(value or "").strip().lower()
        if not clean:
            return ""
        return "_".join(
            part
            for part in "".join(
                ch if ch.isalnum() or ch == "_" else "_"
                for ch in clean
            ).split("_")
            if part
        )

    @classmethod
    def _kis_outcome_horizon(cls, value: str) -> str:
        clean = cls._clean_outcome_horizon(value)
        if clean in {"short", "short_term", "day", "intraday"}:
            return "short"
        if clean in {"mid", "medium", "middle", "mid_term", "swing"}:
            return "mid"
        if clean in {"long", "long_term", "position"}:
            return "long"
        if clean in {"core_etf", "core", "etf"}:
            return "core_etf"
        return clean

    @classmethod
    def _binance_outcome_horizon(cls, value: str) -> str:
        clean = cls._clean_outcome_horizon(value)
        if not clean:
            return ""
        if "volatile_attack" in clean or clean in {"volatile", "attack"}:
            return "volatile_attack"
        if "spot" in clean:
            return "spot"
        if "future" in clean or clean in {"long", "short", "perp", "perpetual"}:
            return "futures"
        return clean

    def _holding_minutes(self, block: dict[str, Any]) -> float:
        start = str(block.get("opened_at") or block.get("created_at") or "")
        end = str(block.get("closed_at") or block.get("updated_at") or "")
        if not start or not end:
            return 0.0
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return max((end_dt - start_dt).total_seconds() / 60.0, 0.0)

    @staticmethod
    def _source_table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _source_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row[1]) for row in rows}

    @staticmethod
    def _block_order_expr(alias: str, columns: set[str]) -> str:
        if "closed_at" in columns and "updated_at" in columns:
            return f"COALESCE(NULLIF({alias}.closed_at, ''), {alias}.updated_at) DESC"
        if "closed_at" in columns:
            return f"NULLIF({alias}.closed_at, '') DESC"
        if "updated_at" in columns:
            return f"{alias}.updated_at DESC"
        return f"{alias}.rowid DESC"

    def _kis_exit_price_select(self, conn: sqlite3.Connection) -> str:
        if not self._source_table_exists(conn, "block_orders"):
            return "NULL"
        columns = self._source_table_columns(conn, "block_orders")
        if "block_id" not in columns or "side" not in columns:
            return "NULL"
        avg_expr = "NULLIF(o.avg_fill_price, 0)" if "avg_fill_price" in columns else "NULL"
        limit_expr = "NULLIF(o.limit_price, 0)" if "limit_price" in columns else "NULL"
        order_expr = "o.id DESC" if "id" in columns else "o.rowid DESC"
        return f"""
                       (
                         SELECT COALESCE({avg_expr}, {limit_expr})
                         FROM block_orders AS o
                         WHERE o.block_id = b.block_id AND o.side = 'sell'
                         ORDER BY {order_expr}
                         LIMIT 1
                       )
        """.strip()

    def _prompt_wiki_application_metadata(self, prompt: dict[str, Any]) -> dict[str, Any]:
        direct = prompt.get("jue_wiki_application")
        if isinstance(direct, dict):
            return self._normalized_wiki_application_metadata(direct)
        source = prompt.get("jue_wiki")
        if not isinstance(source, dict):
            source = prompt.get("jue_wiki_selection_observation")
        if not isinstance(source, dict):
            return {}
        pages = source.get("pages") if isinstance(source.get("pages"), list) else []
        requested_summaries = (
            source.get("requested_symbol_summaries")
            if isinstance(source.get("requested_symbol_summaries"), list)
            else []
        )
        selected_page_ids = self._page_ids_from_rows(pages)
        requested_symbol_summary_page_ids = self._page_ids_from_rows(
            requested_summaries
        )
        return self._normalized_wiki_application_metadata(
            {
                "selection_run_id": str(source.get("selection_run_id") or ""),
                "prompt_mode": str(source.get("prompt_mode") or ""),
                "selected_page_ids": selected_page_ids,
                "requested_symbol_summary_page_ids": requested_symbol_summary_page_ids,
                "quality_summary": summarize_jue_wiki_quality_pressure_for_prompt(
                    [*pages, *requested_summaries]
                ),
                "trust_profile": source.get("trust_profile")
                if isinstance(source.get("trust_profile"), dict)
                else {},
                "budget_report": source.get("budget_report")
                if isinstance(source.get("budget_report"), dict)
                else {},
            }
        )

    @classmethod
    def _normalized_wiki_application_metadata(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(metadata)
        selected_page_ids = cls._clean_page_ids(normalized.get("selected_page_ids"))
        requested_symbol_summary_page_ids = cls._clean_page_ids(
            normalized.get("requested_symbol_summary_page_ids")
        )
        applied_page_ids = cls._clean_page_ids(normalized.get("applied_page_ids"))
        if not applied_page_ids:
            applied_page_ids = list(
                dict.fromkeys([*selected_page_ids, *requested_symbol_summary_page_ids])
            )
        normalized["selected_page_ids"] = selected_page_ids
        normalized["requested_symbol_summary_page_ids"] = (
            requested_symbol_summary_page_ids
        )
        normalized["applied_page_ids"] = applied_page_ids
        normalized["requested_symbol_summary_count"] = len(
            requested_symbol_summary_page_ids
        )
        quality_summary = (
            normalized.get("quality_summary")
            if isinstance(normalized.get("quality_summary"), dict)
            else {}
        )
        if quality_summary and not isinstance(
            normalized.get("quality_pressure_action_plan"),
            dict,
        ):
            action_plan = build_jue_wiki_quality_pressure_action_plan_for_prompt(
                quality_summary
            )
            if action_plan:
                normalized["quality_pressure_action_plan"] = action_plan
        return normalized

    @classmethod
    def _prompt_wiki_decision_adjustments_summary(
        cls,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = (
            metadata.get("decision_adjustments")
            if isinstance(metadata.get("decision_adjustments"), list)
            else []
        )
        if not rows:
            rows = cls._decision_adjustments_from_trust_profile(metadata)
        result: list[dict[str, Any]] = []
        for row in rows[:6]:
            if not isinstance(row, dict):
                continue
            compact: dict[str, Any] = {}
            for key in (
                "source",
                "action",
                "target_risk_posture",
                "reason",
                "current_risk_posture",
                "current_status",
            ):
                value = str(row.get(key) or "").strip()
                if value:
                    compact[key] = value[:180]
            for key in ("recommended_allowed_uses", "deprioritized_allowed_uses"):
                values = [
                    str(item)[:120]
                    for item in list(row.get(key) or [])[:8]
                    if str(item).strip()
                ]
                if values:
                    compact[key] = list(dict.fromkeys(values))
            effectiveness = cls._prompt_decision_adjustment_effectiveness_summary(
                row.get("decision_adjustment_effectiveness")
            )
            if effectiveness:
                compact["decision_adjustment_effectiveness"] = effectiveness
            audit_effectiveness = cls._prompt_decision_adjustment_effectiveness_summary(
                row.get("decision_adjustment_audit_effectiveness")
            )
            if audit_effectiveness:
                compact["decision_adjustment_audit_effectiveness"] = (
                    audit_effectiveness
                )
            evidence_grade = cls._decision_adjustment_evidence_grade(
                effectiveness=effectiveness,
                audit_effectiveness=audit_effectiveness,
            )
            if evidence_grade:
                compact["evidence_grade"] = evidence_grade
            audit_policy = (
                row.get("decision_adjustment_audit_policy")
                if isinstance(row.get("decision_adjustment_audit_policy"), dict)
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
            if compact.get("action"):
                result.append(compact)
        return result

    @classmethod
    def _prompt_wiki_quality_summary(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        summary = (
            metadata.get("quality_summary")
            if isinstance(metadata.get("quality_summary"), dict)
            else {}
        )
        if not summary:
            return {}
        warning_page_ids_raw = (
            summary.get("warning_page_ids")
            if isinstance(summary.get("warning_page_ids"), dict)
            else {}
        )
        warning_page_ids: dict[str, list[str]] = {}
        for warning, page_ids in warning_page_ids_raw.items():
            warning_key = str(warning).strip()[:160]
            if not warning_key or not isinstance(page_ids, list):
                continue
            clean_page_ids = cls._clean_page_ids(page_ids)[:8]
            if clean_page_ids:
                warning_page_ids[warning_key] = clean_page_ids
        top_warnings: list[dict[str, Any]] = []
        for row in list(summary.get("top_warnings") or [])[:6]:
            if not isinstance(row, dict):
                continue
            warning = str(row.get("warning") or "").strip()
            if warning:
                compact = {
                    "warning": warning[:160],
                    "count": _safe_int(row.get("count")),
                }
                effectiveness = _compact_quality_warning_effectiveness_for_prompt(
                    row.get("effectiveness")
                )
                if effectiveness:
                    compact["effectiveness"] = effectiveness
                top_warnings.append(compact)
        result = {
            "row_count": _safe_int(summary.get("row_count")),
            "status_counts": canonical_jue_wiki_status_counts(
                summary.get("status_counts")
            ),
            "warning_counts": {
                str(key)[:160]: _safe_int(value)
                for key, value in dict(summary.get("warning_counts") or {}).items()
                if str(key).strip()
            },
            "top_warnings": top_warnings,
            "warning_page_ids": warning_page_ids,
            "weak_page_ids": cls._clean_page_ids(summary.get("weak_page_ids"))[:8],
            "caution_page_ids": cls._clean_page_ids(summary.get("caution_page_ids"))[
                :12
            ],
        }
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _prompt_wiki_quality_warning_source_summary(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        summary = (
            metadata.get("quality_warning_source_summary")
            if isinstance(metadata.get("quality_warning_source_summary"), dict)
            else {}
        )
        if not summary:
            return {}
        def compact_source_rows(source_rows: Any) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for row in list(source_rows or [])[:8]:
                if not isinstance(row, dict):
                    continue
                compact = {
                    "page_id": str(row.get("page_id") or "")[:180],
                    "decision_scope": str(row.get("decision_scope") or "")[:80],
                    "venue": str(row.get("venue") or "")[:80],
                    "horizon": str(row.get("horizon") or "")[:80],
                    "status": str(row.get("status") or "").strip().lower()[:80],
                    "quality_warnings": [
                        str(item)[:160]
                        for item in list(row.get("quality_warnings") or [])[:8]
                        if str(item).strip()
                    ],
                    "prior_statuses": [
                        str(item)[:80]
                        for item in list(row.get("prior_statuses") or [])[:8]
                        if str(item).strip()
                    ],
                    "reasons": [
                        str(item)[:180]
                        for item in list(row.get("reasons") or [])[:6]
                        if str(item).strip()
                    ],
                }
                for key in ("sample_count",):
                    if row.get(key) not in (None, ""):
                        compact[key] = _safe_int(row.get(key))
                for key in (
                    "win_rate",
                    "expectancy",
                    "helpful_score",
                    "confidence",
                ):
                    if row.get(key) not in (None, ""):
                        compact[key] = _safe_float(row.get(key))
                rows.append(
                    {
                        key: value
                        for key, value in compact.items()
                        if value not in (None, "", [], {})
                    }
                )
            return rows

        degraded_source_rows = (
            summary.get("top_degraded_sources")
            if isinstance(summary.get("top_degraded_sources"), list)
            else summary.get("top_degraded_quality_warning_sources")
        )
        active_source_rows = (
            summary.get("top_active_sources")
            if isinstance(summary.get("top_active_sources"), list)
            else summary.get("top_active_quality_warning_sources")
        )
        degraded_rows = compact_source_rows(degraded_source_rows)
        active_rows = compact_source_rows(active_source_rows)
        result = {
            "source_count": _safe_int(
                summary.get("source_count")
                if summary.get("source_count") not in (None, "")
                else summary.get("quality_warning_source_effectiveness_count")
            ),
            "top_degraded_sources": degraded_rows,
            "top_active_sources": active_rows,
        }
        degraded_count = _safe_int(
            summary.get("degraded_count")
            if summary.get("degraded_count") not in (None, "")
            else summary.get("quality_warning_source_degraded_count")
        )
        if degraded_count or degraded_rows:
            result["degraded_count"] = degraded_count
        active_count = _safe_int(
            summary.get("active_count")
            if summary.get("active_count") not in (None, "")
            else summary.get("quality_warning_source_active_count")
        )
        if active_count or active_rows:
            result["active_count"] = active_count
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _prompt_wiki_quality_pressure_action_plan(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        plan = (
            metadata.get("quality_pressure_action_plan")
            if isinstance(metadata.get("quality_pressure_action_plan"), dict)
            else {}
        )
        if not plan:
            return {}
        required_adjustments: list[dict[str, Any]] = []
        for row in list(plan.get("required_adjustments") or [])[:6]:
            if not isinstance(row, dict):
                continue
            compact = {
                "adjustment_type": str(row.get("adjustment_type") or "")[:120],
                "reason": str(row.get("reason") or "")[:160],
                "warning": str(row.get("warning") or "")[:160],
                "page_ids": cls._clean_page_ids(row.get("page_ids"))[:8],
                "resolution": str(row.get("resolution") or "")[:180],
            }
            if row.get("count") not in (None, ""):
                compact["count"] = _safe_int(row.get("count"))
            effectiveness = _compact_quality_warning_effectiveness_for_prompt(
                row.get("effectiveness")
            )
            if effectiveness:
                compact["effectiveness"] = effectiveness
            required_adjustments.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
        repair_focus: list[dict[str, Any]] = []
        for row in list(plan.get("repair_focus") or [])[:6]:
            if not isinstance(row, dict):
                continue
            compact = {
                "priority_type": str(row.get("priority_type") or "")[:120],
                "warning": str(row.get("warning") or "")[:160],
                "page_ids": cls._clean_page_ids(row.get("page_ids"))[:8],
                "decision_use": str(row.get("decision_use") or "")[:160],
                "effectiveness_status": str(row.get("effectiveness_status") or "")[
                    :80
                ],
            }
            if row.get("count") not in (None, ""):
                compact["count"] = _safe_int(row.get("count"))
            effectiveness = _compact_quality_warning_effectiveness_for_prompt(
                row.get("effectiveness")
            )
            if effectiveness:
                compact["effectiveness"] = effectiveness
            repair_focus.append(
                {
                    key: value
                    for key, value in compact.items()
                    if value not in (None, "", [], {})
                }
            )
        pressure_source = (
            plan.get("quality_effectiveness_pressure")
            if isinstance(plan.get("quality_effectiveness_pressure"), dict)
            else {}
        )
        quality_effectiveness_pressure: dict[str, Any] = {}
        if pressure_source:
            quality_effectiveness_pressure = {
                "status": str(pressure_source.get("status") or "")[:80],
                "degraded_warnings": [
                    str(item)[:160]
                    for item in list(pressure_source.get("degraded_warnings") or [])[
                        :8
                    ]
                    if str(item).strip()
                ],
                "probe_warnings": [
                    str(item)[:160]
                    for item in list(pressure_source.get("probe_warnings") or [])[:8]
                    if str(item).strip()
                ],
                "active_warnings": [
                    str(item)[:160]
                    for item in list(pressure_source.get("active_warnings") or [])[:8]
                    if str(item).strip()
                ],
            }
            quality_effectiveness_pressure = {
                key: value
                for key, value in quality_effectiveness_pressure.items()
                if value not in (None, "", [], {})
            }
        result = {
            "status": str(plan.get("status") or "")[:80],
            "hard_blocker": bool(plan.get("hard_blocker")),
            "decision_policy": str(plan.get("decision_policy") or "")[:240],
            "required_adjustments": required_adjustments,
            "repair_focus": repair_focus,
            "quality_effectiveness_pressure": quality_effectiveness_pressure,
            "caution_page_ids": cls._clean_page_ids(plan.get("caution_page_ids"))[:12],
        }
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _prompt_decision_adjustment_effectiveness_summary(
        source: Any,
    ) -> dict[str, Any]:
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
            counts = JueWikiApplicationService._prompt_decision_adjustment_grade_counts(
                row.get(key)
            )
            if counts:
                compact[key] = counts
        grade_performance = (
            JueWikiApplicationService._prompt_decision_adjustment_grade_performance(
                row.get("evidence_grade_performance")
            )
        )
        if grade_performance:
            compact["evidence_grade_performance"] = grade_performance
        execution_hint = str(row.get("execution_hint") or "").strip()
        if execution_hint:
            compact["execution_hint"] = execution_hint[:120]
        return compact

    @staticmethod
    def _prompt_decision_adjustment_grade_counts(source: Any) -> dict[str, int]:
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

    @staticmethod
    def _prompt_decision_adjustment_grade_performance(
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

    @staticmethod
    def _decision_adjustment_evidence_grade(
        *,
        effectiveness: dict[str, Any],
        audit_effectiveness: dict[str, Any],
    ) -> dict[str, Any]:
        basis = (
            "decision_adjustment_audit_effectiveness"
            if audit_effectiveness
            else ("decision_adjustment_effectiveness" if effectiveness else "")
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

    @staticmethod
    def _decision_adjustments_from_trust_profile(
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        trust_profile = (
            metadata.get("trust_profile")
            if isinstance(metadata.get("trust_profile"), dict)
            else {}
        )
        usage_contract = (
            trust_profile.get("usage_contract")
            if isinstance(trust_profile.get("usage_contract"), dict)
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
        row: dict[str, Any] = {
            "source": "usage_contract.risk_posture_guidance",
        }
        for key in ("action", "target_risk_posture", "reason"):
            value = str(adjustment.get(key) or "").strip()
            if value:
                row[key] = value
        for key in ("current_risk_posture", "current_status"):
            value = str(guidance.get(key) or "").strip()
            if value:
                row[key] = value
        for key in ("recommended_allowed_uses", "deprioritized_allowed_uses"):
            values = [
                str(item).strip()
                for item in list(guidance.get(key) or [])[:8]
                if str(item).strip()
            ]
            if values:
                row[key] = values
        effectiveness = (
            guidance.get("decision_adjustment_effectiveness")
            if isinstance(guidance.get("decision_adjustment_effectiveness"), dict)
            else {}
        )
        if effectiveness:
            row["decision_adjustment_effectiveness"] = effectiveness
        audit_effectiveness = (
            guidance.get("decision_adjustment_audit_effectiveness")
            if isinstance(
                guidance.get("decision_adjustment_audit_effectiveness"),
                dict,
            )
            else {}
        )
        if audit_effectiveness:
            row["decision_adjustment_audit_effectiveness"] = audit_effectiveness
        audit_policy = (
            guidance.get("decision_adjustment_audit_policy")
            if isinstance(guidance.get("decision_adjustment_audit_policy"), dict)
            else {}
        )
        if audit_policy:
            row["decision_adjustment_audit_policy"] = audit_policy
        return [row]

    @staticmethod
    def _prompt_wiki_trust_profile_summary(
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        source = (
            metadata.get("trust_profile")
            if isinstance(metadata.get("trust_profile"), dict)
            else {}
        )
        if not source:
            return {}
        result: dict[str, Any] = {}
        for key in (
            "prompt_mode",
            "authority",
            "trust_level",
            "posture",
            "decision_use",
            "recommendation_id",
            "recommended_mode",
            "configured_prompt_mode",
            "policy_reason",
        ):
            value = str(source.get(key) or "").strip()
            if value:
                result[key] = value[:360]
        sample_count = _safe_int(source.get("sample_count"))
        if sample_count:
            result["sample_count"] = sample_count
        confidence = _safe_float(source.get("confidence"))
        if confidence:
            result["confidence"] = confidence
        reasons = [
            str(reason)[:180]
            for reason in list(source.get("reasons") or [])[:6]
            if str(reason).strip()
        ]
        if reasons:
            result["reasons"] = reasons
        usage_contract = (
            source.get("usage_contract")
            if isinstance(source.get("usage_contract"), dict)
            else {}
        )
        if usage_contract:
            result["usage_contract"] = (
                JueWikiApplicationService._prompt_wiki_usage_contract_summary(
                    usage_contract
                )
            )
        return result

    @staticmethod
    def _prompt_wiki_usage_contract_summary(
        source: dict[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in (
            "version",
            "decision_role",
            "effectiveness_status",
            "risk_posture",
            "conflict_resolution",
        ):
            value = str(source.get(key) or "").strip()
            if value:
                result[key] = value[:360]
        for key in (
            "standalone_trade_authority",
            "requires_live_cross_check",
            "hard_blocker",
        ):
            if key in source:
                result[key] = bool(source.get(key))
        for key in ("allowed_uses", "required_cross_checks"):
            values = [
                str(item)[:120]
                for item in list(source.get(key) or [])[:8]
                if str(item).strip()
            ]
            if values:
                result[key] = values
        guidance = (
            source.get("risk_posture_guidance")
            if isinstance(source.get("risk_posture_guidance"), dict)
            else {}
        )
        if guidance:
            compact_guidance: dict[str, Any] = {}
            for key in ("current_risk_posture", "current_status", "guidance"):
                value = str(guidance.get(key) or "").strip()
                if value:
                    compact_guidance[key] = value[:360]
            for key in (
                "preferred_risk_postures",
                "degraded_risk_postures",
                "recommended_allowed_uses",
                "deprioritized_allowed_uses",
            ):
                values = [
                    str(item)[:120]
                    for item in list(guidance.get(key) or [])[:6]
                    if str(item).strip()
                ]
                if values:
                    compact_guidance[key] = values
            adjustment = (
                guidance.get("decision_adjustment")
                if isinstance(guidance.get("decision_adjustment"), dict)
                else {}
            )
            if adjustment:
                compact_adjustment: dict[str, Any] = {}
                for key in ("action", "target_risk_posture", "reason"):
                    value = str(adjustment.get(key) or "").strip()
                    if value:
                        compact_adjustment[key] = value[:180]
                if compact_adjustment:
                    compact_guidance["decision_adjustment"] = compact_adjustment
            if compact_guidance:
                result["risk_posture_guidance"] = compact_guidance
        return result

    @staticmethod
    def _prompt_wiki_trust_profile_effectiveness_summary(
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        source = (
            metadata.get("trust_profile_effectiveness")
            if isinstance(metadata.get("trust_profile_effectiveness"), dict)
            else {}
        )
        if not source:
            return {}
        profiles = (
            source.get("trust_profiles")
            if isinstance(source.get("trust_profiles"), list)
            else []
        )
        compact_profiles: list[dict[str, Any]] = []
        for row in profiles[:6]:
            if not isinstance(row, dict):
                continue
            compact: dict[str, Any] = {}
            for key in (
                "decision_scope",
                "authority",
                "status",
            ):
                value = str(row.get(key) or "").strip()
                if value:
                    compact[key] = value[:120]
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
            reasons = [
                str(reason)[:160]
                for reason in list(row.get("reasons") or [])[:4]
                if str(reason).strip()
            ]
            if reasons:
                compact["reasons"] = reasons
            if compact:
                compact_profiles.append(compact)
        trust_profile_count_explicit = source.get("trust_profile_count") not in (
            None,
            "",
            [],
            {},
        )
        trust_profile_count = (
            _safe_int(source.get("trust_profile_count"))
            if trust_profile_count_explicit
            else len(compact_profiles)
        )
        if trust_profile_count_explicit and trust_profile_count <= 0:
            compact_profiles = []
        if (
            not trust_profile_count_explicit
            and not compact_profiles
            and trust_profile_count <= 0
        ):
            return {}
        result: dict[str, Any] = {
            "trust_profile_count": trust_profile_count,
        }
        if compact_profiles:
            result["trust_profiles"] = compact_profiles
        target_scope = str(source.get("target_scope") or "").strip()
        if target_scope:
            result["target_scope"] = target_scope[:80]
        status = str(source.get("status") or "").strip()
        if status:
            result["status"] = status[:80]
        return result

    @classmethod
    def _applied_wiki_page_ids(cls, metadata: dict[str, Any]) -> list[str]:
        normalized = cls._normalized_wiki_application_metadata(metadata)
        return cls._clean_page_ids(
            normalized.get("applied_page_ids") or normalized.get("selected_page_ids")
        )

    @staticmethod
    def _page_ids_from_rows(rows: Any) -> list[str]:
        if not isinstance(rows, list):
            return []
        return JueWikiApplicationService._clean_page_ids(
            [row.get("page_id") for row in rows if isinstance(row, dict)]
        )

    @staticmethod
    def _clean_page_ids(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        page_ids: list[str] = []
        for page_id in value:
            if page_id is None:
                continue
            clean_page_id = str(page_id).strip()
            if not clean_page_id or clean_page_id.lower() in {"none", "null"}:
                continue
            page_ids.append(clean_page_id)
        return list(dict.fromkeys(page_ids))

    def _decision_link_from_row(self, row: Any) -> dict[str, Any]:
        return {
            **dict(row),
            "selected_pages": self._clean_page_ids(
                _json_loads(str(row["selected_pages_json"]), [])
            ),
            "metadata": _json_loads(str(row["metadata_json"]), {}),
        }

    def _effectiveness_metric(
        self,
        *,
        page_id: str,
        decision_scope: str,
        venue: str,
        horizon: str,
        rows: list[dict[str, Any]],
        min_samples: int,
    ) -> dict[str, Any]:
        sample_count = len(rows)
        returns = [float(row.get("return_pct") or 0.0) for row in rows]
        maes = [float(row.get("mae_pct") or 0.0) for row in rows]
        resolution_wins = sum(
            1 for row in rows if self._outcome_row_is_resolution_success(row)
        )
        losses = sum(1 for value in returns if value < 0)
        wins = sum(1 for value in returns if value > 0) + resolution_wins
        win_rate = wins / sample_count if sample_count else 0.0
        expectancy = sum(returns) / sample_count if sample_count else 0.0
        median_mae = statistics.median(maes) if maes else 0.0
        drawdown_pressure = abs(min(maes)) if maes else 0.0
        confidence = min(sample_count / max(int(min_samples), 1), 1.0)
        helpful_score = _clamp(expectancy * 10.0 + (win_rate - 0.5) * 12.0, -10.0, 10.0)
        if sample_count < int(min_samples):
            status = "probe"
        elif helpful_score > 1.0 and (expectancy > 0 or (resolution_wins and not losses)):
            status = "active"
        elif helpful_score < -2.0 or expectancy < 0:
            status = "degraded"
        else:
            status = "probe"
        reasons = [
            f"samples:{sample_count}",
            f"win_rate:{win_rate:.4f}",
            f"expectancy:{expectancy:.4f}",
            f"median_mae:{median_mae:.4f}",
            f"resolution_wins:{resolution_wins}",
        ]
        repair_queue_summary = self._effectiveness_repair_queue_summary(rows)
        if repair_queue_summary:
            reasons.append("application_repair_queue_pressure")
            open_count = _safe_int(repair_queue_summary.get("open_count"))
            if open_count > 0:
                reasons.append(f"repair_queue_open_count:{open_count}")
            for action_type in list(repair_queue_summary.get("action_types") or [])[:4]:
                reasons.append(f"repair_queue_action:{action_type}")
        return {
            "page_id": page_id,
            "decision_scope": decision_scope,
            "venue": venue,
            "horizon": horizon,
            "sample_count": sample_count,
            "win_rate": win_rate,
            "expectancy": expectancy,
            "avg_return_pct": expectancy,
            "median_mae_pct": median_mae,
            "drawdown_pressure": drawdown_pressure,
            "resolution_win_count": resolution_wins,
            "helpful_score": helpful_score,
            "confidence": confidence,
            "status": status,
            "reasons": reasons,
        }

    @staticmethod
    def _effectiveness_repair_queue_summary(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        open_count = 0
        action_types: list[str] = []
        seen_outcomes: set[str] = set()
        for row in rows:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            repair_queue = (
                evidence.get("jue_wiki_repair_queue")
                if isinstance(evidence.get("jue_wiki_repair_queue"), dict)
                else {}
            )
            if not repair_queue:
                continue
            outcome_id = str(row.get("outcome_id") or "").strip()
            if outcome_id:
                if outcome_id in seen_outcomes:
                    continue
                seen_outcomes.add(outcome_id)
            open_count += _safe_int(repair_queue.get("open_count"))
            for batch in list(repair_queue.get("open_action_batches") or []):
                if not isinstance(batch, dict):
                    continue
                action_type = str(batch.get("action_type") or "").strip()
                if action_type and action_type not in action_types:
                    action_types.append(action_type)
        return {
            key: value
            for key, value in {
                "open_count": open_count,
                "action_types": action_types,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _outcome_row_is_resolution_success(row: dict[str, Any]) -> bool:
        if str(row.get("outcome_status") or "").strip().lower() != "flat":
            return False
        return str(row.get("outcome_kind") or "").strip().lower() in {
            "resolved_repair_priority",
            "resolved_validation_probe",
        }

    def _effectiveness_from_row(self, row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["reasons"] = _json_loads(str(row["reasons_json"]), [])
        return payload

    def _mode_recommendation_from_row(self, row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["reasons"] = _json_loads(str(row["reasons_json"]), [])
        presence = _json_loads(str(payload.get("metric_presence_json") or "{}"), {})
        if isinstance(presence, dict) and presence.get("__tracked__"):
            metric_presence = {
                str(key): bool(value)
                for key, value in presence.items()
                if str(key) != "__tracked__"
            }
            for key in ("sample_count", "confidence"):
                if not metric_presence.get(key):
                    payload.pop(key, None)
        payload.pop("metric_presence_json", None)
        return payload
