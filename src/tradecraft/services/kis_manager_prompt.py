from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from tradecraft.services.daily_discovery import enrich_discovery_result
from tradecraft.services.kis_exit_gate import MANAGER_CLOSE_ROW_TRIGGERS
from tradecraft.services.kis_entry_gate import (
    normalize_entry_style,
    normalize_entry_trigger_operator,
)
from tradecraft.services.kis_horizon import normalize_horizon
from tradecraft.services.live_authority import VALIDATION_DISCIPLINE_BLOCK_ACTIONS
from tradecraft.services.jue_decision_packet import (
    build_canonical_decision_prompt_bundle,
)
from tradecraft.services.jue_wiki import normalize_jue_wiki_quality_status
from tradecraft.services.jue_wiki_prompt_quality import (
    canonical_jue_wiki_evidence_quality,
    jue_wiki_quality_status_from_evidence,
)
from tradecraft.services.manager_prompt_budget import (
    attach_prompt_budget as attach_manager_prompt_budget,
    format_prompt_budget_alert_message as build_format_prompt_budget_alert_message,
    prompt_budget_error as manager_prompt_budget_error,
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_int(value: Any) -> int:
    try:
        return int(math.floor(float(str(value or "0").replace(",", ""))))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "required"}:
        return True
    if text in {"0", "false", "no", "n", "off", "none", ""}:
        return False
    return True


def _normalize_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _identity_compact_value(value: Any, **_: Any) -> Any:
    return value


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _compact_requested_symbol_token(value: Any) -> str:
    text = _clean_text(value, limit=80).upper()
    match = re.search(r"\b\d{6}\b", text)
    if match:
        return match.group(0)
    return _clean_text(text.split(":", 1)[0], limit=24)


def _clean_text_list(
    value: Any,
    *,
    limit: int = 500,
    max_items: int = 8,
) -> list[str]:
    rows: list[str] = []
    for item in _normalize_list(value):
        text = _clean_text(item, limit=limit)
        if text:
            rows.append(text)
        if len(rows) >= max_items:
            break
    return rows


def _metadata_contract_repair_note(row: dict[str, Any]) -> str:
    repairs = _clean_text_list(
        row.get("period_memory_repair_actions"),
        limit=160,
        max_items=2,
    )
    resolutions = _clean_text_list(
        row.get("metadata_contract_audit_resolutions"),
        limit=180,
        max_items=2,
    )
    parts: list[str] = []
    if repairs:
        parts.append(f"metadata contract repair: {', '.join(repairs)}")
    if resolutions:
        parts.append(f"resolution: {', '.join(resolutions)}")
    return "; ".join(parts)


def clean_symbol_list(value: Any, *, max_items: int = 12) -> list[str]:
    rows: list[str] = []
    for item in _normalize_list(value):
        symbol = str(item or "").strip()
        if _is_symbol(symbol) and symbol not in rows:
            rows.append(symbol)
        if len(rows) >= max_items:
            break
    return rows


def _memory_contract_resolution_contract_from_repair_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    memory_contracts: list[str] = []
    memory_contract_errors: list[str] = []
    impacted_symbols: list[str] = []
    required_checks: list[str] = []

    def add_text(target: list[str], value: Any, *, limit: int = 160) -> None:
        text = _clean_text(value, limit=limit)
        if text and text not in target:
            target.append(text)

    for row in rows:
        if not isinstance(row, dict):
            continue
        add_text(memory_contracts, row.get("memory_contract"))
        add_text(memory_contract_errors, row.get("memory_contract_error"))
        for symbol in _normalize_list(row.get("impacted_symbols")):
            add_text(impacted_symbols, symbol, limit=40)
        for check in _normalize_list(row.get("required_checks")):
            add_text(required_checks, check, limit=120)

    required = bool(
        memory_contracts
        or memory_contract_errors
        or "require_memory_contract_resolution" in set(required_checks)
    )
    if not required:
        return {}
    return {
        "memory_contract_resolution_required": True,
        "memory_contract_resolution_contract": {
            "version": "memory_contract_resolution_contract_v1",
            "response_field": (
                "validation_repair_resolution.resolved_candidates[]."
                "memory_contract_resolution"
            ),
            "memory_contracts": memory_contracts[:6],
            "memory_contract_errors": memory_contract_errors[:6],
            "impacted_symbols": impacted_symbols[:12],
            "required_checks": sorted(set(required_checks))[:10],
            "accepted_resolutions": [
                "cite_memory_and_apply",
                "reject_memory_with_reason",
                "wait_until_memory_refresh",
                "safety_gate_defer_with_contract_note",
            ],
            "instruction": (
                "For each impacted symbol, explicitly cite or reject the memory "
                "contract in validation_repair_resolution.resolved_candidates[]."
                "memory_contract_resolution. Generic candidate rejection does not "
                "repair a memory contract error."
            ),
        },
    }


def _workflow_member_ids(rows: Any, key: str) -> list[str]:
    if not isinstance(rows, list):
        return []
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get(key) or "").strip()
        if value:
            ids.append(value)
    return ids


def manager_run_workflow_provenance(prompt: Any) -> dict[str, Any]:
    prompt_payload = prompt if isinstance(prompt, dict) else {}
    workflow = prompt_payload.get("jue_workflow")
    workflow_payload = workflow if isinstance(workflow, dict) else {}
    return {
        "workflow_id": str(workflow_payload.get("workflow_id") or ""),
        "workflow_version": _safe_int(workflow_payload.get("workflow_version")),
        "skill_ids_json": _json_dumps(
            _workflow_member_ids(workflow_payload.get("skills"), "skill_id")
        ),
        "contract_ids_json": _json_dumps(
            _workflow_member_ids(workflow_payload.get("contracts"), "contract_id")
        ),
    }


def _kis_workflow_with_core_contracts(value: Any) -> dict[str, Any]:
    workflow = dict(value) if isinstance(value, dict) else {}
    if str(workflow.get("status") or "").strip().lower() == "error":
        return workflow
    contracts = [
        dict(row)
        for row in _normalize_list(workflow.get("contracts"))
        if isinstance(row, dict)
    ]
    contract_ids = {
        str(row.get("contract_id") or "").strip()
        for row in contracts
        if str(row.get("contract_id") or "").strip()
    }
    if "jue_wiki_usage_contract_resolution" not in contract_ids:
        contracts.append(
            {
                "contract_id": "jue_wiki_usage_contract_resolution",
                "source": "policy.jue_wiki_usage_contract_policy",
                "required_metadata": "jue_wiki_usage_contract_resolution",
                "purpose": (
                    "preserve audit provenance for Wiki usage-contract "
                    "resolution on every affected block action"
                ),
            }
        )
    workflow["contracts"] = contracts
    return workflow


def _compact_kis_jue_workflow_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    workflow = _kis_workflow_with_core_contracts(value)
    compact = compact_prompt_value(
        workflow,
        list_limit=list_limit,
        string_limit=string_limit,
    )
    compact_workflow = compact if isinstance(compact, dict) else {}
    contracts = [
        row
        for row in _normalize_list(compact_workflow.get("contracts"))
        if isinstance(row, dict)
    ]
    if not any(
        row.get("contract_id") == "jue_wiki_usage_contract_resolution"
        for row in contracts
    ):
        source_contract = next(
            (
                row
                for row in _normalize_list(workflow.get("contracts"))
                if isinstance(row, dict)
                and row.get("contract_id") == "jue_wiki_usage_contract_resolution"
            ),
            None,
        )
        if source_contract:
            preserved = compact_prompt_value(
                source_contract,
                list_limit=max(int(list_limit), 1),
                string_limit=string_limit,
            )
            if isinstance(preserved, dict):
                contracts.append(preserved)
    if contracts:
        compact_workflow["contracts"] = contracts
    return compact_workflow


def prompt_chars(value: dict[str, Any]) -> int:
    return len(_json_dumps(value))


def prompt_section_size_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in prompt.items():
        if key == "prompt_budget":
            continue
        rows.append(
            {
                "section": str(key),
                "chars": len(_json_dumps(value)),
            }
        )
    rows.sort(key=lambda row: int(row["chars"]), reverse=True)
    return rows


def manager_storage_compaction_meta(
    *,
    label: str,
    original_chars: int,
    storage_limit_chars: int,
    retained_keys: list[str],
    emergency: bool = False,
) -> dict[str, Any]:
    return {
        "status": "compacted",
        "label": label,
        "original_chars": int(original_chars),
        "storage_limit_chars": int(storage_limit_chars),
        "retained_keys": retained_keys[:50],
        "emergency": bool(emergency),
    }


KIS_MANAGER_ACTION_KEYS = (
    "adopt_existing_blocks",
    "create_blocks",
    "update_blocks",
    "close_blocks",
    "pause_blocks",
    "rejected_create_blocks",
)

KIS_MANAGER_ACTION_ROW_KEYS = (
    "block_id",
    "symbol",
    "name",
    "qty",
    "qty_initial",
    "qty_open",
    "horizon",
    "entry_style",
    "entry_trigger_operator",
    "entry_trigger_price",
    "entry_price",
    "target_price",
    "stop_price",
    "close_trigger",
    "decision_class",
    "entry_setup",
    "confidence",
    "reason",
    "thesis",
    "risk_note",
    "rejection_reason",
    "live_authority_rejection_reason",
    "live_authority_adjustment_reason",
    "lane_authority_rejection_reason",
    "entry_quality_rejection_reason",
    "cost_feasibility_rejection_reason",
    "validation_repair_rejection_reason",
    "status",
)


def _compact_action_storage_row(row: Any, *, string_limit: int = 260) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in KIS_MANAGER_ACTION_ROW_KEYS:
        if key not in row:
            continue
        value = row.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            compact[key] = _clean_text(value, limit=string_limit)
        elif isinstance(value, (int, float, bool)):
            compact[key] = value
        elif isinstance(value, list):
            compact[key] = [
                _clean_text(item, limit=min(string_limit, 120))
                for item in value[:5]
                if _clean_text(item, limit=min(string_limit, 120))
            ]
        elif isinstance(value, dict):
            compact[key] = compact_etf_prompt_value(
                public_prompt_payload(value),
                list_limit=4,
                string_limit=min(string_limit, 140),
            )
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if metadata:
        compact["metadata_summary"] = compact_etf_prompt_value(
            public_prompt_payload(
                {
                    key: metadata.get(key)
                    for key in (
                        "source",
                        "lane",
                        "block_color",
                        "horizon",
                        "selected_candidate",
                        "applied_policy_versions",
                        "jue_wiki_repair_pressure",
                        "jue_wiki_repair_resolution",
                        "jue_wiki_memory_card_quality",
                        "jue_wiki_memory_card_cross_check",
                        "jue_wiki_reference_basis",
                        "jue_wiki_usage_contract_resolution",
                    )
                    if metadata.get(key) not in (None, "", [], {})
                }
            ),
            list_limit=4,
            string_limit=140,
        )
    return compact


def _compact_action_storage_list(
    rows: Any,
    *,
    item_limit: int,
    string_limit: int,
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for row in _normalize_list(rows)[: max(int(item_limit), 0)]:
        compact = _compact_action_storage_row(row, string_limit=string_limit)
        if compact:
            compacted.append(compact)
    return compacted


def compact_kis_manager_applied_item_for_storage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        text = _clean_text(value, limit=120)
        return {"value": text} if text else {}
    block = value.get("block") if isinstance(value.get("block"), dict) else {}
    out: dict[str, Any] = {}
    for key in (
        "status",
        "reason",
        "block_id",
        "symbol",
        "name",
        "horizon",
        "entry_style",
        "entry_trigger_operator",
    ):
        source = value.get(key)
        if source in (None, "", [], {}) and block:
            source = block.get(key)
        if source in (None, "", [], {}):
            continue
        out[key] = _clean_text(source, limit=140)
    for key in (
        "qty",
        "qty_initial",
        "qty_open",
        "entry_price",
        "entry_trigger_price",
        "target_price",
        "stop_price",
        "confidence",
    ):
        source = value.get(key)
        if source in (None, "", [], {}) and block:
            source = block.get(key)
        if source in (None, "", [], {}):
            continue
        out[key] = source
    for key in ("reasons", "risks", "data_gaps", "next_actions"):
        raw = value.get(key)
        if isinstance(raw, list):
            items = [
                _clean_text(item, limit=140)
                for item in raw[:3]
                if _clean_text(item, limit=140)
            ]
            if items:
                out[key] = items
            if len(raw) > 3:
                out[f"{key}_omitted_count"] = len(raw) - 3
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    block_metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    context_summary: dict[str, Any] = {}
    for key in (
        "block_color",
        "horizon",
        "source",
        "applied_policy_versions",
        "jue_wiki_repair_pressure",
        "jue_wiki_repair_resolution",
        "jue_wiki_memory_card_quality",
        "jue_wiki_memory_card_cross_check",
        "jue_wiki_reference_basis",
        "jue_wiki_usage_contract_resolution",
    ):
        source = metadata.get(key) if key in metadata else block_metadata.get(key)
        if source not in (None, "", [], {}):
            context_summary[key] = source
    if context_summary:
        out["context_summary"] = compact_etf_prompt_value(
            public_prompt_payload(context_summary),
            list_limit=3,
            string_limit=100,
        )
    return out


def compact_kis_manager_applied_for_storage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, raw in value.items():
        key_str = str(key)
        if key_str == "policy_rule_impacts" and isinstance(raw, dict):
            samples: list[dict[str, Any]] = []
            for symbol, impacts in list(raw.items())[:3]:
                rule_ids: list[str] = []
                for impact in _normalize_list(impacts)[:3]:
                    if not isinstance(impact, dict):
                        continue
                    rule_id = _clean_text(impact.get("rule_id"), limit=80)
                    if rule_id:
                        rule_ids.append(rule_id)
                samples.append(
                    {
                        "symbol": _clean_text(symbol, limit=20),
                        "impact_count": len(_normalize_list(impacts)),
                        "rule_ids": rule_ids,
                    }
                )
            out[key_str] = {
                "symbol_count": len(raw),
                "samples": samples,
                "omitted_symbol_count": max(len(raw) - len(samples), 0),
            }
            continue
        if isinstance(raw, list):
            out[key_str] = {
                "item_count": len(raw),
                "items": [
                    item
                    for item in (
                        compact_kis_manager_applied_item_for_storage(row)
                        for row in raw[:2]
                    )
                    if item
                ],
                "omitted_item_count": max(len(raw) - 2, 0),
            }
            continue
        if isinstance(raw, dict):
            if isinstance(raw.get("items"), list):
                items = raw.get("items") or []
                out[key_str] = {
                    "item_count": int(raw.get("item_count") or len(items)),
                    "items": [
                        item
                        for item in (
                            compact_kis_manager_applied_item_for_storage(row)
                            for row in items[:2]
                        )
                        if item
                    ],
                    "omitted_item_count": max(
                        int(raw.get("omitted_item_count") or 0),
                        max(len(items) - 2, 0),
                    ),
                }
                continue
            out[key_str] = compact_etf_prompt_value(
                public_prompt_payload(raw),
                list_limit=3,
                string_limit=100,
            )
            continue
        out[key_str] = _clean_text(raw, limit=120)
    return out


def compact_kis_manager_actions_for_storage(
    value: dict[str, Any],
    *,
    label: str,
    original_chars: int,
    storage_limit: int,
    retained_keys: list[str],
    emergency: bool = False,
    item_limit: int = 16,
    string_limit: int = 260,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "_storage_compaction": manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
            emergency=emergency,
        ),
        "action_counts": {
            key: len(_normalize_list(value.get(key)))
            for key in KIS_MANAGER_ACTION_KEYS
        },
    }
    for key in KIS_MANAGER_ACTION_KEYS:
        compacted = _compact_action_storage_list(
            value.get(key),
            item_limit=item_limit,
            string_limit=string_limit,
        )
        if compacted:
            payload[key] = compacted
    if isinstance(value.get("_applied"), dict):
        payload["_applied"] = compact_kis_manager_applied_for_storage(
            value.get("_applied") or {}
        )
    if isinstance(value.get("hold_decision"), dict):
        payload["hold_decision"] = compact_etf_prompt_value(
            public_prompt_payload(value.get("hold_decision") or {}),
            list_limit=4,
            string_limit=180,
        )
    if isinstance(value.get("prompt_budget"), dict):
        payload["prompt_budget"] = value["prompt_budget"]
    if value.get("status") not in (None, ""):
        payload["status"] = _clean_text(value.get("status"), limit=80)
    return payload


def _fit_kis_manager_prompt_emergency_payload(
    payload: dict[str, Any],
    *,
    storage_limit: int,
) -> dict[str, Any]:
    dropped_keys: list[str] = []

    def has_manager_contract_recovery() -> bool:
        memory = (
            payload.get("investment_memory")
            if isinstance(payload.get("investment_memory"), dict)
            else {}
        )
        recovery = (
            memory.get("validation_recovery_summary")
            if isinstance(memory.get("validation_recovery_summary"), dict)
            else {}
        )
        return bool(_normalize_list(recovery.get("manager_contract_recovered")))

    def drop_key(key: str) -> None:
        if key not in payload:
            return
        payload.pop(key, None)
        if key not in dropped_keys:
            dropped_keys.append(key)

    def annotate() -> None:
        meta = (
            payload.get("_storage_compaction")
            if isinstance(payload.get("_storage_compaction"), dict)
            else {}
        )
        if not isinstance(meta, dict):
            return
        if has_manager_contract_recovery():
            meta["priority_reason"] = "manager_contract_recovery"
        if dropped_keys:
            sample: list[str] = []
            for key in (
                "research_spine",
                "daily_discovery",
                "jue_wiki",
                "live_authority",
            ):
                if key in dropped_keys and key not in sample:
                    sample.append(key)
            for key in dropped_keys:
                if key not in sample:
                    sample.append(key)
                if len(sample) >= 8:
                    break
            meta["dropped_keys"] = sample[:8]
            meta["dropped_key_count"] = len(dropped_keys)
        payload["_storage_compaction"] = meta

    if prompt_chars(payload) <= storage_limit:
        return payload
    critical_context_keys = (
        "research_spine",
        "daily_discovery",
        "quotes",
        "live_authority",
        "aggressive_opportunities",
        "jue_wiki_application",
        "jue_wiki",
    )
    if not has_manager_contract_recovery() and any(
        key in payload for key in critical_context_keys
    ):
        meta = (
            payload.get("_storage_compaction")
            if isinstance(payload.get("_storage_compaction"), dict)
            else {}
        )
        meta["overflow_preserved_critical_context"] = True
        meta["overflow_chars"] = prompt_chars(payload)
        payload["_storage_compaction"] = meta
        return payload
    for key in (
        "daily_discovery",
        "research_spine",
        "quotes",
        "aggressive_opportunities",
        "jue_wiki_application",
        "jue_wiki",
        "live_authority",
        "execution_gate",
        "hold_decision",
    ):
        if key not in payload:
            continue
        drop_key(key)
        annotate()
        if prompt_chars(payload) <= storage_limit:
            return payload
    annotate()
    return payload


KIS_STORAGE_CRITICAL_PROMPT_SECTIONS: tuple[tuple[str, int, int], ...] = (
    ("investment_memory", 2, 120),
    ("jue_wiki", 2, 120),
    ("jue_wiki_application", 2, 120),
    ("jue_wiki_decision_adjustments", 2, 120),
    ("jue_wiki_requested_symbol_coverage", 2, 120),
    ("jue_wiki_memory_card_quality", 2, 120),
    ("jue_wiki_repair_contract", 2, 120),
    ("jue_wiki_action_pressure_contract", 2, 120),
    ("research_spine", 2, 160),
    ("daily_discovery", 2, 160),
    ("aggressive_opportunities", 3, 150),
    ("market_pulse", 2, 140),
    ("opportunity_research_brief", 3, 140),
    ("execution_gate", 2, 120),
)


def preserve_kis_storage_prompt_context(
    compact: dict[str, Any],
    original: dict[str, Any],
    *,
    list_limit: int,
    string_limit: int,
) -> None:
    if not isinstance(compact, dict) or not isinstance(original, dict):
        return
    decision_inputs = [
        _clean_text(item, limit=80)
        for item in list(original.get("decision_inputs") or [])
        if str(item or "").strip()
    ]
    if decision_inputs:
        compact["decision_inputs"] = decision_inputs[:60]
    if isinstance(original.get("diagnostics"), dict):
        compact["diagnostics"] = compact_kis_manager_diagnostics_for_storage(
            original.get("diagnostics") or {},
            list_limit=max(min(int(list_limit), 8), 1),
            string_limit=max(min(int(string_limit), 180), 48),
        )
    for section, default_list_limit, default_string_limit in (
        KIS_STORAGE_CRITICAL_PROMPT_SECTIONS
    ):
        if section not in original:
            continue
        section_list_limit = max(min(int(list_limit), default_list_limit), 1)
        section_string_limit = max(min(int(string_limit), default_string_limit), 48)
        compact[section] = compact_prompt_section(
            section,
            original.get(section),
            list_limit=section_list_limit,
            string_limit=section_string_limit,
        )


def _compact_diagnostic_top_blockers(
    value: Any,
    *,
    list_limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _normalize_list(value)[: max(int(list_limit), 1)]:
        if not isinstance(row, dict):
            continue
        tag = _clean_text(row.get("tag"), limit=120)
        if not tag:
            continue
        weight = (
            _safe_int(row.get("weight"))
            or _safe_int(row.get("count"))
            or _safe_int(row.get("score"))
            or 1
        )
        rows.append({"tag": tag, "weight": max(weight, 1)})
    return rows


def compact_kis_manager_diagnostics_for_storage(
    value: dict[str, Any],
    *,
    list_limit: int = 8,
    string_limit: int = 180,
) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    if not diagnostics:
        return {}
    keep_scalar = (
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
        "jue_wiki_action_reference_unscoped_page_omitted_count",
        "jue_wiki_usage_contract_status",
        "jue_wiki_usage_contract_resolution_count",
        "jue_wiki_usage_contract_resolution_ratio",
        "jue_wiki_action_reference_recovery_status",
        "jue_wiki_action_reference_recovery_memory_scope",
        "jue_wiki_action_reference_recovery_open_gap_count",
        "jue_wiki_action_reference_recovery_resolved_count",
        "jue_wiki_action_reference_recovery_total_count",
        "jue_wiki_action_reference_recovery_ratio",
        "jue_wiki_action_reference_recovery_latest_resolution_status",
        "jue_wiki_action_reference_recovery_latest_status",
        "degraded_jue_wiki_effectiveness_count",
        "degraded_jue_wiki_effectiveness_resolution_status",
        "candidate_memory_hint_status",
        "candidate_memory_hint_count",
        "candidate_memory_hint_resolved_count",
        "candidate_memory_hint_unresolved_count",
        "research_spine_memory_status",
        "research_spine_memory_count",
        "research_spine_memory_resolved_count",
        "research_spine_memory_unresolved_count",
        "memory_contract_status",
        "memory_contract_count",
        "memory_contract_resolved_count",
        "memory_contract_unresolved_count",
        "memory_contract_action_resolved_count",
        "memory_contract_hold_resolved_count",
        "memory_contract_response_resolved_count",
    )
    out: dict[str, Any] = {}
    for key in keep_scalar:
        if diagnostics.get(key) not in (None, "", [], {}):
            out[key] = diagnostics.get(key)
    for key in (
        "blocker_tags",
        "top_blockers",
        "jue_wiki_missing_summary_symbols",
        "jue_wiki_prompt_omitted_symbols",
        "jue_wiki_action_reference_unscoped_page_ids",
        "jue_wiki_action_reference_missing_actions",
        "jue_wiki_attention_must_address",
        "jue_wiki_weak_memory_card_symbols",
        "degraded_jue_wiki_effectiveness_page_ids",
        "candidate_memory_hint_missing_symbols",
        "research_spine_memory_missing_symbols",
        "memory_contract_missing_symbols",
        "memory_contract_missing_contracts",
        "memory_contract_missing_errors",
        "memory_contract_resolution_modes",
        "memory_contract_rows",
    ):
        if diagnostics.get(key) not in (None, "", [], {}):
            if key == "top_blockers":
                out[key] = _compact_diagnostic_top_blockers(
                    diagnostics.get(key),
                    list_limit=max(int(list_limit), 1),
                )
            else:
                out[key] = compact_etf_prompt_value(
                    public_prompt_payload(diagnostics.get(key)),
                    list_limit=max(int(list_limit), 1),
                    string_limit=max(int(string_limit), 48),
                )
    return out


def compact_validation_repair_for_emergency_storage(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata = validation_repair_action_metadata(value)
    repair = metadata.get("validation_repair") if isinstance(metadata, dict) else {}
    source = repair if isinstance(repair, dict) and repair else value
    out: dict[str, Any] = {}
    for key in ("version", "scope", "status"):
        raw = source.get(key)
        if raw not in (None, "", [], {}):
            out[key] = _clean_text(raw, limit=96)
    for key in ("repair_item_count", "constraint_count"):
        count = _safe_int(source.get(key))
        if count:
            out[key] = count
    for key in (
        "discipline_ids",
        "repair_action_ids",
        "scale_blockers",
    ):
        rows = [
            _clean_text(item, limit=96)
            for item in _normalize_list(source.get(key))[:3]
            if str(item or "").strip()
        ]
        if rows:
            out[key] = rows
    if "hard_filter" in source:
        out["hard_filter"] = bool(source.get("hard_filter"))
    elif metadata:
        out["hard_filter"] = False
    for key in ("risk_budget_multiplier", "max_budget_multiplier"):
        raw = source.get(key)
        if raw not in (None, "", [], {}):
            out[key] = _safe_float(raw)
    return out


def compact_manager_storage_payload(
    value: dict[str, Any],
    *,
    limit: int,
    label: str,
    compact_value: Callable[..., Any] | None = None,
    clean_text: Callable[[Any, int], str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if compact_value is None:
        def compact_fn(raw: Any, *, list_limit: int, string_limit: int) -> Any:
            return compact_etf_prompt_value(
                public_prompt_payload(raw),
                list_limit=list_limit,
                string_limit=string_limit,
            )
    else:
        compact_fn = compact_value
    clean_text_fn = clean_text or (
        lambda raw, text_limit: _clean_text(raw, limit=text_limit)
    )
    storage_limit = max(int(limit), 1000)
    original_chars = prompt_chars(value)
    if original_chars <= storage_limit:
        return value
    retained_keys = [str(key) for key in value.keys()]
    if label == "kis_manager_actions":
        for item_limit, string_limit in ((16, 260), (10, 180), (6, 120), (3, 80)):
            compact_actions = compact_kis_manager_actions_for_storage(
                value,
                label=label,
                original_chars=original_chars,
                storage_limit=storage_limit,
                retained_keys=retained_keys,
                item_limit=item_limit,
                string_limit=string_limit,
            )
            if prompt_chars(compact_actions) <= storage_limit:
                return compact_actions
        return compact_kis_manager_actions_for_storage(
            value,
            label=label,
            original_chars=original_chars,
            storage_limit=storage_limit,
            retained_keys=retained_keys,
            emergency=True,
            item_limit=1,
            string_limit=80,
        )
    string_limit = 900
    list_limit = 10
    compact = compact_fn(
        value,
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if not isinstance(compact, dict):
        compact = {}
    if label == "kis_manager_prompt":
        preserve_kis_storage_prompt_context(
            compact,
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if isinstance(value.get("prompt_budget"), dict):
        compact["prompt_budget"] = value["prompt_budget"]
    if isinstance(value.get("prompt_compaction"), dict):
        compact["prompt_compaction"] = compact_fn(
            value["prompt_compaction"],
            list_limit=8,
            string_limit=180,
        )
    compact["_storage_compaction"] = manager_storage_compaction_meta(
        label=label,
        original_chars=original_chars,
        storage_limit_chars=storage_limit,
        retained_keys=retained_keys,
    )
    while prompt_chars(compact) > storage_limit and (
        string_limit > 120 or list_limit > 2
    ):
        string_limit = max(int(string_limit * 0.55), 120)
        list_limit = max(int(list_limit // 2), 2)
        compact = compact_fn(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
        if not isinstance(compact, dict):
            compact = {}
        if label == "kis_manager_prompt":
            preserve_kis_storage_prompt_context(
                compact,
                value,
                list_limit=list_limit,
                string_limit=string_limit,
            )
        if isinstance(value.get("prompt_budget"), dict):
            compact["prompt_budget"] = value["prompt_budget"]
        compact["_storage_compaction"] = manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
        )
    if prompt_chars(compact) <= storage_limit:
        return compact
    emergency_payload = {
        "_storage_compaction": manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
            emergency=True,
        ),
        "prompt_budget": value.get("prompt_budget")
        if isinstance(value.get("prompt_budget"), dict)
        else {},
        "status": clean_text_fn(value.get("status"), 80),
        "hold_decision": compact_fn(
            value.get("hold_decision") or {},
            list_limit=4,
            string_limit=220,
        ),
    }
    if "research_spine" in value:
        emergency_payload["research_spine"] = compact_prompt_section(
            "research_spine",
            value.get("research_spine"),
            list_limit=1,
            string_limit=64,
        )
    if "daily_discovery" in value:
        emergency_payload["daily_discovery"] = compact_prompt_section(
            "daily_discovery",
            value.get("daily_discovery"),
            list_limit=1,
            string_limit=64,
        )
    if "quotes" in value:
        emergency_payload["quotes"] = compact_prompt_section(
            "quotes",
            value.get("quotes"),
            list_limit=1,
            string_limit=64,
        )
    if "execution_gate" in value:
        emergency_payload["execution_gate"] = compact_prompt_section(
            "execution_gate",
            value.get("execution_gate"),
            list_limit=1,
            string_limit=80,
        )
    if "validation_repair" in value:
        repair = compact_validation_repair_for_emergency_storage(
            value.get("validation_repair") or {}
        )
        emergency_payload["validation_repair"] = repair or compact_fn(
            value.get("validation_repair") or {},
            list_limit=3,
            string_limit=100,
        )
    if "validation_repair_response_contract" in value:
        emergency_payload["validation_repair_response_contract"] = compact_fn(
            value.get("validation_repair_response_contract") or {},
            list_limit=4,
            string_limit=120,
        )
    if "aggressive_opportunities" in value:
        emergency_payload["aggressive_opportunities"] = compact_prompt_section(
            "aggressive_opportunities",
            value.get("aggressive_opportunities"),
            list_limit=4,
            string_limit=100,
        )
    if label == "kis_manager_prompt":
        preserve_kis_storage_prompt_context(
            emergency_payload,
            value,
            list_limit=1,
            string_limit=80,
        )
    if "live_authority" in value:
        emergency_payload["live_authority"] = compact_prompt_section(
            "live_authority",
            value.get("live_authority"),
            list_limit=1,
            string_limit=64,
        )
    if "investment_memory" in value:
        emergency_payload["investment_memory"] = compact_prompt_section(
            "investment_memory",
            value.get("investment_memory"),
            list_limit=1,
            string_limit=64,
        )
    if "jue_wiki" in value:
        emergency_payload["jue_wiki"] = compact_prompt_section(
            "jue_wiki",
            value.get("jue_wiki"),
            list_limit=1,
            string_limit=64,
        )
    if "jue_wiki_application" in value:
        emergency_payload["jue_wiki_application"] = (
            compact_jue_wiki_application_prompt(
                value.get("jue_wiki_application") or {},
                list_limit=1,
                string_limit=80,
                emergency=True,
            )
        )
    if "jue_wiki_decision_adjustments" in value:
        emergency_payload["jue_wiki_decision_adjustments"] = compact_fn(
            value.get("jue_wiki_decision_adjustments") or {},
            list_limit=4,
            string_limit=120,
        )
    if "jue_wiki_requested_symbol_coverage" in value:
        emergency_payload["jue_wiki_requested_symbol_coverage"] = compact_fn(
            value.get("jue_wiki_requested_symbol_coverage") or {},
            list_limit=4,
            string_limit=120,
        )
    if "jue_wiki_memory_card_quality" in value:
        emergency_payload["jue_wiki_memory_card_quality"] = (
            compact_jue_wiki_memory_card_quality_prompt(
                value.get("jue_wiki_memory_card_quality") or {},
                list_limit=4,
                string_limit=120,
            )
        )
    if "jue_wiki_repair_contract" in value:
        emergency_payload["jue_wiki_repair_contract"] = (
            compact_jue_wiki_repair_contract_prompt(
                value.get("jue_wiki_repair_contract") or {},
                list_limit=4,
                string_limit=120,
            )
        )
    return _fit_kis_manager_prompt_emergency_payload(
        emergency_payload,
        storage_limit=storage_limit,
    )


PROMPT_DROPPED_KEYS = {
    "raw",
    "raw_payload",
    "response",
    "body",
    "html",
    "content",
    "payload_json",
    "raw_json",
}
HOLD_DECISION_HORIZONS = ("short", "mid", "long", "core_etf", "cash")
CREATIVE_HYPOTHESIS_TYPES = {
    "leader_pullback",
    "second_rank",
    "next_sector",
    "missed_upside",
    "etf_rotation",
    "contrarian",
}
CREATIVE_HYPOTHESIS_DECISIONS = {
    "create_wait_block",
    "create_now_block",
    "watch",
    "reject",
}
ETF_SNAPSHOT_PROMPT_KEYS = {
    "symbol",
    "name",
    "status",
    "price",
    "change_pct",
    "volume",
    "turnover_krw",
    "captured_at",
    "stale",
    "error_message",
}
ETF_SCORE_PROMPT_KEYS = {
    "symbol",
    "name",
    "status",
    "label",
    "liquidity_score",
    "momentum_score",
    "core_fit_score",
    "risk_score",
    "scored_at",
    "reasons",
    "risks",
    "error_message",
}
ETF_CANDIDATE_PROMPT_KEYS = {
    "symbol",
    "name",
    "asset_class",
    "horizon_bias",
    "horizon",
    "score",
    "confidence",
    "risk_score",
    "sources",
    "reasons",
    "risks",
}
PROMPT_BLOCK_KEYS = {
    "block_id",
    "symbol",
    "name",
    "qty_initial",
    "qty_open",
    "entry_price",
    "target_price",
    "stop_price",
    "status",
    "force_exit_requested",
    "thesis",
    "llm_reason",
    "risk_note",
    "created_by",
    "created_at",
    "opened_at",
    "closed_at",
    "updated_at",
}
PROMPT_BLOCK_METADATA_KEYS = {
    "entry_style",
    "entry_trigger_price",
    "entry_trigger_operator",
    "entry_trigger_status",
    "horizon",
    "block_color",
    "confidence",
    "decision_class",
    "target_block_value_krw",
    "max_loss_krw",
    "stop_policy",
    "allocation_reason",
    "jue_wiki_repair_pressure",
    "jue_wiki_repair_resolution",
    "jue_wiki_memory_card_quality",
    "jue_wiki_memory_card_cross_check",
    "jue_wiki_selection_resolution",
    "jue_wiki_freshness_cross_check",
    "jue_wiki_context_gap",
    "jue_wiki_reference_basis",
    "jue_wiki_usage_contract_resolution",
    "cost_feasibility",
    "what_would_change_my_mind",
    "user_directive",
    "user_directives",
    "post_review_required",
}
VISIBLE_BLOCK_STATUSES = {"proposed", "entry_pending", "open", "exit_pending"}
PROMPT_EVENT_PAYLOAD_KEYS = {
    "reason",
    "side",
    "symbol",
    "price",
    "qty",
    "quantity",
    "limit_price",
    "status",
}
PROMPT_QUOTE_KEYS = {
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
MARKET_JUDGMENT_STRATEGY_KEYS = {
    "symbol",
    "name",
    "score",
    "score_method_version",
    "suitability",
    "confidence",
    "stance",
    "reasons",
    "risks",
    "checks",
    "data_warnings",
    "data_coverage",
    "valuation",
    "asset_class",
    "horizon_bias",
    "sources",
}
PROMPT_BUDGET_COMPACTION_ORDER: tuple[tuple[str, int, int], ...] = (
    ("blocks", 12, 220),
    ("pre_adoption_symbol_analysis", 8, 180),
    ("investment_memory", 5, 180),
    ("jue_wiki", 3, 180),
    ("jue_wiki_decision_adjustments", 4, 150),
    ("jue_wiki_requested_symbol_coverage", 4, 120),
    ("jue_wiki_memory_card_quality", 4, 120),
    ("jue_wiki_repair_contract", 4, 120),
    ("research_spine", 8, 190),
    ("opportunity_research_brief", 5, 180),
    ("decision_packet", 5, 170),
    ("decision_packet_v2", 5, 170),
    ("candidate_policy_impacts", 24, 140),
    ("validation_repair", 5, 150),
    ("validation_repair_response_contract", 5, 150),
    ("execution_gate", 5, 150),
    ("market_judgment", 5, 170),
    ("policy_rules", 8, 180),
    ("recent_events", 30, 180),
    ("portfolio_balance", 8, 170),
    ("etf_universe", 40, 120),
    ("etf_research", 8, 170),
    ("market_pulse", 6, 170),
    ("aggressive_opportunities", 8, 160),
    ("missed_upside_reviews", 6, 160),
    ("creative_hypotheses", 6, 160),
    ("decision_lifecycle_v3", 5, 170),
    ("strategy", 10, 180),
    ("daily_discovery", 6, 170),
    ("trading_playbook", 6, 150),
    ("policy", 5, 150),
    ("jue_workflow", 5, 150),
    ("output_schema", 5, 150),
    ("live_authority", 5, 150),
    ("quotes", 20, 140),
    ("kr_pattern_lab", 6, 150),
)
PROMPT_BUDGET_OMITTABLE_SECTIONS = (
    "candidate_policy_impacts",
    "policy_rules",
    "kr_pattern_lab",
    "etf_research",
    "recent_events",
    "decision_packet",
    "market_judgment",
    "pre_adoption_symbol_analysis",
    "portfolio_balance",
    "etf_universe",
    "missed_upside_reviews",
    "creative_hypotheses",
    "trading_playbook",
)
PROMPT_BUDGET_CRITICAL_OPPORTUNITY_SECTIONS = (
    "quotes",
    "research_spine",
    "daily_discovery",
    "aggressive_opportunities",
    "market_pulse",
    "decision_packet_v2",
)


def compact_prompt_value(
    value: Any,
    *,
    list_limit: int = 6,
    string_limit: int = 240,
) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in PROMPT_DROPPED_KEYS:
                continue
            compact[str(key)] = compact_prompt_value(
                child,
                list_limit=list_limit,
                string_limit=string_limit,
            )
        return compact
    if isinstance(value, list):
        return [
            compact_prompt_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
            )
            for item in value[: max(int(list_limit), 0)]
        ]
    if isinstance(value, str):
        return _clean_text(value, limit=string_limit)
    return value


def compact_prompt_fields(value: Any, allowed_keys: set[str]) -> dict[str, Any]:
    payload = compact_prompt_value(value)
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload.get(key)
        for key in allowed_keys
        if payload.get(key) not in (None, "", [], {})
    }


def _compact_prompt_mapping(
    value: Any,
    *,
    limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, child in list(value.items())[: max(int(limit), 0)]:
        compact[_clean_text(key, limit=80)] = compact_prompt_value(
            child,
            list_limit=2,
            string_limit=string_limit,
        )
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def _kis_memory_rows_for_prompt_scope(value: Any) -> list[Any]:
    rows: list[Any] = []
    for row in _normalize_list(value):
        if not isinstance(row, dict) or _kis_memory_node_matches_prompt_scope(row):
            rows.append(row)
    return rows


def _kis_memory_mapping_key_matches_prompt_scope(key: Any) -> bool:
    text = str(key or "").strip().lower()
    compact = (
        text.replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not compact:
        return True
    if "binance" in compact or "crypto" in compact:
        return False
    if compact.endswith(("usdt", "usdc", "busd")):
        return False
    if compact in {"btc", "eth", "bnb", "sol", "xrp", "ada", "doge"}:
        return False
    return True


def _kis_memory_child_is_translated(child: Any) -> bool:
    if isinstance(child, dict):
        return _kis_wiki_memory_transferability_is_translated(child)
    if isinstance(child, (list, tuple)):
        return any(_kis_memory_child_is_translated(item) for item in child)
    return False


def _kis_memory_mapping_for_prompt_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scoped: dict[str, Any] = {}
    for key, child in value.items():
        if (
            not _kis_memory_mapping_key_matches_prompt_scope(key)
            and not _kis_memory_child_is_translated(child)
        ):
            continue
        if isinstance(child, dict):
            row = {**child, "id": child.get("id") or key}
            if not _kis_memory_node_matches_prompt_scope(row):
                continue
        scoped[key] = child
    return scoped


def _kis_memory_node_matches_prompt_scope(row: dict[str, Any]) -> bool:
    if not _kis_wiki_memory_item_matches_scope(row):
        return False
    if not _kis_jue_wiki_prompt_item_matches_scope(row):
        return False
    source_scope = str(row.get("source_scope") or "").strip()
    if (
        source_scope
        and not _kis_wiki_memory_transferability_is_translated(row)
        and not _kis_wiki_memory_scope_value_matches(source_scope)
    ):
        return False
    return True


_KIS_MEMORY_SCOPE_COUNT_MAPPING_KEYS = {
    "source_scope_counts",
    "target_scope_counts",
    "scope_counts",
}


def _kis_memory_payload_for_prompt_scope(
    value: Any,
    *,
    filter_mapping_keys: bool = True,
) -> Any:
    if isinstance(value, dict):
        node_matches_scope = _kis_memory_node_matches_prompt_scope(value)
        scoped: dict[str, Any] = {}
        for key, child in value.items():
            if not node_matches_scope and not isinstance(
                child,
                (dict, list, tuple),
            ):
                continue
            if (
                filter_mapping_keys
                and not _kis_memory_mapping_key_matches_prompt_scope(key)
                and not _kis_memory_child_is_translated(child)
            ):
                continue
            scoped_child = _kis_memory_payload_for_prompt_scope(
                child,
                filter_mapping_keys=filter_mapping_keys
                and str(key) not in _KIS_MEMORY_SCOPE_COUNT_MAPPING_KEYS,
            )
            if scoped_child not in (None, "", [], {}):
                scoped[key] = scoped_child
        return scoped
    if isinstance(value, (list, tuple)):
        rows: list[Any] = []
        for item in value:
            scoped_item = _kis_memory_payload_for_prompt_scope(
                item,
                filter_mapping_keys=filter_mapping_keys,
            )
            if scoped_item not in (None, "", [], {}):
                rows.append(scoped_item)
        return rows
    return value


def _kis_decision_skill_matches_prompt_scope(key: Any, child: dict[str, Any]) -> bool:
    if _kis_wiki_memory_transferability_is_translated(child):
        return True
    identity = " ".join(
        str(value or "")
        for value in (
            key,
            child.get("skill_id"),
            child.get("id"),
            child.get("scope"),
            child.get("target_scope"),
            child.get("market"),
        )
    )
    compact = (
        identity.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not compact:
        return True
    return "binance" not in compact and "crypto" not in compact


def compact_investment_memory_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "invalid"}
    limit = max(int(list_limit), 1)
    text_limit = max(int(string_limit), 60)
    compact: dict[str, Any] = {
        key: _clean_text(value.get(key), limit=text_limit)
        for key in (
            "status",
            "memory_scope",
            "persona",
            "trading_policy",
            "safety_note",
        )
        if value.get(key) not in (None, "", [], {})
    }
    if not compact.get("status"):
        compact["status"] = "ok"

    scoped = value.get("scoped_memory") if isinstance(value.get("scoped_memory"), dict) else {}
    if scoped:
        scoped_payload: dict[str, Any] = {}
        for scope in ("core", "local", "cross", "translated"):
            rows = _kis_memory_rows_for_prompt_scope(scoped.get(scope))[
                : min(limit, 4)
            ]
            if rows:
                scoped_payload[scope] = compact_prompt_value(
                    rows,
                    list_limit=min(limit, 4),
                    string_limit=text_limit,
                )
        scalar_scoped = {
            key: scoped.get(key)
            for key in ("blocked_count", "local_count", "core_count")
            if scoped.get(key) not in (None, "", [], {})
        }
        scoped_payload.update(scalar_scoped)
        if scoped_payload:
            compact["scoped_memory"] = scoped_payload

    list_sections = (
        ("items", min(limit, 6)),
        ("notes", min(limit, 6)),
        ("lessons", min(limit, 6)),
        ("memories", min(limit, 6)),
        ("active_policies", min(limit, 8)),
        ("policy_scorecards", min(limit, 8)),
        ("policy_rules", min(limit, 8)),
        ("recent_reflections", min(limit, 5)),
        ("latest_journals", min(limit, 4)),
        ("seed_memory", min(limit, 3)),
        ("active_insights", min(limit, 6)),
        ("policy_revisions", min(limit, 6)),
        ("policy_outcomes", min(limit, 6)),
    )
    for key, section_limit in list_sections:
        rows = _kis_memory_rows_for_prompt_scope(value.get(key))[:section_limit]
        if rows:
            compact[key] = compact_prompt_value(
                rows,
                list_limit=section_limit,
                string_limit=text_limit,
            )

    for key, section_limit in (
        ("symbol_notes", min(limit, 8)),
        ("block_notes", min(limit, 6)),
        ("validation_repair_backlog", min(limit, 4)),
        ("policy_rule_evaluation", min(limit, 5)),
    ):
        mapping = _compact_prompt_mapping(
            _kis_memory_mapping_for_prompt_scope(value.get(key)),
            limit=section_limit,
            string_limit=text_limit,
        )
        if mapping:
            compact[key] = mapping

    for key in (
        "period_memory_coverage",
        "period_reviews",
        "historical_replays",
        "block_design_constraints",
        "validation_recovery_summary",
        "next_block_design_playbook",
        "translated_policy_context",
        "jue_wiki_selection_memory",
        "jue_wiki_context_gap_memory",
        "jue_wiki_action_reference_memory",
        "jue_wiki_usage_contract_memory",
        "market_pulse",
        "etf_core",
        "jue_wiki",
        "decision_skill_status",
    ):
        scoped_value = _kis_memory_payload_for_prompt_scope(value.get(key))
        payload = compact_prompt_value(
            scoped_value,
            list_limit=min(limit, 4),
            string_limit=text_limit,
        )
        if payload not in (None, "", [], {}):
            compact[key] = payload

    decision_skills = value.get("decision_skills")
    if isinstance(decision_skills, dict):
        compact_decision_skills: dict[str, dict[str, Any]] = {}
        for key, child in list(decision_skills.items())[: min(limit, 6)]:
            if not isinstance(child, dict):
                continue
            if not _kis_decision_skill_matches_prompt_scope(key, child):
                continue
            compact_child = {
                child_key: _clean_text(child.get(child_key), limit=text_limit)
                for child_key in ("skill_id", "version", "preview")
                if child.get(child_key) not in (None, "", [], {})
            }
            if "preview" not in compact_child and child.get("content_md"):
                compact_child["preview"] = _clean_text(
                    child.get("content_md"),
                    limit=text_limit,
                )
            compact_decision_skills[_clean_text(key, limit=80)] = compact_child
        compact["decision_skills"] = compact_decision_skills
        compact["decision_skills"] = {
            key: child
            for key, child in compact["decision_skills"].items()
            if child
        }
        if not compact["decision_skills"]:
            compact.pop("decision_skills", None)

    compact["_prompt_compaction"] = {
        "section": "investment_memory",
        "original_chars": len(_json_dumps(value)),
        "mode": "decision_memory_compact",
    }
    return compact


def sanitize_hold_trigger(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    symbol = str(value.get("symbol") or "").strip()
    row: dict[str, Any] = {
        "symbol": symbol if _is_symbol(symbol) else "",
        "condition": _clean_text(value.get("condition"), limit=500),
        "price": _safe_float(value.get("price")) or None,
        "horizon": normalize_horizon(value.get("horizon")),
        "reason": _clean_text(value.get("reason"), limit=600),
    }
    return {key: child for key, child in row.items() if child not in {"", None}}


def sanitize_horizon_notes(value: Any) -> dict[str, list[str]]:
    source = value if isinstance(value, dict) else {}
    return {
        horizon: _clean_text_list(source.get(horizon), limit=500, max_items=5)
        for horizon in HOLD_DECISION_HORIZONS
    }


def sanitize_kis_hold_decision(
    value: Any,
    *,
    action_count: int = 0,
    missed_upside_reviews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    triggers = [
        row
        for row in (
            sanitize_hold_trigger(item)
            for item in _normalize_list(source.get("next_triggers"))[:8]
        )
        if row
    ]
    reviews = (
        missed_upside_reviews
        if missed_upside_reviews is not None
        else [
            row
            for row in _normalize_list(source.get("missed_upside_reviews"))[:8]
            if isinstance(row, dict)
        ]
    )
    summary = _clean_text(source.get("summary"), limit=800)
    if not summary and action_count <= 0:
        summary = "이번 KIS 매니저 실행은 새 블록 없이 관망했습니다."
    return {
        "summary": summary,
        "reasons": _clean_text_list(source.get("reasons"), limit=500, max_items=8),
        "watch_symbols": clean_symbol_list(source.get("watch_symbols"), max_items=16),
        "long_watch_symbols": clean_symbol_list(
            source.get("long_watch_symbols"),
            max_items=12,
        ),
        "next_triggers": triggers,
        "data_gaps": _clean_text_list(source.get("data_gaps"), limit=500, max_items=8),
        "risk_notes": _clean_text_list(source.get("risk_notes"), limit=500, max_items=8),
        "horizon_notes": sanitize_horizon_notes(source.get("horizon_notes")),
        "missed_upside_reviews": reviews[:8],
        "action_count": max(int(action_count), 0),
    }


def _kis_manager_action_item_count(actions: dict[str, Any]) -> int:
    if not isinstance(actions, dict):
        return 0
    return sum(
        len(actions.get(key) or [])
        for key in (
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        )
        if isinstance(actions.get(key), list)
    )


def _kis_actions_have_wiki_attention_resolution(actions: dict[str, Any]) -> bool:
    return _kis_actions_have_wiki_repair_metadata(
        actions,
        metadata_keys=("jue_wiki_repair_attention",),
    )


def _kis_actions_have_wiki_repair_metadata(
    actions: dict[str, Any],
    *,
    metadata_keys: tuple[str, ...] = (
        "jue_wiki_repair_attention",
        "jue_wiki_repair_pressure",
        "jue_wiki_repair_resolution",
        "jue_wiki_memory_card_quality",
        "jue_wiki_memory_card_cross_check",
    ),
) -> bool:
    if not isinstance(actions, dict):
        return False
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in metadata_keys:
                repair_note = metadata.get(metadata_key)
                if repair_note in (None, "", [], {}):
                    repair_note = row.get(metadata_key)
                if _kis_repair_note_is_negative(repair_note):
                    continue
                if _kis_repair_note_is_concrete(repair_note):
                    return True
    return False


def _kis_repair_note_is_concrete(value: Any) -> bool:
    generic = {
        "handled",
        "done",
        "ok",
        "resolved",
        "checked",
        "considered",
        "repair pressure handled",
        "wiki repair handled",
        "처리",
        "처리함",
        "확인",
        "확인함",
    }
    if isinstance(value, dict):
        if not value:
            return False
        return any(_kis_repair_note_is_concrete(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_kis_repair_note_is_concrete(child) for child in value)
    text = str(value or "").strip().lower()
    if not text or text in generic:
        return False
    if len(text) < 12:
        return False
    concrete_terms = (
        "repair:",
        "source_id",
        "resolution",
        "action_metadata",
        "metadata_records",
        "repair_attention",
        "wiki_attention",
        "stale",
        "missing",
        "omitted",
        "reduced",
        "defer",
        "refresh",
        "coverage",
        "evidence",
        "financial",
        "fresh",
        "narrative",
        "quality",
        "quote",
        "basis",
        "cross",
        "sizing",
        "confidence",
        "trigger",
        "waiting",
        "probe",
        "reject",
        "risk",
        "wiki",
        "위키",
        "수리",
        "누락",
        "오래",
        "갱신",
        "축소",
        "보류",
        "대기",
        "근거",
        "위험",
        "리스크",
    )
    return any(term in text for term in concrete_terms)


def _kis_repair_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_kis_repair_note_is_negative(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_kis_repair_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "still unresolved",
        "not resolved",
        "repair unresolved",
        "resolution missing",
        "repair missing",
        "not checked",
        "not cross checked",
        "no cross check",
        "no cross checks",
        "cross check missing",
        "cross checks missing",
        "no live quote",
        "live quote missing",
        "no valuation",
        "valuation missing",
        "no sizing reduction",
        "sizing reduction missing",
        "no risk gate",
        "risk gate missing",
        "미해결",
        "미확인",
        "수리 미완료",
        "해결 누락",
        "교차확인 없음",
        "교차 확인 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_prompt_has_active_validation_repair(prompt: dict[str, Any]) -> bool:
    repair = _kis_scoped_validation_repair(prompt)
    if bool(repair.get("hard_filter")):
        return False
    return (
        _safe_int(repair.get("repair_item_count")) > 0
        or _safe_int(repair.get("constraint_count")) > 0
    )


def _kis_prompt_has_jue_wiki_validation_repair_contract(
    prompt: dict[str, Any],
) -> bool:
    contract = _kis_scoped_jue_wiki_validation_repair_contract(prompt)
    if bool(contract.get("requires_validation_repair_resolution")):
        return True
    status = str(contract.get("status") or "").strip().lower()
    if status in {"repair_required", "degraded", "warning"}:
        return True
    if isinstance(contract.get("contract_feedback_gap"), dict):
        return True
    feedback_gap = _kis_scoped_jue_wiki_contract_feedback_gap(prompt)
    return bool(feedback_gap)


def _kis_prompt_has_wiki_repair_priorities(prompt: dict[str, Any]) -> bool:
    repair_contract = _kis_scoped_jue_wiki_repair_contract(prompt)
    if _normalize_list(repair_contract.get("action_batches")):
        return True
    if _normalize_list(repair_contract.get("top_priorities")):
        return True
    action_plan = repair_contract.get("repair_pressure_action_plan")
    action_plan = action_plan if isinstance(action_plan, dict) else {}
    for key in (
        "action_batch_total_count",
        "action_batch_omitted_count",
        "action_batch_visible_pressure_count",
        "omitted_priority_count",
        "total_priority_count",
    ):
        if _safe_int(action_plan.get(key)) > 0:
            return True
    return _safe_int(repair_contract.get("repair_priority_count")) > 0


def _kis_degraded_wiki_effectiveness_items(
    prompt: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(prompt, dict):
        return []
    jue_wiki = prompt.get("jue_wiki") if isinstance(prompt.get("jue_wiki"), dict) else {}
    rows: list[dict[str, Any]] = []
    for key in ("pages", "requested_symbol_summaries"):
        for item in _normalize_list(jue_wiki.get(key)):
            if not isinstance(item, dict):
                continue
            if not _kis_jue_wiki_prompt_item_matches_scope(item):
                continue
            effectiveness = (
                item.get("effectiveness")
                if isinstance(item.get("effectiveness"), dict)
                else {}
            )
            status = str(effectiveness.get("status") or "").strip().lower()
            if status != "degraded":
                continue
            rows.append(
                {
                    **item,
                    "effectiveness_reasons": list(
                        effectiveness.get("reasons") or []
                    ),
                }
            )
    for item in _normalize_list(jue_wiki.get("effectiveness_attention_items")):
        if not isinstance(item, dict):
            continue
        if not _kis_jue_wiki_prompt_item_matches_scope(item):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "degraded":
            rows.append(item)
    return rows


def _kis_prompt_has_degraded_wiki_effectiveness(prompt: dict[str, Any]) -> bool:
    return bool(_kis_degraded_wiki_effectiveness_items(prompt))


def _kis_prompt_has_wiki_attention_response_contract(prompt: dict[str, Any]) -> bool:
    repair_contract = _kis_scoped_jue_wiki_repair_contract(prompt)
    contract = repair_contract.get("attention_plan_response_contract")
    contract = contract if isinstance(contract, dict) else {}
    if str(contract.get("status") or "").strip().lower() != "active":
        return False
    return bool(_normalize_list(contract.get("must_address")))


def _kis_prompt_has_wiki_selection_guidance(prompt: dict[str, Any]) -> bool:
    return bool(_kis_wiki_selection_guidance_terms(prompt))


def _kis_wiki_memory_item_matches_scope(
    row: dict[str, Any],
    *,
    inherited_translated: bool = False,
) -> bool:
    item_scope = str(
        row.get("memory_scope")
        or row.get("scope")
        or row.get("venue")
        or row.get("market")
        or ""
    ).strip().lower()
    translated = inherited_translated or _kis_wiki_memory_transferability_is_translated(
        row
    )
    if item_scope and not _kis_wiki_memory_scope_value_matches(item_scope):
        if not translated:
            return False
    policy_id = str(row.get("policy_id") or row.get("id") or "").strip()
    return translated or _kis_memory_mapping_key_matches_prompt_scope(policy_id)


def _kis_wiki_memory_scope_value_matches(value: str) -> bool:
    scope = (
        value.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not scope:
        return True
    tokens = {token for token in scope.split("_") if token}
    if tokens.intersection({"global", "shared", "core"}):
        return True
    if "binance" in tokens or "crypto" in tokens or scope.startswith(("binance", "crypto")):
        return False
    if "kis" in tokens or "krx" in tokens:
        return True
    if scope.startswith(("kr_equity", "korean_equity")):
        return True
    if scope.startswith(("korea_equity", "domestic_equity")):
        return True
    if "kr" in tokens and tokens.intersection({"equity", "equities", "stock", "stocks"}):
        return True
    if "korean" in tokens and tokens.intersection({"equity", "equities", "stock", "stocks"}):
        return True
    if "domestic" in tokens and tokens.intersection({"equity", "equities", "stock", "stocks"}):
        return True
    return scope in {"kr", "korea", "domestic"}


def _kis_wiki_memory_transferability_is_translated(value: dict[str, Any]) -> bool:
    transferability = str(
        value.get("transferability")
        or value.get("translation_status")
        or value.get("cross_scope_status")
        or value.get("cross_venue_status")
        or ""
    ).strip().lower()
    if transferability in {"translated", "translation", "cross", "cross_venue"}:
        return True
    return bool(value.get("translated") is True or value.get("is_translated") is True)


def _kis_wiki_memory_container_matches_scope(container: dict[str, Any]) -> bool:
    target_scope = str(
        container.get("target_scope")
        or container.get("memory_scope")
        or container.get("scope")
        or container.get("venue")
        or container.get("market")
        or ""
    ).strip().lower()
    if _kis_wiki_memory_transferability_is_translated(container):
        return True
    return _kis_wiki_memory_scope_value_matches(target_scope)


def _kis_jue_wiki_prompt_item_matches_scope(item: dict[str, Any]) -> bool:
    if _kis_wiki_memory_transferability_is_translated(item):
        return True
    item_scope = str(
        item.get("target_scope")
        or item.get("memory_scope")
        or item.get("scope")
        or item.get("venue")
        or item.get("market")
        or ""
    ).strip().lower()
    if item_scope and not _kis_wiki_memory_scope_value_matches(item_scope):
        return False
    page_id = str(item.get("page_id") or item.get("id") or "").strip()
    return _kis_memory_mapping_key_matches_prompt_scope(page_id)


def _kis_scoped_memory_card_quality(prompt: dict[str, Any]) -> dict[str, Any]:
    quality = (
        prompt.get("jue_wiki_memory_card_quality")
        if isinstance(prompt, dict)
        else {}
    )
    quality = quality if isinstance(quality, dict) else {}
    if quality and not _kis_wiki_memory_container_matches_scope(quality):
        return {}
    return quality


def _kis_scoped_jue_wiki_repair_contract(prompt: dict[str, Any]) -> dict[str, Any]:
    repair_contract = (
        prompt.get("jue_wiki_repair_contract") if isinstance(prompt, dict) else {}
    )
    repair_contract = repair_contract if isinstance(repair_contract, dict) else {}
    if repair_contract and not _kis_wiki_memory_container_matches_scope(
        repair_contract
    ):
        return {}
    if not repair_contract:
        return {}
    contract = dict(repair_contract)
    top_priorities = [
        row
        for row in _normalize_list(repair_contract.get("top_priorities"))
        if not isinstance(row, dict) or _kis_jue_wiki_prompt_item_matches_scope(row)
    ]
    action_batches = [
        row
        for row in _normalize_list(repair_contract.get("action_batches"))
        if not isinstance(row, dict) or _kis_jue_wiki_prompt_item_matches_scope(row)
    ]
    if "top_priorities" in repair_contract:
        contract["top_priorities"] = top_priorities
        original_priority_count = _safe_int(
            repair_contract.get("repair_priority_count")
        )
        contract["repair_priority_count"] = (
            max(original_priority_count, len(top_priorities)) if top_priorities else 0
        )
    if "action_batches" in repair_contract:
        contract["action_batches"] = action_batches
        visible_count = sum(
            max(_safe_int(row.get("count")), 0)
            for row in action_batches
            if isinstance(row, dict)
        ) or len(action_batches)
        contract["action_batch_total_count"] = (
            max(_safe_int(repair_contract.get("action_batch_total_count")), len(action_batches))
            if action_batches
            else 0
        )
        contract["action_batch_omitted_count"] = (
            _safe_int(repair_contract.get("action_batch_omitted_count"))
            if action_batches
            else 0
        )
        contract["action_batch_visible_pressure_count"] = (
            max(
                _safe_int(repair_contract.get("action_batch_visible_pressure_count")),
                visible_count,
            )
            if action_batches
            else 0
        )
        action_plan = (
            dict(repair_contract.get("repair_pressure_action_plan"))
            if isinstance(repair_contract.get("repair_pressure_action_plan"), dict)
            else {}
        )
        if action_plan:
            action_plan["action_batch_total_count"] = contract[
                "action_batch_total_count"
            ]
            action_plan["action_batch_omitted_count"] = contract[
                "action_batch_omitted_count"
            ]
            action_plan["action_batch_visible_pressure_count"] = contract[
                "action_batch_visible_pressure_count"
            ]
            contract["repair_pressure_action_plan"] = action_plan
    attention_contract = contract.get("attention_plan_response_contract")
    if isinstance(attention_contract, dict) and not _kis_jue_wiki_prompt_item_matches_scope(
        attention_contract
    ):
        contract["attention_plan_response_contract"] = {}
    return contract


def _kis_scoped_validation_repair(prompt: dict[str, Any]) -> dict[str, Any]:
    repair = prompt.get("validation_repair") if isinstance(prompt, dict) else {}
    repair = repair if isinstance(repair, dict) else {}
    if repair and not _kis_wiki_memory_container_matches_scope(repair):
        return {}
    return repair


def _kis_scoped_proactive_decision_pressure(prompt: dict[str, Any]) -> dict[str, Any]:
    pressure = (
        prompt.get("proactive_decision_pressure") if isinstance(prompt, dict) else {}
    )
    pressure = pressure if isinstance(pressure, dict) else {}
    if pressure and not _kis_wiki_memory_container_matches_scope(pressure):
        return {}
    return pressure


def _kis_scoped_jue_wiki_action_pressure_contract(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    contract = (
        prompt.get("jue_wiki_action_pressure_contract")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if contract and not _kis_wiki_memory_container_matches_scope(contract):
        return {}
    return contract


def _kis_requested_symbol_coverage_has_explicit_scope(coverage: dict[str, Any]) -> bool:
    return any(
        coverage.get(key) not in (None, "", [], {})
        for key in ("target_scope", "memory_scope", "scope", "venue", "market")
    )


def _kis_requested_symbol_coverage_symbols(coverage: dict[str, Any]) -> list[str]:
    symbols: list[str] = []

    def add(value: Any) -> None:
        raw = _clean_text(value, limit=80).upper()
        if not raw:
            return
        token = _compact_requested_symbol_token(raw)
        clean = token or raw[:24]
        if clean and clean not in symbols:
            symbols.append(clean)

    for key in (
        "missing_summary_symbols",
        "unsummarized_symbols",
        "prompt_omitted_symbols",
        "degraded_summary_symbols",
    ):
        for item in _normalize_list(coverage.get(key)):
            add(item)
    for item in _normalize_list(coverage.get("degraded_summary_reasons")):
        if isinstance(item, dict):
            add(item.get("symbol"))
    return symbols


def _kis_requested_symbol_coverage_matches_scope(coverage: dict[str, Any]) -> bool:
    if not _kis_wiki_memory_container_matches_scope(coverage):
        return False
    if (
        _kis_requested_symbol_coverage_has_explicit_scope(coverage)
        or _kis_wiki_memory_transferability_is_translated(coverage)
    ):
        return True
    symbols = _kis_requested_symbol_coverage_symbols(coverage)
    if not symbols:
        return True
    return all(_is_symbol(symbol) for symbol in symbols)


def _kis_scoped_requested_symbol_coverage(prompt: dict[str, Any]) -> dict[str, Any]:
    coverage = (
        prompt.get("jue_wiki_requested_symbol_coverage")
        if isinstance(prompt, dict)
        else {}
    )
    coverage = coverage if isinstance(coverage, dict) else {}
    if coverage and _kis_requested_symbol_coverage_matches_scope(coverage):
        return coverage
    jue_wiki = prompt.get("jue_wiki") if isinstance(prompt, dict) else {}
    jue_wiki = jue_wiki if isinstance(jue_wiki, dict) else {}
    nested = jue_wiki.get("requested_symbol_coverage")
    nested = nested if isinstance(nested, dict) else {}
    if not nested:
        return {}
    scoped = dict(nested)
    if not any(
        scoped.get(key) not in (None, "", [], {})
        for key in ("target_scope", "memory_scope", "scope", "venue", "market")
    ):
        parent_scope = str(jue_wiki.get("target_scope") or "").strip()
        if parent_scope:
            scoped["target_scope"] = parent_scope
    if not _kis_requested_symbol_coverage_matches_scope(scoped):
        return {}
    return scoped


def _kis_scoped_jue_wiki_validation_repair_contract(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    contract = (
        prompt.get("jue_wiki_validation_repair_contract")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if contract and not _kis_wiki_memory_container_matches_scope(contract):
        return {}
    return contract


def _kis_scoped_jue_wiki_contract_feedback_gap(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    feedback_gap = (
        prompt.get("jue_wiki_contract_feedback_gap") if isinstance(prompt, dict) else {}
    )
    feedback_gap = feedback_gap if isinstance(feedback_gap, dict) else {}
    if feedback_gap and not _kis_wiki_memory_container_matches_scope(feedback_gap):
        return {}
    return feedback_gap


def _kis_prompt_has_wiki_action_reference_memory(prompt: dict[str, Any]) -> bool:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    status = str(reference_memory.get("status") or "").strip().lower()
    if not _kis_wiki_memory_container_matches_scope(reference_memory):
        return False
    translated = _kis_wiki_memory_transferability_is_translated(reference_memory)
    items = _normalize_list(reference_memory.get("items"))
    return status == "available" and any(
        isinstance(row, dict)
        and _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        )
        for row in items
    )


def _kis_wiki_action_reference_recovery_diagnostics(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    recovery = (
        memory.get("jue_wiki_action_reference_recovery")
        if isinstance(memory.get("jue_wiki_action_reference_recovery"), dict)
        else {}
    )
    if recovery and not _kis_wiki_memory_container_matches_scope(recovery):
        recovery = {}
    if not recovery:
        reference_memory = (
            memory.get("jue_wiki_action_reference_memory")
            if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
            else {}
        )
        if not _kis_wiki_memory_container_matches_scope(reference_memory):
            return {}
        translated = _kis_wiki_memory_transferability_is_translated(reference_memory)
        has_recovery_guidance = any(
            "unresolved_recovery" in _json_dumps(row).lower()
            or "resolve_action_reference_recovery" in _json_dumps(row).lower()
            for row in _normalize_list(reference_memory.get("items"))
            if isinstance(row, dict)
            and _kis_wiki_memory_item_matches_scope(
                row,
                inherited_translated=translated,
            )
        )
        if not has_recovery_guidance:
            return {}
        return {
            "jue_wiki_action_reference_recovery_status": "unresolved",
            "jue_wiki_action_reference_recovery_memory_scope": "kis",
            "jue_wiki_action_reference_recovery_open_gap_count": 1,
            "jue_wiki_action_reference_recovery_resolved_count": 0,
            "jue_wiki_action_reference_recovery_total_count": 1,
            "jue_wiki_action_reference_recovery_ratio": 0.0,
            "jue_wiki_action_reference_recovery_latest_resolution_status": (
                "unresolved"
            ),
            "jue_wiki_action_reference_recovery_latest_status": "missing",
        }
    return {
        "jue_wiki_action_reference_recovery_status": str(
            recovery.get("status") or ""
        ).strip(),
        "jue_wiki_action_reference_recovery_memory_scope": str(
            recovery.get("memory_scope") or ""
        ).strip(),
        "jue_wiki_action_reference_recovery_open_gap_count": _safe_int(
            recovery.get("open_gap_count")
        ),
        "jue_wiki_action_reference_recovery_resolved_count": _safe_int(
            recovery.get("resolved_count")
        ),
        "jue_wiki_action_reference_recovery_total_count": _safe_int(
            recovery.get("total_count")
        ),
        "jue_wiki_action_reference_recovery_ratio": round(
            _safe_float(recovery.get("recovery_ratio")),
            4,
        ),
        "jue_wiki_action_reference_recovery_latest_resolution_status": str(
            recovery.get("latest_resolution_status") or ""
        ).strip(),
        "jue_wiki_action_reference_recovery_latest_status": str(
            recovery.get("latest_status") or ""
        ).strip(),
    }


def _kis_prompt_has_wiki_action_reference_recovery_guidance(
    prompt: dict[str, Any],
) -> bool:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    recovery = (
        memory.get("jue_wiki_action_reference_recovery")
        if isinstance(memory.get("jue_wiki_action_reference_recovery"), dict)
        else {}
    )
    if recovery and _kis_wiki_memory_container_matches_scope(recovery):
        return True
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    if not _kis_wiki_memory_container_matches_scope(reference_memory):
        return False
    translated = _kis_wiki_memory_transferability_is_translated(reference_memory)
    for row in _normalize_list(reference_memory.get("items")):
        if not isinstance(row, dict) or not _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        ):
            continue
        text = _json_dumps(row).lower()
        if (
            "unresolved_recovery" in text
            or "resolve_action_reference_recovery" in text
            or "jue_wiki_action_reference_recovery" in text
        ):
            return True
    return False


def _kis_resolved_wiki_action_reference_recovery_diagnostics(
    recovery: dict[str, Any],
    *,
    resolution_status: str,
) -> dict[str, Any]:
    result = dict(recovery)
    total_count = max(
        _safe_int(result.get("jue_wiki_action_reference_recovery_total_count")),
        1,
    )
    resolved_count = max(
        _safe_int(result.get("jue_wiki_action_reference_recovery_resolved_count")),
        total_count,
    )
    result.update(
        {
            "jue_wiki_action_reference_recovery_status": "resolved",
            "jue_wiki_action_reference_recovery_open_gap_count": 0,
            "jue_wiki_action_reference_recovery_resolved_count": resolved_count,
            "jue_wiki_action_reference_recovery_total_count": total_count,
            "jue_wiki_action_reference_recovery_ratio": 1.0,
            "jue_wiki_action_reference_recovery_latest_resolution_status": (
                resolution_status
            ),
            "jue_wiki_action_reference_recovery_latest_status": (
                "referenced" if resolution_status == "action_metadata" else "no_actions"
            ),
        }
    )
    return result


def _kis_wiki_action_reference_terms(prompt: dict[str, Any]) -> list[str]:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _kis_wiki_memory_container_matches_scope(reference_memory):
        return terms
    translated = _kis_wiki_memory_transferability_is_translated(reference_memory)

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    for row in _normalize_list(reference_memory.get("items")):
        if not isinstance(row, dict):
            continue
        if not _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        ):
            continue
        for key in ("policy_id", "latest_status"):
            add(row.get(key))
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        for key in ("status", "manager_instruction", "required_evidence"):
            value = guidance.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
            else:
                add(value)
    for term in (
        "fresh_jue_wiki_context",
        "jue_wiki_freshness_cross_check",
        "jue_wiki_selection_resolution",
        "jue_wiki_reference_basis",
        "live_cross_check",
        "research_spine",
        "valuation",
        "quote",
    ):
        add(term)
    return terms


def _kis_wiki_action_reference_translation_terms(prompt: dict[str, Any]) -> list[str]:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _kis_wiki_memory_container_matches_scope(reference_memory):
        return terms
    inherited_translated = _kis_wiki_memory_transferability_is_translated(
        reference_memory
    )

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    has_translated_memory = False
    for row in _normalize_list(reference_memory.get("items")):
        if not isinstance(row, dict):
            continue
        row_translated = (
            inherited_translated
            or _kis_wiki_memory_transferability_is_translated(row)
        )
        if not row_translated or not _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=inherited_translated,
        ):
            continue
        has_translated_memory = True
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        for value in _normalize_list(guidance.get("required_evidence")):
            add(value)
    if has_translated_memory:
        for term in (
            "translated_kr_equity_mapping",
            "translated_kis_mapping",
            "kr_equity_translation_mapping",
            "cross_venue_mapping",
            "cross_scope_mapping",
            "translated_policy_context",
        ):
            add(term)
    return terms


def _kis_payload_resolves_wiki_action_reference_terms(
    *,
    payload: Any,
    terms: list[str],
    translation_terms: list[str],
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    action_symbols: set[str] | None = None,
    require_selected_symbol_page_reference: bool = False,
) -> bool:
    if not _kis_payload_uses_only_allowed_wiki_page_ids(
        payload,
        allowed_page_ids=allowed_page_ids,
    ):
        return False
    if require_selected_symbol_page_reference and not (
        _kis_payload_has_selected_symbol_wiki_page_reference(
            payload,
            required_symbol_page_ids=required_symbol_page_ids,
        )
    ):
        return False
    if not _kis_payload_wiki_page_ids_match_action_symbols(
        payload,
        action_symbols=action_symbols,
        required_symbol_page_ids=required_symbol_page_ids,
    ):
        return False
    if terms and not _kis_payload_mentions_any_term(payload, terms):
        return False
    if translation_terms and not _kis_payload_mentions_any_term(
        payload,
        translation_terms,
    ):
        return False
    return True


def _kis_wiki_selection_guidance_terms(prompt: dict[str, Any]) -> list[str]:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    selection_memory = (
        memory.get("jue_wiki_selection_memory")
        if isinstance(memory.get("jue_wiki_selection_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _kis_wiki_memory_container_matches_scope(selection_memory):
        return terms
    translated = _kis_wiki_memory_transferability_is_translated(selection_memory)

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    for row in _normalize_list(selection_memory.get("items")):
        if not isinstance(row, dict):
            continue
        if not _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        ):
            continue
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        if str(guidance.get("status") or "").strip().lower() != (
            "freshness_repair_required"
        ):
            continue
        for key in ("policy_id", "primary_reason", "selected_page_ids"):
            value = row.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
            else:
                add(value)
        for key in (
            "status",
            "manager_instruction",
            "required_evidence",
            "cross_check_page_ids",
        ):
            value = guidance.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
            else:
                add(value)
    return terms


def _kis_wiki_selection_guidance_translation_terms(prompt: dict[str, Any]) -> list[str]:
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    selection_memory = (
        memory.get("jue_wiki_selection_memory")
        if isinstance(memory.get("jue_wiki_selection_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _kis_wiki_memory_container_matches_scope(selection_memory):
        return terms
    inherited_translated = _kis_wiki_memory_transferability_is_translated(
        selection_memory
    )

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    has_translated_memory = False
    for row in _normalize_list(selection_memory.get("items")):
        if not isinstance(row, dict):
            continue
        row_translated = (
            inherited_translated
            or _kis_wiki_memory_transferability_is_translated(row)
        )
        if not row_translated or not _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=inherited_translated,
        ):
            continue
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        if str(guidance.get("status") or "").strip().lower() != (
            "freshness_repair_required"
        ):
            continue
        has_translated_memory = True
        for value in _normalize_list(guidance.get("required_evidence")):
            text = str(value or "").strip().lower()
            if any(marker in text for marker in ("translat", "mapping", "cross_")):
                add(value)
    if has_translated_memory:
        for term in (
            "translated_kr_equity_mapping",
            "translated_kis_mapping",
            "kr_equity_translation_mapping",
            "cross_venue_mapping",
            "cross_scope_mapping",
            "translated_policy_context",
        ):
            add(term)
    return terms


def _kis_payload_resolves_wiki_selection_guidance_terms(
    *,
    payload: Any,
    terms: list[str],
    translation_terms: list[str],
) -> bool:
    if _kis_wiki_selection_guidance_note_is_negative(payload):
        return False
    if terms and not _kis_payload_mentions_any_term(payload, terms):
        return False
    if translation_terms and not _kis_payload_mentions_any_term(
        payload,
        translation_terms,
    ):
        return False
    return True


def _kis_wiki_selection_guidance_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _kis_wiki_selection_guidance_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_kis_wiki_selection_guidance_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "selection unresolved",
        "selection guidance unresolved",
        "selection resolution missing",
        "selection audit resolution missing",
        "fresh jue wiki context missing",
        "fresh wiki context missing",
        "fresh context missing",
        "wiki freshness missing",
        "no fresh jue wiki context",
        "no fresh wiki context",
        "no fresh context",
        "no live cross check",
        "no live cross checks",
        "without live cross check",
        "without live cross checks",
        "live cross check missing",
        "live cross checks missing",
        "not cross checked",
        "not checked",
        "미해결",
        "미확인",
        "신선도 미확인",
        "위키 신선도 미확인",
        "교차확인 없음",
        "교차 확인 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_prompt_has_requested_symbol_coverage_gap(prompt: dict[str, Any]) -> bool:
    coverage = _kis_scoped_requested_symbol_coverage(prompt)
    status = str(coverage.get("status") or "").strip().lower()
    if status not in {"partial", "none"}:
        return False
    if _normalize_list(coverage.get("missing_summary_symbols")):
        return True
    if "missing_summary_symbols" in coverage:
        return False
    return bool(_normalize_list(coverage.get("unsummarized_symbols")))


def _kis_prompt_has_memory_card_quality_gap(prompt: dict[str, Any]) -> bool:
    quality = _kis_scoped_memory_card_quality(prompt)
    action_plan = (
        quality.get("action_plan") if isinstance(quality.get("action_plan"), dict) else {}
    )
    if str(action_plan.get("status") or "").strip().lower() == "active":
        return bool(
            _normalize_list(action_plan.get("symbols"))
            or str(action_plan.get("required_action") or "").strip()
        )
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    if _normalize_list(summary.get("weak_symbols")):
        return True
    status_counts = (
        summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
    )
    if _safe_int(status_counts.get("weak")) > 0:
        return True
    return _kis_memory_card_quality_gap_summary_is_active(
        _kis_repair_contract_memory_card_quality_gap_summary(prompt)
    )


def _kis_repair_contract_memory_card_quality_gap_summary(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    repair_contract = _kis_scoped_jue_wiki_repair_contract(prompt)
    loop = repair_contract.get("repair_loop_effectiveness")
    loop = loop if isinstance(loop, dict) else {}
    if loop and not _kis_wiki_memory_container_matches_scope(loop):
        return {}
    gap_summary = loop.get("memory_card_quality_gap_summary")
    gap_summary = gap_summary if isinstance(gap_summary, dict) else {}
    if gap_summary and not _kis_wiki_memory_container_matches_scope(gap_summary):
        return {}
    return gap_summary


def _kis_memory_card_quality_gap_summary_is_active(
    gap_summary: dict[str, Any],
) -> bool:
    if not isinstance(gap_summary, dict) or not gap_summary:
        return False
    status = str(gap_summary.get("status") or "").strip().lower()
    if status in {"repair_required", "warning", "degraded"}:
        return True
    if _normalize_list(gap_summary.get("priority_missing_fields")):
        return True
    if _normalize_list(gap_summary.get("priority_required_checks")):
        return True
    if _kis_memory_card_quality_focus_is_active(gap_summary.get("priority_focus")):
        return True
    for key in (
        "missing_field_missed_counts",
        "required_check_missed_counts",
    ):
        counts = gap_summary.get(key) if isinstance(gap_summary.get(key), dict) else {}
        if any(_safe_int(value) > 0 for value in counts.values()):
            return True
    for key in ("top_missing_fields", "top_required_checks"):
        for row in _normalize_list(gap_summary.get(key)):
            if isinstance(row, dict) and _safe_int(row.get("missed_count")) > 0:
                return True
    return False


def _kis_memory_card_quality_focus_is_active(value: Any) -> bool:
    focus = value if isinstance(value, dict) else {}
    if not focus:
        return False
    if not (
        str(focus.get("missing_field") or "").strip()
        or str(focus.get("required_check") or "").strip()
    ):
        return False
    count_keys = (
        "missing_field_missed_count",
        "required_check_missed_count",
    )
    present_counts = [
        _safe_int(focus.get(key))
        for key in count_keys
        if focus.get(key) not in (None, "", [], {})
    ]
    if present_counts:
        return any(count > 0 for count in present_counts)
    return True


def _kis_memory_card_quality_gap_summary_required_terms(
    gap_summary: dict[str, Any],
) -> tuple[list[str], bool]:
    terms: list[str] = []
    priority_terms: list[str] = []
    missed_terms: list[str] = []

    def add(target: list[str], value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in target:
            target.append(text)

    for field in _normalize_list(gap_summary.get("priority_missing_fields"))[:1]:
        add(priority_terms, field)
    for check in _normalize_list(gap_summary.get("priority_required_checks"))[:1]:
        add(priority_terms, check)
    if priority_terms:
        return priority_terms, True

    focus = (
        gap_summary.get("priority_focus")
        if isinstance(gap_summary.get("priority_focus"), dict)
        else {}
    )
    add(priority_terms, focus.get("missing_field"))
    add(priority_terms, focus.get("required_check"))
    if priority_terms:
        return priority_terms, True

    for counts_key in (
        "missing_field_counts",
        "missing_field_missed_counts",
        "required_check_counts",
        "required_check_missed_counts",
    ):
        counts = (
            gap_summary.get(counts_key)
            if isinstance(gap_summary.get(counts_key), dict)
            else {}
        )
        for field, count in counts.items():
            if counts_key.endswith("_missed_counts") and _safe_int(count) > 0:
                add(missed_terms, field)
            add(terms, field)
    for row in _normalize_list(gap_summary.get("top_missing_fields")):
        if not isinstance(row, dict):
            continue
        field = row.get("field")
        if _safe_int(row.get("missed_count")) > 0:
            add(missed_terms, field)
        add(terms, field)
    for row in _normalize_list(gap_summary.get("top_required_checks")):
        if not isinstance(row, dict):
            continue
        check = row.get("check")
        if _safe_int(row.get("missed_count")) > 0:
            add(missed_terms, check)
        add(terms, check)
    if missed_terms:
        return missed_terms, True
    return terms, False


def _kis_prompt_has_action_pressure(prompt: dict[str, Any]) -> bool:
    pressure = _kis_scoped_proactive_decision_pressure(prompt)
    return str(pressure.get("status") or "").strip().lower() == "action_required"


def _kis_prompt_has_wiki_action_pressure(prompt: dict[str, Any]) -> bool:
    contract = _kis_scoped_jue_wiki_action_pressure_contract(prompt)
    if str(contract.get("status") or "").strip().lower() == "active":
        return True
    return bool(_normalize_list(contract.get("page_ids")))


def _kis_execution_gate_blocks_contract(prompt: dict[str, Any]) -> bool:
    gate = prompt.get("execution_gate") if isinstance(prompt, dict) else {}
    gate = gate if isinstance(gate, dict) else {}
    if str(gate.get("status") or "").strip().lower() in {
        "blocked",
        "disabled",
        "error",
        "halted",
    }:
        return True
    kill = gate.get("kill_switch") if isinstance(gate.get("kill_switch"), dict) else {}
    if bool(kill.get("enabled")):
        return True
    if gate.get("new_entry_allowed_by_session") is False:
        return True
    return False


def _kis_response_has_concrete_repair_resolution(
    response: dict[str, Any],
    *,
    target_symbols: set[str] | None = None,
) -> bool:
    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response, dict)
        else {}
    )
    resolution = resolution if isinstance(resolution, dict) else {}
    accepted = {
        "small_waiting_block",
        "one_share_probe",
        "probe_waiting_block",
        "updated_price_geometry",
        "candidate_rejected",
        "safety_gate_defer",
    }

    def negative_repair_row(value: Any) -> bool:
        if isinstance(value, dict):
            return any(negative_repair_row(child) for child in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(negative_repair_row(child) for child in value)
        text = str(value or "").strip().lower()
        if not text:
            return False
        compact = (
            _strip_model_missing_error_tokens(text)
            .replace("_", " ")
            .replace("-", " ")
            .replace("/", " ")
            .replace(";", " ")
            .replace(",", " ")
        )
        negative_phrases = (
            "validation repair not resolved",
            "repair not resolved",
            "repair unresolved",
            "resolution missing",
            "repair missing",
            "no concrete next trigger",
            "no concrete trigger",
            "trigger unavailable",
            "next trigger unavailable",
            "failed to repair",
            "cannot repair",
            "수리 미해결",
            "해결 못함",
        )
        return any(phrase in compact for phrase in negative_phrases)

    for row in _normalize_list(resolution.get("resolved_candidates")):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("resolution") or "").strip().lower()
        if kind not in accepted:
            continue
        if negative_repair_row(row):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            continue
        if target_symbols and symbol not in target_symbols:
            continue
        next_trigger = _clean_text(row.get("next_trigger"), limit=300)
        evidence_gap = _clean_text(row.get("evidence_gap"), limit=300)
        if kind in {"candidate_rejected", "safety_gate_defer"}:
            if evidence_gap or next_trigger:
                return True
            continue
        if next_trigger:
            return True
    return False


def _kis_hold_has_concrete_next_step(hold_decision: dict[str, Any]) -> bool:
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    for row in _normalize_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        if _is_symbol(row.get("symbol")) and _clean_text(
            row.get("condition") or row.get("reason"),
            limit=300,
        ):
            return True
        if _safe_float(row.get("price")) > 0 and _clean_text(
            row.get("condition"),
            limit=300,
        ):
            return True
    if _normalize_list(hold.get("data_gaps")) and _normalize_list(
        hold.get("watch_symbols")
    ):
        return True
    return False


def _kis_hold_identity_symbols(hold_decision: dict[str, Any]) -> set[str]:
    hold = hold_decision if isinstance(hold_decision, dict) else {}

    def extract(value: Any) -> set[str]:
        values = value if isinstance(value, list) else [value]
        symbols: set[str] = set()
        for item in values:
            symbol = str(item or "").strip().upper()
            if _is_symbol(symbol):
                symbols.add(symbol)
        return symbols

    symbols: set[str] = (
        extract(hold.get("symbol"))
        | extract(hold.get("symbols"))
        | extract(hold.get("watch_symbols"))
        | extract(hold.get("long_watch_symbols"))
    )
    for row in _normalize_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        symbols.update(
            extract(row.get("symbol"))
            | extract(row.get("code"))
            | extract(row.get("ticker"))
            | extract(row.get("symbols"))
        )
    return symbols


def _kis_memory_card_quality_required_terms(prompt: dict[str, Any]) -> list[str]:
    quality = _kis_scoped_memory_card_quality(prompt)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    action_plan = (
        quality.get("action_plan") if isinstance(quality.get("action_plan"), dict) else {}
    )
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for field in _normalize_list(action_plan.get("required_checks")):
        add(field)
    for row in _normalize_list(action_plan.get("missing_fields_by_symbol")):
        if isinstance(row, dict):
            for field in _normalize_list(row.get("missing_fields")):
                add(field)
            for field in _normalize_list(row.get("required_checks")):
                add(field)
    field_counts = (
        summary.get("missing_field_counts")
        if isinstance(summary.get("missing_field_counts"), dict)
        else {}
    )
    for field in field_counts:
        add(field)
    for row in _normalize_list(summary.get("missing_fields_by_symbol")):
        if isinstance(row, dict):
            for field in _normalize_list(row.get("missing_fields")):
                add(field)
            for field in _normalize_list(row.get("required_checks")):
                add(field)
    for row in _normalize_list(summary.get("rows")):
        if isinstance(row, dict):
            for field in _normalize_list(row.get("missing_fields")):
                add(field)
            for field in _normalize_list(row.get("required_checks")):
                add(field)
    gap_summary = _kis_repair_contract_memory_card_quality_gap_summary(prompt)
    gap_terms, has_missed_terms = _kis_memory_card_quality_gap_summary_required_terms(
        gap_summary
    )
    if has_missed_terms:
        return gap_terms
    for field in gap_terms:
        add(field)
    return terms


def _kis_memory_card_quality_target_symbols(prompt: dict[str, Any]) -> set[str]:
    quality = _kis_scoped_memory_card_quality(prompt)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    action_plan = (
        quality.get("action_plan") if isinstance(quality.get("action_plan"), dict) else {}
    )
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            symbol = str(item or "").strip().upper()
            if _is_symbol(symbol):
                symbols.add(symbol)

    add(summary.get("weak_symbols"))
    add(action_plan.get("symbols"))
    for source in (summary, action_plan):
        for row in _normalize_list(source.get("missing_fields_by_symbol")):
            if isinstance(row, dict):
                add(row.get("symbol"))
        for row in _normalize_list(source.get("rows")):
            if isinstance(row, dict):
                add(row.get("symbol"))
    return symbols


def _kis_action_identity_symbols(row: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            symbol = str(item or "").strip().upper()
            if _is_symbol(symbol):
                symbols.add(symbol)

    add(row.get("symbol"))
    add(row.get("code"))
    add(row.get("ticker"))
    add(row.get("symbols"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    add(metadata.get("symbol"))
    add(metadata.get("code"))
    add(metadata.get("ticker"))
    add(metadata.get("symbols"))
    return symbols


def _kis_action_identity_block_ids(row: dict[str, Any]) -> set[str]:
    block_ids: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip().lower()
            if text:
                block_ids.add(text)

    add(row.get("block_id"))
    add(row.get("id"))
    add(row.get("block_ids"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    add(metadata.get("block_id"))
    add(metadata.get("id"))
    add(metadata.get("block_ids"))
    return block_ids


def _kis_action_identity_symbols_with_block_map(
    row: dict[str, Any],
    *,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> set[str]:
    symbols = set(_kis_action_identity_symbols(row))
    if not block_symbol_map:
        return symbols
    for block_id in _kis_action_identity_block_ids(row):
        symbols.update(block_symbol_map.get(block_id, set()))
    return symbols


def _kis_hold_has_concrete_next_step_for_symbols(
    hold_decision: dict[str, Any],
    target_symbols: set[str],
) -> bool:
    if not target_symbols:
        return _kis_hold_has_concrete_next_step(hold_decision)
    hold = hold_decision if isinstance(hold_decision, dict) else {}

    def extract(value: Any) -> set[str]:
        values = value if isinstance(value, list) else [value]
        symbols: set[str] = set()
        for item in values:
            symbol = str(item or "").strip().upper()
            if _is_symbol(symbol):
                symbols.add(symbol)
        return symbols

    for row in _normalize_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        row_symbols = (
            extract(row.get("symbol"))
            | extract(row.get("code"))
            | extract(row.get("ticker"))
            | extract(row.get("symbols"))
        )
        if row_symbols.isdisjoint(target_symbols):
            continue
        if str(row.get("condition") or row.get("reason") or "").strip():
            return True
        if _safe_float(row.get("price")) > 0 and str(
            row.get("condition") or ""
        ).strip():
            return True
    watch_symbols = extract(hold.get("watch_symbols"))
    return bool(
        watch_symbols.intersection(target_symbols)
        and _normalize_list(hold.get("data_gaps"))
    )


def _kis_memory_card_quality_action_has_specific_evidence(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _kis_memory_card_quality_required_terms(prompt)
    target_symbols = _kis_memory_card_quality_target_symbols(prompt)
    for key in ("create_blocks", "update_blocks", "close_blocks", "pause_blocks"):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            if target_symbols and _kis_action_identity_symbols(row).isdisjoint(
                target_symbols
            ):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_memory_card_quality",
                "jue_wiki_memory_card_cross_check",
            ):
                repair_note = metadata.get(metadata_key)
                if repair_note in (None, "", [], {}):
                    repair_note = row.get(metadata_key)
                if _kis_repair_note_is_negative(repair_note):
                    continue
                if terms:
                    if _kis_payload_mentions_any_term(repair_note, terms):
                        return True
                elif _kis_repair_note_is_concrete(repair_note):
                    return True
    return False


def _kis_memory_card_quality_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _kis_memory_card_quality_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_kis_memory_card_quality_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        _strip_model_missing_error_tokens(text)
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "memory card quality not resolved",
        "memory card quality unresolved",
        "memory card quality unavailable",
        "quality evidence unavailable",
        "no fresh memory card quality evidence",
        "no memory card quality evidence",
        "not refreshed",
        "not cross checked",
        "without quality evidence",
        "품질 미해결",
        "품질 근거 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_wiki_repair_reference_terms(prompt: dict[str, Any]) -> list[str]:
    repair_contract = _kis_scoped_jue_wiki_repair_contract(prompt)
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 2 and text not in terms:
            terms.append(text)

    def add_values(source: dict[str, Any], keys: tuple[str, ...]) -> None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, dict):
                for child_key in value:
                    add(child_key)
                continue
            for item in _normalize_list(value):
                add(item)
            if value not in (None, "", [], {}):
                add(value)

    for row in _normalize_list(repair_contract.get("top_priorities")):
        if not isinstance(row, dict):
            continue
        add_values(
            row,
            (
                "source_id",
                "page_id",
                "symbol",
                "symbols",
                "action_type",
                "priority_type",
                "type",
                "warning",
                "warnings",
                "quality_warning",
                "quality_warnings",
            ),
        )

    for row in _normalize_list(repair_contract.get("action_batches")):
        if not isinstance(row, dict):
            continue
        add_values(
            row,
            (
                "source_id",
                "page_id",
                "symbol",
                "symbols",
                "action_type",
                "warning",
                "warnings",
                "quality_warning",
                "quality_warnings",
                "warning_counts",
            ),
        )

    action_plan = repair_contract.get("repair_pressure_action_plan")
    action_plan = action_plan if isinstance(action_plan, dict) else {}
    for key in (
        "action_batch_type_counts",
        "action_batch_warning_counts",
        "omitted_priority_type_counts",
    ):
        counts = action_plan.get(key) if isinstance(action_plan.get(key), dict) else {}
        for term in counts:
            add(term)
    for row in _kis_degraded_wiki_effectiveness_items(prompt):
        add_values(
            row,
            (
                "page_id",
                "symbol",
                "symbols",
                "warning",
                "warnings",
                "quality_warning",
                "quality_warnings",
                "effectiveness_reasons",
            ),
        )
    return terms


def _kis_payload_mentions_any_term(value: Any, terms: list[str]) -> bool:
    if not terms:
        return False
    text = _json_dumps(value).lower()
    text_spaced = text.replace("_", " ")
    for term in terms:
        if term in text or term.replace("_", " ") in text_spaced:
            return True
    return False


def _kis_candidate_memory_hint_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(prompt, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("candidates", "candidate_memory_hints"):
        for row in _normalize_list(prompt.get(key)):
            if isinstance(row, dict):
                rows.append(row)
    strategy = prompt.get("strategy") if isinstance(prompt.get("strategy"), dict) else {}
    for key in ("top_symbols", "candidates"):
        for row in _normalize_list(strategy.get(key)):
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _kis_candidate_memory_hint_items(
    prompt: dict[str, Any],
) -> list[tuple[set[str], list[str]]]:
    policy = (
        prompt.get("candidate_memory_hint_policy")
        if isinstance(prompt.get("candidate_memory_hint_policy"), dict)
        else {}
    )
    rows = _kis_candidate_memory_hint_rows(prompt)
    if not bool(policy.get("required")) and not any(
        isinstance(row.get("memory_hint"), dict) for row in rows
    ):
        return []

    def add_term(terms: list[str], value: Any) -> None:
        text = _clean_text(value, limit=180).lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    items: list[tuple[set[str], list[str]]] = []
    for row in rows:
        hint = row.get("memory_hint") if isinstance(row.get("memory_hint"), dict) else {}
        if not hint:
            continue
        terms: list[str] = []
        for key in ("reasons", "risks", "checks"):
            for item in _normalize_list(hint.get(key)):
                add_term(terms, item)
        for item in _normalize_list(hint.get("sources")):
            add_term(terms, item)
        if terms:
            items.append((_kis_action_identity_symbols(row), terms))
    return items


def _kis_payload_mentions_any_symbol(value: Any, symbols: set[str]) -> bool:
    if not symbols:
        return True
    text = _json_dumps(value).upper()
    return any(symbol in text for symbol in symbols)


def _kis_candidate_memory_hint_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _kis_candidate_memory_hint_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_kis_candidate_memory_hint_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "candidate memory hint not applied",
        "memory hint not applied",
        "memory hint unresolved",
        "memory hint unavailable",
        "research spine memory not applied",
        "research spine memory unresolved",
        "research spine memory unavailable",
        "memory context unavailable",
        "fresh context absent",
        "no fresh memory",
        "no memory context",
        "without memory context",
        "memory missing",
        "not applied",
        "적용 못함",
        "메모리 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_actions_resolve_candidate_memory_hint(
    actions: dict[str, Any],
    *,
    symbols: set[str],
    terms: list[str],
) -> bool:
    if not isinstance(actions, dict):
        return False
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            row_symbols = _kis_action_identity_symbols(row)
            if symbols and row_symbols and row_symbols.isdisjoint(symbols):
                continue
            if symbols and not row_symbols and not _kis_payload_mentions_any_symbol(
                row, symbols
            ):
                continue
            if _kis_candidate_memory_hint_note_is_negative(row):
                continue
            if _kis_payload_mentions_any_term(row, terms):
                return True
    return False


def _kis_payload_resolves_candidate_memory_hint(
    value: Any,
    *,
    symbols: set[str],
    terms: list[str],
) -> bool:
    if _kis_candidate_memory_hint_note_is_negative(value):
        return False
    return _kis_payload_mentions_any_term(
        value, terms
    ) and _kis_payload_mentions_any_symbol(value, symbols)


def _kis_candidate_memory_hint_resolution_missing(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    items = _kis_candidate_memory_hint_items(prompt)
    if not items:
        return False
    for symbols, terms in items:
        if (
            _kis_actions_resolve_candidate_memory_hint(
                actions,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                response,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                hold_decision,
                symbols=symbols,
                terms=terms,
            )
        ):
            continue
        return True
    return False


def _kis_candidate_memory_hint_resolution_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    items = _kis_candidate_memory_hint_items(prompt)
    if not items:
        return {
            "candidate_memory_hint_status": "inactive",
            "candidate_memory_hint_count": 0,
            "candidate_memory_hint_resolved_count": 0,
            "candidate_memory_hint_unresolved_count": 0,
            "candidate_memory_hint_missing_symbols": [],
        }
    resolved_count = 0
    missing_symbols: list[str] = []
    for symbols, terms in items:
        resolved = (
            _kis_actions_resolve_candidate_memory_hint(
                actions,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                response,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                hold_decision,
                symbols=symbols,
                terms=terms,
            )
        )
        if resolved:
            resolved_count += 1
            continue
        for symbol in sorted(symbols):
            if symbol not in missing_symbols:
                missing_symbols.append(symbol)
    unresolved_count = len(items) - resolved_count
    status = (
        "resolved"
        if unresolved_count <= 0
        else "partial"
        if resolved_count > 0
        else "unresolved"
    )
    return {
        "candidate_memory_hint_status": status,
        "candidate_memory_hint_count": len(items),
        "candidate_memory_hint_resolved_count": resolved_count,
        "candidate_memory_hint_unresolved_count": unresolved_count,
        "candidate_memory_hint_missing_symbols": missing_symbols[:12],
    }


def _kis_research_spine_memory_items(
    prompt: dict[str, Any],
) -> list[tuple[set[str], list[str]]]:
    if not isinstance(prompt, dict):
        return []
    policy = prompt.get("research_spine_policy")
    policy = policy if isinstance(policy, dict) else {}
    memory_policy = (
        policy.get("memory_application")
        if isinstance(policy.get("memory_application"), dict)
        else {}
    )
    spine = prompt.get("research_spine") if isinstance(prompt.get("research_spine"), dict) else {}
    packets = _normalize_list(spine.get("packets"))
    memory_keys = (
        "symbol_memory",
        "symbol_analysis_memory",
    )
    has_memory_packet = any(
        isinstance(row, dict)
        and (
            any(isinstance(row.get(key), dict) for key in memory_keys)
            or "symbol_memory" in _normalize_list(row.get("buckets"))
            or "symbol_analysis_memory"
            in _normalize_list(
                row.get("evidence", {}).get("sources")
                if isinstance(row.get("evidence"), dict)
                else []
            )
        )
        for row in packets
    )
    if not bool(memory_policy.get("required")) and not has_memory_packet:
        return []

    def add(terms: list[str], value: Any) -> None:
        text = _clean_text(value, limit=220).lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    def collect(terms: list[str], value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"symbol", "name", "price", "change_pct", "qty", "quantity"}:
                    continue
                collect(terms, child)
            return
        if isinstance(value, list):
            for child in value:
                collect(terms, child)
            return
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            add(terms, value)

    items: list[tuple[set[str], list[str]]] = []
    for row in packets:
        if not isinstance(row, dict):
            continue
        terms: list[str] = []
        for key in memory_keys:
            source = row.get(key)
            if isinstance(source, dict):
                collect(terms, source)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        is_memory_packet = "symbol_memory" in _normalize_list(
            row.get("buckets")
        ) or "symbol_analysis_memory" in _normalize_list(evidence.get("sources"))
        if is_memory_packet:
            for key in ("reasons", "risks", "checks"):
                collect(terms, evidence.get(key))
        if terms:
            items.append((_kis_action_identity_symbols(row), terms))
    return items


def _kis_research_spine_memory_terms(prompt: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for _, item_terms in _kis_research_spine_memory_items(prompt):
        for term in item_terms:
            if term not in terms:
                terms.append(term)
    return terms


def _strip_model_missing_error_tokens(text: str) -> str:
    return re.sub(r"\b[a-z0-9_]*missing_from_model\b", " ", text)


def _kis_action_symbols_from_actions(actions: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()
    if not isinstance(actions, dict):
        return symbols
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if isinstance(row, dict):
                symbols.update(_kis_action_identity_symbols(row))
    return symbols


def _kis_research_spine_memory_resolution_missing(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    items = _kis_research_spine_memory_items(prompt)
    if not items:
        return False
    action_symbols = _kis_action_symbols_from_actions(actions)
    relevant_items = [
        (symbols, terms)
        for symbols, terms in items
        if not symbols or not action_symbols or bool(symbols & action_symbols)
    ]
    for symbols, terms in relevant_items:
        if (
            _kis_actions_resolve_candidate_memory_hint(
                actions,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                response,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                hold_decision,
                symbols=symbols,
                terms=terms,
            )
        ):
            continue
        return True
    return False


def _kis_research_spine_memory_resolution_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    items = _kis_research_spine_memory_items(prompt)
    if not items:
        return {
            "research_spine_memory_status": "inactive",
            "research_spine_memory_count": 0,
            "research_spine_memory_resolved_count": 0,
            "research_spine_memory_unresolved_count": 0,
            "research_spine_memory_missing_symbols": [],
        }
    action_symbols = _kis_action_symbols_from_actions(actions)
    relevant_items = [
        (symbols, terms)
        for symbols, terms in items
        if not symbols or not action_symbols or bool(symbols & action_symbols)
    ]
    if not relevant_items:
        return {
            "research_spine_memory_status": "no_relevant_actions",
            "research_spine_memory_count": 0,
            "research_spine_memory_resolved_count": 0,
            "research_spine_memory_unresolved_count": 0,
            "research_spine_memory_missing_symbols": [],
        }
    resolved_count = 0
    missing_symbols: list[str] = []
    for symbols, terms in relevant_items:
        resolved = (
            _kis_actions_resolve_candidate_memory_hint(
                actions,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                response,
                symbols=symbols,
                terms=terms,
            )
            or _kis_payload_resolves_candidate_memory_hint(
                hold_decision,
                symbols=symbols,
                terms=terms,
            )
        )
        if resolved:
            resolved_count += 1
            continue
        for symbol in sorted(symbols):
            if symbol not in missing_symbols:
                missing_symbols.append(symbol)
    unresolved_count = len(relevant_items) - resolved_count
    status = (
        "resolved"
        if unresolved_count <= 0
        else "partial"
        if resolved_count > 0
        else "unresolved"
    )
    return {
        "research_spine_memory_status": status,
        "research_spine_memory_count": len(relevant_items),
        "research_spine_memory_resolved_count": resolved_count,
        "research_spine_memory_unresolved_count": unresolved_count,
        "research_spine_memory_missing_symbols": missing_symbols[:12],
    }


def _kis_actions_have_prompt_linked_wiki_repair_metadata(
    prompt: dict[str, Any],
    actions: dict[str, Any],
    *,
    metadata_keys: tuple[str, ...],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _kis_wiki_repair_reference_terms(prompt)
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in metadata_keys:
                repair_note = metadata.get(metadata_key)
                if repair_note in (None, "", [], {}):
                    repair_note = row.get(metadata_key)
                if _kis_repair_note_is_negative(repair_note):
                    continue
                if not _kis_repair_note_is_concrete(repair_note):
                    continue
                if not terms or _kis_payload_mentions_any_term(
                    {"repair_note": repair_note, "symbol": row.get("symbol")},
                    terms,
                ):
                    return True
    return False


def _kis_response_has_prompt_linked_repair_resolution(
    prompt: dict[str, Any],
    response: dict[str, Any],
) -> bool:
    if not _kis_response_has_concrete_repair_resolution(response):
        return False
    terms = _kis_wiki_repair_reference_terms(prompt)
    if not terms:
        return True
    return _kis_payload_mentions_any_term(
        response.get("validation_repair_resolution"),
        terms,
    )


def _kis_memory_contract_repair_terms(prompt: dict[str, Any]) -> list[str]:
    repair = _kis_scoped_validation_repair(prompt)
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    for key in ("memory_contracts", "memory_contract_errors"):
        for item in _normalize_list(repair.get(key)):
            add(item)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            add(row.get("memory_contract"))
            add(row.get("memory_contract_error"))
    return terms


def _kis_memory_contract_repair_target_symbols(prompt: dict[str, Any]) -> set[str]:
    repair = _kis_scoped_validation_repair(prompt)
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            raw = str(item or "").strip().upper()
            if not raw:
                continue
            if _is_symbol(raw):
                symbols.add(raw)
                continue
            token = _compact_requested_symbol_token(raw)
            if _is_symbol(token):
                symbols.add(token)

    for key in (
        "symbol",
        "symbols",
        "code",
        "codes",
        "ticker",
        "tickers",
        "target_symbol",
        "target_symbols",
        "impacted_symbol",
        "impacted_symbols",
        "missing_symbol",
        "missing_symbols",
    ):
        add(repair.get(key))
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            for key in (
                "symbol",
                "symbols",
                "code",
                "codes",
                "ticker",
                "tickers",
                "target_symbol",
                "target_symbols",
                "impacted_symbol",
                "impacted_symbols",
                "missing_symbol",
                "missing_symbols",
            ):
                add(row.get(key))
    return symbols


def _kis_memory_contract_repair_details_by_symbol(
    prompt: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    repair = _kis_scoped_validation_repair(prompt)
    details: dict[str, dict[str, list[str]]] = {}

    def add_text(rows: list[str], value: Any) -> None:
        text = _clean_text(value, limit=160)
        if text and text not in rows:
            rows.append(text)

    def symbols_from(row: dict[str, Any]) -> set[str]:
        symbols: set[str] = set()
        for key in (
            "symbol",
            "symbols",
            "code",
            "codes",
            "ticker",
            "tickers",
            "target_symbol",
            "target_symbols",
            "impacted_symbol",
            "impacted_symbols",
            "missing_symbol",
            "missing_symbols",
        ):
            values = row.get(key)
            values = values if isinstance(values, list) else [values]
            for item in values:
                raw = str(item or "").strip().upper()
                if _is_symbol(raw):
                    symbols.add(raw)
                    continue
                token = _compact_requested_symbol_token(raw)
                if _is_symbol(token):
                    symbols.add(token)
        return symbols

    rows: list[dict[str, Any]] = []
    if isinstance(repair, dict):
        rows.append(repair)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if isinstance(row, dict):
                rows.append(row)
    for row in rows:
        row_symbols = symbols_from(row)
        if not row_symbols:
            continue
        for symbol in row_symbols:
            detail = details.setdefault(symbol, {"contracts": [], "errors": []})
            add_text(detail["contracts"], row.get("memory_contract"))
            add_text(detail["errors"], row.get("memory_contract_error"))
            for value in _normalize_list(row.get("memory_contracts")):
                add_text(detail["contracts"], value)
            for value in _normalize_list(row.get("memory_contract_errors")):
                add_text(detail["errors"], value)
    return details


def _kis_prompt_requires_memory_contract_repair(prompt: dict[str, Any]) -> bool:
    repair = _kis_scoped_validation_repair(prompt)
    if _kis_memory_contract_repair_terms(prompt):
        return True
    for key in ("required_checks",):
        if "require_memory_contract_resolution" in {
            str(item or "").strip()
            for item in _normalize_list(repair.get(key))
        }:
            return True
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            if str(row.get("discipline_id") or "").strip() == "memory_contract":
                return True
            if "require_memory_contract_resolution" in {
                str(item or "").strip()
                for item in _normalize_list(row.get("required_checks"))
            }:
                return True
    return False


def _kis_actions_resolve_memory_contract_repair(
    *,
    actions: dict[str, Any],
    terms: list[str],
    target_symbols: set[str],
) -> bool:
    if not isinstance(actions, dict):
        return False
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            if target_symbols and _kis_action_identity_symbols(row).isdisjoint(
                target_symbols
            ):
                continue
            if _kis_memory_contract_resolution_note_is_negative(row):
                continue
            if not terms or _kis_payload_mentions_any_term(row, terms):
                return True
    return False


def _kis_memory_contract_resolution_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _kis_memory_contract_resolution_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _kis_memory_contract_resolution_note_is_negative(child) for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "memory contract not applied",
        "memory contract unresolved",
        "memory contract unavailable",
        "contract resolution missing",
        "memory contract resolution missing",
        "no fresh memory contract",
        "no memory contract",
        "without memory contract",
        "not applied",
        "반영하지 못함",
        "계약 미해결",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_memory_contract_repair_resolved(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _kis_prompt_requires_memory_contract_repair(prompt):
        return True
    terms = _kis_memory_contract_repair_terms(prompt)
    target_symbols = _kis_memory_contract_repair_target_symbols(prompt)
    if not terms:
        return (
            _kis_response_has_concrete_repair_resolution(
                response,
                target_symbols=target_symbols,
            )
            or _kis_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                target_symbols,
            )
            or _kis_actions_resolve_memory_contract_repair(
                actions=actions,
                terms=terms,
                target_symbols=target_symbols,
            )
        )
    if _kis_memory_contract_resolution_note_is_negative(
        response.get("validation_repair_resolution")
    ):
        response_mentions_contract = False
    else:
        response_mentions_contract = _kis_payload_mentions_any_term(
            response.get("validation_repair_resolution"),
            terms,
        )
    if _kis_memory_contract_resolution_note_is_negative(hold_decision):
        hold_mentions_contract = False
    else:
        hold_mentions_contract = _kis_payload_mentions_any_term(hold_decision, terms)
    action_mentions_contract = _kis_actions_resolve_memory_contract_repair(
        actions=actions,
        terms=terms,
        target_symbols=target_symbols,
    )
    mentions_contract = (
        response_mentions_contract
        or hold_mentions_contract
        or action_mentions_contract
    )
    if not mentions_contract:
        return False
    return (
        (
            response_mentions_contract
            and _kis_response_has_concrete_repair_resolution(
                response,
                target_symbols=target_symbols,
            )
        )
        or (
            hold_mentions_contract
            and _kis_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                target_symbols,
            )
        )
        or action_mentions_contract
    )


def _kis_memory_contract_repair_resolution_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    if not _kis_prompt_requires_memory_contract_repair(prompt):
        return {
            "memory_contract_status": "inactive",
            "memory_contract_count": 0,
            "memory_contract_resolved_count": 0,
            "memory_contract_unresolved_count": 0,
            "memory_contract_missing_symbols": [],
            "memory_contract_missing_contracts": [],
            "memory_contract_missing_errors": [],
            "memory_contract_resolution_modes": [],
            "memory_contract_action_resolved_count": 0,
            "memory_contract_hold_resolved_count": 0,
            "memory_contract_response_resolved_count": 0,
            "memory_contract_rows": [],
        }
    terms = _kis_memory_contract_repair_terms(prompt)
    target_symbols = sorted(_kis_memory_contract_repair_target_symbols(prompt))
    details_by_symbol = _kis_memory_contract_repair_details_by_symbol(prompt)
    validation_repair_resolution = response.get("validation_repair_resolution")
    response_resolution_negative = _kis_memory_contract_resolution_note_is_negative(
        validation_repair_resolution
    )
    hold_resolution_negative = _kis_memory_contract_resolution_note_is_negative(
        hold_decision
    )
    if not target_symbols:
        response_resolved = (
            not response_resolution_negative
            and (
                (not terms)
                or _kis_payload_mentions_any_term(validation_repair_resolution, terms)
            )
            and _kis_response_has_concrete_repair_resolution(response)
        )
        hold_resolved = (
            not hold_resolution_negative
            and (
                (not terms) or _kis_payload_mentions_any_term(hold_decision, terms)
            )
            and _kis_hold_has_concrete_next_step(hold_decision)
        )
        action_resolved = _kis_actions_resolve_memory_contract_repair(
            actions=actions,
            terms=terms,
            target_symbols=set(),
        )
        resolved = response_resolved or hold_resolved or action_resolved
        modes = []
        if action_resolved:
            modes.append("action_metadata")
        if hold_resolved:
            modes.append("hold_trigger")
        if response_resolved:
            modes.append("response_resolution")
        return {
            "memory_contract_status": "resolved" if resolved else "unresolved",
            "memory_contract_count": 1,
            "memory_contract_resolved_count": 1 if resolved else 0,
            "memory_contract_unresolved_count": 0 if resolved else 1,
            "memory_contract_missing_symbols": [],
            "memory_contract_missing_contracts": [] if resolved else terms[:12],
            "memory_contract_missing_errors": [],
            "memory_contract_resolution_modes": modes,
            "memory_contract_action_resolved_count": 1 if action_resolved else 0,
            "memory_contract_hold_resolved_count": 1 if hold_resolved else 0,
            "memory_contract_response_resolved_count": (
                1 if response_resolved else 0
            ),
            "memory_contract_rows": [],
        }

    resolved_symbols: list[str] = []
    action_resolved_count = 0
    hold_resolved_count = 0
    response_resolved_count = 0
    memory_contract_rows: list[dict[str, Any]] = []
    for symbol in target_symbols:
        symbol_set = {symbol}
        detail = details_by_symbol.get(symbol, {})
        response_resolved = (
            not response_resolution_negative
            and (
                (not terms)
                or _kis_payload_mentions_any_term(
                    validation_repair_resolution,
                    terms,
                )
            )
            and _kis_response_has_concrete_repair_resolution(
                response,
                target_symbols=symbol_set,
            )
        )
        hold_resolved = (
            not hold_resolution_negative
            and (
                (not terms) or _kis_payload_mentions_any_term(hold_decision, terms)
            )
            and _kis_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                symbol_set,
            )
        )
        action_resolved = _kis_actions_resolve_memory_contract_repair(
            actions=actions,
            terms=terms,
            target_symbols=symbol_set,
        )
        if response_resolved or hold_resolved or action_resolved:
            resolved_symbols.append(symbol)
        resolution_modes = [
            mode
            for mode, flag in (
                ("action_metadata", action_resolved),
                ("hold_trigger", hold_resolved),
                ("response_resolution", response_resolved),
            )
            if flag
        ]
        memory_contract_rows.append(
            {
                "symbol": symbol,
                "status": "resolved" if resolution_modes else "unresolved",
                "contracts": detail.get("contracts", [])[:4],
                "errors": detail.get("errors", [])[:4],
                "resolution_modes": resolution_modes,
            }
        )
        if action_resolved:
            action_resolved_count += 1
        if hold_resolved:
            hold_resolved_count += 1
        if response_resolved:
            response_resolved_count += 1
    missing_symbols = [
        symbol for symbol in target_symbols if symbol not in resolved_symbols
    ]
    missing_contracts: list[str] = []
    missing_errors: list[str] = []
    for symbol in missing_symbols:
        detail = details_by_symbol.get(symbol, {})
        for contract in detail.get("contracts", []):
            if contract not in missing_contracts:
                missing_contracts.append(contract)
        for error in detail.get("errors", []):
            if error not in missing_errors:
                missing_errors.append(error)
    status = (
        "resolved"
        if not missing_symbols
        else "partial"
        if resolved_symbols
        else "unresolved"
    )
    return {
        "memory_contract_status": status,
        "memory_contract_count": len(target_symbols),
        "memory_contract_resolved_count": len(resolved_symbols),
        "memory_contract_unresolved_count": len(missing_symbols),
        "memory_contract_missing_symbols": missing_symbols[:12],
        "memory_contract_missing_contracts": missing_contracts[:12],
        "memory_contract_missing_errors": missing_errors[:12],
        "memory_contract_resolution_modes": [
            mode
            for mode, count in (
                ("action_metadata", action_resolved_count),
                ("hold_trigger", hold_resolved_count),
                ("response_resolution", response_resolved_count),
            )
            if count > 0
        ],
        "memory_contract_action_resolved_count": action_resolved_count,
        "memory_contract_hold_resolved_count": hold_resolved_count,
        "memory_contract_response_resolved_count": response_resolved_count,
        "memory_contract_rows": memory_contract_rows[:12],
    }


def _kis_unavailable_wiki_context(prompt: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(prompt, dict):
        return {}
    candidates: list[Any] = [prompt.get("jue_wiki")]
    memory = prompt.get("investment_memory")
    if isinstance(memory, dict):
        candidates.append(memory.get("jue_wiki"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if not _kis_wiki_memory_container_matches_scope(candidate):
            continue
        status = str(candidate.get("status") or "").strip().lower()
        available = candidate.get("available")
        if status in {"error", "disabled", "unavailable"} or available is False:
            return {
                "status": status or "unavailable",
                "reason": _clean_text(candidate.get("reason"), limit=160),
            }
    return {}


def _kis_prompt_has_unavailable_wiki_context(prompt: dict[str, Any]) -> bool:
    return bool(_kis_unavailable_wiki_context(prompt))


def _kis_wiki_context_gap_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_kis_wiki_context_gap_note_is_negative(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_kis_wiki_context_gap_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "unresolved",
        "not resolved",
        "gap unresolved",
        "context still unresolved",
        "no live cross check",
        "no live cross checks",
        "without live cross check",
        "without live cross checks",
        "live cross check missing",
        "live cross checks missing",
        "no research spine",
        "without research spine",
        "research spine missing",
        "cross check missing",
        "cross checks missing",
        "not cross checked",
        "not checked",
        "미해결",
        "미확인",
        "교차확인 없음",
        "교차 확인 없음",
        "대체근거 없음",
        "대체 근거 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_actions_resolve_unavailable_wiki_context(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    gap = _kis_unavailable_wiki_context(prompt)
    if not gap or not isinstance(actions, dict):
        return False
    terms = [
        term
        for term in (
            "wiki",
            "위키",
            gap.get("status"),
            gap.get("reason"),
            "live_cross_check",
            "research_spine",
        )
        if str(term or "").strip()
    ]
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            note = metadata.get("jue_wiki_context_gap")
            if note in (None, "", [], {}):
                note = row.get("jue_wiki_context_gap")
            if not _kis_repair_note_is_concrete(note):
                continue
            if _kis_wiki_context_gap_note_is_negative(note):
                continue
            if _kis_payload_mentions_any_term({"note": note}, terms):
                return True
    return False


def _kis_hold_resolves_unavailable_wiki_context(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    gap = _kis_unavailable_wiki_context(prompt)
    if not gap or not _kis_hold_has_concrete_next_step(hold_decision):
        return False
    if _kis_wiki_context_gap_note_is_negative(hold_decision):
        return False
    return _kis_payload_mentions_any_term(
        hold_decision,
        [
            "wiki",
            "위키",
            gap.get("status"),
            gap.get("reason"),
            "live_cross_check",
            "research_spine",
        ],
    )


def _kis_actions_resolve_wiki_action_reference_memory(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _kis_wiki_action_reference_terms(prompt)
    translation_terms = _kis_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _kis_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _kis_prompt_wiki_reference_symbol_page_ids(prompt)
    block_symbol_map = _kis_prompt_block_symbol_map(prompt)
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_reference_basis",
                "jue_wiki_freshness_cross_check",
                "jue_wiki_selection_resolution",
                "jue_wiki_action_reference_recovery",
                "jue_wiki_context_gap",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if not _kis_repair_note_is_concrete(note):
                    continue
                action_symbols = _kis_action_identity_symbols_with_block_map(
                    row,
                    block_symbol_map=block_symbol_map,
                )
                if _kis_action_identity_block_ids(row) and not action_symbols:
                    continue
                if _kis_payload_resolves_wiki_action_reference_terms(
                    payload={"note": note, "symbol": row.get("symbol")},
                    terms=terms,
                    translation_terms=translation_terms,
                    allowed_page_ids=allowed_page_ids,
                    required_symbol_page_ids=required_symbol_page_ids,
                    action_symbols=action_symbols,
                ):
                    return True
    return False


def _kis_actions_resolve_wiki_action_reference_recovery(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _kis_wiki_action_reference_terms(prompt)
    translation_terms = _kis_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _kis_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _kis_prompt_wiki_reference_symbol_page_ids(prompt)
    block_symbol_map = _kis_prompt_block_symbol_map(prompt)
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            note = metadata.get("jue_wiki_action_reference_recovery")
            if note in (None, "", [], {}):
                note = row.get("jue_wiki_action_reference_recovery")
            if not _kis_repair_note_is_concrete(note):
                continue
            action_symbols = _kis_action_identity_symbols_with_block_map(
                row,
                block_symbol_map=block_symbol_map,
            )
            if _kis_action_identity_block_ids(row) and not action_symbols:
                continue
            if _kis_payload_resolves_wiki_action_reference_terms(
                payload={"note": note, "symbol": row.get("symbol")},
                terms=terms,
                translation_terms=translation_terms,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                action_symbols=action_symbols,
            ):
                return True
    return False


def _kis_hold_resolves_wiki_action_reference_memory(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    terms = _kis_wiki_action_reference_terms(prompt)
    translation_terms = _kis_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _kis_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _kis_prompt_wiki_reference_symbol_page_ids(prompt)
    hold_symbols = _kis_hold_identity_symbols(hold_decision)
    if _kis_hold_has_concrete_next_step(hold_decision):
        return _kis_payload_resolves_wiki_action_reference_terms(
            payload=hold_decision,
            terms=terms,
            translation_terms=translation_terms,
            allowed_page_ids=allowed_page_ids,
            required_symbol_page_ids=required_symbol_page_ids,
            action_symbols=hold_symbols,
            require_selected_symbol_page_reference=True,
        )
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    metadata = hold.get("metadata") if isinstance(hold.get("metadata"), dict) else {}
    recovery_note = metadata.get("jue_wiki_action_reference_recovery")
    if recovery_note in (None, "", [], {}):
        recovery_note = hold.get("jue_wiki_action_reference_recovery")
    if not _kis_repair_note_is_concrete(recovery_note):
        return False
    return _kis_payload_resolves_wiki_action_reference_terms(
        payload={"note": recovery_note},
        terms=terms,
        translation_terms=translation_terms,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
        action_symbols=hold_symbols,
        require_selected_symbol_page_reference=True,
    )


def _kis_hold_resolves_wiki_action_reference_recovery(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    metadata = hold.get("metadata") if isinstance(hold.get("metadata"), dict) else {}
    recovery_note = metadata.get("jue_wiki_action_reference_recovery")
    if recovery_note in (None, "", [], {}):
        recovery_note = hold.get("jue_wiki_action_reference_recovery")
    if not _kis_repair_note_is_concrete(recovery_note):
        return False
    terms = _kis_wiki_action_reference_terms(prompt)
    translation_terms = _kis_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _kis_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _kis_prompt_wiki_reference_symbol_page_ids(prompt)
    hold_symbols = _kis_hold_identity_symbols(hold_decision)
    return _kis_payload_resolves_wiki_action_reference_terms(
        payload={"note": recovery_note},
        terms=terms,
        translation_terms=translation_terms,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
        action_symbols=hold_symbols,
        require_selected_symbol_page_reference=True,
    )


def _kis_prompt_has_applicable_wiki_context(prompt: dict[str, Any]) -> bool:
    if not isinstance(prompt, dict):
        return False
    if _kis_prompt_has_unavailable_wiki_context(prompt):
        return False
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    if _normalize_list(application.get("selected_page_ids")):
        return True
    wiki = prompt.get("jue_wiki") if isinstance(prompt.get("jue_wiki"), dict) else {}
    if _normalize_list(wiki.get("pages")):
        return True
    memory = (
        prompt.get("investment_memory")
        if isinstance(prompt.get("investment_memory"), dict)
        else {}
    )
    memory_wiki = (
        memory.get("jue_wiki") if isinstance(memory.get("jue_wiki"), dict) else {}
    )
    if memory_wiki and str(memory_wiki.get("status") or "").lower() not in {
        "error",
        "disabled",
        "unavailable",
    }:
        return bool(_normalize_list(memory_wiki.get("pages")))
    return False


def _kis_wiki_reference_symbols_from_payload(value: Any) -> set[str]:
    text = _json_dumps(value).upper()
    return {
        match
        for match in re.findall(r"KIS\.SYMBOL\.([0-9]{6})", text)
        if _is_symbol(match)
    }


def _kis_wiki_reference_page_ids_from_payload(value: Any) -> set[str]:
    text = _json_dumps(value).upper()
    return {
        match.lower()
        for match in re.findall(r"KIS\.(?:SYMBOL\.[0-9]{6}|OPS\.[A-Z0-9_.:-]+)", text)
    }


def _kis_payload_uses_only_allowed_wiki_page_ids(
    value: Any,
    *,
    allowed_page_ids: set[str] | None,
) -> bool:
    page_ids = _kis_wiki_reference_page_ids_from_payload(value)
    if not page_ids or not allowed_page_ids:
        return True
    return not (page_ids - allowed_page_ids)


def _kis_payload_wiki_page_ids_match_action_symbols(
    value: Any,
    *,
    action_symbols: set[str] | None,
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> bool:
    page_ids = _kis_wiki_reference_page_ids_from_payload(value)
    referenced_symbol_page_ids = {
        page_id
        for page_id in page_ids
        if re.fullmatch(r"kis\.symbol\.[0-9]{6}", page_id)
    }
    if not action_symbols:
        return True
    expected_symbol_page_ids: set[str] = set()
    for symbol in action_symbols or set():
        expected_symbol_page_ids.update(
            (required_symbol_page_ids or {}).get(symbol.upper(), set())
        )
    if expected_symbol_page_ids and not referenced_symbol_page_ids:
        return False
    if not referenced_symbol_page_ids:
        return True
    if not expected_symbol_page_ids:
        return not bool(required_symbol_page_ids)
    return not (referenced_symbol_page_ids - expected_symbol_page_ids)


def _kis_payload_has_selected_symbol_wiki_page_reference(
    value: Any,
    *,
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> bool:
    selected_symbol_page_ids: set[str] = set()
    for page_ids in (required_symbol_page_ids or {}).values():
        selected_symbol_page_ids.update(page_ids)
    if not selected_symbol_page_ids:
        return True
    page_ids = _kis_wiki_reference_page_ids_from_payload(value)
    referenced_symbol_page_ids = {
        page_id
        for page_id in page_ids
        if re.fullmatch(r"kis\.symbol\.[0-9]{6}", page_id)
    }
    if not referenced_symbol_page_ids:
        return False
    return not (referenced_symbol_page_ids - selected_symbol_page_ids)


def _kis_prompt_wiki_reference_page_ids(prompt: dict[str, Any]) -> set[str]:
    selected_page_ids: set[str] = set()
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    for page_id in _normalize_list(application.get("selected_page_ids")):
        text = str(page_id or "").strip().lower()
        if text:
            selected_page_ids.add(text)
    if selected_page_ids:
        return selected_page_ids
    page_ids: set[str] = set()
    wiki = prompt.get("jue_wiki") if isinstance(prompt.get("jue_wiki"), dict) else {}
    for row in _normalize_list(wiki.get("pages")):
        if not isinstance(row, dict):
            continue
        text = str(row.get("page_id") or "").strip().lower()
        if text:
            page_ids.add(text)
    return page_ids


def _kis_prompt_wiki_reference_symbol_page_ids(
    prompt: dict[str, Any],
) -> dict[str, set[str]]:
    symbol_page_ids: dict[str, set[str]] = {}
    for page_id in _kis_prompt_wiki_reference_page_ids(prompt):
        match = re.fullmatch(r"kis\.symbol\.([0-9]{6})", page_id)
        if not match:
            continue
        symbol = match.group(1).upper()
        symbol_page_ids.setdefault(symbol, set()).add(page_id)
    return symbol_page_ids


def _kis_prompt_block_symbol_map(prompt: dict[str, Any]) -> dict[str, set[str]]:
    block_symbol_map: dict[str, set[str]] = {}
    if not isinstance(prompt, dict):
        return block_symbol_map
    for section in ("blocks", "open_blocks", "active_blocks"):
        for row in _normalize_list(prompt.get(section)):
            if not isinstance(row, dict):
                continue
            symbols = _kis_action_identity_symbols(row)
            if not symbols:
                continue
            for block_id in _kis_action_identity_block_ids(row):
                block_symbol_map.setdefault(block_id, set()).update(symbols)
    return block_symbol_map


def _kis_wiki_reference_has_traceable_id(value: Any) -> bool:
    text = _json_dumps(value).upper()
    return "KIS.SYMBOL." in text or "KIS.OPS." in text


def _kis_wiki_reference_matches_action_symbols(
    row: dict[str, Any],
    value: Any,
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> bool:
    if not _kis_wiki_reference_has_traceable_id(value):
        return False
    page_ids = _kis_wiki_reference_page_ids_from_payload(value)
    if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
        return False
    action_symbols = _kis_action_identity_symbols_with_block_map(
        row,
        block_symbol_map=block_symbol_map,
    )
    expected_symbol_page_ids: set[str] = set()
    for symbol in action_symbols:
        expected_symbol_page_ids.update(
            (required_symbol_page_ids or {}).get(symbol.upper(), set())
        )
    if allowed_page_ids and action_symbols and not expected_symbol_page_ids:
        return False
    if expected_symbol_page_ids:
        referenced_symbol_page_ids = {
            page_id
            for page_id in page_ids
            if re.fullmatch(r"kis\.symbol\.[0-9]{6}", page_id)
        }
        if referenced_symbol_page_ids - expected_symbol_page_ids:
            return False
        return bool(page_ids & expected_symbol_page_ids)
    reference_symbols = _kis_wiki_reference_symbols_from_payload(value)
    if not reference_symbols:
        return True
    if _kis_action_identity_block_ids(row):
        return False
    return not action_symbols or bool(reference_symbols & action_symbols)


def _kis_wiki_reference_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_kis_wiki_reference_note_is_negative(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_kis_wiki_reference_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "wiki missing",
        "wiki unavailable",
        "wiki not available",
        "no wiki",
        "without wiki",
        "no fresh context",
        "fresh context unavailable",
        "fresh jue wiki context unavailable",
        "jue wiki missing",
        "jue wiki unavailable",
        "위키 없음",
        "위키 누락",
        "위키 미확인",
        "위키 근거 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_action_has_wiki_reference(
    row: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> bool:
    if not isinstance(row, dict):
        return False
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for source in (metadata, row):
        for key, value in source.items():
            if not str(key or "").startswith("jue_wiki"):
                continue
            if _kis_wiki_reference_note_is_negative(value):
                continue
            if _kis_repair_note_is_concrete(value) or _kis_payload_mentions_any_term(
                value,
                [
                    "jue_wiki",
                    "fresh_jue_wiki_context",
                    "wiki",
                    "위키",
                    "kis.symbol.",
                    "kis.ops.",
                ],
            ):
                return _kis_wiki_reference_matches_action_symbols(
                    row,
                    value,
                    allowed_page_ids=allowed_page_ids,
                    required_symbol_page_ids=required_symbol_page_ids,
                    block_symbol_map=block_symbol_map,
                )
    evidence_payload = {
        "evidence_refs": row.get("evidence_refs"),
        "evidence": row.get("evidence"),
        "metadata_evidence_refs": metadata.get("evidence_refs"),
    }
    if _kis_wiki_reference_note_is_negative(evidence_payload):
        return False
    return _kis_payload_mentions_any_term(
        evidence_payload,
        [
            "jue_wiki",
            "fresh_jue_wiki_context",
            "wiki",
            "위키",
            "kis.symbol.",
            "kis.ops.",
        ],
    ) and _kis_wiki_reference_matches_action_symbols(
        row,
        evidence_payload,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
        block_symbol_map=block_symbol_map,
    )


def _kis_hold_wiki_reference_matches_context(
    value: Any,
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> bool:
    if not _kis_wiki_reference_has_traceable_id(value):
        return False
    page_ids = _kis_wiki_reference_page_ids_from_payload(value)
    if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
        return False
    selected_symbol_page_ids: set[str] = set()
    for page_id_set in (required_symbol_page_ids or {}).values():
        selected_symbol_page_ids.update(page_id_set)
    if selected_symbol_page_ids:
        return bool(page_ids & selected_symbol_page_ids)
    return True


def _kis_hold_has_wiki_reference(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> bool:
    if not isinstance(hold_decision, dict):
        return False
    metadata = (
        hold_decision.get("metadata")
        if isinstance(hold_decision.get("metadata"), dict)
        else {}
    )
    for source in (metadata, hold_decision):
        for key, value in source.items():
            if not str(key or "").startswith("jue_wiki"):
                continue
            if _kis_wiki_reference_note_is_negative(value):
                continue
            if _kis_repair_note_is_concrete(value) or _kis_payload_mentions_any_term(
                value,
                [
                    "jue_wiki",
                    "fresh_jue_wiki_context",
                    "wiki",
                    "위키",
                    "kis.symbol.",
                    "kis.ops.",
                ],
            ):
                return _kis_hold_wiki_reference_matches_context(
                    value,
                    allowed_page_ids=allowed_page_ids,
                    required_symbol_page_ids=required_symbol_page_ids,
                )
    evidence_payload = {
        "evidence_refs": hold_decision.get("evidence_refs"),
        "evidence": hold_decision.get("evidence"),
        "metadata_evidence_refs": metadata.get("evidence_refs"),
    }
    if _kis_wiki_reference_note_is_negative(evidence_payload):
        return False
    return _kis_payload_mentions_any_term(
        evidence_payload,
        [
            "jue_wiki",
            "fresh_jue_wiki_context",
            "wiki",
            "위키",
            "kis.symbol.",
            "kis.ops.",
        ],
    ) and _kis_hold_wiki_reference_matches_context(
        evidence_payload,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
    )


def _kis_required_symbol_page_entries(
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for symbol, page_ids in sorted((required_symbol_page_ids or {}).items()):
        for page_id in sorted(page_ids):
            entries.append((page_id, symbol.upper()))
    return entries


def _kis_hold_required_symbol_page_ids(
    hold_decision: dict[str, Any],
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> dict[str, set[str]]:
    target_symbols = _kis_hold_identity_symbols(hold_decision)
    if not target_symbols:
        return dict(required_symbol_page_ids or {})
    return {
        symbol: set(page_ids)
        for symbol, page_ids in (required_symbol_page_ids or {}).items()
        if symbol.upper() in target_symbols
    }


def _kis_hold_uncovered_target_symbols(
    hold_decision: dict[str, Any],
    required_symbol_page_ids: dict[str, set[str]] | None,
    *,
    allowed_page_ids: set[str] | None = None,
) -> set[str]:
    target_symbols = _kis_hold_identity_symbols(hold_decision)
    if not target_symbols:
        return set()
    if not required_symbol_page_ids:
        return set(target_symbols) if allowed_page_ids else set()
    covered_symbols = {
        str(symbol or "").strip().upper()
        for symbol in required_symbol_page_ids
        if str(symbol or "").strip()
    }
    return target_symbols - covered_symbols


def _kis_hold_wiki_reference_page_ids(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
) -> set[str]:
    if not isinstance(hold_decision, dict):
        return set()
    metadata = (
        hold_decision.get("metadata")
        if isinstance(hold_decision.get("metadata"), dict)
        else {}
    )
    referenced_page_ids: set[str] = set()
    for source in (metadata, hold_decision):
        for key, value in source.items():
            if not str(key or "").startswith("jue_wiki"):
                continue
            if _kis_wiki_reference_note_is_negative(value):
                continue
            if not (
                _kis_repair_note_is_concrete(value)
                or _kis_payload_mentions_any_term(
                    value,
                    [
                        "jue_wiki",
                        "fresh_jue_wiki_context",
                        "wiki",
                        "위키",
                        "kis.symbol.",
                        "kis.ops.",
                    ],
                )
            ):
                continue
            page_ids = _kis_wiki_reference_page_ids_from_payload(value)
            if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
                continue
            if allowed_page_ids:
                page_ids &= allowed_page_ids
            referenced_page_ids.update(page_ids)
    evidence_payload = {
        "evidence_refs": hold_decision.get("evidence_refs"),
        "evidence": hold_decision.get("evidence"),
        "metadata_evidence_refs": metadata.get("evidence_refs"),
    }
    if not _kis_wiki_reference_note_is_negative(
        evidence_payload
    ) and _kis_payload_mentions_any_term(
        evidence_payload,
        [
            "jue_wiki",
            "fresh_jue_wiki_context",
            "wiki",
            "위키",
            "kis.symbol.",
            "kis.ops.",
        ],
    ):
        page_ids = _kis_wiki_reference_page_ids_from_payload(evidence_payload)
        if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
            return referenced_page_ids
        if allowed_page_ids:
            page_ids &= allowed_page_ids
        referenced_page_ids.update(page_ids)
    return referenced_page_ids


def _kis_hold_wiki_reference_missing_symbol_pages(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    referenced_page_ids = _kis_hold_wiki_reference_page_ids(
        hold_decision,
        allowed_page_ids=allowed_page_ids,
    )
    return [
        {"section": "hold_decision", "page_id": page_id, "symbol": symbol}
        for page_id, symbol in _kis_required_symbol_page_entries(
            required_symbol_page_ids
        )
        if page_id not in referenced_page_ids
    ]


def _kis_hold_wiki_reference_count(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> int:
    required_entries = _kis_required_symbol_page_entries(required_symbol_page_ids)
    if required_entries:
        referenced_page_ids = _kis_hold_wiki_reference_page_ids(
            hold_decision,
            allowed_page_ids=allowed_page_ids,
        )
        return sum(1 for page_id, _symbol in required_entries if page_id in referenced_page_ids)
    return (
        1
        if _kis_hold_has_wiki_reference(
            hold_decision,
            allowed_page_ids=allowed_page_ids,
            required_symbol_page_ids=required_symbol_page_ids,
        )
        else 0
    )


def _kis_action_wiki_reference_count(
    actions: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> int:
    if not isinstance(actions, dict):
        return 0
    count = 0
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if isinstance(row, dict) and _kis_action_has_wiki_reference(
                row,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                block_symbol_map=block_symbol_map,
            ):
                count += 1
    return count


def _kis_action_wiki_reference_missing_actions(
    actions: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not isinstance(actions, dict):
        return []
    missing: list[dict[str, Any]] = []
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict) or _kis_action_has_wiki_reference(
                row,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                block_symbol_map=block_symbol_map,
            ):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            symbols = sorted(
                _kis_action_identity_symbols_with_block_map(
                    row,
                    block_symbol_map=block_symbol_map,
                )
            )
            summary: dict[str, Any] = {"section": key}
            if row.get("block_id") not in (None, "", [], {}):
                summary["block_id"] = _clean_text(row.get("block_id"), limit=80)
            if symbols:
                summary["symbol"] = symbols[0]
            for source_key, target_key in (
                ("qty", "qty"),
                ("qty_open", "qty"),
                ("quantity", "qty"),
            ):
                qty = _safe_int(row.get(source_key))
                if qty > 0 and "qty" not in summary:
                    summary[target_key] = qty
            horizon = _clean_text(
                row.get("horizon") or metadata.get("horizon"),
                limit=40,
            )
            if horizon:
                summary["horizon"] = horizon
            reason = _clean_text(row.get("reason") or metadata.get("reason"), limit=120)
            if reason:
                summary["reason"] = reason
            missing.append(summary)
            if len(missing) >= max(int(limit), 1):
                return missing
    return missing


def _kis_prompt_has_wiki_usage_contract(prompt: dict[str, Any]) -> bool:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    trust_profile = (
        application.get("trust_profile")
        if isinstance(application.get("trust_profile"), dict)
        else {}
    )
    usage_contract = (
        trust_profile.get("usage_contract")
        if isinstance(trust_profile.get("usage_contract"), dict)
        else {}
    )
    if usage_contract:
        return True
    for memory_key in ("investment_memory", "memory"):
        memory = (
            prompt.get(memory_key)
            if isinstance(prompt.get(memory_key), dict)
            else {}
        )
        usage_memory = (
            memory.get("jue_wiki_usage_contract_memory")
            if isinstance(memory.get("jue_wiki_usage_contract_memory"), dict)
            else {}
        )
        if _kis_payload_mentions_any_term(
            usage_memory,
            [
                "jue_wiki_usage_contract_resolution",
                "required_evidence",
                "usage_contract",
                "usage contract",
            ],
        ):
            return True
    workflow = (
        prompt.get("jue_workflow")
        if isinstance(prompt.get("jue_workflow"), dict)
        else {}
    )
    for contract in _normalize_list(workflow.get("contracts")):
        if not isinstance(contract, dict):
            continue
        if (
            contract.get("contract_id") == "jue_wiki_usage_contract_resolution"
            and _kis_prompt_has_applicable_wiki_context(prompt)
        ):
            return True
    return False


def _kis_wiki_usage_contract_required_terms(prompt: dict[str, Any]) -> list[str]:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    trust_profile = (
        application.get("trust_profile")
        if isinstance(application.get("trust_profile"), dict)
        else {}
    )
    usage_contract = (
        trust_profile.get("usage_contract")
        if isinstance(trust_profile.get("usage_contract"), dict)
        else {}
    )
    terms: list[str] = []

    def add_term(value: Any) -> None:
        term = str(value or "").strip().lower()
        if (
            term
            and term != "jue_wiki_usage_contract_resolution"
            and term not in terms
        ):
            terms.append(term)

    for key in ("required_cross_checks",):
        for value in _normalize_list(usage_contract.get(key)):
            add_term(value)

    for memory_key in ("investment_memory", "memory"):
        memory = (
            prompt.get(memory_key)
            if isinstance(prompt.get(memory_key), dict)
            else {}
        )
        usage_memory = (
            memory.get("jue_wiki_usage_contract_memory")
            if isinstance(memory.get("jue_wiki_usage_contract_memory"), dict)
            else {}
        )
        for item in _normalize_list(usage_memory.get("items")):
            if not isinstance(item, dict):
                continue
            guidance = (
                item.get("application_guidance")
                if isinstance(item.get("application_guidance"), dict)
                else {}
            )
            for key in ("required_cross_checks", "cross_checks", "required_terms"):
                for value in _normalize_list(guidance.get(key)):
                    add_term(value)
    return terms


def _kis_usage_contract_resolution_is_concrete(
    value: Any,
    *,
    required_terms: list[str] | None = None,
) -> bool:
    text = _clean_text(value, limit=800)
    if not text:
        return False
    compact = (
        text.strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "usage contract resolution missing",
        "contract resolution missing",
        "resolution missing",
        "resolution unavailable",
        "not resolved",
        "not checked",
        "not cross checked",
        "cross check missing",
        "cross checks missing",
        "cross check unavailable",
        "cross checks unavailable",
        "no cross check",
        "no cross checks",
        "no live quote",
        "no account state",
        "no risk gate",
        "no current price structure",
        "교차확인 없음",
        "교차 확인 없음",
        "계약 해결 누락",
        "사용 계약 해결 누락",
        "미해결",
        "미확인",
    )
    if any(phrase in compact for phrase in negative_phrases):
        return False
    for term in required_terms or []:
        normalized_term = (
            str(term or "")
            .strip()
            .lower()
            .replace("_", " ")
            .replace("-", " ")
            .replace("/", " ")
        )
        if normalized_term and normalized_term not in compact:
            return False
    return True


def _kis_action_wiki_usage_contract_resolution_count(
    actions: dict[str, Any],
    *,
    required_terms: list[str] | None = None,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> int:
    if not isinstance(actions, dict):
        return 0
    count = 0
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            resolution = _clean_text(
                metadata.get("jue_wiki_usage_contract_resolution")
                or row.get("jue_wiki_usage_contract_resolution"),
                limit=800,
            )
            if _kis_usage_contract_resolution_is_concrete(
                resolution,
                required_terms=required_terms,
            ) and _kis_usage_contract_resolution_matches_action_symbols(
                row,
                resolution,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                block_symbol_map=block_symbol_map,
            ):
                count += 1
    return count


def _kis_usage_contract_resolution_matches_action_symbols(
    row: dict[str, Any],
    resolution: Any,
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> bool:
    action_symbols = _kis_action_identity_symbols_with_block_map(
        row,
        block_symbol_map=block_symbol_map,
    )
    expected_symbol_page_ids: set[str] = set()
    for symbol in action_symbols:
        expected_symbol_page_ids.update(
            (required_symbol_page_ids or {}).get(symbol.upper(), set())
        )
    if allowed_page_ids and action_symbols and not expected_symbol_page_ids:
        return False
    page_ids = _kis_wiki_reference_page_ids_from_payload(resolution)
    if not page_ids:
        if _kis_action_identity_block_ids(row) and expected_symbol_page_ids:
            text = _json_dumps(resolution).upper()
            return any(symbol.upper() in text for symbol in action_symbols)
        selected_symbol_page_count = sum(
            len(page_ids) for page_ids in (required_symbol_page_ids or {}).values()
        )
        if selected_symbol_page_count > 1 and expected_symbol_page_ids:
            text = _json_dumps(resolution).upper()
            return any(symbol.upper() in text for symbol in action_symbols)
        return True
    if allowed_page_ids and page_ids - allowed_page_ids:
        return False
    if expected_symbol_page_ids:
        referenced_symbol_page_ids = {
            page_id
            for page_id in page_ids
            if re.fullmatch(r"kis\.symbol\.[0-9]{6}", page_id)
        }
        if referenced_symbol_page_ids - expected_symbol_page_ids:
            return False
        return bool(page_ids & expected_symbol_page_ids)
    reference_symbols = _kis_wiki_reference_symbols_from_payload(resolution)
    if not reference_symbols:
        return True
    if _kis_action_identity_block_ids(row):
        return False
    return not action_symbols or bool(reference_symbols & action_symbols)


def _kis_hold_decision_has_payload(hold_decision: dict[str, Any]) -> bool:
    return isinstance(hold_decision, dict) and bool(hold_decision)


def _kis_hold_wiki_usage_contract_resolution_count(
    hold_decision: dict[str, Any],
    *,
    required_terms: list[str] | None = None,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> int:
    if not isinstance(hold_decision, dict):
        return 0
    metadata = (
        hold_decision.get("metadata")
        if isinstance(hold_decision.get("metadata"), dict)
        else {}
    )
    resolution = _clean_text(
        metadata.get("jue_wiki_usage_contract_resolution")
        or hold_decision.get("jue_wiki_usage_contract_resolution"),
        limit=800,
    )
    if not _kis_usage_contract_resolution_is_concrete(
        resolution,
        required_terms=required_terms,
    ):
        return 0
    required_entries = _kis_required_symbol_page_entries(required_symbol_page_ids)
    if len(required_entries) <= 1:
        page_ids = _kis_wiki_reference_page_ids_from_payload(resolution)
        if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
            return 0
        if page_ids and required_entries:
            required_page_id = required_entries[0][0]
            return 1 if required_page_id in page_ids else 0
        return 1
    page_ids = _kis_wiki_reference_page_ids_from_payload(resolution)
    if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
        return 0
    if allowed_page_ids:
        page_ids &= allowed_page_ids
    return sum(1 for page_id, _symbol in required_entries if page_id in page_ids)


def _kis_hold_has_prompt_linked_concrete_next_step(
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _kis_hold_has_concrete_next_step(hold_decision):
        return False
    terms = _kis_wiki_repair_reference_terms(prompt)
    if not terms:
        return True
    return _kis_payload_mentions_any_term(hold_decision, terms)


def _kis_requested_symbol_coverage_terms(prompt: dict[str, Any]) -> list[str]:
    coverage = _kis_scoped_requested_symbol_coverage(prompt)
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    missing = _normalize_list(coverage.get("missing_summary_symbols"))
    if missing:
        for symbol in missing:
            add(symbol)
        for symbol in _normalize_list(coverage.get("prompt_omitted_symbols")):
            add(symbol)
        return terms
    if "missing_summary_symbols" in coverage:
        return terms
    for symbol in _normalize_list(coverage.get("unsummarized_symbols")):
        add(symbol)
    for symbol in _normalize_list(coverage.get("prompt_omitted_symbols")):
        add(symbol)
    return terms


def _kis_requested_symbol_coverage_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _kis_requested_symbol_coverage_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _kis_requested_symbol_coverage_note_is_negative(child) for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "coverage unresolved",
        "coverage still unresolved",
        "summary still missing",
        "still missing",
        "no fresh summary",
        "no fresh wiki summary",
        "no wiki summary",
        "without wiki summary",
        "no live cross check",
        "no live cross checks",
        "not resolved",
        "unresolved",
        "미해결",
        "요약 없음",
        "아직 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_hold_resolves_requested_symbol_coverage(
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _kis_hold_has_concrete_next_step(hold_decision):
        return False
    if _kis_requested_symbol_coverage_note_is_negative(hold_decision):
        return False
    terms = _kis_requested_symbol_coverage_terms(prompt)
    if not terms:
        return True
    return _kis_payload_mentions_any_term(hold_decision, terms)


def _kis_actions_resolve_requested_symbol_coverage(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _kis_requested_symbol_coverage_terms(prompt)
    if not terms:
        return False
    resolution_terms = [
        "requested_symbol_coverage",
        "requested_symbol_summary",
        "summary_missing",
        "wiki_summary",
        "live_cross_check",
        "cross_check",
        "research_refresh",
        "위키",
        "요약",
        "교차확인",
    ]
    metadata_keys = (
        "jue_wiki_requested_symbol_coverage",
        "jue_wiki_requested_symbol_coverage_resolution",
        "requested_symbol_coverage",
        "requested_symbol_coverage_resolution",
        "wiki_coverage_resolution",
    )
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            symbol_payload: dict[str, Any] = {
                "symbol": row.get("symbol"),
                "code": row.get("code"),
                "ticker": row.get("ticker"),
                "symbols": row.get("symbols"),
                "metadata_symbols": (
                    metadata.get("symbols")
                    or metadata.get("symbol")
                    or metadata.get("requested_symbols")
                ),
            }
            resolution_payload: dict[str, Any] = {
                "metadata": {
                    metadata_key: metadata.get(metadata_key)
                    for metadata_key in metadata_keys
                    if metadata.get(metadata_key) not in (None, "", [], {})
                },
                "row": {
                    metadata_key: row.get(metadata_key)
                    for metadata_key in metadata_keys
                    if row.get(metadata_key) not in (None, "", [], {})
                },
            }
            if _kis_requested_symbol_coverage_note_is_negative(resolution_payload):
                continue
            if _kis_payload_mentions_any_term(
                symbol_payload,
                terms,
            ) and _kis_payload_mentions_any_term(resolution_payload, resolution_terms):
                return True
    return False


def _kis_actions_resolve_wiki_selection_guidance(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _kis_wiki_selection_guidance_terms(prompt)
    if not terms:
        return False
    translation_terms = _kis_wiki_selection_guidance_translation_terms(prompt)
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_selection_resolution",
                "jue_wiki_freshness_cross_check",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if not _kis_repair_note_is_concrete(note):
                    continue
                if _kis_payload_resolves_wiki_selection_guidance_terms(
                    payload={"note": note, "symbol": row.get("symbol")},
                    terms=terms,
                    translation_terms=translation_terms,
                ):
                    return True
    return False


def _kis_hold_resolves_wiki_selection_guidance(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _kis_hold_has_concrete_next_step(hold_decision):
        return False
    terms = _kis_wiki_selection_guidance_terms(prompt)
    if not terms:
        return False
    translation_terms = _kis_wiki_selection_guidance_translation_terms(prompt)
    return _kis_payload_resolves_wiki_selection_guidance_terms(
        payload=hold_decision,
        terms=terms,
        translation_terms=translation_terms,
    )


def _kis_prompt_has_wiki_decision_adjustments(prompt: dict[str, Any]) -> bool:
    contract = (
        prompt.get("jue_wiki_decision_adjustments")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if not _kis_wiki_memory_container_matches_scope(contract):
        return False
    status = str(contract.get("status") or "").strip().lower()
    return status == "active" and bool(
        _kis_wiki_decision_adjustment_rows(prompt)
    )


def _kis_wiki_decision_adjustment_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    contract = (
        prompt.get("jue_wiki_decision_adjustments")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if not _kis_wiki_memory_container_matches_scope(contract):
        return []
    translated = _kis_wiki_memory_transferability_is_translated(contract)
    return [
        row
        for row in _normalize_list(contract.get("adjustments"))
        if isinstance(row, dict)
        and _kis_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        )
    ]


def _kis_wiki_decision_adjustment_terms(prompt: dict[str, Any]) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for row in _kis_wiki_decision_adjustment_rows(prompt):
        for key in (
            "action",
            "target_risk_posture",
            "reason",
            "current_risk_posture",
            "current_status",
        ):
            add(row.get(key))
        for key in ("recommended_allowed_uses", "deprioritized_allowed_uses"):
            for item in _normalize_list(row.get(key)):
                add(item)
    return terms


def _kis_wiki_decision_adjustment_evidence_terms(prompt: dict[str, Any]) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for row in _kis_wiki_decision_adjustment_rows(prompt):
        evidence_grade = (
            row.get("evidence_grade")
            if isinstance(row.get("evidence_grade"), dict)
            else {}
        )
        if not evidence_grade:
            continue
        for key in ("instruction", "status", "basis"):
            add(evidence_grade.get(key))
    return terms


def _kis_wiki_decision_adjustment_execution_hint_terms(
    prompt: dict[str, Any],
) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for row in _kis_wiki_decision_adjustment_rows(prompt):
        for key in (
            "decision_adjustment_effectiveness",
            "decision_adjustment_audit_effectiveness",
        ):
            effectiveness = row.get(key) if isinstance(row.get(key), dict) else {}
            add(effectiveness.get("execution_hint"))
    return terms


def _kis_wiki_decision_adjustment_translation_terms(prompt: dict[str, Any]) -> list[str]:
    contract = (
        prompt.get("jue_wiki_decision_adjustments")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if not _kis_wiki_memory_container_matches_scope(contract):
        return []
    inherited_translated = _kis_wiki_memory_transferability_is_translated(contract)
    has_translated_adjustment = any(
        isinstance(row, dict)
        and (
            inherited_translated
            or _kis_wiki_memory_transferability_is_translated(row)
        )
        for row in _kis_wiki_decision_adjustment_rows(prompt)
    )
    if not has_translated_adjustment:
        return []
    return [
        "translated_kr_equity_mapping",
        "translated_kis_mapping",
        "kr_equity_translation_mapping",
        "cross_venue_mapping",
        "cross_scope_mapping",
        "translated_policy_context",
    ]


def _kis_wiki_decision_adjustment_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _kis_wiki_decision_adjustment_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _kis_wiki_decision_adjustment_note_is_negative(child)
            for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "decision adjustment not applied",
        "wiki decision adjustment not applied",
        "adjustment not applied",
        "decision adjustment unresolved",
        "adjustment unresolved",
        "decision adjustment unavailable",
        "adjustment unavailable",
        "decision adjustment missing",
        "adjustment missing",
        "evidence unavailable",
        "execution not performed",
        "execution unavailable",
        "not performed",
        "not applied",
        "보정 미적용",
        "보정 미해결",
        "보정 불가",
        "적용 못함",
        "실행 안함",
        "실행 못함",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _kis_actions_have_prompt_linked_decision_adjustment_metadata(
    prompt: dict[str, Any],
    actions: dict[str, Any],
    *,
    required_terms: list[str] | None = None,
) -> bool:
    terms = _kis_wiki_decision_adjustment_terms(prompt)
    for key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        for row in _normalize_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_decision_adjustment",
                "jue_wiki_decision_adjustments",
                "jue_wiki_decision_adjustment_resolution",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if _kis_wiki_decision_adjustment_note_is_negative(note):
                    continue
                if not _kis_repair_note_is_concrete(note):
                    continue
                if not terms or _kis_payload_mentions_any_term(note, terms):
                    if required_terms and not _kis_payload_mentions_any_term(
                        note,
                        required_terms,
                    ):
                        continue
                    return True
    return False


def _kis_wiki_decision_adjustment_resolved(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    terms = _kis_wiki_decision_adjustment_terms(prompt)
    evidence_terms = _kis_wiki_decision_adjustment_evidence_terms(prompt)
    execution_hint_terms = _kis_wiki_decision_adjustment_execution_hint_terms(prompt)
    translation_terms = _kis_wiki_decision_adjustment_translation_terms(prompt)
    if _kis_actions_have_prompt_linked_decision_adjustment_metadata(prompt, actions):
        if evidence_terms:
            if not _kis_actions_have_prompt_linked_decision_adjustment_metadata(
                prompt,
                actions,
                required_terms=evidence_terms,
            ):
                return False
        if execution_hint_terms:
            if not _kis_actions_have_prompt_linked_decision_adjustment_metadata(
                prompt,
                actions,
                required_terms=execution_hint_terms,
            ):
                return False
        if translation_terms:
            if not _kis_actions_have_prompt_linked_decision_adjustment_metadata(
                prompt,
                actions,
                required_terms=translation_terms,
            ):
                return False
        return True
    if (
        not _kis_wiki_decision_adjustment_note_is_negative(hold_decision)
        and _kis_payload_mentions_any_term(hold_decision, terms)
    ):
        if evidence_terms and not _kis_payload_mentions_any_term(
            hold_decision,
            evidence_terms,
        ):
            return False
        if execution_hint_terms and not _kis_payload_mentions_any_term(
            hold_decision,
            execution_hint_terms,
        ):
            return False
        if translation_terms and not _kis_payload_mentions_any_term(
            hold_decision,
            translation_terms,
        ):
            return False
        return _kis_hold_has_concrete_next_step(hold_decision)
    validation_repair_resolution = response.get("validation_repair_resolution")
    if _kis_payload_mentions_any_term(
        validation_repair_resolution,
        terms,
    ):
        if _kis_wiki_decision_adjustment_note_is_negative(
            validation_repair_resolution,
        ):
            return False
        if evidence_terms and not _kis_payload_mentions_any_term(
            validation_repair_resolution,
            evidence_terms,
        ):
            return False
        if execution_hint_terms and not _kis_payload_mentions_any_term(
            validation_repair_resolution,
            execution_hint_terms,
        ):
            return False
        if translation_terms and not _kis_payload_mentions_any_term(
            validation_repair_resolution,
            translation_terms,
        ):
            return False
        return _kis_response_has_concrete_repair_resolution(response)
    return False


def _kis_memory_card_quality_resolution_has_specific_evidence(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    terms = _kis_memory_card_quality_required_terms(prompt)
    target_symbols = _kis_memory_card_quality_target_symbols(prompt)
    if not terms:
        hold_resolved = (
            not _kis_memory_card_quality_note_is_negative(hold_decision)
            and _kis_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                target_symbols,
            )
        )
        return hold_resolved or _kis_memory_card_quality_action_has_specific_evidence(
            prompt=prompt,
            actions=actions,
        )
    if (
        not _kis_memory_card_quality_note_is_negative(hold_decision)
        and _kis_payload_mentions_any_term(hold_decision, terms)
    ):
        return _kis_hold_has_concrete_next_step_for_symbols(
            hold_decision,
            target_symbols,
        )
    validation_repair_resolution = response.get("validation_repair_resolution")
    if (
        not _kis_memory_card_quality_note_is_negative(validation_repair_resolution)
        and _kis_payload_mentions_any_term(
            validation_repair_resolution,
            terms,
        )
    ):
        return _kis_response_has_concrete_repair_resolution(
            response,
            target_symbols=target_symbols,
        )
    if _kis_memory_card_quality_action_has_specific_evidence(
        prompt=prompt,
        actions=actions,
    ):
        return True
    return False


def kis_manager_response_contract_error(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> str:
    """Return a contract error when a no-action KIS run hides required repair work."""

    if _kis_execution_gate_blocks_contract(prompt):
        return ""
    action_count = _kis_manager_action_item_count(actions)
    if action_count > 0 and _kis_research_spine_memory_resolution_missing(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    ):
        return "research_spine_memory_resolution_missing_from_model"
    if _kis_candidate_memory_hint_resolution_missing(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    ):
        return "candidate_memory_hint_resolution_missing_from_model"
    active_repair = (
        _kis_prompt_has_active_validation_repair(prompt)
        or _kis_prompt_has_jue_wiki_validation_repair_contract(prompt)
        or _kis_prompt_has_wiki_repair_priorities(prompt)
        or _kis_prompt_has_wiki_attention_response_contract(prompt)
        or _kis_prompt_has_requested_symbol_coverage_gap(prompt)
        or _kis_prompt_has_memory_card_quality_gap(prompt)
        or _kis_prompt_has_degraded_wiki_effectiveness(prompt)
        or _kis_prompt_has_wiki_decision_adjustments(prompt)
        or _kis_prompt_has_wiki_selection_guidance(prompt)
        or _kis_prompt_has_unavailable_wiki_context(prompt)
        or _kis_prompt_has_wiki_action_reference_memory(prompt)
    )
    wiki_attention = _kis_prompt_has_wiki_attention_response_contract(prompt)
    requested_symbol_coverage_gap = _kis_prompt_has_requested_symbol_coverage_gap(
        prompt
    )
    requested_symbol_coverage_action_resolved = (
        requested_symbol_coverage_gap
        and _kis_actions_resolve_requested_symbol_coverage(
            prompt=prompt,
            actions=actions,
        )
    )
    requested_symbol_coverage_hold_resolved = (
        requested_symbol_coverage_gap
        and _kis_hold_resolves_requested_symbol_coverage(prompt, hold_decision)
    )
    memory_card_quality_gap = _kis_prompt_has_memory_card_quality_gap(prompt)
    attention_action_resolved = (
        wiki_attention
        and _kis_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=("jue_wiki_repair_attention",),
        )
    )
    memory_card_quality_resolved = (
        memory_card_quality_gap
        and _kis_memory_card_quality_resolution_has_specific_evidence(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    )
    degraded_wiki_effectiveness_gap = _kis_prompt_has_degraded_wiki_effectiveness(
        prompt
    )
    wiki_decision_adjustment_gap = _kis_prompt_has_wiki_decision_adjustments(prompt)
    wiki_decision_adjustment_resolved = (
        wiki_decision_adjustment_gap
        and _kis_wiki_decision_adjustment_resolved(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    )
    wiki_selection_guidance_gap = _kis_prompt_has_wiki_selection_guidance(prompt)
    wiki_selection_guidance_action_resolved = (
        wiki_selection_guidance_gap
        and _kis_actions_resolve_wiki_selection_guidance(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_selection_guidance_hold_resolved = (
        wiki_selection_guidance_gap
        and _kis_hold_resolves_wiki_selection_guidance(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    unavailable_wiki_context_gap = _kis_prompt_has_unavailable_wiki_context(prompt)
    unavailable_wiki_context_action_resolved = (
        unavailable_wiki_context_gap
        and _kis_actions_resolve_unavailable_wiki_context(
            prompt=prompt,
            actions=actions,
        )
    )
    unavailable_wiki_context_hold_resolved = (
        unavailable_wiki_context_gap
        and _kis_hold_resolves_unavailable_wiki_context(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    wiki_action_reference_gap = _kis_prompt_has_wiki_action_reference_memory(prompt)
    wiki_action_reference_action_resolved = (
        wiki_action_reference_gap
        and _kis_actions_resolve_wiki_action_reference_memory(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_action_reference_hold_resolved = (
        wiki_action_reference_gap
        and _kis_hold_resolves_wiki_action_reference_memory(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    memory_contract_repair_required = _kis_prompt_requires_memory_contract_repair(
        prompt
    )
    memory_contract_repair_resolved = _kis_memory_contract_repair_resolved(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )
    degraded_wiki_effectiveness_action_resolved = (
        degraded_wiki_effectiveness_gap
        and _kis_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=(
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ),
        )
    )
    repair_resolved = (
        attention_action_resolved
        or _kis_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=(
                "validation_repair",
                "validation_repair_enforcement",
                "jue_wiki_repair_attention",
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ),
        )
        or _kis_response_has_prompt_linked_repair_resolution(prompt, response)
        or (
            wiki_attention
            and _kis_hold_has_prompt_linked_concrete_next_step(prompt, hold_decision)
        )
        or (
            requested_symbol_coverage_gap
            and (
                requested_symbol_coverage_action_resolved
                or requested_symbol_coverage_hold_resolved
            )
        )
        or memory_card_quality_resolved
        or wiki_decision_adjustment_resolved
        or wiki_selection_guidance_action_resolved
        or wiki_selection_guidance_hold_resolved
        or unavailable_wiki_context_action_resolved
        or unavailable_wiki_context_hold_resolved
        or wiki_action_reference_action_resolved
        or wiki_action_reference_hold_resolved
    )
    if memory_card_quality_gap and not memory_card_quality_resolved:
        repair_resolved = False
    if requested_symbol_coverage_gap and not (
        requested_symbol_coverage_action_resolved
        or requested_symbol_coverage_hold_resolved
    ):
        repair_resolved = False
    if wiki_decision_adjustment_gap and not wiki_decision_adjustment_resolved:
        repair_resolved = False
    if memory_contract_repair_required and not memory_contract_repair_resolved:
        repair_resolved = False
    if wiki_selection_guidance_gap and not (
        wiki_selection_guidance_action_resolved
        or (action_count <= 0 and wiki_selection_guidance_hold_resolved)
    ):
        repair_resolved = False
    if unavailable_wiki_context_gap and not (
        unavailable_wiki_context_action_resolved
        or (action_count <= 0 and unavailable_wiki_context_hold_resolved)
    ):
        repair_resolved = False
    if wiki_action_reference_gap and not (
        wiki_action_reference_action_resolved
        or (action_count <= 0 and wiki_action_reference_hold_resolved)
    ):
        repair_resolved = False
    if action_count > 0:
        if (
            wiki_action_reference_gap
            and not wiki_action_reference_action_resolved
        ):
            return "wiki_action_reference_resolution_missing_from_model"
        if (
            unavailable_wiki_context_gap
            and not unavailable_wiki_context_action_resolved
        ):
            return "wiki_context_gap_resolution_missing_from_model"
        if (
            wiki_selection_guidance_gap
            and not wiki_selection_guidance_action_resolved
        ):
            return "validation_repair_resolution_missing_from_model"
        if memory_contract_repair_required and not memory_contract_repair_resolved:
            return "memory_contract_resolution_missing_from_model"
        if requested_symbol_coverage_gap and not (
            requested_symbol_coverage_action_resolved
            or requested_symbol_coverage_hold_resolved
        ):
            return "validation_repair_resolution_missing_from_model"
        if (
            degraded_wiki_effectiveness_gap
            and not degraded_wiki_effectiveness_action_resolved
        ):
            return "validation_repair_resolution_missing_from_model"
        if active_repair and not repair_resolved:
            return "validation_repair_resolution_missing_from_model"
        return ""
    if memory_contract_repair_required and not memory_contract_repair_resolved:
        return "memory_contract_resolution_missing_from_model"
    if unavailable_wiki_context_gap and not unavailable_wiki_context_hold_resolved:
        return "wiki_context_gap_resolution_missing_from_model"
    if wiki_action_reference_gap and not wiki_action_reference_hold_resolved:
        return "wiki_action_reference_resolution_missing_from_model"
    if active_repair and not repair_resolved:
        return "validation_repair_resolution_missing_from_model"
    if (
        _kis_prompt_has_action_pressure(prompt)
        or _kis_prompt_has_wiki_action_pressure(prompt)
    ) and not _kis_hold_has_concrete_next_step(hold_decision):
        return "hold_decision_missing_concrete_trigger"
    return ""


def kis_manager_run_diagnostics(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    """Return audit diagnostics for KIS manager wiki/repair pressure handling."""

    action_count = _kis_manager_action_item_count(actions)
    repair_contract = _kis_scoped_jue_wiki_repair_contract(prompt)
    top_priorities = _normalize_list(repair_contract.get("top_priorities"))
    action_batches = _normalize_list(repair_contract.get("action_batches"))
    requested_symbol_coverage = _kis_scoped_requested_symbol_coverage(prompt)
    attention_contract = repair_contract.get("attention_plan_response_contract")
    attention_contract = attention_contract if isinstance(attention_contract, dict) else {}
    attention_active = (
        str(attention_contract.get("status") or "").strip().lower() == "active"
        and bool(_normalize_list(attention_contract.get("must_address")))
    )
    memory_card_quality_gap = _kis_prompt_has_memory_card_quality_gap(prompt)
    memory_card_quality = _kis_scoped_memory_card_quality(prompt)
    memory_card_summary = (
        memory_card_quality.get("summary")
        if isinstance(memory_card_quality.get("summary"), dict)
        else {}
    )
    degraded_effectiveness_items = _kis_degraded_wiki_effectiveness_items(prompt)
    degraded_effectiveness_page_ids: list[str] = []
    for item in degraded_effectiveness_items:
        page_id = str(item.get("page_id") or "").strip()
        if page_id and page_id not in degraded_effectiveness_page_ids:
            degraded_effectiveness_page_ids.append(page_id)
    candidate_memory_hint_summary = _kis_candidate_memory_hint_resolution_summary(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )
    research_spine_memory_summary = _kis_research_spine_memory_resolution_summary(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )
    memory_contract_summary = _kis_memory_contract_repair_resolution_summary(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )

    blocker_tags: dict[str, int] = {}

    def add(tag: str, weight: int = 1) -> None:
        blocker_tags[tag] = blocker_tags.get(tag, 0) + max(int(weight), 1)

    repair_priority_count = _safe_int(
        repair_contract.get("repair_priority_count")
    ) or len(top_priorities)
    repair_priority_resolved = (
        _kis_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=(
                "validation_repair",
                "validation_repair_enforcement",
                "jue_wiki_repair_attention",
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ),
        )
        or _kis_response_has_prompt_linked_repair_resolution(prompt, response)
        or _kis_hold_has_prompt_linked_concrete_next_step(prompt, hold_decision)
    )
    if repair_priority_count > 0 and not repair_priority_resolved:
        add("unresolved_jue_wiki_repair_priorities", 3)
    if action_batches and not repair_priority_resolved:
        add("unresolved_jue_wiki_repair_action_batches", 3)

    attention_resolved_by_action = (
        _kis_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=("jue_wiki_repair_attention",),
        )
    )
    attention_resolved_by_hold = _kis_hold_has_prompt_linked_concrete_next_step(
        prompt,
        hold_decision,
    )
    if attention_resolved_by_action:
        attention_resolution_status = "action_metadata"
    elif attention_resolved_by_hold:
        attention_resolution_status = "hold_trigger"
    else:
        attention_resolution_status = "unresolved"
    if attention_active and not (
        attention_resolved_by_action or attention_resolved_by_hold
    ):
        add("unresolved_jue_wiki_attention_plan", 3)

    degraded_effectiveness_resolved_by_action = (
        _kis_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=(
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ),
        )
    )
    degraded_effectiveness_resolved_by_hold = (
        _kis_hold_has_prompt_linked_concrete_next_step(prompt, hold_decision)
    )
    degraded_effectiveness_resolved_by_response = (
        _kis_response_has_prompt_linked_repair_resolution(prompt, response)
    )
    if degraded_effectiveness_resolved_by_action:
        degraded_effectiveness_resolution_status = "action_metadata"
    elif action_count <= 0 and degraded_effectiveness_resolved_by_hold:
        degraded_effectiveness_resolution_status = "hold_trigger"
    elif action_count <= 0 and degraded_effectiveness_resolved_by_response:
        degraded_effectiveness_resolution_status = "response_resolution"
    else:
        degraded_effectiveness_resolution_status = "unresolved"
    if degraded_effectiveness_items and not (
        degraded_effectiveness_resolved_by_action
        or (action_count <= 0 and degraded_effectiveness_resolved_by_hold)
        or (action_count <= 0 and degraded_effectiveness_resolved_by_response)
    ):
        add("unresolved_degraded_jue_wiki_effectiveness", 3)

    requested_symbol_coverage_gap = _kis_prompt_has_requested_symbol_coverage_gap(prompt)
    requested_symbol_coverage_resolved_by_action = (
        requested_symbol_coverage_gap
        and _kis_actions_resolve_requested_symbol_coverage(
            prompt=prompt,
            actions=actions,
        )
    )
    requested_symbol_coverage_resolved_by_hold = (
        requested_symbol_coverage_gap
        and _kis_hold_resolves_requested_symbol_coverage(
            prompt,
            hold_decision,
        )
    )
    if requested_symbol_coverage_gap and not (
        requested_symbol_coverage_resolved_by_action
        or requested_symbol_coverage_resolved_by_hold
    ):
        add("unresolved_jue_wiki_requested_symbol_coverage", 2)

    memory_card_quality_resolved = (
        memory_card_quality_gap
        and _kis_memory_card_quality_resolution_has_specific_evidence(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    )
    memory_card_quality_terms = _kis_memory_card_quality_required_terms(prompt)
    memory_card_quality_resolved_by_action = (
        memory_card_quality_gap
        and _kis_memory_card_quality_action_has_specific_evidence(
            prompt=prompt,
            actions=actions,
        )
    )
    memory_card_quality_resolved_by_hold = (
        memory_card_quality_resolved
        and not memory_card_quality_resolved_by_action
        and (
            not memory_card_quality_terms
            or _kis_payload_mentions_any_term(hold_decision, memory_card_quality_terms)
        )
        and _kis_hold_has_concrete_next_step_for_symbols(
            hold_decision,
            _kis_memory_card_quality_target_symbols(prompt),
        )
    )
    memory_card_quality_resolved_by_response = (
        memory_card_quality_resolved
        and not memory_card_quality_resolved_by_action
        and not memory_card_quality_resolved_by_hold
        and bool(response.get("validation_repair_resolution"))
    )
    if memory_card_quality_gap and not memory_card_quality_resolved:
        add("unresolved_jue_wiki_memory_card_quality", 2)
    candidate_memory_hint_unresolved_count = _safe_int(
        candidate_memory_hint_summary.get("candidate_memory_hint_unresolved_count")
    )
    if candidate_memory_hint_unresolved_count > 0:
        add("unresolved_candidate_memory_hint", candidate_memory_hint_unresolved_count)
    research_spine_memory_unresolved_count = _safe_int(
        research_spine_memory_summary.get("research_spine_memory_unresolved_count")
    )
    if action_count > 0 and research_spine_memory_unresolved_count > 0:
        add("unresolved_research_spine_memory", research_spine_memory_unresolved_count)
    memory_contract_unresolved_count = _safe_int(
        memory_contract_summary.get("memory_contract_unresolved_count")
    )
    if memory_contract_unresolved_count > 0:
        add("unresolved_memory_contract", memory_contract_unresolved_count)

    wiki_selection_guidance_gap = _kis_prompt_has_wiki_selection_guidance(prompt)
    wiki_selection_guidance_resolved_by_action = (
        wiki_selection_guidance_gap
        and _kis_actions_resolve_wiki_selection_guidance(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_selection_guidance_resolved_by_hold = (
        wiki_selection_guidance_gap
        and not wiki_selection_guidance_resolved_by_action
        and action_count <= 0
        and _kis_hold_resolves_wiki_selection_guidance(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    if wiki_selection_guidance_gap and not (
        wiki_selection_guidance_resolved_by_action
        or wiki_selection_guidance_resolved_by_hold
    ):
        add("unresolved_jue_wiki_selection_guidance", 2)

    unavailable_wiki_context_gap = _kis_prompt_has_unavailable_wiki_context(prompt)
    unavailable_wiki_context_resolved_by_action = (
        unavailable_wiki_context_gap
        and _kis_actions_resolve_unavailable_wiki_context(
            prompt=prompt,
            actions=actions,
        )
    )
    unavailable_wiki_context_resolved_by_hold = (
        unavailable_wiki_context_gap
        and not unavailable_wiki_context_resolved_by_action
        and action_count <= 0
        and _kis_hold_resolves_unavailable_wiki_context(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    if unavailable_wiki_context_gap and not (
        unavailable_wiki_context_resolved_by_action
        or unavailable_wiki_context_resolved_by_hold
    ):
        add("unresolved_jue_wiki_context_gap", 2)

    wiki_action_reference_memory_gap = _kis_prompt_has_wiki_action_reference_memory(
        prompt
    )
    wiki_action_reference_memory_resolved_by_action = (
        wiki_action_reference_memory_gap
        and _kis_actions_resolve_wiki_action_reference_memory(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_action_reference_memory_resolved_by_hold = (
        wiki_action_reference_memory_gap
        and not wiki_action_reference_memory_resolved_by_action
        and action_count <= 0
        and _kis_hold_resolves_wiki_action_reference_memory(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    if wiki_action_reference_memory_gap and not (
        wiki_action_reference_memory_resolved_by_action
        or wiki_action_reference_memory_resolved_by_hold
    ):
        add("unresolved_jue_wiki_action_reference_memory", 2)
    wiki_action_reference_recovery_action_resolved = (
        wiki_action_reference_memory_gap
        and _kis_actions_resolve_wiki_action_reference_recovery(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_action_reference_recovery_hold_resolved = (
        wiki_action_reference_memory_gap
        and not wiki_action_reference_recovery_action_resolved
        and action_count <= 0
        and _kis_hold_resolves_wiki_action_reference_recovery(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )

    applicable_wiki_context = _kis_prompt_has_applicable_wiki_context(prompt)
    wiki_action_reference_allowed_page_ids = _kis_prompt_wiki_reference_page_ids(prompt)
    wiki_action_reference_symbol_page_ids = (
        _kis_prompt_wiki_reference_symbol_page_ids(prompt)
    )
    wiki_action_reference_block_symbol_map = _kis_prompt_block_symbol_map(prompt)
    wiki_action_reference_decision_count = action_count
    if action_count > 0:
        wiki_action_reference_count = _kis_action_wiki_reference_count(
            actions,
            allowed_page_ids=wiki_action_reference_allowed_page_ids,
            required_symbol_page_ids=wiki_action_reference_symbol_page_ids,
            block_symbol_map=wiki_action_reference_block_symbol_map,
        )
        wiki_action_reference_missing_actions = (
            _kis_action_wiki_reference_missing_actions(
                actions,
                allowed_page_ids=wiki_action_reference_allowed_page_ids,
                required_symbol_page_ids=wiki_action_reference_symbol_page_ids,
                block_symbol_map=wiki_action_reference_block_symbol_map,
            )
        )
    elif _kis_hold_decision_has_payload(hold_decision):
        hold_symbol_page_ids = _kis_hold_required_symbol_page_ids(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
        )
        hold_uncovered_target_symbols = _kis_hold_uncovered_target_symbols(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
            allowed_page_ids=wiki_action_reference_allowed_page_ids,
        )
        hold_required_symbol_entries = _kis_required_symbol_page_entries(
            hold_symbol_page_ids
        )
        wiki_action_reference_decision_count = max(
            1,
            len(hold_required_symbol_entries) + len(hold_uncovered_target_symbols),
        )
        wiki_action_reference_count = (
            _kis_hold_wiki_reference_count(
                hold_decision,
                allowed_page_ids=wiki_action_reference_allowed_page_ids,
                required_symbol_page_ids=hold_symbol_page_ids,
            )
            if hold_required_symbol_entries or not hold_uncovered_target_symbols
            else 0
        )
        if hold_required_symbol_entries:
            if wiki_action_reference_count > 0:
                wiki_action_reference_missing_actions = (
                    _kis_hold_wiki_reference_missing_symbol_pages(
                        hold_decision,
                        allowed_page_ids=wiki_action_reference_allowed_page_ids,
                        required_symbol_page_ids=hold_symbol_page_ids,
                    )
                )
            else:
                wiki_action_reference_missing_actions = [{"section": "hold_decision"}]
            wiki_action_reference_missing_actions.extend(
                {
                    "section": "hold_decision",
                    "symbol": symbol,
                }
                for symbol in sorted(hold_uncovered_target_symbols)
            )
        elif hold_uncovered_target_symbols:
            wiki_action_reference_missing_actions = [
                {
                    "section": "hold_decision",
                    "symbol": symbol,
                }
                for symbol in sorted(hold_uncovered_target_symbols)
            ]
        else:
            wiki_action_reference_missing_actions = (
                []
                if wiki_action_reference_count > 0
                else [{"section": "hold_decision"}]
            )
    else:
        wiki_action_reference_count = 0
        wiki_action_reference_missing_actions = []
    wiki_action_reference_ratio = (
        round(wiki_action_reference_count / wiki_action_reference_decision_count, 3)
        if wiki_action_reference_decision_count > 0
        else 0.0
    )
    partial_wiki_action_reference = (
        applicable_wiki_context
        and wiki_action_reference_decision_count > 0
        and 0 < wiki_action_reference_count < wiki_action_reference_decision_count
    )
    complete_wiki_action_reference = (
        wiki_action_reference_decision_count > 0
        and wiki_action_reference_count >= wiki_action_reference_decision_count
    )
    unscoped_wiki_action_reference = (
        not applicable_wiki_context and wiki_action_reference_count > 0
    )
    wiki_action_reference_referenced_page_ids = sorted(
        _kis_wiki_reference_page_ids_from_payload(
            {"actions": actions, "hold_decision": hold_decision}
        )
    )
    wiki_action_reference_unscoped_page_ids = (
        wiki_action_reference_referenced_page_ids[:12]
        if unscoped_wiki_action_reference
        else []
    )
    wiki_action_reference_unscoped_page_omitted_count = (
        max(
            len(wiki_action_reference_referenced_page_ids)
            - len(wiki_action_reference_unscoped_page_ids),
            0,
        )
        if unscoped_wiki_action_reference
        else 0
    )
    if (
        applicable_wiki_context
        and wiki_action_reference_decision_count > 0
        and wiki_action_reference_count <= 0
    ):
        add("missing_jue_wiki_action_reference", 1)
    elif partial_wiki_action_reference:
        add(
            "partial_jue_wiki_action_reference",
            wiki_action_reference_decision_count - wiki_action_reference_count,
        )
    elif unscoped_wiki_action_reference:
        add("unscoped_jue_wiki_action_reference", wiki_action_reference_count)
    wiki_usage_contract_gap = _kis_prompt_has_wiki_usage_contract(prompt)
    wiki_usage_contract_required_terms = _kis_wiki_usage_contract_required_terms(
        prompt
    )
    wiki_usage_contract_decision_count = action_count
    if action_count > 0:
        wiki_usage_contract_resolution_count = (
            _kis_action_wiki_usage_contract_resolution_count(
                actions,
                required_terms=wiki_usage_contract_required_terms,
                allowed_page_ids=wiki_action_reference_allowed_page_ids,
                required_symbol_page_ids=wiki_action_reference_symbol_page_ids,
                block_symbol_map=wiki_action_reference_block_symbol_map,
            )
        )
    elif _kis_hold_decision_has_payload(hold_decision):
        hold_usage_symbol_page_ids = _kis_hold_required_symbol_page_ids(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
        )
        hold_usage_uncovered_target_symbols = _kis_hold_uncovered_target_symbols(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
            allowed_page_ids=wiki_action_reference_allowed_page_ids,
        )
        wiki_usage_contract_required_symbol_entries = _kis_required_symbol_page_entries(
            hold_usage_symbol_page_ids
        )
        wiki_usage_contract_decision_count = max(
            1,
            len(wiki_usage_contract_required_symbol_entries)
            + len(hold_usage_uncovered_target_symbols),
        )
        wiki_usage_contract_resolution_count = (
            (
                _kis_hold_wiki_usage_contract_resolution_count(
                    hold_decision,
                    required_terms=wiki_usage_contract_required_terms,
                    allowed_page_ids=wiki_action_reference_allowed_page_ids,
                    required_symbol_page_ids=hold_usage_symbol_page_ids,
                )
            )
            if wiki_usage_contract_required_symbol_entries
            or not hold_usage_uncovered_target_symbols
            else 0
        )
    else:
        wiki_usage_contract_resolution_count = 0
    wiki_usage_contract_resolution_ratio = (
        round(
            wiki_usage_contract_resolution_count
            / wiki_usage_contract_decision_count,
            3,
        )
        if wiki_usage_contract_decision_count > 0
        else 0.0
    )
    partial_wiki_usage_contract_resolution = (
        wiki_usage_contract_gap
        and wiki_usage_contract_decision_count > 0
        and 0
        < wiki_usage_contract_resolution_count
        < wiki_usage_contract_decision_count
    )
    complete_wiki_usage_contract_resolution = (
        wiki_usage_contract_decision_count > 0
        and wiki_usage_contract_resolution_count
        >= wiki_usage_contract_decision_count
    )
    if (
        wiki_usage_contract_gap
        and wiki_usage_contract_decision_count > 0
        and wiki_usage_contract_resolution_count <= 0
    ):
        add(
            "missing_jue_wiki_usage_contract_resolution",
            wiki_usage_contract_decision_count,
        )
    elif partial_wiki_usage_contract_resolution:
        add(
            "partial_jue_wiki_usage_contract_resolution",
            wiki_usage_contract_decision_count - wiki_usage_contract_resolution_count,
        )
    wiki_action_reference_recovery = _kis_wiki_action_reference_recovery_diagnostics(
        prompt
    )
    wiki_action_reference_recovery_resolution = (
        "action_metadata"
        if wiki_action_reference_recovery_action_resolved
        else "hold_trigger"
        if wiki_action_reference_recovery_hold_resolved
        else ""
    )
    if (
        wiki_action_reference_recovery_resolution
        and _kis_prompt_has_wiki_action_reference_recovery_guidance(prompt)
    ):
        wiki_action_reference_recovery = (
            _kis_resolved_wiki_action_reference_recovery_diagnostics(
                wiki_action_reference_recovery,
                resolution_status=wiki_action_reference_recovery_resolution,
            )
        )
    wiki_action_reference_open_gap_count = _safe_int(
        wiki_action_reference_recovery.get(
            "jue_wiki_action_reference_recovery_open_gap_count"
        )
    )
    wiki_action_reference_recovery_status = str(
        wiki_action_reference_recovery.get(
            "jue_wiki_action_reference_recovery_status"
        )
        or ""
    ).strip().lower()
    wiki_action_reference_recovery_resolution_status = str(
        wiki_action_reference_recovery.get(
            "jue_wiki_action_reference_recovery_latest_resolution_status"
        )
        or ""
    ).strip().lower()
    if (
        wiki_action_reference_open_gap_count > 0
        or wiki_action_reference_recovery_status in {"open_gaps", "unresolved"}
        or wiki_action_reference_recovery_resolution_status == "unresolved"
    ):
        add(
            "unresolved_jue_wiki_action_reference_recovery",
            max(wiki_action_reference_open_gap_count, 1),
        )

    top_blockers = [
        {"tag": tag, "weight": weight}
        for tag, weight in sorted(
            blocker_tags.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ][:8]
    return {
        "version": "kis_manager_diagnostics_v1",
        "action_count": action_count,
        "blocker_tags": blocker_tags,
        "top_blockers": top_blockers,
        "jue_wiki_repair_priority_count": repair_priority_count,
        "jue_wiki_repair_action_batch_count": len(action_batches),
        "jue_wiki_requested_symbol_coverage_status": requested_symbol_coverage.get(
            "status"
        ),
        "jue_wiki_missing_summary_symbols": _normalize_list(
            requested_symbol_coverage.get("missing_summary_symbols")
        ),
        "jue_wiki_prompt_omitted_symbols": _normalize_list(
            requested_symbol_coverage.get("prompt_omitted_symbols")
        ),
        "jue_wiki_attention_status": (
            str(attention_contract.get("status") or "").strip().lower()
            if attention_contract
            else "inactive"
        ),
        "jue_wiki_attention_must_address": _normalize_list(
            attention_contract.get("must_address")
        ),
        "jue_wiki_attention_resolution_status": (
            attention_resolution_status if attention_active else "inactive"
        ),
        "degraded_jue_wiki_effectiveness_count": len(
            degraded_effectiveness_items
        ),
        "degraded_jue_wiki_effectiveness_page_ids": (
            degraded_effectiveness_page_ids
        ),
        "degraded_jue_wiki_effectiveness_resolution_status": (
            degraded_effectiveness_resolution_status
            if degraded_effectiveness_items
            else "inactive"
        ),
        "jue_wiki_memory_card_quality_status": (
            "active" if memory_card_quality_gap else "inactive"
        ),
        "jue_wiki_memory_card_quality_resolution_status": (
            "action_metadata"
            if memory_card_quality_resolved_by_action
            else "hold_trigger"
            if memory_card_quality_resolved_by_hold
            else "response_resolution"
            if memory_card_quality_resolved_by_response
            else "unresolved"
            if memory_card_quality_gap
            else "inactive"
        ),
        "jue_wiki_selection_guidance_status": (
            "active" if wiki_selection_guidance_gap else "inactive"
        ),
        "jue_wiki_selection_guidance_resolution_status": (
            "action_metadata"
            if wiki_selection_guidance_resolved_by_action
            else "hold_trigger"
            if wiki_selection_guidance_resolved_by_hold
            else "unresolved"
            if wiki_selection_guidance_gap
            else "inactive"
        ),
        "jue_wiki_context_gap_status": (
            "active" if unavailable_wiki_context_gap else "inactive"
        ),
        "jue_wiki_context_gap_resolution_status": (
            "action_metadata"
            if unavailable_wiki_context_resolved_by_action
            else "hold_trigger"
            if unavailable_wiki_context_resolved_by_hold
            else "unresolved"
            if unavailable_wiki_context_gap
            else "inactive"
        ),
        "jue_wiki_action_reference_memory_status": (
            "active" if wiki_action_reference_memory_gap else "inactive"
        ),
        "jue_wiki_action_reference_memory_resolution_status": (
            "action_metadata"
            if wiki_action_reference_memory_resolved_by_action
            else "hold_trigger"
            if wiki_action_reference_memory_resolved_by_hold
            else "unresolved"
            if wiki_action_reference_memory_gap
            else "inactive"
        ),
        "jue_wiki_action_reference_status": (
            "unscoped"
            if unscoped_wiki_action_reference
            else "referenced"
            if complete_wiki_action_reference
            else "partial"
            if partial_wiki_action_reference
            else "missing"
            if applicable_wiki_context and wiki_action_reference_decision_count > 0
            else "no_actions"
            if applicable_wiki_context
            else "inactive"
        ),
        "jue_wiki_action_reference_count": wiki_action_reference_count,
        "jue_wiki_action_reference_ratio": wiki_action_reference_ratio,
        "jue_wiki_action_reference_unscoped_page_ids": (
            wiki_action_reference_unscoped_page_ids
        ),
        "jue_wiki_action_reference_unscoped_page_omitted_count": (
            wiki_action_reference_unscoped_page_omitted_count
        ),
        "jue_wiki_action_reference_required_trace_markers": [
            "kis.symbol.",
            "kis.ops.",
            "jue_wiki_action_reference_gap.",
        ],
        "jue_wiki_action_reference_allowed_page_ids": sorted(
            wiki_action_reference_allowed_page_ids
        )[:12],
        "jue_wiki_action_reference_missing_actions": (
            wiki_action_reference_missing_actions
        ),
        "jue_wiki_usage_contract_status": (
            "resolved"
            if complete_wiki_usage_contract_resolution
            else "partial"
            if partial_wiki_usage_contract_resolution
            else "missing"
            if wiki_usage_contract_gap and wiki_usage_contract_decision_count > 0
            else "no_actions"
            if wiki_usage_contract_gap
            else "inactive"
        ),
        "jue_wiki_usage_contract_resolution_count": (
            wiki_usage_contract_resolution_count
        ),
        "jue_wiki_usage_contract_resolution_ratio": (
            wiki_usage_contract_resolution_ratio
        ),
        "jue_wiki_usage_contract_required_terms": (
            wiki_usage_contract_required_terms
        ),
        **wiki_action_reference_recovery,
        **candidate_memory_hint_summary,
        **research_spine_memory_summary,
        **memory_contract_summary,
        "jue_wiki_weak_memory_card_symbols": _normalize_list(
            memory_card_summary.get("weak_symbols")
        ),
    }


def compact_manager_prompt_context(
    prompt: dict[str, Any],
    *,
    response: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
    hold_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact KIS manager context pack with wiki diagnostics."""

    response_payload = response or {}
    action_payload = actions or {}
    hold_payload = hold_decision or {}
    diagnostics = kis_manager_run_diagnostics(
        prompt=prompt if isinstance(prompt, dict) else {},
        response=response_payload,
        actions=action_payload,
        hold_decision=hold_payload,
    )
    if not isinstance(prompt, dict) or not prompt:
        return {"diagnostics": diagnostics} if diagnostics else {}
    context: dict[str, Any] = {}
    for section, list_limit, string_limit in (
        ("jue_wiki_requested_symbol_coverage", 4, 120),
        ("jue_wiki_repair_contract", 4, 120),
        ("jue_wiki_memory_card_quality", 4, 120),
        ("jue_wiki_decision_adjustments", 4, 120),
        ("jue_wiki", 2, 120),
        ("jue_wiki_application", 2, 120),
        ("jue_wiki_selection_observation", 4, 120),
        ("jue_wiki_validation_repair_effectiveness", 4, 120),
        ("jue_wiki_application_coverage", 4, 120),
        ("investment_memory", 2, 120),
        ("research_spine", 3, 24),
        ("market_pulse", 2, 24),
        ("execution_gate", 2, 120),
    ):
        if section not in prompt:
            continue
        context[section] = compact_prompt_section(
            section,
            prompt.get(section),
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if diagnostics:
        context["diagnostics"] = diagnostics
    return context


def normalize_creative_hypothesis_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[\s/-]+", "_", raw)
    return compact if compact in CREATIVE_HYPOTHESIS_TYPES else "contrarian"


def normalize_creative_hypothesis_decision(value: Any) -> str:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[\s/-]+", "_", raw)
    return compact if compact in CREATIVE_HYPOTHESIS_DECISIONS else "watch"


def _confidence(value: Any) -> float:
    return round(min(max(_safe_float(value), 0.0), 1.0), 4)


def sanitize_creative_hypothesis_block(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    symbol = str(value.get("symbol") or "").strip()
    entry_style_raw = str(value.get("entry_style") or "").strip().lower()
    if entry_style_raw in {"", "none", "no", "watch"}:
        entry_style = "none"
    else:
        entry_style = normalize_entry_style(entry_style_raw)
    trigger_price = _safe_float(value.get("entry_trigger_price"))
    row: dict[str, Any] = {
        "symbol": symbol if _is_symbol(symbol) else "",
        "qty": max(_safe_int(value.get("qty")), 0),
        "entry_style": entry_style,
        "entry_trigger_price": trigger_price or None,
        "entry_trigger_operator": normalize_entry_trigger_operator(
            value.get("entry_trigger_operator"),
            trigger_price=trigger_price,
        ),
        "target_price": _safe_float(value.get("target_price")) or None,
        "stop_price": _safe_float(value.get("stop_price")) or None,
        "horizon": normalize_horizon(value.get("horizon")),
        "reason": _clean_text(value.get("reason"), limit=700),
    }
    return {key: child for key, child in row.items() if child not in {"", None}}


def sanitize_creative_hypothesis(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    symbols = clean_symbol_list(value.get("symbols"), max_items=8)
    proposed_block = sanitize_creative_hypothesis_block(value.get("proposed_block"))
    block_symbol = str(proposed_block.get("symbol") or "")
    if block_symbol and block_symbol not in symbols:
        symbols.append(block_symbol)
    title = _clean_text(value.get("title"), limit=220)
    summary = _clean_text(value.get("summary"), limit=900)
    if not title and not summary and not symbols:
        return {}
    return {
        "hypothesis_id": _clean_text(value.get("hypothesis_id"), limit=80),
        "hypothesis_type": normalize_creative_hypothesis_type(
            value.get("hypothesis_type")
        ),
        "title": title,
        "summary": summary,
        "symbols": symbols,
        "sector": _clean_text(value.get("sector"), limit=120),
        "horizon": normalize_horizon(value.get("horizon")),
        "decision": normalize_creative_hypothesis_decision(value.get("decision")),
        "confidence": _confidence(value.get("confidence")),
        "evidence": _clean_text_list(value.get("evidence"), limit=500, max_items=8),
        "risks": _clean_text_list(value.get("risks"), limit=500, max_items=8),
        "invalidation": _clean_text(value.get("invalidation"), limit=700),
        "proposed_block": proposed_block,
        "next_check": _clean_text(value.get("next_check"), limit=300),
    }


def sanitize_creative_hypotheses(
    value: Any,
    *,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _normalize_list(value):
        row = sanitize_creative_hypothesis(item)
        if row:
            rows.append(row)
        if len(rows) >= max_items:
            break
    return rows


def compact_etf_prompt_value(
    value: Any,
    *,
    list_limit: int = 6,
    string_limit: int = 240,
) -> Any:
    return compact_prompt_value(
        value,
        list_limit=list_limit,
        string_limit=string_limit,
    )


def compact_etf_prompt_fields(value: Any, allowed_keys: set[str]) -> dict[str, Any]:
    return compact_prompt_fields(value, allowed_keys)


def public_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        public: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized == "content_md":
                public["preview"] = _clean_text(child, limit=220)
                continue
            if normalized in PROMPT_DROPPED_KEYS:
                continue
            public[str(key)] = public_prompt_payload(child)
        return public
    if isinstance(value, list):
        return [public_prompt_payload(item) for item in value]
    return value


def build_prompt_strategy_payload(
    strategy_payload: dict[str, Any],
    *,
    research_spine: dict[str, Any] | None = None,
    max_symbols: int = 12,
) -> dict[str, Any]:
    list_limit = max(_safe_int(max_symbols), 1)
    packets = (
        _normalize_list(research_spine.get("packets"))
        if isinstance(research_spine, dict)
        else []
    )
    packet_by_symbol = {
        str(row.get("symbol") or ""): row
        for row in packets
        if isinstance(row, dict) and _is_symbol(row.get("symbol"))
    }
    top_symbols: list[dict[str, Any]] = []
    for row in _normalize_list(strategy_payload.get("candidates"))[:list_limit]:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        packet = packet_by_symbol.get(symbol) or {}
        quality = packet.get("quality") if isinstance(packet.get("quality"), dict) else {}
        top_row = {
            "symbol": symbol,
            "name": _clean_text(row.get("name"), limit=80),
            "asset_class": str(row.get("asset_class") or "equity"),
            "buckets": _normalize_list(packet.get("buckets"))[:4],
            "score": _safe_int(row.get("score")),
            "confidence": _safe_int(row.get("confidence")),
            "decision_use": str(quality.get("decision_use") or ""),
            "warnings": _normalize_list(quality.get("warnings"))[:3],
        }
        memory_hint = row.get("memory_hint")
        if isinstance(memory_hint, dict):
            compact_hint = compact_etf_prompt_value(
                public_prompt_payload(memory_hint),
                list_limit=4,
                string_limit=180,
            )
            if isinstance(compact_hint, dict) and compact_hint:
                top_row["memory_hint"] = compact_hint
        top_symbols.append(top_row)
    if not top_symbols:
        for row in packets[:list_limit]:
            if not isinstance(row, dict):
                continue
            quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
            top_symbols.append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "name": _clean_text(row.get("name"), limit=80),
                    "asset_class": str(row.get("asset_class") or ""),
                    "buckets": _normalize_list(row.get("buckets"))[:4],
                    "score": _safe_int(row.get("score")),
                    "confidence": _safe_int(row.get("confidence")),
                    "decision_use": str(quality.get("decision_use") or ""),
                    "warnings": _normalize_list(quality.get("warnings"))[:3],
                }
            )
    source_rows = compact_etf_prompt_value(
        public_prompt_payload(strategy_payload.get("sources") or []),
        list_limit=8,
        string_limit=180,
    )
    return {
        "status": str(strategy_payload.get("status") or "unknown"),
        "mode": "reference_compact",
        "score_method_version": str(strategy_payload.get("score_method_version") or ""),
        "candidate_count": _safe_int(strategy_payload.get("candidate_count"))
        or len(_normalize_list(strategy_payload.get("candidates"))),
        "exclusion_count": len(_normalize_list(strategy_payload.get("exclusions"))),
        "top_symbols": top_symbols,
        "sources": source_rows if isinstance(source_rows, list) else [],
        "methodology": [
            _clean_text(row, limit=160)
            for row in _normalize_list(strategy_payload.get("methodology"))[:3]
        ],
        "note": "Detailed per-symbol evidence is in research_spine.packets.",
    }


def _kis_manager_policy() -> dict[str, Any]:
    return {
        "block_unit": "Each block is independent even when symbols overlap.",
        "llm_permissions": "full_block_management",
        "execution_guard": "orders are validated by a rule/gate layer",
        "memory_guard": (
            "investment memory guides live block trading; hard safety gates override it"
        ),
        "memory_scope_policy": (
            "Treat investment_memory.scoped_memory.core and local KIS "
            "memories as primary. Treat translated Binance memories only "
            "as cross-venue process lessons, not as direct Korean-equity "
            "evidence or direct Korean-equity rules. "
            "investment_memory.translated_policy_context contains translated "
            "lessons only; use it to ask better checks, not to override KIS "
            "research, account, quote, or safety-gate evidence. Read "
            "available_count, omitted_count, and source_scope_counts as "
            "coverage/selection metadata: visible translated lessons are only "
            "a sample when omitted_count is positive, so do not overfit the "
            "visible items."
        ),
        "period_memory_coverage_policy": {
            "source": "investment_memory.period_memory_coverage",
            "applies_to_scope": "kis",
            "missing_coverage_decision_effect": [
                (
                    "record missing weekly/monthly review or replay in "
                    "hold_decision.data_gaps"
                ),
                (
                    "reduce action confidence or explain why current live "
                    "evidence overrides the gap"
                ),
                (
                    "include the gap in risk_note or action metadata for "
                    "affected block actions"
                ),
                (
                    "when policy_rules or validation_repair require "
                    "metadata_contract_audit_resolution, populate "
                    "metadata_contract_audit_resolution on the affected action"
                ),
                (
                    "when validation_repair provides metadata_contract_repair_note, "
                    "copy that note into metadata_contract_repair_note on the "
                    "affected action"
                ),
            ],
            "confidence_rule": (
                "Do not use absent period memory as proof that a setup is clean."
            ),
            "override_rule": (
                "A trade may still proceed when live quote, account, research, "
                "valuation, and risk evidence are strong; in that case name the "
                "coverage gap and the evidence that overrode it."
            ),
        },
        "jue_wiki_selection_memory_policy": {
            "source": "investment_memory.jue_wiki_selection_memory",
            "applies_to_scope": "kis",
            "freshness_guidance_effect": [
                "refresh or cross-check selected Wiki pages before size increase",
                (
                    "record jue_wiki_selection_resolution or "
                    "jue_wiki_freshness_cross_check on affected actions"
                ),
                (
                    "use fresh_jue_wiki_context, selection_audit_resolution, "
                    "and live_cross_check as required evidence"
                ),
            ],
            "confidence_rule": (
                "Do not treat stale selected Wiki pages as conviction evidence "
                "until live quote, account, research, and selection audit evidence "
                "cross-check the memory."
            ),
        },
        "jue_wiki_context_gap_memory_policy": {
            "source": "investment_memory.jue_wiki_context_gap_memory",
            "applies_to_scope": "kis",
            "gap_guidance_effect": [
                "verify Wiki context availability before high-confidence action",
                (
                    "record jue_wiki_context_gap on affected actions when "
                    "Wiki remains unavailable"
                ),
                "use fresh_jue_wiki_context or live_cross_check as required evidence",
            ],
            "confidence_rule": (
                "Repeated Wiki context gaps are operational caution signals. "
                "Do not raise confidence from memory unless current Wiki context "
                "is fresh or the action records a concrete live cross-check."
            ),
        },
        "jue_wiki_action_reference_memory_policy": {
            "source": "investment_memory.jue_wiki_action_reference_memory",
            "applies_to_scope": "kis",
            "reference_guidance_effect": [
                (
                    "attach jue_wiki_freshness_cross_check or "
                    "jue_wiki_selection_resolution when selected Wiki memory "
                    "influences an action"
                ),
                (
                    "if an action does not use Wiki memory, record the "
                    "live/research basis that overrode Wiki memory"
                ),
                (
                    "use live_cross_check before allowing high confidence "
                    "without an action-level Wiki reference"
                ),
            ],
            "confidence_rule": (
                "Repeated missing Wiki action references reduce audit confidence. "
                "Do not claim memory-backed conviction unless the affected action "
                "names the Wiki cross-check, selection resolution, or explicit "
                "non-Wiki evidence basis."
            ),
        },
        "jue_wiki_usage_contract_policy": {
            "source": "jue_wiki_application.trust_profile",
            "memory_source": "investment_memory.jue_wiki_usage_contract_memory",
            "applies_to_scope": "kis",
            "standalone_trade_authority": False,
            "required_action_metadata": "jue_wiki_usage_contract_resolution",
            "required_cross_checks": [
                "live_quote",
                "account_state",
                "risk_gate",
                "fresh_research_conflicts",
                "current_price_structure",
            ],
            "memory_guidance_effect": [
                (
                    "apply usage-contract reflections as action-level evidence "
                    "requirements, not as standalone trade authority"
                ),
                (
                    "when memory requires jue_wiki_usage_contract_resolution, "
                    "every affected action must name the live cross-check that "
                    "confirmed, reduced, or overrode the Wiki memory prior"
                ),
            ],
            "decision_effect": (
                "Selected Wiki pages are memory priors, not standalone trade "
                "authority. When Wiki trust_profile or usage_contract influences "
                "an action, record how live quote, account, risk gate, fresh "
                "research, and price structure confirmed or overrode that memory."
            ),
        },
        "jue_wiki_effectiveness_policy": (
            "Use Jue Wiki page effectiveness as a trading-design prior. "
            "active Wiki pages may raise confidence only after live quote, "
            "account, and risk gates agree. probe Wiki pages support small "
            "probe or waiting-entry designs, not oversized conviction. "
            "degraded Wiki pages are repair/probe evidence: do not use them "
            "as standalone entry support, and if they still influence a block "
            "then name the cross-check, sizing reduction, or repair resolution."
        ),
        "account_value_guard": (
            "Use total_value_krw only for portfolio scale, weights, and "
            "risk context. Use orderable_cash_krw for buy sizing and "
            "available_qty/unallocated_qty for sell or adoption sizing."
        ),
        "existing_position_adoption": (
            "Use adopt_existing_blocks to assign unallocated account positions "
            "to block ledger entries without sending buy orders. User-bought "
            "or manually added holdings are special-watch positions: run and "
            "use pre_adoption_symbol_analysis before deciding horizon/target/stop. "
            "Do not default them to short-term merely because they are new; "
            "default toward mid unless price action, risk, or a user directive "
            "clearly supports another horizon."
        ),
        "user_directives": (
            "Block user directives are high-priority soft instructions for "
            "horizon, intent, and management mode. Apply them unless safety "
            "gates or current risk clearly conflict."
        ),
        "allowed_actions": [
            "adopt_existing_blocks",
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
        ],
        "waiting_entry_blocks": (
            "For pullback or breakout plans that should not buy now, use "
            "create_blocks with entry_style=wait_for_price, "
            "entry_trigger_price, and entry_trigger_operator. The rule "
            "executor watches the trigger without extra LLM calls."
        ),
    }


def _kis_validation_repair_response_contract() -> dict[str, Any]:
    return {
        "version": "kis_validation_repair_response_contract_v1",
        "required_when": (
            "validation_repair has repair_item_count/constraint_count or "
            "jue_wiki_repair_contract has top_priorities/action_batches or "
            "jue_wiki_validation_repair_contract requires resolution or "
            "jue_wiki_contract_feedback_gap is present or "
            "jue_wiki_memory_card_quality has an active action_plan or "
            "jue_wiki pages carry degraded effectiveness"
        ),
        "core_rule": (
            "Validation repair and degraded Wiki memory are repair work, not "
            "blanket no-action reasons. Resolve them into candidate-level "
            "execution checks, smaller waiting/probe block designs, explicit "
            "target/stop corrections, or precise reject conditions. When an "
            "action proceeds under active jue_wiki_repair_contract pressure, "
            "include jue_wiki_repair_pressure or jue_wiki_repair_resolution on "
            "the action so the block record shows how stale/omitted Wiki repair "
            "work affected sizing, horizon, or evidence requirements. When an "
            "action uses degraded Wiki effectiveness, the action metadata itself "
            "must include jue_wiki_repair_resolution or jue_wiki_repair_pressure; "
            "a response-only repair note is not enough. When "
            "jue_wiki_memory_card_quality flags thin Wiki memory cards, include "
            "jue_wiki_memory_card_quality or jue_wiki_memory_card_cross_check on "
            "the action, or record a concrete hold_decision trigger/data gap for "
            "live research cross-check before high-confidence judgment."
        ),
        "accepted_resolutions": [
            "create a small executable wait_for_price or sized probe block",
            "update an existing block's target/stop/what_would_change_my_mind",
            "name the exact candidate reject reason and missing evidence",
            "defer only because a server safety gate blocks execution",
        ],
        "hold_only_contract": (
            "If all action arrays are empty, hold_decision must name the top "
            "candidate symbols reviewed, the repair discipline or degraded page "
            "addressed, and the next concrete price/condition trigger or data gap."
        ),
        "blanket_hold_allowed": False,
        "safety_gates_still_override": True,
    }


def _kis_wiki_action_metadata_output_schema(action_name: str) -> dict[str, str]:
    action_label = action_name.replace("_", " ")
    return {
        "jue_wiki_repair_pressure": (
            "optional string; how active wiki repair pressure, omitted repair "
            f"queues, or degraded Wiki effectiveness affected this {action_label}'s "
            "confidence, sizing, horizon, or evidence requirements"
        ),
        "jue_wiki_repair_resolution": (
            "optional string; required action metadata when this action uses "
            "degraded Wiki effectiveness; explain the live quote/account/risk "
            "cross-check and sizing or trigger adjustment, because response-only "
            "repair notes do not resolve degraded Wiki effectiveness for actions"
        ),
        "jue_wiki_memory_card_quality": (
            "optional string; how thin Wiki memory card quality affected this "
            f"{action_label}'s confidence, sizing, horizon, or evidence requirements"
        ),
        "jue_wiki_memory_card_cross_check": (
            "optional string; live quote/account/valuation/research cross-check "
            "used before trusting thin Wiki memory cards"
        ),
        "jue_wiki_selection_resolution": (
            "optional string; required when "
            "investment_memory.jue_wiki_selection_memory.application_guidance "
            "requires freshness repair and this action uses or sizes from the "
            "selected Wiki context; cite fresh_jue_wiki_context, "
            "selection_audit_resolution, or live_cross_check"
        ),
        "jue_wiki_freshness_cross_check": (
            "optional string; live quote/account/research evidence used to "
            "cross-check stale selected Wiki pages before size increase"
        ),
        "jue_wiki_context_gap": (
            "optional string; required when investment_memory.jue_wiki or "
            "jue_wiki is disabled/error/unavailable and this action proceeds; "
            "explain the missing Wiki context and the live_cross_check, "
            "research_spine, valuation, or quote evidence used instead"
        ),
        "jue_wiki_reference_basis": (
            "optional string; required when "
            "investment_memory.jue_wiki_action_reference_memory.application_guidance "
            "requires wiki-reference repair and this action proceeds; either cite "
            "the selected Wiki cross-check or explicitly name the live/research "
            "basis that overrode Wiki memory"
        ),
        "jue_wiki_usage_contract_resolution": (
            "optional string; required when "
            "jue_wiki_application.trust_profile.usage_contract or "
            "investment_memory.jue_wiki_usage_contract_memory.application_guidance "
            f"requires usage-contract resolution for this {action_label}; state "
            "that Wiki memory has no standalone trade authority and name the "
            "live_quote, account_state, risk_gate, fresh_research_conflicts, or "
            "current_price_structure cross-check that confirmed, reduced, or "
            "overrode the Wiki usage contract"
        ),
        "period_memory_coverage_gap": (
            "optional string; required when investment_memory.period_memory_coverage "
            "shows missing weekly/monthly review or replay for this "
            f"{action_label}; describe the gap and confidence/risk effect"
        ),
        "period_memory_override_reason": (
            "optional string; required when this action proceeds despite a period "
            "memory coverage gap; explain why current live evidence overrides the gap"
        ),
        "metadata_contract_audit_resolution": (
            "optional string; required when validation repair or policy_rules mention "
            "metadata_contract_audit_resolution; explain how this action resolves, "
            "defers, or compensates for the period memory metadata contract audit gap"
        ),
        "metadata_contract_repair_note": (
            "optional string; required when validation_repair.block_design_constraints "
            "provide metadata_contract_repair_note; copy the compact repair note so "
            "reflection can verify that this action followed the memory contract repair"
        ),
    }


def _kis_manager_output_schema(
    decision_metadata_output_schema: dict[str, Any],
) -> dict[str, Any]:
    return {
        "adopt_existing_blocks": [
            {
                "symbol": "6-digit existing holding",
                "qty": "integer <= unallocated account quantity",
                "target_price": "number",
                "stop_price": "number",
                "horizon": "short|mid|long|core_etf",
                "allocation_reason": "why this block improves portfolio balance",
                "thesis": "string",
                "confidence": "0.0-1.0",
                "risk_note": "string",
                "entry_quality": (
                    "optional string; "
                    "pullback|extended_momentum|fair_value_pullback|momentum_only"
                ),
                "chase_risk": "optional string; low|elevated|high",
                "price_location": (
                    "optional string; pullback|near_support|near_20d_high|upper_band"
                ),
                "pullback_confirmed": "optional boolean",
                "entry_quality_score": "optional number 0-100",
                **decision_metadata_output_schema,
                **_kis_wiki_action_metadata_output_schema(
                    "adopt_existing_blocks"
                ),
            }
        ],
        "create_blocks": [
            {
                "symbol": "6-digit",
                "qty": "integer",
                "target_price": "number",
                "stop_price": "number",
                "entry_style": "aggressive_limit|wait_for_price",
                "entry_trigger_price": (
                    "number required when entry_style=wait_for_price"
                ),
                "entry_trigger_operator": (
                    "lte|gte required when entry_style=wait_for_price"
                ),
                "horizon": "short|mid|long|core_etf",
                "allocation_reason": "why this block improves portfolio balance",
                "thesis": "string",
                "confidence": "0.0-1.0",
                "risk_note": "string",
                **decision_metadata_output_schema,
                **_kis_wiki_action_metadata_output_schema("create_blocks"),
            }
        ],
        "update_blocks": [
            {
                "block_id": "string",
                "target_price": "number",
                "stop_price": "number",
                "reason": "string",
                **decision_metadata_output_schema,
                **_kis_wiki_action_metadata_output_schema("update_blocks"),
            }
        ],
        "close_blocks": [
            {
                "block_id": "string",
                "reason": "string",
                **decision_metadata_output_schema,
                **_kis_wiki_action_metadata_output_schema("close_blocks"),
            }
        ],
        "pause_blocks": [
            {
                "block_id": "string",
                "reason": "string",
                **decision_metadata_output_schema,
                **_kis_wiki_action_metadata_output_schema("pause_blocks"),
            }
        ],
        "hold_decision": {
            "summary": "string",
            "reasons": ["string"],
            "watch_symbols": ["6-digit symbol"],
            "long_watch_symbols": ["6-digit symbol"],
            "next_triggers": [
                {
                    "symbol": "6-digit symbol",
                    "condition": "string",
                    "price": "optional number",
                    "horizon": "short|mid|long|core_etf",
                    "reason": "string",
                }
            ],
            "data_gaps": ["string"],
            "risk_notes": ["string"],
            "horizon_notes": {
                "short": ["string"],
                "mid": ["string"],
                "long": ["string"],
                "core_etf": ["string"],
                "cash": ["string"],
            },
        },
        "validation_repair_resolution": {
            "required": (
                "mandatory whenever validation_repair has repair_item_count, "
                "constraint_count, jue_wiki_repair_contract has top_priorities "
                "or action_batches, "
                "jue_wiki_validation_repair_contract requires resolution, or "
                "jue_wiki_contract_feedback_gap is present, or "
                "jue_wiki_memory_card_quality has an active action_plan, or "
                "jue_wiki pages carry degraded effectiveness"
            ),
            "resolved_candidates": [
                {
                    "symbol": "6-digit symbol",
                    "resolution": (
                        "small_waiting_block|one_share_probe|updated_price_geometry|"
                        "candidate_rejected|safety_gate_defer"
                    ),
                    "horizon": "short|mid|long|core_etf",
                    "next_trigger": "price/volume/regime condition",
                    "evidence_gap": "precise missing evidence, if rejected",
                    "memory_contract": (
                        "required when validation_repair."
                        "memory_contract_resolution_required=true"
                    ),
                    "memory_contract_error": (
                        "required when validation_repair."
                        "memory_contract_resolution_required=true"
                    ),
                    "memory_contract_resolution": (
                        "required when validation_repair."
                        "memory_contract_resolution_required=true; cite_memory_and_apply|"
                        "reject_memory_with_reason|wait_until_memory_refresh|"
                        "safety_gate_defer_with_contract_note plus concise evidence"
                    ),
                }
            ],
            "blanket_hold_allowed": False,
        },
        "creative_hypotheses": [
            {
                "hypothesis_id": "string",
                "hypothesis_type": (
                    "leader_pullback|second_rank|next_sector|missed_upside|"
                    "etf_rotation|contrarian"
                ),
                "title": "string",
                "summary": "string",
                "symbols": ["6-digit symbol"],
                "sector": "string",
                "horizon": "short|mid|long|core_etf",
                "decision": "create_wait_block|create_now_block|watch|reject",
                "confidence": "0.0-1.0",
                "evidence": ["string"],
                "risks": ["string"],
                "invalidation": "string",
                "proposed_block": {
                    "symbol": "6-digit symbol",
                    "qty": "integer",
                    "entry_style": "wait_for_price|aggressive_limit|none",
                    "entry_trigger_price": "optional number",
                    "entry_trigger_operator": "lte|gte",
                    "target_price": "optional number",
                    "stop_price": "optional number",
                    "horizon": "short|mid|long|core_etf",
                    "reason": "string",
                },
                "next_check": "string",
            }
        ],
    }


def build_kis_manager_prompt_payload(
    *,
    clock: dict[str, Any],
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    block_backlog_summary: dict[str, Any],
    quotes: list[dict[str, Any]],
    pre_adoption_symbol_analysis: dict[str, Any],
    allocation: dict[str, Any],
    portfolio_balance: dict[str, Any],
    etf_universe: list[dict[str, Any]],
    etf_research: dict[str, Any],
    recent_events: list[dict[str, Any]],
    decision_packet_v2: dict[str, Any],
    decision_lifecycle_v3: dict[str, Any],
    decision_packet: dict[str, Any],
    candidate_policy_impacts: dict[str, Any],
    validation_repair: dict[str, Any],
    execution_gate: dict[str, Any],
    aggressive_opportunities: dict[str, Any] | None = None,
    direct_daily_discovery: dict[str, Any] | None,
    user_directives: list[dict[str, Any]],
    strategy: dict[str, Any],
    research_spine: dict[str, Any],
    market_judgment: dict[str, Any],
    market_pulse: dict[str, Any],
    missed_upside_reviews: list[dict[str, Any]],
    investment_memory: dict[str, Any],
    policy_rule_evaluation: dict[str, Any],
    live_authority: dict[str, Any],
    kr_pattern_lab: dict[str, Any],
    language_policy: dict[str, Any],
    jue_workflow: dict[str, Any],
    trading_playbook: dict[str, Any],
    untrusted_data_boundary: dict[str, Any],
    decision_metadata_output_schema: dict[str, Any],
) -> dict[str, Any]:
    aggressive_opportunities = (
        aggressive_opportunities if isinstance(aggressive_opportunities, dict) else {}
    )
    decision_bundle = build_canonical_decision_prompt_bundle(
        target_scope="kis",
        decision_packet_v2=decision_packet_v2,
        legacy_decision_packet=decision_packet,
        base_inputs=[
            "account",
            "blocks",
            "quotes",
            "execution_gate",
            "strategy",
            "research_spine",
            "investment_memory",
        ],
        lifecycle_packet_key="decision_lifecycle_v3",
        extra_inputs=[
            "candidate_policy_impacts",
            "validation_repair",
            "aggressive_opportunities",
            "live_authority",
            "kr_pattern_lab",
            "trading_playbook",
            "missed_upside_reviews",
        ],
    )
    decision_inputs = list(decision_bundle["decision_inputs"])
    has_candidate_memory_hints = bool(
        _kis_candidate_memory_hint_items({"strategy": strategy})
    )
    if has_candidate_memory_hints and "candidate_memory_hint_policy" not in decision_inputs:
        decision_inputs.append("candidate_memory_hint_policy")
    if direct_daily_discovery and "daily_discovery" not in decision_inputs:
        decision_inputs.append("daily_discovery")
    if isinstance(aggressive_opportunities, dict) and aggressive_opportunities.get(
        "candidates"
    ) and "aggressive_opportunities" not in decision_inputs:
        decision_inputs.append("aggressive_opportunities")
    if "validation_repair_response_contract" not in decision_inputs:
        decision_inputs.append("validation_repair_response_contract")
    opportunity_research_brief = build_opportunity_research_brief(
        daily_discovery=direct_daily_discovery,
        research_spine=research_spine,
        aggressive_opportunities=aggressive_opportunities,
        market_pulse=market_pulse,
    )
    if (
        opportunity_research_brief.get("status") == "ok"
        and "opportunity_research_brief" not in decision_inputs
    ):
        decision_inputs.append("opportunity_research_brief")

    prompt: dict[str, Any] = {
        "task": "Manage independent KIS stock trading blocks. Return JSON only.",
        "required_decision_skills": [
            "block_manager",
            "risk_manager",
            "reflection",
        ],
        "jue_workflow": _kis_workflow_with_core_contracts(jue_workflow),
        "language_policy": language_policy,
        "trading_playbook": trading_playbook,
        "policy": _kis_manager_policy(),
        "clock": clock,
        "account": account,
        "blocks": blocks,
        "block_backlog_summary": block_backlog_summary,
        "quotes": quotes,
        "pre_adoption_symbol_analysis": pre_adoption_symbol_analysis,
        "allocation": allocation,
        "portfolio_balance": portfolio_balance,
        "etf_universe": etf_universe,
        "etf_research": etf_research,
        "recent_events": recent_events,
        "canonical_decision_packet": decision_bundle["canonical_decision_packet"],
        "decision_packet_v2": decision_packet_v2,
        "decision_lifecycle_v3": decision_lifecycle_v3,
        "decision_packet": decision_packet,
        "candidate_policy_impacts": candidate_policy_impacts,
        "validation_repair": validation_repair,
        "validation_repair_response_contract": (
            _kis_validation_repair_response_contract()
        ),
        "execution_gate": execution_gate,
        "aggressive_opportunities": aggressive_opportunities,
        "decision_packet_policy": decision_bundle["decision_packet_policy"],
        "untrusted_data_boundary": untrusted_data_boundary,
        "decision_inputs": decision_inputs,
        "user_directives": user_directives,
        "strategy": strategy,
        "candidate_memory_hint_policy": {
            "required": has_candidate_memory_hints,
            "action_contract": "cite_or_reject_candidate_memory_hint",
            "sources": [
                "symbol_memory",
                "symbol_analysis_memory",
                "scoped_local_memory",
                "scoped_translated_memory",
            ],
            "instruction": (
                "For every KIS candidate with memory_hint, either cite the hint in "
                "the created/updated block thesis, risk_note, or metadata, or explain "
                "in hold_decision why live evidence overrides or rejects the hint. "
                "Do not silently ignore candidate memory."
            ),
        },
        "research_spine_policy": {
            "role": "primary_research_context",
            "instruction": (
                "Use research_spine.packets before raw strategy or RAG fields. "
                "Packets marked quality.decision_use=caution can still inspire "
                "hypotheses, but require explicit validation or waiting-entry "
                "structure before becoming live blocks."
            ),
            "bucket_review": (
                "Review owned_symbols, pre_surge, ETF buckets, equity buckets, and "
                "daily_discovery. Do not let a strong ETF list hide individual "
                "equity opportunities or user-held symbols."
            ),
            "memory_application": {
                "required": True,
                "sources": [
                    "symbol_memory",
                    "symbol_analysis_memory",
                    "live_context",
                    "block_state",
                    "quote",
                ],
                "action_contract": "cite_or_reject_research_spine_memory",
                "instruction": (
                    "When a packet has symbol_memory, symbol_analysis_memory, "
                    "live_context, block_state, or quote evidence, explicitly use it "
                    "in the candidate review. If the memory conflicts with a trade "
                    "idea, reject or downsize the idea and record the conflict in "
                    "risk_note, hold_decision, or action metadata. Do not ignore "
                    "owned-symbol live_context when updating, closing, or adding "
                    "another block."
                ),
            },
            "contract": "New blocks must satisfy research_spine.contract.block_requirements.",
        },
        "opportunity_research_brief": opportunity_research_brief,
        "research_spine": research_spine,
        "market_judgment": market_judgment,
        "market_pulse": market_pulse,
        "missed_upside_reviews": missed_upside_reviews,
        "investment_memory": investment_memory,
        "policy_rules": {
            "mode": "versioned_policy_as_data",
            "hard_filters": False,
            "evaluation": policy_rule_evaluation,
            "instruction": (
                "Use active policy rule effects as sizing, confirmation, "
                "target/stop, and risk-note adjustments only. Do not bypass "
                "cash, holdings, duplicate-order, or kill-switch gates."
            ),
        },
        "live_authority": live_authority,
        "live_authority_policy": {
            "role": "live outcome authority gate",
            "instruction": (
                "Use live_authority to calibrate aggression. A restricted or "
                "observe_only grade means Jue may still study and stage "
                "waiting blocks, but should avoid expanding size or frequency "
                "until realized live evidence improves. A scale_candidate "
                "grade allows more conviction only when the current thesis, "
                "price location, and safety gates also agree."
            ),
            "hard_filters": False,
            "safety_gates_still_override": True,
        },
        "exploration_budget_policy": {
            "role": "active profit-seeking exploration lane",
            "instruction": (
                "Jue must actively search for asymmetric profit opportunities. "
                "When evidence is promising but not yet strong enough for a "
                "full block, prefer a structured wait_for_price block, a "
                "small executable scout block, or a creative_hypotheses record over "
                "silent holding. Do not treat recent losses or weak confidence "
                "as a blanket reason to stop exploring."
            ),
            "preferred_actions": [
                "wait_for_price block near support or pullback reclaim",
                "small executable scout block when live_authority allows probe override",
                "separate short/mid/long blocks for different theses on the same symbol",
                "creative hypothesis with next trigger when execution is not ready",
            ],
            "no_action_contract": (
                "If create/update/close/pause/adopt actions are all empty while "
                "aggressive_opportunities has candidates, hold_decision must "
                "explicitly name the top rejected candidates and the exact "
                "missing execution condition."
            ),
            "safety_gates_still_override": True,
            "hard_filters": False,
        },
        "cost_feasibility_policy": {
            "role": "per-block execution quality check",
            "computed_by": "HERMES after Jue proposes or adopts a block",
            "instruction": (
                "Design entries, targets, stops, and horizons so estimated "
                "net_target_profit_after_cost_krw stays positive and "
                "target_cost_multiple is comfortably above 1. Thin short "
                "targets should become better wait_for_price entries, wider "
                "targets, longer horizon blocks, or no new block."
            ),
            "hard_filters": False,
        },
        "kr_pattern_lab": kr_pattern_lab,
        "kr_pattern_lab_policy": {
            "role": "validated Korean-equity pattern prior",
            "instruction": (
                "Use kr_pattern_lab.active_sets as statistically validated "
                "positive priors and rejected_sets as caution priors. Do not "
                "treat replay-derived source_types as direct live proof; "
                "confirm with current quote path, research_spine, account "
                "allocation, and live_authority before increasing size."
            ),
            "hard_filters": False,
            "safety_gates_still_override": True,
        },
        "output_schema": _kis_manager_output_schema(
            decision_metadata_output_schema
        ),
    }
    if direct_daily_discovery:
        prompt["daily_discovery"] = direct_daily_discovery
    return prompt


def compact_etf_universe_rows(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _normalize_list(compact_etf_prompt_value(public_prompt_payload(value)))[
        : max(int(limit), 0)
    ]:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                key: row.get(key)
                for key in ("symbol", "name", "category")
                if row.get(key) not in (None, "", [], {})
            }
        )
    return rows


def compact_prompt_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    safe_payload: dict[str, Any] = {}
    for key in PROMPT_EVENT_PAYLOAD_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, (int, float, bool)):
            safe_payload[key] = value
        else:
            safe_payload[key] = _clean_text(value, limit=120)
    return {
        "id": row.get("id"),
        "block_id": _clean_text(
            row.get("block_id") or safe_payload.get("block_id"),
            limit=80,
        ),
        "event_type": _clean_text(row.get("event_type"), limit=80),
        "message": _clean_text(row.get("message"), limit=180),
        "payload": safe_payload,
        "created_at": _clean_text(row.get("created_at"), limit=80),
    }


def compact_prompt_events(
    rows: list[dict[str, Any]],
    *,
    limit: int = 80,
) -> list[dict[str, Any]]:
    return [
        compact_prompt_event(row)
        for row in list(rows or [])[: max(int(limit), 1)]
        if isinstance(row, dict)
    ]


def compact_daily_discovery_prompt(
    value: dict[str, Any] | None,
    *,
    item_limit: int = 20,
    block_candidate_limit: int = 12,
    pre_surge_candidate_limit: int = 16,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    def compact_row(
        row: dict[str, Any],
        *,
        include_pre_surge: bool = True,
    ) -> dict[str, Any]:
        row = enrich_discovery_result(row)
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        pre_surge = (
            row.get("pre_surge") if isinstance(row.get("pre_surge"), dict) else {}
        )
        payload: dict[str, Any] = {
            "symbol": _clean_text(row.get("symbol"), limit=16),
            "name": _clean_text(row.get("name") or analysis.get("name"), limit=80),
            "market": _clean_text(row.get("market"), limit=20),
            "score": row.get("score"),
            "stance": _clean_text(row.get("stance") or analysis.get("stance"), limit=80),
            "confidence": row.get("confidence", analysis.get("confidence")),
            "summary": _clean_text(
                row.get("summary") or analysis.get("summary"),
                limit=260,
            ),
        }
        if include_pre_surge and pre_surge:
            payload["pre_surge"] = compact_prompt_value(
                pre_surge,
                list_limit=5,
                string_limit=140,
            )
        return {key: item for key, item in payload.items() if item not in ("", None)}

    items = [
        compact_row(row)
        for row in list(value.get("items") or [])[: max(int(item_limit), 0)]
        if isinstance(row, dict)
    ]
    block_candidates = [
        compact_row(row, include_pre_surge=False)
        for row in list(value.get("block_candidates") or [])[
            : max(int(block_candidate_limit), 0)
        ]
        if isinstance(row, dict)
    ]
    pre_surge_candidates = [
        compact_row(row)
        for row in list(value.get("pre_surge_candidates") or [])[
            : max(int(pre_surge_candidate_limit), 0)
        ]
        if isinstance(row, dict)
    ]
    if not pre_surge_candidates:
        pre_surge_candidates = [
            row
            for row in items
            if isinstance(row.get("pre_surge"), dict)
            and row["pre_surge"].get("is_candidate")
        ][: max(int(pre_surge_candidate_limit), 0)]
    return {
        "status": _clean_text(value.get("status") or "unknown", limit=40),
        "trading_day": _clean_text(value.get("trading_day"), limit=40),
        "summary": _clean_text(value.get("summary"), limit=500),
        "items": [row for row in items if row],
        "block_candidates": [row for row in block_candidates if row],
        "pre_surge_candidates": [row for row in pre_surge_candidates if row],
    }


def _opportunity_brief_row(
    row: Any,
    *,
    source: str,
    bucket: str = "",
) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    if source.startswith("daily_discovery"):
        row = enrich_discovery_result(row)
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    pre_surge = (
        row.get("pre_surge") if isinstance(row.get("pre_surge"), dict) else {}
    )
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    symbol = _clean_text(row.get("symbol"), limit=16)
    if not _is_symbol(symbol):
        return {}
    payload: dict[str, Any] = {
        "symbol": symbol,
        "name": _clean_text(row.get("name") or analysis.get("name"), limit=80),
        "source": source,
        "bucket": bucket,
        "score": row.get("score") or pre_surge.get("score"),
        "confidence": row.get("confidence") or analysis.get("confidence"),
        "stance": _clean_text(row.get("stance") or analysis.get("stance"), limit=80),
        "summary": _clean_text(
            row.get("summary") or analysis.get("summary"),
            limit=220,
        ),
        "decision_use": _clean_text(quality.get("decision_use"), limit=60),
    }
    reasons = (
        pre_surge.get("reasons")
        or row.get("reasons")
        or analysis.get("reasons")
        or evidence.get("reasons")
    )
    risks = row.get("risks") or analysis.get("risks") or evidence.get("risks")
    checks = row.get("checks") or evidence.get("checks")
    if reasons:
        payload["reasons"] = _clean_text_list(reasons, limit=140, max_items=3)
    if risks:
        payload["risks"] = _clean_text_list(risks, limit=120, max_items=2)
    if checks:
        payload["checks"] = _clean_text_list(checks, limit=120, max_items=2)
    if pre_surge:
        payload["pre_surge"] = {
            key: item
            for key, item in compact_prompt_value(
                pre_surge,
                list_limit=3,
                string_limit=100,
            ).items()
            if key
            in {
                "is_candidate",
                "lane",
                "score",
                "entry_bias",
                "preferred_horizon",
                "reasons",
            }
        }
    return {key: item for key, item in payload.items() if item not in ("", None, [], {})}


def _append_unique_opportunity_row(
    rows: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    row: dict[str, Any],
    *,
    limit: int,
) -> None:
    if not row or len(rows) >= max(int(limit), 0):
        return
    key = (str(row.get("symbol") or ""), str(row.get("source") or ""))
    if not key[0] or key in seen:
        return
    rows.append(row)
    seen.add(key)


def build_opportunity_research_brief(
    *,
    daily_discovery: dict[str, Any] | None,
    research_spine: dict[str, Any] | None,
    aggressive_opportunities: dict[str, Any] | None,
    market_pulse: dict[str, Any] | None,
    per_bucket_limit: int = 8,
) -> dict[str, Any]:
    limit = max(int(per_bucket_limit), 1)
    daily = daily_discovery if isinstance(daily_discovery, dict) else {}
    spine = research_spine if isinstance(research_spine, dict) else {}
    aggressive = (
        aggressive_opportunities if isinstance(aggressive_opportunities, dict) else {}
    )
    pulse = market_pulse if isinstance(market_pulse, dict) else {}
    buckets = spine.get("buckets") if isinstance(spine.get("buckets"), dict) else {}

    seen: set[tuple[str, str]] = set()
    pre_surge_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    discovery_rows: list[dict[str, Any]] = []
    aggressive_rows: list[dict[str, Any]] = []

    for row in _normalize_list(daily.get("pre_surge_candidates")):
        _append_unique_opportunity_row(
            pre_surge_rows,
            seen,
            _opportunity_brief_row(
                row,
                source="daily_discovery.pre_surge",
                bucket="pre_surge",
            ),
            limit=limit,
        )
    for row in _normalize_list(buckets.get("pre_surge")):
        _append_unique_opportunity_row(
            pre_surge_rows,
            seen,
            _opportunity_brief_row(
                row,
                source="research_spine.pre_surge",
                bucket="pre_surge",
            ),
            limit=limit,
        )
    for row in _normalize_list(daily.get("block_candidates")):
        _append_unique_opportunity_row(
            block_rows,
            seen,
            _opportunity_brief_row(
                row,
                source="daily_discovery.block_candidate",
                bucket="block_candidate",
            ),
            limit=limit,
        )
    for row in _normalize_list(daily.get("items")):
        brief = _opportunity_brief_row(
            row,
            source="daily_discovery.item",
            bucket="daily_discovery",
        )
        if (row.get("pre_surge") if isinstance(row, dict) else {}) and (
            isinstance(row.get("pre_surge"), dict)
            and row["pre_surge"].get("is_candidate")
        ):
            _append_unique_opportunity_row(
                pre_surge_rows,
                seen,
                {**brief, "source": "daily_discovery.item.pre_surge"},
                limit=limit,
            )
        else:
            _append_unique_opportunity_row(
                discovery_rows,
                seen,
                brief,
                limit=limit,
            )
    for row in _normalize_list(buckets.get("daily_discovery")):
        _append_unique_opportunity_row(
            discovery_rows,
            seen,
            _opportunity_brief_row(
                row,
                source="research_spine.daily_discovery",
                bucket="daily_discovery",
            ),
            limit=limit,
        )
    for row in _normalize_list(aggressive.get("candidates")):
        _append_unique_opportunity_row(
            aggressive_rows,
            seen,
            _opportunity_brief_row(
                row,
                source="aggressive_opportunities",
                bucket="aggressive",
            ),
            limit=limit,
        )

    total = (
        len(pre_surge_rows)
        + len(block_rows)
        + len(discovery_rows)
        + len(aggressive_rows)
    )
    return {
        "status": "ok" if total else "empty",
        "role": "minimum_surviving_opportunity_context",
        "instruction": (
            "This compact brief must survive prompt budget compaction. Use it "
            "to keep pre-surge, daily discovery, and aggressive candidates "
            "visible even when raw research sections are omitted."
        ),
        "source_status": {
            "daily_discovery": str(daily.get("status") or "missing"),
            "research_spine": str(spine.get("status") or "missing"),
            "aggressive_opportunities": str(aggressive.get("status") or "missing"),
            "market_pulse": str(pulse.get("status") or "missing"),
        },
        "pre_surge_candidates": pre_surge_rows,
        "block_candidates": block_rows,
        "daily_discovery_candidates": discovery_rows,
        "aggressive_candidates": aggressive_rows,
    }


def compact_prompt_quote(value: Any) -> dict[str, Any]:
    return compact_prompt_fields(value, PROMPT_QUOTE_KEYS)


def compact_market_judgment_strategy(value: Any) -> dict[str, Any]:
    return compact_prompt_fields(value, MARKET_JUDGMENT_STRATEGY_KEYS)


def compact_market_judgment_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "invalid"}
    run = value.get("run") if isinstance(value.get("run"), dict) else {}
    judgments: list[dict[str, Any]] = []
    for row in list(value.get("judgments") or [])[:12]:
        if not isinstance(row, dict):
            continue
        payload = {
            "symbol": _clean_text(row.get("symbol"), limit=16),
            "name": _clean_text(row.get("name"), limit=80),
            "stance": _clean_text(row.get("stance"), limit=40),
            "account_action": _clean_text(row.get("account_action"), limit=60),
            "horizon": _clean_text(row.get("horizon"), limit=40),
            "confidence": row.get("confidence"),
            "reasons": [
                _clean_text(item, limit=180)
                for item in list(row.get("reasons") or [])[:3]
            ],
            "risks": [
                _clean_text(item, limit=180)
                for item in list(row.get("risks") or [])[:3]
            ],
            "triggers": [
                _clean_text(item, limit=160)
                for item in list(row.get("triggers") or [])[:3]
            ],
            "data_gaps": [
                _clean_text(item, limit=120)
                for item in list(row.get("data_gaps") or [])[:3]
            ],
            "quote": compact_prompt_quote(row.get("quote") or {}),
            "position": compact_prompt_value(
                row.get("position") or {},
                string_limit=160,
            ),
            "strategy": compact_market_judgment_strategy(row.get("strategy") or {}),
        }
        judgments.append(
            {
                key: item
                for key, item in payload.items()
                if item not in (None, "", [], {})
            }
        )
    coverage = value.get("candidate_coverage")
    return {
        "status": _clean_text(value.get("status") or "unknown", limit=40),
        "run": {
            key: run.get(key)
            for key in ("id", "run_at", "market_session", "status", "mode", "model")
            if run.get(key) not in (None, "", [], {})
        },
        "candidate_coverage": compact_prompt_value(
            coverage,
            list_limit=4,
            string_limit=160,
        )
        if isinstance(coverage, dict)
        else {},
        "judgments": judgments,
    }


def compact_requested_symbol_coverage_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    symbol_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)
    out: dict[str, Any] = {}
    for key in (
        "version",
        "status",
        "decision_policy",
        "required_action",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)
    if "hard_blocker" in value:
        out["hard_blocker"] = bool(value.get("hard_blocker"))
    for key in (
        "requested_symbol_count",
        "summarized_symbol_count",
        "unsummarized_symbol_count",
        "missing_summary_count",
        "prompt_omitted_count",
        "degraded_summary_count",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = value.get(key)

    def compact_kis_symbols(raw: Any) -> list[str]:
        return [
            symbol
            for symbol in (
                _compact_requested_symbol_token(item) for item in _normalize_list(raw)
            )
            if symbol and _is_symbol(symbol)
        ]

    symbols = compact_kis_symbols(value.get("unsummarized_symbols"))
    if symbols:
        out["unsummarized_symbols"] = symbols[:symbol_limit]
        out["unsummarized_symbol_omitted_count"] = max(
            len(symbols) - symbol_limit,
            0,
        )
    missing_symbols = compact_kis_symbols(value.get("missing_summary_symbols"))
    if missing_symbols:
        out["missing_summary_symbols"] = missing_symbols[:symbol_limit]
        out["missing_summary_symbol_omitted_count"] = max(
            len(missing_symbols) - symbol_limit,
            0,
        )
    omitted_symbols = compact_kis_symbols(value.get("prompt_omitted_symbols"))
    if omitted_symbols:
        out["prompt_omitted_symbols"] = omitted_symbols[:symbol_limit]
        out["prompt_omitted_symbol_omitted_count"] = max(
            len(omitted_symbols) - symbol_limit,
            0,
        )
    degraded_symbols = compact_kis_symbols(value.get("degraded_summary_symbols"))
    if degraded_symbols:
        out["degraded_summary_symbols"] = degraded_symbols[:symbol_limit]
        out["degraded_summary_symbol_omitted_count"] = max(
            len(degraded_symbols) - symbol_limit,
            0,
        )
    degraded_reasons: list[dict[str, Any]] = []
    for item in _normalize_list(value.get("degraded_summary_reasons"))[:symbol_limit]:
        if not isinstance(item, dict):
            continue
        symbol = _compact_requested_symbol_token(item.get("symbol"))
        if not symbol:
            continue
        row: dict[str, Any] = {"symbol": symbol}
        if item.get("freshness") not in (None, "", [], {}):
            row["freshness"] = _clean_text(item.get("freshness"), limit=text_limit)
        quality_status = normalize_jue_wiki_quality_status(item.get("quality_status"))
        if quality_status:
            row["quality_status"] = quality_status
        warnings = [
            _clean_text(raw, limit=text_limit)
            for raw in _normalize_list(item.get("quality_warnings"))[:symbol_limit]
            if str(raw).strip() and len(str(raw)) <= text_limit
        ]
        if warnings:
            row["quality_warnings"] = warnings
        degraded_reasons.append(row)
    if degraded_reasons:
        out["degraded_summary_reasons"] = degraded_reasons
        out["degraded_summary_reason_omitted_count"] = max(
            len(_normalize_list(value.get("degraded_summary_reasons")))
            - len(degraded_reasons),
            0,
        )
    adjustments: list[dict[str, Any]] = []
    for item in _normalize_list(value.get("required_adjustments"))[:symbol_limit]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        symbol = _compact_requested_symbol_token(
            item.get("symbol") or item.get("code") or item.get("ticker")
        )
        if symbol:
            row["symbol"] = symbol
        item_symbols = [
            child
            for child in (
                _compact_requested_symbol_token(raw)
                for raw in _normalize_list(item.get("symbols"))
            )
            if child
        ]
        if item_symbols:
            row["symbols"] = item_symbols[:symbol_limit]
            row["symbol_omitted_count"] = max(len(item_symbols) - symbol_limit, 0)
        for key in (
            "adjustment_type",
            "resolution",
            "action",
            "required_action",
            "decision_policy",
        ):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        raw_reason = str(item.get("reason") or "")
        if raw_reason and len(raw_reason) <= text_limit:
            row["reason"] = _clean_text(raw_reason, limit=text_limit)
        elif raw_reason:
            row["reason_omitted_for_prompt_budget"] = True
        if row:
            adjustments.append(row)
    if adjustments:
        out["required_adjustments"] = adjustments
        out["required_adjustment_omitted_count"] = max(
            len(_normalize_list(value.get("required_adjustments"))) - len(adjustments),
            0,
        )
    return out


def _requested_symbol_coverage_from_budget_report(
    budget_report: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(budget_report, dict):
        return {}
    summary_symbols = [
        symbol
        for symbol in (
            _compact_requested_symbol_token(item)
            for item in _normalize_list(
                budget_report.get("requested_symbol_summary_symbols")
            )
        )
        if symbol and _is_symbol(symbol)
    ]
    coverage = {
        "status": budget_report.get("requested_symbol_summary_coverage_status"),
        "requested_symbol_count": budget_report.get("requested_symbol_count"),
        "summarized_symbol_count": len(summary_symbols),
        "unsummarized_symbol_count": budget_report.get(
            "requested_symbol_unsummarized_count"
        ),
        "unsummarized_symbols": budget_report.get(
            "requested_symbol_unsummarized_symbols"
        ),
        "missing_summary_count": budget_report.get(
            "requested_symbol_missing_summary_count"
        ),
        "missing_summary_symbols": budget_report.get(
            "requested_symbol_missing_summary_symbols"
        ),
        "prompt_omitted_count": budget_report.get(
            "requested_symbol_prompt_omitted_count"
        ),
        "prompt_omitted_symbols": budget_report.get(
            "requested_symbol_prompt_omitted_symbols"
        ),
        "degraded_summary_count": budget_report.get(
            "requested_symbol_degraded_summary_count"
        ),
        "degraded_summary_symbols": budget_report.get(
            "requested_symbol_degraded_summary_symbols"
        ),
        "degraded_summary_reasons": budget_report.get(
            "requested_symbol_degraded_summary_reasons"
        ),
    }
    compact = compact_requested_symbol_coverage_prompt(
        coverage,
        list_limit=list_limit,
        string_limit=string_limit,
    )
    return {
        key: value
        for key, value in compact.items()
        if not (key.endswith("_omitted_count") and _safe_int(value) == 0)
    }


def _compact_jue_wiki_evidence_quality_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    canonical = canonical_jue_wiki_evidence_quality(value)
    row = {
        key: canonical.get(key)
        for key in (
            "summary_line",
            "source_count",
            "status_counts",
            "warning_counts",
            "source_type_counts",
            "top_warnings",
        )
        if canonical.get(key) not in (None, "", [], {})
    }
    repair_queue = _compact_jue_wiki_evidence_repair_queue_for_prompt(
        canonical.get("repair_queue")
    )
    if repair_queue:
        row["repair_queue"] = repair_queue
    return row


def _compact_jue_wiki_evidence_repair_queue_for_prompt(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    open_count = _safe_int(value.get("open_count"))
    if open_count > 0:
        row["open_count"] = open_count
    actions: list[dict[str, Any]] = []
    for action in _normalize_list(value.get("actions"))[:4]:
        if not isinstance(action, dict):
            continue
        compact_action: dict[str, Any] = {}
        for key in ("action_type", "status"):
            raw = action.get(key)
            if raw not in (None, "", [], {}):
                compact_action[key] = _clean_text(raw, limit=120)
        quality_warnings = [
            _clean_text(warning, limit=120)
            for warning in _normalize_list(action.get("quality_warnings"))[:6]
            if str(warning or "").strip()
        ]
        if quality_warnings:
            compact_action["quality_warnings"] = quality_warnings
        if compact_action:
            actions.append(compact_action)
    if actions:
        row["actions"] = actions
    return row


def _jue_wiki_quality_warnings_from_evidence(
    value: Any,
    *,
    limit: int = 3,
) -> list[str]:
    if not isinstance(value, dict):
        return []
    warnings: list[str] = []
    for item in _normalize_list(value.get("top_warnings")):
        if isinstance(item, dict):
            warning = item.get("warning") or item.get("key") or item.get("name")
        else:
            warning = item
        text = _clean_text(warning, limit=120)
        if text and text not in warnings:
            warnings.append(text)
        if len(warnings) >= max(int(limit), 0):
            break
    return warnings


def _compact_jue_wiki_source_ref_for_prompt(value: Any) -> dict[str, Any] | str:
    if not isinstance(value, dict):
        return _clean_text(value, limit=180)
    row: dict[str, Any] = {}
    for key in (
        "source_type",
        "source_id",
        "source_scope",
        "kind",
        "id",
        "status",
        "action_type",
        "repair_status",
        "decision_use",
        "observed_at",
        "as_of",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=180)
    symbols = [
        _clean_text(symbol, limit=40)
        for symbol in _normalize_list(value.get("symbols"))[:6]
        if str(symbol or "").strip()
    ]
    if symbols:
        row["symbols"] = symbols
    evidence_quality = _compact_jue_wiki_evidence_quality_for_prompt(
        value.get("evidence_quality")
    )
    if evidence_quality:
        row["evidence_quality"] = evidence_quality
    quality_status = normalize_jue_wiki_quality_status(value.get("quality_status"))
    if not quality_status:
        quality_status = jue_wiki_quality_status_from_evidence(evidence_quality)
    if quality_status:
        row["quality_status"] = quality_status
    quality_warnings = [
        _clean_text(warning, limit=120)
        for warning in _normalize_list(value.get("quality_warnings"))[:6]
        if str(warning or "").strip()
    ]
    if not quality_warnings:
        quality_warnings = _jue_wiki_quality_warnings_from_evidence(
            evidence_quality,
            limit=6,
        )
    if quality_warnings:
        row["quality_warnings"] = quality_warnings
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_quality_warning_effectiveness_for_prompt(
    value: Any,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _normalize_list(value)[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        warning = _clean_text(item.get("warning"), limit=120)
        if not warning:
            continue
        row: dict[str, Any] = {"warning": warning}
        for key, max_len in (("page_id", 160), ("status", 80)):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                row[key] = _clean_text(raw, limit=max_len)
        if item.get("sample_count") not in (None, "", [], {}):
            row["sample_count"] = _safe_int(item.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _safe_float(item.get(key))
        reasons = [
            _clean_text(reason, limit=120)
            for reason in _normalize_list(item.get("reasons"))[:4]
            if str(reason or "").strip()
        ]
        if reasons:
            row["reasons"] = reasons
        rows.append(row)
    return rows


def _compact_jue_wiki_effectiveness_bundle_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (("status", 80), ("decision_use", 180)):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=max_len)
    metrics: list[dict[str, Any]] = []
    for item in _normalize_list(value.get("metrics"))[:4]:
        if not isinstance(item, dict):
            continue
        metric: dict[str, Any] = {}
        for key, max_len in (
            ("warning", 120),
            ("page_id", 160),
            ("source_type", 80),
            ("source_id", 160),
            ("status", 80),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                metric[key] = _clean_text(raw, limit=max_len)
        if item.get("sample_count") not in (None, "", [], {}):
            metric["sample_count"] = _safe_int(item.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if item.get(key) not in (None, "", [], {}):
                metric[key] = _safe_float(item.get(key))
        reasons = [
            _clean_text(reason, limit=120)
            for reason in _normalize_list(item.get("reasons"))[:4]
            if str(reason or "").strip()
        ]
        if reasons:
            metric["reasons"] = reasons
        if metric:
            metrics.append(metric)
    if metrics:
        row["metrics"] = metrics
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_page_effectiveness_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (
        ("status", 80),
        ("venue", 80),
        ("horizon", 80),
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=max_len)
    if value.get("sample_count") not in (None, "", [], {}):
        row["sample_count"] = _safe_int(value.get("sample_count"))
    for key in (
        "win_rate",
        "expectancy",
        "avg_return_pct",
        "median_mae_pct",
        "drawdown_pressure",
        "helpful_score",
        "confidence",
    ):
        if value.get(key) not in (None, "", [], {}):
            row[key] = _safe_float(value.get(key))
    reasons = [
        _clean_text(reason, limit=120)
        for reason in _normalize_list(value.get("reasons"))[:3]
        if str(reason or "").strip()
    ]
    if reasons:
        row["reasons"] = reasons
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_usage_guidance_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (
        ("trust_level", 40),
        ("risk_posture", 80),
        ("decision_use", 180),
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=max_len)
    for key in ("allowed_uses", "required_cross_checks"):
        items = [
            _clean_text(item, limit=100)
            for item in _normalize_list(value.get(key))[:8]
            if str(item or "").strip()
        ]
        if items:
            row[key] = items
    if value.get("hard_blocker") not in (None, "", [], {}):
        row["hard_blocker"] = _safe_bool(value.get("hard_blocker"))
    if value.get("max_confidence_without_cross_check") not in (None, "", [], {}):
        row["max_confidence_without_cross_check"] = _safe_float(
            value.get("max_confidence_without_cross_check")
        )
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_status_list(value: Any, *, limit: int = 6) -> list[str]:
    statuses: list[str] = []
    for item in _normalize_list(value)[: max(int(limit), 0)]:
        status = _clean_text(item, limit=80).lower()
        if status and status not in statuses:
            statuses.append(status)
    return statuses


def _compact_jue_wiki_effectiveness_attention_items_for_prompt(
    value: Any,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _normalize_list(value)[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key, max_len in (
            ("page_id", 160),
            ("kind", 80),
            ("status", 80),
            ("evidence_id", 180),
            ("warning", 160),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                row[key] = _clean_text(raw, limit=max_len)
        if row and row not in items:
            items.append(row)
    return items


def _jue_wiki_effectiveness_attention_items_from_rows_for_prompt(
    rows: list[Any],
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = _clean_text(row.get("page_id"), limit=160)
        if not page_id:
            continue
        for kind, key in (
            ("usage_guidance", "usage_guidance_effectiveness"),
            ("memory_card_quality", "memory_card_quality_effectiveness"),
            ("quality_warning_source", "quality_warning_source_effectiveness"),
            ("quality_warning", "quality_warning_effectiveness"),
        ):
            for item in _jue_wiki_effectiveness_attention_items_for_value_for_prompt(
                page_id=page_id,
                kind=kind,
                value=row.get(key),
            ):
                if item not in items:
                    items.append(item)
                if len(items) >= limit:
                    return _compact_jue_wiki_effectiveness_attention_items_for_prompt(
                        items,
                        limit=limit,
                    )
    return _compact_jue_wiki_effectiveness_attention_items_for_prompt(
        items,
        limit=limit,
    )


def _jue_wiki_effectiveness_attention_items_for_value_for_prompt(
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
            for metric in _normalize_list(row.get("metrics"))
            if isinstance(metric, dict)
        ]
        source_rows = metrics or [row]
        for source in source_rows:
            if not isinstance(source, dict):
                continue
            status = _clean_text(
                source.get("status") or row.get("status"),
                limit=80,
            ).lower()
            evidence_id = (
                ""
                if kind == "quality_warning"
                else _clean_text(
                    source.get("page_id")
                    or source.get("source_id")
                    or source.get("rule_id"),
                    limit=180,
                )
            )
            warning = _clean_text(
                source.get("warning") or row.get("warning"),
                limit=160,
            )
            if not status and not evidence_id and not warning:
                continue
            item: dict[str, Any] = {"page_id": page_id, "kind": kind}
            if status:
                item["status"] = status
            if evidence_id:
                item["evidence_id"] = evidence_id
            if warning:
                item["warning"] = warning
            if item not in items:
                items.append(item)
    return items


def _compact_jue_wiki_page_for_prompt(
    value: Any,
    *,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "page_id",
        "title",
        "page_type",
        "scope",
        "symbol",
        "rank",
        "score",
        "freshness",
        "freshness_status",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=96) if isinstance(raw, str) else raw
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    for key in ("summary", "content", "body", "notes"):
        raw = value.get(key)
        if raw in (None, "", [], {}):
            continue
        row["summary" if key == "summary" else "excerpt"] = _clean_text(
            raw,
            limit=max(int(string_limit), 1),
        )
        break
    for key in (
        "selection_reasons",
        "selection_penalties",
        "quality_warnings",
        "freshness_warnings",
    ):
        raw = value.get(key)
        if isinstance(raw, list):
            row[key] = [
                _clean_text(item, limit=120)
                for item in raw[:3]
                if str(item or "").strip()
            ]
    source_refs = value.get("source_refs")
    if isinstance(source_refs, list):
        refs = [
            ref
            for ref in (
                _compact_jue_wiki_source_ref_for_prompt(item)
                for item in source_refs[:3]
            )
            if ref not in (None, "", [], {})
        ]
        if refs:
            row["source_refs"] = refs
    evidence_quality = _compact_jue_wiki_evidence_quality_for_prompt(
        value.get("evidence_quality")
    )
    if evidence_quality:
        row["evidence_quality"] = evidence_quality
        if not row.get("quality_status"):
            quality_status = jue_wiki_quality_status_from_evidence(evidence_quality)
            if quality_status:
                row["quality_status"] = quality_status
        if not row.get("quality_warnings"):
            warnings = _jue_wiki_quality_warnings_from_evidence(evidence_quality)
            if warnings:
                row["quality_warnings"] = warnings
    memory_card_quality = _compact_jue_wiki_memory_card_quality_details_for_prompt(
        value.get("memory_card_quality")
    )
    if memory_card_quality:
        row["memory_card_quality"] = memory_card_quality
    effectiveness = _compact_jue_wiki_page_effectiveness_for_prompt(
        value.get("effectiveness")
    )
    if effectiveness:
        row["effectiveness"] = effectiveness
    usage_guidance = _compact_jue_wiki_usage_guidance_for_prompt(
        value.get("usage_guidance")
    )
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for source_key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle_for_prompt(
            value.get(source_key)
        )
        if effectiveness_bundle:
            row[source_key] = effectiveness_bundle
    quality_warning_effectiveness = (
        _compact_jue_wiki_quality_warning_effectiveness_for_prompt(
            value.get("quality_warning_effectiveness")
        )
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_freshness_summary_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {
        key: value.get(key)
        for key in ("page_count", "status_counts", "warning_counts")
        if value.get(key) not in (None, "", [], {})
    }
    for key in ("stale_page_ids", "unknown_page_ids"):
        row[key] = [
            _clean_text(item, limit=120)
            for item in list(value.get(key) or [])[:12]
            if str(item or "").strip()
        ]
    return row


def _compact_jue_wiki_memory_card_quality_details_for_prompt(
    value: Any,
    *,
    item_limit: int = 4,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def string_list(raw: Any, *, limit: int, max_len: int = 96) -> list[str]:
        values: list[str] = []
        for item in _normalize_list(raw)[: max(int(limit), 0)]:
            text = _clean_text(item, limit=max_len)
            if text and text not in values:
                values.append(text)
        return values

    row: dict[str, Any] = {}
    for key in ("status", "resolution", "required_action", "decision_use"):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=120)
    symbols = clean_symbol_list(value.get("symbols"), max_items=8)
    if symbols:
        row["symbols"] = symbols
    missing_fields = string_list(value.get("missing_fields"), limit=8)
    if missing_fields:
        row["missing_fields"] = missing_fields
    required_checks = string_list(value.get("required_checks"), limit=8, max_len=140)
    if required_checks:
        row["required_checks"] = required_checks
    if value.get("candidate_resolution_required") not in (None, "", [], {}):
        row["candidate_resolution_required"] = bool(
            value.get("candidate_resolution_required")
        )

    items: list[dict[str, Any]] = []
    for item in _normalize_list(value.get("items"))[: max(int(item_limit), 0)]:
        child = _compact_jue_wiki_memory_card_quality_details_for_prompt(
            item,
            item_limit=0,
        )
        if child:
            items.append(child)
    if items:
        row["items"] = items
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_memory_card_for_prompt(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    card: dict[str, str] = {}
    for key, limit, raw_limit in (
        ("stance", 160, 320),
        ("durable_facts", 220, 440),
        ("trading_history", 220, 440),
        ("lessons", 220, 440),
        ("contradictions", 180, 360),
        ("open_questions", 220, 440),
    ):
        raw_text = _clean_text(
            value.get(key),
            limit=max(len(str(value.get(key) or "")), 1),
        )
        if not raw_text or len(raw_text) > raw_limit:
            continue
        card[key] = _clean_text(raw_text, limit=limit)
    return card


def _jue_wiki_memory_card_quality_for_prompt(card: dict[str, str]) -> dict[str, Any]:
    if not isinstance(card, dict) or not card:
        return {}
    core_keys = ("stance", "durable_facts", "lessons", "open_questions")
    present_keys = [key for key in core_keys if str(card.get(key) or "").strip()]
    missing_keys = [key for key in core_keys if key not in present_keys]
    evidence_keys = [key for key in present_keys if key != "stance"]
    if "stance" in present_keys and len(evidence_keys) >= 2:
        status = "strong"
    elif len(present_keys) >= 2:
        status = "partial"
    else:
        status = "weak"
    quality: dict[str, Any] = {
        "status": status,
        "present_keys": present_keys,
        "missing_keys": missing_keys,
    }
    if status != "strong":
        quality["required_action"] = "cross_check_live_research_before_high_confidence"
    return quality


def _compact_jue_wiki_requested_symbol_summary_for_prompt(
    value: Any,
    *,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "symbol",
        "page_id",
        "title",
        "selected_as_page",
        "confidence",
        "freshness",
        "freshness_status",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=96) if isinstance(raw, str) else raw
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    quality_warnings = [
        _clean_text(warning, limit=120)
        for warning in _normalize_list(value.get("quality_warnings"))[:3]
        if str(warning or "").strip()
    ]
    if quality_warnings:
        row["quality_warnings"] = quality_warnings
    freshness_warnings = [
        _clean_text(warning, limit=120)
        for warning in _normalize_list(value.get("freshness_warnings"))[:3]
        if str(warning or "").strip()
    ]
    if freshness_warnings:
        row["freshness_warnings"] = freshness_warnings
    evidence_quality = _compact_jue_wiki_evidence_quality_for_prompt(
        value.get("evidence_quality")
    )
    if evidence_quality:
        row["evidence_quality"] = evidence_quality
        if not row.get("quality_status"):
            quality_status = jue_wiki_quality_status_from_evidence(evidence_quality)
            if quality_status:
                row["quality_status"] = quality_status
        if not row.get("quality_warnings"):
            warnings = _jue_wiki_quality_warnings_from_evidence(evidence_quality)
            if warnings:
                row["quality_warnings"] = warnings
    summary = _clean_text(value.get("summary"), limit=max(int(string_limit), 1))
    if summary:
        row["summary"] = summary
    effectiveness = _compact_jue_wiki_page_effectiveness_for_prompt(
        value.get("effectiveness")
    )
    if effectiveness:
        row["effectiveness"] = effectiveness
    usage_guidance = _compact_jue_wiki_usage_guidance_for_prompt(
        value.get("usage_guidance")
    )
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle_for_prompt(
            value.get(key)
        )
        if effectiveness_bundle:
            row[key] = effectiveness_bundle
    quality_warning_effectiveness = (
        _compact_jue_wiki_quality_warning_effectiveness_for_prompt(
            value.get("quality_warning_effectiveness")
        )
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    memory_card = value.get("memory_card")
    if isinstance(memory_card, dict):
        card = _compact_jue_wiki_memory_card_for_prompt(memory_card)
        if card:
            row["memory_card"] = card
            explicit_quality = (
                _compact_jue_wiki_memory_card_quality_details_for_prompt(
                    value.get("memory_card_quality")
                )
            )
            row["memory_card_quality"] = explicit_quality or (
                _jue_wiki_memory_card_quality_for_prompt(card)
            )
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def compact_jue_wiki_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> Any:
    if not isinstance(value, dict):
        return compact_etf_prompt_value(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    out: dict[str, Any] = {}
    for key in (
        "status",
        "prompt_mode",
        "selection_run_id",
        "target_scope",
        "primary_context",
        "raw_context_policy",
        "evidence_quality_summary",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            out[key] = _clean_text(raw, limit=120) if isinstance(raw, str) else raw
    evidence_quality = _compact_jue_wiki_evidence_quality_for_prompt(
        value.get("evidence_quality")
    )
    if evidence_quality:
        out["evidence_quality"] = evidence_quality
    freshness_summary = _compact_jue_wiki_freshness_summary_for_prompt(
        value.get("freshness_summary")
    )
    if freshness_summary:
        out["freshness_summary"] = freshness_summary
    effectiveness_attention_items = (
        _compact_jue_wiki_effectiveness_attention_items_for_prompt(
            value.get("effectiveness_attention_items"),
            limit=max(int(list_limit), 1) * 4,
        )
    )
    if effectiveness_attention_items:
        out["effectiveness_attention_items"] = effectiveness_attention_items
    repair_contract = compact_jue_wiki_repair_contract_prompt(
        {"action_batches": value.get("repair_action_batches")},
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if repair_contract.get("action_batches"):
        out["repair_action_batches"] = repair_contract["action_batches"]
    repair_queue = _compact_jue_wiki_repair_queue_for_prompt(
        value.get("repair_queue"),
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if repair_queue:
        out["repair_queue"] = repair_queue
    pages = value.get("pages")
    if isinstance(pages, list):
        compact_pages = [
            row
            for row in (
                _compact_jue_wiki_page_for_prompt(
                    page,
                    string_limit=string_limit,
                )
                for page in pages[: max(int(list_limit), 0)]
            )
            if row
        ]
        if compact_pages:
            out["pages"] = compact_pages
        out["page_count"] = len(pages)
        omitted = max(len(pages) - len(compact_pages), 0)
        if omitted:
            out["omitted_page_count"] = omitted
    requested_symbol_summaries = value.get("requested_symbol_summaries")
    if isinstance(requested_symbol_summaries, list):
        summaries = [
            row
            for row in (
                _compact_jue_wiki_requested_symbol_summary_for_prompt(
                    summary,
                    string_limit=string_limit,
                )
                for summary in requested_symbol_summaries[: max(int(list_limit), 0)]
            )
            if row
        ]
        if summaries:
            out["requested_symbol_summaries"] = summaries
        omitted = max(len(requested_symbol_summaries) - len(summaries), 0)
        if omitted:
            out["requested_symbol_summaries_omitted_count"] = omitted
    if "effectiveness_attention_items" not in out:
        derived_attention_items = (
            _jue_wiki_effectiveness_attention_items_from_rows_for_prompt(
                [
                    *list(out.get("pages") or []),
                    *list(out.get("requested_symbol_summaries") or []),
                ],
                limit=max(int(list_limit), 1) * 4,
            )
        )
        if derived_attention_items:
            out["effectiveness_attention_items"] = derived_attention_items
    content = _clean_text(value.get("content"), limit=max(int(string_limit), 1))
    if content:
        out["content"] = content
    if isinstance(value.get("budget_report"), dict):
        out["budget_report"] = compact_etf_prompt_value(
            value.get("budget_report"),
            list_limit=4,
            string_limit=120,
        )
        requested_symbol_coverage = _requested_symbol_coverage_from_budget_report(
            value.get("budget_report"),
            list_limit=list_limit,
            string_limit=string_limit,
        )
        if requested_symbol_coverage:
            out["requested_symbol_coverage"] = requested_symbol_coverage
    out["compacted_for_prompt_budget"] = True
    return out


def _top_count_items(
    value: Any,
    *,
    limit: int,
    always_keep_token: str | None = None,
) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    rows: list[tuple[str, int]] = []
    for key, raw_count in value.items():
        text = _clean_text(key, limit=96)
        if not text:
            continue
        rows.append((text, _safe_int(raw_count)))
    rows.sort(key=lambda item: (-item[1], item[0]))
    out: dict[str, int] = {}
    for key, count in rows[: max(int(limit), 0)]:
        out[key] = count
    if always_keep_token:
        for key, count in rows:
            if always_keep_token in key:
                out[key] = count
    return out


def _compact_jue_wiki_selection_audit_for_storage(
    value: Any,
    *,
    page_limit: int,
    reason_limit: int,
    penalty_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    selected_page_count = _safe_int(value.get("selected_page_count"))
    if selected_page_count:
        out["selected_page_count"] = selected_page_count
    reason_counts = _top_count_items(
        value.get("reason_counts"),
        limit=reason_limit,
        always_keep_token="manager_contract_recovery",
    )
    if reason_counts:
        out["reason_counts"] = reason_counts
    penalty_counts = _top_count_items(
        value.get("penalty_counts"),
        limit=penalty_limit,
    )
    if penalty_counts:
        out["penalty_counts"] = penalty_counts
    top_pages: list[dict[str, Any]] = []
    for page in _normalize_list(value.get("top_pages"))[: max(int(page_limit), 0)]:
        if not isinstance(page, dict):
            continue
        page_id = _clean_text(page.get("page_id"), limit=96)
        if not page_id:
            continue
        row: dict[str, Any] = {"page_id": page_id}
        rank = _safe_int(page.get("rank"))
        if rank:
            row["rank"] = rank
        reasons = [
            _clean_text(reason, limit=80)
            for reason in _normalize_list(page.get("selection_reasons"))[:2]
            if str(reason or "").strip()
        ]
        if reasons:
            row["selection_reasons"] = reasons
        top_pages.append(row)
    if top_pages:
        out["top_pages"] = top_pages
    return out


def _compact_jue_wiki_trust_profile_for_storage(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    text_limit = max(min(int(string_limit), 240), 48)
    out: dict[str, Any] = {}
    for key in (
        "trust_level",
        "authority",
        "decision_use",
        "posture",
        "policy_reason",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            out[key] = _clean_text(raw, limit=text_limit)
    usage_contract = (
        value.get("usage_contract")
        if isinstance(value.get("usage_contract"), dict)
        else {}
    )
    if usage_contract:
        contract: dict[str, Any] = {}
        for key in ("decision_role", "effectiveness_status", "risk_posture"):
            raw = usage_contract.get(key)
            if raw not in (None, "", [], {}):
                contract[key] = _clean_text(raw, limit=text_limit)
        for key in (
            "requires_live_cross_check",
            "standalone_trade_authority",
            "hard_blocker",
        ):
            raw = usage_contract.get(key)
            if raw not in (None, "", [], {}):
                contract[key] = bool(raw)
        checks = [
            _clean_text(item, limit=80)
            for item in _normalize_list(
                usage_contract.get("required_cross_checks")
            )[: max(int(list_limit), 1)]
            if str(item or "").strip()
        ]
        if checks:
            contract["required_cross_checks"] = checks
        if contract:
            out["usage_contract"] = contract
    return {key: child for key, child in out.items() if child not in (None, "", [], {})}


def compact_jue_wiki_application_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
    emergency: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    selected_page_ids = [
        _clean_text(page_id, limit=96)
        for page_id in _normalize_list(value.get("selected_page_ids"))[
            : max(int(list_limit), 1)
        ]
        if str(page_id or "").strip()
    ]
    if selected_page_ids:
        out["selected_page_ids"] = selected_page_ids
    audit = _compact_jue_wiki_selection_audit_for_storage(
        value.get("selection_audit"),
        page_limit=1 if emergency else max(min(int(list_limit), 2), 1),
        reason_limit=4 if emergency else 6,
        penalty_limit=1 if emergency else 3,
    )
    if audit:
        out["selection_audit"] = audit
    for key in (
        "status",
        "prompt_mode",
        "selection_run_id",
        "target_scope",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            out[key] = _clean_text(raw, limit=max(min(int(string_limit), 120), 48))
    trust_profile = _compact_jue_wiki_trust_profile_for_storage(
        value.get("trust_profile"),
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if trust_profile:
        out["trust_profile"] = trust_profile
    if emergency:
        return out
    return out


def _compact_jue_wiki_repair_queue_for_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("open_count", "resolved_count"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _safe_int(value.get(key))
    symbol_limit = max(int(list_limit), 0) * 4
    open_symbols = [
        _clean_text(symbol, limit=32)
        for symbol in _normalize_list(value.get("open_symbols"))[:symbol_limit]
        if str(symbol or "").strip()
    ]
    if open_symbols:
        out["open_symbols"] = open_symbols
    repair_contract = compact_jue_wiki_repair_contract_prompt(
        {"action_batches": value.get("open_action_batches")},
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if repair_contract.get("action_batches"):
        out["open_action_batches"] = repair_contract["action_batches"]
    return {
        key: child
        for key, child in out.items()
        if child not in (None, "", [], {})
    }


def _compact_memory_card_quality_gap_summary_for_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)

    def compact_counts(key: str) -> dict[str, int]:
        raw = value.get(key) if isinstance(value.get(key), dict) else {}
        return {
            _clean_text(raw_key, limit=80): _safe_int(raw_value)
            for raw_key, raw_value in sorted(raw.items())
            if str(raw_key).strip()
        }

    def compact_top(key: str, *, label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in _normalize_list(value.get(key))[:item_limit]:
            if not isinstance(item, dict):
                continue
            label_value = _clean_text(item.get(label), limit=text_limit)
            if not label_value:
                continue
            row: dict[str, Any] = {label: label_value}
            for metric_key in ("sample_count", "missed_count"):
                if item.get(metric_key) not in (None, "", [], {}):
                    row[metric_key] = _safe_int(item.get(metric_key))
            rows.append(row)
        return rows

    def compact_terms(key: str) -> list[str]:
        terms: list[str] = []
        for item in _normalize_list(value.get(key))[:item_limit]:
            text = _clean_text(item, limit=text_limit)
            if text and text not in terms:
                terms.append(text)
        return terms

    def fallback_priority_terms(
        *,
        priority_key: str,
        missed_counts_key: str,
        top_key: str,
        label: str,
    ) -> list[str]:
        terms = compact_terms(priority_key)
        if terms:
            return terms
        missed_counts = (
            value.get(missed_counts_key)
            if isinstance(value.get(missed_counts_key), dict)
            else {}
        )
        for item, count in sorted(
            missed_counts.items(),
            key=lambda row: (-_safe_int(row[1]), str(row[0])),
        ):
            if _safe_int(count) <= 0:
                continue
            text = _clean_text(item, limit=text_limit)
            if text and text not in terms:
                terms.append(text)
            if len(terms) >= item_limit:
                return terms
        for row in _normalize_list(value.get(top_key)):
            if not isinstance(row, dict) or _safe_int(row.get("missed_count")) <= 0:
                continue
            text = _clean_text(row.get(label), limit=text_limit)
            if text and text not in terms:
                terms.append(text)
            if len(terms) >= item_limit:
                return terms
        return terms

    def priority_metric(
        *,
        top_key: str,
        label: str,
        sample_counts_key: str,
        missed_counts_key: str,
    ) -> tuple[str, int, int] | None:
        for row in _normalize_list(value.get(top_key))[:item_limit]:
            if not isinstance(row, dict):
                continue
            item = _clean_text(row.get(label), limit=text_limit)
            missed_count = _safe_int(row.get("missed_count"))
            if item and missed_count > 0:
                return item, _safe_int(row.get("sample_count")), missed_count
        missed_counts = (
            value.get(missed_counts_key)
            if isinstance(value.get(missed_counts_key), dict)
            else {}
        )
        sample_counts = (
            value.get(sample_counts_key)
            if isinstance(value.get(sample_counts_key), dict)
            else {}
        )
        for item, missed_count in sorted(
            missed_counts.items(),
            key=lambda row: (-_safe_int(row[1]), str(row[0])),
        ):
            clean = _clean_text(item, limit=text_limit)
            if clean and _safe_int(missed_count) > 0:
                return clean, _safe_int(sample_counts.get(item)), _safe_int(missed_count)
        return None

    def compact_priority_focus() -> dict[str, Any]:
        source_focus = (
            value.get("priority_focus")
            if isinstance(value.get("priority_focus"), dict)
            else {}
        )
        if source_focus:
            compact_focus: dict[str, Any] = {}
            for key in ("missing_field", "required_check", "instruction"):
                text = _clean_text(source_focus.get(key), limit=text_limit)
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
        "status": _clean_text(value.get("status"), limit=text_limit),
        "priority_missing_fields": fallback_priority_terms(
            priority_key="priority_missing_fields",
            missed_counts_key="missing_field_missed_counts",
            top_key="top_missing_fields",
            label="field",
        ),
        "priority_required_checks": fallback_priority_terms(
            priority_key="priority_required_checks",
            missed_counts_key="required_check_missed_counts",
            top_key="top_required_checks",
            label="check",
        ),
        "priority_focus": compact_priority_focus(),
        "missing_field_counts": compact_counts("missing_field_counts"),
        "missing_field_missed_counts": compact_counts(
            "missing_field_missed_counts"
        ),
        "required_check_counts": compact_counts("required_check_counts"),
        "required_check_missed_counts": compact_counts(
            "required_check_missed_counts"
        ),
        "top_missing_fields": compact_top("top_missing_fields", label="field"),
        "top_required_checks": compact_top("top_required_checks", label="check"),
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def compact_jue_wiki_repair_contract_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)
    out: dict[str, Any] = {}
    for key in ("version", "status"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)
    for key in (
        "repair_priority_count",
        "top_priority_count",
        "omitted_priority_count",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _safe_int(value.get(key))
    for key in (
        "priority_type_counts",
        "top_priority_type_counts",
        "omitted_priority_type_counts",
    ):
        if isinstance(value.get(key), dict) and value.get(key):
            out[key] = {
                _clean_text(raw_key, limit=80): _safe_int(raw_value)
                for raw_key, raw_value in value.get(key, {}).items()
                if str(raw_key).strip()
            }

    action_plan = value.get("repair_pressure_action_plan")
    if isinstance(action_plan, dict) and action_plan:
        plan: dict[str, Any] = {}
        for key in ("status", "required_response"):
            if action_plan.get(key) not in (None, "", [], {}):
                plan[key] = _clean_text(action_plan.get(key), limit=text_limit)
        for key in (
            "total_priority_count",
            "top_priority_count",
            "omitted_priority_count",
            "action_batch_count",
            "action_batch_total_count",
            "action_batch_omitted_count",
            "action_batch_visible_pressure_count",
        ):
            if action_plan.get(key) not in (None, "", [], {}):
                plan[key] = _safe_int(action_plan.get(key))
        if action_plan.get("action_batch_pressure_visibility_ratio") not in (
            None,
            "",
            [],
            {},
        ):
            plan["action_batch_pressure_visibility_ratio"] = round(
                min(
                    max(
                        _safe_float(
                            action_plan.get("action_batch_pressure_visibility_ratio")
                        ),
                        0.0,
                    ),
                    1.0,
                ),
                4,
            )
        omitted_types = action_plan.get("omitted_priority_type_counts")
        if isinstance(omitted_types, dict) and omitted_types:
            plan["omitted_priority_type_counts"] = {
                _clean_text(raw_key, limit=80): _safe_int(raw_value)
                for raw_key, raw_value in omitted_types.items()
                if str(raw_key).strip()
            }
        batch_type_counts = action_plan.get("action_batch_type_counts")
        if isinstance(batch_type_counts, dict) and batch_type_counts:
            plan["action_batch_type_counts"] = {
                _clean_text(raw_key, limit=120): _safe_int(raw_value)
                for raw_key, raw_value in batch_type_counts.items()
                if str(raw_key).strip()
            }
        elif _normalize_list(value.get("action_batches")):
            inferred_batch_type_counts: dict[str, int] = {}
            for batch in _normalize_list(value.get("action_batches")):
                if not isinstance(batch, dict):
                    continue
                action_type = _clean_text(batch.get("action_type"), limit=120)
                if not action_type:
                    continue
                count = max(_safe_int(batch.get("count")), 0) or 1
                inferred_batch_type_counts[action_type] = (
                    inferred_batch_type_counts.get(action_type, 0) + count
                )
            if inferred_batch_type_counts:
                plan["action_batch_type_counts"] = inferred_batch_type_counts
        batch_scopes = [
            _clean_text(raw_value, limit=40)
            for raw_value in _normalize_list(action_plan.get("action_batch_scopes"))[:8]
            if str(raw_value).strip()
        ]
        if not batch_scopes:
            batch_scopes = []
            for batch in _normalize_list(value.get("action_batches")):
                if not isinstance(batch, dict):
                    continue
                scope = _clean_text(batch.get("scope"), limit=40)
                if scope and scope not in batch_scopes:
                    batch_scopes.append(scope)
                if len(batch_scopes) >= 8:
                    break
        if batch_scopes:
            plan["action_batch_scopes"] = batch_scopes
        batch_warning_counts = action_plan.get("action_batch_warning_counts")
        if isinstance(batch_warning_counts, dict) and batch_warning_counts:
            plan["action_batch_warning_counts"] = {
                _clean_text(raw_key, limit=120): _safe_int(raw_value)
                for raw_key, raw_value in batch_warning_counts.items()
                if str(raw_key).strip() and _safe_int(raw_value) > 0
            }
        elif _normalize_list(value.get("action_batches")):
            inferred_warning_counts: dict[str, int] = {}
            for batch in _normalize_list(value.get("action_batches")):
                if not isinstance(batch, dict):
                    continue
                raw_counts = batch.get("warning_counts")
                if isinstance(raw_counts, dict):
                    for raw_key, raw_value in raw_counts.items():
                        warning = _clean_text(raw_key, limit=120)
                        count = _safe_int(raw_value)
                        if warning and count > 0:
                            inferred_warning_counts[warning] = (
                                inferred_warning_counts.get(warning, 0) + count
                            )
                    continue
                for raw_value in _normalize_list(batch.get("warnings")):
                    warning = _clean_text(raw_value, limit=120)
                    if warning:
                        inferred_warning_counts[warning] = (
                            inferred_warning_counts.get(warning, 0) + 1
                        )
            if inferred_warning_counts:
                plan["action_batch_warning_counts"] = inferred_warning_counts
        max_severity_score = _safe_float(
            action_plan.get("action_batch_max_severity_score")
        )
        if max_severity_score <= 0 and _normalize_list(value.get("action_batches")):
            max_severity_score = max(
                (
                    _safe_float(batch.get("max_severity_score"))
                    for batch in _normalize_list(value.get("action_batches"))
                    if isinstance(batch, dict)
                ),
                default=0.0,
            )
        if max_severity_score > 0:
            plan["action_batch_max_severity_score"] = max_severity_score
        if plan:
            out["repair_pressure_action_plan"] = plan

    repair_loop_effectiveness = value.get("repair_loop_effectiveness")
    if isinstance(repair_loop_effectiveness, dict) and repair_loop_effectiveness:
        loop: dict[str, Any] = {}
        if repair_loop_effectiveness.get("status") not in (None, "", [], {}):
            loop["status"] = _clean_text(
                repair_loop_effectiveness.get("status"),
                limit=text_limit,
            )
        gap_summary = _compact_memory_card_quality_gap_summary_for_prompt(
            repair_loop_effectiveness.get("memory_card_quality_gap_summary"),
            list_limit=item_limit,
            string_limit=text_limit,
        )
        if gap_summary:
            loop["memory_card_quality_gap_summary"] = gap_summary
        if loop:
            out["repair_loop_effectiveness"] = loop

    action_batches: list[dict[str, Any]] = []
    for item in _normalize_list(value.get("action_batches"))[:item_limit]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key in ("scope", "action_type"):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        if item.get("count") not in (None, "", [], {}):
            row["count"] = _safe_int(item.get("count"))
        if item.get("max_severity_score") not in (None, "", [], {}):
            max_severity_score = _safe_float(item.get("max_severity_score"))
            if max_severity_score > 0:
                row["max_severity_score"] = max_severity_score
        raw_warning_counts = item.get("warning_counts")
        if isinstance(raw_warning_counts, dict) and raw_warning_counts:
            warning_counts = {
                _clean_text(raw_key, limit=text_limit): _safe_int(raw_value)
                for raw_key, raw_value in raw_warning_counts.items()
                if str(raw_key).strip() and _safe_int(raw_value) > 0
            }
            if warning_counts:
                row["warning_counts"] = warning_counts
        for source_key, target_key, max_len, value_limit in (
            ("symbols", "symbols", 20, max(item_limit, 8)),
            ("warnings", "warnings", text_limit, max(item_limit, 4)),
            (
                "recommended_actions",
                "recommended_actions",
                text_limit,
                max(item_limit, 4),
            ),
            ("priority_types", "priority_types", 80, max(item_limit, 4)),
        ):
            values = [
                _clean_text(raw_value, limit=max_len)
                for raw_value in _normalize_list(item.get(source_key))[:value_limit]
                if str(raw_value).strip()
            ]
            if values:
                row[target_key] = values
        if row:
            action_batches.append(row)
    if action_batches:
        out["action_batches"] = action_batches
        raw_action_batch_count = len(_normalize_list(value.get("action_batches")))
        source_action_batch_total_count = _safe_int(
            value.get("action_batch_total_count")
        ) or _safe_int(
            (action_plan or {}).get("action_batch_total_count")
            if isinstance(action_plan, dict)
            else None
        )
        if source_action_batch_total_count > 0:
            out["action_batch_total_count"] = source_action_batch_total_count
        source_action_batch_omitted_count = _safe_int(
            value.get("action_batch_omitted_count")
        ) or _safe_int(
            (action_plan or {}).get("action_batch_omitted_count")
            if isinstance(action_plan, dict)
            else None
        )
        visible_action_batch_omitted_count = max(
            raw_action_batch_count - len(action_batches),
            0,
        )
        visible_action_batch_pressure_count = sum(
            max(_safe_int(row.get("count")), 0) for row in action_batches
        )
        if visible_action_batch_pressure_count <= 0 and action_batches:
            visible_action_batch_pressure_count = len(action_batches)
        if source_action_batch_total_count <= 0:
            source_action_batch_total_count = visible_action_batch_pressure_count
        total_action_batch_omitted_count = (
            source_action_batch_omitted_count + visible_action_batch_omitted_count
        )
        visibility_ratio = (
            round(
                min(
                    max(
                        visible_action_batch_pressure_count
                        / source_action_batch_total_count,
                        0.0,
                    ),
                    1.0,
                ),
                4,
            )
            if source_action_batch_total_count > 0
            else 0.0
        )
        out["action_batch_visible_pressure_count"] = (
            visible_action_batch_pressure_count
        )
        out["action_batch_pressure_visibility_ratio"] = visibility_ratio
        out["action_batch_omitted_count"] = total_action_batch_omitted_count
        if isinstance(out.get("repair_pressure_action_plan"), dict):
            if source_action_batch_total_count > 0:
                out["repair_pressure_action_plan"][
                    "action_batch_total_count"
                ] = source_action_batch_total_count
            out["repair_pressure_action_plan"][
                "action_batch_visible_pressure_count"
            ] = visible_action_batch_pressure_count
            out["repair_pressure_action_plan"][
                "action_batch_pressure_visibility_ratio"
            ] = visibility_ratio
            out["repair_pressure_action_plan"][
                "action_batch_omitted_count"
            ] = total_action_batch_omitted_count

    priorities: list[dict[str, Any]] = []
    for item in _normalize_list(value.get("top_priorities"))[:item_limit]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key in (
            "page_id",
            "priority_type",
            "source_id",
            "repair_status",
            "freshness",
            "quality_status",
        ):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        symbols = [
            _clean_text(raw_symbol, limit=20)
            for raw_symbol in _normalize_list(item.get("symbols"))[:item_limit]
            if str(raw_symbol).strip()
        ]
        if symbols:
            row["symbols"] = symbols
        for key in ("repair_action", "reason", "why"):
            raw_text = str(item.get(key) or "").strip()
            if not raw_text:
                continue
            if len(raw_text) <= text_limit:
                row[key] = _clean_text(raw_text, limit=text_limit)
            else:
                row[f"{key}_omitted_for_prompt_budget"] = True
        if row:
            priorities.append(row)
    if priorities:
        out["top_priorities"] = priorities
        out["top_priority_omitted_count"] = max(
            len(_normalize_list(value.get("top_priorities"))) - len(priorities),
            0,
        )
    return out


def compact_jue_wiki_memory_card_quality_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)
    out: dict[str, Any] = {}

    def compact_string_list(
        raw: Any,
        *,
        limit: int,
        max_len: int = 120,
    ) -> list[str]:
        values: list[str] = []
        for item in _normalize_list(raw)[: max(int(limit), 0)]:
            text = _clean_text(item, limit=max_len)
            if text and text not in values:
                values.append(text)
        return values

    def compact_missing_fields_by_symbol(raw: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in _normalize_list(raw)[:item_limit]:
            if not isinstance(item, dict):
                continue
            symbol = clean_symbol_list([item.get("symbol")], max_items=1)
            if not symbol:
                continue
            row: dict[str, Any] = {"symbol": symbol[0]}
            for key in ("status", "quality"):
                if item.get(key) not in (None, "", [], {}):
                    row[key] = _clean_text(item.get(key), limit=80)
            missing_fields = compact_string_list(
                item.get("missing_fields"),
                limit=8,
                max_len=80,
            )
            if missing_fields:
                row["missing_fields"] = missing_fields
            required_checks = compact_string_list(
                item.get("required_checks"),
                limit=8,
                max_len=text_limit,
            )
            if required_checks:
                row["required_checks"] = required_checks
            rows.append(row)
        return rows

    for key in ("version", "status"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)

    summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
    compact_summary: dict[str, Any] = {}
    for key in ("version", "status"):
        if summary.get(key) not in (None, "", [], {}):
            compact_summary[key] = _clean_text(summary.get(key), limit=text_limit)
    status_counts = (
        summary.get("status_counts")
        if isinstance(summary.get("status_counts"), dict)
        else {}
    )
    if status_counts:
        compact_summary["status_counts"] = {
            _clean_text(raw_key, limit=40): _safe_int(raw_value)
            for raw_key, raw_value in status_counts.items()
            if str(raw_key).strip()
        }
    missing_field_counts = (
        summary.get("missing_field_counts")
        if isinstance(summary.get("missing_field_counts"), dict)
        else {}
    )
    if missing_field_counts:
        compact_summary["missing_field_counts"] = {
            _clean_text(raw_key, limit=60): _safe_int(raw_value)
            for raw_key, raw_value in missing_field_counts.items()
            if str(raw_key).strip()
        }
    weak_symbols = clean_symbol_list(
        summary.get("weak_symbols"),
        max_items=item_limit,
    )
    if weak_symbols:
        compact_summary["weak_symbols"] = weak_symbols
    missing_fields_by_symbol = compact_missing_fields_by_symbol(
        summary.get("missing_fields_by_symbol")
    )
    if missing_fields_by_symbol:
        compact_summary["missing_fields_by_symbol"] = missing_fields_by_symbol
    rows: list[dict[str, Any]] = []
    for item in _normalize_list(summary.get("rows"))[:item_limit]:
        if not isinstance(item, dict):
            continue
        symbol = clean_symbol_list([item.get("symbol")], max_items=1)
        if not symbol:
            continue
        row = {"symbol": symbol[0]}
        for key in ("quality", "required_action"):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        missing_fields = compact_string_list(
            item.get("missing_fields"),
            limit=8,
            max_len=80,
        )
        if missing_fields:
            row["missing_fields"] = missing_fields
        required_checks = compact_string_list(
            item.get("required_checks"),
            limit=8,
            max_len=text_limit,
        )
        if required_checks:
            row["required_checks"] = required_checks
        reason = str(item.get("reason") or "").strip()
        if reason and len(reason) <= text_limit:
            row["reason"] = _clean_text(reason, limit=text_limit)
        elif reason:
            row["reason_omitted_for_prompt_budget"] = True
        rows.append(row)
    if rows:
        compact_summary["rows"] = rows
    if compact_summary:
        out["summary"] = compact_summary

    action_plan = (
        value.get("action_plan") if isinstance(value.get("action_plan"), dict) else {}
    )
    compact_plan: dict[str, Any] = {}
    for key in ("status", "required_action", "decision_policy"):
        if action_plan.get(key) not in (None, "", [], {}):
            compact_plan[key] = _clean_text(action_plan.get(key), limit=text_limit)
    plan_symbols = clean_symbol_list(action_plan.get("symbols"), max_items=item_limit)
    if plan_symbols:
        compact_plan["symbols"] = plan_symbols
    plan_missing_fields = compact_missing_fields_by_symbol(
        action_plan.get("missing_fields_by_symbol")
    )
    if plan_missing_fields:
        compact_plan["missing_fields_by_symbol"] = plan_missing_fields
    required_checks = compact_string_list(
        action_plan.get("required_checks"),
        limit=item_limit * 3,
        max_len=text_limit,
    )
    if required_checks:
        compact_plan["required_checks"] = required_checks
    reason = str(action_plan.get("reason") or "").strip()
    if reason and len(reason) <= text_limit:
        compact_plan["reason"] = _clean_text(reason, limit=text_limit)
    elif reason:
        compact_plan["reason_omitted_for_prompt_budget"] = True
    if compact_plan:
        out["action_plan"] = compact_plan
    return out


def compact_jue_wiki_selection_observation_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)
    out: dict[str, Any] = {}
    for key in (
        "status",
        "selection_run_id",
        "target_scope",
        "prompt_mode",
        "configured_prompt_mode",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)
    repair_contract = compact_jue_wiki_repair_contract_prompt(
        {"action_batches": value.get("repair_action_batches")},
        list_limit=item_limit,
        string_limit=text_limit,
    )
    if repair_contract.get("action_batches"):
        out["repair_action_batches"] = repair_contract["action_batches"]
    evidence_quality = value.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        quality: dict[str, Any] = {}
        if evidence_quality.get("summary_line") not in (None, "", [], {}):
            quality["summary_line"] = _clean_text(
                evidence_quality.get("summary_line"),
                limit=text_limit,
            )
        status_counts = evidence_quality.get("status_counts")
        if isinstance(status_counts, dict) and status_counts:
            quality["status_counts"] = {
                _clean_text(raw_key, limit=40): _safe_int(raw_value)
                for raw_key, raw_value in status_counts.items()
                if str(raw_key).strip() and _safe_int(raw_value) > 0
            }
        top_warnings = [
            _clean_text(raw_warning, limit=text_limit)
            for raw_warning in _normalize_list(evidence_quality.get("top_warnings"))[
                :item_limit
            ]
            if str(raw_warning).strip()
        ]
        if top_warnings:
            quality["top_warnings"] = top_warnings
        if quality:
            out["evidence_quality"] = quality
    pages = [
        {
            key: row.get(key)
            for key in ("page_id", "rank", "score", "char_count")
            if row.get(key) not in (None, "", [], {})
        }
        for row in _normalize_list(value.get("pages"))[:item_limit]
        if isinstance(row, dict)
    ]
    if pages:
        out["pages"] = pages
    budget_report = value.get("budget_report")
    if isinstance(budget_report, dict) and budget_report:
        out["budget_report"] = {
            key: _safe_int(budget_report.get(key))
            for key in (
                "selected_count",
                "repair_priority_total_count",
                "repair_priority_selected_count",
                "repair_priority_omitted_count",
            )
            if budget_report.get(key) not in (None, "", [], {})
        }
    return out


def compact_prompt_section(
    section: str,
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> Any:
    if section == "blocks":
        return [
            compact_prompt_block(row)
            for row in _normalize_list(value)[: max(int(list_limit), 0)]
        ]
    if section == "recent_events":
        events = compact_prompt_events(
            _normalize_list(value),
            limit=max(int(list_limit), 0),
        )
        return compact_etf_prompt_value(
            events,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "quotes":
        return [
            compact_prompt_quote(row)
            for row in _normalize_list(value)[: max(int(list_limit), 0)]
        ]
    if section == "investment_memory":
        return compact_investment_memory_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki":
        return compact_jue_wiki_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_application":
        return compact_jue_wiki_application_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_requested_symbol_coverage":
        return compact_requested_symbol_coverage_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_repair_contract":
        return compact_jue_wiki_repair_contract_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_memory_card_quality":
        return compact_jue_wiki_memory_card_quality_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_selection_observation":
        return compact_jue_wiki_selection_observation_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "daily_discovery":
        return compact_daily_discovery_prompt(
            value if isinstance(value, dict) else {},
            item_limit=max(int(list_limit), 0),
            block_candidate_limit=max(min(int(list_limit), 12), 0),
            pre_surge_candidate_limit=max(min(int(list_limit), 16), 0),
        )
    if section == "live_authority":
        return compact_live_authority_prompt_value(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_workflow":
        return _compact_kis_jue_workflow_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    return compact_etf_prompt_value(
        value,
        list_limit=list_limit,
        string_limit=string_limit,
    )


def enforce_prompt_budget(
    prompt: dict[str, Any],
    *,
    max_chars: int,
) -> None:
    configured_max = max(int(max_chars), 10_000)
    max_allowed = max(configured_max - 2_500, 10_000)
    if len(_json_dumps(prompt)) <= max_allowed:
        return
    compacted: list[dict[str, Any]] = []
    for section, list_limit, string_limit in PROMPT_BUDGET_COMPACTION_ORDER:
        if section not in prompt:
            continue
        before = len(_json_dumps(prompt.get(section)))
        if before <= 0:
            continue
        prompt[section] = compact_prompt_section(
            section,
            prompt.get(section),
            list_limit=list_limit,
            string_limit=string_limit,
        )
        after = len(_json_dumps(prompt.get(section)))
        if after < before:
            compacted.append(
                {
                    "section": section,
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    if len(_json_dumps(prompt)) > max_allowed:
        if "candidate_policy_impacts" in prompt:
            before = len(_json_dumps(prompt.get("candidate_policy_impacts")))
            impacts = prompt.get("candidate_policy_impacts")
            if isinstance(impacts, dict):
                compact_impacts: dict[str, Any] = {}
                if "_global" in impacts:
                    compact_impacts["_global"] = compact_etf_prompt_value(
                        impacts.get("_global"),
                        list_limit=2,
                        string_limit=100,
                    )
                for key, rows in impacts.items():
                    if key == "_global":
                        continue
                    if len(compact_impacts) >= 25:
                        break
                    compact_impacts[str(key)] = compact_etf_prompt_value(
                        rows,
                        list_limit=2,
                        string_limit=100,
                    )
                prompt["candidate_policy_impacts"] = compact_impacts
            else:
                prompt["candidate_policy_impacts"] = compact_etf_prompt_value(
                    impacts,
                    list_limit=12,
                    string_limit=100,
                )
            after = len(_json_dumps(prompt.get("candidate_policy_impacts")))
            if after < before:
                compacted.append(
                    {
                        "section": "candidate_policy_impacts:hard",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
        hard_compaction_order: tuple[tuple[str, int, int], ...] = (
            ("decision_packet", 3, 110),
            ("decision_packet_v2", 3, 110),
            ("pre_adoption_symbol_analysis", 4, 110),
            ("investment_memory", 3, 110),
            ("jue_wiki", 2, 100),
            ("jue_wiki_memory_card_quality", 3, 100),
            ("jue_wiki_repair_contract", 4, 100),
            ("research_spine", 4, 120),
            ("opportunity_research_brief", 3, 110),
            ("market_judgment", 3, 110),
            ("market_pulse", 3, 110),
            ("aggressive_opportunities", 6, 110),
            ("daily_discovery", 3, 110),
            ("policy_rules", 5, 100),
            ("portfolio_balance", 5, 100),
            ("etf_universe", 24, 80),
            ("etf_research", 4, 110),
            ("missed_upside_reviews", 3, 100),
            ("creative_hypotheses", 3, 100),
            ("trading_playbook", 3, 100),
            ("strategy", 6, 110),
            ("recent_events", 16, 110),
        )
        for section, list_limit, string_limit in hard_compaction_order:
            if section not in prompt:
                continue
            before = len(_json_dumps(prompt.get(section)))
            if before <= 0:
                continue
            prompt[section] = compact_prompt_section(
                section,
                prompt.get(section),
                list_limit=list_limit,
                string_limit=string_limit,
            )
            after = len(_json_dumps(prompt.get(section)))
            if after < before:
                compacted.append(
                    {
                        "section": f"{section}:hard",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
            if len(_json_dumps(prompt)) <= max_allowed:
                break
    if len(_json_dumps(prompt)) > max_allowed:
        emergency_order: tuple[tuple[str, int, int], ...] = (
            ("blocks", 6, 120),
            ("candidate_policy_impacts", 8, 70),
            ("pre_adoption_symbol_analysis", 2, 70),
            ("investment_memory", 2, 70),
            ("jue_wiki", 1, 70),
            ("jue_wiki_memory_card_quality", 2, 70),
            ("policy_rules", 3, 70),
            ("decision_packet", 2, 70),
            ("decision_packet_v2", 2, 70),
            ("research_spine", 3, 80),
            ("opportunity_research_brief", 2, 70),
            ("market_judgment", 2, 70),
            ("portfolio_balance", 3, 70),
            ("etf_universe", 12, 60),
            ("missed_upside_reviews", 2, 70),
            ("creative_hypotheses", 2, 70),
            ("trading_playbook", 2, 70),
            ("live_authority", 2, 70),
            ("jue_workflow", 2, 70),
            ("output_schema", 2, 70),
            ("quotes", 10, 70),
            ("kr_pattern_lab", 2, 70),
            ("market_pulse", 2, 70),
            ("aggressive_opportunities", 4, 70),
            ("daily_discovery", 2, 70),
            ("etf_research", 2, 70),
            ("strategy", 3, 70),
            ("recent_events", 8, 70),
        )
        for section, list_limit, string_limit in emergency_order:
            if section not in prompt:
                continue
            before = len(_json_dumps(prompt.get(section)))
            if before <= 0:
                continue
            prompt[section] = compact_prompt_section(
                section,
                prompt.get(section),
                list_limit=list_limit,
                string_limit=string_limit,
            )
            after = len(_json_dumps(prompt.get(section)))
            if after < before:
                compacted.append(
                    {
                        "section": f"{section}:emergency",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
            if len(_json_dumps(prompt)) <= max_allowed:
                break
    if len(_json_dumps(prompt)) > max_allowed:
        for section in PROMPT_BUDGET_OMITTABLE_SECTIONS:
            if section not in prompt:
                continue
            before = len(_json_dumps(prompt.get(section)))
            if before <= 0:
                continue
            prompt[section] = {
                "status": "omitted_for_prompt_budget",
                "original_chars": before,
            }
            after = len(_json_dumps(prompt.get(section)))
            compacted.append(
                {
                    "section": f"{section}:omitted",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
            if len(_json_dumps(prompt)) <= max_allowed:
                break
    if compacted:
        prompt["prompt_compaction"] = {
            "version": "prompt_compaction_v1",
            "max_chars": configured_max,
            "effective_max_chars": max_allowed,
            "sections": compacted,
            "final_chars_before_budget": len(_json_dumps(prompt)),
        }


def attach_prompt_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
) -> None:
    attach_manager_prompt_budget(
        prompt,
        target_chars=target_chars,
        warn_chars=warn_chars,
        max_chars=max_chars,
        section_size_rows=prompt_section_size_rows,
        prompt_chars=prompt_chars,
        policy=(
            "research_spine is the primary research context; duplicated raw "
            "strategy/discovery context should stay reference-sized."
        ),
        required_sections=("opportunity_research_brief", "research_spine", "strategy"),
    )


def prompt_budget_error(prompt: dict[str, Any]) -> str:
    return manager_prompt_budget_error(prompt)


def format_prompt_budget_alert_message(
    *,
    venue: str,
    run_id: int,
    error_message: str,
    prompt: dict[str, Any],
) -> str:
    return build_format_prompt_budget_alert_message(
        venue=venue,
        run_id=run_id,
        error_message=error_message,
        prompt=prompt,
    )


def extend_prompt_compaction(
    prompt: dict[str, Any],
    *,
    max_chars: int,
    effective_max_chars: int,
    sections: list[dict[str, Any]],
) -> None:
    if not sections:
        return
    current = (
        dict(prompt.get("prompt_compaction"))
        if isinstance(prompt.get("prompt_compaction"), dict)
        else {}
    )
    existing_sections = list(current.get("sections") or [])
    current.update(
        {
            "version": str(current.get("version") or "prompt_compaction_v1"),
            "max_chars": int(current.get("max_chars") or max_chars),
            "effective_max_chars": int(
                current.get("effective_max_chars") or effective_max_chars
            ),
            "sections": [*existing_sections, *sections],
            "final_chars_before_budget": len(_json_dumps(prompt)),
        }
    )
    prompt["prompt_compaction"] = current


def omit_largest_prompt_sections_for_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    include_critical_opportunity: bool = False,
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    target = max(int(target_chars), 10_000)
    omittable_sections = tuple(PROMPT_BUDGET_OMITTABLE_SECTIONS)
    if include_critical_opportunity:
        for section in PROMPT_BUDGET_CRITICAL_OPPORTUNITY_SECTIONS:
            if section not in prompt:
                continue
            value = prompt.get(section)
            if (
                isinstance(value, dict)
                and value.get("status") == "omitted_for_prompt_budget"
            ):
                continue
            before = len(_json_dumps(value))
            if before <= 0:
                continue
            prompt[section] = compact_prompt_section(
                section,
                value,
                list_limit=1,
                string_limit=48,
            )
            after = len(_json_dumps(prompt.get(section)))
            if after < before:
                compacted.append(
                    {
                        "section": f"{section}:critical_preserved",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
    while len(_json_dumps(prompt)) > target:
        candidates: list[tuple[int, str]] = []
        for section in omittable_sections:
            if section not in prompt:
                continue
            value = prompt.get(section)
            if (
                isinstance(value, dict)
                and value.get("status") == "omitted_for_prompt_budget"
            ):
                continue
            before = len(_json_dumps(value))
            if before > 0:
                candidates.append((before, section))
        if not candidates:
            break
        before, section = max(candidates, key=lambda row: row[0])
        prompt[section] = {
            "status": "omitted_for_prompt_budget",
            "original_chars": before,
            "stage": "final_budget_guarantee",
        }
        after = len(_json_dumps(prompt.get(section)))
        compacted.append(
            {
                "section": f"{section}:final_omitted",
                "before_chars": before,
                "after_chars": after,
            }
        )
    return compacted


def compact_prompt_sections_for_warn_budget(
    prompt: dict[str, Any],
    *,
    sections_to_compact: tuple[tuple[str, int, int], ...],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for section, list_limit, string_limit in sections_to_compact:
        if section not in prompt:
            continue
        value = prompt.get(section)
        if (
            isinstance(value, dict)
            and value.get("status") == "omitted_for_prompt_budget"
        ):
            continue
        before = len(_json_dumps(value))
        if before <= 0:
            continue
        prompt[section] = compact_prompt_section(
            section,
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
        after = len(_json_dumps(prompt.get(section)))
        if after < before:
            compacted.append(
                {
                    "section": f"{section}:warn_compact",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    return compacted


def compact_live_authority_prompt_value(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def scalar_payload(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}
        compact: dict[str, Any] = {}
        for key in keys:
            child = source.get(key)
            if child in (None, "", [], {}):
                continue
            if isinstance(child, str):
                compact[key] = _clean_text(child, limit=string_limit)
            elif isinstance(child, (int, float, bool)):
                compact[key] = child
        return compact

    def text_list(source: Any, *, limit: int | None = None) -> list[str]:
        return [
            _clean_text(row, limit=string_limit)
            for row in _normalize_list(source)[: max(int(limit or list_limit), 0)]
            if _clean_text(row, limit=string_limit)
        ]

    def compact_gate(source: Any) -> dict[str, Any]:
        gate = scalar_payload(
            source,
            (
                "status",
                "blocks_scale_up",
                "requires_live_shadow",
                "requires_waiting_entry",
                "cap_multiplier",
                "active_sample_count",
                "effective_sample_count",
                "legacy_proxy_sample_count",
                "pending_block_count",
                "min_samples_to_scale",
                "entry_mode",
                "risk_budget_mode",
                "entry_policy",
                "scale_policy",
            ),
        )
        if isinstance(source, dict):
            for key in ("focus_reasons", "weak_validation_ids", "scale_blocked_discipline_ids"):
                rows = text_list(source.get(key), limit=2)
                if rows:
                    gate[key] = rows
        return gate

    def compact_lane_action(source: Any) -> dict[str, Any]:
        action = scalar_payload(
            source,
            (
                "grade",
                "action",
                "sample_count",
                "max_budget_multiplier",
                "applied_max_budget_multiplier",
                "scale_up_allowed",
                "scale_decision",
                "expectancy_pct",
                "win_rate",
                "max_drawdown_pct",
                "profit_factor",
                "recovery_factor",
                "cost_drag_pct_of_gross_pnl",
                "cost_precision_verified_rate",
                "cost_evidence_status",
                "cost_evidence_repair_hint",
                "cost_hybrid_alpha_count",
                "cost_hybrid_alpha_net_pnl",
                "avg_entry_quality_score",
                "bad_entry_quality_rate_pct",
                "dominant_bad_entry_quality_label",
                "dominant_good_entry_quality_label",
                "validation_evidence_status",
                "validation_evidence_repair_hint",
                "validation_repair_enforced_count",
                "validation_repair_waiting_entry_count",
                "scale_blocked_by_performance_evidence",
                "scale_blocked_by_cost_precision",
                "scale_blocked_by_cost_evidence",
                "scale_blocked_by_entry_quality",
                "scale_blocked_by_validation_evidence",
                "scale_blocked_by_validation_repair",
                "requires_waiting_entry",
                "scale_up_blocked_by_shadow_gate",
                "scale_up_blocked_by_exposure_gate",
                "scale_up_blocked_by_validation_remediation",
                "scale_up_blocked_by_active_revision",
            ),
        )
        if isinstance(source, dict):
            for key in (
                "scale_blockers",
                "scale_repair_targets",
                "performance_weak_metrics",
                "performance_scale_blocking_metrics",
                "cost_repair_targets",
                "entry_repair_targets",
                "validation_evidence_repair_targets",
                "validation_missing_dimensions",
                "core_validation_evidence_gaps",
            ):
                rows = text_list(source.get(key), limit=3)
                if rows:
                    action[key] = rows
            passport = scalar_payload(
                source.get("risk_budget_passport"),
                (
                    "raw_fractional_kelly_fraction",
                    "kelly_cap_multiplier",
                    "drawdown_cap_multiplier",
                    "recovery_factor_cap_multiplier",
                    "ruin_cap_multiplier",
                    "risk_of_ruin_pct",
                    "lane_confidence_score",
                    "applied_risk_budget_multiplier",
                    "recommended_risk_fraction",
                    "max_risk_cap_fraction",
                    "risk_fraction_cap_multiplier",
                    "cost_precision_cap_multiplier",
                    "verified_edge_sample_cap_multiplier",
                    "verified_edge_net_cap_multiplier",
                    "entry_quality_cap_multiplier",
                    "validation_shadow_cap_multiplier",
                    "validation_exposure_cap_multiplier",
                    "validation_remediation_cap_multiplier",
                    "active_revision_cap_multiplier",
                    "effective_risk_budget_multiplier",
                    "scale_decision",
                    "active_revision_gate_status",
                    "validation_shadow_gate_status",
                    "validation_exposure_gate_status",
                    "validation_remediation_gate_status",
                ),
            )
            if isinstance(source.get("risk_budget_passport"), dict):
                for key in ("scale_blockers", "scale_repair_targets"):
                    rows = text_list(source["risk_budget_passport"].get(key), limit=3)
                    if rows:
                        passport[key] = rows
            if passport:
                action["risk_budget_passport"] = passport
            for key in (
                "validation_shadow_gate",
                "validation_exposure_gate",
                "validation_remediation_gate",
                "active_revision_gate",
            ):
                gate = compact_gate(source.get(key))
                if gate:
                    action[key] = gate
        return action

    def compact_lane_authority(source: Any) -> dict[str, Any]:
        authority = scalar_payload(
            source,
            (
                "version",
                "global_scale_up_allowed",
                "max_budget_multiplier",
                "validation_gate_status",
                "execution_posture",
                "probe_policy",
                "probe_lane_count",
                "scale_blocked_lane_count",
                "lane_action_count",
                "weak_lane_count",
                "blocked_lane_count",
            ),
        )
        if not isinstance(source, dict):
            return authority
        for key in (
            "probe_lane_names",
            "scale_blocked_lanes",
            "weak_lanes",
            "insufficient_lanes",
            "validation_evidence_weak_lanes",
            "shadow_blocked_lanes",
            "exposure_blocked_lanes",
            "remediation_blocked_lanes",
            "block_design_requirements",
        ):
            rows = text_list(source.get(key), limit=4)
            if rows:
                authority[key] = rows
        raw_actions = source.get("lane_actions")
        if isinstance(raw_actions, dict):
            actions: dict[str, Any] = {}
            for lane, action in list(raw_actions.items())[: max(int(list_limit), 0)]:
                if not isinstance(action, dict):
                    continue
                lane_key = _clean_text(lane, limit=80)
                if lane_key:
                    actions[lane_key] = compact_lane_action(action)
            if actions:
                authority["lane_actions"] = actions
        for key in (
            "validation_shadow_gate",
            "validation_exposure_gate",
            "validation_remediation_gate",
            "active_revision_gate",
        ):
            gate = compact_gate(source.get(key))
            if gate:
                authority[key] = gate
        return authority

    def compact_validation_pressure(source: Any) -> dict[str, Any]:
        pressure = scalar_payload(
            source,
            (
                "version",
                "severity",
                "gate_status",
                "readiness",
                "risk_governor_action",
                "hard_block",
                "hard_fail_count",
                "hard_blocking_count",
                "scale_up_allowed",
                "entry_posture",
                "sizing_posture",
                "remediation_entry_mode",
                "remediation_risk_budget_mode",
                "instruction",
            ),
        )
        if not isinstance(source, dict):
            return pressure
        for key in ("fail_ids", "warn_ids", "missing_ids", "block_design_requirements"):
            rows = text_list(source.get(key), limit=5)
            if rows:
                pressure[key] = rows
        actions = []
        for row in _normalize_list(source.get("discipline_actions"))[:8]:
            if not isinstance(row, dict):
                continue
            actions.append(
                scalar_payload(
                    row,
                    (
                        "id",
                        "status",
                        "entry_constraint",
                        "sizing_constraint",
                        "repair_action",
                        "block_design_focus",
                    ),
                )
            )
        if actions:
            pressure["discipline_actions"] = actions
        return pressure

    def remediation_discipline_actions(source: Any) -> list[dict[str, Any]]:
        if not isinstance(source, dict):
            return []
        raw_rows: list[Any] = []
        raw_rows.extend(_normalize_list(source.get("work_queue")))
        for category in _normalize_list(source.get("categories")):
            if isinstance(category, dict):
                raw_rows.extend(_normalize_list(category.get("items")))

        actions: list[dict[str, Any]] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            discipline_id = _clean_text(
                row.get("discipline_id") or row.get("id"),
                limit=80,
            )
            if not discipline_id:
                continue
            template = VALIDATION_DISCIPLINE_BLOCK_ACTIONS.get(
                discipline_id,
                {
                    "entry_constraint": "prefer_waiting_entry_until_validation_repairs",
                    "sizing_constraint": "no_scale_up_until_discipline_passes",
                    "repair_action": "repair_validation_evidence_before_size_increase",
                    "block_design_focus": "verified_edge_quality_before_execution",
                },
            )
            action = {
                "id": discipline_id,
                "status": _clean_text(row.get("status") or "missing", limit=40),
                "entry_constraint": template["entry_constraint"],
                "sizing_constraint": template["sizing_constraint"],
                "repair_action": template["repair_action"],
                "block_design_focus": template["block_design_focus"],
            }
            diagnostic_action = _clean_text(
                row.get("action")
                or row.get("runner_hint")
                or row.get("lane_policy_hint")
                or row.get("exit_criteria"),
                limit=string_limit,
            )
            if diagnostic_action:
                action["diagnostic_action"] = diagnostic_action
            actions.append(action)
        return actions

    def compact_validation_gate(source: Any) -> dict[str, Any]:
        gate = scalar_payload(
            source,
            (
                "status",
                "readiness",
                "reason",
                "fail_count",
                "discipline_count",
                "expected_discipline_count",
                "risk_governor_action",
                "risk_governor_source",
            ),
        )
        if not isinstance(source, dict):
            return gate
        for key in ("risk_governor_reasons", "operator_guidance"):
            rows = text_list(source.get(key), limit=3)
            if rows:
                gate[key] = rows
        pressure = compact_validation_pressure(source.get("validation_pressure"))
        remediation_actions = remediation_discipline_actions(
            source.get("remediation_plan")
        )
        if remediation_actions:
            existing_actions = (
                pressure.get("discipline_actions")
                if isinstance(pressure.get("discipline_actions"), list)
                else []
            )
            existing_ids = {
                str(row.get("id") or "")
                for row in existing_actions
                if isinstance(row, dict)
            }
            for action in remediation_actions:
                if str(action.get("id") or "") in existing_ids:
                    continue
                existing_actions.append(action)
                existing_ids.add(str(action.get("id") or ""))
            pressure["discipline_actions"] = existing_actions[:10]
        if pressure:
            gate["validation_pressure"] = pressure
        passport = scalar_payload(
            source.get("validation_passport"),
            (
                "version",
                "status",
                "readiness",
                "score",
                "expected_count",
                "actual_count",
                "pass_count",
                "warn_count",
                "fail_count",
                "missing_count",
                "requires_revalidation",
            ),
        )
        if passport:
            gate["validation_passport"] = passport
        remediation = scalar_payload(
            source.get("remediation_plan"),
            (
                "status",
                "primary_next_action",
                "weak_count",
                "failed_count",
                "missing_count",
            ),
        )
        if isinstance(source.get("remediation_plan"), dict):
            work = []
            for row in _normalize_list(source["remediation_plan"].get("work_queue"))[:2]:
                if not isinstance(row, dict):
                    continue
                work.append(
                    scalar_payload(
                        row,
                        (
                            "task_id",
                            "discipline_id",
                            "status",
                            "priority",
                            "owner",
                            "lane_policy_hint",
                            "blocks_scaling",
                            "blocks_new_entries",
                            "runner_hint",
                            "exit_criteria",
                        ),
                    )
                )
            if work:
                remediation["work_queue"] = work
        if remediation:
            gate["remediation_plan"] = remediation
        return gate

    def compact_performance_lanes(source: Any) -> list[dict[str, Any]]:
        lanes = []
        for row in _normalize_list(source)[: max(min(int(list_limit), 4), 0)]:
            if not isinstance(row, dict):
                continue
            lanes.append(
                scalar_payload(
                    row,
                    (
                        "lane",
                        "quality_hint",
                        "action_hint",
                        "sample_count",
                        "expectancy_pct",
                        "win_rate_pct",
                        "profit_factor",
                        "max_drawdown_pct",
                        "recovery_factor",
                        "risk_budget_multiplier",
                        "risk_of_ruin_pct",
                        "cost_drag_pct_of_gross_pnl",
                        "cost_evidence_status",
                        "avg_entry_quality_score",
                        "bad_entry_quality_rate_pct",
                    ),
                )
            )
        return [row for row in lanes if row]

    payload = scalar_payload(
        value,
        (
            "status",
            "live_grade",
            "allow_scale_up",
            "max_budget_multiplier",
            "scorecard_count",
        ),
    )
    lane_authority = compact_lane_authority(value.get("lane_authority"))
    if lane_authority:
        payload["lane_authority"] = lane_authority
    validation_gate = compact_validation_gate(value.get("validation_gate"))
    if validation_gate:
        payload["validation_gate"] = validation_gate
    active_revision = compact_prompt_value(
        value.get("active_revision_evidence"),
        list_limit=min(max(int(list_limit), 1), 3),
        string_limit=string_limit,
    )
    if active_revision:
        payload["active_revision_evidence"] = active_revision
    performance_lanes = compact_performance_lanes(value.get("performance_lanes"))
    if performance_lanes:
        payload["performance_lanes"] = performance_lanes
    return payload


def finalize_prompt_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
) -> None:
    configured_max = max(int(max_chars), 10_000)
    prompt.pop("prompt_budget", None)
    enforce_prompt_budget(prompt, max_chars=configured_max)
    attach_prompt_budget(
        prompt,
        target_chars=target_chars,
        warn_chars=warn_chars,
        max_chars=configured_max,
    )
    warn_limit = max(int(warn_chars), int(target_chars), 10_000)
    if not prompt_budget_error(prompt) and len(_json_dumps(prompt)) <= warn_limit:
        return

    if not prompt_budget_error(prompt) and len(_json_dumps(prompt)) <= configured_max:
        prompt.pop("prompt_budget", None)
        soft_reserve = 2_500
        effective_warn = max(warn_limit - soft_reserve, 10_000)
        sections = compact_prompt_sections_for_warn_budget(
            prompt,
            sections_to_compact=(
                ("investment_memory", 5, 150),
                ("jue_wiki", 2, 130),
                ("jue_wiki_requested_symbol_coverage", 3, 100),
                ("jue_wiki_repair_contract", 4, 100),
                ("research_spine", 6, 150),
                ("opportunity_research_brief", 4, 140),
                ("daily_discovery", 5, 150),
                ("aggressive_opportunities", 5, 150),
                ("strategy", 8, 150),
            ),
        )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max,
            effective_max_chars=effective_warn,
            sections=sections,
        )
        if len(_json_dumps(prompt)) <= effective_warn:
            attach_prompt_budget(
                prompt,
                target_chars=target_chars,
                warn_chars=warn_chars,
                max_chars=configured_max,
            )
            return
        sections = omit_largest_prompt_sections_for_budget(
            prompt,
            target_chars=effective_warn,
            include_critical_opportunity=False,
        )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max,
            effective_max_chars=effective_warn,
            sections=sections,
        )
        attach_prompt_budget(
            prompt,
            target_chars=target_chars,
            warn_chars=warn_chars,
            max_chars=configured_max,
        )
        if (
            not prompt_budget_error(prompt)
            and len(_json_dumps(prompt)) <= configured_max
        ):
            return

    prompt.pop("prompt_budget", None)
    reserve = 12_000
    warn_reserve = 2_500
    effective_max = max(
        min(configured_max - reserve, warn_limit - warn_reserve),
        10_000,
    )
    sections = omit_largest_prompt_sections_for_budget(
        prompt,
        target_chars=effective_max,
        include_critical_opportunity=False,
    )
    if len(_json_dumps(prompt)) > effective_max:
        sections.extend(
            omit_largest_prompt_sections_for_budget(
                prompt,
                target_chars=effective_max,
                include_critical_opportunity=True,
            )
        )
    extend_prompt_compaction(
        prompt,
        max_chars=configured_max,
        effective_max_chars=effective_max,
        sections=sections,
    )
    attach_prompt_budget(
        prompt,
        target_chars=target_chars,
        warn_chars=warn_chars,
        max_chars=configured_max,
    )
    if not prompt_budget_error(prompt) and len(_json_dumps(prompt)) <= configured_max:
        return

    prompt.pop("prompt_budget", None)
    final_target = max(configured_max - 8_000, 10_000)
    final_sections = compact_prompt_sections_for_warn_budget(
        prompt,
        sections_to_compact=(
            ("blocks", 4, 70),
            ("investment_memory", 1, 60),
            ("jue_wiki_requested_symbol_coverage", 2, 60),
            ("jue_wiki_repair_contract", 2, 60),
            ("decision_packet_v2", 1, 60),
            ("live_authority", 1, 60),
            ("validation_repair", 1, 60),
            ("output_schema", 1, 60),
            ("jue_workflow", 1, 60),
            ("prompt_compaction", 1, 60),
            ("market_pulse", 1, 60),
            ("daily_discovery", 1, 60),
            ("aggressive_opportunities", 2, 60),
            ("research_spine", 2, 70),
            ("opportunity_research_brief", 1, 60),
        ),
    )
    if len(_json_dumps(prompt)) > final_target:
        final_sections.extend(
            omit_largest_prompt_sections_for_budget(
                prompt,
                target_chars=final_target,
                include_critical_opportunity=True,
            )
        )
    if len(_json_dumps(prompt)) > final_target:
        for section in (
            "live_authority",
            "output_schema",
            "jue_workflow",
            "validation_repair",
            "decision_packet_v2",
            "investment_memory",
            "blocks",
        ):
            if section not in prompt:
                continue
            before = len(_json_dumps(prompt.get(section)))
            if before <= 700:
                continue
            prompt[section] = {
                "status": "last_resort_compacted_for_prompt_budget",
                "original_chars": before,
            }
            final_sections.append(
                {
                    "section": f"{section}:last_resort",
                    "before_chars": before,
                    "after_chars": len(_json_dumps(prompt.get(section))),
                }
            )
            if len(_json_dumps(prompt)) <= final_target:
                break
    extend_prompt_compaction(
        prompt,
        max_chars=configured_max,
        effective_max_chars=final_target,
        sections=final_sections,
    )
    attach_prompt_budget(
        prompt,
        target_chars=target_chars,
        warn_chars=warn_chars,
        max_chars=configured_max,
    )


def compact_block_lane_authority_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scalar_keys = (
        "version",
        "global_scale_up_allowed",
        "execution_posture",
        "probe_policy",
        "probe_lane_count",
        "scale_blocked_lane_count",
        "max_budget_multiplier",
        "validation_gate_status",
    )
    list_keys = (
        "weak_lanes",
        "probe_lane_names",
        "scale_blocked_lanes",
        "insufficient_lanes",
        "scale_candidate_lanes",
        "qualified_lanes",
        "exposure_blocked_lanes",
        "remediation_blocked_lanes",
        "shadow_blocked_lanes",
        "validation_evidence_weak_lanes",
    )
    out: dict[str, Any] = {
        key: value.get(key)
        for key in scalar_keys
        if value.get(key) not in (None, "", [], {})
    }
    for key in list_keys:
        items = [
            _clean_text(item, limit=80)
            for item in _normalize_list(value.get(key))[:4]
            if _clean_text(item, limit=80)
        ]
        if items:
            out[key] = items
    lane_actions = (
        value.get("lane_actions")
        if isinstance(value.get("lane_actions"), dict)
        else {}
    )
    compact_actions: dict[str, Any] = {}
    for lane, action in list(lane_actions.items())[:2]:
        if not isinstance(action, dict):
            continue
        compact_action = {
            key: action.get(key)
            for key in (
                "action",
                "reason",
                "requires_waiting_entry",
                "scale_up_allowed",
                "budget_multiplier",
                "max_budget_multiplier",
                "qty_cap",
                "performance_quality_hint",
            )
            if action.get(key) not in (None, "", [], {})
        }
        if "reason" in compact_action:
            compact_action["reason"] = _clean_text(
                compact_action["reason"],
                limit=160,
            )
        requirements = [
            _clean_text(item, limit=90)
            for item in _normalize_list(action.get("entry_quality_requirements"))[:4]
            if _clean_text(item, limit=90)
        ]
        if requirements:
            compact_action["entry_quality_requirements"] = requirements
        passport = (
            action.get("risk_budget_passport")
            if isinstance(action.get("risk_budget_passport"), dict)
            else {}
        )
        passport_summary = {
            key: passport.get(key)
            for key in (
                "sample_confidence",
                "applied_risk_budget_multiplier",
                "recommended_risk_fraction",
                "risk_of_ruin_pct",
                "cost_evidence_status",
                "validation_evidence_status",
                "validation_missing_dimensions",
                "scale_blocked_by_validation_evidence",
            )
            if passport.get(key) not in (None, "", [], {})
        }
        if passport_summary:
            compact_action["risk_budget_passport"] = passport_summary
        if compact_action:
            compact_actions[_clean_text(lane, limit=80)] = compact_action
    if compact_actions:
        out["lane_actions"] = compact_actions
    return out


def compact_block_live_authority_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {
        key: value.get(key)
        for key in (
            "status",
            "live_grade",
            "allow_scale_up",
            "max_budget_multiplier",
            "scorecard_count",
            "validation_gate_status",
            "validation_readiness",
            "risk_governor_action",
            "risk_governor_source",
        )
        if value.get(key) not in (None, "", [], {})
    }
    if value.get("validation_gate_reason"):
        out["validation_gate_reason"] = _clean_text(
            value.get("validation_gate_reason"),
            limit=180,
        )
    weak_disciplines = [
        _clean_text(item, limit=80)
        for item in _normalize_list(value.get("weak_disciplines"))[:8]
        if _clean_text(item, limit=80)
    ]
    if weak_disciplines:
        out["weak_disciplines"] = weak_disciplines
    lane_authority = compact_block_lane_authority_metadata(
        value.get("lane_authority")
    )
    if lane_authority:
        out["lane_authority"] = lane_authority
    active_revision = (
        value.get("active_revision_evidence")
        if isinstance(value.get("active_revision_evidence"), dict)
        else {}
    )
    active_revision_summary = {
        key: active_revision.get(key)
        for key in (
            "status",
            "authority_posture",
            "sample_building_gate_mode",
            "active_sample_count",
            "min_samples_to_scale",
            "scale_up_allowed",
        )
        if active_revision.get(key) not in (None, "", [], {})
    }
    if active_revision_summary:
        out["active_revision_evidence"] = active_revision_summary
    return out


def compact_prompt_block(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    payload = compact_prompt_fields(value, PROMPT_BLOCK_KEYS)
    for key in ("thesis", "risk_note", "llm_reason"):
        if key in payload:
            payload[key] = _clean_text(payload.get(key), limit=260)
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    compact_metadata: dict[str, Any] = {}
    for key in PROMPT_BLOCK_METADATA_KEYS:
        if key in metadata:
            if key == "cost_feasibility":
                compact_metadata[key] = compact_prompt_value(
                    metadata.get(key),
                    list_limit=1,
                    string_limit=90,
                )
                continue
            compact_metadata[key] = compact_prompt_value(
                metadata.get(key),
                list_limit=4,
                string_limit=140,
            )
    if metadata.get("applied_policy_versions"):
        compact_metadata["applied_policy_versions"] = compact_prompt_value(
            metadata.get("applied_policy_versions"),
            list_limit=3,
            string_limit=80,
        )
    if metadata.get("policy_rule_impacts"):
        compact_metadata["policy_rule_impacts"] = compact_prompt_value(
            metadata.get("policy_rule_impacts"),
            list_limit=1,
            string_limit=90,
        )
    if metadata.get("live_authority"):
        compact_metadata["live_authority"] = compact_block_live_authority_metadata(
            metadata.get("live_authority"),
        )
    if compact_metadata:
        payload["metadata"] = compact_metadata
    return payload


def prompt_block_needs_detail(value: dict[str, Any]) -> bool:
    status = str(value.get("status") or "").strip()
    if status in VISIBLE_BLOCK_STATUSES:
        return True
    if _safe_float(value.get("qty_open")) > 0:
        return True
    if _safe_bool(value.get("force_exit_requested")):
        return True
    return False


def compact_prompt_block_backlog_item(value: dict[str, Any]) -> dict[str, Any]:
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    payload = {
        "block_id": _clean_text(value.get("block_id"), limit=80),
        "symbol": _clean_text(value.get("symbol"), limit=16),
        "name": _clean_text(value.get("name"), limit=80),
        "status": _clean_text(value.get("status"), limit=40),
        "horizon": _clean_text(metadata.get("horizon"), limit=40),
        "qty_open": value.get("qty_open"),
        "updated_at": _clean_text(value.get("updated_at"), limit=80),
    }
    return {key: item for key, item in payload.items() if item not in ("", None)}


def compact_manager_prompt_blocks(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    detailed: list[dict[str, Any]] = []
    omitted_by_status: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for row in blocks:
        if not isinstance(row, dict):
            continue
        if prompt_block_needs_detail(row):
            compact = compact_prompt_block(row)
            if compact:
                detailed.append(compact)
            continue
        status = str(row.get("status") or "unknown").strip() or "unknown"
        omitted_by_status[status] = omitted_by_status.get(status, 0) + 1
        if len(samples) < 6:
            samples.append(compact_prompt_block_backlog_item(row))
    omitted_count = sum(omitted_by_status.values())
    return detailed, {
        "version": "block_backlog_summary_v1",
        "input_count": len([row for row in blocks if isinstance(row, dict)]),
        "detailed_count": len(detailed),
        "omitted_count": omitted_count,
        "omitted_by_status": omitted_by_status,
        "omitted_rule": (
            "Zero-quantity inactive blocks are summarized here so live blocks, "
            "pre-surge candidates, research, and quotes stay inside the prompt budget."
        ),
        "samples": samples,
    }


def kis_trading_playbook() -> dict[str, Any]:
    return {
        "version": "kis_trading_playbook_v1",
        "style": "aggressive_value_cycle",
        "principles": [
            "비대칭 손익비가 보이면 관망만 하지 말고 실행 가능한 블록이나 매수대기블록으로 구체화한다.",
            "공격성은 추격 매수가 아니라 좋은 종목을 좋은 가격까지 기다리는 인내와 실행력이다.",
            "좋은 기업/ETF 후보를 넓게 탐색하되 실행은 소수의 명확한 블록으로 좁힌다.",
            "기존 블록은 방치하지 않고 추가/유지/축소/청산/대기전환 중 하나의 판단 근거를 남긴다.",
        ],
        "entry_evidence_stack": [
            "business_quality",
            "undervaluation",
            "price_location",
            "upside_catalyst",
            "risk_invalidation",
        ],
        "behavior_separation": {
            "source": "closed_blocks_orders_and_live_authority_performance_lanes",
            "scale_success_patterns": [
                "target_reached",
                "profit_lock_then_target_follow_through",
                "low_risk_waiting_entry_filled_then_target",
            ],
            "scale_success_lanes": [
                "core_etf",
                "value_cycle",
                "long_accumulation",
            ],
            "reduce_loss_patterns": [
                "stop_reached",
                "force_exit_without_invalidation",
                "early_mid_long_exit_noise",
            ],
            "stop_churn_policy": {
                "default_mid_long_action": "hold_or_update_until_signal",
                "requires": [
                    "target_reached",
                    "stop_reached",
                    "profit_giveback",
                    "thesis_invalidated",
                    "current_quote_beyond_target_or_stop",
                ],
                "instruction": (
                    "Do not convert normal intraday noise into churn. For mid, "
                    "long, and core_etf blocks, prefer hold/update/rebalance unless "
                    "a concrete rule signal or thesis invalidation exists."
                ),
            },
            "force_exit_policy": {
                "requires_invalidation": True,
                "allowed_triggers": sorted(MANAGER_CLOSE_ROW_TRIGGERS),
                "instruction": (
                    "Open blocks may be force-exited only with target/stop/profit "
                    "giveback, thesis invalidation, reconciliation cleanup, data "
                    "error, or explicit operator confirmation."
                ),
            },
            "growth_instruction": (
                "Grow what live closed-block data proves: target-reaching designs, "
                "patient value-cycle entries, and core ETF allocation. Reduce "
                "stop-heavy churn and vague discretionary exits."
            ),
        },
        "lanes": {
            "value_cycle": {
                "primary_goal": (
                    "buy_undervalued_quality_at_low_risk_prices_sell_or_trim_when_"
                    "fair_or_overvalued"
                ),
                "research_scope": "broad_scan",
                "execution_scope": "narrow_execution",
                "default_new_entry_style": "wait_for_price",
                "extended_momentum_default": "watch_or_pullback_waiting_block",
                "immediate_entry_exception": {
                    "allowed": True,
                    "requires": [
                        "valuation_not_expensive",
                        "price_not_extended_or_pullback_confirmed",
                        "clear_asymmetric_reward_risk",
                        "specific_invalidation_price",
                    ],
                    "bias": "rare_and_small",
                },
                "execution_funnel": {
                    "wide_research": "many symbols may be studied",
                    "watchlist": "only candidates with enough evidence stay visible",
                    "waiting_blocks": (
                        "preferred for price-sensitive value-cycle entries"
                    ),
                    "live_entries": (
                        "few symbols; require the full entry evidence stack"
                    ),
                },
                "sell_trim_logic": [
                    "trim_or_close_when_price_reaches_fair_value_or_overvaluation",
                    "trim_when_reward_risk_no_longer_asymmetric",
                    "close_when_original_thesis_breaks",
                    "avoid_selling_mid_or_long_blocks_only_because_of_normal_noise",
                ],
            },
            "value_pullback": {
                "default_posture": "patient_value_pullback",
                "preferred_entry_style": "wait_for_price",
                "score_bias": {
                    "reward": [
                        "undervaluation_or_fair_value_discount",
                        "pullback_or_low_risk_price_location",
                        "quality_or_growth_thesis_intact",
                    ],
                    "penalize": [
                        "extended_price_without_pullback",
                        "momentum_only_without_valuation_support",
                        "unclear_invalidation_price",
                    ],
                },
                "reflection_questions": [
                    "Did Jue chase strength instead of waiting for the planned price?",
                    "Was the block supported by undervaluation or only by short-term heat?",
                    "Would a waiting entry or mid/long block have captured the move better?",
                ],
            },
            "pre_surge_discovery": {
                "role": "find_candidates_before_price_surge",
                "default_action": "scout_or_waiting_block",
                "instruction": (
                    "Daily discovery pre_surge candidates are early hypotheses for "
                    "small scout blocks, mid-horizon waiting blocks, or concrete "
                    "watch triggers before the crowd move appears."
                ),
                "block_design": [
                    "Prefer wait_for_price when the current quote is already stretched.",
                    (
                        "Use aggressive_limit only when valuation, price location, "
                        "reward/risk, and invalidation are already clear."
                    ),
                    (
                        "Every pre_surge block needs entry logic, target, stop, "
                        "horizon, and invalidation."
                    ),
                ],
            },
            "long_accumulation": {
                "enabled": True,
                "same_symbol_dual_block_pattern": (
                    "short_profit_block_and_long_runner_block_can_coexist"
                ),
                "entry_guidance": (
                    "Use wait_for_price for pullback accumulation plans and for "
                    "extended momentum names that are attractive only at a better price."
                ),
                "missed_upside_learning": (
                    "When a closed short winner later extends, consider whether a "
                    "small long runner or follow-up waiting entry block should exist."
                ),
            },
            "creative_hypotheses": {
                "enabled": True,
                "required_each_manager_run": True,
                "hypothesis_types": [
                    "leader_pullback",
                    "pullback",
                    "second_rank",
                    "next_sector",
                    "missed_upside",
                    "etf_rotation",
                    "contrarian",
                ],
                "required_questions": [
                    (
                        "If the leader is extended, what second-rank or lagging "
                        "candidate could catch rotation?"
                    ),
                    (
                        "Which strong theme deserves a pullback wait_for_price "
                        "block instead of a chase?"
                    ),
                    (
                        "Which high-quality but undervalued candidate is near a "
                        "low-risk entry zone?"
                    ),
                    (
                        "Which next sector could receive capital after today's "
                        "leaders tire?"
                    ),
                    (
                        "Which recent closed winner should have had a mid/long "
                        "runner block?"
                    ),
                    (
                        "Should this exposure be taken through an ETF/core block "
                        "instead of a single stock?"
                    ),
                ],
            },
            "core_etf": {
                "role": (
                    "ETF/Core blocks are for market exposure, diversification, and "
                    "planned rebalance rather than scalp trading."
                ),
                "target_stop_semantics": (
                    "For core_etf blocks, target_price and stop_price are "
                    "rebalance/risk thresholds, not automatic scalp-style "
                    "take-profit or stop-loss triggers."
                ),
                "decision_inputs": [
                    "current allocation drift versus portfolio_balance targets",
                    "ETF liquidity and order-size suitability",
                    "stale, missing, or error ETF snapshot/score data",
                    "strategy candidates tagged asset_class=etf or horizon_bias=core_etf",
                ],
            },
        },
        "horizon_review": {
            "cadence": "regular_market_30m_full_portfolio",
            "market_hours_only": True,
            "patience_guard": {
                "minimum_live_age_before_discretionary_close": {
                    "mid": "72h",
                    "long": "14d",
                    "core_etf": "7d",
                },
                "early_close_requires": [
                    "target_reached event",
                    "stop_reached event",
                    "profit_giveback event",
                    "current quote already beyond target or stop",
                    "ledger cleanup for a block with no open quantity",
                ],
            },
        },
        "horizon_policy": {
            "short": "intraday to 1 week; tick target/stop allowed",
            "mid": "2 weeks to 3 months; manager-reviewed thesis block",
            "long": "3 months plus; hold/add/rebalance bias unless thesis breaks",
            "core_etf": "ETF/core allocation; rebalance/risk thresholds",
            "cash": "dry powder and volatility buffer",
        },
        "horizon_action_authority": {
            "short": "active_trade",
            "mid": "selective_adjust_or_close",
            "long": "hold_add_rebalance_bias",
            "core_etf": "rebalance_bias",
            "cash": "allocation_buffer",
        },
    }


def compact_validation_repair_prompt(
    memory_context: dict[str, Any],
    *,
    scope: str,
    compact_value: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    compact = compact_value or _identity_compact_value
    if not isinstance(memory_context, dict) or not memory_context:
        return {
            "version": "validation_repair_prompt_v1",
            "scope": scope,
            "status": "missing",
            "repair_backlog": [],
            "block_design_constraints": [],
        }
    backlog = (
        memory_context.get("validation_repair_backlog")
        if isinstance(memory_context.get("validation_repair_backlog"), dict)
        else {}
    )
    constraints = (
        memory_context.get("block_design_constraints")
        if isinstance(memory_context.get("block_design_constraints"), dict)
        else {}
    )
    backlog_items: list[dict[str, Any]] = []
    for row in _normalize_list(backlog.get("items") or backlog.get("primary_items"))[:6]:
        if not isinstance(row, dict):
            continue
        backlog_items.append(
            {
                key: compact(row.get(key), list_limit=3, string_limit=120)
                for key in (
                    "policy_id",
                    "repair_policy_id",
                    "repair_action_id",
                    "event_key",
                    "venue",
                    "discipline_id",
                    "memory_contract",
                    "memory_contract_error",
                    "impacted_symbols",
                    "priority",
                    "status",
                    "label",
                    "owner",
                    "cadence",
                    "automation_hook",
                    "execution_weight",
                    "last_repair_status",
                    "last_repair_policy_status",
                    "last_repair_action",
                    "last_repair_confidence",
                    "last_repair_automation_hook",
                    "last_repair_execution_weight",
                    "last_repair_reason",
                    "lane_policy_hint",
                    "scale_blocker",
                    "validation_effect_profile",
                    "entry_bias",
                    "sizing_policy",
                    "target_stop_review",
                    "min_reward_risk",
                    "max_stop_risk_pct",
                    "risk_budget_multiplier",
                    "max_budget_multiplier",
                    "required_evidence",
                    "required_checks",
                    "blocks_scaling",
                    "blocks_new_entries",
                    "runner_hint",
                    "verification_artifact",
                    "exit_criteria",
                    "validation_mode",
                    "allowed_entry_posture",
                    "live_shadow_required",
                    "scale_up_blocked",
                    "evidence_targets",
                    "pass_current_gap",
                    "pass_collection_hook",
                    "pass_criteria",
                    "pass_required_evidence",
                    "pass_jue_behavior_until_pass",
                    "pass_m1_runtime_profile",
                )
                if row.get(key) not in (None, "", [], {})
            }
        )
    constraint_items: list[dict[str, Any]] = []
    for row in _normalize_list(constraints.get("items"))[:6]:
        if not isinstance(row, dict):
            continue
        item = {
            key: compact(row.get(key), list_limit=4, string_limit=140)
            for key in (
                "policy_id",
                "venue",
                "discipline_id",
                "memory_contract",
                "memory_contract_error",
                "impacted_symbols",
                "scale_blocker",
                "period_memory_status",
                "period_memory_gap_count",
                "period_memory_override_count",
                "period_memory_contract_gap_count",
                "period_memory_missing_metadata",
                "period_memory_repair_actions",
                "metadata_contract_audit_resolutions",
                "period_memory_repair_quality",
                "priority",
                "validation_effect_profile",
                "entry_bias",
                "sizing_policy",
                "target_stop_review",
                "min_reward_risk",
                "max_stop_risk_pct",
                "risk_budget_multiplier",
                "max_budget_multiplier",
                "required_evidence",
                "required_checks",
                "blocks_scaling",
                "blocks_new_entries",
                "runner_hint",
                "verification_artifact",
                "exit_criteria",
                "risk_note",
                "pass_current_gap",
                "pass_collection_hook",
                "pass_criteria",
                "pass_required_evidence",
            )
            if row.get(key) not in (None, "", [], {})
        }
        note = _metadata_contract_repair_note(row)
        if note:
            item["metadata_contract_repair_note"] = compact(
                note,
                list_limit=1,
                string_limit=300,
            )
        constraint_items.append(item)
    memory_contract_resolution = _memory_contract_resolution_contract_from_repair_rows(
        [*backlog_items, *constraint_items]
    )
    return {
        "version": "validation_repair_prompt_v1",
        "scope": scope,
        "status": str(backlog.get("status") or constraints.get("status") or "ok"),
        "repair_item_count": _safe_int(
            backlog.get("total_item_count") or backlog.get("count") or len(backlog_items)
        ),
        "constraint_count": _safe_int(
            constraints.get("total_item_count")
            or constraints.get("count")
            or len(constraint_items)
        ),
        "instruction": (
            "Use these 19-test repair items as soft block-design constraints. "
            "They adjust entry style, evidence requirements, sizing, target/stop "
            "review, and scale-up authority; they are not strategy hard filters. "
            "Cash, position, duplicate-order, and kill-switch safety gates still override."
        ),
        "repair_backlog": backlog_items,
        "block_design_constraints": constraint_items,
        **memory_contract_resolution,
    }


def validation_repair_action_metadata(
    validation_repair: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(validation_repair, dict) or not validation_repair:
        return {}
    backlog = [
        row
        for row in _normalize_list(validation_repair.get("repair_backlog"))[:4]
        if isinstance(row, dict)
    ]
    constraints = [
        row
        for row in _normalize_list(validation_repair.get("block_design_constraints"))[:4]
        if isinstance(row, dict)
    ]
    if not backlog and not constraints:
        return {}
    policy_ids: list[str] = []
    discipline_ids: list[str] = []
    required_evidence: list[str] = []
    required_checks: list[str] = []
    entry_biases: list[str] = []
    sizing_policies: list[str] = []
    scale_blockers: list[str] = []
    blocks_scaling: list[str] = []
    blocks_new_entries: list[str] = []
    runner_hints: list[str] = []
    verification_artifacts: list[str] = []
    repair_action_ids: list[str] = []
    automation_hooks: list[str] = []
    pass_collection_hooks: list[str] = []
    pass_current_gaps: list[str] = []
    pass_criteria: list[str] = []
    execution_weights: list[str] = []
    allowed_entry_postures: list[str] = []
    last_repair_statuses: list[str] = []
    last_repair_reasons: list[str] = []
    period_memory_statuses: list[str] = []
    period_memory_gap_count = 0
    period_memory_override_count = 0
    period_memory_contract_gap_count = 0
    period_memory_missing_metadata: list[str] = []
    period_memory_repair_actions: list[str] = []
    metadata_contract_audit_resolutions: list[str] = []
    metadata_contract_repair_notes: list[str] = []
    period_memory_repair_qualities: list[str] = []
    memory_contracts: list[str] = []
    memory_contract_errors: list[str] = []
    impacted_symbols: list[str] = []
    scale_up_blocked = False
    live_shadow_required = False
    risk_budget_multipliers: list[float] = []
    max_budget_multipliers: list[float] = []
    min_reward_risks: list[float] = []
    max_stop_risk_pcts: list[float] = []
    for row in [*backlog, *constraints]:
        for source, target in (
            (row.get("policy_id"), policy_ids),
            (row.get("repair_action_id"), repair_action_ids),
            (row.get("discipline_id"), discipline_ids),
            (row.get("scale_blocker"), scale_blockers),
            (row.get("entry_bias"), entry_biases),
            (row.get("sizing_policy"), sizing_policies),
            (row.get("blocks_scaling"), blocks_scaling),
            (row.get("blocks_new_entries"), blocks_new_entries),
            (row.get("runner_hint"), runner_hints),
            (row.get("verification_artifact"), verification_artifacts),
            (row.get("automation_hook"), automation_hooks),
            (row.get("pass_collection_hook"), pass_collection_hooks),
            (row.get("pass_current_gap"), pass_current_gaps),
            (row.get("pass_criteria"), pass_criteria),
            (row.get("execution_weight"), execution_weights),
            (row.get("allowed_entry_posture"), allowed_entry_postures),
            (row.get("last_repair_status"), last_repair_statuses),
            (row.get("last_repair_reason"), last_repair_reasons),
            (row.get("period_memory_status"), period_memory_statuses),
            (row.get("period_memory_repair_quality"), period_memory_repair_qualities),
            (row.get("memory_contract"), memory_contracts),
            (row.get("memory_contract_error"), memory_contract_errors),
        ):
            value = str(source or "").strip()
            if value and value not in target:
                target.append(value)
        for item in _normalize_list(row.get("impacted_symbols")):
            value = str(item or "").strip()
            if value and value not in impacted_symbols:
                impacted_symbols.append(value)
        period_memory_gap_count += _safe_int(row.get("period_memory_gap_count"))
        period_memory_override_count += _safe_int(
            row.get("period_memory_override_count")
        )
        period_memory_contract_gap_count += _safe_int(
            row.get("period_memory_contract_gap_count")
        )
        for key, target in (
            ("period_memory_missing_metadata", period_memory_missing_metadata),
            ("period_memory_repair_actions", period_memory_repair_actions),
            (
                "metadata_contract_audit_resolutions",
                metadata_contract_audit_resolutions,
            ),
        ):
            for item in _normalize_list(row.get(key)):
                value = str(item or "").strip()
                if value and value not in target:
                    target.append(value)
        repair_note = _metadata_contract_repair_note(row)
        if repair_note and repair_note not in metadata_contract_repair_notes:
            metadata_contract_repair_notes.append(repair_note)
        scale_up_blocked = scale_up_blocked or _safe_bool(row.get("scale_up_blocked"))
        live_shadow_required = live_shadow_required or _safe_bool(
            row.get("live_shadow_required")
        )
        for key, target in (
            ("required_evidence", required_evidence),
            ("required_checks", required_checks),
        ):
            for item in _normalize_list(row.get(key)):
                value = str(item or "").strip()
                if value and value not in target:
                    target.append(value)
        for key, target in (
            ("risk_budget_multiplier", risk_budget_multipliers),
            ("max_budget_multiplier", max_budget_multipliers),
            ("min_reward_risk", min_reward_risks),
            ("max_stop_risk_pct", max_stop_risk_pcts),
        ):
            value = _safe_float(row.get(key))
            if value > 0:
                target.append(value)
    effective_risk_budget_multiplier = (
        min(risk_budget_multipliers) if risk_budget_multipliers else 0.0
    )
    effective_max_budget_multiplier = (
        min(max_budget_multipliers) if max_budget_multipliers else 0.0
    )
    effective_min_reward_risk = max(min_reward_risks) if min_reward_risks else 0.0
    effective_max_stop_risk_pct = min(max_stop_risk_pcts) if max_stop_risk_pcts else 0.0
    return {
        "validation_repair": {
            "version": "validation_repair_action_v1",
            "scope": str(validation_repair.get("scope") or ""),
            "status": str(validation_repair.get("status") or "ok"),
            "repair_item_count": _safe_int(validation_repair.get("repair_item_count")),
            "constraint_count": _safe_int(validation_repair.get("constraint_count")),
            "policy_ids": policy_ids[:8],
            "repair_action_ids": repair_action_ids[:8],
            "discipline_ids": discipline_ids[:8],
            "scale_blockers": scale_blockers[:8],
            "entry_biases": entry_biases[:6],
            "sizing_policies": sizing_policies[:6],
            "blocks_scaling": blocks_scaling[:6],
            "blocks_new_entries": blocks_new_entries[:6],
            "automation_hooks": automation_hooks[:6],
            "pass_collection_hooks": pass_collection_hooks[:6],
            "pass_current_gaps": pass_current_gaps[:6],
            "pass_criteria": pass_criteria[:6],
            "execution_weights": execution_weights[:6],
            "allowed_entry_postures": allowed_entry_postures[:6],
            "last_repair_statuses": last_repair_statuses[:6],
            "last_repair_reasons": last_repair_reasons[:4],
            "period_memory_statuses": period_memory_statuses[:4],
            "period_memory_gap_count": period_memory_gap_count,
            "period_memory_override_count": period_memory_override_count,
            "period_memory_contract_gap_count": period_memory_contract_gap_count,
            "memory_contracts": memory_contracts[:6],
            "memory_contract_errors": memory_contract_errors[:6],
            "impacted_symbols": impacted_symbols[:12],
            "period_memory_missing_metadata": period_memory_missing_metadata[:6],
            "period_memory_repair_actions": period_memory_repair_actions[:6],
            "metadata_contract_audit_resolutions": (
                metadata_contract_audit_resolutions[:6]
            ),
            "metadata_contract_repair_notes": metadata_contract_repair_notes[:4],
            "period_memory_repair_qualities": period_memory_repair_qualities[:4],
            "runner_hints": runner_hints[:6],
            "verification_artifacts": verification_artifacts[:6],
            "required_evidence": required_evidence[:10],
            "required_checks": required_checks[:10],
            "scale_up_blocked": scale_up_blocked,
            "live_shadow_required": live_shadow_required,
            "risk_budget_multiplier": round(effective_risk_budget_multiplier, 6)
            if effective_risk_budget_multiplier > 0
            else None,
            "max_budget_multiplier": round(effective_max_budget_multiplier, 6)
            if effective_max_budget_multiplier > 0
            else None,
            "min_reward_risk": round(effective_min_reward_risk, 6)
            if effective_min_reward_risk > 0
            else None,
            "max_stop_risk_pct": round(effective_max_stop_risk_pct, 6)
            if effective_max_stop_risk_pct > 0
            else None,
            "repair_backlog": backlog,
            "block_design_constraints": constraints,
            "hard_filter": False,
        }
    }


def validation_evidence_plan_from_repair(repair: Any) -> dict[str, Any]:
    if not isinstance(repair, dict) or not repair:
        return {}

    dimension_by_discipline = {
        "backtest": "backtest",
        "backtesting": "backtest",
        "backtest_quality": "backtest",
        "walk_forward": "walk_forward",
        "walk_forward_analysis": "walk_forward",
        "wfa": "walk_forward",
        "out_of_sample": "out_of_sample",
        "out_of_sample_test": "out_of_sample",
        "oos": "out_of_sample",
        "live_shadow": "live_shadow",
        "live_shadow_test": "live_shadow",
        "shadow": "live_shadow",
    }
    required_dimensions: list[str] = []
    for discipline_id in _normalize_list(repair.get("discipline_ids")):
        key = str(discipline_id or "").strip().lower()
        dimension = dimension_by_discipline.get(key)
        if dimension and dimension not in required_dimensions:
            required_dimensions.append(dimension)

    status_tokens = {
        str(value or "").strip().lower()
        for value in _normalize_list(repair.get("last_repair_statuses"))
        if str(value or "").strip()
    }
    repair_status = str(repair.get("status") or "").strip().lower()
    scale_blocked = _safe_bool(repair.get("scale_up_blocked"))
    pending = (
        repair_status
        in {"pending", "running", "active", "active_caution", "error", "failed", "blocked"}
        or any(
            token.startswith("queued")
            or token in {
                "pending",
                "running",
                "active_caution",
                "error",
                "failed",
                "blocked",
            }
            for token in status_tokens
        )
    )
    plan = {
        "version": "validation_evidence_plan_v1",
        "source": "validation_repair",
        "status": "repair_required" if pending or scale_blocked else "requirements_attached",
        "required_dimensions": required_dimensions[:4],
        "missing_dimensions": required_dimensions[:4],
        "required_evidence": _normalize_list(repair.get("required_evidence"))[:10],
        "required_checks": _normalize_list(repair.get("required_checks"))[:10],
        "pass_collection_hooks": _normalize_list(
            repair.get("pass_collection_hooks")
        )[:6],
        "pass_current_gaps": _normalize_list(repair.get("pass_current_gaps"))[:6],
        "pass_criteria": _normalize_list(repair.get("pass_criteria"))[:6],
        "verification_artifacts": _normalize_list(
            repair.get("verification_artifacts")
        )[:6],
        "period_memory_repair_qualities": (
            validation_repair_period_memory_quality_tokens(repair)[:6]
        ),
        "scale_up_blocked": scale_blocked,
        "live_shadow_required": _safe_bool(repair.get("live_shadow_required")),
    }
    return {
        key: value
        for key, value in plan.items()
        if value not in (None, "", [], {})
    }


def validation_repair_note(validation_repair: dict[str, Any]) -> str:
    if not isinstance(validation_repair, dict):
        return ""
    discipline_ids = [
        str(row or "").strip()
        for row in _normalize_list(validation_repair.get("discipline_ids"))[:3]
        if str(row or "").strip()
    ]
    entry_biases = [
        str(row or "").strip()
        for row in _normalize_list(validation_repair.get("entry_biases"))[:2]
        if str(row or "").strip()
    ]
    period_memory_qualities = validation_repair_period_memory_quality_tokens(
        validation_repair
    )[:3]
    if not discipline_ids and not entry_biases and not period_memory_qualities:
        return ""
    parts = []
    if discipline_ids:
        parts.append("검증항목=" + ",".join(discipline_ids))
    if entry_biases:
        parts.append("진입성향=" + ",".join(entry_biases))
    if period_memory_qualities:
        parts.append("메모리수리=" + ",".join(period_memory_qualities))
    return "19검증 반영 - " + " / ".join(parts)


def validation_repair_discipline_tokens(value: Any) -> list[str]:
    repair = value if isinstance(value, dict) else {}
    tokens: list[str] = []

    def add(raw: Any) -> None:
        token = re.sub(r"[\s/]+", "_", str(raw or "").strip().lower())
        if token and token not in tokens:
            tokens.append(token)

    for raw in _normalize_list(repair.get("discipline_ids")):
        add(raw)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if isinstance(row, dict):
                add(row.get("discipline_id"))
    return tokens[:8]


def validation_repair_period_memory_quality_tokens(value: Any) -> list[str]:
    repair = value if isinstance(value, dict) else {}
    tokens: list[str] = []

    def add(raw: Any) -> None:
        token = re.sub(r"[\s/]+", "_", str(raw or "").strip().lower())
        if token and token not in tokens:
            tokens.append(token)

    add(repair.get("period_memory_repair_quality"))
    for raw in _normalize_list(repair.get("period_memory_repair_qualities")):
        add(raw)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _normalize_list(repair.get(section)):
            if isinstance(row, dict):
                add(row.get("period_memory_repair_quality"))
                for raw in _normalize_list(row.get("period_memory_repair_qualities")):
                    add(raw)
    return tokens[:8]
