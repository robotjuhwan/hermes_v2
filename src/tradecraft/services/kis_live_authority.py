from __future__ import annotations

import math
import re
from typing import Any

from tradecraft.services.kis_entry_gate import ENTRY_WAIT_STYLE, performance_scale_entry_quality_check
from tradecraft.services.kis_horizon import normalize_horizon
from tradecraft.services.kis_manager_prompt import (
    validation_repair_discipline_tokens,
    validation_repair_period_memory_quality_tokens,
)

SMALL_WAITING_PROBE_VALUE_CAP_KRW = 50_000.0


def lane_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s/]+", "_", text)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return 0.0
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
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


def _clean_text(value: Any, *, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def active_revision_waiting_entry_reason(live_authority: dict[str, Any]) -> str:
    evidence = (
        live_authority.get("active_revision_evidence")
        if isinstance(live_authority.get("active_revision_evidence"), dict)
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
    effective_samples = _safe_int(evidence.get("effective_sample_count"))
    min_samples = _safe_int(evidence.get("min_samples_to_scale"))
    scale_up_allowed = _safe_bool(evidence.get("scale_up_allowed"))
    hard_blocking_count = _safe_int(evidence.get("hard_blocking_count"))
    if min_samples > 0 and effective_samples < min_samples and not scale_up_allowed:
        return "active_revision_evidence:insufficient_samples"
    if hard_blocking_count > 0 and not scale_up_allowed:
        return "active_revision_evidence:hard_blocking"
    return ""


def live_authority_new_block_qty_cap(
    live_authority: dict[str, Any],
) -> int | None:
    if not isinstance(live_authority, dict):
        return None
    validation_gate = (
        live_authority.get("validation_gate")
        if isinstance(live_authority.get("validation_gate"), dict)
        else {}
    )
    validation_pressure = (
        validation_gate.get("validation_pressure")
        if isinstance(validation_gate.get("validation_pressure"), dict)
        else {}
    )
    gate_status = str(validation_gate.get("status") or "").strip().lower()
    risk_governor = str(
        validation_gate.get("risk_governor_action") or ""
    ).strip().lower()
    pressure_severity = str(validation_pressure.get("severity") or "").strip().lower()
    entry_posture = str(
        validation_pressure.get("entry_posture") or ""
    ).strip().lower()
    sizing_posture = str(
        validation_pressure.get("sizing_posture") or ""
    ).strip().lower()
    authority_status = str(live_authority.get("status") or "").strip().lower()
    if authority_status == "error":
        return 1
    if risk_governor == "halt_new_risk":
        return 1
    if risk_governor in {"de_risk", "risk_off", "reduced"}:
        return 1
    if (
        entry_posture == "no_new_entry"
        or sizing_posture in {
            "halt_new_risk",
            "no_new_risk",
        }
        or pressure_severity in {
            "hard_block",
            "blocked",
            "risk_off",
        }
    ):
        return 1
    if gate_status in {
        "blocked_by_validation",
        "validation_probe",
        "validation_error",
        "validation_incomplete",
        "validation_missing",
        "validation_research_only",
        "validation_stale",
    }:
        return 1
    return None


def live_authority_new_risk_halt(live_authority: dict[str, Any]) -> str:
    if not isinstance(live_authority, dict):
        return ""
    validation_gate = (
        live_authority.get("validation_gate")
        if isinstance(live_authority.get("validation_gate"), dict)
        else {}
    )
    risk_governor = str(
        validation_gate.get("risk_governor_action") or ""
    ).strip().lower()
    if risk_governor == "halt_new_risk":
        return "halt_new_risk"
    return ""


def live_authority_budget_zero(live_authority: dict[str, Any]) -> bool:
    if not isinstance(live_authority, dict):
        return False
    return (
        "max_budget_multiplier" in live_authority
        and _safe_float(live_authority.get("max_budget_multiplier")) <= 0
    )


def active_revision_immediate_probe_allowed(
    live_authority: dict[str, Any],
    waiting_reason: str,
) -> bool:
    reason = str(waiting_reason or "").strip().lower()
    if not reason.startswith("active_revision_evidence:"):
        return False
    if any(token in reason for token in ("pending_close", "hard_blocking")):
        return False
    evidence = (
        live_authority.get("active_revision_evidence")
        if isinstance(live_authority.get("active_revision_evidence"), dict)
        else {}
    )
    status = str(evidence.get("status") or "").strip().lower()
    allowed_statuses = {
        "no_active_revision_samples",
        "no_active_revision_samples_with_proxy",
        "active_revision_sample_building",
        "insufficient_active_revision_samples",
        "insufficient_samples",
    }
    if status and status not in allowed_statuses:
        return False
    validation_gate = (
        live_authority.get("validation_gate")
        if isinstance(live_authority.get("validation_gate"), dict)
        else {}
    )
    gate_status = str(validation_gate.get("status") or "").strip().lower()
    if gate_status not in {"", "clear", "validation_normal"}:
        return False
    risk_governor = str(
        validation_gate.get("risk_governor_action") or ""
    ).strip().lower()
    if risk_governor in {"halt_new_risk", "risk_off", "de_risk"}:
        return False
    validation_pressure = (
        validation_gate.get("validation_pressure")
        if isinstance(validation_gate.get("validation_pressure"), dict)
        else {}
    )
    entry_posture = str(
        validation_pressure.get("entry_posture") or ""
    ).strip().lower()
    if entry_posture == "patient_waiting_entry":
        return False
    return True


def lane_authority_immediate_probe_allowed(lane_action: dict[str, Any]) -> bool:
    if not isinstance(lane_action, dict):
        return False
    grade = str(lane_action.get("grade") or "").strip().lower()
    action = str(lane_action.get("action") or "").strip().lower()
    reason = str(lane_action.get("reason") or "").strip().lower()
    quality_hint = str(
        lane_action.get("performance_quality_hint") or ""
    ).strip().lower()
    if grade not in {"insufficient", ""}:
        return False
    sample_building = any(
        token in value
        for value in (action, reason, quality_hint)
        for token in (
            "small_probe_until_sample_builds",
            "sample_building",
            "no_alpha_samples",
        )
    )
    if not sample_building:
        return False
    block_tokens = {
        "observe",
        "weak",
        "cost",
        "entry_quality",
        "validation_repair",
        "validation_evidence",
        "risk_budget_reduced",
        "risk_off",
        "halt",
        "recovery",
    }
    if any(token in action or token in reason for token in block_tokens):
        return False
    weak_sources = [
        str(item).strip().lower()
        for item in list(lane_action.get("weak_lane_sources") or [])
        if str(item).strip()
    ]
    if weak_sources:
        return False
    risk_of_ruin = _safe_float(lane_action.get("risk_of_ruin_pct"))
    if risk_of_ruin >= 20.0:
        return False
    drawdown = _safe_float(lane_action.get("max_drawdown_pct"))
    if drawdown <= -7.0:
        return False
    recovery = _safe_float(lane_action.get("recovery_factor"))
    if 0.0 < recovery < 0.5:
        return False
    return True


def live_authority_waiting_entry_required(live_authority: dict[str, Any]) -> str:
    if not isinstance(live_authority, dict):
        return ""
    active_revision_reason = active_revision_waiting_entry_reason(live_authority)
    if active_revision_reason:
        return active_revision_reason
    validation_gate = (
        live_authority.get("validation_gate")
        if isinstance(live_authority.get("validation_gate"), dict)
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
    gate_status = str(validation_gate.get("status") or "").strip().lower()
    risk_governor = str(
        validation_gate.get("risk_governor_action") or ""
    ).strip().lower()
    authority_status = str(live_authority.get("status") or "").strip().lower()
    if authority_status == "error":
        return "live_authority_error"
    if risk_governor == "risk_off":
        return "risk_governor:risk_off"
    if gate_status in {
        "blocked_by_validation",
        "validation_error",
        "validation_incomplete",
        "validation_missing",
        "validation_research_only",
        "validation_stale",
    }:
        return gate_status
    if entry_posture == "patient_waiting_entry":
        return "validation_pressure:patient_waiting_entry"
    lane_authority = (
        live_authority.get("lane_authority")
        if isinstance(live_authority.get("lane_authority"), dict)
        else {}
    )
    for gate_key in ("validation_shadow_gate", "validation_exposure_gate"):
        gate = (
            lane_authority.get(gate_key)
            if isinstance(lane_authority.get(gate_key), dict)
            else {}
        )
        if not gate:
            continue
        if _safe_bool(gate.get("requires_waiting_entry")) or _safe_bool(
            gate.get("blocks_scale_up")
        ):
            status = str(gate.get("status") or gate_key).strip().lower()
            return f"{gate_key}:{status}"
    return ""



def candidate_lanes_for_row(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    horizon = normalize_horizon(row.get("horizon"))
    setup_tokens = {
        token
        for token in (
            lane_token(row.get("strategy_family")),
            lane_token(row.get("entry_setup")),
            lane_token(row.get("setup")),
            lane_token(row.get("lane")),
            lane_token(metadata.get("strategy_family")),
            lane_token(metadata.get("entry_setup")),
            lane_token(metadata.get("setup")),
            lane_token(metadata.get("lane")),
        )
        if token
    }
    candidate_lanes = {horizon, f"kis:{horizon}"}
    for token in setup_tokens:
        candidate_lanes.add(token)
        candidate_lanes.add(f"{horizon}:{token}")
        candidate_lanes.add(f"kis:{horizon}:{token}")

    repair = row.get("validation_repair")
    if not isinstance(repair, dict):
        repair = metadata.get("validation_repair")
    repair_tokens = list(validation_repair_discipline_tokens(repair))
    if isinstance(repair, dict):
        for key in ("discipline_id", "dimension"):
            token = lane_token(repair.get(key))
            if token and token not in repair_tokens:
                repair_tokens.append(token)
    for token in repair_tokens[:8]:
        candidate_lanes.add(f"{horizon}:validation:{token}")
        candidate_lanes.add(f"kis:{horizon}:validation:{token}")
    for token in validation_repair_period_memory_quality_tokens(repair):
        candidate_lanes.add(f"{horizon}:period_memory:{token}")
        candidate_lanes.add(f"kis:{horizon}:period_memory:{token}")
    return candidate_lanes


def lane_set(value: Any) -> set[str]:
    return {
        token
        for token in (lane_token(item) for item in list(value or []))
        if token
    }


def match_lane_authority_for_row(
    lane_authority: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(lane_authority, dict):
        return {}
    horizon = normalize_horizon(row.get("horizon"))
    candidate_lanes = candidate_lanes_for_row(row)
    weak_lanes = lane_set(lane_authority.get("weak_lanes"))
    weak_lane_groups: dict[str, set[str]] = {}
    for key in (
        "cost_weak_lanes",
        "cost_evidence_weak_lanes",
        "entry_quality_weak_lanes",
        "validation_evidence_weak_lanes",
        "validation_repair_weak_lanes",
    ):
        weak_lane_groups[key] = lane_set(lane_authority.get(key))
        weak_lanes.update(weak_lane_groups[key])
    insufficient_lanes = lane_set(lane_authority.get("insufficient_lanes"))
    scale_candidate_lanes = lane_set(lane_authority.get("scale_candidate_lanes"))
    qualified_lanes = lane_set(lane_authority.get("qualified_lanes"))
    matched_weak = sorted(candidate_lanes.intersection(weak_lanes))
    matched_insufficient = sorted(candidate_lanes.intersection(insufficient_lanes))
    matched_scale_candidate = sorted(
        candidate_lanes.intersection(scale_candidate_lanes)
    )
    matched_qualified = sorted(candidate_lanes.intersection(qualified_lanes))
    if (
        not matched_weak
        and not matched_insufficient
        and not matched_scale_candidate
        and not matched_qualified
    ):
        return {}
    matched_lane = (
        matched_weak[0]
        if matched_weak
        else matched_insufficient[0]
        if matched_insufficient
        else matched_scale_candidate[0]
        if matched_scale_candidate
        else matched_qualified[0]
    )
    lane_actions = (
        lane_authority.get("lane_actions")
        if isinstance(lane_authority.get("lane_actions"), dict)
        else {}
    )
    lane_detail = (
        lane_actions.get(matched_lane)
        or lane_actions.get(horizon)
        or lane_actions.get(f"kis:{horizon}")
        or {}
    )
    if not isinstance(lane_detail, dict):
        lane_detail = {}
    return {
        "candidate_lanes": candidate_lanes,
        "matched_lane": matched_lane,
        "matched_weak": matched_weak,
        "matched_insufficient": matched_insufficient,
        "matched_scale_candidate": matched_scale_candidate,
        "matched_qualified": matched_qualified,
        "lane_detail": lane_detail,
        "validation_evidence_weak_lanes": weak_lane_groups[
            "validation_evidence_weak_lanes"
        ],
        "weak_lane_sources": sorted(
            key for key, lanes in weak_lane_groups.items() if matched_lane in lanes
        ),
    }


def build_lane_authority_action(
    *,
    row: dict[str, Any],
    lane_match: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(lane_match, dict) or not lane_match:
        return {}
    horizon = normalize_horizon(row.get("horizon"))
    matched_lane = str(lane_match.get("matched_lane") or "").strip()
    matched_weak = list(lane_match.get("matched_weak") or [])
    matched_insufficient = list(lane_match.get("matched_insufficient") or [])
    matched_scale_candidate = list(lane_match.get("matched_scale_candidate") or [])
    lane_detail = (
        lane_match.get("lane_detail")
        if isinstance(lane_match.get("lane_detail"), dict)
        else {}
    )
    validation_evidence_weak_lanes = set(
        lane_match.get("validation_evidence_weak_lanes") or []
    )
    risk_budget_passport = (
        lane_detail.get("risk_budget_passport")
        if isinstance(lane_detail.get("risk_budget_passport"), dict)
        else {}
    )
    action = str(lane_detail.get("action") or "").strip().lower()
    if not action:
        action = (
            "observe_or_waiting_entry"
            if matched_weak
            else "small_probe_until_sample_builds"
            if matched_insufficient
            else "eligible_to_press_when_validation_clear"
            if matched_scale_candidate
            else "normal_or_selective_press"
        )
    grade = str(lane_detail.get("grade") or "").strip().lower()
    if not grade:
        grade = (
            "observe_only"
            if matched_weak
            else "insufficient"
            if matched_insufficient
            else "scale_candidate"
            if matched_scale_candidate
            else "qualified"
        )
    applied_budget_multiplier = _safe_float(
        lane_detail.get("applied_max_budget_multiplier")
    )
    max_budget_multiplier = _safe_float(lane_detail.get("max_budget_multiplier"))
    passport_budget_multiplier = _safe_float(
        risk_budget_passport.get("effective_risk_budget_multiplier")
        or risk_budget_passport.get("applied_risk_budget_multiplier")
    )
    evidence_fields = lane_authority_evidence_fields(
        lane_detail,
        risk_budget_passport,
    )
    weak_lane_sources = list(lane_match.get("weak_lane_sources") or [])
    scale_blockers = {
        str(item)
        for item in list(evidence_fields.get("scale_blockers") or [])
        if str(item).strip()
    }
    if (
        "cost_evidence_repair" in scale_blockers
        or "cost_evidence_weak_lanes" in weak_lane_sources
    ):
        evidence_fields.setdefault("scale_blocked_by_cost_precision", True)
        evidence_fields.setdefault("scale_blocked_by_cost_evidence", True)
    if "verified_edge_sample_cap" in scale_blockers:
        evidence_fields.setdefault("scale_blocked_by_verified_edge_samples", True)
    if "verified_edge_net_pnl_cap" in scale_blockers:
        evidence_fields.setdefault("scale_blocked_by_verified_edge_net_pnl", True)
    original_qty = max(_safe_int(row.get("qty")), 0)
    qty_cap = original_qty if original_qty > 0 else 1
    qty_cap_source = "no_cap"
    budget_multiplier_sources = [
        ("applied_max_budget_multiplier", applied_budget_multiplier),
        ("max_budget_multiplier", max_budget_multiplier),
        ("risk_budget_passport", passport_budget_multiplier),
    ]
    valid_budget_multipliers = [
        (source, value)
        for source, value in budget_multiplier_sources
        if value > 0
    ]
    if valid_budget_multipliers:
        budget_multiplier_source, budget_multiplier = min(
            valid_budget_multipliers,
            key=lambda item: item[1],
        )
    else:
        budget_multiplier_source = ""
        budget_multiplier = 0.0
    if (
        (matched_weak or matched_insufficient)
        and 0 < budget_multiplier < 1
        and original_qty > 1
    ):
        qty_cap = max(int(math.floor(original_qty * budget_multiplier)), 1)
        qty_cap_source = budget_multiplier_source
        target_value = _safe_float(row.get("target_block_value_krw"))
        entry_style = str(row.get("entry_style") or "").strip().lower()
        if (
            entry_style == ENTRY_WAIT_STYLE
            and 0 < target_value <= SMALL_WAITING_PROBE_VALUE_CAP_KRW
        ):
            qty_cap = original_qty
            qty_cap_source = f"{qty_cap_source}:small_waiting_probe_value_preserved"
    elif matched_weak or matched_insufficient:
        qty_cap = min(qty_cap, 1)
        qty_cap_source = "default_small_probe"
    scale_budget_multiplier = budget_multiplier if budget_multiplier > 1 else 0.0
    scale_up_allowed = bool(lane_detail.get("scale_up_allowed")) and (
        scale_budget_multiplier > 1
    )
    return {
        **evidence_fields,
        "lane": horizon,
        "matched_lane": matched_lane,
        "grade": grade,
        "action": action,
        "max_budget_multiplier": max_budget_multiplier,
        "applied_max_budget_multiplier": applied_budget_multiplier,
        "risk_budget_passport_multiplier": passport_budget_multiplier,
        "budget_multiplier": budget_multiplier,
        "budget_multiplier_source": budget_multiplier_source,
        "scale_up_allowed": scale_up_allowed,
        "validation_evidence_status": str(
            lane_detail.get("validation_evidence_status") or ""
        ).strip(),
        "validation_missing_dimensions": [
            str(item)
            for item in list(lane_detail.get("validation_missing_dimensions") or [])[:4]
            if str(item).strip()
        ],
        "validation_failed_dimensions": [
            str(item)
            for item in list(lane_detail.get("validation_failed_dimensions") or [])[:4]
            if str(item).strip()
        ],
        "scale_blocked_by_validation_evidence": bool(
            lane_detail.get("scale_blocked_by_validation_evidence")
            or risk_budget_passport.get("scale_blocked_by_validation_evidence")
            or matched_lane in validation_evidence_weak_lanes
        ),
        "weak_lane_sources": weak_lane_sources,
        "requires_waiting_entry": bool(
            matched_weak or lane_detail.get("requires_waiting_entry")
        ),
        "entry_quality_requirements": [
            str(item)
            for item in list(lane_detail.get("entry_quality_requirements") or [])[:4]
            if str(item).strip()
        ],
        "qty_cap": qty_cap,
        "qty_cap_source": qty_cap_source,
        "qty_scale_multiplier": (
            round(scale_budget_multiplier, 6) if scale_up_allowed else 0.0
        ),
        "reason": f"{matched_lane}:{action}",
    }


def performance_lane_action(
    *,
    live_authority: dict[str, Any],
    candidate_lanes: set[str],
    row: dict[str, Any],
) -> dict[str, Any]:
    rows = live_authority.get("performance_lanes")
    if not isinstance(rows, list):
        return {}

    candidates = {lane_token(item) for item in candidate_lanes if lane_token(item)}
    matches: list[tuple[int, dict[str, Any], str]] = []
    priority = {
        "weak_review": 0,
        "sample_building": 1,
        "no_alpha_samples": 2,
        "scale_candidate": 3,
        "qualified": 4,
    }
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        venue = str(raw.get("venue") or "").strip().lower()
        if venue and venue != "kis":
            continue
        lane = lane_token(raw.get("lane"))
        quality_hint = str(raw.get("quality_hint") or "").strip().lower()
        if not lane or lane not in candidates or quality_hint not in priority:
            continue
        matches.append((priority[quality_hint], raw, lane))
    if not matches:
        return {}

    _, lane_row, matched_lane = sorted(matches, key=lambda item: item[0])[0]
    quality_hint = str(lane_row.get("quality_hint") or "").strip().lower()
    action_hint = str(lane_row.get("action_hint") or "").strip().lower()
    weak = quality_hint == "weak_review"
    scale_candidate = quality_hint == "scale_candidate"
    qualified = quality_hint == "qualified"
    validation_gate = (
        live_authority.get("validation_gate")
        if isinstance(live_authority.get("validation_gate"), dict)
        else {}
    )
    validation_status = str(validation_gate.get("status") or "").strip().lower()
    lane_authority = (
        live_authority.get("lane_authority")
        if isinstance(live_authority.get("lane_authority"), dict)
        else {}
    )
    validation_shadow_gate = (
        lane_authority.get("validation_shadow_gate")
        if isinstance(lane_authority.get("validation_shadow_gate"), dict)
        else {}
    )
    validation_exposure_gate = (
        lane_authority.get("validation_exposure_gate")
        if isinstance(lane_authority.get("validation_exposure_gate"), dict)
        else {}
    )
    shadow_blocks_scale = _safe_bool(
        validation_shadow_gate.get("blocks_scale_up")
    ) or _safe_bool(validation_shadow_gate.get("requires_waiting_entry"))
    exposure_blocks_scale = _safe_bool(
        validation_exposure_gate.get("blocks_scale_up")
    ) or _safe_bool(validation_exposure_gate.get("requires_waiting_entry"))
    validation_scale_blocked = shadow_blocks_scale or exposure_blocks_scale
    exposure_cap_multiplier = _safe_float(
        validation_exposure_gate.get("cap_multiplier")
    )
    global_max_multiplier = _safe_float(live_authority.get("max_budget_multiplier"))
    has_risk_budget_multiplier = (
        lane_row.get("risk_budget_multiplier") not in (None, "", [], {})
    )
    has_profit_factor = lane_row.get("profit_factor") not in (None, "", [], {})
    has_max_drawdown = lane_row.get("max_drawdown_pct") not in (None, "", [], {})
    has_recovery_factor = lane_row.get("recovery_factor") not in (None, "", [], {})
    has_risk_of_ruin = lane_row.get("risk_of_ruin_pct") not in (None, "", [], {})
    has_lane_confidence = (
        lane_row.get("lane_confidence_score") not in (None, "", [], {})
    )
    risk_budget_multiplier = _safe_float(lane_row.get("risk_budget_multiplier"))
    profit_factor = _safe_float(lane_row.get("profit_factor"))
    max_drawdown_pct = _safe_float(lane_row.get("max_drawdown_pct"))
    recovery_factor = _safe_float(lane_row.get("recovery_factor"))
    recovery_cap_multiplier = _safe_float(
        lane_row.get("recovery_factor_cap_multiplier")
    )
    risk_of_ruin_pct = _safe_float(lane_row.get("risk_of_ruin_pct"))
    lane_confidence_score = _safe_float(lane_row.get("lane_confidence_score"))
    scale_metrics_present = bool(
        has_risk_budget_multiplier
        and has_profit_factor
        and has_max_drawdown
        and has_recovery_factor
        and has_risk_of_ruin
        and has_lane_confidence
    )
    scale_metrics_missing = bool(scale_candidate and not scale_metrics_present)
    risk_profile_allows_scale = bool(
        scale_metrics_present
        and risk_budget_multiplier > 1.0
        and profit_factor >= 1.5
        and recovery_factor >= 1.0
        and max_drawdown_pct >= -7.0
        and risk_of_ruin_pct < 10.0
        and lane_confidence_score >= 0.5
    )
    risk_profile_requires_waiting = bool(
        scale_metrics_missing
        or 0.0 < risk_budget_multiplier < 1.0
        or 0.0 < recovery_cap_multiplier < 1.0
        or risk_of_ruin_pct >= 10.0
    )
    scale_entry_quality = performance_scale_entry_quality_check(row)
    performance_scale_allowed = (
        scale_candidate
        and bool(live_authority.get("allow_scale_up"))
        and validation_status in {"", "clear"}
        and global_max_multiplier > 1.0
        and risk_profile_allows_scale
        and not validation_scale_blocked
        and bool(scale_entry_quality.get("scale_up_allowed"))
    )
    if performance_scale_allowed:
        budget_multiplier = min(global_max_multiplier, 1.25)
    elif weak:
        budget_multiplier = 0.25
    elif qualified or scale_candidate:
        budget_multiplier = 1.0
    else:
        budget_multiplier = 0.5
    if risk_budget_multiplier > 0.0:
        budget_multiplier = min(budget_multiplier, risk_budget_multiplier)
    if shadow_blocks_scale:
        budget_multiplier = min(budget_multiplier, 1.0)
    if exposure_blocks_scale and exposure_cap_multiplier > 0.0:
        budget_multiplier = min(budget_multiplier, exposure_cap_multiplier)
    original_qty = max(_safe_int(row.get("qty")), 0)
    qty_cap = (
        original_qty
        if (
            (performance_scale_allowed or qualified or scale_candidate)
            and budget_multiplier >= 1.0
        )
        else max(int(math.floor(original_qty * budget_multiplier)), 1)
    )
    if original_qty <= 0:
        qty_cap = 1
    requires_waiting_entry = weak or "waiting" in action_hint
    if risk_profile_requires_waiting and not performance_scale_allowed:
        requires_waiting_entry = True
    action = action_hint or (
        "observe_or_waiting_entry"
        if weak
        else "risk_budget_reduced_waiting_entry"
        if risk_profile_requires_waiting
        else "eligible_to_review_for_sizing_increase"
        if performance_scale_allowed
        else "normal_or_selective_press"
        if qualified
        else "small_probe_until_sample_builds"
    )
    return {
        "lane": normalize_horizon(row.get("horizon")),
        "matched_lane": matched_lane,
        "grade": (
            "observe_only"
            if weak
            else "scale_candidate"
            if scale_candidate
            else "qualified"
            if qualified
            else "insufficient"
        ),
        "action": action,
        "max_budget_multiplier": budget_multiplier,
        "applied_max_budget_multiplier": budget_multiplier,
        "budget_multiplier": budget_multiplier,
        "budget_multiplier_source": "performance_lanes",
        "scale_up_allowed": performance_scale_allowed,
        "validation_scale_blocked": validation_scale_blocked,
        "validation_shadow_gate_status": validation_shadow_gate.get("status"),
        "validation_exposure_gate_status": validation_exposure_gate.get("status"),
        "risk_budget_multiplier": (
            round(risk_budget_multiplier, 6)
            if risk_budget_multiplier > 0.0
            else None
        ),
        "risk_profile_allows_scale": risk_profile_allows_scale,
        "risk_profile_requires_waiting": risk_profile_requires_waiting,
        "scale_metrics_missing": scale_metrics_missing,
        "profit_factor": profit_factor if profit_factor > 0 else None,
        "max_drawdown_pct": max_drawdown_pct if max_drawdown_pct != 0.0 else None,
        "recovery_factor": recovery_factor if recovery_factor != 0.0 else None,
        "recovery_factor_cap_multiplier": (
            round(recovery_cap_multiplier, 6)
            if recovery_cap_multiplier > 0.0
            else None
        ),
        "risk_of_ruin_pct": lane_row.get("risk_of_ruin_pct"),
        "lane_confidence_score": lane_row.get("lane_confidence_score"),
        "recommended_risk_fraction": lane_row.get("recommended_risk_fraction"),
        "scale_metrics_present": scale_metrics_present,
        "scale_metrics_required": [
            "risk_budget_multiplier",
            "profit_factor",
            "max_drawdown_pct",
            "recovery_factor",
            "risk_of_ruin_pct",
            "lane_confidence_score",
        ],
        "requires_waiting_entry": (
            requires_waiting_entry
            or shadow_blocks_scale
            or exposure_blocks_scale
        ),
        "entry_quality_requirements": [
            "respect_realized_performance_lane_action_hint",
            (
                "scale_only_when_entry_quality_validation_and_live_evidence_agree"
                if performance_scale_allowed
                else "use_waiting_entry_until_risk_budget_recovers"
                if risk_profile_requires_waiting
                else "prefer_waiting_entry_until_lane_recovers"
            ),
        ],
        "qty_cap": qty_cap,
        "qty_cap_source": "performance_lanes",
        "qty_scale_multiplier": (
            round(budget_multiplier, 6) if performance_scale_allowed else 0.0
        ),
        "performance_quality_hint": quality_hint,
        "performance_action_hint": action_hint,
        "scale_entry_quality": scale_entry_quality,
        "reason": f"{matched_lane}:{action}",
        "source": "performance_lanes",
    }


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
        number = _safe_float(value)
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
