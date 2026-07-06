from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends

from tradecraft.services.live_authority import compact_live_authority_for_status


TRADING_VALIDATION_SUMMARY_KEYS = (
    "total_score",
    "readiness",
    "diagnostic_status",
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
)


@dataclass(frozen=True)
class TradingRouteDeps:
    require_admin_auth: Callable[..., Any]
    live_authority_payload: Callable[[], dict[str, Any]]
    trading_validation_status_payload: Callable[[str], dict[str, Any]]
    trading_validation_service: Callable[[str], Any]
    sync_live_performance_and_edges: Callable[[], dict[str, Any]]


def build_trading_router(deps: TradingRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/live/authority")
    async def live_authority(
        compact: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = await asyncio.to_thread(deps.live_authority_payload)
        if not compact:
            return payload
        return _compact_live_authority_response(payload)

    @router.get("/api/trading/validation/status")
    async def trading_validation_status(
        venue: str = "",
        compact: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = await asyncio.to_thread(
            deps.trading_validation_status_payload,
            venue,
        )
        if not compact:
            return payload
        return _compact_trading_validation_response(payload)

    @router.get("/api/trading/validation")
    async def trading_validation_status_alias(
        venue: str = "",
        compact: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = await asyncio.to_thread(
            deps.trading_validation_status_payload,
            venue,
        )
        if not compact:
            return payload
        return _compact_trading_validation_response(payload)

    @router.post("/api/trading/validation/run-once")
    async def trading_validation_run_once(
        venue: str = "",
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        clean_venue = venue.strip().lower()
        service = deps.trading_validation_service(clean_venue)
        sync_result = await asyncio.to_thread(deps.sync_live_performance_and_edges)
        payload = await asyncio.to_thread(service.run_once, venue=clean_venue)
        payload["sync"] = sync_result
        return payload

    return router


def _compact_trading_validation_response(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "error", "compact": True, "reason": "invalid_payload"}
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    source = nested or payload
    summary = (
        payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else source.get("summary")
        if isinstance(source.get("summary"), dict)
        else {}
    )
    disciplines = (
        source.get("disciplines") if isinstance(source.get("disciplines"), list) else []
    )
    compact_disciplines = _compact_rows(disciplines, limit=100)
    metrics = (
        source.get("metrics")
        if isinstance(source.get("metrics"), dict)
        else payload.get("metrics")
        if isinstance(payload.get("metrics"), dict)
        else {}
    )
    compact_metrics = {
        key: metrics[key]
        for key in (
            "sample_count",
            "trade_count",
            "closed_block_count",
            "net_pnl",
            "net_pnl_krw",
            "net_pnl_usdt",
        )
        if key in metrics
    }
    remediation_plan = (
        source.get("remediation_plan")
        if isinstance(source.get("remediation_plan"), dict)
        else payload.get("remediation_plan")
        if isinstance(payload.get("remediation_plan"), dict)
        else {}
    )
    compact_remediation = {
        key: remediation_plan[key]
        for key in (
            "status",
            "trade_blocking",
            "blocking_scope",
            "primary_next_action",
            "weak_count",
            "failed_count",
            "missing_count",
        )
        if key in remediation_plan
    }
    lane_policy_hints = remediation_plan.get("lane_policy_hints")
    if isinstance(lane_policy_hints, dict):
        compact_hints = {
            key: lane_policy_hints[key]
            for key in (
                "version",
                "trade_blocking",
                "blocking_scope",
                "entry_mode",
                "risk_budget_mode",
                "requires_shadow_or_waiting_entry",
                "scale_up_allowed",
            )
            if key in lane_policy_hints
        }
        if compact_hints:
            compact_remediation["lane_policy_hints"] = compact_hints
    if isinstance(remediation_plan.get("work_queue"), list):
        compact_remediation["work_queue"] = _compact_rows(
            remediation_plan.get("work_queue"),
            limit=6,
        )
    venues = payload.get("venues")
    has_venue_breakdown = isinstance(venues, dict) and bool(venues)
    failed_ids = _discipline_ids(disciplines, "fail")
    warned_ids = _discipline_ids(disciplines, "warn")
    compact_summary = {
        key: summary[key] for key in TRADING_VALIDATION_SUMMARY_KEYS if key in summary
    }
    discipline_count = (
        payload.get("discipline_count")
        or source.get("discipline_count")
        or len(disciplines)
    )
    expected_discipline_count = payload.get("expected_discipline_count") or source.get(
        "expected_discipline_count"
    )
    compat_payload = {
        "summary": compact_summary,
        "discipline_count": discipline_count,
        "expected_discipline_count": expected_discipline_count,
    }
    if compact_metrics:
        compat_payload["metrics"] = compact_metrics
    top_bottlenecks = _compact_rows(
        source.get("top_bottlenecks") or payload.get("top_bottlenecks"),
        limit=6,
    )
    bottlenecks = _compact_rows(
        source.get("bottlenecks") or payload.get("bottlenecks"),
        limit=6,
    )
    compact: dict[str, Any] = {
        "status": str(payload.get("status") or "ok"),
        "compact": True,
        "venue": str(payload.get("venue") or source.get("venue") or ""),
        "run_id": payload.get("run_id") or source.get("run_id"),
        "computed_at": payload.get("computed_at") or source.get("computed_at"),
        "readiness": payload.get("readiness") or summary.get("readiness"),
        "diagnostic_status": payload.get("diagnostic_status")
        or summary.get("diagnostic_status"),
        "score": payload.get("score") or summary.get("total_score"),
        "summary": compact_summary,
        "discipline_count": discipline_count,
        "expected_discipline_count": expected_discipline_count,
        "metrics": compact_metrics,
        "sample_count": payload.get("sample_count") or compact_metrics.get("sample_count"),
        "remediation_plan": compact_remediation,
        "operator_guidance": [
            str(row)
            for row in (
                source.get("operator_guidance")
                if isinstance(source.get("operator_guidance"), list)
                else payload.get("operator_guidance")
                if isinstance(payload.get("operator_guidance"), list)
                else []
            )
            if str(row).strip()
        ][:6],
        "failed_discipline_ids": payload.get("failed_discipline_ids")
        if isinstance(payload.get("failed_discipline_ids"), list)
        else failed_ids,
        "warned_discipline_ids": payload.get("warned_discipline_ids")
        if isinstance(payload.get("warned_discipline_ids"), list)
        else warned_ids,
        "failure_drivers": _compact_rows(payload.get("failure_drivers"), limit=4),
        "cost_drivers": _compact_rows(payload.get("cost_drivers"), limit=4),
        "recovery_focus": [
            str(row)
            for row in (
                payload.get("recovery_focus")
                if isinstance(payload.get("recovery_focus"), list)
                else []
            )
            if str(row).strip()
        ][:4],
        "primary_next_actions": _compact_next_actions(
            source.get("primary_next_actions")
            if isinstance(source.get("primary_next_actions"), list)
            else payload.get("primary_next_actions")
            if isinstance(payload.get("primary_next_actions"), list)
            else [],
            limit=6,
        ),
    }
    if compact_disciplines and not has_venue_breakdown:
        compact["disciplines"] = compact_disciplines
    if top_bottlenecks:
        compact["top_bottlenecks"] = top_bottlenecks
    if bottlenecks:
        compact["bottlenecks"] = bottlenecks
    if (
        nested
        or compact_summary
        or discipline_count
        or expected_discipline_count
        or compact_metrics
    ):
        compact["payload"] = compat_payload
    for key, value in compact_summary.items():
        if key not in {"total_score", "readiness", "diagnostic_status"}:
            compact[key] = value
    lane_authority = payload.get("lane_authority_summary") or source.get(
        "lane_authority_summary"
    )
    if isinstance(lane_authority, dict):
        compact["lane_authority_summary"] = {
            key: lane_authority[key]
            for key in (
                "version",
                "status",
                "venue",
                "execution_posture",
                "probe_policy",
                "probe_lane_count",
                "probe_lane_names",
                "scale_blocked_lane_count",
                "scale_blocked_lanes",
                "reduced_lane_count",
                "scale_candidate_lanes",
                "weak_lanes",
                "validation_repair_weak_lanes",
            )
            if key in lane_authority
        }
        compact["lane_authority_summary"]["reduced_lanes"] = _compact_rows(
            lane_authority.get("reduced_lanes"),
            limit=6,
        )
    if isinstance(venues, dict):
        compact["venues"] = {
            str(venue): _compact_trading_validation_venue_for_aggregate(venue_payload)
            for venue, venue_payload in venues.items()
            if isinstance(venue_payload, dict)
        }
    return {key: value for key, value in compact.items() if value not in (None, [], {})}


def _discipline_ids(rows: list[Any], status: str) -> list[str]:
    return [
        str(row.get("id") or "")
        for row in rows
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower() == status
        and str(row.get("id") or "").strip()
    ]


def _compact_trading_validation_venue_for_aggregate(
    venue_payload: dict[str, Any],
) -> dict[str, Any]:
    compact = _compact_trading_validation_response(venue_payload)
    disciplines = compact.get("disciplines")
    if isinstance(disciplines, list):
        issue_rows = [
            row
            for row in disciplines
            if isinstance(row, dict)
            and str(row.get("status") or "").strip().lower() != "pass"
        ]
        compact["disciplines"] = _compact_rows(
            issue_rows or disciplines,
            limit=6,
            text_limit=160,
        )
    for key, limit in (
        ("top_bottlenecks", 4),
        ("failure_drivers", 3),
        ("cost_drivers", 3),
    ):
        rows = compact.get(key)
        if isinstance(rows, list):
            compact[key] = _compact_rows(rows, limit=limit, text_limit=180)
    actions = compact.get("primary_next_actions")
    if isinstance(actions, list):
        compact["primary_next_actions"] = _compact_next_actions(actions, limit=3)
    guidance = compact.get("operator_guidance")
    if isinstance(guidance, list):
        compact["operator_guidance"] = [
            _compact_text(item, limit=160)
            for item in guidance[:3]
            if str(item or "").strip()
        ]
    recovery = compact.get("recovery_focus")
    if isinstance(recovery, list):
        compact["recovery_focus"] = [
            _compact_text(item, limit=120)
            for item in recovery[:3]
            if str(item or "").strip()
        ]
    return compact


def _compact_rows(rows: Any, *, limit: int, text_limit: int = 500) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {
            key: _compact_row_value(row.get(key))
            for key in (
                "id",
                "label",
                "status",
                "evidence",
                "action",
                "venue",
                "lane",
                "reason",
                "group_type",
                "group",
                "sample_count",
                "total_net_pnl",
                "total_cost",
                "expectancy_pct",
                "win_rate_pct",
                "profit_factor",
                "cost_drag_pct_of_gross_pnl",
                "cost_drag_pct_of_abs_gross_pnl",
                "net_negative_after_cost",
                "risk_score",
                "authority_multiplier",
            )
            if key in row
        }
        item = {
            key: (_compact_text(value, limit=text_limit) if isinstance(value, str) else value)
            for key, value in item.items()
        }
        if item:
            compact.append(item)
        if len(compact) >= max(int(limit), 0):
            break
    return compact


def _compact_row_value(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_text(value, limit=500)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [
            _compact_row_value(item)
            for item in value[:8]
            if _compact_row_value(item) not in ("", [], {}, None)
        ]
    if isinstance(value, dict):
        return {
            str(key): _compact_row_value(item)
            for key, item in list(value.items())[:8]
            if str(key).strip()
            and _compact_row_value(item) not in ("", [], {}, None)
        }
    return _compact_text(value, limit=500)


def _compact_text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[: max(int(limit), 0)]


def _compact_next_actions(rows: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = {
                key: _compact_text(row.get(key))
                for key in ("venue", "status", "action", "reason")
                if _compact_text(row.get(key))
            }
        else:
            action = _compact_text(row)
            item = {"action": action} if action else {}
        if item:
            compact.append(item)
        if len(compact) >= max(int(limit), 0):
            break
    return compact


def _compact_live_authority_response(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "error", "compact": True, "reason": "invalid_payload"}
    venues = payload.get("venues") if isinstance(payload.get("venues"), dict) else {}
    compact_venues = {
        str(venue): compact_live_authority_for_status(venue_payload)
        for venue, venue_payload in venues.items()
        if isinstance(venue_payload, dict)
    }
    edge = payload.get("edge") if isinstance(payload.get("edge"), dict) else {}
    performance = (
        payload.get("performance")
        if isinstance(payload.get("performance"), dict)
        else {}
    )
    return {
        "status": str(payload.get("status") or "ok"),
        "compact": True,
        "edge": {
            key: edge[key]
            for key in ("status", "scorecard_count", "db_path")
            if key in edge
        },
        "performance": {
            key: performance[key]
            for key in ("status", "strategy_revision_id", "db_path")
            if key in performance
        },
        "venues": compact_venues,
    }
