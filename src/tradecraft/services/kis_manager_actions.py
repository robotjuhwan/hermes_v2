from __future__ import annotations

import math
import re
from typing import Any

from tradecraft.services.kis_entry_gate import (
    ENTRY_WAIT_STYLE,
    entry_quality_fields,
    invalid_long_price_structure_reason,
    normalize_entry_style,
    normalize_entry_trigger_operator,
)
from tradecraft.services.kis_horizon import normalize_horizon
from tradecraft.services.kis_ledger import (
    positions_by_symbol,
    unallocated_qty_by_symbol,
)

DECISION_METADATA_NUMERIC_FIELDS = {
    "target_block_value_krw",
    "max_loss_krw",
}
DECISION_METADATA_TEXT_FIELDS = {
    "strategy_family": 120,
    "entry_setup": 160,
    "stop_policy": 500,
    "decision_class": 300,
    "close_trigger": 300,
    "what_would_change_my_mind": 1200,
    "jue_wiki_repair_pressure": 600,
    "jue_wiki_repair_resolution": 600,
    "jue_wiki_memory_card_quality": 600,
    "jue_wiki_memory_card_cross_check": 600,
    "jue_wiki_usage_contract_resolution": 800,
    "period_memory_coverage_gap": 600,
    "period_memory_override_reason": 600,
    "metadata_contract_audit_resolution": 800,
    "metadata_contract_repair_note": 1000,
}
DECISION_METADATA_STRUCTURED_FIELDS = {
    "jue_wiki_decision_adjustment",
    "jue_wiki_decision_adjustments",
    "jue_wiki_decision_adjustment_resolution",
    "jue_wiki_execution_hint_resolution",
    "period_memory_contract_audit",
}
DECISION_METADATA_OUTPUT_SCHEMA = {
    "target_block_value_krw": "optional number; intended block value in KRW",
    "max_loss_krw": "optional number; expected maximum loss in KRW",
    "strategy_family": "optional string; setup family used for lane-specific validation, e.g. value_pullback|late_chase|pullback_reclaim",
    "entry_setup": "optional string; concrete entry setup used for lane-specific validation",
    "stop_policy": "optional string; rule_exit|manager_review or equivalent policy",
    "decision_class": "optional string; create|adopt|hold|update|close|pause classification",
    "close_trigger": "optional string; target_reached|stop_reached|profit_giveback|thesis_invalidated|reconciliation_cleanup",
    "what_would_change_my_mind": "optional string; concrete invalidation condition",
    "jue_wiki_repair_pressure": "optional string; how wiki repair pressure or omitted repair queue affected confidence, sizing, horizon, or evidence requirements",
    "jue_wiki_repair_resolution": "optional string; how this action resolves or compensates for active wiki repair pressure",
    "jue_wiki_memory_card_quality": "optional string; how thin Wiki memory card quality affected confidence, sizing, horizon, or evidence requirements",
    "jue_wiki_memory_card_cross_check": "optional string; live research, quote, flow, report, or valuation cross-check used before trusting thin Wiki memory cards",
    "jue_wiki_usage_contract_resolution": "optional string; live quote/account/risk/research/price-structure cross-check that confirmed or overrode the Wiki usage contract",
    "period_memory_coverage_gap": "optional string; missing weekly/monthly review or replay that affected this action's confidence, sizing, horizon, or evidence requirements",
    "period_memory_override_reason": "optional string; why current live evidence overrides a period memory coverage gap for this action",
    "metadata_contract_audit_resolution": "optional string; how this action resolves, defers, or compensates for an active period memory metadata contract audit gap",
    "metadata_contract_repair_note": "optional string; compact repair note copied from validation_repair metadata_contract_repair_note for reflection audit",
    "period_memory_contract_audit": "optional object; added by sanitizer when period memory gap/override metadata is incomplete for reflection audit",
    "jue_wiki_decision_adjustment": "optional object/string; active Wiki decision adjustment used by this action",
    "jue_wiki_decision_adjustments": "optional list; active Wiki decision adjustments preserved for reflection audit",
    "jue_wiki_decision_adjustment_resolution": "optional object/string; how this action followed, repaired, or intentionally overrode Wiki decision adjustment execution hints",
    "jue_wiki_execution_hint_resolution": "optional object/string; execution-hint-specific resolution for later reflection audit",
    "post_review_required": "optional boolean; true when follow-up review is required",
}


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


def _safe_int(value: Any) -> int:
    return int(math.floor(_safe_float(value)))


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


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _normalize_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def period_memory_contract_audit(row: dict[str, Any]) -> dict[str, Any]:
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    gap = _clean_text(
        row.get("period_memory_coverage_gap")
        if "period_memory_coverage_gap" in row
        else row_metadata.get("period_memory_coverage_gap"),
        limit=600,
    )
    override_reason = _clean_text(
        row.get("period_memory_override_reason")
        if "period_memory_override_reason" in row
        else row_metadata.get("period_memory_override_reason"),
        limit=600,
    )
    audit_resolution = _clean_text(
        row.get("metadata_contract_audit_resolution")
        if "metadata_contract_audit_resolution" in row
        else row_metadata.get("metadata_contract_audit_resolution"),
        limit=800,
    )
    repair_note = _clean_text(
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
        "required_metadata": [
            "period_memory_coverage_gap",
            "period_memory_override_reason",
        ],
        "repair_action": repair_action,
    }
    if audit_resolution:
        audit["metadata_contract_audit_resolution"] = audit_resolution
    if repair_note:
        audit["metadata_contract_repair_note"] = repair_note
    return audit


def decision_metadata_fields(row: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in DECISION_METADATA_NUMERIC_FIELDS:
        if key in row:
            metadata[key] = max(_safe_float(row.get(key)), 0.0)
    for key, limit in DECISION_METADATA_TEXT_FIELDS.items():
        source = row.get(key) if key in row else row_metadata.get(key)
        if source in (None, "", [], {}):
            continue
        cleaned = _clean_text(source, limit=limit)
        if cleaned:
            metadata[key] = cleaned
    audit = period_memory_contract_audit(row)
    if audit:
        metadata["period_memory_contract_audit"] = audit
    for key in DECISION_METADATA_STRUCTURED_FIELDS:
        source = row.get(key) if key in row else row_metadata.get(key)
        if source in (None, "", [], {}):
            continue
        if isinstance(source, (dict, list)):
            metadata[key] = source
        else:
            cleaned = _clean_text(source, limit=1000)
            if cleaned:
                metadata[key] = cleaned
    if "post_review_required" in row:
        metadata["post_review_required"] = _safe_bool(row.get("post_review_required"))
    elif "post_review_required" in row_metadata:
        metadata["post_review_required"] = _safe_bool(row_metadata.get("post_review_required"))
    return metadata


def sanitize_kis_block_id_actions(
    rows: Any,
    block_ids: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _normalize_list(rows):
        if not isinstance(row, dict):
            continue
        block_id = str(row.get("block_id") or "").strip()
        if block_id not in block_ids:
            continue
        out.append(
            {
                "block_id": block_id,
                "reason": _clean_text(row.get("reason"), limit=1000),
                **decision_metadata_fields(row),
            }
        )
    return out


def manager_update_action_plan(
    *,
    row: dict[str, Any],
    current: dict[str, Any] | None,
    quote: dict[str, Any],
) -> dict[str, Any]:
    block_id = str(row.get("block_id") or "")
    if not current:
        return {
            "status": "rejected",
            "reason": "block_missing",
            "block_id": block_id,
        }
    symbol = str(current.get("symbol") or "")
    reference_price = _safe_float(quote.get("price")) or _safe_float(
        current.get("entry_price")
    )
    next_target = _safe_float(row.get("target_price")) or _safe_float(
        current.get("target_price")
    )
    next_stop = _safe_float(row.get("stop_price")) or _safe_float(
        current.get("stop_price")
    )
    invalid_reason = invalid_long_price_structure_reason(
        reference_price=reference_price,
        target_price=next_target,
        stop_price=next_stop,
    )
    if invalid_reason:
        rejection = {
            "status": "rejected",
            "reason": "invalid_update_target_stop_bounds",
            "detail": invalid_reason,
            "block_id": block_id,
            "symbol": symbol,
            "reference_price": reference_price or None,
            "target_price": next_target or None,
            "stop_price": next_stop or None,
        }
        rejection["metadata_event"] = {
            "event_type": "manager_update_rejected",
            "message": "manager update rejected by target/stop bounds",
        }
        return rejection
    return {
        "status": "update",
        "block_id": block_id,
        "fields": {
            "target_price": row.get("target_price") or None,
            "stop_price": row.get("stop_price") or None,
            "llm_reason": row.get("reason") or "",
        },
    }


def manager_close_action_plan(
    *,
    row: dict[str, Any],
    block: dict[str, Any] | None,
    is_waiting_entry: bool,
    close_guard: dict[str, Any],
) -> dict[str, Any]:
    block_id = str(row.get("block_id") or "")
    if not block:
        return {
            "status": "rejected",
            "reason": "block_missing",
            "block_id": block_id,
        }
    reason = _clean_text(row.get("reason"), limit=1000)
    metadata_message = reason or "manager close metadata"
    if is_waiting_entry:
        return {
            "status": "close_waiting_entry",
            "block_id": block_id,
            "reason": reason or "llm_cancel_waiting_entry",
            "metadata_event_type": "manager_close_decision_metadata",
            "metadata_message": metadata_message,
        }
    if not bool(close_guard.get("allowed")):
        event_payload = {
            **close_guard,
            "requested_reason": reason,
            "decision_class": row.get("decision_class"),
            "close_trigger": row.get("close_trigger"),
        }
        return {
            "status": "defer",
            "block_id": block_id,
            "event_type": "manager_close_deferred",
            "event_message": "manager close deferred by horizon patience guard",
            "event_payload": event_payload,
            "rejection": {"action": "close", **close_guard},
        }
    return {
        "status": "request_exit",
        "block_id": block_id,
        "fields": {
            "force_exit_requested": 1,
            "llm_reason": reason or "llm_close",
        },
        "metadata_event_type": "manager_close_decision_metadata",
        "metadata_message": metadata_message,
    }


def _manager_payload_source(parsed: Any) -> dict[str, Any]:
    source = parsed if isinstance(parsed, dict) else {}
    if isinstance(source.get("payload"), dict) and (
        source.get("selected_contract_id") or source.get("contract_id")
    ):
        return source["payload"]
    return source


def _reference_position_price(position: dict[str, Any], quote: dict[str, Any]) -> float:
    return (
        _safe_float(quote.get("price"))
        or _safe_float(position.get("mark_price"))
        or _safe_float(position.get("avg_price"))
    )


def sanitize_kis_manager_actions(
    parsed: Any,
    *,
    blocks: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    account: dict[str, Any],
) -> dict[str, Any]:
    block_ids = {str(row.get("block_id") or "") for row in blocks}
    source = _manager_payload_source(parsed)
    unallocated = unallocated_qty_by_symbol(account=account, blocks=blocks)
    positions = positions_by_symbol(account)

    adopt_existing_blocks: list[dict[str, Any]] = []
    for row in _normalize_list(source.get("adopt_existing_blocks")):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        qty = max(_safe_int(row.get("qty")), 0)
        target = _safe_float(row.get("target_price"))
        stop = _safe_float(row.get("stop_price"))
        position = positions.get(symbol) or {}
        quote = quotes.get(symbol) or {}
        reference_price = _reference_position_price(position, quote)
        if not _is_symbol(symbol) or qty <= 0 or target <= 0 or stop <= 0:
            continue
        if qty > max(int(unallocated.get(symbol, 0)), 0):
            continue
        if reference_price > 0 and not (stop < reference_price < target):
            continue
        unallocated[symbol] = max(int(unallocated.get(symbol, 0)) - qty, 0)
        adopt_existing_blocks.append(
            {
                "symbol": symbol,
                "qty": qty,
                "target_price": target,
                "stop_price": stop,
                "horizon": normalize_horizon(row.get("horizon")),
                "allocation_reason": _clean_text(
                    row.get("allocation_reason"),
                    limit=1000,
                ),
                "thesis": _clean_text(row.get("thesis"), limit=2000),
                "confidence": _safe_float(row.get("confidence")),
                "risk_note": _clean_text(row.get("risk_note"), limit=2000),
                **decision_metadata_fields(row),
            }
        )

    create_blocks: list[dict[str, Any]] = []
    for row in _normalize_list(source.get("create_blocks")):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        qty = max(_safe_int(row.get("qty")), 0)
        target = _safe_float(row.get("target_price"))
        stop = _safe_float(row.get("stop_price"))
        quote_price = _safe_float((quotes.get(symbol) or {}).get("price"))
        entry_style = normalize_entry_style(row.get("entry_style"))
        trigger_price = _safe_float(row.get("entry_trigger_price"))
        trigger_operator = normalize_entry_trigger_operator(
            row.get("entry_trigger_operator"),
            trigger_price=trigger_price,
            reference_price=quote_price,
        )
        reference_price = trigger_price if entry_style == ENTRY_WAIT_STYLE else quote_price
        if not _is_symbol(symbol) or qty <= 0 or target <= 0 or stop <= 0:
            continue
        if entry_style == ENTRY_WAIT_STYLE and trigger_price <= 0:
            continue
        if reference_price > 0 and not (stop < reference_price < target):
            continue
        create_blocks.append(
            {
                "symbol": symbol,
                "qty": qty,
                "target_price": target,
                "stop_price": stop,
                "entry_style": entry_style,
                "entry_trigger_price": trigger_price,
                "entry_trigger_operator": trigger_operator,
                "horizon": normalize_horizon(row.get("horizon")),
                "allocation_reason": _clean_text(
                    row.get("allocation_reason"),
                    limit=1000,
                ),
                "thesis": _clean_text(row.get("thesis"), limit=2000),
                "confidence": _safe_float(row.get("confidence")),
                "risk_note": _clean_text(row.get("risk_note"), limit=2000),
                **entry_quality_fields(row),
                **decision_metadata_fields(row),
            }
        )

    update_blocks: list[dict[str, Any]] = []
    for row in _normalize_list(source.get("update_blocks")):
        if not isinstance(row, dict):
            continue
        block_id = str(row.get("block_id") or "").strip()
        if block_id not in block_ids:
            continue
        update_blocks.append(
            {
                "block_id": block_id,
                "target_price": _safe_float(row.get("target_price")),
                "stop_price": _safe_float(row.get("stop_price")),
                "reason": _clean_text(row.get("reason"), limit=1000),
                **decision_metadata_fields(row),
            }
        )

    return {
        "adopt_existing_blocks": adopt_existing_blocks,
        "create_blocks": create_blocks,
        "update_blocks": update_blocks,
        "close_blocks": sanitize_kis_block_id_actions(
            source.get("close_blocks"),
            block_ids,
        ),
        "pause_blocks": sanitize_kis_block_id_actions(
            source.get("pause_blocks"),
            block_ids,
        ),
    }
