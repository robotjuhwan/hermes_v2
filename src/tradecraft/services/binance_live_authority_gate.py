from __future__ import annotations

import re
from typing import Any

from tradecraft.services.binance_ledger import safe_float, safe_int
from tradecraft.services.binance_policy_effects import truthy_gate_value
from tradecraft.services.live_authority import active_revision_probe_budget_multiplier

LIVE_AUTHORITY_VALIDATION_HARD_BLOCK_STATUSES = {
    "blocked_by_validation",
    "validation_error",
    "validation_incomplete",
    "validation_missing",
    "validation_research_only",
    "validation_stale",
}
LIVE_AUTHORITY_VALIDATION_WAIT_ONLY_STATUSES = {
    "validation_normal",
    "validation_probe",
}


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[: max(int(limit), 1)]


def live_authority_validation_gate(
    live_authority: dict[str, Any] | None,
) -> dict[str, str]:
    payload = live_authority if isinstance(live_authority, dict) else {}
    raw_gate = payload.get("validation_gate")
    gate = raw_gate if isinstance(raw_gate, dict) else {}
    return {
        "status": str(gate.get("status") or "").strip().lower(),
        "readiness": str(gate.get("readiness") or "").strip().lower(),
        "reason": str(gate.get("reason") or "").strip(),
        "risk_governor_action": str(
            gate.get("risk_governor_action") or ""
        ).strip().lower(),
    }


def active_revision_waiting_entry_reason(
    live_authority: dict[str, Any] | None,
) -> str:
    payload = live_authority if isinstance(live_authority, dict) else {}
    evidence = (
        payload.get("active_revision_evidence")
        if isinstance(payload.get("active_revision_evidence"), dict)
        else {}
    )
    if not evidence:
        return ""
    status = str(evidence.get("status") or "").strip().lower()
    if status in {
        "no_active_revision_samples",
        "no_active_revision_samples_with_proxy",
        "active_revision_samples_pending_close",
        "active_revision_samples_pending_close_with_proxy",
        "active_revision_sample_building",
        "insufficient_active_revision_samples",
    }:
        return f"active_revision_evidence:{status}"
    effective_samples = safe_int(evidence.get("effective_sample_count"))
    min_samples = safe_int(evidence.get("min_samples_to_scale"))
    scale_up_allowed = truthy_gate_value(evidence.get("scale_up_allowed"))
    hard_blocking_count = safe_int(evidence.get("hard_blocking_count"))
    if min_samples > 0 and effective_samples < min_samples and not scale_up_allowed:
        return "active_revision_evidence:insufficient_samples"
    if hard_blocking_count > 0 and not scale_up_allowed:
        return "active_revision_evidence:hard_blocking"
    return ""


def active_revision_budget_multiplier(
    live_authority: dict[str, Any] | None,
) -> float | None:
    reason = active_revision_waiting_entry_reason(live_authority)
    if not reason:
        return None
    payload = live_authority if isinstance(live_authority, dict) else {}
    evidence = (
        payload.get("active_revision_evidence")
        if isinstance(payload.get("active_revision_evidence"), dict)
        else {}
    )
    return active_revision_probe_budget_multiplier(evidence)


def lane_authority_evidence_fields(
    lane_detail: dict[str, Any],
    risk_budget_passport: dict[str, Any],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    detail = lane_detail if isinstance(lane_detail, dict) else {}
    passport = risk_budget_passport if isinstance(risk_budget_passport, dict) else {}

    def first_value(key: str) -> Any:
        for source in (detail, passport):
            value = source.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    def text_field(key: str, *, alias: str | None = None) -> None:
        value = first_value(key)
        if value in (None, ""):
            return
        text = _clean_text(value, limit=220)
        if text:
            evidence[alias or key] = text

    def number_field(key: str, *, alias: str | None = None) -> None:
        value = first_value(key)
        if value in (None, ""):
            return
        number = safe_float(value)
        if number != 0.0:
            evidence[alias or key] = round(number, 6)

    def bool_field(key: str, *, alias: str | None = None) -> None:
        value = first_value(key)
        if isinstance(value, bool):
            evidence[alias or key] = value

    def compact_dict_field(key: str, *, alias: str | None = None) -> None:
        value = first_value(key)
        if not isinstance(value, dict):
            return
        compact: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:8]:
            item_key = _clean_text(raw_key, limit=80)
            if not item_key:
                continue
            if isinstance(raw_value, bool):
                compact[item_key] = raw_value
            elif isinstance(raw_value, int):
                compact[item_key] = raw_value
            elif isinstance(raw_value, float):
                compact[item_key] = round(raw_value, 6)
            elif isinstance(raw_value, str):
                compact[item_key] = _clean_text(raw_value, limit=120)
        if compact:
            evidence[alias or key] = compact

    def compact_list_field(key: str, *, alias: str | None = None) -> None:
        value = first_value(key)
        if not isinstance(value, list):
            return
        compact = [
            _clean_text(item, limit=140)
            for item in value[:6]
            if _clean_text(item, limit=140)
        ]
        if compact:
            evidence[alias or key] = compact

    for key in (
        "cost_evidence_status",
        "cost_evidence_repair_hint",
        "entry_quality_repair_hint",
        "dominant_bad_entry_quality_label",
        "dominant_good_entry_quality_label",
        "validation_evidence_status",
        "validation_evidence_repair_hint",
        "scale_decision",
    ):
        text_field(key)
    text_field("budget_status", alias="risk_budget_passport_status")
    compact_list_field("cost_repair_targets")
    compact_list_field("entry_repair_targets")
    compact_list_field("core_validation_evidence_gaps")
    compact_list_field("validation_evidence_repair_targets")
    compact_list_field("validation_evidence_required_evidence")
    compact_list_field("validation_evidence_required_checks")
    compact_list_field("validation_evidence_pass_collection_hooks")
    compact_list_field("validation_evidence_pass_current_gaps")
    compact_list_field("validation_evidence_pass_criteria")
    compact_list_field("validation_evidence_verification_artifacts")
    compact_list_field("scale_blockers")
    compact_list_field("scale_repair_targets")
    compact_list_field("reasons", alias="risk_budget_passport_reasons")
    for key in (
        "cost_precision_counts",
        "missing_cost_component_counts",
        "present_cost_component_counts",
        "required_cost_component_counts",
        "cost_precision_reason_counts",
        "entry_quality_label_counts",
        "bad_entry_quality_label_counts",
        "good_entry_quality_label_counts",
    ):
        compact_dict_field(key)
    for key in (
        "cost_hybrid_alpha_count",
        "cost_hybrid_alpha_net_pnl",
        "cost_verified_alpha_count",
        "cost_unverified_alpha_count",
        "cost_verified_alpha_net_pnl",
        "cost_unverified_alpha_net_pnl",
        "verified_edge_sample_cap_multiplier",
        "validation_evidence_cap_multiplier",
        "effective_risk_budget_multiplier",
        "applied_risk_budget_multiplier",
        "recommended_risk_fraction",
        "risk_of_ruin_pct",
    ):
        number_field(key)
    for key in (
        "scale_blocked_by_cost_precision",
        "scale_blocked_by_cost_evidence",
        "scale_blocked_by_verified_edge_samples",
        "scale_blocked_by_verified_edge_net_pnl",
        "scale_blocked_by_validation_evidence",
    ):
        bool_field(key)
    return evidence


def live_authority_create_gate(
    live_authority: dict[str, Any] | None,
    *,
    waiting_entry: bool,
) -> dict[str, Any]:
    gate = live_authority_validation_gate(live_authority)
    gate_status = gate["status"]
    risk_governor = gate["risk_governor_action"]
    payload = live_authority if isinstance(live_authority, dict) else {}
    audit_gate: dict[str, Any] = dict(gate)
    active_revision_reason = active_revision_waiting_entry_reason(payload)
    if active_revision_reason:
        audit_gate["active_revision_evidence"] = {
            "reason": active_revision_reason,
            "budget_multiplier_cap": active_revision_budget_multiplier(payload),
        }
        if not waiting_entry:
            return {
                "ok": False,
                "reason": (
                    "live_authority_requires_waiting_entry:"
                    f"{active_revision_reason}"
                ),
                "gate": audit_gate,
            }
    if risk_governor == "halt_new_risk":
        return {
            "ok": False,
            "reason": "live_authority_risk_governor:halt_new_risk",
            "gate": audit_gate,
        }
    if risk_governor == "risk_off" and not waiting_entry:
        return {
            "ok": False,
            "reason": "live_authority_requires_waiting_entry:risk_governor:risk_off",
            "gate": audit_gate,
        }
    validation_gate = (
        payload.get("validation_gate")
        if isinstance(payload.get("validation_gate"), dict)
        else {}
    )
    validation_pressure = (
        validation_gate.get("validation_pressure")
        if isinstance(validation_gate.get("validation_pressure"), dict)
        else {}
    )
    entry_posture = str(
        validation_pressure.get("entry_posture") or ""
    ).strip().lower()
    if entry_posture:
        audit_gate["validation_pressure_entry_posture"] = entry_posture
    pressure_severity = str(
        validation_pressure.get("severity") or ""
    ).strip().lower()
    if pressure_severity:
        audit_gate["validation_pressure_severity"] = pressure_severity
    sizing_posture = str(
        validation_pressure.get("sizing_posture") or ""
    ).strip().lower()
    if sizing_posture:
        audit_gate["validation_pressure_sizing_posture"] = sizing_posture
    if entry_posture == "patient_waiting_entry" and not waiting_entry:
        return {
            "ok": False,
            "reason": (
                "live_authority_requires_waiting_entry:"
                "validation_pressure:patient_waiting_entry"
            ),
            "gate": audit_gate,
        }
    lane_authority = (
        payload.get("lane_authority")
        if isinstance(payload.get("lane_authority"), dict)
        else {}
    )
    for gate_key in ("validation_shadow_gate", "validation_exposure_gate"):
        extra_gate = (
            lane_authority.get(gate_key)
            if isinstance(lane_authority.get(gate_key), dict)
            else {}
        )
        if not extra_gate:
            continue
        if truthy_gate_value(
            extra_gate.get("requires_waiting_entry")
        ) or truthy_gate_value(extra_gate.get("blocks_scale_up")):
            status = str(extra_gate.get("status") or gate_key).strip().lower()
            audit_gate[gate_key] = {
                key: value
                for key, value in {
                    "status": status,
                    "requires_waiting_entry": extra_gate.get(
                        "requires_waiting_entry"
                    ),
                    "blocks_scale_up": extra_gate.get("blocks_scale_up"),
                }.items()
                if value not in (None, "", [], {})
            }
            if not waiting_entry:
                return {
                    "ok": False,
                    "reason": (
                        "live_authority_requires_waiting_entry:"
                        f"{gate_key}:{status}"
                    ),
                    "gate": audit_gate,
                }
    if str(payload.get("status") or "").strip().lower() == "error":
        return {
            "ok": False,
            "reason": "live_authority_error",
            "gate": audit_gate,
        }
    if "max_budget_multiplier" in payload and safe_float(
        payload.get("max_budget_multiplier")
    ) <= 0:
        return {
            "ok": False,
            "reason": "live_authority_budget_zero",
            "gate": audit_gate,
        }
    if not gate_status or gate_status == "clear":
        return {"ok": True, "gate": audit_gate}
    if gate_status in LIVE_AUTHORITY_VALIDATION_HARD_BLOCK_STATUSES:
        return {
            "ok": False,
            "reason": f"live_authority_validation_gate:{gate_status}",
            "gate": audit_gate,
        }
    if (
        gate_status in LIVE_AUTHORITY_VALIDATION_WAIT_ONLY_STATUSES
        and not waiting_entry
    ):
        return {
            "ok": False,
            "reason": f"live_authority_requires_waiting_entry:{gate_status}",
            "gate": gate,
        }
    return {"ok": True, "gate": audit_gate}
