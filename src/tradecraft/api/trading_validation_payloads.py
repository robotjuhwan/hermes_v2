from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def annotate_trading_validation_freshness(
    payload: dict[str, Any],
    *,
    max_age_sec: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    age_sec = _iso_age_sec(payload.get("computed_at"), now=now)
    max_age_sec = max(int(max_age_sec), 1)
    payload["max_age_sec"] = max_age_sec
    payload["age_sec"] = round(age_sec, 3) if age_sec is not None else None
    if str(payload.get("status") or "").strip().lower() == "empty":
        payload["stale"] = False
        payload["stale_reason"] = ""
        return payload
    stale = age_sec is None or age_sec > max_age_sec
    payload["stale"] = stale
    payload["stale_reason"] = (
        "invalid_or_missing_computed_at"
        if age_sec is None
        else f"age_sec={int(age_sec)},max_age_sec={max_age_sec}"
        if stale
        else ""
    )
    return payload


def promote_trading_validation_payload_fields(
    payload: dict[str, Any],
    *,
    expected_discipline_count: int,
) -> dict[str, Any]:
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    source = nested or payload
    summary = (
        source.get("summary")
        if isinstance(source.get("summary"), dict)
        else payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else {}
    )
    disciplines = source.get("disciplines") if isinstance(source.get("disciplines"), list) else []
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    monte_carlo = (
        source.get("monte_carlo") if isinstance(source.get("monte_carlo"), dict) else {}
    )
    remediation_plan = (
        source.get("remediation_plan")
        if isinstance(source.get("remediation_plan"), dict)
        else {}
    )
    operator_guidance = (
        source.get("operator_guidance")
        if isinstance(source.get("operator_guidance"), list)
        else []
    )
    discipline_count = source.get("discipline_count")
    if discipline_count is None:
        discipline_count = len(disciplines)
    diagnostic_status = (
        summary.get("diagnostic_status")
        or source.get("diagnostic_status")
        or _infer_diagnostic_status(summary)
    )
    if isinstance(summary, dict) and diagnostic_status:
        summary["diagnostic_status"] = diagnostic_status
    top_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if top_summary is not summary and diagnostic_status:
        top_summary["diagnostic_status"] = diagnostic_status
    readiness = _risk_adjusted_readiness(
        summary.get("readiness") or source.get("readiness") or "",
        summary,
    )
    if isinstance(summary, dict) and readiness:
        summary["readiness"] = readiness
    if top_summary is not summary and readiness:
        top_summary["readiness"] = readiness
    payload["readiness"] = readiness
    payload["diagnostic_status"] = diagnostic_status
    payload["score"] = summary.get("total_score")
    if "sample_count" in metrics:
        payload["sample_count"] = metrics.get("sample_count")
    failure_attribution = (
        metrics.get("failure_attribution")
        if isinstance(metrics.get("failure_attribution"), dict)
        else {}
    )
    cost_simulation = (
        metrics.get("cost_simulation")
        if isinstance(metrics.get("cost_simulation"), dict)
        else {}
    )
    payload["failure_drivers"] = _compact_metric_rows(
        failure_attribution.get("worst_groups"),
        fields=(
            "group_type",
            "group",
            "sample_count",
            "total_net_pnl",
            "expectancy_pct",
            "win_rate_pct",
            "profit_factor",
            "cost_drag_pct_of_gross_pnl",
            "risk_score",
        ),
    )
    payload["cost_drivers"] = _compact_metric_rows(
        cost_simulation.get("worst_cost_groups"),
        fields=(
            "group_type",
            "group",
            "sample_count",
            "total_net_pnl",
            "total_cost",
            "cost_drag_pct_of_abs_gross_pnl",
            "net_negative_after_cost",
        ),
    )
    payload["recovery_focus"] = [
        str(row)
        for row in (
            failure_attribution.get("recovery_focus")
            if isinstance(failure_attribution.get("recovery_focus"), list)
            else []
        )
        if str(row).strip()
    ][:4]
    payload["failed_discipline_ids"] = [
        str(row.get("id") or "")
        for row in disciplines
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower() == "fail"
        and str(row.get("id") or "").strip()
    ]
    payload["warned_discipline_ids"] = [
        str(row.get("id") or "")
        for row in disciplines
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower() == "warn"
        and str(row.get("id") or "").strip()
    ]
    for key in (
        "pass_count",
        "warn_count",
        "fail_count",
        "missing_count",
        "diagnostic_pass_count",
        "diagnostic_warn_count",
        "diagnostic_fail_count",
        "diagnostic_missing_count",
        "hard_fail_count",
        "hard_missing_count",
        "hard_blocking_count",
        "core_pass_count",
        "core_warn_count",
        "core_fail_count",
        "core_missing_count",
    ):
        if key in summary:
            payload[key] = summary.get(key)
    payload["discipline_count"] = discipline_count
    payload["expected_discipline_count"] = int(expected_discipline_count)
    payload["disciplines"] = disciplines
    payload["metrics"] = metrics
    payload["monte_carlo"] = monte_carlo
    payload["operator_guidance"] = operator_guidance
    payload["remediation_plan"] = remediation_plan
    venue = str(source.get("venue") or payload.get("venue") or "")
    payload["lane_authority_summary"] = summarize_trading_validation_lane_authority(
        metrics,
        venue=venue,
    )
    if venue:
        venue_payloads = {venue: payload}
        payload["top_bottlenecks"] = summarize_trading_validation_bottlenecks(
            venue_payloads,
            limit=6,
        )
        payload["primary_next_actions"] = summarize_trading_validation_next_actions(
            venue_payloads,
        )
    return payload


def _compact_metric_rows(
    rows: Any,
    *,
    fields: tuple[str, ...],
    limit: int = 6,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {key: row.get(key) for key in fields if key in row}
        if item:
            compact.append(item)
        if len(compact) >= max(int(limit or 0), 0):
            break
    return compact


def summarize_trading_validation_lane_authority(
    metrics: dict[str, Any],
    *,
    venue: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    lane_scorecards = (
        metrics.get("lane_scorecards")
        if isinstance(metrics.get("lane_scorecards"), dict)
        else {}
    )
    lane_actions = (
        lane_scorecards.get("lane_actions")
        if isinstance(lane_scorecards.get("lane_actions"), dict)
        else {}
    )
    reduced_lanes: list[dict[str, Any]] = []
    probe_lane_names: list[str] = []
    for lane, raw_action in sorted(lane_actions.items()):
        if not isinstance(raw_action, dict):
            continue
        lane_name = str(lane or "")
        action = raw_action
        if _lane_action_allows_probe(action):
            probe_lane_names.append(lane_name)
        authority_multiplier = _to_float(
            action.get("authority_multiplier")
            if action.get("authority_multiplier") is not None
            else action.get("max_budget_multiplier")
        )
        reasons = lane_authority_reduction_reasons(action)
        if not reasons:
            continue
        reduced_lanes.append(
            {
                "venue": str(venue or ""),
                "lane": lane_name,
                "grade": str(action.get("grade") or ""),
                "action": str(action.get("action") or ""),
                "authority_multiplier": round(authority_multiplier, 6),
                "requires_waiting_entry": bool(action.get("requires_waiting_entry")),
                "reasons": reasons[:8],
                "cost_precision_verified_rate_pct": action.get(
                    "cost_precision_verified_rate_pct"
                ),
                "cost_verified_alpha_count": action.get("cost_verified_alpha_count"),
                "cost_unverified_alpha_count": action.get(
                    "cost_unverified_alpha_count"
                ),
                "avg_entry_quality_score": action.get("avg_entry_quality_score"),
                "bad_entry_quality_rate_pct": action.get("bad_entry_quality_rate_pct"),
                "validation_repair_enforced_count": action.get(
                    "validation_repair_enforced_count"
                ),
                "validation_repair_scale_up_blocked_count": action.get(
                    "validation_repair_scale_up_blocked_count"
                ),
                "validation_repair_requirements": action.get(
                    "validation_repair_requirements",
                    [],
                ),
            }
        )
    reduced_lanes = sorted(
        reduced_lanes,
        key=lambda row: (
            _to_float(row.get("authority_multiplier") or 0),
            str(row.get("venue") or ""),
            str(row.get("lane") or ""),
        ),
    )
    status = "missing"
    if lane_scorecards:
        status = "warn" if reduced_lanes else "pass"
    weak_lanes = _compact_name_list(lane_scorecards.get("weak_lanes"), limit=limit)
    scale_blocked_lanes = _dedupe_names(
        [
            str(row.get("lane") or "").strip()
            for row in reduced_lanes
            if str(row.get("lane") or "").strip()
        ],
        limit=limit,
    )
    probe_lane_names = _dedupe_names(probe_lane_names, limit=limit)
    allow_scale_up = bool(lane_scorecards.get("global_scale_up_allowed"))
    return {
        "version": "lane_authority_summary_v1",
        "status": status,
        "venue": str(venue or ""),
        "execution_posture": _lane_execution_posture(
            allow_scale_up=allow_scale_up,
            probe_lanes=probe_lane_names,
            scale_blocked_lanes=scale_blocked_lanes,
            weak_lanes=weak_lanes,
        ),
        "probe_policy": (
            "small waiting-entry/probe blocks remain allowed; scale-up waits for lane evidence and safety gates"
            if probe_lane_names and not allow_scale_up
            else ""
        ),
        "probe_lane_count": len(probe_lane_names),
        "probe_lane_names": probe_lane_names,
        "scale_blocked_lane_count": len(scale_blocked_lanes),
        "scale_blocked_lanes": scale_blocked_lanes,
        "reduced_lane_count": len(reduced_lanes),
        "reduced_lanes": reduced_lanes[:limit],
        "scale_candidate_lanes": lane_scorecards.get("scale_candidate_lanes", []),
        "weak_lanes": weak_lanes,
        "insufficient_lanes": lane_scorecards.get("insufficient_lanes", []),
        "cost_evidence_weak_lanes": lane_scorecards.get(
            "cost_evidence_weak_lanes",
            [],
        ),
        "entry_quality_weak_lanes": lane_scorecards.get(
            "entry_quality_weak_lanes",
            [],
        ),
        "validation_repair_weak_lanes": lane_scorecards.get(
            "validation_repair_weak_lanes",
            [],
        ),
    }


def _infer_diagnostic_status(summary: dict[str, Any]) -> str:
    hard_fail_count = int(summary.get("hard_fail_count") or 0)
    hard_missing_count = int(summary.get("hard_missing_count") or 0)
    fail_count = int(summary.get("fail_count") or 0)
    missing_count = int(summary.get("missing_count") or 0)
    warn_count = int(summary.get("warn_count") or 0)
    if hard_fail_count > 0 or hard_missing_count > 0:
        return "blocked"
    if fail_count > 0:
        return "risk_repair"
    if missing_count > 0:
        return "incomplete"
    if warn_count > 0:
        return "watch"
    return "clear"


def _risk_adjusted_readiness(value: Any, summary: dict[str, Any]) -> str:
    readiness = str(value or "").strip().lower()
    hard_fail_count = int(summary.get("hard_fail_count") or 0)
    hard_missing_count = int(summary.get("hard_missing_count") or 0)
    fail_count = int(summary.get("fail_count") or 0)
    if hard_fail_count > 0 or hard_missing_count > 0:
        return "blocked_by_validation"
    if fail_count > 0 and readiness in {"", "normal", "scale_ready"}:
        return "probe"
    return readiness


def lane_authority_reduction_reasons(action: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason and reason not in reasons:
            reasons.append(reason)

    grade = str(action.get("grade") or "").strip().lower()
    action_id = str(action.get("action") or "").strip().lower()
    authority_multiplier = _to_float(
        action.get("authority_multiplier")
        if action.get("authority_multiplier") is not None
        else action.get("max_budget_multiplier")
    )
    if bool(action.get("scale_blocked_by_validation_repair")):
        add("validation_repair")
    if bool(action.get("scale_blocked_by_cost_precision")):
        add("cost_precision")
    if bool(action.get("scale_blocked_by_cost_evidence")):
        add("cost_evidence")
    if bool(action.get("scale_blocked_by_verified_edge_samples")):
        add("verified_edge_samples")
    if bool(action.get("scale_blocked_by_entry_quality")):
        add("entry_quality")
    if bool(action.get("requires_waiting_entry")):
        add("waiting_entry")
    if grade == "weak":
        add("weak_performance")
    elif grade == "insufficient":
        add("sample_insufficient")
    if authority_multiplier > 0 and authority_multiplier < 1.0:
        add("reduced_authority")
    if "repair" in action_id:
        add("repair_before_scale")
    return reasons


def _compact_name_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_names(
        [str(row or "").strip() for row in value if str(row or "").strip()],
        limit=limit,
    )


def _dedupe_names(values: list[str], *, limit: int = 12) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= max(int(limit or 0), 0):
            break
    return out


def _lane_action_allows_probe(action: dict[str, Any]) -> bool:
    action_id = str(action.get("action") or "").strip().lower()
    grade = str(action.get("grade") or "").strip().lower()
    authority_multiplier = _to_float(
        action.get("authority_multiplier")
        if action.get("authority_multiplier") is not None
        else action.get("max_budget_multiplier")
    )
    hard_stop_tokens = (
        "blocked",
        "halt",
        "disabled",
        "research_only",
        "no_trade",
        "avoid",
    )
    if any(token in action_id for token in hard_stop_tokens):
        return False
    if bool(action.get("requires_waiting_entry")):
        return True
    if any(
        token in action_id
        for token in (
            "probe",
            "sample",
            "waiting_entry",
            "validation_repair_enforced_before_scale",
        )
    ):
        return True
    return authority_multiplier > 0 or grade in {"qualified", "insufficient", "weak"}


def _lane_execution_posture(
    *,
    allow_scale_up: bool,
    probe_lanes: list[str],
    scale_blocked_lanes: list[str],
    weak_lanes: list[str],
) -> str:
    if allow_scale_up and not scale_blocked_lanes:
        return "scale_allowed"
    if probe_lanes and scale_blocked_lanes:
        return "probe_allowed_scale_blocked"
    if probe_lanes:
        return "probe_allowed_sample_building"
    if scale_blocked_lanes or weak_lanes:
        return "review_required_no_scale"
    return "normal_selective"


def aggregate_trading_validation_lane_authority(
    venues: dict[str, Any],
    *,
    limit: int = 16,
) -> dict[str, Any]:
    reduced_lanes: list[dict[str, Any]] = []
    probe_lane_names: list[str] = []
    scale_blocked_lanes: list[str] = []
    scale_candidate_lanes: list[str] = []
    weak_lanes: list[str] = []
    validation_repair_weak_lanes: list[str] = []
    seen_status = False
    for venue, payload in venues.items():
        if not isinstance(payload, dict):
            continue
        summary = (
            payload.get("lane_authority_summary")
            if isinstance(payload.get("lane_authority_summary"), dict)
            else {}
        )
        if not summary:
            continue
        seen_status = True
        for row in list(summary.get("reduced_lanes") or []):
            if isinstance(row, dict):
                reduced_lanes.append({**row, "venue": str(row.get("venue") or venue)})
        for raw_lane in list(summary.get("probe_lane_names") or []):
            lane = str(raw_lane or "").strip()
            if lane:
                probe_lane_names.append(f"{venue}:{lane}")
        for raw_lane in list(summary.get("scale_blocked_lanes") or []):
            lane = str(raw_lane or "").strip()
            if lane:
                scale_blocked_lanes.append(f"{venue}:{lane}")
        for raw_lane in list(summary.get("scale_candidate_lanes") or []):
            lane = str(raw_lane or "").strip()
            if lane:
                scale_candidate_lanes.append(f"{venue}:{lane}")
        for raw_lane in list(summary.get("weak_lanes") or []):
            lane = str(raw_lane or "").strip()
            if lane:
                weak_lanes.append(f"{venue}:{lane}")
        for raw_lane in list(summary.get("validation_repair_weak_lanes") or []):
            lane = str(raw_lane or "").strip()
            if lane:
                validation_repair_weak_lanes.append(f"{venue}:{lane}")
    reduced_lanes = sorted(
        reduced_lanes,
        key=lambda row: (
            _to_float(row.get("authority_multiplier") or 0),
            str(row.get("venue") or ""),
            str(row.get("lane") or ""),
        ),
    )
    return {
        "version": "lane_authority_summary_v1",
        "status": "warn" if reduced_lanes else "pass" if seen_status else "missing",
        "venue": "aggregate",
        "execution_posture": _lane_execution_posture(
            allow_scale_up=bool(scale_candidate_lanes) and not reduced_lanes,
            probe_lanes=probe_lane_names,
            scale_blocked_lanes=scale_blocked_lanes,
            weak_lanes=weak_lanes,
        ),
        "probe_policy": (
            "small waiting-entry/probe blocks remain allowed; scale-up waits for lane evidence and safety gates"
            if probe_lane_names and reduced_lanes
            else ""
        ),
        "probe_lane_count": len(probe_lane_names),
        "probe_lane_names": probe_lane_names[:limit],
        "scale_blocked_lane_count": len(scale_blocked_lanes),
        "scale_blocked_lanes": scale_blocked_lanes[:limit],
        "reduced_lane_count": len(reduced_lanes),
        "reduced_lanes": reduced_lanes[:limit],
        "scale_candidate_lanes": scale_candidate_lanes[:limit],
        "weak_lanes": weak_lanes[:limit],
        "validation_repair_weak_lanes": validation_repair_weak_lanes[:limit],
    }


def aggregate_trading_validation_venue_payloads(
    venues: dict[str, Any],
    *,
    db_path: str,
    live_performance_db_path: str,
    max_age_sec: int,
    expected_discipline_count: int,
    empty_payload: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [
        row
        for row in venues.values()
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower() != "empty"
    ]
    if not rows:
        return empty_payload() if empty_payload is not None else {}

    summary_totals = {
        "pass_count": 0,
        "warn_count": 0,
        "fail_count": 0,
        "missing_count": 0,
        "hard_fail_count": 0,
        "hard_missing_count": 0,
        "core_fail_count": 0,
        "core_missing_count": 0,
    }
    combined_disciplines: list[dict[str, Any]] = []
    computed_at_values: list[str] = []
    readiness_values: list[str] = []
    stale = False
    status = "ok"
    for row in rows:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        disciplines = (
            payload.get("disciplines")
            if isinstance(payload.get("disciplines"), list)
            else row.get("disciplines")
            if isinstance(row.get("disciplines"), list)
            else []
        )
        for item in disciplines:
            if isinstance(item, dict):
                combined_disciplines.append(item)
        for key in ("pass_count", "warn_count", "fail_count", "missing_count"):
            summary_totals[key] += int(summary.get(key) or 0)
        if (
            "hard_fail_count" in summary
            or "core_fail_count" in summary
            or "core_missing_count" in summary
        ):
            hard_fail_count = int(
                summary.get("hard_fail_count")
                if summary.get("hard_fail_count") is not None
                else int(summary.get("core_fail_count") or 0)
                + int(summary.get("core_missing_count") or 0)
            )
        else:
            hard_fail_count = int(summary.get("fail_count") or 0)
        summary_totals["hard_fail_count"] += hard_fail_count
        summary_totals["hard_missing_count"] += int(summary.get("hard_missing_count") or 0)
        summary_totals["core_fail_count"] += int(summary.get("core_fail_count") or 0)
        summary_totals["core_missing_count"] += int(summary.get("core_missing_count") or 0)
        readiness = str(summary.get("readiness") or "").strip().lower()
        if readiness:
            readiness_values.append(readiness)
        if str(row.get("computed_at") or ""):
            computed_at_values.append(str(row.get("computed_at")))
        stale = stale or bool(row.get("stale"))
        if str(row.get("status") or "").strip().lower() == "error":
            status = "error"

    venue_count = max(len(rows), 1)
    discipline_count = sum(
        int(
            (
                row.get("payload")
                if isinstance(row.get("payload"), dict)
                else {}
            ).get("discipline_count")
            or row.get("discipline_count")
            or expected_discipline_count
        )
        for row in rows
    )
    total = max(discipline_count, 1)
    total_score = (
        summary_totals["pass_count"] + summary_totals["warn_count"] * 0.5
    ) / total * 100.0
    if summary_totals["hard_fail_count"] > 0:
        readiness = "blocked_by_validation"
    elif summary_totals["fail_count"] > 0:
        readiness = "probe"
    elif readiness_values and all(value == "scale_ready" for value in readiness_values):
        readiness = "scale_ready"
    elif (
        summary_totals["pass_count"] >= 10 * venue_count
        and summary_totals["missing_count"] <= 4 * venue_count
    ):
        readiness = "scale_ready"
    elif summary_totals["pass_count"] >= 6 * venue_count:
        readiness = "normal"
    elif summary_totals["pass_count"] >= 3 * venue_count:
        readiness = "probe"
    else:
        readiness = "research_only"
    if summary_totals["hard_fail_count"] > 0 or summary_totals["hard_missing_count"] > 0:
        diagnostic_status = "blocked"
    elif summary_totals["fail_count"] > 0:
        diagnostic_status = "risk_repair"
    elif summary_totals["missing_count"] > 0:
        diagnostic_status = "incomplete"
    elif summary_totals["warn_count"] > 0:
        diagnostic_status = "watch"
    else:
        diagnostic_status = "clear"
    summary = {
        "total_score": round(total_score, 2),
        "readiness": readiness,
        "diagnostic_status": diagnostic_status,
        **summary_totals,
    }
    computed_at = max(computed_at_values) if computed_at_values else ""
    return annotate_trading_validation_freshness(
        {
            "status": status,
            "db_path": db_path,
            "run_id": "validation-venue-aggregate",
            "venue": "aggregate",
            "computed_at": computed_at,
            "summary": summary,
            "payload": {
                "summary": summary,
                "discipline_count": discipline_count,
                "disciplines": combined_disciplines,
                "venue": "aggregate",
                "computed_at": computed_at,
            },
            "readiness": readiness,
            "diagnostic_status": diagnostic_status,
            "score": summary["total_score"],
            "discipline_count": discipline_count,
            "expected_discipline_count": expected_discipline_count * venue_count,
            "disciplines": combined_disciplines,
            "metrics": {},
            "monte_carlo": {},
            "operator_guidance": [],
            "remediation_plan": {},
            "stale": stale,
            "config": {
                "db_path": db_path,
                "live_performance_db_path": live_performance_db_path,
                "max_age_sec": int(max_age_sec),
            },
        },
        max_age_sec=max_age_sec,
    )


def summarize_trading_validation_bottlenecks(
    venues: dict[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    severity = {"fail": 0, "missing": 1, "warn": 2}
    discipline_priority = {
        "data_validation": 0,
        "cost_simulation": 1,
        "capacity_analysis": 2,
        "monte_carlo": 3,
        "kelly_sizing": 4,
        "risk_of_ruin": 5,
        "mdd_limit": 6,
        "profit_factor": 7,
        "recovery_factor": 8,
        "sortino_ratio": 9,
        "sharpe_ratio": 10,
        "calmar_ratio": 11,
        "stress_test": 12,
        "walk_forward_analysis": 13,
        "out_of_sample_test": 14,
        "overfit_validation": 15,
        "regime_test": 16,
        "correlation": 17,
        "factor_exposure": 18,
    }
    items: list[dict[str, Any]] = []
    for venue, payload in venues.items():
        if not isinstance(payload, dict):
            continue
        disciplines = (
            payload.get("disciplines")
            if isinstance(payload.get("disciplines"), list)
            else []
        )
        for row in disciplines:
            if (
                not isinstance(row, dict)
                or str(row.get("status") or "").strip().lower() == "pass"
            ):
                continue
            items.append(
                {
                    "venue": str(venue or ""),
                    "id": str(row.get("id") or ""),
                    "label": str(row.get("label") or row.get("id") or ""),
                    "status": str(row.get("status") or ""),
                    "evidence": str(row.get("evidence") or ""),
                    "action": str(row.get("action") or ""),
                }
            )
    ranked = sorted(
        items,
        key=lambda row: (
            severity.get(str(row.get("status") or "").strip().lower(), 9),
            discipline_priority.get(str(row.get("id") or ""), 99),
            str(row.get("venue") or ""),
            str(row.get("id") or ""),
        ),
    )
    return ranked[: max(int(limit or 0), 0)]


def summarize_trading_validation_next_actions(
    venues: dict[str, Any],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for venue, payload in venues.items():
        if not isinstance(payload, dict):
            continue
        plan = (
            payload.get("remediation_plan")
            if isinstance(payload.get("remediation_plan"), dict)
            else {}
        )
        action = str(plan.get("primary_next_action") or "").strip()
        if not action:
            continue
        actions.append(
            {
                "venue": str(venue or ""),
                "status": str(plan.get("status") or ""),
                "action": action,
            }
        )
    return actions


def _iso_age_sec(value: Any, *, now: datetime | None = None) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max((current - dt.astimezone(timezone.utc)).total_seconds(), 0.0)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
