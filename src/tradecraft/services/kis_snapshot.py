from __future__ import annotations

from collections.abc import Callable
from typing import Any

ACTIVE_BLOCK_STATUSES = {"entry_pending", "open", "exit_pending"}
VISIBLE_BLOCK_STATUSES = ACTIVE_BLOCK_STATUSES | {"proposed"}


def _default_clean_text(value: Any, *, limit: int) -> str:
    return str(value or "")[: max(int(limit), 0)]


def _compact_storage_compaction_meta(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("status", "label", "priority_reason"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = str(value.get(key))[:120]
    if value.get("emergency") not in (None, ""):
        out["emergency"] = bool(value.get("emergency"))
    for key in ("original_chars", "storage_limit_chars", "dropped_key_count"):
        if value.get(key) not in (None, "", [], {}):
            try:
                out[key] = int(value.get(key))
            except (TypeError, ValueError):
                continue
    dropped = value.get("dropped_keys")
    if isinstance(dropped, list):
        out["dropped_keys"] = [str(item)[:80] for item in dropped[:8]]
    return out


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
    "jue_wiki_action_reference_missing_actions",
    "jue_wiki_attention_must_address",
    "jue_wiki_weak_memory_card_symbols",
    "degraded_jue_wiki_effectiveness_page_ids",
)


def _compact_manager_diagnostic_value(value: Any, *, limit: int = 8) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[: max(int(limit), 1)]:
            clean_key = str(key or "")[:80]
            if not clean_key:
                continue
            if isinstance(item, (str, int, float, bool)) or item is None:
                out[clean_key] = item if not isinstance(item, str) else item[:140]
        return {
            key: item
            for key, item in out.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        rows: list[Any] = []
        for item in value[: max(int(limit), 1)]:
            if isinstance(item, dict):
                compact_item = _compact_manager_diagnostic_value(item, limit=6)
                if compact_item:
                    rows.append(compact_item)
            elif str(item or "").strip():
                rows.append(str(item)[:140])
        return rows
    if isinstance(value, str):
        return value[:140]
    return value


def _compact_manager_diagnostics(value: Any) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    if not diagnostics:
        return {}
    out: dict[str, Any] = {}
    for key in _MANAGER_DIAGNOSTIC_SCALAR_KEYS:
        if diagnostics.get(key) not in (None, "", [], {}):
            out[key] = diagnostics.get(key)
    for key in _MANAGER_DIAGNOSTIC_COLLECTION_KEYS:
        if diagnostics.get(key) not in (None, "", [], {}):
            compact_value = _compact_manager_diagnostic_value(diagnostics.get(key))
            if compact_value not in (None, "", [], {}):
                out[key] = compact_value
    return out


def visible_kis_block_rows(
    blocks: list[dict[str, Any]],
    *,
    visible_statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    statuses = visible_statuses or VISIBLE_BLOCK_STATUSES
    return [
        row
        for row in blocks
        if str(row.get("status") or "") in statuses
    ]


def history_kis_block_rows(
    blocks: list[dict[str, Any]],
    *,
    visible_statuses: set[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    statuses = visible_statuses or VISIBLE_BLOCK_STATUSES
    rows = [
        row
        for row in blocks
        if str(row.get("status") or "") not in statuses
    ]
    if limit is None:
        return rows
    return rows[: max(int(limit), 1)]


def compact_kis_manager_run(
    row: dict[str, Any],
    *,
    clean_text: Callable[..., str] = _default_clean_text,
) -> dict[str, Any]:
    if not isinstance(row, dict) or row.get("status") == "missing":
        return {"status": "missing"}
    actions = row.get("actions") if isinstance(row.get("actions"), dict) else {}
    action_counts = {
        key: len(value)
        for key, value in actions.items()
        if isinstance(value, list)
    }
    applied = row.get("applied") if isinstance(row.get("applied"), dict) else {}
    response = row.get("response") if isinstance(row.get("response"), dict) else {}
    prompt = row.get("prompt") if isinstance(row.get("prompt"), dict) else {}
    storage_compaction = _compact_storage_compaction_meta(
        prompt.get("_storage_compaction")
    )
    compact_context = (
        prompt.get("compact_manager_context")
        if isinstance(prompt.get("compact_manager_context"), dict)
        else {}
    )
    diagnostics_source = (
        row.get("diagnostics")
        or prompt.get("diagnostics")
        or compact_context.get("diagnostics")
    )
    diagnostics = _compact_manager_diagnostics(diagnostics_source)
    return {
        key: item
        for key, item in {
            "id": row.get("id"),
            "run_at": row.get("run_at"),
            "market_session": row.get("market_session"),
            "status": row.get("status"),
            "mode": row.get("mode"),
            "model": row.get("model"),
            "error_message": clean_text(row.get("error_message"), limit=500),
            "workflow_id": row.get("workflow_id"),
            "workflow_version": row.get("workflow_version"),
            "skill_ids": list(row.get("skill_ids") or [])[:8],
            "contract_ids": list(row.get("contract_ids") or [])[:8],
            "action_counts": action_counts,
            "applied": {
                key: item
                for key, item in applied.items()
                if key in {"status", "created_count", "updated_count", "closed_count"}
            },
            "hold_decision": row.get("hold_decision"),
            "creative_hypotheses": list(row.get("creative_hypotheses") or [])[:4],
            "no_action_watch": response.get("no_action_watch")
            if isinstance(response.get("no_action_watch"), dict)
            else None,
            "latest_input_summary": response.get("latest_input_summary")
            if isinstance(response.get("latest_input_summary"), dict)
            else None,
            "diagnostics": diagnostics,
            "storage_compaction": storage_compaction,
        }.items()
        if item not in (None, "", [], {})
    }
