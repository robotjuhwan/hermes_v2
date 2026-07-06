from __future__ import annotations

from typing import Any

from tradecraft.services.binance_lane import raw_binance_horizon_requests_futures
from tradecraft.services.binance_growth_governor import (
    growth_governor_row_lanes,
)
from tradecraft.services.binance_policy_effects import (
    policy_effect_audit,
    policy_rule_ids,
)
from tradecraft.services.binance_symbol import normalize_market


JUE_WIKI_REPAIR_METADATA_KEYS = (
    "jue_wiki_repair_pressure",
    "jue_wiki_repair_resolution",
    "jue_wiki_usage_contract_resolution",
)
JUE_WIKI_DECISION_ADJUSTMENT_METADATA_KEYS = (
    "jue_wiki_decision_adjustment",
    "jue_wiki_decision_adjustments",
    "jue_wiki_decision_adjustment_resolution",
    "jue_wiki_execution_hint_resolution",
)
PERIOD_MEMORY_METADATA_KEYS = (
    "period_memory_coverage_gap",
    "period_memory_override_reason",
    "metadata_contract_audit_resolution",
    "metadata_contract_repair_note",
)
PERIOD_MEMORY_REQUIRED_METADATA = [
    "period_memory_coverage_gap",
    "period_memory_override_reason",
]


def empty_manager_action_results() -> dict[str, list[dict[str, Any]]]:
    return {
        "adopted": [],
        "created": [],
        "updated": [],
        "closed": [],
        "paused": [],
    }


def rejected_manager_action(
    reason: Any,
    *,
    input_row: dict[str, Any] | None = None,
    **context: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": "rejected",
        "reason": str(reason),
    }
    for key, value in context.items():
        if value in (None, "", [], {}):
            continue
        row[key] = value
    if input_row is not None:
        row["input"] = input_row
    return row


def manager_block_action_result(
    status: Any,
    block_id: Any,
    **context: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": str(status),
        "block_id": str(block_id),
    }
    for key, value in context.items():
        if value in (None, "", [], {}):
            continue
        row[key] = value
    return row


def manager_created_block_result(
    block: dict[str, Any],
    *,
    live_entry: bool,
    waiting_entry: bool,
    entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block_id = block.get("block_id")
    if live_entry and not waiting_entry:
        payload = entry or {}
        return {
            "status": payload.get("status"),
            "block_id": block_id,
            "order": payload.get("order"),
        }
    return manager_block_action_result(
        "waiting_entry" if waiting_entry else "created",
        block_id,
    )


def _merge_jue_wiki_repair_metadata(
    metadata: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in (
        *JUE_WIKI_REPAIR_METADATA_KEYS,
        *JUE_WIKI_DECISION_ADJUSTMENT_METADATA_KEYS,
        *PERIOD_MEMORY_METADATA_KEYS,
    ):
        value = row.get(key)
        if value in (None, "", [], {}):
            value = row_metadata.get(key)
        if value in (None, "", [], {}):
            continue
        metadata[key] = value
    audit = period_memory_contract_audit(metadata)
    if audit:
        metadata["period_memory_contract_audit"] = audit
    return metadata


def _clean_metadata_text(value: Any, *, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[: max(int(limit), 1)]


def period_memory_contract_audit(row: dict[str, Any]) -> dict[str, Any]:
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    gap = _clean_metadata_text(
        row.get("period_memory_coverage_gap")
        if "period_memory_coverage_gap" in row
        else row_metadata.get("period_memory_coverage_gap")
    )
    override_reason = _clean_metadata_text(
        row.get("period_memory_override_reason")
        if "period_memory_override_reason" in row
        else row_metadata.get("period_memory_override_reason")
    )
    audit_resolution = _clean_metadata_text(
        row.get("metadata_contract_audit_resolution")
        if "metadata_contract_audit_resolution" in row
        else row_metadata.get("metadata_contract_audit_resolution"),
        limit=800,
    )
    repair_note = _clean_metadata_text(
        row.get("metadata_contract_repair_note")
        if "metadata_contract_repair_note" in row
        else row_metadata.get("metadata_contract_repair_note"),
        limit=1000,
    )
    if not gap and not override_reason:
        return {}
    status = ""
    missing_metadata: list[str] = []
    repair_action = ""
    if gap and not override_reason:
        status = "missing_override_reason"
        missing_metadata = ["period_memory_override_reason"]
        repair_action = "add_period_memory_override_reason_before_scaling"
    elif override_reason and not gap:
        status = "missing_coverage_gap"
        missing_metadata = ["period_memory_coverage_gap"]
        repair_action = "name_period_memory_coverage_gap_before_using_override"
    if not status:
        return {}
    audit = {
        "status": status,
        "policy_id": f"period_memory_coverage.{status}",
        "gap": gap,
        "override_reason": override_reason,
        "missing_metadata": missing_metadata,
        "required_metadata": PERIOD_MEMORY_REQUIRED_METADATA,
        "repair_action": repair_action,
    }
    if audit_resolution:
        audit["metadata_contract_audit_resolution"] = audit_resolution
    if repair_note:
        audit["metadata_contract_repair_note"] = repair_note
    return audit


def manager_create_block_metadata(
    row: dict[str, Any],
    *,
    entry_gate: dict[str, Any],
    live_authority_gate: dict[str, Any],
    lane_authority_gate: dict[str, Any],
    cost_edge_gate: dict[str, Any],
    growth_governor: dict[str, Any],
    growth_governor_applies: bool,
    policy_impacts: list[dict[str, Any]] | None,
    policy_enforcement: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {})
    metadata = _merge_jue_wiki_repair_metadata(metadata, row)
    metadata["entry_gate"] = entry_gate
    metadata["live_authority_gate"] = live_authority_gate
    metadata["lane_authority_gate"] = lane_authority_gate
    metadata["cost_edge_gate"] = cost_edge_gate
    if growth_governor_applies:
        metadata["growth_governor"] = growth_governor
    clean_policy_impacts = [
        impact for impact in (policy_impacts or []) if isinstance(impact, dict)
    ]
    clean_policy_enforcement = (
        policy_enforcement if isinstance(policy_enforcement, dict) else {}
    )
    if clean_policy_impacts:
        metadata["policy_rule_impacts"] = clean_policy_impacts
        metadata["applied_policy_versions"] = policy_rule_ids(clean_policy_impacts)
        audit = policy_effect_audit(clean_policy_impacts)
        if audit:
            metadata["policy_effect_audit"] = audit
        if clean_policy_enforcement.get("adjustments") or clean_policy_enforcement.get(
            "checks"
        ):
            metadata["policy_effect_enforcement"] = clean_policy_enforcement
    return metadata


def manager_market_horizon_conflict(row: dict[str, Any]) -> dict[str, Any] | None:
    market = normalize_market(row.get("market") or row.get("venue"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    raw_horizon = row.get("horizon")
    if raw_horizon in (None, ""):
        raw_horizon = metadata.get("manager_contract_raw_horizon") or metadata.get("horizon")
    elif not raw_binance_horizon_requests_futures(raw_horizon):
        raw_horizon = metadata.get("manager_contract_raw_horizon") or raw_horizon
    if market != "futures" and raw_binance_horizon_requests_futures(raw_horizon):
        return {
            "status": "rejected",
            "reason": f"market_horizon_conflict:{market}:futures",
            "message": (
                "create_blocks with horizon=futures must set market=futures; "
                "spot/upbit_spot blocks must use short, mid, or long horizons."
            ),
            "input": row,
        }
    return None


def validation_repair_metadata_update(
    row: dict[str, Any],
    block: dict[str, Any],
) -> dict[str, Any]:
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    repair = row.get("validation_repair")
    if not isinstance(repair, dict):
        repair = row_metadata.get("validation_repair")
    metadata = dict(block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
    if isinstance(repair, dict):
        metadata["validation_repair"] = repair
    metadata = _merge_jue_wiki_repair_metadata(metadata, row)
    if metadata == (block.get("metadata") if isinstance(block.get("metadata"), dict) else {}):
        return {}
    return metadata


def manager_update_fields(
    row: dict[str, Any],
    block: dict[str, Any],
) -> dict[str, Any]:
    allowed = {"target_price", "stop_price", "thesis", "llm_reason", "risk_note"}
    if str(block.get("status") or "") == "proposed":
        allowed.add("entry_price")
    fields: dict[str, Any] = {}
    for key in allowed:
        if key in row:
            fields[key] = row[key]
    if "reason" in row and "llm_reason" not in fields:
        fields["llm_reason"] = row.get("reason")
    metadata = validation_repair_metadata_update(row, block)
    if metadata:
        fields["metadata"] = metadata
    return fields


def manager_exit_request_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "force_exit_requested": 1,
        "llm_reason": row.get("reason", "manager_close_requested"),
    }


def manager_closed_fields(
    row: dict[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    return {
        "status": "closed",
        "closed_at": closed_at,
        "llm_reason": row.get("reason", ""),
    }


def manager_pause_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {"status": "paused", "llm_reason": row.get("reason", "")}


def manager_close_has_adverse_evidence(
    row: dict[str, Any],
    block: dict[str, Any],
) -> bool:
    if any(
        bool(row.get(key))
        for key in (
            "force",
            "force_exit",
            "force_exit_requested",
            "manual",
            "operator_confirmed",
        )
    ):
        return True

    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    block_metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    parts: list[str] = [
        str(row.get("reason") or ""),
        str(row.get("risk_note") or ""),
        str(row.get("llm_reason") or ""),
        str(row.get("evidence") or ""),
        str(row.get("evidence_refs") or ""),
        str(metadata.get("reason") or ""),
        str(metadata.get("risk_note") or ""),
        str(metadata.get("evidence") or ""),
        str(block_metadata.get("invalidation") or ""),
    ]
    text = " ".join(parts).lower()
    if not text.strip():
        return False

    adverse_tokens = {
        "adverse",
        "avoid",
        "breakdown",
        "cancel",
        "churn",
        "depth",
        "drawdown",
        "error",
        "failed",
        "force_exit",
        "funding",
        "invalid",
        "invalidated",
        "invalidation",
        "liquidation",
        "liquidity",
        "manual",
        "order_error",
        "overheat",
        "reject",
        "risk",
        "spread",
        "stale",
        "stop",
        "thesis_broken",
        "trigger_broken",
        "wick",
        "강제",
        "리스크",
        "무효",
        "손절",
        "스프레드",
        "실패",
        "오류",
        "위험",
        "유동성",
        "청산",
    }
    return any(token in text for token in adverse_tokens)


def manager_growth_governor_create_rejection(
    row: dict[str, Any],
    *,
    applies: bool,
    growth_governor: dict[str, Any],
    growth_unlock: dict[str, Any] | None = None,
    governed_new_blocks: int,
    max_new_blocks: int,
    waiting_entry: bool,
) -> dict[str, Any] | None:
    if not applies:
        return None
    if not bool(growth_governor.get("allow_new_blocks", True)):
        return rejected_manager_action(
            "growth_governor_halt_new_blocks",
            input_row=row,
            growth_governor=growth_governor,
        )
    if governed_new_blocks >= max(max_new_blocks, 0):
        return rejected_manager_action(
            "growth_governor_new_block_limit",
            input_row=row,
            growth_governor=growth_governor,
        )
    if (
        bool(growth_governor.get("require_waiting_entry"))
        and not bool(waiting_entry)
        and not manager_growth_unlock_allows_volatile_attack_immediate_probe(
            row,
            growth_unlock=growth_unlock,
        )
    ):
        return rejected_manager_action(
            "growth_governor_requires_waiting_entry",
            input_row=row,
            growth_governor=growth_governor,
        )
    return None


def manager_growth_unlock_allows_volatile_attack_immediate_probe(
    row: dict[str, Any],
    *,
    growth_unlock: dict[str, Any] | None,
) -> bool:
    if not isinstance(growth_unlock, dict):
        return False
    permissions = growth_unlock.get("action_permissions")
    if not isinstance(permissions, dict):
        return False
    if not bool(permissions.get("volatile_attack_probe")):
        return False
    return "volatile_attack" in growth_governor_row_lanes(row)


def manager_create_policy_repair_rejection(
    row: dict[str, Any],
    *,
    policy_enforcement: dict[str, Any],
    repair_enforcement: dict[str, Any],
) -> dict[str, Any] | None:
    if policy_enforcement.get("rejected"):
        return rejected_manager_action(
            policy_enforcement.get("reason") or "policy_effect_rejected",
            input_row=row,
            policy_effect_enforcement=policy_enforcement,
        )
    if repair_enforcement.get("rejected"):
        return rejected_manager_action(
            repair_enforcement.get("reason") or "validation_repair_rejected",
            input_row=row,
            validation_repair_enforcement=repair_enforcement,
        )
    return None
