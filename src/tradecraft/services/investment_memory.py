from __future__ import annotations

import copy
import json
import re
import sqlite3
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from tradecraft.services.jue_language_policy import jue_language_policy
from tradecraft.services.jue_lifecycle import JueLifecycleRepository
from tradecraft.services.jue_skill_registry import JueSkillRegistry, JueSkillValidationError
from tradecraft.services.krx_holiday import KRXHolidayCalendar
from tradecraft.services.codex_native import CodexNativeRuntime
from tradecraft.services.trading_validation import DISCIPLINE_DEFINITIONS

KST = ZoneInfo("Asia/Seoul")
VALID_RITUAL_SLOTS = {
    "seed",
    "pre_open",
    "midday",
    "post_close",
    "block_reflection",
    "weekly",
}
SLOT_LABELS = {
    "seed": "초기 메모리 시드",
    "pre_open": "장전 마음가짐",
    "midday": "장중 점검",
    "post_close": "마감 리뷰",
    "block_reflection": "블록 거래 반성",
    "weekly": "주간 압축",
    "weekly_review": "주간 운용 반성",
    "weekly_replay": "주간 의사결정 리플레이",
    "monthly_review": "월간 운용 반성",
}
SOFT_POLICY_STRENGTHS = {"soft", "observation", "preference", "caution", "watch"}
MEMORY_SCOPES = {"core", "kis", "binance"}
MEMORY_TRANSFERABILITY = {"direct", "translated", "blocked"}
CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "BTC", "ETH")
RITUAL_WORKFLOW_BY_SLOT = {
    "pre_open": "kis_pre_open",
    "midday": "kis_intraday_manager",
    "post_close": "kis_post_close",
    "block_reflection": "block_reflection",
    "weekly": "policy_revision",
    "weekly_review": "policy_revision",
    "weekly_replay": "policy_revision",
    "monthly_review": "policy_revision",
}
MEMORY_RUN_INPUT_STORAGE_LIMIT = 60_000
MEMORY_RUN_OUTPUT_STORAGE_LIMIT = 80_000
LANE_WEAK_SOURCE_SCALE_BLOCKERS = {
    "cost_weak_lanes": "cost_evidence_repair",
    "cost_evidence_weak_lanes": "cost_evidence_repair",
    "entry_quality_weak_lanes": "entry_quality_repair",
    "validation_evidence_weak_lanes": "validation_evidence_repair",
    "validation_repair_weak_lanes": "validation_repair_enforced",
}
_COST_COMPONENT_ALIASES = {
    "fee": "fees",
    "fees": "fees",
    "commission": "fees",
    "commissions": "fees",
    "tax": "taxes",
    "taxes": "taxes",
    "funding": "funding",
    "funding_fee": "funding",
    "slippage": "slippage",
    "spread": "spread",
    "spread_cost": "spread",
    "book_spread": "spread",
}
_CANONICAL_COST_COMPONENTS = {
    "fees",
    "taxes",
    "funding",
    "slippage",
    "spread",
}
_COST_COMPONENT_DECLARATION_KEYS = (
    "recorded_cost_components",
    "verified_cost_components",
    "present_cost_components",
    "cost_components_present",
    "zero_cost_components",
    "explicit_zero_cost_components",
)


class TelegramSender(Protocol):
    async def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default
    return parsed


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def _compact_validation_pass_path(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_dict(raw: Any, *, limit: int = 12) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        compact: dict[str, Any] = {}
        for key, item in raw.items():
            clean_key = _clean_text(key, limit=80)
            if not clean_key:
                continue
            if isinstance(item, str):
                compact[clean_key] = _clean_text(item, limit=180)
            elif isinstance(item, (int, float, bool)):
                compact[clean_key] = item
            elif isinstance(item, (list, tuple, set)):
                compact[clean_key] = [
                    _clean_text(child, limit=120)
                    for child in list(item)[:8]
                    if _clean_text(child, limit=120)
                ]
            elif isinstance(item, dict):
                compact[clean_key] = compact_dict(item, limit=8)
            if len(compact) >= max(int(limit), 1):
                break
        return {
            key: item
            for key, item in compact.items()
            if item not in ("", [], {}, None)
        }

    behavior = compact_dict(value.get("jue_behavior_until_pass"), limit=8)
    runtime = compact_dict(value.get("m1_runtime_profile"), limit=8)
    required_evidence = compact_dict(value.get("required_evidence"), limit=12)
    payload = {
        "version": _clean_text(value.get("version"), limit=80),
        "current_gap": _clean_text(value.get("current_gap"), limit=80),
        "collection_hook": _clean_text(value.get("collection_hook"), limit=120),
        "collection_cadence": _clean_text(
            value.get("collection_cadence"),
            limit=80,
        ),
        "pass_criteria": _clean_text(value.get("pass_criteria"), limit=260),
        "required_evidence": required_evidence,
        "jue_behavior_until_pass": behavior,
        "m1_runtime_profile": runtime,
    }
    return {
        key: item
        for key, item in payload.items()
        if item not in ("", [], {}, None)
    }


def _compact_trading_validation_event_payload(
    payload: dict[str, Any],
    *,
    source_payload_chars: int,
) -> dict[str, Any]:
    if str(payload.get("compaction_version") or "") == "memory_event_validation_v2":
        return payload
    remediation = (
        payload.get("remediation_plan")
        if isinstance(payload.get("remediation_plan"), dict)
        else {}
    )
    active_revision_evidence = (
        remediation.get("active_revision_evidence")
        if isinstance(remediation.get("active_revision_evidence"), dict)
        else {}
    )
    compact_active_evidence = {
        key: active_revision_evidence.get(key)
        for key in (
            "active_sample_count",
            "all_revision_sample_count",
            "authority_posture",
            "block_design_requirement",
            "can_scale_up",
            "can_trade_live",
            "live_authority",
            "readiness",
            "risk_governor_action",
        )
        if active_revision_evidence.get(key) not in (None, "", [], {})
    }
    for key in (
        "active_revision_sample_building_failed_discipline_ids",
        "failed_discipline_ids",
        "weak_discipline_ids",
    ):
        raw_values = active_revision_evidence.get(key)
        if isinstance(raw_values, list):
            values = [_clean_text(row, limit=80) for row in raw_values[:12]]
            values = [row for row in values if row]
            if values:
                compact_active_evidence[key] = values

    raw_work_queue = (
        remediation.get("work_queue")
        if isinstance(remediation.get("work_queue"), list)
        else []
    )
    work_queue: list[dict[str, Any]] = []
    for item in raw_work_queue[:8]:
        if not isinstance(item, dict):
            continue
        compact_item = {
            key: item.get(key)
            for key in (
                "repair_action_id",
                "discipline_id",
                "status",
                "priority",
                "lane_policy_hint",
                "blocks_scaling",
                "blocks_new_entries",
                "allowed_entry_posture",
                "scale_up_blocked",
            )
            if item.get(key) not in (None, "", [], {})
        }
        pass_path = _compact_validation_pass_path(item.get("pass_path"))
        if pass_path:
            compact_item["pass_path"] = pass_path
        if compact_item:
            work_queue.append(compact_item)

    weak_disciplines = [
        {
            key: row.get(key)
            for key in ("id", "label", "status", "action")
            if isinstance(row, dict) and row.get(key) not in (None, "")
        }
        for row in list(payload.get("weak_disciplines") or [])[:10]
        if isinstance(row, dict)
    ]
    compact_remediation = {
        key: remediation.get(key)
        for key in (
            "status",
            "primary_next_action",
            "weak_count",
            "failed_count",
            "missing_count",
        )
        if remediation.get(key) not in (None, "", [], {})
    }
    if compact_active_evidence:
        compact_remediation["active_revision_evidence"] = compact_active_evidence
    if work_queue:
        compact_remediation["work_queue"] = work_queue
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    compact_summary = {
        key: summary.get(key)
        for key in (
            "readiness",
            "total_score",
            "pass_count",
            "warn_count",
            "fail_count",
            "missing_count",
            "hard_blocking_count",
            "active_revision_sample_count",
            "min_samples_to_scale",
            "scale_up_allowed",
        )
        if summary.get(key) not in (None, "", [], {})
    }

    return {
        "compaction_version": "memory_event_validation_v2",
        "source_payload_chars": int(source_payload_chars),
        "venue": _clean_text(payload.get("venue"), limit=40),
        "run_id": _clean_text(payload.get("run_id"), limit=120),
        "computed_at": _clean_text(payload.get("computed_at"), limit=80),
        "summary": compact_summary,
        "weak_disciplines": weak_disciplines,
        "remediation_plan": compact_remediation,
    }


def _complete_validation_disciplines_for_memory(
    disciplines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [row for row in disciplines if isinstance(row, dict)]
    present_ids = {
        _clean_text(row.get("id"), limit=80)
        for row in rows
        if _clean_text(row.get("id"), limit=80)
    }
    if not present_ids or len(rows) >= len(DISCIPLINE_DEFINITIONS):
        return rows
    completed = list(rows)
    for definition in DISCIPLINE_DEFINITIONS:
        discipline_id = _clean_text(definition.get("id"), limit=80)
        if not discipline_id or discipline_id in present_ids:
            continue
        completed.append(
            {
                **definition,
                "status": "missing",
                "evidence": "검증 row가 payload에 없습니다.",
                "action": (
                    f"{definition.get('label') or discipline_id} 검증 결과를 "
                    "생성하고 validation payload에 포함해야 합니다."
                ),
                "metric": {
                    "status": "missing",
                    "reason": "absent_discipline_row",
                },
                "memory_generated_missing_row": True,
            }
        )
    return completed


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


def _clean_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _cost_component_label(value: Any) -> str:
    key = _clean_key(value)
    component = _COST_COMPONENT_ALIASES.get(key, key)
    return component if component in _CANONICAL_COST_COMPONENTS else ""


def _is_absent_cost_component_marker(value: Any) -> bool:
    return value is None or value == "" or value is False


def _declared_cost_components(value: Any) -> set[str]:
    raw_value = value
    if isinstance(value, str):
        parsed = _json_loads(value, None)
        raw_value = parsed if parsed is not None else value
    present: set[str] = set()
    if isinstance(raw_value, dict):
        for raw_key, raw_marker in raw_value.items():
            if _is_absent_cost_component_marker(raw_marker):
                continue
            if component := _cost_component_label(raw_key):
                present.add(component)
        return present
    if isinstance(raw_value, (list, tuple, set)):
        for item in raw_value:
            if isinstance(item, dict):
                marker = item.get(
                    "present",
                    item.get("recorded", item.get("verified", True)),
                )
                if _is_absent_cost_component_marker(marker):
                    continue
                label = (
                    item.get("component")
                    or item.get("name")
                    or item.get("key")
                    or item.get("id")
                )
                if component := _cost_component_label(label):
                    present.add(component)
                continue
            if component := _cost_component_label(item):
                present.add(component)
        return present
    if isinstance(raw_value, str):
        normalized = raw_value.replace("\n", ",").replace(";", ",").replace("|", ",")
        pieces = normalized.split(",") if "," in normalized else normalized.split()
        for piece in pieces:
            if component := _cost_component_label(piece):
                present.add(component)
    return present


def _compact_live_authority_for_reflection(block: dict[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    live_authority = (
        metadata.get("live_authority")
        if isinstance(metadata.get("live_authority"), dict)
        else block.get("live_authority")
        if isinstance(block.get("live_authority"), dict)
        else {}
    )
    if not live_authority:
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "live_grade",
        "allow_scale_up",
        "max_budget_multiplier",
        "validation_gate_status",
        "validation_readiness",
        "validation_gate_reason",
    ):
        if live_authority.get(key) not in (None, ""):
            compact[key] = live_authority.get(key)
    validation_passport = (
        live_authority.get("validation_passport")
        if isinstance(live_authority.get("validation_passport"), dict)
        else {}
    )
    if validation_passport:
        passport: dict[str, Any] = {
            key: validation_passport.get(key)
            for key in (
                "version",
                "status",
                "readiness",
                "score",
                "expected_count",
                "actual_count",
                "is_complete",
                "pass_count",
                "warn_count",
                "fail_count",
                "missing_count",
                "requires_revalidation",
                "risk_governor_action",
                "risk_governor_source",
            )
            if validation_passport.get(key) not in (None, "")
        }
        for key in ("failed_ids", "weak_ids"):
            values = (
                validation_passport.get(key)
                if isinstance(validation_passport.get(key), list)
                else []
            )
            if values:
                passport[key] = [
                    _clean_text(value, limit=80)
                    for value in values[:10]
                    if _clean_text(value, limit=80)
                ]
        if passport:
            compact["validation_passport"] = passport
    validation_pressure = (
        live_authority.get("validation_pressure")
        if isinstance(live_authority.get("validation_pressure"), dict)
        else {}
    )
    if validation_pressure:
        compact_pressure: dict[str, Any] = {
            key: validation_pressure.get(key)
            for key in (
                "version",
                "severity",
                "entry_posture",
                "sizing_posture",
                "risk_governor_action",
                "scale_up_allowed",
                "remediation_entry_mode",
                "remediation_risk_budget_mode",
            )
            if validation_pressure.get(key) not in (None, "", [], {})
        }
        for key in ("fail_ids", "warn_ids", "missing_ids", "block_design_requirements"):
            values = (
                validation_pressure.get(key)
                if isinstance(validation_pressure.get(key), list)
                else []
            )
            if values:
                compact_pressure[key] = [
                    _clean_text(value, limit=100)
                    for value in values[:10]
                    if _clean_text(value, limit=100)
                ]
        actions = (
            validation_pressure.get("discipline_actions")
            if isinstance(validation_pressure.get("discipline_actions"), list)
            else []
        )
        compact_actions: list[dict[str, Any]] = []
        for row in actions[:19]:
            if not isinstance(row, dict):
                continue
            compact_actions.append(
                {
                    key: row.get(key)
                    for key in (
                        "id",
                        "status",
                        "entry_constraint",
                        "sizing_constraint",
                        "repair_action",
                        "block_design_focus",
                    )
                    if row.get(key) not in (None, "", [], {})
                }
            )
        if compact_actions:
            compact_pressure["discipline_actions"] = compact_actions
        if compact_pressure:
            compact["validation_pressure"] = compact_pressure
    discipline_matrix = (
        live_authority.get("discipline_matrix")
        if isinstance(live_authority.get("discipline_matrix"), dict)
        else {}
    )
    if discipline_matrix:
        summary = (
            discipline_matrix.get("summary")
            if isinstance(discipline_matrix.get("summary"), dict)
            else {}
        )
        statuses = (
            discipline_matrix.get("statuses")
            if isinstance(discipline_matrix.get("statuses"), list)
            else []
        )
        compact_statuses = [
            {
                key: row.get(key)
                for key in ("id", "label", "status", "action")
                if isinstance(row, dict) and row.get(key) not in (None, "")
            }
            for row in statuses[:19]
            if isinstance(row, dict)
        ]
        compact["discipline_matrix"] = {
            key: discipline_matrix.get(key)
            for key in ("expected_count", "actual_count")
            if discipline_matrix.get(key) not in (None, "")
        }
        if summary:
            compact["discipline_matrix"]["summary"] = {
                key: summary.get(key)
                for key in (
                    "score",
                    "readiness",
                    "pass_count",
                    "warn_count",
                    "fail_count",
                    "missing_count",
                )
                if summary.get(key) not in (None, "")
            }
        if compact_statuses:
            compact["discipline_matrix"]["statuses"] = compact_statuses
    failed = (
        live_authority.get("failed_disciplines")
        if isinstance(live_authority.get("failed_disciplines"), list)
        else []
    )
    if failed:
        compact["failed_disciplines"] = [
            {
                key: row.get(key)
                for key in ("id", "label", "status", "action")
                if isinstance(row, dict) and row.get(key) not in (None, "")
            }
            for row in failed[:6]
            if isinstance(row, dict)
        ]
    weak = (
        live_authority.get("weak_disciplines")
        if isinstance(live_authority.get("weak_disciplines"), list)
        else []
    )
    if weak:
        compact["weak_disciplines"] = [
            {
                key: row.get(key)
                for key in ("id", "label", "status", "action")
                if isinstance(row, dict) and row.get(key) not in (None, "")
            }
            for row in weak[:6]
            if isinstance(row, dict)
        ]
    capacity = (
        live_authority.get("capacity_bottleneck")
        if isinstance(live_authority.get("capacity_bottleneck"), dict)
        else {}
    )
    if capacity:
        compact["capacity_bottleneck"] = {
            key: capacity.get(key)
            for key in (
                "status",
                "capacity_method",
                "min_capacity_ratio",
                "tightest_symbol",
                "tightest_block_id",
            )
            if capacity.get(key) not in (None, "")
        }
    attribution = (
        live_authority.get("failure_attribution")
        if isinstance(live_authority.get("failure_attribution"), dict)
        else {}
    )
    if attribution:
        compact_attribution: dict[str, Any] = {}
        recovery_focus = (
            attribution.get("recovery_focus")
            if isinstance(attribution.get("recovery_focus"), list)
            else []
        )
        if recovery_focus:
            compact_attribution["recovery_focus"] = [
                _clean_text(row, limit=220)
                for row in recovery_focus[:4]
                if _clean_text(row, limit=220)
            ]
        for key in ("worst_groups", "best_groups"):
            rows = attribution.get(key) if isinstance(attribution.get(key), list) else []
            if rows:
                compact_attribution[key] = [
                    {
                        field: row.get(field)
                        for field in (
                            "group_type",
                            "group",
                            "sample_count",
                            "total_net_pnl",
                            "expectancy_pct",
                            "profit_factor",
                            "risk_score",
                        )
                        if isinstance(row, dict) and row.get(field) not in (None, "")
                    }
                    for row in rows[:4]
                    if isinstance(row, dict)
                ]
        if compact_attribution:
            compact["failure_attribution"] = compact_attribution
    validation_recovery_focus = (
        live_authority.get("validation_recovery_focus")
        if isinstance(live_authority.get("validation_recovery_focus"), list)
        else []
    )
    if validation_recovery_focus:
        compact["validation_recovery_focus"] = [
            {
                key: row.get(key)
                for key in (
                    "source",
                    "status",
                    "reason",
                    "action",
                    "source_scope",
                    "active_set_count",
                    "active_oos_coverage_rate_pct",
                    "active_wfa_coverage_rate_pct",
                )
                if isinstance(row, dict) and row.get(key) not in (None, "")
            }
            for row in validation_recovery_focus[:4]
            if isinstance(row, dict)
        ]
    guidance = (
        live_authority.get("operator_guidance")
        if isinstance(live_authority.get("operator_guidance"), list)
        else []
    )
    if guidance:
        compact["operator_guidance"] = [
            _clean_text(row, limit=220)
            for row in guidance[:4]
            if _clean_text(row, limit=220)
        ]
    remediation_plan = (
        live_authority.get("remediation_plan")
        if isinstance(live_authority.get("remediation_plan"), dict)
        else {}
    )
    if remediation_plan:
        compact_categories: list[dict[str, Any]] = []
        categories = (
            remediation_plan.get("categories")
            if isinstance(remediation_plan.get("categories"), list)
            else []
        )
        for raw_category in categories[:3]:
            if not isinstance(raw_category, dict):
                continue
            items = raw_category.get("items") if isinstance(raw_category.get("items"), list) else []
            compact_items = [
                {
                    key: item.get(key)
                    for key in ("discipline_id", "label", "status", "action")
                    if isinstance(item, dict) and item.get(key) not in (None, "")
                }
                for item in items[:3]
                if isinstance(item, dict)
            ]
            compact_categories.append(
                {
                    key: raw_category.get(key)
                    for key in ("id", "label", "weak_count", "fail_count")
                    if raw_category.get(key) not in (None, "")
                }
                | ({"items": compact_items} if compact_items else {})
            )
        raw_work_queue = (
            remediation_plan.get("work_queue")
            if isinstance(remediation_plan.get("work_queue"), list)
            else []
        )
        compact_work_queue = [
            {
                key: item.get(key)
                for key in (
                    "task_id",
                    "discipline_id",
                    "status",
                    "priority",
                    "owner",
                    "cadence",
                    "lane_policy_hint",
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
                )
                if isinstance(item, dict) and item.get(key) not in (None, "")
            }
            for item in raw_work_queue[:4]
            if isinstance(item, dict)
        ]
        compact["remediation_plan"] = {
            key: remediation_plan.get(key)
            for key in (
                "status",
                "primary_next_action",
                "weak_count",
                "failed_count",
                "missing_count",
            )
            if remediation_plan.get(key) not in (None, "")
        }
        if compact_categories:
            compact["remediation_plan"]["categories"] = compact_categories
        if compact_work_queue:
            compact["remediation_plan"]["work_queue"] = compact_work_queue
    return compact


def _compact_validation_repair_enforcement_for_reflection(
    block: dict[str, Any],
) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    enforcement = (
        metadata.get("validation_repair_enforcement")
        if isinstance(metadata.get("validation_repair_enforcement"), dict)
        else block.get("validation_repair_enforcement")
        if isinstance(block.get("validation_repair_enforcement"), dict)
        else {}
    )
    if not enforcement:
        return {}
    compact: dict[str, Any] = {
        key: enforcement.get(key)
        for key in (
            "version",
            "scale_up_blocked",
            "waiting_entry_required",
            "live_shadow_required",
            "budget_multiplier",
            "rejected",
            "reason",
        )
        if enforcement.get(key) not in (None, "", [], {})
    }
    for key in (
        "repair_action_ids",
        "automation_hooks",
        "allowed_entry_postures",
        "last_repair_statuses",
        "last_repair_reasons",
    ):
        values = enforcement.get(key) if isinstance(enforcement.get(key), list) else []
        if values:
            compact[key] = [
                _clean_text(value, limit=180)
                for value in values[:8]
                if _clean_text(value, limit=180)
            ]
    adjustments = (
        enforcement.get("adjustments")
        if isinstance(enforcement.get("adjustments"), list)
        else []
    )
    compact_adjustments: list[dict[str, Any]] = []
    for row in adjustments[:8]:
        if not isinstance(row, dict):
            continue
        compact_adjustments.append(
            {
                key: row.get(key)
                for key in (
                    "field",
                    "from",
                    "to",
                    "entry_trigger_price",
                    "entry_trigger_operator",
                    "reason",
                )
                if row.get(key) not in (None, "", [], {})
            }
        )
    if compact_adjustments:
        compact["adjustments"] = compact_adjustments
    return compact


def _compact_lane_authority_gate_for_reflection(block: dict[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    gate = (
        metadata.get("lane_authority_gate")
        if isinstance(metadata.get("lane_authority_gate"), dict)
        else block.get("lane_authority_gate")
        if isinstance(block.get("lane_authority_gate"), dict)
        else {}
    )
    if not gate:
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "source",
        "matched_lane",
        "action",
        "reason",
        "grade",
        "scale_decision",
        "scale_up_allowed",
        "requires_waiting_entry",
        "budget_multiplier",
        "applied_max_budget_multiplier",
        "applied_risk_budget_multiplier",
        "max_budget_multiplier",
        "qty_cap",
        "cost_evidence_status",
        "cost_evidence_repair_hint",
        "entry_quality_repair_hint",
        "validation_evidence_repair_hint",
    ):
        if gate.get(key) not in (None, "", [], {}):
            compact[key] = gate.get(key)
    for key in (
        "matched_lanes",
        "weak_lane_sources",
        "scale_blockers",
        "scale_repair_targets",
        "cost_repair_targets",
        "entry_repair_targets",
    ):
        values = gate.get(key) if isinstance(gate.get(key), list) else []
        if values:
            compact[key] = [
                _clean_text(value, limit=180)
                for value in values[:8]
                if _clean_text(value, limit=180)
            ]
    passport = (
        gate.get("risk_budget_passport")
        if isinstance(gate.get("risk_budget_passport"), dict)
        else {}
    )
    if passport:
        compact_passport: dict[str, Any] = {
            key: passport.get(key)
            for key in (
                "lane_key",
                "status",
                "grade",
                "scale_decision",
                "scale_up_allowed",
                "risk_budget_multiplier",
                "applied_risk_budget_multiplier",
                "applied_max_budget_multiplier",
                "max_budget_multiplier",
                "kelly_fraction",
                "kelly_cap",
                "mdd_cap",
                "risk_of_ruin_cap",
                "risk_of_ruin_pct",
                "max_drawdown_pct",
                "live_trade_count",
                "shadow_trade_count",
                "confidence",
            )
            if passport.get(key) not in (None, "", [], {})
        }
        for key in (
            "scale_blockers",
            "scale_repair_targets",
            "cost_repair_targets",
            "entry_repair_targets",
        ):
            values = passport.get(key) if isinstance(passport.get(key), list) else []
            if values:
                compact_passport[key] = [
                    _clean_text(value, limit=180)
                    for value in values[:8]
                    if _clean_text(value, limit=180)
                ]
        if compact_passport:
            compact["risk_budget_passport"] = compact_passport
    return compact


def _safe_int(value: Any) -> int:
    try:
        return int(round(_safe_float(value)))
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _is_crypto_symbol(value: Any) -> bool:
    text = re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())
    return bool(text) and any(text.endswith(suffix) for suffix in CRYPTO_QUOTE_SUFFIXES)


def _jue_workflow_pack(workflow_id: str) -> dict[str, Any]:
    try:
        return JueSkillRegistry().compile_prompt_pack(workflow_id)
    except JueSkillValidationError as exc:
        return {
            "workflow_id": workflow_id,
            "status": "error",
            "error_message": str(exc),
        }


def _normalize_memory_scope(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "kr": "kis",
        "krx": "kis",
        "korea": "kis",
        "domestic": "kis",
        "crypto": "binance",
        "bnb": "binance",
        "global": "core",
        "general": "core",
    }
    text = aliases.get(text, text)
    return text if text in MEMORY_SCOPES else ""


def _normalize_transferability(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "local": "direct",
        "same_venue": "direct",
        "cross": "translated",
        "cross_venue": "translated",
        "ignore": "blocked",
    }
    text = aliases.get(text, text)
    return text if text in MEMORY_TRANSFERABILITY else ""


def _evidence_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _memory_scope_from_evidence(value: Any) -> str:
    for row in _evidence_items(value):
        scope = _normalize_memory_scope(
            row.get("memory_scope")
            or row.get("scope")
            or row.get("venue")
            or row.get("market")
        )
        if scope:
            return scope
    return ""


def _transferability_from_evidence(value: Any) -> str:
    for row in _evidence_items(value):
        transferability = _normalize_transferability(row.get("transferability"))
        if transferability:
            return transferability
    return ""


def _infer_memory_scope(
    *,
    memory_type: Any,
    key: Any,
    evidence: Any = None,
) -> str:
    scope = _memory_scope_from_evidence(evidence)
    if scope:
        return scope
    memory_type_text = str(memory_type or "").strip().lower()
    key_text = str(key or "").strip()
    if memory_type_text in {"general", "regime", "policy", "seed"}:
        return "core"
    if key_text.startswith("bnb_") or _is_crypto_symbol(key_text):
        return "binance"
    if _is_symbol(key_text):
        return "kis"
    if key_text.startswith("blk_") or key_text.startswith("kis_"):
        return "kis"
    return "core"


def _block_memory_scope(block: dict[str, Any]) -> str:
    scope = _normalize_memory_scope(block.get("memory_scope") or block.get("venue"))
    if scope:
        return scope
    market = str(block.get("market") or "").strip().lower()
    block_id = str(block.get("block_id") or "")
    symbol = str(block.get("symbol") or "")
    if market in {"spot", "futures"} or block_id.startswith("bnb_") or _is_crypto_symbol(symbol):
        return "binance"
    if _is_symbol(symbol) or block_id.startswith("blk_") or block_id.startswith("kis_"):
        return "kis"
    return "core"


def _default_transferability(*, memory_type: str, scope: str) -> str:
    if scope == "core":
        return "direct"
    if memory_type in {
        "block_reflection",
        "general",
        "period_review",
        "policy",
        "policy_rule",
        "policy_scorecard",
        "regime",
    }:
        return "translated"
    return "direct"


def _scope_bucket(
    *,
    target_scope: str,
    item_scope: str,
    transferability: str,
    memory_type: str,
) -> str:
    if not target_scope:
        return "local"
    if item_scope == "core":
        return "core"
    if item_scope == target_scope:
        return "local"
    if transferability == "blocked":
        return "blocked"
    if transferability == "translated":
        return "translated"
    return "blocked"


def _truncate(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 1)].rstrip() + "..."


def _storage_compaction_meta(
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
        "retained_keys": retained_keys[:40],
        "emergency": bool(emergency),
    }


_STORAGE_DROPPED_KEYS = {
    "raw",
    "raw_json",
    "payload_json",
    "html",
    "body",
    "content",
    "response",
    "raw_response",
    "prompt",
    "snapshot",
}


def _compact_storage_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
    depth: int = 0,
) -> Any:
    if depth > 5:
        text = str(value or "").strip()
        limit = max(int(string_limit), 80)
        if len(text) > limit:
            return f"[truncated:{len(text)} chars]"
        return text
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key or "")
            if not text_key:
                continue
            if text_key.strip().lower() in _STORAGE_DROPPED_KEYS:
                continue
            compacted = _compact_storage_value(
                item,
                string_limit=max(int(string_limit * 0.75), 80),
                list_limit=list_limit,
                depth=depth + 1,
            )
            if compacted in ({}, [], "", None):
                continue
            compact[text_key] = compacted
            if len(compact) >= 32:
                break
        return compact
    if isinstance(value, (list, tuple)):
        return [
            item
            for item in (
                _compact_storage_value(
                    row,
                    string_limit=max(int(string_limit * 0.75), 80),
                    list_limit=list_limit,
                    depth=depth + 1,
                )
                for row in list(value)[: max(int(list_limit), 1)]
            )
            if item not in ({}, [], "", None)
        ]
    if isinstance(value, str):
        text = value.strip()
        limit = max(int(string_limit), 80)
        if len(text) > limit:
            return f"[truncated:{len(text)} chars]"
        return text
    return value


def _ensure_storage_payload_limit(
    payload: dict[str, Any],
    *,
    limit: int,
    label: str,
    original_chars: int | None = None,
) -> dict[str, Any]:
    storage_limit = max(int(limit), 1000)
    original_size = int(original_chars or len(_json_dumps(payload)))
    current_size = len(_json_dumps(payload))
    if current_size <= storage_limit and original_size <= storage_limit:
        return payload
    retained_keys = [str(key) for key in payload.keys()]
    string_limit = 900
    list_limit = 8
    compact = _compact_storage_value(
        payload,
        string_limit=string_limit,
        list_limit=list_limit,
    )
    if not isinstance(compact, dict):
        compact = {}
    compact["_storage_compaction"] = _storage_compaction_meta(
        label=label,
        original_chars=original_size,
        storage_limit_chars=storage_limit,
        retained_keys=retained_keys,
    )
    while len(_json_dumps(compact)) > storage_limit and (
        string_limit > 120 or list_limit > 2
    ):
        string_limit = max(int(string_limit * 0.55), 120)
        list_limit = max(int(list_limit // 2), 2)
        compact = _compact_storage_value(
            payload,
            string_limit=string_limit,
            list_limit=list_limit,
        )
        if not isinstance(compact, dict):
            compact = {}
        compact["_storage_compaction"] = _storage_compaction_meta(
            label=label,
            original_chars=original_size,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
        )
    if len(_json_dumps(compact)) <= storage_limit:
        return compact
    return {
        "_storage_compaction": _storage_compaction_meta(
            label=label,
            original_chars=original_size,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
            emergency=True,
        ),
        "status": _clean_text(payload.get("status") or "", limit=80),
        "task": _clean_text(payload.get("task") or "", limit=400),
        "slot": _clean_text(payload.get("slot") or "", limit=80),
        "trading_day": _clean_text(payload.get("trading_day") or "", limit=80),
    }


def _compact_market_pulse(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing"}
    return {
        "status": value.get("status") or "unknown",
        "regime": value.get("regime") or "",
        "score": value.get("score"),
        "score_method_version": value.get("score_method_version") or "",
    }


def _compact_decision_packet_v2(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    previous_reviews: list[dict[str, Any]] = []
    for row in list(value.get("previous_decision_reviews") or [])[-5:]:
        if not isinstance(row, dict):
            continue
        previous_reviews.append(
            {
                "run_id": row.get("run_id"),
                "run_at": row.get("run_at"),
                "status": row.get("status"),
                "mode": row.get("mode"),
                "action_counts": row.get("action_counts") or {},
                "applied_counts": row.get("applied_counts") or {},
                "error_message": _compact_error_message(row.get("error_message"), limit=240),
            }
        )
    packet = {
        "version": str(value.get("version") or ""),
        "generated_at": str(value.get("generated_at") or ""),
        "risk_budget": value.get("risk_budget") if isinstance(value.get("risk_budget"), dict) else {},
        "recent_execution_summary": (
            value.get("recent_execution_summary")
            if isinstance(value.get("recent_execution_summary"), dict)
            else {}
        ),
        "previous_decision_reviews": previous_reviews,
        "llm_focus_questions": [
            _truncate(row, 180)
            for row in list(value.get("llm_focus_questions") or [])[:6]
        ],
    }
    blocks = value.get("blocks") if isinstance(value.get("blocks"), list) else []
    compact_blocks: list[dict[str, Any]] = []
    for row in blocks[:8]:
        if not isinstance(row, dict):
            continue
        stop_policy = row.get("stop_policy") if isinstance(row.get("stop_policy"), dict) else {}
        technical = row.get("technical") if isinstance(row.get("technical"), dict) else {}
        compact_blocks.append(
            {
                "block_id": row.get("block_id"),
                "symbol": row.get("symbol"),
                "horizon": row.get("horizon"),
                "status": row.get("status"),
                "price": technical.get("price"),
                "day_change_pct": technical.get("day_change_pct"),
                "stop_touched_now": stop_policy.get("stop_touched_now"),
                "target_touched_now": stop_policy.get("target_touched_now"),
                "touch_action": stop_policy.get("touch_action"),
            }
        )
    if compact_blocks:
        packet["blocks"] = compact_blocks
    return packet


def _compact_daily_discovery(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    def compact_row(row: dict[str, Any]) -> dict[str, Any]:
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        payload: dict[str, Any] = {
            "symbol": _clean_text(row.get("symbol"), limit=16),
            "name": _clean_text(row.get("name") or analysis.get("name"), limit=80),
            "market": _clean_text(row.get("market"), limit=20),
            "score": row.get("score"),
            "stance": _clean_text(row.get("stance") or analysis.get("stance"), limit=80),
            "confidence": row.get("confidence", analysis.get("confidence")),
            "summary": _clean_text(row.get("summary") or analysis.get("summary"), limit=260),
        }
        return {
            key: item
            for key, item in payload.items()
            if item not in ("", None)
        }

    items = [
        compact_row(row)
        for row in list(value.get("items") or [])[:10]
        if isinstance(row, dict)
    ]
    block_candidates = [
        compact_row(row)
        for row in list(value.get("block_candidates") or [])[:5]
        if isinstance(row, dict)
    ]
    return {
        "status": _clean_text(value.get("status") or "unknown", limit=40),
        "trading_day": _clean_text(value.get("trading_day"), limit=40),
        "summary": _clean_text(value.get("summary"), limit=500),
        "items": [row for row in items if row],
        "block_candidates": [row for row in block_candidates if row],
    }


def _compact_error_message(value: Any, *, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    markers = (
        " USER: ",
        " USER:",
        " user SYSTEM:",
        " user ",
        "-------- user",
        "user:",
        "\"messages\"",
        "\"context\"",
    )
    indices = [text.find(marker) for marker in markers if text.find(marker) > 0]
    if indices:
        text = text[: min(indices)].strip()
    return _truncate(text, limit)


def _compact_message_md(value: Any, *, limit: int = 6000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    marker = "LLM 메모리 생성은 실패했습니다:"
    if marker in text:
        prefix, error = text.split(marker, 1)
        text = f"{prefix.rstrip()}\n\n{marker} {_compact_error_message(error)}"
    for leak_marker in (" USER: ", "-------- user", "\"context\":"):
        index = text.find(leak_marker)
        if index > 0:
            text = text[:index].rstrip()
            break
    return _truncate(text, limit)


_GENERIC_SYMBOL_NAMES = {"정보", "투자", "종목", "종목명", "코드", "리포트", "기업"}
_SYMBOL_NAME_KEYS = (
    ("asset_name", 4),
    ("prdt_name", 4),
    ("hts_kor_isnm", 4),
    ("name", 2),
    ("company_name", 2),
    ("symbol_name", 2),
)


def _clean_symbol_display_name(value: Any, *, symbol: str) -> str:
    text = _clean_text(value, limit=80)
    code = str(symbol or "").strip()
    if not text or text == code or _is_symbol(text):
        return ""
    if text in _GENERIC_SYMBOL_NAMES:
        return ""
    if "<" in text or ">" in text:
        return ""
    return text


def _collect_symbol_display_names(value: Any) -> dict[str, str]:
    collected: dict[str, tuple[int, str]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            symbol = str(
                node.get("symbol")
                or node.get("asset")
                or node.get("pdno")
                or node.get("mksc_shrn_iscd")
                or ""
            ).strip()
            if _is_symbol(symbol):
                for key, weight in _SYMBOL_NAME_KEYS:
                    name = _clean_symbol_display_name(node.get(key), symbol=symbol)
                    current = collected.get(symbol)
                    if name and (current is None or weight > current[0]):
                        collected[symbol] = (weight, name)
                        break
            for child in node.values():
                visit(child)
            return
        if isinstance(node, list):
            for child in node[:400]:
                visit(child)

    visit(value)
    return {symbol: name for symbol, (_weight, name) in collected.items()}


def _prefer_symbol_names_in_message(message: str, names: dict[str, str]) -> str:
    text = str(message or "")
    if not text or not names:
        return text
    for symbol, name in sorted(names.items()):
        if not _is_symbol(symbol) or not name:
            continue
        display = f"{name} ({symbol})"
        text = re.sub(
            rf"\b{re.escape(symbol)}\s*\(\s*{re.escape(symbol)}\s*\)",
            display,
            text,
        )
        text = re.sub(
            rf"(?<![0-9A-Za-z(]){re.escape(symbol)}(?![0-9A-Za-z)])",
            display,
            text,
        )
    return text


def _symbol_display_label(row: dict[str, Any], *, fallback_symbol: str = "") -> str:
    symbol = str(row.get("symbol") or fallback_symbol or "").strip()
    name = _clean_symbol_display_name(row.get("name") or row.get("asset_name"), symbol=symbol)
    if name and symbol:
        return f"{name} ({symbol})"
    return name or symbol or "-"


def _compact_position(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    symbol = str(row.get("symbol") or row.get("asset") or "")[:16]
    if symbol:
        payload["symbol"] = symbol
    name = _clean_text(row.get("name") or row.get("asset_name"), limit=40)
    if name:
        payload["name"] = name
    numeric_sources = {
        "qty": ("qty",),
        "available_qty": ("available_qty", "available"),
        "avg_price": ("avg_price",),
        "mark_price": ("mark_price", "price"),
        "value_krw": ("value_krw",),
        "unrealized_pnl_krw": ("unrealized_pnl_krw", "pnl_krw"),
        "unrealized_pnl_pct": ("unrealized_pnl_pct",),
        "weight": ("weight", "weight_pct"),
    }
    for target_key, source_keys in numeric_sources.items():
        for source_key in source_keys:
            if source_key not in row or row.get(source_key) in (None, "", [], {}):
                continue
            payload[target_key] = _safe_float(row.get(source_key))
            break
    return payload


def _compact_account_context(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing"}
    numeric_keys = [
        "cash_krw",
        "orderable_cash_krw",
        "settled_cash_krw",
        "receivable_cash_krw",
        "settlement_cash_krw",
        "next_day_cash_krw",
        "today_sell_amount_krw",
        "today_fee_tax_krw",
        "total_value_krw",
        "position_count",
    ]
    compact: dict[str, Any] = {
        key: _safe_float(value.get(key))
        for key in numeric_keys
        if value.get(key) not in (None, "")
    }
    for key in ("status", "captured_at", "source", "error_message"):
        if value.get(key):
            compact[key] = _clean_text(value.get(key), limit=160)
    raw_positions = (
        value.get("positions")
        if "positions" in value and value.get("positions") is not None
        else value.get("items")
    )
    positions = [
        row
        for row in list(raw_positions or [])
        if isinstance(row, dict)
    ]
    compact["positions"] = [_compact_position(row) for row in positions[:8]]
    compact["position_sample_count"] = len(compact["positions"])
    compact["position_total_count"] = len(positions)
    return compact


def _compact_block_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "block_id",
        "symbol",
        "name",
        "status",
        "horizon",
        "created_by",
        "created_at",
        "opened_at",
        "closed_at",
    ]
    payload: dict[str, Any] = {
        key: _clean_text(row.get(key), limit=80)
        for key in keys
        if row.get(key) not in (None, "")
    }
    for key in (
        "qty_initial",
        "qty_open",
        "entry_price",
        "target_price",
        "stop_price",
        "current_price",
        "unrealized_pnl_krw",
        "unrealized_pnl_pct",
        "realized_pnl_krw",
    ):
        if row.get(key) not in (None, ""):
            payload[key] = _safe_float(row.get(key))
    if isinstance(row.get("quote"), dict):
        quote = row["quote"]
        payload["quote"] = {
            key: quote.get(key)
            for key in ("price", "change_pct", "source", "stale")
            if key in quote
        }
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if "horizon" not in payload and metadata.get("horizon"):
        payload["horizon"] = _clean_text(metadata.get("horizon"), limit=32)
    if metadata.get("allocation_reason"):
        payload["allocation_reason"] = _clean_text(
            metadata.get("allocation_reason"),
            limit=180,
        )
    for key in ("thesis", "llm_reason", "risk_note"):
        if row.get(key):
            payload[key] = _clean_text(row.get(key), limit=220)
    return payload


def _compact_order_row(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, limit in (
        ("block_id", 80),
        ("symbol", 16),
        ("side", 12),
        ("status", 32),
        ("reason", 80),
        ("created_at", 80),
    ):
        if row.get(key) not in (None, ""):
            payload[key] = _clean_text(row.get(key), limit=limit)
    for key, caster in (
        ("qty", _safe_int),
        ("limit_price", _safe_float),
    ):
        if row.get(key) not in (None, ""):
            payload[key] = caster(row.get(key))
    return payload


def _compact_event_row(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, limit in (
        ("block_id", 80),
        ("event_type", 60),
        ("message", 180),
        ("created_at", 80),
    ):
        if row.get(key) not in (None, ""):
            payload[key] = _clean_text(row.get(key), limit=limit)
    return payload


def _block_horizon(row: dict[str, Any]) -> str:
    horizon = str(row.get("horizon") or "").strip()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if not horizon:
        horizon = str(metadata.get("horizon") or "").strip()
    return horizon


def _is_active_block(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "") in {"open", "entry_pending", "exit_pending"}


def _compact_blocks_context(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing", "blocks": []}
    if not isinstance(value.get("blocks"), list) and (
        "active_blocks" in value or "open_count" in value
    ):
        explicit_open_count = (
            _safe_int(value.get("open_count"))
            if value.get("open_count") not in (None, "")
            else None
        )
        active_blocks = [
            row
            for row in list(value.get("active_blocks") or [])
            if isinstance(row, dict)
        ][:10]
        if explicit_open_count == 0:
            active_blocks = []
        compact: dict[str, Any] = {
            "status": value.get("status") or "ok",
            "open_count": (
                explicit_open_count
                if explicit_open_count is not None
                else len(active_blocks)
            ),
            "allocation": value.get("allocation") or {},
            "active_blocks": [_compact_block_row(row) for row in active_blocks],
            "recent_closed_blocks": [
                _compact_block_row(row)
                for row in list(value.get("recent_closed_blocks") or [])[:6]
                if isinstance(row, dict)
            ],
            "recent_orders": [
                _compact_order_row(row)
                for row in list(value.get("recent_orders") or [])[:12]
                if isinstance(row, dict)
            ],
            "recent_events": [
                _compact_event_row(row)
                for row in list(value.get("recent_events") or [])[:12]
                if isinstance(row, dict)
            ],
        }
        for count_key in ("total_count", "open_total_count", "closed_sample_count"):
            if value.get(count_key) not in (None, ""):
                compact[count_key] = _safe_int(value.get(count_key))
        return compact
    blocks = [row for row in list(value.get("blocks") or []) if isinstance(row, dict)]
    active = [
        row
        for row in blocks
        if str(row.get("status") or "") in {"open", "entry_pending", "exit_pending"}
    ][:10]
    recent_closed = [
        row
        for row in blocks
        if str(row.get("status") or "") in {"closed", "error", "paused"}
    ][:6]
    events = [
        row
        for row in list(value.get("events") or [])
        if isinstance(row, dict)
    ][:12]
    orders = [
        row
        for row in list(value.get("orders") or [])
        if isinstance(row, dict)
    ][:12]
    return {
        "status": value.get("status") or "ok",
        "total_count": len(blocks),
        "open_total_count": len(
            [
                row
                for row in blocks
                if str(row.get("status") or "")
                in {"open", "entry_pending", "exit_pending"}
            ]
        ),
        "open_count": len(active),
        "closed_sample_count": len(recent_closed),
        "allocation": value.get("allocation") or {},
        "active_blocks": [_compact_block_row(row) for row in active],
        "recent_closed_blocks": [_compact_block_row(row) for row in recent_closed],
        "recent_orders": [
            _compact_order_row(row)
            for row in orders
        ],
        "recent_events": [
            _compact_event_row(row)
            for row in events
        ],
    }


def _compact_strategy_context(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing", "candidates": []}
    candidates = [
        row
        for row in list(value.get("candidates") or [])
        if isinstance(row, dict)
    ][:10]
    compact_candidates: list[dict[str, Any]] = []
    for row in candidates:
        candidate: dict[str, Any] = {}
        for key, limit in (("symbol", 16), ("name", 60)):
            if row.get(key) not in (None, ""):
                candidate[key] = _clean_text(row.get(key), limit=limit)
        for key in ("score", "confidence", "risk_score"):
            if row.get(key) not in (None, ""):
                candidate[key] = row.get(key)
        for key in ("suitability", "data_coverage"):
            if row.get(key) not in (None, "", [], {}):
                candidate[key] = row.get(key)
        drivers = [
            _clean_text(item, limit=120)
            for item in list(row.get("drivers") or [])[:3]
            if _clean_text(item, limit=120)
        ]
        if drivers:
            candidate["drivers"] = drivers
        risks = [
            _clean_text(item, limit=120)
            for item in list(row.get("risks") or [])[:3]
            if _clean_text(item, limit=120)
        ]
        if risks:
            candidate["risks"] = risks
        if candidate:
            compact_candidates.append(candidate)
    raw_sources = (
        value.get("sources")
        if "sources" in value and value.get("sources") is not None
        else value.get("source_status")
    )
    sources = list(raw_sources or []) if isinstance(raw_sources, list) else raw_sources or []
    return {
        "status": value.get("status") or "ok",
        "candidate_count": len(list(value.get("candidates") or [])),
        "sources": sources,
        "candidates": compact_candidates,
    }


def _compact_etf_universe_sample(rows: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    for row in list(rows or [])[:limit]:
        if not isinstance(row, dict):
            continue
        sample.append(
            {
                key: _clean_text(row.get(key), limit=80)
                for key in ("symbol", "name", "category")
                if row.get(key) not in (None, "")
            }
        )
    return sample


def _etf_item_state(snapshot: dict[str, Any], score: dict[str, Any]) -> str:
    states = {
        str(snapshot.get("status") or "").strip(),
        str(score.get("status") or "").strip(),
        str(score.get("label") or "").strip(),
    }
    if "error" in states:
        return "error"
    if snapshot.get("stale") or score.get("stale") or "stale" in states:
        return "stale"
    if "missing" in states or "unknown" in states:
        return "missing"
    return str(snapshot.get("status") or score.get("status") or "ok") or "ok"


def _compact_etf_research_context(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing", "items": []}
    items: list[dict[str, Any]] = []
    for row in list(value.get("items") or [])[:6]:
        if not isinstance(row, dict):
            continue
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        score = row.get("score") if isinstance(row.get("score"), dict) else {}
        latest: dict[str, Any] = {}
        if score.get("label") not in (None, ""):
            latest["label"] = _clean_text(score.get("label"), limit=40)
        for key, source in (
            ("price", snapshot),
            ("change_pct", snapshot),
            ("turnover_krw", snapshot),
            ("liquidity_score", score),
            ("core_fit_score", score),
            ("risk_score", score),
        ):
            if source.get(key) not in (None, ""):
                latest[key] = _safe_float(source.get(key))
        risk_status = (
            score.get("risk_status")
            if score.get("risk_status") not in (None, "")
            else score.get("risk_label")
            if score.get("risk_label") not in (None, "")
            else score.get("risk")
        )
        if risk_status not in (None, ""):
            latest["risk_status"] = _clean_text(risk_status, limit=40)
        item: dict[str, Any] = {
            "symbol": _clean_text(row.get("symbol") or snapshot.get("symbol"), limit=16),
            "name": _clean_text(row.get("name") or snapshot.get("name"), limit=80),
            "state": _etf_item_state(snapshot, score),
        }
        if latest:
            item["latest"] = latest
        for key, source_key, source, limit in (
            ("snapshot_status", "status", snapshot, 40),
            ("score_status", "status", score, 40),
            ("captured_at", "captured_at", snapshot, 80),
            ("scored_at", "scored_at", score, 80),
        ):
            if source.get(source_key) not in (None, ""):
                item[key] = _clean_text(source.get(source_key), limit=limit)
        if "score_status" not in item and score.get("label") not in (None, ""):
            item["score_status"] = _clean_text(score.get("label"), limit=40)
        error_message = (
            snapshot.get("error_message")
            if snapshot.get("error_message") not in (None, "")
            else score.get("error_message")
        )
        if error_message not in (None, ""):
            item["error_message"] = _compact_error_message(error_message, limit=180)
        item = {
            key: item_value
            for key, item_value in item.items()
            if item_value not in (None, "", [], {})
        }
        if item:
            items.append(item)
    candidates: list[dict[str, Any]] = []
    for row in list(value.get("strategy_etf_candidates") or [])[:5]:
        if not isinstance(row, dict):
            continue
        candidate: dict[str, Any] = {}
        for key, limit in (
            ("symbol", 16),
            ("name", 80),
            ("horizon_bias", 40),
            ("asset_class", 40),
        ):
            if row.get(key) not in (None, ""):
                candidate[key] = _clean_text(row.get(key), limit=limit)
        for key in ("score", "confidence", "risk_score"):
            if row.get(key) not in (None, ""):
                candidate[key] = row.get(key)
        if candidate:
            candidates.append(candidate)
    provider_status = (
        _compact_status_context(value.get("provider_status"), limit=800)
        if isinstance(value.get("provider_status"), dict)
        else {}
    )
    return {
        "status": value.get("status") or provider_status.get("status") or "unknown",
        "reason": _clean_text(value.get("reason"), limit=120),
        "error_message": _compact_error_message(value.get("error_message"), limit=180),
        "provider_status": provider_status,
        "configured_universe_sample": _compact_etf_universe_sample(
            value.get("configured_universe")
        ),
        "provider_universe_sample": _compact_etf_universe_sample(
            value.get("provider_universe")
        ),
        "item_count": len(list(value.get("items") or [])),
        "items": items,
        "strategy_etf_candidates": candidates,
    }


def _compact_etf_allocation_context(
    allocation: dict[str, Any] | None,
    *,
    active_core_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(allocation, dict):
        return {
            "status": "from_blocks" if active_core_blocks else "missing",
            "active_core_block_count": len(active_core_blocks),
        }
    items: list[dict[str, Any]] = []
    for row in list(allocation.get("items") or []):
        if not isinstance(row, dict):
            continue
        if _block_horizon(row) != "core_etf":
            continue
        item: dict[str, Any] = {"horizon": "core_etf"}
        for key in (
            "current_value_krw",
            "current_weight",
            "target_weight",
            "drift",
        ):
            if row.get(key) not in (None, ""):
                item[key] = _safe_float(row.get(key))
        items.append(item)
    target = None
    targets = allocation.get("targets") if isinstance(allocation.get("targets"), dict) else {}
    if "core_etf" in targets:
        target = _safe_float(targets.get("core_etf"))
    return {
        "status": allocation.get("status") or "ok",
        "core_etf_target_weight": target,
        "core_etf_items": items[:3],
        "active_core_block_count": len(active_core_blocks),
    }


def _compact_etf_core_context(
    *,
    etf_research: dict[str, Any] | None,
    blocks: list[dict[str, Any]] | None,
    allocation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    active_core_blocks = [
        _compact_block_row(row)
        for row in list(blocks or [])
        if isinstance(row, dict)
        and _block_horizon(row) == "core_etf"
        and _is_active_block(row)
    ][:6]
    research = _compact_etf_research_context(etf_research)
    allocation_summary = _compact_etf_allocation_context(
        allocation,
        active_core_blocks=active_core_blocks,
    )
    has_research = isinstance(etf_research, dict)
    has_allocation = (
        bool(allocation_summary.get("core_etf_items"))
        or allocation_summary.get("core_etf_target_weight") is not None
    )
    if not has_research and not active_core_blocks and not has_allocation:
        return None
    return {
        "status": research.get("status") if has_research else "blocks_only",
        "research": research,
        "allocation": allocation_summary,
        "active_core_blocks": active_core_blocks,
        "policy_note": (
            "ETF/Core is for exposure, diversification, and rebalance. "
            "Targets and stops are risk/rebalance thresholds, not company valuation calls."
        ),
    }


def _compact_status_context(value: Any, *, limit: int = 1800) -> Any:
    if not isinstance(value, dict):
        return value
    text = _json_dumps(value)
    if len(text) <= limit:
        return value
    keys = [
        "status",
        "updated_at",
        "total_count",
        "ok_count",
        "error_count",
        "stale_count",
        "last_success_at",
        "last_error_at",
        "report_count",
        "symbol_count",
        "source_count",
        "error_message",
    ]
    return {
        key: value.get(key)
        for key in keys
        if key in value and value.get(key) not in (None, "")
    }


def _compact_jue_workflow(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    skills = [
        {
            key: item
            for key, item in {
                "skill_id": row.get("skill_id"),
                "name": row.get("name"),
                "version": row.get("version"),
                "scope": row.get("scope"),
                "required_outputs": list(row.get("required_outputs") or [])[:6]
                if "required_outputs" in row
                else None,
            }.items()
            if item not in (None, "", [], {})
        }
        for row in list(value.get("skills") or [])[:8]
        if isinstance(row, dict)
    ]
    contracts = [
        {
            key: item
            for key, item in {
                "contract_id": row.get("contract_id"),
                "version": row.get("version"),
                "required": bool(row.get("required"))
                if "required" in row and row.get("required") is not None
                else None,
                "source_types": list(row.get("source_types") or [])[:6]
                if "source_types" in row
                else None,
            }.items()
            if item not in (None, "", [], {})
        }
        for row in list(value.get("contracts") or [])[:8]
        if isinstance(row, dict)
    ]
    compact = {
        "workflow_id": value.get("workflow_id"),
        "workflow_version": value.get("workflow_version"),
        "scope": value.get("scope"),
        "status": value.get("status") or "ok",
        "error_message": value.get("error_message") or "",
        "model_policy": value.get("model_policy") or {},
        "cadence": value.get("cadence") or {},
        "required_context": list(value.get("required_context") or [])[:12],
        "authority": value.get("authority") or {},
        "safety_gates": list(value.get("safety_gates") or [])[:12],
        "skills": skills,
        "contracts": contracts,
        "prompt_budget": value.get("prompt_budget") or {},
    }
    return {
        key: item
        for key, item in compact.items()
        if item not in (None, "", [], {})
    }


def _compact_llm_usage_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    total = value.get("total") if isinstance(value.get("total"), dict) else {}
    rows = value.get("by_component") if isinstance(value.get("by_component"), list) else []
    metric_keys = (
        "call_count",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "estimated_token_count",
        "error_count",
    )
    compact_total = {
        key: total.get(key)
        for key in metric_keys
        if total.get(key) not in (None, "", [], {})
    }
    compact_rows: list[dict[str, Any]] = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        compact_row = {
            "component": row.get("component"),
            **{
                key: row.get(key)
                for key in metric_keys
                if row.get(key) not in (None, "", [], {})
            },
        }
        compact_row = {
            key: item
            for key, item in compact_row.items()
            if item not in (None, "", [], {})
        }
        if compact_row:
            compact_rows.append(compact_row)
    compact: dict[str, Any] = {}
    if value.get("trading_day") not in (None, ""):
        compact["trading_day"] = value.get("trading_day")
    if compact_total:
        compact["total"] = compact_total
    if compact_rows:
        compact["by_component"] = compact_rows
    return compact


def _compact_ritual_context(
    context: dict[str, Any] | None,
    *,
    limit: int = 16_000,
) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {"status": "missing"}
    compact: dict[str, Any] = {
        "captured_at": _clean_text(context.get("captured_at"), limit=80),
        "account": _compact_account_context(context.get("account")),
        "blocks": _compact_blocks_context(context.get("blocks")),
    }
    if isinstance(context.get("binance_blocks"), dict):
        compact["binance_blocks"] = _compact_blocks_context(context.get("binance_blocks"))
    if isinstance(context.get("clock"), dict):
        compact["clock"] = _compact_status_context(context.get("clock"), limit=1000)
    if isinstance(context.get("market_clock"), dict):
        compact["market_clock"] = _compact_status_context(
            context.get("market_clock"),
            limit=1000,
        )
    if isinstance(context.get("market_pulse"), dict):
        compact["market_pulse"] = _compact_market_pulse(context.get("market_pulse"))
    if isinstance(context.get("daily_discovery"), dict):
        compact_daily_discovery = _compact_daily_discovery(context.get("daily_discovery"))
        if compact_daily_discovery:
            compact["daily_discovery"] = compact_daily_discovery
    if isinstance(context.get("market_judgment"), dict):
        compact["market_judgment"] = _compact_status_context(
            context.get("market_judgment"),
            limit=2200,
        )
    if isinstance(context.get("latest_manager_run"), dict):
        latest = context["latest_manager_run"]
        compact["latest_manager_run"] = {
            "status": latest.get("status"),
            "mode": latest.get("mode"),
            "run_at": latest.get("run_at"),
            "error_message": _compact_error_message(latest.get("error_message")),
            "actions": _compact_status_context(latest.get("actions") or {}, limit=1200),
        }
    if isinstance(context.get("strategy"), dict):
        compact["strategy"] = _compact_strategy_context(context.get("strategy"))
    etf_core = (
        context.get("etf_core")
        if isinstance(context.get("etf_core"), dict)
        else _compact_etf_core_context(
            etf_research=context.get("etf_research")
            if isinstance(context.get("etf_research"), dict)
            else None,
            blocks=list((context.get("blocks") or {}).get("blocks") or [])
            if isinstance(context.get("blocks"), dict)
            else [],
            allocation=context.get("allocation")
            if isinstance(context.get("allocation"), dict)
            else None,
        )
    )
    if etf_core is not None:
        compact["etf_core"] = etf_core
    if isinstance(context.get("llm_usage"), dict):
        compact["llm_usage"] = _compact_llm_usage_context(context.get("llm_usage"))
    if isinstance(context.get("jue_workflow"), dict):
        compact["jue_workflow"] = _compact_jue_workflow(context.get("jue_workflow"))
    for key in (
        "research",
        "reports_status",
        "valuation_status",
        "strategy_source_status",
        "memory_status",
    ):
        if key in context:
            compact[key] = _compact_status_context(context.get(key), limit=1800)

    text = _json_dumps(compact)
    if len(text) <= limit:
        return compact
    compact["research"] = _compact_status_context(compact.get("research"), limit=600)
    if isinstance(compact.get("strategy"), dict):
        compact["strategy"]["candidates"] = compact["strategy"].get("candidates", [])[:5]
    if isinstance(compact.get("blocks"), dict):
        compact["blocks"]["active_blocks"] = compact["blocks"].get("active_blocks", [])[:6]
        compact["blocks"]["recent_events"] = compact["blocks"].get("recent_events", [])[:6]
        compact["blocks"]["recent_orders"] = compact["blocks"].get("recent_orders", [])[:6]
    if isinstance(compact.get("binance_blocks"), dict):
        compact["binance_blocks"]["active_blocks"] = compact["binance_blocks"].get(
            "active_blocks",
            [],
        )[:6]
        compact["binance_blocks"]["recent_events"] = compact["binance_blocks"].get(
            "recent_events",
            [],
        )[:6]
        compact["binance_blocks"]["recent_orders"] = compact["binance_blocks"].get(
            "recent_orders",
            [],
        )[:6]
    if isinstance(compact.get("etf_core"), dict):
        compact["etf_core"]["active_core_blocks"] = compact["etf_core"].get(
            "active_core_blocks",
            [],
        )[:4]
        if isinstance(compact["etf_core"].get("research"), dict):
            compact["etf_core"]["research"]["items"] = compact["etf_core"]["research"].get(
                "items",
                [],
            )[:4]
    if len(_json_dumps(compact)) <= limit:
        return compact
    if isinstance(compact.get("account"), dict):
        compact["account"]["positions"] = compact["account"].get("positions", [])[:4]
    if isinstance(compact.get("blocks"), dict):
        compact["blocks"]["active_blocks"] = compact["blocks"].get("active_blocks", [])[:4]
        compact["blocks"]["recent_closed_blocks"] = compact["blocks"].get(
            "recent_closed_blocks",
            [],
        )[:3]
    if isinstance(compact.get("binance_blocks"), dict):
        compact["binance_blocks"]["active_blocks"] = compact["binance_blocks"].get(
            "active_blocks",
            [],
        )[:4]
        compact["binance_blocks"]["recent_closed_blocks"] = compact["binance_blocks"].get(
            "recent_closed_blocks",
            [],
        )[:3]
    return compact


def _compact_active_policies_for_ritual_prompt(
    rows: Any,
    *,
    limit: int = 6,
    reason_limit: int = 180,
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        scorecard = row.get("scorecard") if isinstance(row.get("scorecard"), dict) else {}
        compact_row = {
            "policy_id": _clean_text(row.get("policy_id"), limit=160),
            "action": _clean_text(row.get("action"), limit=60),
            "strength": _clean_text(row.get("strength"), limit=60),
            "status": _clean_text(row.get("status"), limit=60),
            "reason": _truncate(row.get("reason"), reason_limit),
        }
        if row.get("confidence") not in (None, ""):
            compact_row["confidence"] = _safe_float(row.get("confidence"))
        if scorecard:
            compact_row["scorecard"] = {
                key: scorecard.get(key)
                for key in (
                    "policy_id",
                    "status",
                    "action",
                    "sample_count",
                    "win_rate",
                    "expectancy_pct",
                    "confidence",
                )
                if scorecard.get(key) not in ("", None)
            }
        compact.append(
            {
                key: value
                for key, value in compact_row.items()
                if value not in ("", {}, None)
            }
        )
        if len(compact) >= max(int(limit), 0):
            break
    return compact


def _strip_block_text_for_ritual_budget(payload: dict[str, Any]) -> None:
    for section in ("blocks", "binance_blocks"):
        block_payload = payload.get(section)
        if not isinstance(block_payload, dict):
            continue
        for key in ("active_blocks", "recent_closed_blocks"):
            rows = block_payload.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for text_key in ("llm_reason", "thesis", "risk_note", "allocation_reason"):
                    if text_key in row:
                        row[text_key] = _truncate(row.get(text_key), 80)
        block_payload["recent_events"] = []
        block_payload["recent_orders"] = []


def _enforce_ritual_prompt_budget(
    prompt: dict[str, Any],
    *,
    max_chars: int = MEMORY_RUN_INPUT_STORAGE_LIMIT,
) -> dict[str, Any]:
    budget = max(int(max_chars), 8_000)
    payload = copy.deepcopy(prompt)
    original_chars = len(_json_dumps(payload))

    def current_chars() -> int:
        return len(_json_dumps(payload))

    def attach_budget(status: str) -> dict[str, Any]:
        payload["prompt_budget"] = {
            "status": status,
            "max_chars": budget,
            "original_chars": original_chars,
            "final_chars": current_chars(),
            "policy": "investment_memory_ritual_prompt_v1",
        }
        payload["prompt_budget"]["final_chars"] = current_chars()
        return payload

    if current_chars() <= budget:
        return attach_budget("ok")

    payload["persona"] = _truncate(payload.get("persona"), 700)
    policies = payload.get("policies") if isinstance(payload.get("policies"), dict) else {}
    if policies:
        payload["policies"] = {
            "trading": _truncate(policies.get("trading"), 700),
            "update": _truncate(policies.get("update"), 500),
            "telegram": _truncate(policies.get("telegram"), 420),
            "active": _compact_active_policies_for_ritual_prompt(
                policies.get("active"),
                limit=6,
                reason_limit=80,
            ),
        }
    if isinstance(payload.get("context"), dict):
        payload["context"] = _compact_ritual_context(payload.get("context"), limit=4_000)
        _strip_block_text_for_ritual_budget(payload["context"])
    payload["safety"] = [
        "HERMES is a live block-trading judgment system.",
        "Express actions as block intent, validation trigger, target, stop, or policy memory.",
        "Safety gates remain outside memory policy.",
    ]
    if current_chars() <= budget:
        return attach_budget("ok")

    if isinstance(payload.get("context"), dict):
        payload["context"] = _compact_ritual_context(payload.get("context"), limit=2_400)
        _strip_block_text_for_ritual_budget(payload["context"])
    policies = payload.get("policies") if isinstance(payload.get("policies"), dict) else {}
    if policies:
        payload["policies"]["active"] = _compact_active_policies_for_ritual_prompt(
            policies.get("active"),
            limit=3,
            reason_limit=120,
        )
    payload["persona"] = _truncate(payload.get("persona"), 360)
    payload["output_schema"] = {
        "title": "short Korean title",
        "message_md": "Telegram-ready Korean markdown",
        "memory_updates": "symbols/blocks/notes updates",
        "policy_changes": "observe/prefer/caution only",
    }
    payload["telegram_display_policy"] = [
        "Use symbol name first when known, e.g. 삼성전자 (005930)."
    ]
    if current_chars() <= budget:
        return attach_budget("ok")

    minimal = {
        "task": payload.get("task"),
        "language_policy": payload.get("language_policy"),
        "slot": payload.get("slot"),
        "slot_label": payload.get("slot_label"),
        "trading_day": payload.get("trading_day"),
        "persona": _truncate(payload.get("persona"), 240),
        "policies": {
            "active": _compact_active_policies_for_ritual_prompt(
                (payload.get("policies") or {}).get("active")
                if isinstance(payload.get("policies"), dict)
                else [],
                limit=2,
                reason_limit=90,
            )
        },
        "context": _compact_ritual_context(
            payload.get("context") if isinstance(payload.get("context"), dict) else {},
            limit=1_400,
        ),
        "output_schema": payload.get("output_schema"),
        "safety": ["HERMES live block-trading memory. Safety gates override."],
    }
    _strip_block_text_for_ritual_budget(minimal["context"])
    payload = minimal
    return attach_budget("ok" if current_chars() <= budget else "over_budget")


def _compact_run_payload(value: Any, *, limit: int = 4000) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    original_chars = len(_json_dumps(value))
    payload = dict(value)
    if isinstance(payload.get("blocks"), dict):
        payload["blocks"] = _compact_blocks_context(payload.get("blocks"))
    if isinstance(payload.get("binance_blocks"), dict):
        payload["binance_blocks"] = _compact_blocks_context(payload.get("binance_blocks"))
    if isinstance(payload.get("account"), dict):
        payload["account"] = _compact_account_context(payload.get("account"))
    if isinstance(payload.get("jue_workflow"), dict):
        payload["jue_workflow"] = _compact_jue_workflow(payload.get("jue_workflow"))
    for key in (
        "research",
        "reports_status",
        "valuation_status",
        "strategy_source_status",
        "memory_status",
        "market_judgment",
    ):
        if isinstance(payload.get(key), dict):
            payload[key] = _compact_status_context(payload.get(key), limit=1200)
    if isinstance(payload.get("context"), dict):
        payload["context"] = _compact_ritual_context(
            payload.get("context"),
            limit=max(limit // 2, 1600),
        )
    if "message_md" in payload:
        payload["message_md"] = _compact_message_md(payload.get("message_md"))
    if "message" in payload:
        payload["message"] = _compact_message_md(payload.get("message"), limit=2400)
    for key in ("persona", "task"):
        if key in payload:
            payload[key] = _truncate(payload.get(key), 600)
    if isinstance(payload.get("policies"), dict):
        payload["policies"] = _compact_status_context(payload.get("policies"), limit=1200)
    if len(_json_dumps(payload)) <= limit:
        return _ensure_storage_payload_limit(
            payload,
            limit=limit,
            label="memory_run",
            original_chars=original_chars,
        )
    compact: dict[str, Any] = {
        key: payload.get(key)
        for key in (
            "task",
            "slot",
            "slot_label",
            "trading_day",
            "context",
            "account",
            "blocks",
            "binance_blocks",
            "jue_workflow",
            "safety",
        )
        if key in payload
    }
    if len(_json_dumps(compact)) <= limit:
        return _ensure_storage_payload_limit(
            compact,
            limit=limit,
            label="memory_run",
            original_chars=original_chars,
        )
    if isinstance(compact.get("context"), dict):
        compact["context"] = _compact_ritual_context(compact["context"], limit=1400)
    return _ensure_storage_payload_limit(
        compact,
        limit=limit,
        label="memory_run",
        original_chars=original_chars,
    )


def _compact_telegram_result(value: Any) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"text", "message", "description"}:
                compact[key] = _compact_message_md(item, limit=2400)
            elif key in {"error", "error_message", "detail"}:
                compact[key] = _compact_telegram_result(item)
            else:
                compact[key] = _compact_telegram_result(item)
        return compact
    if isinstance(value, list):
        return [_compact_telegram_result(item) for item in value[:20]]
    if isinstance(value, str):
        return _compact_error_message(value, limit=800) if "USER:" in value else _truncate(value, 2400)
    return value


def _compact_policy_impact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    effect = value.get("effect") if isinstance(value.get("effect"), dict) else {}
    compact_effect = {
        key: effect.get(key)
        for key in (
            "policy_mode",
            "hard_filter",
            "safety_gate_override",
            "action",
            "status",
            "entry_bias",
            "exit_bias",
            "validation_effect_profile",
            "discipline_id",
            "scale_blocker",
            "require_stop_price",
            "require_validation_review",
            "require_fresh_data",
            "require_positive_net_edge",
            "require_capacity_check",
            "require_shadow_validation",
            "require_regime_match",
            "require_risk_budget_review",
            "require_exposure_review",
            "require_validation_repair_review",
            "require_scale_repair_review",
            "required_evidence",
            "target_stop_review",
            "min_reward_risk",
            "max_stop_risk_pct",
            "risk_budget_multiplier",
            "max_budget_multiplier",
            "sizing_policy",
            "validation_pressure_entry_constraint",
            "validation_pressure_sizing_constraint",
            "validation_pressure_repair_action",
            "validation_pressure_block_design_focus",
            "validation_pressure_status",
            "risk_note",
        )
        if effect.get(key) not in ("", None, [], {})
    }
    compact = {
        "policy_id": _clean_text(value.get("policy_id"), limit=160),
        "version": _safe_int(value.get("version")),
        "rule_id": _clean_text(value.get("rule_id"), limit=180),
        "status": _clean_text(value.get("status"), limit=80),
        "action": _clean_text(value.get("action"), limit=80),
        "effect": compact_effect,
        "evidence": {
            key: item
            for key, item in {
                "workflow_ids": _compact_text_list(
                    (value.get("evidence") or {}).get("workflow_ids")
                    if isinstance(value.get("evidence"), dict)
                    else None,
                    limit=8,
                    item_limit=120,
                ),
                "skill_ids": _compact_text_list(
                    (value.get("evidence") or {}).get("skill_ids")
                    if isinstance(value.get("evidence"), dict)
                    else None,
                    limit=8,
                    item_limit=120,
                ),
                "contract_ids": _compact_text_list(
                    (value.get("evidence") or {}).get("contract_ids")
                    if isinstance(value.get("evidence"), dict)
                    else None,
                    limit=8,
                    item_limit=160,
                ),
            }.items()
            if item not in ("", [], {}, None)
        },
        "matched_metric": value.get("matched_metric")
        if isinstance(value.get("matched_metric"), dict)
        else {},
        "reason": _clean_text(value.get("reason"), limit=320),
    }
    return {
        key: item
        for key, item in compact.items()
        if item not in ("", [], {}, None)
    }


def _compact_policy_impacts(values: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    return [
        row
        for row in (_compact_policy_impact(item) for item in list(values or [])[:limit])
        if row
    ]


def _compact_policy_impact_map(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, list[dict[str, Any]]] = {}
    for raw_key, rows in list(value.items())[:20]:
        key = _clean_text(raw_key, limit=160)
        if not key:
            continue
        impacts = _compact_policy_impacts(rows)
        if impacts:
            compact[key] = impacts
    return compact


def _compact_policy_rule_evaluation(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": value.get("status") or "ok",
        "active_rule_count": value.get("active_rule_count") or 0,
        "applied_count": value.get("applied_count") or 0,
        "active_rules": [],
        "global": _compact_policy_impacts(value.get("global")),
        "by_symbol": _compact_policy_impact_map(value.get("by_symbol")),
        "by_block": _compact_policy_impact_map(value.get("by_block")),
    }


def _build_block_design_constraints(
    *,
    validation_repair_backlog: dict[str, Any],
    rule_evaluation: dict[str, Any],
    limit: int = 8,
) -> dict[str, Any]:
    backlog_items = (
        validation_repair_backlog.get("items")
        if isinstance(validation_repair_backlog, dict)
        and isinstance(validation_repair_backlog.get("items"), list)
        else []
    )
    backlog_by_policy = {
        str(row.get("policy_id") or ""): row
        for row in backlog_items
        if isinstance(row, dict) and str(row.get("policy_id") or "")
    }
    recovered_policy_ids = {
        str(row.get("policy_id") or "")
        for row in list(validation_repair_backlog.get("manager_contract_recovered") or [])
        if isinstance(row, dict) and str(row.get("policy_id") or "")
    }
    impacts: list[dict[str, Any]] = []
    for source_key in ("global",):
        rows = (
            rule_evaluation.get(source_key)
            if isinstance(rule_evaluation.get(source_key), list)
            else []
        )
        impacts.extend(row for row in rows if isinstance(row, dict))
    active_rules = (
        rule_evaluation.get("active_rules")
        if isinstance(rule_evaluation.get("active_rules"), list)
        else []
    )
    active_by_policy = {
        str(row.get("policy_id") or ""): row
        for row in active_rules
        if isinstance(row, dict) and str(row.get("policy_id") or "")
    }
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in impacts:
        policy_id = str(row.get("policy_id") or "")
        if not policy_id.startswith((
            "validation.",
            "lane_scale.",
            "manager_contract_error.",
            "jue_wiki_selection.",
            "jue_wiki_execution_hint.",
            "period_memory_coverage.",
        )):
            continue
        if policy_id in seen:
            continue
        if policy_id in recovered_policy_ids:
            continue
        seen.add(policy_id)
        effect = row.get("effect") if isinstance(row.get("effect"), dict) else {}
        source_rule = active_by_policy.get(policy_id, {})
        source_scorecard = (
            source_rule.get("source_scorecard")
            if isinstance(source_rule.get("source_scorecard"), dict)
            else {}
        )
        backlog = backlog_by_policy.get(policy_id, {})
        discipline_id = _clean_text(
            effect.get("discipline_id")
            or effect.get("period_memory_status")
            or source_scorecard.get("discipline_id")
            or source_scorecard.get("scale_blocker")
            or source_scorecard.get("period_memory_status")
            or policy_id.rsplit(".", 1)[-1],
            limit=80,
        )
        venue = (
            _normalize_memory_scope(source_scorecard.get("venue"))
            or _normalize_memory_scope(source_scorecard.get("memory_scope"))
            or _normalize_memory_scope(source_scorecard.get("scope"))
            or _normalize_memory_scope(backlog.get("memory_scope"))
            or _normalize_memory_scope(backlog.get("venue"))
            or _normalize_memory_scope(backlog.get("scope"))
        )
        if not venue and policy_id.startswith(("validation.kis.", "validation.binance.")):
            venue = policy_id.split(".", 2)[1]
        item = {
            "policy_id": policy_id,
            "venue": venue or backlog.get("venue") or "core",
            "discipline_id": discipline_id,
            "scale_blocker": effect.get("scale_blocker")
            or source_scorecard.get("scale_blocker"),
            "priority": backlog.get("priority") or "p2",
            "pass_current_gap": source_scorecard.get("pass_current_gap")
            or effect.get("validation_pass_current_gap")
            or backlog.get("pass_current_gap"),
            "pass_collection_hook": source_scorecard.get("pass_collection_hook")
            or effect.get("pass_collection_hook")
            or backlog.get("pass_collection_hook"),
            "pass_criteria": source_scorecard.get("pass_criteria")
            or effect.get("pass_criteria")
            or backlog.get("pass_criteria"),
            "validation_effect_profile": effect.get("validation_effect_profile"),
            "execution_hint": effect.get("execution_hint"),
            "period_memory_status": effect.get("period_memory_status")
            or source_scorecard.get("period_memory_status"),
            "period_memory_gap_count": source_scorecard.get("period_memory_gap_count"),
            "period_memory_override_count": source_scorecard.get(
                "period_memory_override_count"
            ),
            "period_memory_contract_gap_count": source_scorecard.get(
                "period_memory_contract_gap_count"
            ),
            "period_memory_repair_quality": effect.get(
                "period_memory_repair_quality"
            ),
            "period_memory_missing_metadata": _compact_text_list(
                source_scorecard.get("period_memory_missing_metadata"),
                limit=6,
                item_limit=80,
            ),
            "period_memory_repair_actions": _compact_text_list(
                source_scorecard.get("period_memory_repair_actions"),
                limit=6,
                item_limit=160,
            ),
            "memory_contract": effect.get("memory_contract")
            or source_scorecard.get("contract"),
            "memory_contract_error": effect.get("memory_contract_error")
            or source_scorecard.get("latest_error"),
            "memory_contract_rows": _compact_memory_contract_rows(
                effect.get("memory_contract_rows")
                if isinstance(effect.get("memory_contract_rows"), list)
                else source_scorecard.get("memory_contract_rows")
                if isinstance(source_scorecard.get("memory_contract_rows"), list)
                else backlog.get("memory_contract_rows"),
                limit=6,
            ),
            "impacted_symbols": _compact_text_list(
                source_scorecard.get("impacted_symbols"),
                limit=8,
                item_limit=40,
            ),
            "metadata_contract_audit_resolutions": _compact_text_list(
                source_scorecard.get("metadata_contract_audit_resolutions"),
                limit=6,
                item_limit=180,
            ),
            "metadata_contract_repair_notes": _compact_text_list(
                source_scorecard.get("metadata_contract_repair_notes"),
                limit=4,
                item_limit=300,
            ),
            "entry_bias": effect.get("entry_bias"),
            "sizing_policy": effect.get("sizing_policy"),
            "validation_pressure_entry_constraint": effect.get(
                "validation_pressure_entry_constraint"
            ),
            "validation_pressure_sizing_constraint": effect.get(
                "validation_pressure_sizing_constraint"
            ),
            "validation_pressure_repair_action": effect.get(
                "validation_pressure_repair_action"
            ),
            "validation_pressure_block_design_focus": effect.get(
                "validation_pressure_block_design_focus"
            ),
            "validation_pressure_status": effect.get("validation_pressure_status"),
            "target_stop_review": effect.get("target_stop_review"),
            "min_reward_risk": effect.get("min_reward_risk"),
            "max_stop_risk_pct": effect.get("max_stop_risk_pct"),
            "risk_budget_multiplier": effect.get("risk_budget_multiplier"),
            "max_budget_multiplier": effect.get("max_budget_multiplier"),
            "required_evidence": list(effect.get("required_evidence") or [])[:8],
            "pass_required_evidence": source_scorecard.get("pass_required_evidence")
            or effect.get("pass_required_evidence")
            or backlog.get("pass_required_evidence"),
            "required_checks": [
                key
                for key in (
                    "require_fresh_data",
                    "require_positive_net_edge",
                    "require_capacity_check",
                    "require_shadow_validation",
                    "require_regime_match",
                    "require_risk_budget_review",
                    "require_exposure_review",
                    "require_validation_review",
                    "require_scale_repair_review",
                    "require_jue_wiki_execution_hint_audit",
                    "require_fresh_wiki_context",
                    "require_period_memory_override_audit",
                    "require_period_memory_fresh_review_or_replay",
                    "require_period_memory_metadata_contract_repair",
                    "require_memory_contract_resolution",
                )
                if bool(effect.get(key))
            ],
            "exit_criteria": backlog.get("exit_criteria"),
            "blocks_scaling": backlog.get("blocks_scaling") or effect.get("sizing_policy"),
            "risk_note": effect.get("risk_note"),
        }
        items.append(
            {
                key: value
                for key, value in item.items()
                if value not in (None, "", [], {})
            }
        )
        if len(items) >= max(int(limit), 1):
            break
    if not items:
        return {"status": "clear", "items": []}
    return {
        "status": "active_constraints",
        "item_count": len(items),
        "items": items,
        "instruction": (
            "Apply these soft constraints to the next block design: choose entry "
            "style, quantity, target/stop, and evidence references according to "
            "each validation effect. Do not treat them as hard bans."
        ),
    }


def _validation_recovery_response(item: dict[str, Any]) -> list[str]:
    responses: list[str] = []
    entry_bias = str(item.get("entry_bias") or item.get("lane_policy_hint") or "").lower()
    sizing_policy = str(item.get("sizing_policy") or item.get("blocks_scaling") or "").lower()
    target_stop_review = str(item.get("target_stop_review") or "").lower()
    if any(token in entry_bias for token in ("wait", "waiting", "probe", "pullback", "대기")):
        responses.append("prefer_waiting_or_probe_entry")
    if any(
        token in sizing_policy
        for token in ("reduce", "cap", "probe", "no_scale", "보류", "축소")
    ) or 0 < _safe_float(item.get("risk_budget_multiplier")) < 1:
        responses.append("reduce_or_cap_sizing")
    if target_stop_review:
        responses.append("review_target_stop_before_entry")
    if item.get("required_evidence") or item.get("required_checks"):
        responses.append("require_evidence_repair")
    if not responses:
        responses.append("observe_until_next_validation")
    return responses


def _build_validation_recovery_summary(
    *,
    validation_repair_backlog: dict[str, Any],
    block_design_constraints: dict[str, Any],
    rule_evaluation: dict[str, Any],
    limit: int = 8,
) -> dict[str, Any]:
    backlog_items = [
        row
        for row in list(validation_repair_backlog.get("items") or [])
        if isinstance(row, dict)
    ]
    constraint_items = [
        row
        for row in list(block_design_constraints.get("items") or [])
        if isinstance(row, dict)
    ]
    manager_contract_recovered = [
        row
        for row in list(validation_repair_backlog.get("manager_contract_recovered") or [])
        if isinstance(row, dict)
    ][: max(int(limit), 1)]
    by_policy: dict[str, dict[str, Any]] = {}
    for row in backlog_items + constraint_items:
        policy_id = str(row.get("policy_id") or "")
        if not policy_id:
            continue
        merged = dict(by_policy.get(policy_id) or {})
        merged.update({key: value for key, value in row.items() if value not in (None, "", [], {})})
        by_policy[policy_id] = merged
    items: list[dict[str, Any]] = []
    for row in by_policy.values():
        item = {
            "policy_id": row.get("policy_id"),
            "venue": row.get("venue") or "core",
            "discipline_id": row.get("discipline_id"),
            "status": row.get("status") or row.get("policy_status") or "active",
            "priority": row.get("priority") or "p2",
            "entry_bias": row.get("entry_bias") or row.get("lane_policy_hint"),
            "sizing_policy": row.get("sizing_policy") or row.get("blocks_scaling"),
            "target_stop_review": row.get("target_stop_review"),
            "risk_budget_multiplier": row.get("risk_budget_multiplier"),
            "max_budget_multiplier": row.get("max_budget_multiplier"),
            "min_reward_risk": row.get("min_reward_risk"),
            "required_evidence": list(row.get("required_evidence") or [])[:8],
            "required_checks": list(row.get("required_checks") or [])[:8],
            "exit_criteria": row.get("exit_criteria"),
            "current_jue_response": _validation_recovery_response(row),
        }
        items.append(
            {
                key: value
                for key, value in item.items()
                if value not in (None, "", [], {})
            }
        )
    priority_rank = {"p0": 0, "p1": 1, "p2": 2}
    status_rank = {"fail": 0, "failed": 0, "missing": 1, "warn": 2, "warning": 2}
    items.sort(
        key=lambda row: (
            priority_rank.get(str(row.get("priority") or ""), 9),
            status_rank.get(str(row.get("status") or ""), 9),
            str(row.get("venue") or ""),
            str(row.get("discipline_id") or ""),
        )
    )
    active_count = len(items)
    if not active_count:
        return {
            "status": "clear",
            "active_diagnostic_count": 0,
            "hard_filter": False,
            "scale_up_allowed": True,
            "items": [],
            "manager_contract_recovered": manager_contract_recovered,
            "jue_response_summary": {
                "new_entries": "normal_selective",
                "sizing": "normal",
                "target_stop": "normal",
            },
        }
    response_counts: dict[str, int] = {}
    for item in items:
        for response in list(item.get("current_jue_response") or []):
            response_counts[response] = response_counts.get(response, 0) + 1
    applied_count = _safe_int(rule_evaluation.get("applied_count"))
    return {
        "status": "active_repair",
        "active_diagnostic_count": active_count,
        "applied_policy_impact_count": applied_count,
        "hard_filter": False,
        "scale_up_allowed": False,
        "items": items[: max(int(limit), 1)],
        "manager_contract_recovered": manager_contract_recovered,
        "jue_response_summary": {
            "new_entries": (
                "waiting_or_probe_preferred"
                if response_counts.get("prefer_waiting_or_probe_entry")
                else "selective"
            ),
            "sizing": (
                "reduced_or_capped"
                if response_counts.get("reduce_or_cap_sizing")
                else "normal"
            ),
            "target_stop": (
                "review_required"
                if response_counts.get("review_target_stop_before_entry")
                else "normal"
            ),
            "evidence": (
                "repair_required"
                if response_counts.get("require_evidence_repair")
                else "normal"
            ),
        },
        "instruction": (
            "These diagnostics are soft operating constraints: Jue should reduce "
            "size, prefer waiting/probe entries, and repair evidence until the "
            "exit criteria are met. They are not strategy hard bans."
        ),
    }


def _unique_clean_values(values: Iterable[Any], *, limit: int = 12) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            item: Any = _clean_text(value, limit=180)
            key = item
        else:
            item = value
            key = _json_dumps(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max(int(limit), 1):
            break
    return out


def _positive_min(values: Iterable[Any]) -> float | None:
    positives = [
        value
        for value in (_safe_float(item) for item in values)
        if value > 0
    ]
    return min(positives) if positives else None


def _positive_max(values: Iterable[Any]) -> float | None:
    positives = [
        value
        for value in (_safe_float(item) for item in values)
        if value > 0
    ]
    return max(positives) if positives else None


def _build_symbol_design_adjustments(
    *,
    rule_evaluation: dict[str, Any],
    limit: int = 12,
) -> list[dict[str, Any]]:
    by_symbol = (
        rule_evaluation.get("by_symbol")
        if isinstance(rule_evaluation.get("by_symbol"), dict)
        else {}
    )
    items: list[dict[str, Any]] = []
    for symbol, impacts in list(by_symbol.items())[: max(int(limit), 1)]:
        rows = [row for row in list(impacts or []) if isinstance(row, dict)]
        if not rows:
            continue
        effects = [
            row.get("effect")
            for row in rows
            if isinstance(row.get("effect"), dict)
        ]
        policy_ids = _unique_clean_values(
            (row.get("policy_id") for row in rows),
            limit=6,
        )
        entry_biases = _unique_clean_values(
            (effect.get("entry_bias") for effect in effects),
            limit=4,
        )
        sizing_policies = _unique_clean_values(
            (effect.get("sizing_policy") for effect in effects),
            limit=4,
        )
        target_stop_reviews = _unique_clean_values(
            (effect.get("target_stop_review") for effect in effects),
            limit=4,
        )
        required_checks = _unique_clean_values(
            check
            for effect in effects
            for check in (
                "require_fresh_data",
                "require_positive_net_edge",
                "require_capacity_check",
                "require_shadow_validation",
                "require_regime_match",
                "require_risk_budget_review",
                "require_exposure_review",
                "require_validation_review",
                "require_validation_repair_review",
                "require_scale_repair_review",
            )
            if bool(effect.get(check))
        )
        min_risk_budget = _positive_min(
            effect.get("risk_budget_multiplier") for effect in effects
        )
        max_budget_cap = _positive_min(
            effect.get("max_budget_multiplier") for effect in effects
        )
        min_reward_risk = _positive_max(
            effect.get("min_reward_risk") for effect in effects
        )
        max_stop_risk_pct = _positive_min(
            effect.get("max_stop_risk_pct") for effect in effects
        )
        item = {
            "symbol": _clean_text(symbol, limit=40),
            "policy_ids": policy_ids,
            "entry_biases": entry_biases,
            "sizing_policies": sizing_policies,
            "target_stop_reviews": target_stop_reviews,
            "required_checks": required_checks,
            "risk_budget_multiplier": round(min_risk_budget, 6)
            if min_risk_budget is not None
            else None,
            "max_budget_multiplier": round(max_budget_cap, 6)
            if max_budget_cap is not None
            else None,
            "min_reward_risk": round(min_reward_risk, 6)
            if min_reward_risk is not None
            else None,
            "max_stop_risk_pct": round(max_stop_risk_pct, 6)
            if max_stop_risk_pct is not None
            else None,
        }
        items.append(
            {
                key: value
                for key, value in item.items()
                if value not in (None, "", [], {})
            }
        )
    return items


def _build_next_block_design_playbook(
    *,
    validation_recovery_summary: dict[str, Any],
    block_design_constraints: dict[str, Any],
    rule_evaluation: dict[str, Any],
    limit: int = 8,
) -> dict[str, Any]:
    constraint_items = [
        row
        for row in list(block_design_constraints.get("items") or [])
        if isinstance(row, dict)
    ][: max(int(limit), 1)]
    response = (
        validation_recovery_summary.get("jue_response_summary")
        if isinstance(validation_recovery_summary.get("jue_response_summary"), dict)
        else {}
    )
    if not constraint_items:
        return {
            "status": "normal",
            "hard_filter": False,
            "entry": {"posture": "normal_selective"},
            "sizing": {"policy": "normal"},
            "target_stop": {"review_required": False},
            "evidence": {"repair_required": False},
        }

    risk_budget_multiplier = _positive_min(
        row.get("risk_budget_multiplier") for row in constraint_items
    )
    max_budget_multiplier = _positive_min(
        row.get("max_budget_multiplier") for row in constraint_items
    )
    min_reward_risk = _positive_max(row.get("min_reward_risk") for row in constraint_items)
    max_stop_risk_pct = _positive_min(
        row.get("max_stop_risk_pct") for row in constraint_items
    )
    required_evidence = _unique_clean_values(
        evidence
        for row in constraint_items
        for evidence in list(row.get("required_evidence") or [])
    )
    required_checks = _unique_clean_values(
        check
        for row in constraint_items
        for check in list(row.get("required_checks") or [])
    )
    entry_biases = _unique_clean_values(
        row.get("entry_bias") for row in constraint_items
    )
    sizing_policies = _unique_clean_values(
        row.get("sizing_policy") for row in constraint_items
    )
    target_stop_reviews = _unique_clean_values(
        row.get("target_stop_review") for row in constraint_items
    )
    policy_ids = _unique_clean_values(
        row.get("policy_id") for row in constraint_items
    )
    symbol_adjustments = _build_symbol_design_adjustments(
        rule_evaluation=rule_evaluation,
        limit=12,
    )
    playbook = {
        "status": "active",
        "hard_filter": False,
        "active_constraint_count": len(constraint_items),
        "scale_up_allowed": bool(validation_recovery_summary.get("scale_up_allowed")),
        "policy_ids": policy_ids,
        "entry": {
            "posture": response.get("new_entries") or "selective",
            "biases": entry_biases,
            "instruction": (
                "Prefer waiting/probe entries when validation or reflection pressure "
                "is active; immediate entries need explicit edge and evidence."
            ),
        },
        "sizing": {
            "policy": response.get("sizing") or "normal",
            "risk_budget_multiplier": round(risk_budget_multiplier, 6)
            if risk_budget_multiplier is not None
            else None,
            "max_budget_multiplier": round(max_budget_multiplier, 6)
            if max_budget_multiplier is not None
            else None,
            "sizing_policies": sizing_policies,
            "instruction": (
                "Use the smallest active multiplier as a soft cap until the linked "
                "diagnostics recover; do not scale a weak lane just because a new "
                "signal appears."
            ),
        },
        "target_stop": {
            "review_required": (response.get("target_stop") == "review_required")
            or bool(target_stop_reviews),
            "reviews": target_stop_reviews,
            "min_reward_risk": round(min_reward_risk, 6)
            if min_reward_risk is not None
            else None,
            "max_stop_risk_pct": round(max_stop_risk_pct, 6)
            if max_stop_risk_pct is not None
            else None,
            "instruction": (
                "Reprice target and stop around net edge, drawdown pressure, and "
                "entry quality before proposing the next block."
            ),
        },
        "evidence": {
            "repair_required": response.get("evidence") == "repair_required"
            or bool(required_evidence or required_checks),
            "required_evidence": required_evidence,
            "required_checks": required_checks,
        },
        "symbol_adjustments": symbol_adjustments,
        "instruction": (
            "This is the bridge from reflection/validation to the next block: adjust "
            "entry style, quantity, target, stop, and evidence references before "
            "creating or updating a block. These are soft trading policies, not hard bans."
        ),
    }
    return {
        key: value
        for key, value in playbook.items()
        if value not in (None, "", [], {})
    }


def _scoped_memory_item(
    *,
    memory_type: str,
    key: Any,
    summary_md: Any,
    confidence: Any = None,
    evidence: Any = None,
    updated_at: Any = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _infer_memory_scope(
        memory_type=memory_type,
        key=key,
        evidence=evidence,
    )
    transferability = _transferability_from_evidence(evidence) or _default_transferability(
        memory_type=memory_type,
        scope=scope,
    )
    item = {
        "memory_type": memory_type,
        "key": _clean_text(key, limit=180),
        "scope": scope,
        "transferability": transferability,
        "confidence": _safe_float(confidence),
        "summary_md": _truncate(summary_md, 520),
        "updated_at": _clean_text(updated_at, limit=80),
    }
    if extra:
        item.update(
            {
                _clean_text(key, limit=80): value
                for key, value in extra.items()
                if value not in ("", [], {}, None)
            }
        )
    return {
        key: value
        for key, value in item.items()
        if value not in ("", [], {}, None)
    }


def _policy_row_scope(row: dict[str, Any]) -> str:
    source_scorecard = (
        row.get("source_scorecard") if isinstance(row.get("source_scorecard"), dict) else {}
    )
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for payload in (row, source_scorecard, raw):
        scope = _normalize_memory_scope(
            payload.get("memory_scope")
            or payload.get("scope")
            or payload.get("venue")
            or payload.get("market")
        )
        if scope:
            return scope
    for evidence in (
        row.get("scope_evidence"),
        source_scorecard.get("scope_evidence"),
        raw.get("scope_evidence"),
        row.get("evidence"),
    ):
        scope = _memory_scope_from_evidence(evidence)
        if scope:
            return scope
    policy_id = str(
        row.get("policy_id")
        or source_scorecard.get("policy_id")
        or raw.get("policy_id")
        or ""
    ).lower()
    policy_parts = [part for part in policy_id.split(".") if part]
    if policy_id.startswith(("binance.", "crypto.")) or "binance" in policy_parts[:3]:
        return "binance"
    if policy_id.startswith(("kis.", "krx.", "domestic.")) or any(
        part in {"kis", "krx", "domestic"} for part in policy_parts[:3]
    ):
        return "kis"
    return "core"


def _policy_row_transferability(row: dict[str, Any], *, memory_type: str) -> str:
    source_scorecard = (
        row.get("source_scorecard") if isinstance(row.get("source_scorecard"), dict) else {}
    )
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    policy_id = str(
        row.get("policy_id")
        or source_scorecard.get("policy_id")
        or raw.get("policy_id")
        or ""
    ).lower()
    source = str(
        row.get("source")
        or source_scorecard.get("source")
        or raw.get("source")
        or ""
    ).lower()
    if policy_id.startswith("jue_wiki_selection.") or source == "jue_wiki_selection_audit":
        return "direct"
    for evidence in (
        row.get("scope_evidence"),
        source_scorecard.get("scope_evidence"),
        raw.get("scope_evidence"),
        row.get("evidence"),
    ):
        transferability = _transferability_from_evidence(evidence)
        if transferability:
            return transferability
    for payload in (row, source_scorecard, raw):
        transferability = _normalize_transferability(payload.get("transferability"))
        if transferability:
            return transferability
    return _default_transferability(
        memory_type=memory_type,
        scope=_policy_row_scope(row),
    )


def _is_jue_wiki_selection_key(value: Any) -> bool:
    return str(value or "").strip().lower().startswith("jue_wiki_selection.")


def _insight_row_transferability(row: dict[str, Any], *, fallback: str) -> str:
    if _is_jue_wiki_selection_key(row.get("key")):
        return "direct"
    evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip().lower()
        event_key = str(item.get("event_key") or "").strip().lower()
        if source == "jue_wiki_selection_audit" or event_key.startswith(
            "jue_wiki_selection_audit:"
        ):
            return "direct"
    return _normalize_transferability(row.get("transferability")) or fallback


def _policy_scope_evidence(row: dict[str, Any], *, memory_type: str) -> list[dict[str, Any]]:
    source_scorecard = (
        row.get("source_scorecard") if isinstance(row.get("source_scorecard"), dict) else {}
    )
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for evidence in (
        row.get("scope_evidence"),
        source_scorecard.get("scope_evidence"),
        raw.get("scope_evidence"),
    ):
        if _memory_scope_from_evidence(evidence):
            return _evidence_items(evidence)
    return [
        {
            "memory_scope": _policy_row_scope(row),
            "transferability": _policy_row_transferability(row, memory_type=memory_type),
        }
    ]


def _policy_revision_memory_scope(row: dict[str, Any]) -> str:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    evidence_metrics = (
        evidence.get("metrics") if isinstance(evidence.get("metrics"), dict) else {}
    )
    for payload in (row, evidence, metrics, evidence_metrics):
        scope = _normalize_memory_scope(
            payload.get("memory_scope")
            or payload.get("venue")
            or payload.get("market")
            or payload.get("target_scope")
        )
        if scope:
            return scope
    policy_id = str(row.get("policy_id") or "").strip().lower()
    if policy_id.startswith(("kis_", "kis.", "krx_", "krx.")):
        return "kis"
    if policy_id.startswith(("binance_", "binance.", "crypto_", "crypto.")):
        return "binance"
    return "core"


def _scoped_policy_revision_id(*, memory_scope: str, policy_id: str) -> str:
    scope = _normalize_memory_scope(memory_scope)
    normalized_policy_id = _clean_text(policy_id, limit=140)
    if scope in {"kis", "binance"} and not normalized_policy_id.startswith(f"{scope}_"):
        return f"{scope}_{normalized_policy_id}"
    return normalized_policy_id


def _policy_rows_for_target_scope(
    rows: list[dict[str, Any]],
    *,
    target_scope: str | None,
    memory_type: str,
) -> list[dict[str, Any]]:
    normalized_target = _normalize_memory_scope(target_scope)
    if not normalized_target:
        return rows
    scoped: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        scope = _policy_row_scope(row)
        transferability = _policy_row_transferability(row, memory_type=memory_type)
        bucket = _scope_bucket(
            target_scope=normalized_target,
            item_scope=scope,
            transferability=transferability,
            memory_type=memory_type,
        )
        if bucket not in {"core", "local"}:
            continue
        bucket_priority = 0 if bucket == "local" else 1
        scoped.append(
            (
                bucket_priority,
                index,
                {**row, "scope": scope, "transferability": transferability},
            )
        )
    return [row for _, _, row in sorted(scoped, key=lambda item: (item[0], item[1]))]


def _translated_policy_context_for_target_scope(
    rows: list[dict[str, Any]],
    *,
    target_scope: str | None,
    limit: int = 4,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    if not normalized_target:
        return {"status": "not_applicable", "target_scope": ""}
    items: list[tuple[int, int, dict[str, Any]]] = []
    source_scope_counts: dict[str, int] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        source_scope = _policy_row_scope(row)
        transferability = _policy_row_transferability(
            row,
            memory_type="policy_scorecard",
        )
        if (
            _scope_bucket(
                target_scope=normalized_target,
                item_scope=source_scope,
                transferability=transferability,
                memory_type="policy_scorecard",
            )
            != "translated"
        ):
            continue
        source_scope_key = source_scope or "unknown"
        source_scope_counts[source_scope_key] = (
            source_scope_counts.get(source_scope_key, 0) + 1
        )
        status = _clean_text(row.get("status"), limit=60)
        status_priority = 0 if status in {"active_preference", "active_caution"} else 1
        items.append(
            (
                status_priority,
                index,
                {
                    "policy_id": _clean_text(row.get("policy_id"), limit=160),
                    "source_scope": source_scope,
                    "transferability": transferability,
                    "status": status,
                    "action": _clean_text(row.get("action"), limit=40),
                    "sample_count": _safe_int(row.get("sample_count")),
                    "confidence": _safe_float(row.get("confidence")),
                    "expectancy_pct": _safe_float(row.get("expectancy_pct")),
                    "reason": _truncate(row.get("reason"), 260),
                },
            )
        )
    item_limit = max(int(limit), 1)
    sorted_items = sorted(items, key=lambda value: (value[0], value[1]))
    compact_items = [
        {
            key: value
            for key, value in item.items()
            if value not in ("", None, [], {})
        }
        for _, _, item in sorted_items[:item_limit]
    ]
    available_count = len(items)
    selected_count = len(compact_items)
    omitted_count = max(available_count - selected_count, 0)
    metadata = {
        "available_count": available_count,
        "selected_count": selected_count,
        "omitted_count": omitted_count,
        "source_scope_counts": dict(sorted(source_scope_counts.items())),
        "selection_policy": {
            "order": "active status, then prompt order",
            "limit": item_limit,
        },
    }
    if not compact_items:
        return {
            "status": "empty",
            "target_scope": normalized_target,
            "items": [],
            **metadata,
            "instruction": (
                "Use these only as translated lessons, never as direct venue rules."
            ),
        }
    return {
        "status": "available",
        "target_scope": normalized_target,
        "items": compact_items,
        **metadata,
        "instruction": (
            "Use these only as translated lessons, never as direct venue rules."
        ),
    }


def _scope_matches_target(
    *,
    target_scope: str,
    item_scope: str,
    transferability: str,
    memory_type: str,
) -> bool:
    if not target_scope:
        return True
    bucket = _scope_bucket(
        target_scope=target_scope,
        item_scope=item_scope,
        transferability=transferability,
        memory_type=memory_type,
    )
    return bucket in {"core", "local", "translated"}


def _insight_rows_for_target_scope(
    rows: list[dict[str, Any]],
    *,
    target_scope: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_target = _normalize_memory_scope(target_scope)
    if not normalized_target:
        return rows[: max(int(limit), 1)]
    scoped: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        memory_type = str(row.get("memory_type") or "general")
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        item = _scoped_memory_item(
            memory_type=memory_type,
            key=row.get("key"),
            summary_md=row.get("summary_md"),
            confidence=row.get("confidence"),
            evidence=evidence,
            updated_at=row.get("updated_at"),
        )
        scope = (
            _normalize_memory_scope(row.get("memory_scope") or row.get("scope"))
            or str(item.get("scope") or "core")
        )
        transferability = _insight_row_transferability(
            row,
            fallback=str(item.get("transferability") or "direct"),
        )
        if not _scope_matches_target(
            target_scope=normalized_target,
            item_scope=scope,
            transferability=transferability,
            memory_type=memory_type,
        ):
            continue
        priority = 0 if scope == normalized_target else 1
        scoped.append((priority, index, {**row, "scope": scope, "transferability": transferability}))
    return [row for _, _, row in sorted(scoped, key=lambda item: (item[0], item[1]))][
        : max(int(limit), 1)
    ]


def _reflection_rows_for_target_scope(
    rows: list[dict[str, Any]],
    *,
    target_scope: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_target = _normalize_memory_scope(target_scope)
    if not normalized_target:
        return rows[: max(int(limit), 1)]
    scoped: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        scope = _normalize_memory_scope(metrics.get("memory_scope") or metrics.get("venue"))
        if not scope:
            scope = _infer_memory_scope(
                memory_type="block_reflection",
                key=row.get("symbol") or row.get("block_id"),
            )
        transferability = (
            _normalize_transferability(metrics.get("transferability"))
            or _default_transferability(memory_type="block_reflection", scope=scope)
        )
        if not _scope_matches_target(
            target_scope=normalized_target,
            item_scope=scope,
            transferability=transferability,
            memory_type="block_reflection",
        ):
            continue
        priority = 0 if scope == normalized_target else 1
        scoped.append((priority, index, {**row, "scope": scope, "transferability": transferability}))
    return [row for _, _, row in sorted(scoped, key=lambda item: (item[0], item[1]))][
        : max(int(limit), 1)
    ]


def _journal_memory_scope(row: dict[str, Any]) -> str:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    scope = _normalize_memory_scope(
        context.get("memory_scope")
        or context.get("target_scope")
        or context.get("venue")
    )
    if scope:
        return scope
    slot = str(row.get("slot") or "")
    if slot in {"pre_open", "midday", "post_close"}:
        return "kis"
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "message_md", "file_path")
    )
    if "binance" in text.lower() or "bnb_" in text.lower() or re.search(r"\b[A-Z0-9]{2,}USDT\b", text):
        return "binance"
    if re.search(r"\b\d{6}\b", text):
        return "kis"
    if slot == "block_reflection":
        return "core"
    return "core"


def _run_memory_scope(row: sqlite3.Row) -> str:
    payload = _json_loads(row["input_json"], {})
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        context = payload.get("context")
        if isinstance(context, dict):
            candidates.append(context)
        for key in ("account", "blocks", "binance_blocks", "jue_workflow"):
            value = payload.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    for candidate in candidates:
        scope = _normalize_memory_scope(
            candidate.get("memory_scope")
            or candidate.get("target_scope")
            or candidate.get("venue")
            or candidate.get("market")
        )
        if scope:
            return scope
    slot = str(row["slot"] or "")
    if slot in {"pre_open", "midday", "post_close"}:
        return "kis"
    if slot in {"morning", "noon", "night", "crypto_morning", "crypto_noon", "crypto_night"}:
        return "binance"
    text = f"{slot} {_json_dumps(payload)[:4000]}"
    if "binance" in text.lower() or "bnb_" in text.lower() or re.search(r"\b[A-Z0-9]{2,}USDT\b", text):
        return "binance"
    if re.search(r"\b\d{6}\b", text):
        return "kis"
    return "core"


def _journal_rows_for_target_scope(
    rows: list[dict[str, Any]],
    *,
    target_scope: str | None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    normalized_target = _normalize_memory_scope(target_scope)
    max_rows = len(rows) if limit is None else max(int(limit), 1)
    if not normalized_target:
        return rows[:max_rows]
    scoped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scope = _journal_memory_scope(row)
        if scope in {normalized_target, "core"}:
            scoped.append({**row, "memory_scope": scope})
    return scoped[:max_rows]


def _active_policies_for_target_scope(
    rows: list[dict[str, Any]],
    *,
    target_scope: str | None,
) -> list[dict[str, Any]]:
    normalized_target = _normalize_memory_scope(target_scope)
    if not normalized_target:
        return rows
    scoped: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        scorecard = row.get("scorecard") if isinstance(row.get("scorecard"), dict) else row
        scope = _policy_row_scope(scorecard)
        transferability = _policy_row_transferability(scorecard, memory_type="policy_scorecard")
        bucket = _scope_bucket(
            target_scope=normalized_target,
            item_scope=scope,
            transferability=transferability,
            memory_type="policy_scorecard",
        )
        if bucket not in {"core", "local"}:
            continue
        bucket_priority = 0 if bucket == "local" else 1
        scoped.append(
            (
                bucket_priority,
                index,
                {**row, "scope": scope, "transferability": transferability},
            )
        )
    return [row for _, _, row in sorted(scoped, key=lambda item: (item[0], item[1]))]


def _build_validation_repair_backlog(
    events: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
    *,
    target_scope: str | None,
    limit: int = 8,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    scorecard_by_policy = {
        str(row.get("policy_id") or ""): row
        for row in scorecards
        if isinstance(row, dict) and str(row.get("policy_id") or "")
    }
    repair_scorecard_by_action = {
        str(row.get("repair_action_id") or ""): row
        for row in scorecards
        if isinstance(row, dict) and str(row.get("repair_action_id") or "")
    }
    manager_contract_recovered_by_error_policy: dict[str, dict[str, Any]] = {}
    for row in scorecards:
        if not isinstance(row, dict):
            continue
        policy_id = _clean_text(row.get("policy_id"), limit=180)
        if not policy_id.startswith("manager_contract_resolution."):
            continue
        if _clean_text(row.get("resolution_status"), limit=80) != "resolved":
            continue
        parts = policy_id.split(".", 2)
        if len(parts) != 3:
            continue
        venue = _normalize_memory_scope(row.get("venue")) or _normalize_memory_scope(
            parts[1]
        )
        if normalized_target and venue not in {"", normalized_target, "core"}:
            continue
        error_key = _clean_text(parts[2], limit=160)
        error_policy_id = f"manager_contract_error.{venue or 'core'}.{error_key}"
        manager_contract_recovered_by_error_policy[error_policy_id] = {
            "policy_id": error_policy_id,
            "resolution_policy_id": policy_id,
            "contract": _clean_text(row.get("contract"), limit=160),
            "error": error_key,
            "impacted_symbols": _compact_text_list(
                row.get("impacted_symbols"),
                limit=8,
                item_limit=40,
            ),
            "latest_resolution": _clean_text(
                row.get("latest_resolution") or row.get("latest_reason"),
                limit=320,
            ),
        }
    priority_rank = {"p0": 0, "p1": 1, "p2": 2}
    status_rank = {"fail": 0, "failed": 0, "missing": 1, "warn": 2, "warning": 2}
    def backlog_constraints(
        *,
        discipline_id: str,
        work: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        mode = _clean_text(work.get("validation_mode"), limit=100).lower()
        weak_status = str(status or "").strip().lower()
        severe = weak_status in {"fail", "failed", "missing"}
        risk_multiplier = 0.25 if severe else 0.5
        common_scale_blocker = f"validation_{discipline_id}_repair"
        if discipline_id == "data_validation" or mode == "data_repair_before_trade":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "data_quality",
                "entry_bias": "quote_verified_waiting_entry",
                "sizing_policy": "no_size_increase_until_data_clean",
                "target_stop_review": "wait_until_quote_and_fill_evidence_clean",
                "required_evidence": [
                    "fresh_quote",
                    "clean_symbol_identity",
                    "recorded_fill_or_cost_source",
                ],
                "required_checks": ["require_fresh_data"],
                "risk_budget_multiplier": 0.5,
                "max_budget_multiplier": 0.5,
            }
        if discipline_id == "cost_simulation" or mode == "cost_evidence_repair":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "cost_drag",
                "entry_bias": "cost_verified_waiting_entry",
                "sizing_policy": "reduce_cost_weak_lane",
                "target_stop_review": (
                    "widen_expected_move_or_wait_for_price_improvement"
                ),
                "required_evidence": [
                    "fee",
                    "spread",
                    "slippage",
                    "tax_or_funding",
                    "net_pnl_after_costs",
                ],
                "required_checks": ["require_positive_net_edge"],
                "min_reward_risk": 1.8,
                "risk_budget_multiplier": 0.5,
                "max_budget_multiplier": 0.5,
            }
        if discipline_id == "capacity_analysis" or mode == "capacity_depth_check":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "capacity_depth",
                "entry_bias": "depth_checked_waiting_entry",
                "sizing_policy": "cap_by_capacity_until_depth_verified",
                "target_stop_review": "scale_by_depth_and_liquidity",
                "required_evidence": ["turnover", "depth", "spread"],
                "required_checks": ["require_capacity_check"],
                "risk_budget_multiplier": 0.5,
                "max_budget_multiplier": 0.5,
            }
        if discipline_id in {
            "overfit_validation",
            "walk_forward_analysis",
            "out_of_sample_test",
        } or mode == "backtest_wfa_oos_rebuild":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "research_revalidation",
                "entry_bias": "shadow_or_waiting_entry_until_validation_complete",
                "sizing_policy": "no_size_increase_until_wfa_oos_shadow_pass",
                "target_stop_review": "rebuild_backtest_wfa_oos_before_scale_up",
                "required_evidence": [
                    "backtest",
                    "walk_forward",
                    "out_of_sample",
                    "live_shadow",
                ],
                "required_checks": ["require_shadow_validation"],
                "min_reward_risk": 2.0,
                "risk_budget_multiplier": 0.25,
                "max_budget_multiplier": 0.25,
            }
        if discipline_id in {"stress_test", "regime_test"} or mode == "scenario_regime_replay":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "scenario_regime",
                "entry_bias": "regime_confirmed_waiting_entry",
                "sizing_policy": "regime_mismatch_probe_only",
                "target_stop_review": "reprice_for_current_regime",
                "required_evidence": ["current_regime", "scenario_stress"],
                "required_checks": ["require_regime_match"],
                "min_reward_risk": 1.5,
                "risk_budget_multiplier": 0.5,
                "max_budget_multiplier": 0.5,
            }
        if discipline_id in {
            "monte_carlo",
            "kelly_sizing",
            "mdd_limit",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "profit_factor",
            "recovery_factor",
            "risk_of_ruin",
        } or mode == "risk_budget_recalibration":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "risk_adjusted_sizing",
                "entry_bias": "fractional_kelly_probe_entry",
                "sizing_policy": "fractional_kelly_mdd_ruin_probe_only",
                "target_stop_review": "cap_risk_before_increasing_position_size",
                "required_evidence": [
                    "fractional_kelly",
                    "mdd_usage",
                    "ruin_probability",
                    "lane_confidence",
                ],
                "required_checks": ["require_risk_budget_review"],
                "min_reward_risk": 1.7,
                "max_stop_risk_pct": 3.0,
                "risk_budget_multiplier": risk_multiplier,
                "max_budget_multiplier": risk_multiplier,
            }
        if discipline_id in {"correlation", "factor_exposure"} or mode == "portfolio_exposure_check":
            return {
                "scale_blocker": common_scale_blocker,
                "validation_effect_profile": "portfolio_concentration",
                "entry_bias": "concentration_checked_waiting_entry",
                "sizing_policy": "cap_correlated_exposure",
                "target_stop_review": "review_regime_correlation_factor_exposure",
                "required_evidence": [
                    "correlation_cluster",
                    "factor_exposure",
                    "sector_or_beta_bucket",
                ],
                "required_checks": ["require_exposure_review"],
                "risk_budget_multiplier": 0.5,
                "max_budget_multiplier": 0.5,
            }
        return {
            "scale_blocker": common_scale_blocker,
            "validation_effect_profile": "validation_repair",
            "entry_bias": "waiting_or_probe_until_validation_repairs",
            "sizing_policy": "no_size_increase_until_validation_repair_clears",
            "target_stop_review": "review_validation_repair_before_entry",
            "required_evidence": ["fresh_validation_result"],
            "required_checks": ["require_validation_review"],
            "risk_budget_multiplier": risk_multiplier,
            "max_budget_multiplier": risk_multiplier,
        }

    by_key: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") != "trading_validation_signal":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        venue = _normalize_memory_scope(payload.get("venue"))
        if normalized_target and venue not in {"", normalized_target, "core"}:
            continue
        remediation = (
            payload.get("remediation_plan")
            if isinstance(payload.get("remediation_plan"), dict)
            else {}
        )
        work_queue = [
            row
            for row in list(remediation.get("work_queue") or [])
            if isinstance(row, dict)
        ]
        work_by_discipline = {
            _clean_text(row.get("discipline_id"), limit=80): row
            for row in work_queue
            if _clean_text(row.get("discipline_id"), limit=80)
        }
        weak_disciplines = [
            row
            for row in list(payload.get("weak_disciplines") or [])
            if isinstance(row, dict)
        ]
        for weak in weak_disciplines:
            discipline_id = _clean_text(weak.get("id"), limit=80)
            if not discipline_id:
                continue
            work = work_by_discipline.get(discipline_id, {})
            policy_id = (
                f"validation.{venue}.{discipline_id}"
                if venue
                else f"validation.{discipline_id}"
            )
            scorecard = scorecard_by_policy.get(policy_id) or scorecard_by_policy.get(
                f"validation.{discipline_id}"
            ) or {}
            repair_policy_id = (
                f"validation_repair.{venue}.{discipline_id}"
                if venue
                else f"validation_repair.core.{discipline_id}"
            )
            repair_action_id = _clean_text(
                work.get("repair_action_id"),
                limit=140,
            )
            repair_scorecard = (
                scorecard_by_policy.get(repair_policy_id)
                or repair_scorecard_by_action.get(repair_action_id)
                or {}
            )
            key = f"{venue or 'core'}:{discipline_id}"
            status = _clean_text(
                work.get("status") or weak.get("status"),
                limit=40,
            ).lower()
            priority = _clean_text(work.get("priority"), limit=20).lower() or (
                "p0" if status in {"fail", "failed", "missing"} else "p1"
            )
            constraints = backlog_constraints(
                discipline_id=discipline_id,
                work=work,
                status=status,
            )
            pass_path = _compact_validation_pass_path(work.get("pass_path"))
            item = {
                "venue": venue or "core",
                "repair_action_id": repair_action_id,
                "discipline_id": discipline_id,
                "label": _clean_text(weak.get("label") or discipline_id, limit=120),
                "status": status,
                "priority": priority,
                "owner": _clean_text(work.get("owner"), limit=80) or "validation_lab",
                "cadence": _clean_text(work.get("cadence"), limit=80)
                or "next_validation_run",
                "automation_hook": _clean_text(
                    work.get("automation_hook"),
                    limit=120,
                ),
                "execution_weight": _clean_text(
                    work.get("execution_weight"),
                    limit=80,
                ),
                "lane_policy_hint": _clean_text(
                    work.get("lane_policy_hint")
                    or scorecard.get("lane_policy_hint")
                    or weak.get("action"),
                    limit=160,
                ),
                "blocks_scaling": _clean_text(
                    work.get("blocks_scaling") or scorecard.get("blocks_scaling"),
                    limit=160,
                ),
                "blocks_new_entries": _clean_text(
                    work.get("blocks_new_entries"),
                    limit=160,
                ),
                "runner_hint": _clean_text(
                    work.get("runner_hint"),
                    limit=220,
                ),
                "verification_artifact": _clean_text(
                    work.get("verification_artifact"),
                    limit=260,
                ),
                "exit_criteria": _clean_text(
                    work.get("exit_criteria")
                    or f"{discipline_id} returns to pass in the next validation run.",
                    limit=220,
                ),
                "validation_mode": _clean_text(
                    work.get("validation_mode"),
                    limit=100,
                ),
                "allowed_entry_posture": _clean_text(
                    work.get("allowed_entry_posture"),
                    limit=120,
                ),
                "live_shadow_required": bool(work.get("live_shadow_required")),
                "scale_up_blocked": bool(work.get("scale_up_blocked")),
                "evidence_targets": (
                    work.get("evidence_targets")
                    if isinstance(work.get("evidence_targets"), dict)
                    else {}
                ),
                "pass_current_gap": pass_path.get("current_gap"),
                "pass_collection_hook": pass_path.get("collection_hook"),
                "pass_criteria": pass_path.get("pass_criteria"),
                "pass_required_evidence": pass_path.get("required_evidence"),
                "pass_jue_behavior_until_pass": pass_path.get(
                    "jue_behavior_until_pass"
                ),
                "pass_m1_runtime_profile": pass_path.get("m1_runtime_profile"),
                **constraints,
                "policy_id": policy_id,
                "policy_status": _clean_text(scorecard.get("status"), limit=80),
                "sample_count": _safe_int(scorecard.get("sample_count")),
                "confidence": _safe_float(scorecard.get("confidence")),
                "repair_policy_id": repair_policy_id,
                "last_repair_status": _clean_text(
                    repair_scorecard.get("repair_status")
                    or repair_scorecard.get("status"),
                    limit=80,
                ),
                "last_repair_policy_status": _clean_text(
                    repair_scorecard.get("status"),
                    limit=80,
                ),
                "last_repair_action": _clean_text(
                    repair_scorecard.get("action"),
                    limit=80,
                ),
                "last_repair_confidence": _safe_float(
                    repair_scorecard.get("confidence")
                ),
                "last_repair_automation_hook": _clean_text(
                    repair_scorecard.get("automation_hook")
                    or repair_scorecard.get("artifact"),
                    limit=120,
                ),
                "last_repair_execution_weight": _clean_text(
                    repair_scorecard.get("execution_weight"),
                    limit=80,
                ),
                "last_repair_reason": _clean_text(
                    repair_scorecard.get("reason"),
                    limit=260,
                ),
                "event_key": _clean_text(event.get("event_key"), limit=160),
                "created_at": _clean_text(event.get("created_at"), limit=80),
            }
            previous = by_key.get(key)
            current_rank = (
                priority_rank.get(item.get("priority"), 9),
                status_rank.get(item.get("status"), 9),
                -_safe_int(item.get("sample_count")),
            )
            previous_rank = (
                priority_rank.get(previous.get("priority"), 9),
                status_rank.get(previous.get("status"), 9),
                -_safe_int(previous.get("sample_count")),
            ) if previous else None
            if previous and previous_rank <= current_rank:
                continue
            by_key[key] = {
                field: value
                for field, value in item.items()
                if value not in (None, "", [], {})
            }
    for scorecard in scorecards:
        if not isinstance(scorecard, dict):
            continue
        policy_id = _clean_text(scorecard.get("policy_id"), limit=160)
        if not policy_id.startswith("manager_contract_error."):
            continue
        parts = policy_id.split(".", 2)
        venue = _normalize_memory_scope(scorecard.get("venue"))
        error_key = _clean_text(scorecard.get("latest_error"), limit=120)
        if len(parts) == 3:
            venue = venue or _normalize_memory_scope(parts[1])
            error_key = error_key or _clean_text(parts[2], limit=120)
        if normalized_target and venue not in {"", normalized_target, "core"}:
            continue
        if policy_id in manager_contract_recovered_by_error_policy:
            continue
        status = _clean_text(scorecard.get("status"), limit=40).lower()
        if status in {"resolved", "retired", "inactive"}:
            continue
        contract = _clean_text(scorecard.get("contract"), limit=160)
        impacted_symbols = _compact_text_list(
            scorecard.get("impacted_symbols"),
            limit=8,
            item_limit=40,
        )
        priority = "p1" if status == "active_caution" else "p2"
        item = {
            "venue": venue or "core",
            "repair_action_id": (
                f"memory_contract_repair.{venue or 'core'}.{error_key or 'unknown'}"
            ),
            "discipline_id": "memory_contract",
            "label": "manager memory contract repair",
            "status": "missing",
            "priority": priority,
            "owner": "investment_memory",
            "cadence": "next_manager_run",
            "automation_hook": "rerun_manager_with_memory_contract_attention",
            "execution_weight": "lightweight",
            "lane_policy_hint": "resolve_memory_contract_before_size_expansion",
            "blocks_scaling": "no_size_increase_until_memory_contract_repaired",
            "blocks_new_entries": "cite_or_reject_memory_before_new_entries",
            "runner_hint": (
                "Next manager run must cite the memory packet, reject it with "
                "a reason, or choose hold/watch when memory is insufficient."
            ),
            "verification_artifact": "manager_run.response.memory_contract_resolution",
            "exit_criteria": (
                "A fresh manager response records memory_contract_resolution "
                "for the impacted symbol or candidate set."
            ),
            "validation_mode": "memory_contract_repair",
            "allowed_entry_posture": "waiting_or_probe_with_memory_resolution",
            "scale_up_blocked": True,
            "scale_blocker": "memory_contract_repair",
            "validation_effect_profile": "memory_contract",
            "entry_bias": "memory_contract_resolved_probe_or_wait",
            "sizing_policy": "no_size_increase_until_memory_contract_repaired",
            "target_stop_review": "cite_or_reject_memory_before_target_stop",
            "required_evidence": [
                "memory_contract_resolution",
                "memory_evidence_reference",
                "rejection_reason_if_ignored",
            ],
            "required_checks": ["require_memory_contract_resolution"],
            "risk_budget_multiplier": 0.5,
            "max_budget_multiplier": 0.5,
            "policy_id": policy_id,
            "policy_status": status,
            "sample_count": _safe_int(scorecard.get("sample_count")),
            "confidence": _safe_float(scorecard.get("confidence")),
            "repair_policy_id": f"memory_contract_repair.{venue or 'core'}.{error_key or 'unknown'}",
            "memory_contract": contract,
            "memory_contract_error": error_key,
            "memory_contract_rows": _compact_memory_contract_rows(
                scorecard.get("memory_contract_rows"),
                limit=6,
            ),
            "impacted_symbols": impacted_symbols,
            "last_repair_reason": _clean_text(scorecard.get("reason"), limit=260),
            "updated_at": _clean_text(scorecard.get("updated_at"), limit=80),
        }
        key = f"manager_contract_error:{venue or 'core'}:{error_key or policy_id}"
        by_key[key] = {
            field: value
            for field, value in item.items()
            if value not in (None, "", [], {})
        }
    for scorecard in scorecards:
        if not isinstance(scorecard, dict):
            continue
        policy_id = _clean_text(scorecard.get("policy_id"), limit=160)
        if not policy_id.startswith("jue_wiki_selection."):
            continue
        status = _clean_text(scorecard.get("status"), limit=40).lower()
        if status not in {"active_caution", "active_preference"}:
            continue
        penalty_counts = (
            scorecard.get("penalty_counts")
            if isinstance(scorecard.get("penalty_counts"), dict)
            else {}
        )
        stale_count = _safe_int(penalty_counts.get("freshness:stale"))
        if stale_count <= 0:
            continue
        parts = policy_id.split(".", 2)
        venue = _normalize_memory_scope(scorecard.get("venue"))
        reason_key = _clean_text(scorecard.get("latest_reason"), limit=120)
        if len(parts) == 3:
            venue = venue or _normalize_memory_scope(parts[1])
            reason_key = reason_key or _clean_text(parts[2], limit=120)
        if normalized_target and venue not in {"", normalized_target, "core"}:
            continue
        selected_page_ids = _compact_text_list(
            scorecard.get("selected_page_ids"),
            limit=8,
            item_limit=120,
        )
        repair_token = re.sub(
            r"_+",
            "_",
            re.sub(
                r"[^a-z0-9_]+",
                "_",
                _clean_text(reason_key or policy_id, limit=120).lower(),
            ),
        ).strip("_") or "unknown"
        item = {
            "venue": venue or "core",
            "repair_action_id": (
                f"jue_wiki_refresh.{venue or 'core'}.{repair_token}"
            ),
            "discipline_id": "wiki_freshness",
            "label": "jue wiki freshness repair",
            "status": "warn",
            "priority": "p1",
            "owner": "jue_wiki",
            "cadence": "before_next_manager_run",
            "automation_hook": "refresh_stale_jue_wiki_pages",
            "execution_weight": "lightweight",
            "lane_policy_hint": "refresh_or_cross_check_wiki_before_entry",
            "blocks_scaling": "no_size_increase_until_wiki_freshness_repaired",
            "blocks_new_entries": "fresh_wiki_or_live_cross_check_before_new_entries",
            "runner_hint": (
                "Refresh selected stale wiki pages or explicitly cross-check them "
                "against live research before relying on the memory."
            ),
            "verification_artifact": "jue_wiki.context_pack.freshness_summary",
            "exit_criteria": (
                "A fresh manager prompt records no freshness:stale penalty for "
                "the selected wiki pages, or cites a live cross-check resolution."
            ),
            "validation_mode": "wiki_freshness_repair",
            "allowed_entry_posture": "waiting_or_probe_with_fresh_wiki_cross_check",
            "scale_up_blocked": True,
            "scale_blocker": "jue_wiki_freshness_repair",
            "validation_effect_profile": "wiki_freshness",
            "entry_bias": "fresh_wiki_cross_checked_probe_or_wait",
            "sizing_policy": "no_size_increase_until_wiki_freshness_repaired",
            "target_stop_review": "refresh_or_cross_check_wiki_before_target_stop",
            "required_evidence": [
                "fresh_jue_wiki_context",
                "selection_audit_resolution",
                "live_cross_check",
            ],
            "required_checks": ["require_fresh_wiki_context"],
            "risk_budget_multiplier": 0.5,
            "max_budget_multiplier": 0.5,
            "policy_id": policy_id,
            "policy_status": status,
            "sample_count": _safe_int(scorecard.get("sample_count")),
            "confidence": _safe_float(scorecard.get("confidence")),
            "repair_policy_id": (
                f"jue_wiki_refresh.{venue or 'core'}.{repair_token}"
            ),
            "selected_page_ids": selected_page_ids,
            "penalty_counts": penalty_counts,
            "last_repair_reason": _clean_text(scorecard.get("reason"), limit=260),
            "updated_at": _clean_text(scorecard.get("updated_at"), limit=80),
        }
        key = f"jue_wiki_selection:{venue or 'core'}:{reason_key or policy_id}"
        by_key[key] = {
            field: value
            for field, value in item.items()
            if value not in (None, "", [], {})
        }
    items = sorted(
        by_key.values(),
        key=lambda row: (
            priority_rank.get(row.get("priority"), 9),
            status_rank.get(row.get("status"), 9),
            -_safe_int(row.get("sample_count")),
            str(row.get("created_at") or ""),
        ),
    )[: max(int(limit), 1)]
    recovered_items = [
        {
            field: value
            for field, value in row.items()
            if value not in (None, "", [], {})
        }
        for row in manager_contract_recovered_by_error_policy.values()
    ][: max(int(limit), 1)]
    if not items:
        return {
            "status": "clear",
            "items": [],
            "manager_contract_recovered": recovered_items,
        }
    return {
        "status": "needs_repair",
        "item_count": len(items),
        "items": items,
        "manager_contract_recovered": recovered_items,
        "instruction": (
            "Treat these as repair backlog before size expansion: reduce or wait on "
            "affected lanes until each exit_criteria is satisfied by fresh validation."
        ),
    }


def _build_scoped_memory_payload(
    *,
    target_scope: str,
    insights: list[dict[str, Any]],
    reflections: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    if not normalized_target:
        return {"status": "unscoped"}
    grouped: dict[str, list[dict[str, Any]]] = {
        "core": [],
        "local": [],
        "translated": [],
    }
    blocked_count = 0

    def add(item: dict[str, Any]) -> None:
        nonlocal blocked_count
        bucket = _scope_bucket(
            target_scope=normalized_target,
            item_scope=str(item.get("scope") or "core"),
            transferability=str(item.get("transferability") or "direct"),
            memory_type=str(item.get("memory_type") or ""),
        )
        if bucket == "blocked":
            blocked_count += 1
            return
        grouped.setdefault(bucket, []).append(item)

    for row in insights[:80]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        add(
            _scoped_memory_item(
                memory_type=str(row.get("memory_type") or "general"),
                key=row.get("key"),
                summary_md=row.get("summary_md"),
                confidence=row.get("confidence"),
                evidence=evidence,
                updated_at=row.get("updated_at"),
            )
        )

    for row in reflections[:20]:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        scope = _normalize_memory_scope(metrics.get("memory_scope") or metrics.get("venue"))
        evidence = [
            {
                "memory_scope": scope or _infer_memory_scope(
                    memory_type="block_reflection",
                    key=row.get("symbol") or row.get("block_id"),
                ),
                "transferability": metrics.get("transferability") or "translated",
            }
        ]
        add(
            _scoped_memory_item(
                memory_type="block_reflection",
                key=row.get("block_id"),
                summary_md=row.get("lesson_md"),
                confidence=0.72,
                evidence=evidence,
                updated_at=row.get("updated_at"),
                extra={
                    "symbol": row.get("symbol"),
                    "pnl_pct": row.get("pnl_pct"),
                    "exit_reason": row.get("exit_reason"),
                },
            )
        )

    for row in scorecards[:20]:
        add(
            _scoped_memory_item(
                memory_type="policy_scorecard",
                key=row.get("policy_id"),
                summary_md=row.get("reason"),
                confidence=row.get("confidence"),
                evidence=_policy_scope_evidence(row, memory_type="policy_scorecard"),
                updated_at=row.get("updated_at"),
                extra={
                    "status": row.get("status"),
                    "sample_count": row.get("sample_count"),
                    "expectancy_pct": row.get("expectancy_pct"),
                },
            )
        )

    for row in rules[:20]:
        add(
            _scoped_memory_item(
                memory_type="policy_rule",
                key=row.get("rule_id") or row.get("policy_id"),
                summary_md=row.get("reason"),
                confidence=(row.get("source_scorecard") or {}).get("confidence")
                if isinstance(row.get("source_scorecard"), dict)
                else 0.0,
                evidence=_policy_scope_evidence(row, memory_type="policy_rule"),
                updated_at=row.get("created_at") or row.get("activated_at"),
                extra={
                    "status": row.get("status"),
                    "action": row.get("action"),
                    "effect": row.get("effect"),
                },
            )
        )

    return {
        "status": "ok",
        "target_scope": normalized_target,
        "usage_policy": (
            "Use core and local memories as primary decision context. Use translated "
            "cross-venue memories as caution or calibration, not as direct asset evidence."
        ),
        "core": grouped["core"][:8],
        "local": grouped["local"][:10],
        "translated": grouped["translated"][:8],
        "blocked_count": blocked_count,
    }


def _compact_symbol_analysis(
    row: dict[str, Any],
    *,
    summary_limit: int = 500,
) -> dict[str, Any]:
    risks = row.get("risks") if isinstance(row.get("risks"), list) else []
    data_gaps = row.get("data_gaps") if isinstance(row.get("data_gaps"), list) else []
    payload: dict[str, Any] = {
        "created_at": _clean_text(row.get("created_at"), limit=80),
        "trigger": _clean_text(row.get("trigger"), limit=80),
        "stance": _clean_text(row.get("stance"), limit=80),
        "confidence": _safe_float(row.get("confidence")),
        "summary": _clean_text(row.get("summary"), limit=summary_limit),
        "risks": [_clean_text(item, limit=160) for item in risks[:3]],
        "data_gaps": [_clean_text(item, limit=160) for item in data_gaps[:3]],
    }
    return {
        key: value
        for key, value in payload.items()
        if value not in ("", [], None)
    }


def _compact_lifecycle_evidence(value: Any) -> list[dict[str, Any] | str]:
    rows = value if isinstance(value, list) else []
    compact: list[dict[str, Any] | str] = []
    allowed_keys = {
        "source",
        "id",
        "report_id",
        "block_id",
        "symbol",
        "title",
        "status",
        "url",
        "published_at",
        "created_at",
        "updated_at",
        "summary",
        "price",
        "change_pct",
        "score",
        "confidence",
    }
    for row in rows[:8]:
        if isinstance(row, str):
            text = _clean_text(row, limit=240)
            if text:
                compact.append(text)
            continue
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {}
        for key, value_row in row.items():
            if key not in allowed_keys:
                continue
            if isinstance(value_row, (int, float)):
                item[key] = value_row
            elif value_row is not None:
                item[key] = _clean_text(value_row, limit=240)
        if item:
            compact.append(item)
    return compact


def _compact_lifecycle_artifact(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
    compact_evidence = _compact_lifecycle_evidence(evidence)
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    block_implications = (
        payload.get("block_implications")
        if isinstance(payload.get("block_implications"), list)
        else []
    )
    item: dict[str, Any] = {
        "artifact_id": _clean_text(row.get("artifact_id"), limit=160),
        "artifact_type": _clean_text(row.get("artifact_type"), limit=80),
        "workflow_id": _clean_text(row.get("workflow_id"), limit=120),
        "symbol": _clean_text(row.get("symbol"), limit=40),
        "title": _clean_text(row.get("title"), limit=180),
        "summary_md": _truncate(row.get("summary_md"), 700),
        "updated_at": _clean_text(row.get("updated_at"), limit=80),
        "evidence_count": len(evidence),
    }
    if compact_evidence:
        item["evidence"] = compact_evidence
    if block_implications:
        item["block_implications"] = block_implications[:6]
    return {
        key: value
        for key, value in item.items()
        if value not in ("", [], None)
    }


def _symbol_analysis_lifecycle_artifact(row: dict[str, Any]) -> dict[str, Any]:
    symbol = _clean_text(row.get("symbol"), limit=40)
    analysis_id = _clean_text(row.get("id") or row.get("created_at"), limit=80)
    summary = _clean_text(row.get("summary"), limit=900)
    evidence: list[dict[str, Any]] = [
        {
            "source": "symbol_analysis_history",
            "symbol": symbol,
            "status": _clean_text(row.get("status"), limit=40),
            "summary": summary,
            "created_at": _clean_text(row.get("created_at"), limit=80),
        }
    ]
    snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
    quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    if quote:
        evidence.append(
            {
                "source": "quote",
                "symbol": _clean_text(quote.get("symbol") or symbol, limit=40),
                "status": _clean_text(quote.get("status"), limit=40),
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
            }
        )
    return {
        "artifact_id": f"symbol_analysis:{symbol}:{analysis_id or 'latest'}",
        "artifact_type": "symbol_analysis",
        "workflow_id": "instant_symbol_analysis",
        "symbol": symbol,
        "title": f"{_clean_text(row.get('name'), limit=80) or symbol} 종목분석 기억",
        "summary_md": summary,
        "payload": {
            "stance": _clean_text(row.get("stance"), limit=80),
            "confidence": _safe_float(row.get("confidence")),
            "block_implications": [
                {
                    "action": _clean_text(row.get("stance") or "watch_add", limit=80),
                    "confidence": _safe_float(row.get("confidence")),
                    "reason": summary,
                }
            ],
        },
        "evidence": evidence,
        "updated_at": _clean_text(row.get("updated_at"), limit=80),
    }


def _compact_text_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if isinstance(value, str):
        values: list[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    out: list[str] = []
    for raw_value in values[: max(int(limit), 0)]:
        text = _clean_text(raw_value, limit=item_limit)
        if text and text not in out:
            out.append(text)
    return out


def _compact_memory_contract_rows(value: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value[: max(int(limit), 0)]:
        if not isinstance(row, dict):
            continue
        symbol = _clean_text(row.get("symbol"), limit=40)
        if not symbol:
            continue
        compact = {
            "symbol": symbol,
            "status": _clean_text(row.get("status"), limit=40),
            "contracts": _compact_text_list(
                row.get("contracts"),
                limit=4,
                item_limit=160,
            ),
            "errors": _compact_text_list(
                row.get("errors"),
                limit=4,
                item_limit=180,
            ),
            "resolution_modes": _compact_text_list(
                row.get("resolution_modes"),
                limit=4,
                item_limit=80,
            ),
        }
        rows.append(compact)
    return rows


def _compact_policy_summaries_for_budget(
    rows: Any,
    *,
    limit: int,
    summary_limit: int,
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    def priority(row: dict[str, Any]) -> tuple[int, int, float]:
        scorecard = row.get("scorecard") if isinstance(row.get("scorecard"), dict) else row
        policy_id = _clean_text(
            row.get("policy_id") or scorecard.get("policy_id"),
            limit=180,
        )
        status = _clean_text(
            row.get("status") or row.get("strength") or scorecard.get("status"),
            limit=80,
        )
        status_rank = {
            "active_preference": 0,
            "active_caution": 1,
            "candidate": 2,
        }.get(status, 3)
        scope = _normalize_memory_scope(
            row.get("scope")
            or row.get("memory_scope")
            or scorecard.get("scope")
            or scorecard.get("memory_scope")
        )
        scope_rank = 0 if scope and scope != "core" else 1
        family_rank = 3
        if policy_id.startswith("manager_contract_error."):
            family_rank = 0
        elif policy_id.startswith("period_memory_coverage."):
            family_rank = 0
        elif policy_id.startswith("jue_wiki_execution_hint."):
            family_rank = 1
        elif policy_id.startswith(("validation.", "lane_scale.")):
            family_rank = 2
        return (
            scope_rank,
            status_rank,
            family_rank,
            -_safe_float(row.get("confidence") or scorecard.get("confidence")),
        )

    sorted_rows = sorted(
        [row for row in list(rows or []) if isinstance(row, dict)],
        key=priority,
    )
    for row in sorted_rows:
        if not isinstance(row, dict):
            continue
        scorecard = row.get("scorecard") if isinstance(row.get("scorecard"), dict) else row
        gap_count_value = (
            row.get("period_memory_gap_count")
            if row.get("period_memory_gap_count") is not None
            else scorecard.get("period_memory_gap_count")
        )
        override_count_value = (
            row.get("period_memory_override_count")
            if row.get("period_memory_override_count") is not None
            else scorecard.get("period_memory_override_count")
        )
        contract_gap_count_value = (
            row.get("period_memory_contract_gap_count")
            if row.get("period_memory_contract_gap_count") is not None
            else scorecard.get("period_memory_contract_gap_count")
        )
        missing_metadata = _compact_text_list(
            row.get("period_memory_missing_metadata")
            or scorecard.get("period_memory_missing_metadata"),
            limit=6,
            item_limit=80,
        )
        repair_actions = _compact_text_list(
            row.get("period_memory_repair_actions")
            or scorecard.get("period_memory_repair_actions"),
            limit=6,
            item_limit=160,
        )
        audit_resolutions = _compact_text_list(
            row.get("metadata_contract_audit_resolutions")
            or scorecard.get("metadata_contract_audit_resolutions"),
            limit=6,
            item_limit=180,
        )
        repair_notes = _compact_text_list(
            row.get("metadata_contract_repair_notes")
            or scorecard.get("metadata_contract_repair_notes"),
            limit=4,
            item_limit=300,
        )
        compact_row = {
            "policy_id": _clean_text(row.get("policy_id") or scorecard.get("policy_id"), limit=160),
            "scope": _clean_text(row.get("scope") or scorecard.get("scope"), limit=40),
            "transferability": _clean_text(row.get("transferability"), limit=40),
            "source": _clean_text(row.get("source") or scorecard.get("source"), limit=80),
            "status": _clean_text(
                row.get("status") or row.get("strength") or scorecard.get("status"),
                limit=60,
            ),
            "action": _clean_text(row.get("action") or scorecard.get("action"), limit=40),
            "sample_count": _safe_int(scorecard.get("sample_count")),
            "win_rate": _safe_float(scorecard.get("win_rate")),
            "avg_pnl_pct": _safe_float(scorecard.get("avg_pnl_pct")),
            "expectancy_pct": _safe_float(scorecard.get("expectancy_pct")),
            "confidence": _safe_float(row.get("confidence") or scorecard.get("confidence")),
            "period_memory_status": _clean_text(
                row.get("period_memory_status") or scorecard.get("period_memory_status"),
                limit=80,
            ),
            "period_memory_gap_count": (
                _safe_int(gap_count_value)
                if gap_count_value not in (None, "")
                else None
            ),
            "period_memory_override_count": (
                _safe_int(override_count_value)
                if override_count_value not in (None, "")
                else None
            ),
            "period_memory_contract_gap_count": (
                _safe_int(contract_gap_count_value)
                if contract_gap_count_value not in (None, "")
                else None
            ),
            "period_memory_missing_metadata": missing_metadata,
            "period_memory_repair_actions": repair_actions,
            "metadata_contract_audit_resolutions": audit_resolutions,
            "metadata_contract_repair_notes": repair_notes,
            "reason": _truncate(row.get("reason") or scorecard.get("reason"), summary_limit),
        }
        compact.append(
            {
                key: value
                for key, value in compact_row.items()
                if value not in ("", [], None)
            }
        )
        if len(compact) >= max(int(limit), 0):
            break
    return compact


def _compact_jue_wiki_selection_memory(
    rows: Any,
    *,
    target_scope: str | None,
    limit: int = 4,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    selected: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") != "jue_wiki_selection_audit":
            continue
        policy_id = _clean_text(row.get("policy_id"), limit=180)
        if not policy_id:
            continue
        row_scope = _policy_row_scope(row)
        row_transferability = _policy_row_transferability(
            row,
            memory_type="policy_scorecard",
        )
        status = _clean_text(row.get("status"), limit=80)
        resolution_status = _clean_text(row.get("resolution_status"), limit=80)
        if status in {"resolved", "inactive", "retired"}:
            continue
        if _jue_wiki_action_reference_resolution_status(resolution_status):
            continue
        if normalized_target:
            bucket = _scope_bucket(
                target_scope=normalized_target,
                item_scope=row_scope,
                transferability=row_transferability,
                memory_type="policy_scorecard",
            )
            if bucket not in {"core", "local"}:
                continue
        selected_page_ids = _compact_text_list(
            row.get("selected_page_ids"),
            limit=6,
            item_limit=120,
        )
        penalty_counts = (
            row.get("penalty_counts") if isinstance(row.get("penalty_counts"), dict) else {}
        )
        top_pages: list[dict[str, Any]] = []
        for page in list(row.get("top_pages") or [])[:3]:
            if not isinstance(page, dict):
                continue
            page_id = _clean_text(page.get("page_id"), limit=120)
            if not page_id:
                continue
            top_pages.append(
                {
                    key: value
                    for key, value in {
                        "page_id": page_id,
                        "rank": _safe_int(page.get("rank")),
                        "selection_reasons": _compact_text_list(
                            page.get("selection_reasons"),
                            limit=4,
                            item_limit=120,
                        ),
                    }.items()
                    if value not in ("", [], {}, None, 0)
                }
            )
        item = {
            "policy_id": policy_id,
            "scope": row_scope or normalized_target,
            "transferability": row_transferability,
            "status": row.get("status"),
            "sample_count": row.get("sample_count"),
            "confidence": row.get("confidence"),
            "primary_reason": _clean_text(row.get("latest_reason"), limit=160),
            "selected_page_count": row.get("selected_page_count"),
            "selected_page_ids": selected_page_ids,
            "reason_counts": row.get("reason_counts") or {},
            "penalty_counts": penalty_counts,
            "workflow_ids": _compact_text_list(
                row.get("workflow_ids"),
                limit=4,
                item_limit=120,
            ),
            "contract_ids": _compact_text_list(
                row.get("contract_ids"),
                limit=8,
                item_limit=160,
            ),
            "application_guidance": _jue_wiki_selection_application_guidance(
                penalty_counts=penalty_counts,
                selected_page_ids=selected_page_ids,
            ),
            "top_pages": top_pages,
            "summary_md": _truncate(row.get("reason"), 360),
        }
        selected.append(
            {
                key: value
                for key, value in item.items()
                if value not in ("", [], {}, None)
            }
        )
        if len(selected) >= max(int(limit), 1):
            break
    if not selected:
        return {
            "status": "missing",
            "target_scope": normalized_target or "all",
            "item_count": 0,
            "items": [],
        }
    return {
        "status": "available",
        "target_scope": normalized_target or "all",
        "item_count": len(selected),
        "items": selected,
    }


def _jue_wiki_selection_application_guidance(
    *,
    penalty_counts: dict[str, Any],
    selected_page_ids: list[str],
) -> dict[str, Any]:
    stale_count = _safe_int(penalty_counts.get("freshness:stale"))
    if stale_count < 2:
        return {}
    return {
        "status": "freshness_repair_required",
        "manager_instruction": (
            "refresh_or_cross_check_selected_wiki_before_size_increase"
        ),
        "required_evidence": [
            "fresh_jue_wiki_context",
            "selection_audit_resolution",
            "live_cross_check",
        ],
        "cross_check_page_ids": selected_page_ids[:6],
    }


def _compact_jue_wiki_context_gap_memory(
    rows: Any,
    *,
    target_scope: str | None,
    limit: int = 4,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    selected: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") != "jue_wiki_context_gap":
            continue
        policy_id = _clean_text(row.get("policy_id"), limit=180)
        if not policy_id:
            continue
        row_scope = _policy_row_scope(row)
        row_transferability = _policy_row_transferability(
            row,
            memory_type="policy_scorecard",
        )
        status = _clean_text(row.get("status"), limit=80)
        resolution_status = _clean_text(row.get("resolution_status"), limit=80)
        if status in {"resolved", "inactive", "retired"}:
            continue
        if _jue_wiki_action_reference_resolution_status(resolution_status):
            continue
        if normalized_target:
            bucket = _scope_bucket(
                target_scope=normalized_target,
                item_scope=row_scope,
                transferability=row_transferability,
                memory_type="policy_scorecard",
            )
            if bucket not in {"core", "local"}:
                continue
        latest_reason = _clean_text(
            row.get("latest_reason") or row.get("latest_error"),
            limit=160,
        )
        item = {
            "policy_id": policy_id,
            "scope": row_scope or normalized_target,
            "transferability": row_transferability,
            "status": row.get("status"),
            "sample_count": row.get("sample_count"),
            "confidence": row.get("confidence"),
            "latest_reason": latest_reason,
            "wiki_status": _clean_text(row.get("wiki_status"), limit=80),
            "available": row.get("available"),
            "resolution_status": _clean_text(row.get("resolution_status"), limit=80),
            "blocker_count": row.get("blocker_count"),
            "workflow_ids": _compact_text_list(
                row.get("workflow_ids"),
                limit=4,
                item_limit=120,
            ),
            "contract_ids": _compact_text_list(
                row.get("contract_ids"),
                limit=8,
                item_limit=160,
            ),
            "application_guidance": _jue_wiki_context_gap_application_guidance(
                reason=latest_reason,
            ),
            "summary_md": _truncate(row.get("reason"), 360),
        }
        selected.append(
            {
                key: value
                for key, value in item.items()
                if value not in ("", [], {}, None)
            }
        )
        if len(selected) >= max(int(limit), 1):
            break
    if not selected:
        return {
            "status": "missing",
            "target_scope": normalized_target or "all",
            "item_count": 0,
            "items": [],
        }
    return {
        "status": "available",
        "target_scope": normalized_target or "all",
        "item_count": len(selected),
        "items": selected,
    }


def _jue_wiki_context_gap_application_guidance(*, reason: str) -> dict[str, Any]:
    return {
        "status": "context_gap_repair_required",
        "manager_instruction": (
            "verify_wiki_context_or_record_jue_wiki_context_gap_before_action"
        ),
        "required_evidence": [
            "fresh_jue_wiki_context",
            "jue_wiki_context_gap",
            "live_cross_check",
        ],
        "gap_reason": reason,
    }


def _compact_jue_wiki_action_reference_memory(
    rows: Any,
    *,
    target_scope: str | None,
    limit: int = 4,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    selected: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") != "jue_wiki_action_reference_gap":
            continue
        policy_id = _clean_text(row.get("policy_id"), limit=180)
        if not policy_id:
            continue
        row_scope = _policy_row_scope(row)
        row_transferability = _policy_row_transferability(
            row,
            memory_type="policy_scorecard",
        )
        status = _clean_text(row.get("status"), limit=80)
        resolution_status = _clean_text(row.get("resolution_status"), limit=80)
        if status in {"resolved", "inactive", "retired"}:
            continue
        if _jue_wiki_action_reference_resolution_status(resolution_status):
            continue
        if normalized_target:
            bucket = _scope_bucket(
                target_scope=normalized_target,
                item_scope=row_scope,
                transferability=row_transferability,
                memory_type="policy_scorecard",
            )
            if bucket not in {"core", "local"}:
                continue
        latest_status = _clean_text(row.get("latest_status"), limit=80)
        memory_status = _clean_text(row.get("memory_status"), limit=80)
        recovery_status = _clean_text(row.get("recovery_status"), limit=80)
        recovery_latest_resolution_status = _clean_text(
            row.get("recovery_latest_resolution_status"),
            limit=80,
        )
        recovery_open_gap_count = _safe_int(row.get("recovery_open_gap_count"))
        missing_actions = _compact_jue_wiki_missing_actions(
            row.get("missing_actions"),
            limit=4,
        )
        item = {
            "policy_id": policy_id,
            "scope": row_scope or normalized_target,
            "transferability": row_transferability,
            "status": row.get("status"),
            "sample_count": row.get("sample_count"),
            "confidence": row.get("confidence"),
            "latest_status": latest_status,
            "memory_status": memory_status,
            "resolution_status": resolution_status,
            "action_count": row.get("action_count"),
            "reference_count": row.get("reference_count"),
            "reference_ratio": row.get("reference_ratio"),
            "missing_actions": missing_actions,
            "blocker_count": row.get("blocker_count"),
            "unresolved_memory_blocker_count": row.get(
                "unresolved_memory_blocker_count"
            ),
            "recovery_blocker_count": row.get("recovery_blocker_count"),
            "recovery_status": recovery_status,
            "recovery_open_gap_count": recovery_open_gap_count,
            "recovery_latest_resolution_status": recovery_latest_resolution_status,
            "recovery_latest_status": row.get("recovery_latest_status"),
            "workflow_ids": _compact_text_list(
                row.get("workflow_ids"),
                limit=4,
                item_limit=120,
            ),
            "contract_ids": _compact_text_list(
                row.get("contract_ids"),
                limit=8,
                item_limit=160,
            ),
            "application_guidance": _jue_wiki_action_reference_application_guidance(
                latest_status=latest_status,
                memory_status=memory_status,
                resolution_status=resolution_status,
                recovery_status=recovery_status,
                recovery_latest_resolution_status=recovery_latest_resolution_status,
                recovery_open_gap_count=recovery_open_gap_count,
                missing_actions=missing_actions,
            ),
            "summary_md": _truncate(row.get("reason"), 360),
        }
        selected.append(
            {
                key: value
                for key, value in item.items()
                if value not in ("", [], {}, None)
            }
        )
        if len(selected) >= max(int(limit), 1):
            break
    if not selected:
        return {
            "status": "missing",
            "target_scope": normalized_target or "all",
            "item_count": 0,
            "items": [],
        }
    return {
        "status": "available",
        "target_scope": normalized_target or "all",
        "item_count": len(selected),
        "items": selected,
    }


def _compact_jue_wiki_usage_contract_memory(
    rows: Any,
    *,
    target_scope: str | None,
    limit: int = 4,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    selected: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") != "jue_wiki_usage_contract_gap":
            continue
        policy_id = _clean_text(row.get("policy_id"), limit=180)
        if not policy_id:
            continue
        row_scope = _policy_row_scope(row)
        row_transferability = _policy_row_transferability(
            row,
            memory_type="policy_scorecard",
        )
        status = _clean_text(row.get("status"), limit=80)
        if status in {"resolved", "inactive", "retired"}:
            continue
        if normalized_target:
            bucket = _scope_bucket(
                target_scope=normalized_target,
                item_scope=row_scope,
                transferability=row_transferability,
                memory_type="policy_scorecard",
            )
            if bucket not in {"core", "local"}:
                continue
        latest_status = _clean_text(row.get("latest_status"), limit=80)
        item = {
            "policy_id": policy_id,
            "scope": row_scope or normalized_target,
            "transferability": row_transferability,
            "status": row.get("status"),
            "sample_count": row.get("sample_count"),
            "confidence": row.get("confidence"),
            "latest_status": latest_status,
            "action_count": row.get("action_count"),
            "resolution_count": row.get("resolution_count"),
            "resolution_ratio": row.get("resolution_ratio"),
            "missing_blocker_count": row.get("missing_blocker_count"),
            "partial_blocker_count": row.get("partial_blocker_count"),
            "workflow_ids": _compact_text_list(
                row.get("workflow_ids"),
                limit=4,
                item_limit=120,
            ),
            "contract_ids": _compact_text_list(
                row.get("contract_ids"),
                limit=8,
                item_limit=160,
            ),
            "application_guidance": _jue_wiki_usage_contract_application_guidance(
                latest_status=latest_status,
            ),
            "summary_md": _truncate(row.get("reason"), 360),
        }
        selected.append(
            {
                key: value
                for key, value in item.items()
                if value not in ("", [], {}, None)
            }
        )
        if len(selected) >= max(int(limit), 1):
            break
    if not selected:
        return {
            "status": "missing",
            "target_scope": normalized_target or "all",
            "item_count": 0,
            "items": [],
        }
    return {
        "status": "available",
        "target_scope": normalized_target or "all",
        "item_count": len(selected),
        "items": selected,
    }


def _jue_wiki_usage_contract_application_guidance(
    *,
    latest_status: str,
) -> dict[str, Any]:
    return {
        "status": "wiki_usage_contract_resolution_required",
        "manager_instruction": (
            "record_jue_wiki_usage_contract_resolution_on_wiki_influenced_actions"
        ),
        "required_evidence": [
            "jue_wiki_usage_contract_resolution",
            "live_quote_or_spread",
            "account_or_margin_state",
            "risk_gate",
            "fresh_research_or_quant_conflicts",
            "current_price_structure_or_orderbook_depth",
        ],
        "latest_status": latest_status or "missing",
    }


def _jue_wiki_action_reference_application_guidance(
    *,
    latest_status: str,
    memory_status: str = "",
    resolution_status: str = "",
    recovery_status: str = "",
    recovery_latest_resolution_status: str = "",
    recovery_open_gap_count: int = 0,
    missing_actions: Any = None,
) -> dict[str, Any]:
    compact_missing_actions = _compact_jue_wiki_missing_actions(
        missing_actions,
        limit=4,
    )

    def with_missing_actions(payload: dict[str, Any]) -> dict[str, Any]:
        if compact_missing_actions:
            payload["missing_actions"] = compact_missing_actions
        return payload

    if (
        recovery_open_gap_count > 0
        or recovery_status in {"open_gaps", "unresolved"}
        or recovery_latest_resolution_status == "unresolved"
    ):
        return with_missing_actions(
            {
                "status": "wiki_reference_recovery_required",
                "manager_instruction": (
                    "resolve_action_reference_recovery_before_next_decision"
                ),
                "required_evidence": [
                    "jue_wiki_action_reference_recovery",
                    "jue_wiki_reference_basis",
                    "jue_wiki_freshness_cross_check",
                    "jue_wiki_selection_resolution",
                    "explicit_non_wiki_basis_if_no_action",
                    "live_cross_check",
                ],
                "latest_status": latest_status or "missing",
                "recovery_status": recovery_status or "missing",
                "recovery_latest_resolution_status": (
                    recovery_latest_resolution_status or "missing"
                ),
                "recovery_open_gap_count": max(
                    _safe_int(recovery_open_gap_count),
                    0,
                ),
            }
        )
    if resolution_status == "unresolved":
        return with_missing_actions(
            {
                "status": "wiki_reference_repair_required",
                "manager_instruction": (
                    "resolve_action_reference_memory_before_next_decision"
                ),
                "required_evidence": [
                    "jue_wiki_reference_basis",
                    "jue_wiki_freshness_cross_check",
                    "jue_wiki_selection_resolution",
                    "explicit_non_wiki_basis_if_no_action",
                    "live_cross_check",
                ],
                "latest_status": latest_status or "missing",
                "memory_status": memory_status or "missing",
                "resolution_status": resolution_status,
            }
        )
    return with_missing_actions(
        {
            "status": "wiki_reference_repair_required",
            "manager_instruction": (
                "attach_jue_wiki_reference_or_explicitly_record_non_wiki_basis"
            ),
            "required_evidence": [
                "jue_wiki_freshness_cross_check",
                "jue_wiki_selection_resolution",
                "live_cross_check",
            ],
            "latest_status": latest_status or "missing",
        }
    )


def _jue_wiki_action_reference_resolution_status(value: Any) -> str:
    status = _clean_text(value, limit=80)
    if status in {"action_metadata", "hold_trigger", "resolved"}:
        return status
    return ""


def _compact_jue_wiki_missing_actions(value: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in list(value or [])[: max(int(limit), 1)]:
        if not isinstance(row, dict):
            continue
        compact = {
            "section": _clean_text(row.get("section"), limit=80),
            "block_id": _clean_text(row.get("block_id"), limit=80),
            "symbol": _clean_text(row.get("symbol"), limit=40),
            "market": _clean_text(row.get("market"), limit=40),
            "side": _clean_text(row.get("side"), limit=40),
            "lane": _clean_text(row.get("lane"), limit=80),
            "horizon": _clean_text(row.get("horizon"), limit=40),
            "qty": _safe_int(row.get("qty")),
            "reason": _clean_text(row.get("reason"), limit=160),
        }
        actions.append(
            {
                key: item
                for key, item in compact.items()
                if item not in ("", [], {}, None, 0)
            }
        )
    return [row for row in actions if row]


def _jue_wiki_action_reference_policy_ids_from_payload(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    policy_ids: list[str] = []

    def add(raw: Any) -> None:
        policy_id = _clean_text(raw, limit=180)
        if (
            policy_id.startswith("jue_wiki_action_reference_gap.")
            and policy_id not in policy_ids
        ):
            policy_ids.append(policy_id)

    def scan_memory(memory: Any) -> None:
        if not isinstance(memory, dict):
            return
        reference_memory = (
            memory.get("jue_wiki_action_reference_memory")
            if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
            else {}
        )
        for row in list(reference_memory.get("items") or [])[:12]:
            if not isinstance(row, dict):
                continue
            add(row.get("policy_id"))

    scan_memory(value.get("investment_memory"))
    scan_memory(value.get("memory"))
    latest = (
        value.get("latest_decision_input")
        if isinstance(value.get("latest_decision_input"), dict)
        else {}
    )
    scan_memory(latest.get("investment_memory"))
    scan_memory(latest.get("memory"))
    response = value.get("response") if isinstance(value.get("response"), dict) else {}
    latest_summary = (
        response.get("latest_input_summary")
        if isinstance(response.get("latest_input_summary"), dict)
        else {}
    )
    scan_memory(latest_summary.get("investment_memory"))
    scan_memory(latest_summary.get("memory"))
    decision_context = (
        value.get("decision_context")
        if isinstance(value.get("decision_context"), dict)
        else {}
    )
    scan_memory(decision_context.get("investment_memory"))
    scan_memory(decision_context.get("memory"))
    prompt = value.get("prompt") if isinstance(value.get("prompt"), dict) else {}
    scan_memory(prompt.get("investment_memory"))
    scan_memory(prompt.get("memory"))
    return policy_ids


def _jue_wiki_action_reference_recovery_summary(
    rows: Any,
    *,
    target_scope: str | None,
) -> dict[str, Any]:
    normalized_target = _normalize_memory_scope(target_scope)
    relevant: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") != "jue_wiki_action_reference_gap":
            continue
        if normalized_target:
            bucket = _scope_bucket(
                target_scope=normalized_target,
                item_scope=_policy_row_scope(row),
                transferability=_policy_row_transferability(
                    row,
                    memory_type="policy_scorecard",
                ),
                memory_type="policy_scorecard",
            )
            if bucket not in {"core", "local"}:
                continue
        relevant.append(row)
    if not relevant:
        return {
            "status": "missing",
            "memory_scope": normalized_target or "all",
            "open_gap_count": 0,
            "resolved_count": 0,
            "total_count": 0,
            "recovery_ratio": 0.0,
            "latest_resolution_status": "missing",
            "latest_status": "missing",
        }

    def is_resolved(row: dict[str, Any]) -> bool:
        status = _clean_text(row.get("status"), limit=80)
        resolution_status = _clean_text(row.get("resolution_status"), limit=80)
        return status == "resolved" or bool(
            _jue_wiki_action_reference_resolution_status(resolution_status)
        )

    resolved_count = sum(1 for row in relevant if is_resolved(row))
    total_count = len(relevant)
    open_gap_count = max(total_count - resolved_count, 0)
    latest = sorted(
        relevant,
        key=lambda row: _clean_text(row.get("updated_at"), limit=80),
        reverse=True,
    )[0]
    return {
        "status": (
            "resolved"
            if open_gap_count == 0
            else "open_gaps"
            if resolved_count
            else "unresolved"
        ),
        "memory_scope": normalized_target or "all",
        "open_gap_count": open_gap_count,
        "resolved_count": resolved_count,
        "total_count": total_count,
        "recovery_ratio": round(resolved_count / total_count, 3)
        if total_count
        else 0.0,
        "latest_resolution_status": _clean_text(
            latest.get("resolution_status"),
            limit=80,
        )
        or "missing",
        "latest_status": _clean_text(latest.get("latest_status"), limit=80)
        or _clean_text(latest.get("status"), limit=80)
        or "missing",
    }


def _compact_policy_rule_summaries_for_budget(
    rows: Any,
    *,
    limit: int,
    summary_limit: int,
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        source = (
            row.get("source_scorecard")
            if isinstance(row.get("source_scorecard"), dict)
            else {}
        )
        compact_row = {
            "policy_id": _clean_text(row.get("policy_id"), limit=160),
            "rule_id": _clean_text(row.get("rule_id"), limit=180),
            "scope": _clean_text(row.get("scope") or source.get("scope"), limit=40),
            "transferability": _clean_text(row.get("transferability"), limit=40),
            "status": _clean_text(row.get("status"), limit=60),
            "action": _clean_text(row.get("action"), limit=40),
            "condition": row.get("condition") if isinstance(row.get("condition"), dict) else {},
            "effect": row.get("effect") if isinstance(row.get("effect"), dict) else {},
            "reason": _truncate(row.get("reason"), summary_limit),
            "source_scorecard": {
                key: source.get(key)
                for key in (
                    "policy_id",
                    "scope",
                    "status",
                    "action",
                    "sample_count",
                    "avg_pnl_pct",
                    "expectancy_pct",
                    "confidence",
                    "period_memory_status",
                    "period_memory_contract_gap_count",
                    "period_memory_missing_metadata",
                    "period_memory_repair_actions",
                    "metadata_contract_audit_resolutions",
                    "metadata_contract_repair_notes",
                )
                if source.get(key) not in ("", [], None)
            },
        }
        compact.append(
            {
                key: value
                for key, value in compact_row.items()
                if value not in ("", {}, None)
            }
        )
        if len(compact) >= max(int(limit), 0):
            break
    return compact


def _enforce_context_pack_budget(
    payload: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    budget = max(limit + 600, 1600)
    if isinstance(payload.get("period_memory_coverage"), dict):
        payload["period_memory_coverage"] = _compact_period_memory_coverage_for_budget(
            payload.get("period_memory_coverage")
        )
    if len(_json_dumps(payload)) <= budget:
        return payload

    payload["persona"] = _truncate(payload.get("persona"), 240)
    payload["trading_policy"] = _truncate(payload.get("trading_policy"), 240)
    payload["safety_note"] = _truncate(payload.get("safety_note"), 160)
    payload["seed_memory"] = []
    payload["active_insights"] = []
    payload["active_policies"] = _compact_policy_summaries_for_budget(
        payload.get("active_policies"),
        limit=4,
        summary_limit=160,
    )
    payload["policy_scorecards"] = _compact_policy_summaries_for_budget(
        payload.get("policy_scorecards"),
        limit=6,
        summary_limit=180,
    )
    payload["policy_rules"] = _compact_policy_rule_summaries_for_budget(
        payload.get("policy_rules"),
        limit=4,
        summary_limit=160,
    )
    validation_repair_backlog = (
        payload.get("validation_repair_backlog")
        if isinstance(payload.get("validation_repair_backlog"), dict)
        else {}
    )
    if validation_repair_backlog:
        payload["validation_repair_backlog"] = {
            **validation_repair_backlog,
            "items": list(validation_repair_backlog.get("items") or [])[:4],
        }
    block_design_constraints = (
        payload.get("block_design_constraints")
        if isinstance(payload.get("block_design_constraints"), dict)
        else {}
    )
    if block_design_constraints:
        payload["block_design_constraints"] = {
            **block_design_constraints,
            "items": list(block_design_constraints.get("items") or [])[:4],
        }
    next_playbook = (
        payload.get("next_block_design_playbook")
        if isinstance(payload.get("next_block_design_playbook"), dict)
        else {}
    )
    if next_playbook:
        payload["next_block_design_playbook"] = {
            **next_playbook,
            "policy_ids": list(next_playbook.get("policy_ids") or [])[:4],
            "symbol_adjustments": list(next_playbook.get("symbol_adjustments") or [])[:4],
        }
    payload["scoped_memory"] = _compact_scoped_memory_for_budget(
        payload.get("scoped_memory")
    )
    payload["policy_rule_evaluation"] = _compact_policy_rule_evaluation(
        payload.get("policy_rule_evaluation") or {}
    )
    payload["recent_reflections"] = []
    payload["latest_journals"] = []
    payload["symbol_notes"] = {}
    jue_wiki = payload.get("jue_wiki")
    if isinstance(jue_wiki, dict):
        wiki_status = str(jue_wiki.get("status") or "").strip() or "unknown"
        if wiki_status == "error":
            payload["jue_wiki"] = {
                "status": "error",
                "available": False,
                "reason": _truncate(jue_wiki.get("reason"), 120),
            }
        elif wiki_status == "ok":
            payload["jue_wiki"] = {
                "status": "ok",
                "target_scope": jue_wiki.get("target_scope"),
                "char_count": jue_wiki.get("char_count"),
                "page_count": len(jue_wiki.get("pages") or []),
            }
        else:
            payload["jue_wiki"] = {"status": "disabled"}
    payload.pop("symbol_analyses", None)
    payload.pop("lifecycle_artifacts", None)
    payload["block_notes"] = {}
    payload["decision_skills"] = {
        key: {
            "skill_id": value.get("skill_id") or key,
            "version": value.get("version") or "",
        }
        for key, value in (payload.get("decision_skills") or {}).items()
        if isinstance(value, dict)
    }
    if len(_json_dumps(payload)) <= budget:
        return payload

    payload["persona"] = ""
    payload["trading_policy"] = ""
    payload["safety_note"] = "Memory guides decisions; safety gates override."
    payload["active_policies"] = _compact_policy_summaries_for_budget(
        payload.get("active_policies"),
        limit=2,
        summary_limit=120,
    )
    payload["policy_scorecards"] = _compact_policy_summaries_for_budget(
        payload.get("policy_scorecards"),
        limit=3,
        summary_limit=120,
    )
    payload["policy_rules"] = _compact_policy_rule_summaries_for_budget(
        payload.get("policy_rules"),
        limit=2,
        summary_limit=120,
    )
    validation_repair_backlog = (
        payload.get("validation_repair_backlog")
        if isinstance(payload.get("validation_repair_backlog"), dict)
        else {}
    )
    if validation_repair_backlog:
        backlog_items = list(validation_repair_backlog.get("items") or [])[:2]
        payload["validation_repair_backlog"] = (
            {
                "status": validation_repair_backlog.get("status"),
                "item_count": validation_repair_backlog.get("item_count"),
                "items": backlog_items,
            }
            if backlog_items
            else {"status": validation_repair_backlog.get("status") or "clear"}
        )
    block_design_constraints = (
        payload.get("block_design_constraints")
        if isinstance(payload.get("block_design_constraints"), dict)
        else {}
    )
    if block_design_constraints:
        constraint_items = list(block_design_constraints.get("items") or [])[:2]
        payload["block_design_constraints"] = (
            {
                "status": block_design_constraints.get("status"),
                "item_count": block_design_constraints.get("item_count"),
                "items": constraint_items,
            }
            if constraint_items
            else {"status": block_design_constraints.get("status") or "clear"}
        )
    validation_recovery_summary = (
        payload.get("validation_recovery_summary")
        if isinstance(payload.get("validation_recovery_summary"), dict)
        else {}
    )
    if validation_recovery_summary.get("status") == "clear":
        payload["validation_recovery_summary"] = {"status": "clear"}
    next_playbook = (
        payload.get("next_block_design_playbook")
        if isinstance(payload.get("next_block_design_playbook"), dict)
        else {}
    )
    if next_playbook:
        payload["next_block_design_playbook"] = {
            "status": next_playbook.get("status") or "normal",
            "hard_filter": bool(next_playbook.get("hard_filter")),
            "scale_up_allowed": bool(next_playbook.get("scale_up_allowed")),
            "entry": next_playbook.get("entry") or {},
            "sizing": next_playbook.get("sizing") or {},
            "target_stop": next_playbook.get("target_stop") or {},
            "evidence": next_playbook.get("evidence") or {},
            "policy_ids": list(next_playbook.get("policy_ids") or [])[:2],
            "symbol_adjustments": list(next_playbook.get("symbol_adjustments") or [])[:2],
        }
    payload["scoped_memory"] = _compact_scoped_memory_for_budget(
        payload.get("scoped_memory"),
        local_limit=1,
        core_limit=0,
        translated_limit=1,
        summary_limit=120,
    )
    if len(_json_dumps(payload)) > budget:
        empty_section_statuses = {
            "jue_wiki_selection_memory": {"missing", "not_applicable"},
            "jue_wiki_context_gap_memory": {"missing", "not_applicable"},
            "jue_wiki_action_reference_memory": {"missing", "not_applicable"},
            "translated_policy_context": {"missing", "not_applicable"},
            "scoped_memory": {"trimmed"},
        }
        for key, removable_statuses in empty_section_statuses.items():
            section = payload.get(key)
            if not isinstance(section, dict):
                continue
            if str(section.get("status") or "") not in removable_statuses:
                continue
            has_items = any(
                bool(section.get(item_key))
                for item_key in ("items", "core", "local", "translated")
            )
            if not has_items:
                payload.pop(key, None)
        rule_eval = payload.get("policy_rule_evaluation")
        if isinstance(rule_eval, dict) and not rule_eval.get("active_rule_count"):
            payload.pop("policy_rule_evaluation", None)
    return payload


def _compact_period_memory_coverage_for_budget(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "unknown", "scopes": [], "missing_count": 0}
    missing = [
        str(item)
        for item in list(value.get("missing") or [])[:12]
        if str(item).strip()
    ]
    return {
        "status": value.get("status") or "unknown",
        "scopes": [
            str(scope)
            for scope in list(value.get("scopes") or [])[:4]
            if str(scope).strip()
        ],
        "missing_count": len(missing),
    }


def _compact_scoped_memory_for_budget(
    value: Any,
    *,
    local_limit: int = 3,
    core_limit: int = 2,
    translated_limit: int = 1,
    summary_limit: int = 220,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "trimmed", "target_scope": ""}

    def compact_items(items: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(items, list) or limit <= 0:
            return []
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    key: item.get(key)
                    for key in (
                        "memory_type",
                        "key",
                        "scope",
                        "transferability",
                        "confidence",
                        "status",
                        "sample_count",
                        "pnl_pct",
                        "exit_reason",
                    )
                    if item.get(key) not in ("", [], {}, None)
                }
                | {"summary_md": _truncate(item.get("summary_md"), summary_limit)}
            )
            if len(out) >= limit:
                break
        return out

    def translated_items(items: Any) -> Any:
        if not isinstance(items, list):
            return items
        priority = {
            "policy_scorecard": 0,
            "policy_rule": 0,
            "block_reflection": 1,
            "policy_signal": 2,
        }
        return sorted(
            items,
            key=lambda item: priority.get(str((item or {}).get("memory_type") or ""), 3)
            if isinstance(item, dict)
            else 3,
        )

    return {
        "status": "trimmed",
        "target_scope": value.get("target_scope") or "",
        "core": compact_items(value.get("core"), limit=core_limit),
        "local": compact_items(value.get("local"), limit=local_limit),
        "translated": compact_items(
            translated_items(value.get("translated")),
            limit=translated_limit,
        ),
        "blocked_count": value.get("blocked_count", 0),
    }


def _decision_skill_status_payload(
    decision_skills: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    missing_skills = [
        key
        for key, value in decision_skills.items()
        if not str(value.get("content_md") or "").strip()
    ]
    return {
        "decision_skill_status": {
            "count": len(decision_skills),
            "missing": missing_skills,
        },
        "decision_skills": {
            key: {
                "version": value.get("version") or "",
                "preview": _truncate(value.get("content_md"), 180),
            }
            for key, value in decision_skills.items()
        },
    }


def _public_context_pack(payload: dict[str, Any]) -> dict[str, Any]:
    public = dict(payload)
    decision_skills = public.get("decision_skills") or {}
    if isinstance(decision_skills, dict):
        public["decision_skills"] = {
            str(key): {
                "version": value.get("version") or "",
                "preview": _truncate(
                    value.get("preview") or value.get("content_md"),
                    180,
                ),
            }
            for key, value in decision_skills.items()
            if isinstance(value, dict)
        }
    return public


def _compact_memory_policy_rules(rows: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in list(rows or [])[: max(int(limit), 1)]:
        if not isinstance(row, dict):
            continue
        effect = row.get("effect") if isinstance(row.get("effect"), dict) else {}
        compact.append(
            {
                key: value
                for key, value in {
                    "rule_id": _clean_text(row.get("rule_id"), limit=160),
                    "policy_id": _clean_text(row.get("policy_id"), limit=160),
                    "scope": _clean_text(row.get("scope"), limit=40),
                    "status": _clean_text(row.get("status"), limit=60),
                    "action": _clean_text(row.get("action"), limit=60),
                    "effect": {
                        key: value
                        for key, value in {
                            "entry_bias": _clean_text(effect.get("entry_bias"), limit=80),
                            "required_evidence": [
                                _clean_text(value, limit=80)
                                for value in list(effect.get("required_evidence") or [])[:8]
                                if _clean_text(value, limit=80)
                            ],
                            "sizing_policy": _clean_text(
                                effect.get("sizing_policy"),
                                limit=100,
                            ),
                            "require_jue_wiki_usage_contract_audit": (
                                True
                                if effect.get(
                                    "require_jue_wiki_usage_contract_audit"
                                )
                                else None
                            ),
                            "target_stop_review": _clean_text(
                                effect.get("target_stop_review"),
                                limit=80,
                            ),
                            "hard_filter": bool(effect.get("hard_filter", False)),
                        }.items()
                        if value not in ("", None)
                    },
                    "reason": _truncate(row.get("reason") or row.get("reason_md"), 180),
                }.items()
                if value not in ("", None, {})
            }
        )
    return compact


def _compact_memory_reflections(rows: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in list(rows or [])[: max(int(limit), 1)]:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                key: value
                for key, value in {
                    "block_id": _clean_text(row.get("block_id"), limit=160),
                    "symbol": _clean_text(row.get("symbol"), limit=40),
                    "scope": _clean_text(row.get("scope"), limit=40),
                    "pnl_pct": row.get("pnl_pct"),
                    "exit_reason": _clean_text(row.get("exit_reason"), limit=100),
                    "lesson_md": _truncate(row.get("lesson_md"), 240),
                }.items()
                if value not in ("", None)
            }
        )
    return compact


def _compact_memory_journals(rows: Any, *, limit: int = 5, message_limit: int = 420) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in list(rows or [])[: max(int(limit), 1)]:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                key: value
                for key, value in {
                    "trading_day": _clean_text(row.get("trading_day"), limit=40),
                    "slot": _clean_text(row.get("slot"), limit=80),
                    "slot_label": _clean_text(row.get("slot_label"), limit=80),
                    "title": _clean_text(row.get("title"), limit=160),
                    "message_md": _truncate(row.get("message_md"), message_limit),
                    "sent_telegram": bool(row.get("sent_telegram", False)),
                    "memory_scope": _clean_text(row.get("memory_scope"), limit=40),
                }.items()
                if value not in ("", None)
            }
        )
    return compact


def _compact_memory_validation_backlog(backlog: Any, *, limit: int = 6) -> dict[str, Any]:
    if not isinstance(backlog, dict):
        return {"status": "clear", "item_count": 0, "items": []}
    items: list[dict[str, Any]] = []
    for row in list(backlog.get("items") or [])[: max(int(limit), 1)]:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                key: value
                for key, value in {
                    "venue": _clean_text(row.get("venue"), limit=40),
                    "priority": _clean_text(row.get("priority"), limit=20),
                    "status": _clean_text(row.get("status"), limit=60),
                    "label": _clean_text(row.get("label"), limit=120),
                    "discipline_id": _clean_text(row.get("discipline_id"), limit=120),
                    "exit_criteria": _truncate(row.get("exit_criteria"), 180),
                    "lane_policy_hint": _truncate(row.get("lane_policy_hint"), 180),
                    "owner": _clean_text(row.get("owner"), limit=80),
                    "cadence": _clean_text(row.get("cadence"), limit=80),
                    "blocks_scaling": _clean_text(row.get("blocks_scaling"), limit=100),
                    "policy_id": _clean_text(row.get("policy_id"), limit=160),
                    "event_key": _clean_text(row.get("event_key"), limit=160),
                }.items()
                if value not in ("", None)
            }
        )
    return {
        "status": backlog.get("status") or ("needs_repair" if items else "clear"),
        "item_count": int(backlog.get("item_count") or len(items)),
        "items": items,
    }


def _compact_memory_historical_replay(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict) or row.get("status") == "missing":
        return {"status": "missing"}
    return {
        key: value
        for key, value in {
            "status": _clean_text(row.get("status"), limit=60),
            "period_type": _clean_text(row.get("period_type"), limit=40),
            "period_key": _clean_text(row.get("period_key"), limit=80),
            "case_count": row.get("case_count"),
            "replay_md": _truncate(row.get("replay_md"), 240),
            "updated_at": _clean_text(row.get("updated_at"), limit=80),
        }.items()
        if value not in ("", None)
    }


@dataclass(slots=True)
class InvestmentMemoryConfig:
    root_path: str = ".runtime/investment_memory"
    db_path: str = ".runtime/investment_memory.db"
    strategy_md_path: str = ".runtime/strategy_krx.md"
    policy_mode: str = "soft_auto"
    persona_tone: str = "friendly_partner"
    ritual_timezone: str = "Asia/Seoul"
    telegram_enabled: bool = True
    context_max_chars: int = 8000
    ops_summary_cache_ttl_sec: int = 10


class InvestmentMemoryRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()
        self._repair_jue_wiki_selection_provenance()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    slot TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_memory_runs_kind
                    ON memory_runs(kind, slot, run_at DESC);

                CREATE TABLE IF NOT EXISTS daily_journals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    message_md TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    sent_telegram INTEGER NOT NULL DEFAULT 0,
                    telegram_result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trading_day, slot)
                );

                CREATE TABLE IF NOT EXISTS memory_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    transferability TEXT NOT NULL DEFAULT 'direct',
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence REAL NOT NULL DEFAULT 0,
                    summary_md TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    source_run_id INTEGER,
                    updated_at TEXT NOT NULL,
                    UNIQUE(memory_type, key, status)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_insights_lookup
                    ON memory_insights(memory_type, key, status);

                CREATE TABLE IF NOT EXISTS policy_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    transferability TEXT NOT NULL DEFAULT 'direct',
                    action TEXT NOT NULL DEFAULT '',
                    strength TEXT NOT NULL DEFAULT 'soft',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    source_run_id INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_sends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    sent_at TEXT NOT NULL,
                    UNIQUE(trading_day, slot)
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL DEFAULT '',
                    block_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    processed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_memory_events_status
                    ON memory_events(status, created_at);

                CREATE TABLE IF NOT EXISTS block_reflections (
                    block_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    exit_reason TEXT NOT NULL DEFAULT '',
                    pnl_krw REAL NOT NULL DEFAULT 0,
                    pnl_pct REAL NOT NULL DEFAULT 0,
                    mfe_pct REAL NOT NULL DEFAULT 0,
                    mae_pct REAL NOT NULL DEFAULT 0,
                    hold_seconds INTEGER NOT NULL DEFAULT 0,
                    rule_followed INTEGER NOT NULL DEFAULT 0,
                    lesson_md TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    source_run_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_block_reflections_symbol
                    ON block_reflections(symbol, updated_at DESC);

                CREATE TABLE IF NOT EXISTS policy_scorecards (
                    policy_id TEXT PRIMARY KEY,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    transferability TEXT NOT NULL DEFAULT 'direct',
                    action TEXT NOT NULL DEFAULT 'observe',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    avg_pnl_pct REAL NOT NULL DEFAULT 0,
                    expectancy_pct REAL NOT NULL DEFAULT 0,
                    rule_follow_rate REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS policy_rules (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    rule_id TEXT NOT NULL UNIQUE,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    transferability TEXT NOT NULL DEFAULT 'direct',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    action TEXT NOT NULL DEFAULT 'observe',
                    condition_json TEXT NOT NULL DEFAULT '{}',
                    effect_json TEXT NOT NULL DEFAULT '{}',
                    reason TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    source_scorecard_json TEXT NOT NULL DEFAULT '{}',
                    file_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    activated_at TEXT NOT NULL DEFAULT '',
                    retired_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(policy_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_policy_rules_status
                    ON policy_rules(status, policy_id, version DESC);

                CREATE TABLE IF NOT EXISTS period_reviews (
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    mode TEXT NOT NULL DEFAULT 'deterministic',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    review_md TEXT NOT NULL DEFAULT '',
                    policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(period_key, period_type, memory_scope)
                );

                CREATE TABLE IF NOT EXISTS historical_replays (
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    mode TEXT NOT NULL DEFAULT 'deterministic',
                    case_count INTEGER NOT NULL DEFAULT 0,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    replay_md TEXT NOT NULL DEFAULT '',
                    case_reviews_json TEXT NOT NULL DEFAULT '[]',
                    policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(period_key, period_type, memory_scope)
                );

                CREATE TABLE IF NOT EXISTS policy_revisions (
                    revision_id TEXT PRIMARY KEY,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    transferability TEXT NOT NULL DEFAULT 'direct',
                    period_key TEXT NOT NULL DEFAULT '',
                    period_type TEXT NOT NULL DEFAULT '',
                    policy_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT 'keep',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    scope TEXT NOT NULL DEFAULT 'general',
                    condition_json TEXT NOT NULL DEFAULT '{}',
                    effect_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    reason_md TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    activated_at TEXT NOT NULL DEFAULT '',
                    retired_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_policy_revisions_status
                    ON policy_revisions(status, period_type, created_at DESC);

                CREATE TABLE IF NOT EXISTS policy_outcomes (
                    policy_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    transferability TEXT NOT NULL DEFAULT 'direct',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    avg_pnl_pct REAL NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    expectancy_pct REAL NOT NULL DEFAULT 0,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0,
                    rule_follow_rate REAL NOT NULL DEFAULT 0,
                    helped_count INTEGER NOT NULL DEFAULT 0,
                    hurt_count INTEGER NOT NULL DEFAULT 0,
                    notes_md TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(policy_id, rule_id, period_key, period_type, memory_scope)
                );

                CREATE TABLE IF NOT EXISTS symbol_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    trigger TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'instant',
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    summary TEXT NOT NULL DEFAULT '',
                    short_view TEXT NOT NULL DEFAULT '',
                    mid_view TEXT NOT NULL DEFAULT '',
                    long_view TEXT NOT NULL DEFAULT '',
                    stance TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    data_gaps_json TEXT NOT NULL DEFAULT '[]',
                    triggers_json TEXT NOT NULL DEFAULT '[]',
                    target_candidates_json TEXT NOT NULL DEFAULT '[]',
                    stop_candidates_json TEXT NOT NULL DEFAULT '[]',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    raw_response_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_symbol_analyses_symbol_created
                    ON symbol_analyses(symbol, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_symbol_analyses_trigger_created
                    ON symbol_analyses(trigger, created_at DESC);
                """
            )
            self._ensure_memory_insight_scope_columns(conn)
            self._ensure_period_review_scope_tables(conn)
            self._ensure_policy_revision_scope_columns(conn)
            self._ensure_policy_outcome_scope_table(conn)
            self._ensure_policy_change_scope_columns(conn)
            self._ensure_policy_scorecard_scope_columns(conn)
            self._ensure_policy_rule_scope_columns(conn)

    @staticmethod
    def _table_pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [
            str(row["name"])
            for row in sorted(rows, key=lambda value: int(value["pk"] or 0))
            if int(row["pk"] or 0) > 0
        ]

    def _ensure_period_review_scope_tables(self, conn: sqlite3.Connection) -> None:
        self._ensure_scoped_period_table(
            conn,
            table="period_reviews",
            create_sql="""
                CREATE TABLE period_reviews (
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    mode TEXT NOT NULL DEFAULT 'deterministic',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    review_md TEXT NOT NULL DEFAULT '',
                    policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(period_key, period_type, memory_scope)
                )
            """,
            index_sql="""
                CREATE INDEX IF NOT EXISTS idx_period_reviews_type_updated
                    ON period_reviews(period_type, memory_scope, updated_at DESC)
            """,
            columns=[
                "period_key",
                "period_type",
                "memory_scope",
                "start_date",
                "end_date",
                "status",
                "mode",
                "metrics_json",
                "review_md",
                "policy_revision_ids_json",
                "created_at",
                "updated_at",
            ],
            defaults={
                "memory_scope": "'core'",
                "start_date": "''",
                "end_date": "''",
                "status": "'ok'",
                "mode": "'deterministic'",
                "metrics_json": "'{}'",
                "review_md": "''",
                "policy_revision_ids_json": "'[]'",
                "created_at": "''",
                "updated_at": "''",
            },
        )
        self._ensure_scoped_period_table(
            conn,
            table="historical_replays",
            create_sql="""
                CREATE TABLE historical_replays (
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    memory_scope TEXT NOT NULL DEFAULT 'core',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    mode TEXT NOT NULL DEFAULT 'deterministic',
                    case_count INTEGER NOT NULL DEFAULT 0,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    replay_md TEXT NOT NULL DEFAULT '',
                    case_reviews_json TEXT NOT NULL DEFAULT '[]',
                    policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(period_key, period_type, memory_scope)
                )
            """,
            index_sql="""
                CREATE INDEX IF NOT EXISTS idx_historical_replays_type_updated
                    ON historical_replays(period_type, memory_scope, updated_at DESC)
            """,
            columns=[
                "period_key",
                "period_type",
                "memory_scope",
                "start_date",
                "end_date",
                "status",
                "mode",
                "case_count",
                "metrics_json",
                "replay_md",
                "case_reviews_json",
                "policy_revision_ids_json",
                "created_at",
                "updated_at",
            ],
            defaults={
                "memory_scope": "'core'",
                "start_date": "''",
                "end_date": "''",
                "status": "'ok'",
                "mode": "'deterministic'",
                "case_count": "0",
                "metrics_json": "'{}'",
                "replay_md": "''",
                "case_reviews_json": "'[]'",
                "policy_revision_ids_json": "'[]'",
                "created_at": "''",
                "updated_at": "''",
            },
        )

    def _ensure_scoped_period_table(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        create_sql: str,
        index_sql: str,
        columns: list[str],
        defaults: dict[str, str],
    ) -> None:
        pk_columns = self._table_pk_columns(conn, table)
        if pk_columns == ["period_key", "period_type", "memory_scope"]:
            conn.execute(index_sql)
            return
        legacy_table = f"{table}_legacy_scope_migration"
        legacy_columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        conn.execute(f"ALTER TABLE {table} RENAME TO {legacy_table}")
        conn.execute(create_sql)
        select_exprs: list[str] = []
        for column in columns:
            if column == "memory_scope" and column in legacy_columns:
                select_exprs.append("COALESCE(NULLIF(memory_scope, ''), 'core')")
            elif column in legacy_columns:
                select_exprs.append(column)
            else:
                select_exprs.append(defaults[column])
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {table} ({', '.join(columns)})
            SELECT {', '.join(select_exprs)}
            FROM {legacy_table}
            """
        )
        conn.execute(f"DROP TABLE {legacy_table}")
        conn.execute(index_sql)

    def _ensure_policy_revision_scope_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(policy_revisions)").fetchall()
        }
        backfill_required = False
        if "memory_scope" not in columns:
            conn.execute(
                "ALTER TABLE policy_revisions ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'core'"
            )
            backfill_required = True
        if "transferability" not in columns:
            conn.execute(
                "ALTER TABLE policy_revisions ADD COLUMN transferability TEXT NOT NULL DEFAULT 'direct'"
            )
            backfill_required = True
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_policy_revisions_scope_status
                ON policy_revisions(
                    memory_scope,
                    transferability,
                    status,
                    created_at DESC
                )
            """
        )
        stale_query = """
            SELECT revision_id, policy_id, evidence_json
            FROM policy_revisions
        """
        if not backfill_required:
            stale_query += (
                " WHERE memory_scope = ''"
                " OR transferability = ''"
                " OR memory_scope IS NULL"
                " OR transferability IS NULL"
            )
        for row in conn.execute(stale_query).fetchall():
            evidence = _json_loads(row["evidence_json"], {})
            if not isinstance(evidence, dict):
                evidence = {}
            payload = {
                "policy_id": row["policy_id"],
                "evidence": evidence,
            }
            memory_scope = _policy_revision_memory_scope(payload)
            transferability = (
                _normalize_transferability(evidence.get("transferability"))
                or "direct"
            )
            conn.execute(
                """
                UPDATE policy_revisions
                SET memory_scope = ?, transferability = ?
                WHERE revision_id = ?
                """,
                (memory_scope, transferability, row["revision_id"]),
            )

    def _ensure_policy_outcome_scope_table(self, conn: sqlite3.Connection) -> None:
        pk_columns = self._table_pk_columns(conn, "policy_outcomes")
        if pk_columns == [
            "policy_id",
            "rule_id",
            "period_key",
            "period_type",
            "memory_scope",
        ]:
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_policy_outcomes_scope_updated
                    ON policy_outcomes(
                        memory_scope,
                        transferability,
                        updated_at DESC
                    )
                """
            )
            return
        legacy_table = "policy_outcomes_legacy_scope_migration"
        legacy_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(policy_outcomes)").fetchall()
        }
        conn.execute(f"ALTER TABLE policy_outcomes RENAME TO {legacy_table}")
        conn.execute(
            """
            CREATE TABLE policy_outcomes (
                policy_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                period_key TEXT NOT NULL,
                period_type TEXT NOT NULL,
                memory_scope TEXT NOT NULL DEFAULT 'core',
                transferability TEXT NOT NULL DEFAULT 'direct',
                sample_count INTEGER NOT NULL DEFAULT 0,
                avg_pnl_pct REAL NOT NULL DEFAULT 0,
                win_rate REAL NOT NULL DEFAULT 0,
                expectancy_pct REAL NOT NULL DEFAULT 0,
                max_drawdown_pct REAL NOT NULL DEFAULT 0,
                rule_follow_rate REAL NOT NULL DEFAULT 0,
                helped_count INTEGER NOT NULL DEFAULT 0,
                hurt_count INTEGER NOT NULL DEFAULT 0,
                notes_md TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(policy_id, rule_id, period_key, period_type, memory_scope)
            )
            """
        )
        columns = [
            "policy_id",
            "rule_id",
            "period_key",
            "period_type",
            "memory_scope",
            "transferability",
            "sample_count",
            "avg_pnl_pct",
            "win_rate",
            "expectancy_pct",
            "max_drawdown_pct",
            "rule_follow_rate",
            "helped_count",
            "hurt_count",
            "notes_md",
            "updated_at",
        ]
        defaults = {
            "memory_scope": "'core'",
            "transferability": "'direct'",
            "sample_count": "0",
            "avg_pnl_pct": "0",
            "win_rate": "0",
            "expectancy_pct": "0",
            "max_drawdown_pct": "0",
            "rule_follow_rate": "0",
            "helped_count": "0",
            "hurt_count": "0",
            "notes_md": "''",
            "updated_at": "''",
        }
        select_exprs: list[str] = []
        for column in columns:
            if column == "memory_scope" and column in legacy_columns:
                select_exprs.append("COALESCE(NULLIF(memory_scope, ''), 'core')")
            elif column == "transferability" and column in legacy_columns:
                select_exprs.append("COALESCE(NULLIF(transferability, ''), 'direct')")
            elif column in legacy_columns:
                select_exprs.append(column)
            else:
                select_exprs.append(defaults[column])
        conn.execute(
            f"""
            INSERT OR REPLACE INTO policy_outcomes ({', '.join(columns)})
            SELECT {', '.join(select_exprs)}
            FROM {legacy_table}
            """
        )
        conn.execute(f"DROP TABLE {legacy_table}")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_policy_outcomes_scope_updated
                ON policy_outcomes(memory_scope, transferability, updated_at DESC)
            """
        )

    def _ensure_memory_insight_scope_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(memory_insights)").fetchall()
        }
        backfill_required = False
        if "memory_scope" not in columns:
            conn.execute(
                "ALTER TABLE memory_insights ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'core'"
            )
            backfill_required = True
        if "transferability" not in columns:
            conn.execute(
                "ALTER TABLE memory_insights ADD COLUMN transferability TEXT NOT NULL DEFAULT 'direct'"
            )
            backfill_required = True
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_insights_scope_status
                ON memory_insights(
                    memory_scope,
                    transferability,
                    status,
                    updated_at DESC
                )
            """
        )
        stale_query = """
            SELECT id, memory_type, key, confidence, summary_md, evidence_json, updated_at
            FROM memory_insights
        """
        if not backfill_required:
            stale_query += (
                " WHERE memory_scope = ''"
                " OR transferability = ''"
                " OR memory_scope IS NULL"
                " OR transferability IS NULL"
            )
        stale_rows = conn.execute(stale_query).fetchall()
        for row in stale_rows:
            evidence = _json_loads(row["evidence_json"], [])
            if not isinstance(evidence, list):
                evidence = []
            scoped = _scoped_memory_item(
                memory_type=row["memory_type"],
                key=row["key"],
                summary_md=row["summary_md"],
                confidence=row["confidence"],
                evidence=evidence,
                updated_at=row["updated_at"],
            )
            memory_scope = str(scoped.get("scope") or "core")
            transferability = str(scoped.get("transferability") or "direct")
            conn.execute(
                """
                UPDATE memory_insights
                SET memory_scope = ?, transferability = ?
                WHERE id = ?
                """,
                (memory_scope, transferability, row["id"]),
            )

    def _ensure_policy_change_scope_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(policy_changes)").fetchall()
        }
        backfill_required = False
        if "memory_scope" not in columns:
            conn.execute(
                "ALTER TABLE policy_changes ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'core'"
            )
            backfill_required = True
        if "transferability" not in columns:
            conn.execute(
                "ALTER TABLE policy_changes ADD COLUMN transferability TEXT NOT NULL DEFAULT 'direct'"
            )
            backfill_required = True
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_policy_changes_scope_status
                ON policy_changes(
                    memory_scope,
                    transferability,
                    status,
                    created_at DESC
                )
            """
        )
        stale_query = "SELECT id, policy_id FROM policy_changes"
        if not backfill_required:
            stale_query += (
                " WHERE memory_scope = ''"
                " OR transferability = ''"
                " OR memory_scope IS NULL"
                " OR transferability IS NULL"
            )
        stale_rows = conn.execute(stale_query).fetchall()
        for row in stale_rows:
            payload = {"policy_id": row["policy_id"]}
            memory_scope = _policy_row_scope(payload)
            transferability = _policy_row_transferability(
                payload,
                memory_type="policy",
            )
            conn.execute(
                """
                UPDATE policy_changes
                SET memory_scope = ?, transferability = ?
                WHERE id = ?
                """,
                (memory_scope, transferability, row["id"]),
            )

    def _ensure_policy_scorecard_scope_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(policy_scorecards)").fetchall()
        }
        backfill_required = False
        if "memory_scope" not in columns:
            conn.execute(
                "ALTER TABLE policy_scorecards ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'core'"
            )
            backfill_required = True
        if "transferability" not in columns:
            conn.execute(
                "ALTER TABLE policy_scorecards ADD COLUMN transferability TEXT NOT NULL DEFAULT 'direct'"
            )
            backfill_required = True
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_policy_scorecards_scope_status
                ON policy_scorecards(
                    memory_scope,
                    transferability,
                    status,
                    confidence DESC,
                    updated_at DESC
                )
            """
        )
        stale_query = "SELECT policy_id, raw_json FROM policy_scorecards"
        if not backfill_required:
            stale_query += (
                " WHERE memory_scope = ''"
                " OR transferability = ''"
                " OR memory_scope IS NULL"
                " OR transferability IS NULL"
            )
        stale_rows = conn.execute(stale_query).fetchall()
        for row in stale_rows:
            raw = _json_loads(row["raw_json"], {})
            if not isinstance(raw, dict):
                raw = {}
            raw["policy_id"] = row["policy_id"]
            memory_scope = _policy_row_scope(raw)
            transferability = _policy_row_transferability(
                raw,
                memory_type="policy_scorecard",
            )
            conn.execute(
                """
                UPDATE policy_scorecards
                SET memory_scope = ?, transferability = ?
                WHERE policy_id = ?
                """,
                (memory_scope, transferability, row["policy_id"]),
            )

    @staticmethod
    def _jue_wiki_selection_scope_from_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        parts = [part for part in text.split(".") if part]
        if len(parts) >= 2 and parts[0] == "jue_wiki_selection":
            return _normalize_memory_scope(parts[1])
        return ""

    @staticmethod
    def _jue_wiki_selection_scope_evidence(scope: str) -> list[dict[str, str]]:
        normalized_scope = _normalize_memory_scope(scope) or "core"
        return [
            {
                "memory_scope": normalized_scope,
                "transferability": "direct",
                "source": "jue_wiki_selection_audit",
            }
        ]

    def _repair_jue_wiki_selection_provenance(self) -> None:
        with self._connect() as conn:
            scorecard_rows = conn.execute(
                """
                SELECT policy_id, memory_scope, raw_json
                FROM policy_scorecards
                WHERE policy_id LIKE 'jue_wiki_selection.%'
                """
            ).fetchall()
            for row in scorecard_rows:
                policy_id = str(row["policy_id"] or "")
                raw = _json_loads(row["raw_json"], {})
                if not isinstance(raw, dict):
                    raw = {}
                scope = (
                    _normalize_memory_scope(raw.get("memory_scope"))
                    or _normalize_memory_scope(row["memory_scope"])
                    or self._jue_wiki_selection_scope_from_key(policy_id)
                    or "core"
                )
                raw["policy_id"] = policy_id
                raw["memory_scope"] = scope
                raw["scope"] = scope
                raw["transferability"] = "direct"
                raw["scope_evidence"] = self._jue_wiki_selection_scope_evidence(scope)
                raw.setdefault("source", "jue_wiki_selection_audit")
                conn.execute(
                    """
                    UPDATE policy_scorecards
                    SET memory_scope = ?, transferability = ?, raw_json = ?
                    WHERE policy_id = ?
                    """,
                    (scope, "direct", _json_dumps(raw), policy_id),
                )

            insight_rows = conn.execute(
                """
                SELECT memory_type, key, status, memory_scope, evidence_json
                FROM memory_insights
                WHERE key LIKE 'jue_wiki_selection.%'
                """
            ).fetchall()
            for row in insight_rows:
                key = str(row["key"] or "")
                scope = (
                    _normalize_memory_scope(row["memory_scope"])
                    or self._jue_wiki_selection_scope_from_key(key)
                    or "core"
                )
                evidence = _json_loads(row["evidence_json"], [])
                if not isinstance(evidence, list):
                    evidence = []
                repaired_evidence: list[Any] = []
                for item in evidence:
                    if isinstance(item, dict):
                        repaired = dict(item)
                        repaired["memory_scope"] = scope
                        repaired["transferability"] = "direct"
                        repaired.setdefault("source", "jue_wiki_selection_audit")
                        repaired_evidence.append(repaired)
                    else:
                        repaired_evidence.append(item)
                conn.execute(
                    """
                    UPDATE memory_insights
                    SET memory_scope = ?, transferability = ?, evidence_json = ?
                    WHERE memory_type = ? AND key = ? AND status = ?
                    """,
                    (
                        scope,
                        "direct",
                        _json_dumps(repaired_evidence),
                        row["memory_type"],
                        key,
                        row["status"],
                    ),
                )

    @staticmethod
    def _jue_wiki_selection_provenance_status(
        conn: sqlite3.Connection,
    ) -> dict[str, Any]:
        scorecard_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM policy_scorecards
                WHERE policy_id LIKE 'jue_wiki_selection.%'
                """
            ).fetchone()[0]
        )
        dirty_scorecard_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM policy_scorecards
                WHERE policy_id LIKE 'jue_wiki_selection.%'
                  AND (
                    transferability != 'direct'
                    OR transferability = ''
                    OR transferability IS NULL
                    OR memory_scope = ''
                    OR memory_scope IS NULL
                  )
                """
            ).fetchone()[0]
        )
        dirty_scorecard_policy_ids = [
            str(row[0] or "")
            for row in conn.execute(
                """
                SELECT policy_id FROM policy_scorecards
                WHERE policy_id LIKE 'jue_wiki_selection.%'
                  AND (
                    transferability != 'direct'
                    OR transferability = ''
                    OR transferability IS NULL
                    OR memory_scope = ''
                    OR memory_scope IS NULL
                  )
                ORDER BY updated_at DESC, policy_id
                LIMIT 5
                """
            ).fetchall()
        ]
        insight_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memory_insights
                WHERE key LIKE 'jue_wiki_selection.%'
                """
            ).fetchone()[0]
        )
        dirty_insight_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memory_insights
                WHERE key LIKE 'jue_wiki_selection.%'
                  AND (
                    transferability != 'direct'
                    OR transferability = ''
                    OR transferability IS NULL
                    OR memory_scope = ''
                    OR memory_scope IS NULL
                  )
                """
            ).fetchone()[0]
        )
        dirty_insight_keys = [
            str(row[0] or "")
            for row in conn.execute(
                """
                SELECT key FROM memory_insights
                WHERE key LIKE 'jue_wiki_selection.%'
                  AND (
                    transferability != 'direct'
                    OR transferability = ''
                    OR transferability IS NULL
                    OR memory_scope = ''
                    OR memory_scope IS NULL
                  )
                ORDER BY updated_at DESC, key
                LIMIT 5
                """
            ).fetchall()
        ]
        dirty_total = dirty_scorecard_count + dirty_insight_count
        return {
            "status": "dirty" if dirty_total else "clean",
            "scorecard_count": scorecard_count,
            "insight_count": insight_count,
            "dirty_scorecard_count": dirty_scorecard_count,
            "dirty_insight_count": dirty_insight_count,
            "dirty_scorecard_policy_ids": dirty_scorecard_policy_ids,
            "dirty_insight_keys": dirty_insight_keys,
            "dirty_count": dirty_total,
        }

    def _ensure_policy_rule_scope_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(policy_rules)").fetchall()
        }
        backfill_required = False
        if "memory_scope" not in columns:
            conn.execute(
                "ALTER TABLE policy_rules ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'core'"
            )
            backfill_required = True
        if "transferability" not in columns:
            conn.execute(
                "ALTER TABLE policy_rules ADD COLUMN transferability TEXT NOT NULL DEFAULT 'direct'"
            )
            backfill_required = True
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_policy_rules_scope_status
                ON policy_rules(
                    memory_scope,
                    transferability,
                    status,
                    policy_id,
                    version DESC
                )
            """
        )
        stale_query = "SELECT policy_id, source_scorecard_json FROM policy_rules"
        if not backfill_required:
            stale_query += (
                " WHERE memory_scope = ''"
                " OR transferability = ''"
                " OR memory_scope IS NULL"
                " OR transferability IS NULL"
            )
        stale_rows = conn.execute(stale_query).fetchall()
        for row in stale_rows:
            source_scorecard = _json_loads(row["source_scorecard_json"], {})
            if not isinstance(source_scorecard, dict):
                source_scorecard = {}
            payload = {
                "policy_id": row["policy_id"],
                "source_scorecard": source_scorecard,
            }
            memory_scope = _policy_row_scope(payload)
            transferability = _policy_row_transferability(
                payload,
                memory_type="policy_rule",
            )
            conn.execute(
                """
                UPDATE policy_rules
                SET memory_scope = ?, transferability = ?
                WHERE policy_id = ?
                """,
                (memory_scope, transferability, row["policy_id"]),
            )

    def save_run(
        self,
        *,
        kind: str,
        slot: str,
        status: str,
        mode: str,
        model: str,
        error_message: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
    ) -> int:
        stored_input = _compact_run_payload(
            input_payload,
            limit=MEMORY_RUN_INPUT_STORAGE_LIMIT,
        )
        stored_output = _compact_run_payload(
            output_payload,
            limit=MEMORY_RUN_OUTPUT_STORAGE_LIMIT,
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_runs (
                    run_at, kind, slot, status, mode, model, error_message,
                    input_json, output_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    kind,
                    slot,
                    status,
                    mode,
                    model,
                    error_message,
                    _json_dumps(stored_input),
                    _json_dumps(stored_output),
                ),
            )
            return int(cursor.lastrowid)

    def upsert_journal(
        self,
        *,
        trading_day: str,
        slot: str,
        title: str,
        message_md: str,
        file_path: str,
        context: dict[str, Any],
        sent_telegram: bool = False,
        telegram_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        stored_message = _compact_message_md(message_md, limit=6000)
        stored_context = _ensure_storage_payload_limit(
            _compact_ritual_context(context, limit=16_000),
            limit=16_000,
            label="daily_journal_context",
            original_chars=len(_json_dumps(context)) if isinstance(context, dict) else 0,
        )
        stored_telegram_result = _compact_telegram_result(telegram_result or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_journals (
                    trading_day, slot, title, message_md, file_path, context_json,
                    sent_telegram, telegram_result_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trading_day, slot) DO UPDATE SET
                    title = excluded.title,
                    message_md = excluded.message_md,
                    file_path = excluded.file_path,
                    context_json = excluded.context_json,
                    sent_telegram = excluded.sent_telegram,
                    telegram_result_json = excluded.telegram_result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    trading_day,
                    slot,
                    title,
                    stored_message,
                    file_path,
                    _json_dumps(stored_context),
                    1 if sent_telegram else 0,
                    _json_dumps(stored_telegram_result),
                    now,
                    now,
                ),
            )
        return self.get_journal(trading_day, slot) or {}

    def get_journal(self, trading_day: str, slot: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM daily_journals
                WHERE trading_day = ? AND slot = ?
                LIMIT 1
                """,
                (trading_day, slot),
            ).fetchone()
        return self._row_to_journal(row) if row else None

    def latest_journals(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM daily_journals
                ORDER BY trading_day DESC, updated_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_journal(row) for row in rows]

    def journals_for_day(self, trading_day: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM daily_journals
                WHERE trading_day = ?
                ORDER BY CASE slot
                    WHEN 'pre_open' THEN 1
                    WHEN 'midday' THEN 2
                    WHEN 'post_close' THEN 3
                    WHEN 'block_reflection' THEN 4
                    WHEN 'weekly' THEN 5
                    ELSE 9
                END
                """,
                (trading_day,),
            ).fetchall()
        return [self._row_to_journal(row) for row in rows]

    def save_insight(
        self,
        *,
        memory_type: str,
        key: str,
        status: str,
        confidence: float,
        summary_md: str,
        evidence: list[Any] | None = None,
        source_run_id: int | None = None,
    ) -> None:
        now = utc_now_iso()
        evidence_rows = evidence or []
        scoped = _scoped_memory_item(
            memory_type=memory_type,
            key=key,
            summary_md=summary_md,
            confidence=confidence,
            evidence=evidence_rows,
            updated_at=now,
        )
        memory_scope = str(scoped.get("scope") or "core")
        transferability = str(scoped.get("transferability") or "direct")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_insights (
                    memory_type, key, memory_scope, transferability, status,
                    confidence, summary_md, evidence_json, source_run_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_type, key, status) DO UPDATE SET
                    memory_scope = excluded.memory_scope,
                    transferability = excluded.transferability,
                    confidence = excluded.confidence,
                    summary_md = excluded.summary_md,
                    evidence_json = excluded.evidence_json,
                    source_run_id = excluded.source_run_id,
                    updated_at = excluded.updated_at
                """,
                (
                    memory_type,
                    key,
                    memory_scope,
                    transferability,
                    status,
                    max(min(float(confidence), 1.0), 0.0),
                    summary_md,
                    _json_dumps(evidence_rows),
                    source_run_id,
                    now,
                ),
            )

    def list_insights(
        self,
        *,
        memory_type: str = "",
        key: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_insights WHERE 1 = 1"
        params: list[Any] = []
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        if key:
            query += " AND key = ?"
            params.append(key)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_insight(row) for row in rows]

    def list_insights_for_scope(
        self,
        *,
        target_scope: str,
        memory_type: str = "",
        key: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_scope = _normalize_memory_scope(target_scope)
        if not normalized_scope:
            return []
        query = "SELECT * FROM memory_insights WHERE memory_scope = ?"
        params: list[Any] = [normalized_scope]
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        if key:
            query += " AND key = ?"
            params.append(key)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_insight(row) for row in rows]

    def save_symbol_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            raise ValueError("symbol must be a 6-digit code")
        now = utc_now_iso()
        created_at = _clean_text(payload.get("created_at") or now, limit=80)
        updated_at = _clean_text(payload.get("updated_at") or now, limit=80)

        def list_payload(key: str) -> list[Any]:
            value = payload.get(key)
            return value if isinstance(value, list) else []

        def dict_payload(key: str) -> dict[str, Any]:
            value = payload.get(key)
            return value if isinstance(value, dict) else {}

        snapshot_payload = _ensure_storage_payload_limit(
            dict_payload("snapshot"),
            limit=5_000,
            label="symbol_analysis_snapshot",
        )
        prompt_payload = _ensure_storage_payload_limit(
            dict_payload("prompt"),
            limit=5_000,
            label="symbol_analysis_prompt",
        )
        raw_response_payload = _ensure_storage_payload_limit(
            dict_payload("raw_response"),
            limit=6_000,
            label="symbol_analysis_raw_response",
        )

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO symbol_analyses (
                    symbol, name, trigger, source, model, status, summary,
                    short_view, mid_view, long_view, stance, confidence,
                    reasons_json, risks_json, data_gaps_json, triggers_json,
                    target_candidates_json, stop_candidates_json, snapshot_json,
                    prompt_json, raw_response_json, error_message, created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    _clean_text(payload.get("name"), limit=120),
                    _clean_text(payload.get("trigger"), limit=80),
                    _clean_text(payload.get("source") or "instant", limit=80),
                    _clean_text(payload.get("model"), limit=80),
                    _clean_text(payload.get("status") or "ok", limit=40),
                    _clean_text(payload.get("summary"), limit=4000),
                    _clean_text(payload.get("short_view"), limit=1200),
                    _clean_text(payload.get("mid_view"), limit=1200),
                    _clean_text(payload.get("long_view"), limit=1200),
                    _clean_text(payload.get("stance"), limit=80),
                    _safe_float(payload.get("confidence")),
                    _json_dumps(list_payload("reasons")),
                    _json_dumps(list_payload("risks")),
                    _json_dumps(list_payload("data_gaps")),
                    _json_dumps(list_payload("triggers")),
                    _json_dumps(list_payload("target_candidates")),
                    _json_dumps(list_payload("stop_candidates")),
                    _json_dumps(snapshot_payload),
                    _json_dumps(prompt_payload),
                    _json_dumps(raw_response_payload),
                    _compact_error_message(payload.get("error_message")),
                    created_at,
                    updated_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM symbol_analyses WHERE id = ? LIMIT 1",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self._symbol_analysis_public(row) if row else {}

    def list_symbol_analyses(
        self,
        symbol: str,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        target = str(symbol or "").strip()
        if not _is_symbol(target):
            return {
                "status": "invalid_symbol",
                "symbol": target,
                "items": [],
                "count": 0,
            }
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM symbol_analyses
                WHERE symbol = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (target, max(int(limit), 1)),
            ).fetchall()
        items = [self._symbol_analysis_public(row) for row in rows]
        return {
            "status": "ok",
            "symbol": target,
            "count": len(items),
            "items": items,
        }

    def save_policy_change(
        self,
        *,
        policy_id: str,
        action: str,
        strength: str,
        status: str,
        reason: str,
        confidence: float,
        source_run_id: int | None,
    ) -> None:
        scope_payload = {"policy_id": policy_id}
        memory_scope = _policy_row_scope(scope_payload)
        transferability = _policy_row_transferability(
            scope_payload,
            memory_type="policy",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_changes (
                    policy_id, memory_scope, transferability, action, strength,
                    status, reason, confidence, source_run_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_id,
                    memory_scope,
                    transferability,
                    action,
                    strength,
                    status,
                    reason,
                    max(min(float(confidence), 1.0), 0.0),
                    source_run_id,
                    utc_now_iso(),
                ),
            )

    def list_policy_changes(
        self,
        *,
        status: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM policy_changes"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_policy(row) for row in rows]

    def list_policy_changes_for_scope(
        self,
        *,
        target_scope: str,
        status: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        normalized_target = _normalize_memory_scope(target_scope)
        if not normalized_target:
            return []
        query = "SELECT * FROM policy_changes"
        params: list[Any] = [normalized_target]
        clauses: list[str] = ["memory_scope = ?"]
        if status:
            clauses.append("status = ?")
            params.append(status)
        query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_policy(row) for row in rows]

    def save_memory_event(
        self,
        *,
        event_key: str,
        event_type: str,
        block_id: str = "",
        status: str = "pending",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_events (
                    event_key, event_type, block_id, status, payload_json,
                    created_at, processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '')
                ON CONFLICT(event_key) DO UPDATE SET
                    event_type = excluded.event_type,
                    block_id = excluded.block_id,
                    payload_json = excluded.payload_json
                """,
                (
                    event_key,
                    event_type,
                    block_id,
                    status,
                    _json_dumps(payload or {}),
                    now,
                ),
            )
        return self.get_memory_event(event_key) or {}

    def get_memory_event(self, event_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_events WHERE event_key = ? LIMIT 1",
                (event_key,),
            ).fetchone()
        return self._row_to_event(row) if row else None

    def mark_memory_event_processed(self, event_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_events
                SET status = 'processed', processed_at = ?
                WHERE event_key = ?
                """,
                (utc_now_iso(), event_key),
            )

    def list_memory_events(
        self,
        *,
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_events"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_event(row) for row in rows]

    def compact_processed_validation_events(
        self,
        *,
        min_payload_chars: int = 8000,
        max_rows: int = 500,
    ) -> dict[str, Any]:
        min_chars = max(int(min_payload_chars), 1)
        limit = max(int(max_rows), 1)
        compacted = 0
        before_chars = 0
        after_chars = 0
        skipped = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, payload_json
                FROM memory_events
                WHERE status = 'processed'
                  AND event_type = 'trading_validation_signal'
                  AND length(payload_json) >= ?
                  AND payload_json NOT LIKE '%"compaction_version": "memory_event_validation_v2"%'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (min_chars, limit),
            ).fetchall()
            for row in rows:
                raw_payload = str(row["payload_json"] or "{}")
                payload = _json_loads(raw_payload, {})
                if not isinstance(payload, dict):
                    skipped += 1
                    continue
                compact_payload = _compact_trading_validation_event_payload(
                    payload,
                    source_payload_chars=len(raw_payload),
                )
                compact_json = _json_dumps(compact_payload)
                if len(compact_json) >= len(raw_payload):
                    skipped += 1
                    continue
                conn.execute(
                    "UPDATE memory_events SET payload_json = ? WHERE id = ?",
                    (compact_json, int(row["id"])),
                )
                compacted += 1
                before_chars += len(raw_payload)
                after_chars += len(compact_json)
        return {
            "status": "ok",
            "event_type": "trading_validation_signal",
            "compacted_count": compacted,
            "skipped_count": skipped,
            "before_chars": before_chars,
            "after_chars": after_chars,
            "saved_chars": max(before_chars - after_chars, 0),
            "min_payload_chars": min_chars,
            "max_rows": limit,
        }

    def prune_processed_validation_events(
        self,
        *,
        retain_rows_per_venue: int = 720,
    ) -> dict[str, Any]:
        retain = max(int(retain_rows_per_venue), 1)
        rows_by_venue: dict[str, list[int]] = {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event_key
                FROM memory_events
                WHERE status = 'processed'
                  AND event_type = 'trading_validation_signal'
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
            for row in rows:
                event_key = str(row["event_key"] or "")
                parts = event_key.split(":", 2)
                venue = parts[1] if len(parts) >= 3 and parts[1] else "unknown"
                rows_by_venue.setdefault(venue, []).append(int(row["id"]))
            delete_ids: list[int] = []
            kept_count = 0
            venue_counts: dict[str, dict[str, int]] = {}
            for venue, ids in rows_by_venue.items():
                kept = ids[:retain]
                deleted = ids[retain:]
                kept_count += len(kept)
                delete_ids.extend(deleted)
                venue_counts[venue] = {
                    "kept_count": len(kept),
                    "deleted_count": len(deleted),
                }
            for index in range(0, len(delete_ids), 500):
                batch = delete_ids[index : index + 500]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                conn.execute(
                    f"DELETE FROM memory_events WHERE id IN ({placeholders})",
                    tuple(batch),
                )
        return {
            "status": "ok",
            "event_type": "trading_validation_signal",
            "retained_rows_per_venue": retain,
            "kept_count": kept_count,
            "deleted_count": len(delete_ids),
            "venues": venue_counts,
        }

    def compact_old_memory_run_payloads(
        self,
        *,
        recent_rows_per_group: int = 24,
        min_payload_chars: int = 20_000,
        input_limit_chars: int = 6_000,
        output_limit_chars: int = 8_000,
    ) -> dict[str, Any]:
        recent = max(int(recent_rows_per_group), 1)
        min_chars = max(int(min_payload_chars), 1)
        input_limit = max(int(input_limit_chars), 1000)
        output_limit = max(int(output_limit_chars), 1000)
        rows_by_group: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        compacted = 0
        skipped_recent = 0
        skipped_small = 0
        before_chars = 0
        after_chars = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, slot, status, input_json, output_json
                FROM memory_runs
                ORDER BY run_at DESC, id DESC
                """
            ).fetchall()
            for row in rows:
                group = (
                    str(row["kind"] or ""),
                    str(row["slot"] or ""),
                    str(row["status"] or ""),
                )
                rows_by_group.setdefault(group, []).append(row)
            for group_rows in rows_by_group.values():
                for index, row in enumerate(group_rows):
                    if index < recent:
                        skipped_recent += 1
                        continue
                    raw_input = str(row["input_json"] or "{}")
                    raw_output = str(row["output_json"] or "{}")
                    original_size = len(raw_input) + len(raw_output)
                    if original_size < min_chars:
                        skipped_small += 1
                        continue
                    input_payload = _json_loads(raw_input, {})
                    output_payload = _json_loads(raw_output, {})
                    compact_input = _compact_run_payload(
                        input_payload if isinstance(input_payload, dict) else {},
                        limit=input_limit,
                    )
                    compact_output = _ensure_storage_payload_limit(
                        output_payload if isinstance(output_payload, dict) else {},
                        limit=output_limit,
                        label="memory_run_output",
                        original_chars=len(raw_output),
                    )
                    input_json = _json_dumps(compact_input)
                    output_json = _json_dumps(compact_output)
                    compact_size = len(input_json) + len(output_json)
                    if compact_size >= original_size:
                        skipped_small += 1
                        continue
                    conn.execute(
                        """
                        UPDATE memory_runs
                        SET input_json = ?, output_json = ?
                        WHERE id = ?
                        """,
                        (input_json, output_json, int(row["id"])),
                    )
                    compacted += 1
                    before_chars += original_size
                    after_chars += compact_size
        return {
            "status": "ok",
            "compacted_count": compacted,
            "skipped_recent_count": skipped_recent,
            "skipped_small_count": skipped_small,
            "before_chars": before_chars,
            "after_chars": after_chars,
            "saved_chars": max(before_chars - after_chars, 0),
            "recent_rows_per_group": recent,
            "min_payload_chars": min_chars,
            "input_limit_chars": input_limit,
            "output_limit_chars": output_limit,
        }

    def compact_old_symbol_analysis_payloads(
        self,
        *,
        recent_rows_per_symbol: int = 3,
        min_payload_chars: int = 20_000,
        recent_hard_limit_chars: int = 50_000,
        prompt_limit_chars: int = 5_000,
        snapshot_limit_chars: int = 5_000,
        raw_response_limit_chars: int = 6_000,
    ) -> dict[str, Any]:
        recent = max(int(recent_rows_per_symbol), 1)
        min_chars = max(int(min_payload_chars), 1)
        recent_hard_limit = max(int(recent_hard_limit_chars), min_chars)
        prompt_limit = max(int(prompt_limit_chars), 1000)
        snapshot_limit = max(int(snapshot_limit_chars), 1000)
        raw_response_limit = max(int(raw_response_limit_chars), 1000)
        rows_by_symbol: dict[str, list[sqlite3.Row]] = {}
        compacted = 0
        forced_recent = 0
        skipped_recent = 0
        skipped_small = 0
        before_chars = 0
        after_chars = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, symbol, prompt_json, snapshot_json, raw_response_json
                FROM symbol_analyses
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
            for row in rows:
                rows_by_symbol.setdefault(str(row["symbol"] or ""), []).append(row)
            for symbol_rows in rows_by_symbol.values():
                for index, row in enumerate(symbol_rows):
                    raw_prompt = str(row["prompt_json"] or "{}")
                    raw_snapshot = str(row["snapshot_json"] or "{}")
                    raw_response = str(row["raw_response_json"] or "{}")
                    original_size = (
                        len(raw_prompt) + len(raw_snapshot) + len(raw_response)
                    )
                    is_recent = index < recent
                    if is_recent and original_size < recent_hard_limit:
                        skipped_recent += 1
                        continue
                    if original_size < min_chars:
                        skipped_small += 1
                        continue
                    prompt = _json_loads(raw_prompt, {})
                    snapshot = _json_loads(raw_snapshot, {})
                    raw = _json_loads(raw_response, {})
                    compact_prompt = _ensure_storage_payload_limit(
                        prompt if isinstance(prompt, dict) else {},
                        limit=prompt_limit,
                        label="symbol_analysis_prompt",
                        original_chars=len(raw_prompt),
                    )
                    compact_snapshot = _ensure_storage_payload_limit(
                        snapshot if isinstance(snapshot, dict) else {},
                        limit=snapshot_limit,
                        label="symbol_analysis_snapshot",
                        original_chars=len(raw_snapshot),
                    )
                    compact_raw = _ensure_storage_payload_limit(
                        raw if isinstance(raw, dict) else {},
                        limit=raw_response_limit,
                        label="symbol_analysis_raw_response",
                        original_chars=len(raw_response),
                    )
                    prompt_json = _json_dumps(compact_prompt)
                    snapshot_json = _json_dumps(compact_snapshot)
                    raw_json = _json_dumps(compact_raw)
                    compact_size = (
                        len(prompt_json) + len(snapshot_json) + len(raw_json)
                    )
                    if compact_size >= original_size:
                        skipped_small += 1
                        continue
                    conn.execute(
                        """
                        UPDATE symbol_analyses
                        SET prompt_json = ?, snapshot_json = ?, raw_response_json = ?
                        WHERE id = ?
                        """,
                        (prompt_json, snapshot_json, raw_json, int(row["id"])),
                    )
                    compacted += 1
                    if is_recent:
                        forced_recent += 1
                    before_chars += original_size
                    after_chars += compact_size
        return {
            "status": "ok",
            "compacted_count": compacted,
            "forced_recent_count": forced_recent,
            "skipped_recent_count": skipped_recent,
            "skipped_small_count": skipped_small,
            "before_chars": before_chars,
            "after_chars": after_chars,
            "saved_chars": max(before_chars - after_chars, 0),
            "recent_rows_per_symbol": recent,
            "min_payload_chars": min_chars,
            "recent_hard_limit_chars": recent_hard_limit,
            "prompt_limit_chars": prompt_limit,
            "snapshot_limit_chars": snapshot_limit,
            "raw_response_limit_chars": raw_response_limit,
        }

    def upsert_block_reflection(
        self,
        row: dict[str, Any],
        *,
        source_run_id: int | None = None,
    ) -> dict[str, Any]:
        block_id = _clean_text(row.get("block_id"), limit=180)
        if not block_id:
            raise ValueError("block_id required")
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO block_reflections (
                    block_id, symbol, name, status, exit_reason, pnl_krw, pnl_pct,
                    mfe_pct, mae_pct, hold_seconds, rule_followed, lesson_md,
                    metrics_json, source_run_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(block_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = excluded.name,
                    status = excluded.status,
                    exit_reason = excluded.exit_reason,
                    pnl_krw = excluded.pnl_krw,
                    pnl_pct = excluded.pnl_pct,
                    mfe_pct = excluded.mfe_pct,
                    mae_pct = excluded.mae_pct,
                    hold_seconds = excluded.hold_seconds,
                    rule_followed = excluded.rule_followed,
                    lesson_md = excluded.lesson_md,
                    metrics_json = excluded.metrics_json,
                    source_run_id = excluded.source_run_id,
                    updated_at = excluded.updated_at
                """,
                (
                    block_id,
                    str(row.get("symbol") or ""),
                    _clean_text(row.get("name"), limit=120),
                    str(row.get("status") or ""),
                    _clean_text(row.get("exit_reason"), limit=160),
                    _safe_float(row.get("pnl_krw")),
                    _safe_float(row.get("pnl_pct")),
                    _safe_float(row.get("mfe_pct")),
                    _safe_float(row.get("mae_pct")),
                    max(_safe_int(row.get("hold_seconds")), 0),
                    1 if row.get("rule_followed") else 0,
                    str(row.get("lesson_md") or "").strip(),
                    _json_dumps(row.get("metrics") if isinstance(row.get("metrics"), dict) else {}),
                    source_run_id,
                    now,
                    now,
                ),
            )
        return self.get_block_reflection(block_id) or {}

    def get_block_reflection(self, block_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM block_reflections WHERE block_id = ? LIMIT 1",
                (block_id,),
            ).fetchone()
        return self._row_to_reflection(row) if row else None

    def list_block_reflections(
        self,
        *,
        limit: int = 20,
        symbol: str = "",
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM block_reflections"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_reflection(row) for row in rows]

    def reflection_statuses(self, block_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(block_id or "").strip() for block_id in block_ids if str(block_id or "").strip()]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM block_reflections WHERE block_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        out = {block_id: {"status": "pending"} for block_id in ids}
        for row in rows:
            reflection = self._row_to_reflection(row)
            out[str(row["block_id"])] = {
                "status": "reflected",
                "updated_at": reflection.get("updated_at"),
                "pnl_pct": reflection.get("pnl_pct"),
                "lesson_md": _truncate(reflection.get("lesson_md"), 180),
            }
        return out

    def upsert_policy_scorecard(self, row: dict[str, Any]) -> dict[str, Any]:
        policy_id = _clean_text(row.get("policy_id"), limit=160)
        if not policy_id:
            raise ValueError("policy_id required")
        scope_payload = {**row, "policy_id": policy_id}
        memory_scope = _policy_row_scope(scope_payload)
        transferability = _policy_row_transferability(
            scope_payload,
            memory_type="policy_scorecard",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_scorecards (
                    policy_id, memory_scope, transferability, action, status,
                    sample_count, win_rate, avg_pnl_pct, expectancy_pct,
                    rule_follow_rate, confidence, reason, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    memory_scope = excluded.memory_scope,
                    transferability = excluded.transferability,
                    action = excluded.action,
                    status = excluded.status,
                    sample_count = excluded.sample_count,
                    win_rate = excluded.win_rate,
                    avg_pnl_pct = excluded.avg_pnl_pct,
                    expectancy_pct = excluded.expectancy_pct,
                    rule_follow_rate = excluded.rule_follow_rate,
                    confidence = excluded.confidence,
                    reason = excluded.reason,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    policy_id,
                    memory_scope,
                    transferability,
                    str(row.get("action") or "observe"),
                    str(row.get("status") or "candidate"),
                    max(_safe_int(row.get("sample_count")), 0),
                    max(0.0, min(_safe_float(row.get("win_rate")), 1.0)),
                    _safe_float(row.get("avg_pnl_pct")),
                    _safe_float(row.get("expectancy_pct")),
                    max(0.0, min(_safe_float(row.get("rule_follow_rate")), 1.0)),
                    max(0.0, min(_safe_float(row.get("confidence")), 1.0)),
                    _clean_text(row.get("reason"), limit=1200),
                    _json_dumps(row),
                    utc_now_iso(),
                ),
            )
        return self.get_policy_scorecard(policy_id) or {}

    def get_policy_scorecard(self, policy_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM policy_scorecards WHERE policy_id = ? LIMIT 1",
                (policy_id,),
            ).fetchone()
        return self._row_to_scorecard(row) if row else None

    def list_policy_scorecards(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM policy_scorecards
                ORDER BY
                    CASE status
                        WHEN 'active_preference' THEN 0
                        WHEN 'active_caution' THEN 1
                        WHEN 'candidate' THEN 2
                        ELSE 3
                    END,
                    confidence DESC,
                    updated_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_scorecard(row) for row in rows]

    def list_policy_scorecards_for_scope(
        self,
        *,
        target_scope: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        normalized_target = _normalize_memory_scope(target_scope)
        if not normalized_target:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM policy_scorecards
                WHERE memory_scope = ?
                ORDER BY
                    CASE status
                        WHEN 'active_preference' THEN 0
                        WHEN 'active_caution' THEN 1
                        WHEN 'candidate' THEN 2
                        ELSE 3
                    END,
                    confidence DESC,
                    updated_at DESC
                LIMIT ?
                """,
                (normalized_target, max(int(limit), 1)),
            ).fetchall()
        return [self._row_to_scorecard(row) for row in rows]

    def list_translated_policy_scorecards(
        self,
        *,
        target_scope: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        normalized_target = _normalize_memory_scope(target_scope)
        if not normalized_target:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM policy_scorecards
                WHERE memory_scope != 'core'
                  AND memory_scope != ?
                  AND transferability = 'translated'
                ORDER BY
                    CASE status
                        WHEN 'active_preference' THEN 0
                        WHEN 'active_caution' THEN 1
                        WHEN 'candidate' THEN 2
                        ELSE 3
                    END,
                    confidence DESC,
                    updated_at DESC
                LIMIT ?
                """,
                (normalized_target, max(int(limit), 1)),
            ).fetchall()
        scorecards: list[dict[str, Any]] = []
        for row in rows:
            scorecard = self._row_to_scorecard(row)
            if (
                _scope_bucket(
                    target_scope=normalized_target,
                    item_scope=_policy_row_scope(scorecard),
                    transferability=_policy_row_transferability(
                        scorecard,
                        memory_type="policy_scorecard",
                    ),
                    memory_type="policy_scorecard",
                )
                != "translated"
            ):
                continue
            scorecards.append(scorecard)
            if len(scorecards) >= max(int(limit), 1):
                break
        return scorecards

    def upsert_period_review(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        period_key = str(row.get("period_key") or "").strip()
        period_type = str(row.get("period_type") or "").strip()
        if not period_key or not period_type:
            raise ValueError("period_key and period_type required")
        metrics = row.get("metrics") or {}
        memory_scope = (
            _normalize_memory_scope(
                row.get("memory_scope") or row.get("target_scope") or row.get("scope")
            )
            or _memory_scope_from_evidence(metrics)
            or "core"
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO period_reviews (
                    period_key, period_type, memory_scope, start_date, end_date,
                    status, mode, metrics_json, review_md, policy_revision_ids_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_key, period_type, memory_scope) DO UPDATE SET
                    start_date=excluded.start_date,
                    end_date=excluded.end_date,
                    status=excluded.status,
                    mode=excluded.mode,
                    metrics_json=excluded.metrics_json,
                    review_md=excluded.review_md,
                    policy_revision_ids_json=excluded.policy_revision_ids_json,
                    updated_at=excluded.updated_at
                """,
                (
                    period_key,
                    period_type,
                    memory_scope,
                    str(row.get("start_date") or ""),
                    str(row.get("end_date") or ""),
                    str(row.get("status") or "ok"),
                    str(row.get("mode") or "deterministic"),
                    _json_dumps(metrics),
                    str(row.get("review_md") or ""),
                    _json_dumps(list(row.get("policy_revision_ids") or [])),
                    now,
                    now,
                ),
            )
        return self.get_period_review(
            period_key,
            period_type,
            target_scope=memory_scope,
        ) or {}

    def get_period_review(
        self,
        period_key: str,
        period_type: str,
        *,
        target_scope: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_scope = _normalize_memory_scope(target_scope)
        scope_filter = "AND memory_scope = ?" if normalized_scope else ""
        params: list[Any] = [period_key, period_type]
        if normalized_scope:
            params.append(normalized_scope)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM period_reviews
                WHERE period_key = ? AND period_type = ?
                {scope_filter}
                ORDER BY CASE memory_scope WHEN 'core' THEN 0 ELSE 1 END,
                    updated_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._row_to_period_review(row) if row else None

    def latest_period_review(
        self,
        period_type: str,
        *,
        target_scope: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_memory_scope(target_scope)
        scope_filter = "AND memory_scope = ?" if normalized_scope else ""
        params: list[Any] = [period_type]
        if normalized_scope:
            params.append(normalized_scope)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM period_reviews
                WHERE period_type = ?
                {scope_filter}
                ORDER BY end_date DESC, updated_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return (
            self._row_to_period_review(row)
            if row
            else {
                "status": "missing",
                "period_type": period_type,
                "memory_scope": normalized_scope or "",
            }
        )

    def list_period_reviews(
        self,
        *,
        period_type: str = "",
        target_scope: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where: list[str] = []
        if period_type:
            where.append("period_type = ?")
            params.append(period_type)
        normalized_scope = _normalize_memory_scope(target_scope)
        if normalized_scope:
            where.append("memory_scope = ?")
            params.append(normalized_scope)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM period_reviews
                {where_sql}
                ORDER BY end_date DESC, updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_period_review(row) for row in rows]

    def upsert_historical_replay(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        period_key = str(row.get("period_key") or "").strip()
        period_type = str(row.get("period_type") or "").strip()
        if not period_key or not period_type:
            raise ValueError("period_key and period_type required")
        metrics = row.get("metrics") or {}
        memory_scope = (
            _normalize_memory_scope(
                row.get("memory_scope") or row.get("target_scope") or row.get("scope")
            )
            or _memory_scope_from_evidence(metrics)
            or "core"
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO historical_replays (
                    period_key, period_type, memory_scope, start_date, end_date,
                    status, mode, case_count, metrics_json, replay_md,
                    case_reviews_json, policy_revision_ids_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_key, period_type, memory_scope) DO UPDATE SET
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    status = excluded.status,
                    mode = excluded.mode,
                    case_count = excluded.case_count,
                    metrics_json = excluded.metrics_json,
                    replay_md = excluded.replay_md,
                    case_reviews_json = excluded.case_reviews_json,
                    policy_revision_ids_json = excluded.policy_revision_ids_json,
                    updated_at = excluded.updated_at
                """,
                (
                    period_key,
                    period_type,
                    memory_scope,
                    str(row.get("start_date") or ""),
                    str(row.get("end_date") or ""),
                    str(row.get("status") or "ok"),
                    str(row.get("mode") or "deterministic"),
                    max(_safe_int(row.get("case_count")), 0),
                    _json_dumps(metrics),
                    str(row.get("replay_md") or ""),
                    _json_dumps(list(row.get("case_reviews") or [])),
                    _json_dumps(list(row.get("policy_revision_ids") or [])),
                    now,
                    now,
                ),
            )
        return self.get_historical_replay(
            period_key,
            period_type,
            target_scope=memory_scope,
        ) or {}

    def get_historical_replay(
        self,
        period_key: str,
        period_type: str,
        *,
        target_scope: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_scope = _normalize_memory_scope(target_scope)
        scope_filter = "AND memory_scope = ?" if normalized_scope else ""
        params: list[Any] = [period_key, period_type]
        if normalized_scope:
            params.append(normalized_scope)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM historical_replays
                WHERE period_key = ? AND period_type = ?
                {scope_filter}
                ORDER BY CASE memory_scope WHEN 'core' THEN 0 ELSE 1 END,
                    updated_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._row_to_historical_replay(row) if row else None

    def latest_historical_replay(
        self,
        period_type: str,
        *,
        target_scope: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_memory_scope(target_scope)
        scope_filter = "AND memory_scope = ?" if normalized_scope else ""
        params: list[Any] = [period_type]
        if normalized_scope:
            params.append(normalized_scope)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM historical_replays
                WHERE period_type = ?
                {scope_filter}
                ORDER BY end_date DESC, updated_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return (
            self._row_to_historical_replay(row)
            if row
            else {
                "status": "missing",
                "period_type": period_type,
                "memory_scope": normalized_scope or "",
            }
        )

    def list_historical_replays(
        self,
        *,
        period_type: str = "",
        target_scope: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where: list[str] = []
        if period_type:
            where.append("period_type = ?")
            params.append(period_type)
        normalized_scope = _normalize_memory_scope(target_scope)
        if normalized_scope:
            where.append("memory_scope = ?")
            params.append(normalized_scope)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM historical_replays
                {where_sql}
                ORDER BY end_date DESC, updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_historical_replay(row) for row in rows]

    def upsert_policy_revision(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        revision_id = str(row.get("revision_id") or "").strip()
        if not revision_id:
            raise ValueError("revision_id required")
        memory_scope = _policy_revision_memory_scope(row)
        transferability = _normalize_transferability(row.get("transferability")) or "direct"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_revisions (
                    revision_id, memory_scope, transferability, period_key,
                    period_type, policy_id, action, status, scope, condition_json,
                    effect_json, evidence_json, reason_md, confidence, created_at,
                    activated_at, retired_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(revision_id) DO UPDATE SET
                    memory_scope=excluded.memory_scope,
                    transferability=excluded.transferability,
                    period_key=excluded.period_key,
                    period_type=excluded.period_type,
                    policy_id=excluded.policy_id,
                    action=excluded.action,
                    status=excluded.status,
                    scope=excluded.scope,
                    condition_json=excluded.condition_json,
                    effect_json=excluded.effect_json,
                    evidence_json=excluded.evidence_json,
                    reason_md=excluded.reason_md,
                    confidence=excluded.confidence,
                    activated_at=excluded.activated_at,
                    retired_at=excluded.retired_at
                """,
                (
                    revision_id,
                    memory_scope,
                    transferability,
                    str(row.get("period_key") or ""),
                    str(row.get("period_type") or ""),
                    str(row.get("policy_id") or ""),
                    str(row.get("action") or "keep"),
                    str(row.get("status") or "candidate"),
                    str(row.get("scope") or "general"),
                    _json_dumps(row.get("condition") or {}),
                    _json_dumps(row.get("effect") or {}),
                    _json_dumps(row.get("evidence") or {}),
                    str(row.get("reason_md") or ""),
                    _safe_float(row.get("confidence")),
                    now,
                    str(row.get("activated_at") or ""),
                    str(row.get("retired_at") or ""),
                ),
            )
        rows = self.list_policy_revisions(limit=1, revision_id=revision_id)
        return rows[0] if rows else {}

    def list_policy_revisions(
        self,
        *,
        status: str = "",
        period_type: str = "",
        revision_id: str = "",
        target_scope: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if period_type:
            where.append("period_type = ?")
            params.append(period_type)
        if revision_id:
            where.append("revision_id = ?")
            params.append(revision_id)
        normalized_scope = _normalize_memory_scope(target_scope)
        if normalized_scope:
            where.append("memory_scope = ?")
            params.append(normalized_scope)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM policy_revisions
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_policy_revision(row) for row in rows]

    def upsert_policy_outcome(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        policy_id = str(row.get("policy_id") or "").strip()
        rule_id = str(row.get("rule_id") or "").strip()
        period_key = str(row.get("period_key") or "").strip()
        period_type = str(row.get("period_type") or "").strip()
        if not policy_id or not rule_id or not period_key or not period_type:
            raise ValueError("policy_id, rule_id, period_key, and period_type required")
        memory_scope = _policy_revision_memory_scope(row)
        transferability = _normalize_transferability(row.get("transferability")) or "direct"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_outcomes (
                    policy_id, rule_id, period_key, period_type, memory_scope,
                    transferability, sample_count, avg_pnl_pct, win_rate,
                    expectancy_pct, max_drawdown_pct, rule_follow_rate,
                    helped_count, hurt_count, notes_md, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, rule_id, period_key, period_type, memory_scope)
                DO UPDATE SET
                    transferability=excluded.transferability,
                    sample_count=excluded.sample_count,
                    avg_pnl_pct=excluded.avg_pnl_pct,
                    win_rate=excluded.win_rate,
                    expectancy_pct=excluded.expectancy_pct,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    rule_follow_rate=excluded.rule_follow_rate,
                    helped_count=excluded.helped_count,
                    hurt_count=excluded.hurt_count,
                    notes_md=excluded.notes_md,
                    updated_at=excluded.updated_at
                """,
                (
                    policy_id,
                    rule_id,
                    period_key,
                    period_type,
                    memory_scope,
                    transferability,
                    _safe_int(row.get("sample_count")),
                    _safe_float(row.get("avg_pnl_pct")),
                    _safe_float(row.get("win_rate")),
                    _safe_float(row.get("expectancy_pct")),
                    _safe_float(row.get("max_drawdown_pct")),
                    _safe_float(row.get("rule_follow_rate")),
                    _safe_int(row.get("helped_count")),
                    _safe_int(row.get("hurt_count")),
                    str(row.get("notes_md") or ""),
                    now,
                ),
            )
        return self.get_policy_outcome(
            policy_id=policy_id,
            rule_id=rule_id,
            period_key=period_key,
            period_type=period_type,
            target_scope=memory_scope,
        ) or {}

    def get_policy_outcome(
        self,
        *,
        policy_id: str,
        rule_id: str,
        period_key: str,
        period_type: str,
        target_scope: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_scope = _normalize_memory_scope(target_scope)
        scope_filter = "AND memory_scope = ?" if normalized_scope else ""
        params: list[Any] = [policy_id, rule_id, period_key, period_type]
        if normalized_scope:
            params.append(normalized_scope)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM policy_outcomes
                WHERE policy_id = ? AND rule_id = ? AND period_key = ? AND period_type = ?
                {scope_filter}
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._row_to_policy_outcome(row) if row else None

    def list_policy_outcomes(
        self,
        *,
        target_scope: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        normalized_scope = _normalize_memory_scope(target_scope)
        where = "WHERE memory_scope = ?" if normalized_scope else ""
        params: list[Any] = []
        if normalized_scope:
            params.append(normalized_scope)
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM policy_outcomes
                {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_policy_outcome(row) for row in rows]

    def latest_policy_rule(self, policy_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM policy_rules
                WHERE policy_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (str(policy_id or ""),),
            ).fetchone()
        return self._row_to_policy_rule(row) if row else None

    def list_policy_rules(
        self,
        *,
        status: str = "",
        active_only: bool = False,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM policy_rules"
        params: list[Any] = []
        clauses: list[str] = []
        if active_only:
            clauses.append("status IN ('active_caution', 'active_preference')")
        elif status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY status DESC, policy_id ASC, version DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_policy_rule(row) for row in rows]

    def list_policy_rules_for_scope(
        self,
        *,
        target_scope: str,
        status: str = "",
        active_only: bool = False,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        normalized_target = _normalize_memory_scope(target_scope)
        if not normalized_target:
            return []
        query = "SELECT * FROM policy_rules"
        params: list[Any] = [normalized_target]
        clauses: list[str] = ["memory_scope = ?"]
        if active_only:
            clauses.append("status IN ('active_caution', 'active_preference')")
        elif status:
            clauses.append("status = ?")
            params.append(status)
        query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY status DESC, policy_id ASC, version DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_policy_rule(row) for row in rows]

    def prune_retired_policy_rules(
        self,
        *,
        retain_per_policy: int = 2,
    ) -> list[dict[str, Any]]:
        retain = max(int(retain_per_policy), 0)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM policy_rules
                WHERE status = 'retired'
                ORDER BY policy_id ASC, version DESC
                """
            ).fetchall()
            delete_rows: list[dict[str, Any]] = []
            seen_by_policy: dict[str, int] = {}
            for row in rows:
                policy_id = str(row["policy_id"] or "")
                seen = seen_by_policy.get(policy_id, 0)
                if seen >= retain:
                    delete_rows.append(self._row_to_policy_rule(row))
                seen_by_policy[policy_id] = seen + 1
            if delete_rows:
                conn.executemany(
                    """
                    DELETE FROM policy_rules
                    WHERE policy_id = ? AND version = ?
                    """,
                    [
                        (row["policy_id"], int(row["version"]))
                        for row in delete_rows
                    ],
                )
        return delete_rows

    def vacuum(self) -> None:
        with self._connect() as conn:
            conn.execute("VACUUM")

    def upsert_policy_rule(self, row: dict[str, Any]) -> dict[str, Any]:
        policy_id = _clean_text(row.get("policy_id"), limit=160)
        if not policy_id:
            raise ValueError("policy_id required")
        version = max(_safe_int(row.get("version")), 1)
        rule_id = _clean_text(row.get("rule_id") or f"{policy_id}@v{version}", limit=220)
        scope_payload = {**row, "policy_id": policy_id}
        memory_scope = _policy_row_scope(scope_payload)
        transferability = _policy_row_transferability(
            scope_payload,
            memory_type="policy_rule",
        )
        now = utc_now_iso()
        status = str(row.get("status") or "candidate")
        activated_at = str(row.get("activated_at") or "")
        if status.startswith("active") and not activated_at:
            activated_at = now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_rules (
                    policy_id, version, rule_id, memory_scope, transferability,
                    status, action, condition_json, effect_json, reason,
                    evidence_json, source_scorecard_json, file_path, created_at,
                    activated_at, retired_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, version) DO UPDATE SET
                    rule_id = excluded.rule_id,
                    memory_scope = excluded.memory_scope,
                    transferability = excluded.transferability,
                    status = excluded.status,
                    action = excluded.action,
                    condition_json = excluded.condition_json,
                    effect_json = excluded.effect_json,
                    reason = excluded.reason,
                    evidence_json = excluded.evidence_json,
                    source_scorecard_json = excluded.source_scorecard_json,
                    file_path = excluded.file_path,
                    activated_at = excluded.activated_at,
                    retired_at = excluded.retired_at
                """,
                (
                    policy_id,
                    version,
                    rule_id,
                    memory_scope,
                    transferability,
                    status,
                    str(row.get("action") or "observe"),
                    _json_dumps(row.get("condition") if isinstance(row.get("condition"), dict) else {}),
                    _json_dumps(row.get("effect") if isinstance(row.get("effect"), dict) else {}),
                    _clean_text(row.get("reason"), limit=1200),
                    _json_dumps(row.get("evidence") if isinstance(row.get("evidence"), dict) else row.get("evidence") or {}),
                    _json_dumps(row.get("source_scorecard") if isinstance(row.get("source_scorecard"), dict) else {}),
                    str(row.get("file_path") or ""),
                    str(row.get("created_at") or now),
                    activated_at,
                    str(row.get("retired_at") or ""),
                ),
            )
        return self.latest_policy_rule(policy_id) or {}

    def retire_policy_rule(self, policy_id: str, version: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE policy_rules
                SET status = 'retired', retired_at = ?
                WHERE policy_id = ? AND version = ?
                """,
                (utc_now_iso(), str(policy_id or ""), int(version)),
            )

    def record_telegram_send(
        self,
        *,
        trading_day: str,
        slot: str,
        status: str,
        result: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_sends (trading_day, slot, status, result_json, sent_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trading_day, slot) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    sent_at = excluded.sent_at
                """,
                (trading_day, slot, status, _json_dumps(result), utc_now_iso()),
            )

    def _latest_run_for_scope(
        self,
        conn: sqlite3.Connection,
        *,
        target_scope: str,
    ) -> sqlite3.Row | None:
        if not target_scope:
            return conn.execute(
                """
                SELECT id, run_at, kind, slot, status, mode, model, error_message
                FROM memory_runs
                ORDER BY run_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        rows = conn.execute(
            """
            SELECT id, run_at, kind, slot, status, mode, model, error_message, input_json
            FROM memory_runs
            ORDER BY run_at DESC, id DESC
            LIMIT 120
            """
        ).fetchall()
        core_fallback: sqlite3.Row | None = None
        for row in rows:
            scope = _run_memory_scope(row)
            if scope == target_scope:
                return row
            if scope == "core" and core_fallback is None:
                core_fallback = row
        return core_fallback

    def _latest_telegram_send_for_scope(
        self,
        conn: sqlite3.Connection,
        *,
        target_scope: str,
    ) -> dict[str, Any]:
        if not target_scope:
            row = conn.execute(
                "SELECT * FROM telegram_sends ORDER BY sent_at DESC, id DESC LIMIT 1"
            ).fetchone()
            return (
                self._row_to_telegram_send(row)
                if row
                else {"status": "missing"}
            )
        rows = conn.execute(
            """
            SELECT *
            FROM daily_journals
            WHERE sent_telegram = 1
            ORDER BY updated_at DESC, id DESC
            LIMIT 120
            """
        ).fetchall()
        for row in rows:
            journal = self._row_to_journal(row)
            scope = _journal_memory_scope(journal)
            payload = {
                "id": journal["id"],
                "trading_day": journal["trading_day"],
                "slot": journal["slot"],
                "status": "sent" if journal.get("sent_telegram") else "not_sent",
                "result": journal.get("telegram_result") or {},
                "sent_at": journal.get("updated_at") or journal.get("created_at") or "",
                "memory_scope": scope,
                "source": "daily_journal",
            }
            if scope == target_scope:
                return payload
        return {"status": "missing", "memory_scope": target_scope}

    def status(self, *, target_scope: str | None = None) -> dict[str, Any]:
        normalized_scope = _normalize_memory_scope(target_scope)
        with self._connect() as conn:
            run_count = int(conn.execute("SELECT COUNT(*) FROM memory_runs").fetchone()[0])
            journal_count = int(conn.execute("SELECT COUNT(*) FROM daily_journals").fetchone()[0])
            insight_count = int(conn.execute("SELECT COUNT(*) FROM memory_insights").fetchone()[0])
            active_policy_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM policy_changes WHERE status = 'active'"
                ).fetchone()[0]
            )
            pending_event_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM memory_events WHERE status = 'pending'"
                ).fetchone()[0]
            )
            reflection_count = int(conn.execute("SELECT COUNT(*) FROM block_reflections").fetchone()[0])
            scorecard_count = int(conn.execute("SELECT COUNT(*) FROM policy_scorecards").fetchone()[0])
            policy_rule_count = int(conn.execute("SELECT COUNT(*) FROM policy_rules").fetchone()[0])
            latest_reflection_at = str(
                conn.execute(
                    """
                    SELECT MAX(COALESCE(NULLIF(updated_at, ''), created_at))
                    FROM block_reflections
                    """
                ).fetchone()[0]
                or ""
            )
            latest_memory_event_at = str(
                conn.execute("SELECT MAX(created_at) FROM memory_events").fetchone()[0]
                or ""
            )
            historical_replay_count = int(
                conn.execute("SELECT COUNT(*) FROM historical_replays").fetchone()[0]
            )
            active_policy_rule_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM policy_rules
                    WHERE status IN ('active_caution', 'active_preference')
                    """
                ).fetchone()[0]
            )
            seed_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM memory_runs WHERE kind = 'seed'"
                ).fetchone()[0]
            )
            latest_run = self._latest_run_for_scope(
                conn,
                target_scope=normalized_scope,
            )
            latest_replay = conn.execute(
                """
                SELECT * FROM historical_replays
                ORDER BY end_date DESC, updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            latest_send = self._latest_telegram_send_for_scope(
                conn,
                target_scope=normalized_scope,
            )
            jue_wiki_selection_provenance = (
                self._jue_wiki_selection_provenance_status(conn)
            )
        return {
            "status": "ok",
            "db_path": str(self.path),
            "run_count": run_count,
            "journal_count": journal_count,
            "insight_count": insight_count,
            "active_policy_count": active_policy_count,
            "pending_event_count": pending_event_count,
            "reflection_count": reflection_count,
            "latest_reflection_at": latest_reflection_at,
            "latest_memory_event_at": latest_memory_event_at,
            "scorecard_count": scorecard_count,
            "policy_rule_count": policy_rule_count,
            "active_policy_rule_count": active_policy_rule_count,
            "historical_replay_count": historical_replay_count,
            "seeded": seed_count > 0,
            "latest_run": (
                self._row_to_run_summary(latest_run)
                if latest_run
                else {"status": "missing"}
            ),
            "latest_historical_replay": (
                self._row_to_historical_replay(latest_replay)
                if latest_replay
                else {"status": "missing"}
            ),
            "latest_telegram_send": (
                latest_send
                if isinstance(latest_send, dict)
                else {"status": "missing"}
            ),
            "jue_wiki_selection_provenance": jue_wiki_selection_provenance,
        }

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_at": row["run_at"],
            "kind": row["kind"],
            "slot": row["slot"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "error_message": _compact_error_message(row["error_message"]),
            "input": _compact_run_payload(_json_loads(row["input_json"], {})),
            "output": _compact_run_payload(_json_loads(row["output_json"], {})),
        }

    @staticmethod
    def _row_to_run_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_at": row["run_at"],
            "kind": row["kind"],
            "slot": row["slot"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "error_message": _compact_error_message(row["error_message"]),
        }

    @staticmethod
    def _row_to_journal(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "trading_day": row["trading_day"],
            "slot": row["slot"],
            "slot_label": SLOT_LABELS.get(row["slot"], row["slot"]),
            "title": row["title"],
            "message_md": _compact_message_md(row["message_md"]),
            "file_path": row["file_path"],
            "context": _compact_ritual_context(_json_loads(row["context_json"], {})),
            "sent_telegram": bool(row["sent_telegram"]),
            "telegram_result": _compact_telegram_result(
                _json_loads(row["telegram_result_json"], {})
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_insight(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "memory_type": row["memory_type"],
            "key": row["key"],
            "memory_scope": row["memory_scope"],
            "scope": row["memory_scope"],
            "transferability": row["transferability"],
            "status": row["status"],
            "confidence": float(row["confidence"] or 0),
            "summary_md": row["summary_md"],
            "evidence": _json_loads(row["evidence_json"], []),
            "source_run_id": row["source_run_id"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _symbol_analysis_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "symbol": row["symbol"],
            "name": row["name"],
            "trigger": row["trigger"],
            "source": row["source"],
            "model": row["model"],
            "status": row["status"],
            "summary": row["summary"],
            "short_view": row["short_view"],
            "mid_view": row["mid_view"],
            "long_view": row["long_view"],
            "stance": row["stance"],
            "confidence": float(row["confidence"] or 0),
            "reasons": _json_loads(row["reasons_json"], []),
            "risks": _json_loads(row["risks_json"], []),
            "data_gaps": _json_loads(row["data_gaps_json"], []),
            "triggers": _json_loads(row["triggers_json"], []),
            "target_candidates": _json_loads(row["target_candidates_json"], []),
            "stop_candidates": _json_loads(row["stop_candidates_json"], []),
            "snapshot": _json_loads(row["snapshot_json"], {}),
            "error_message": _compact_error_message(row["error_message"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_policy(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "policy_id": row["policy_id"],
            "memory_scope": row["memory_scope"],
            "scope": row["memory_scope"],
            "transferability": row["transferability"],
            "action": row["action"],
            "strength": row["strength"],
            "status": row["status"],
            "reason": row["reason"],
            "confidence": float(row["confidence"] or 0),
            "source_run_id": row["source_run_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_telegram_send(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "trading_day": row["trading_day"],
            "slot": row["slot"],
            "status": row["status"],
            "result": _compact_telegram_result(_json_loads(row["result_json"], {})),
            "sent_at": row["sent_at"],
        }

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "event_key": row["event_key"],
            "event_type": row["event_type"],
            "block_id": row["block_id"],
            "status": row["status"],
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
            "processed_at": row["processed_at"],
        }

    @staticmethod
    def _row_to_reflection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "block_id": row["block_id"],
            "symbol": row["symbol"],
            "name": row["name"],
            "status": row["status"],
            "exit_reason": row["exit_reason"],
            "pnl_krw": float(row["pnl_krw"] or 0),
            "pnl_pct": float(row["pnl_pct"] or 0),
            "mfe_pct": float(row["mfe_pct"] or 0),
            "mae_pct": float(row["mae_pct"] or 0),
            "hold_seconds": int(row["hold_seconds"] or 0),
            "rule_followed": bool(row["rule_followed"]),
            "lesson_md": row["lesson_md"],
            "metrics": _json_loads(row["metrics_json"], {}),
            "source_run_id": row["source_run_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_scorecard(row: sqlite3.Row) -> dict[str, Any]:
        raw = _json_loads(row["raw_json"], {})
        if not isinstance(raw, dict):
            raw = {}
        return {
            **raw,
            "policy_id": row["policy_id"],
            "memory_scope": row["memory_scope"],
            "scope": row["memory_scope"],
            "transferability": row["transferability"],
            "action": row["action"],
            "status": row["status"],
            "sample_count": int(row["sample_count"] or 0),
            "win_rate": float(row["win_rate"] or 0),
            "avg_pnl_pct": float(row["avg_pnl_pct"] or 0),
            "expectancy_pct": float(row["expectancy_pct"] or 0),
            "rule_follow_rate": float(row["rule_follow_rate"] or 0),
            "confidence": float(row["confidence"] or 0),
            "reason": row["reason"],
            "raw": raw,
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_policy_rule(row: sqlite3.Row) -> dict[str, Any]:
        source_scorecard = _json_loads(row["source_scorecard_json"], {})
        if not isinstance(source_scorecard, dict):
            source_scorecard = {}
        return {
            "policy_id": row["policy_id"],
            "version": int(row["version"] or 0),
            "rule_id": row["rule_id"],
            "memory_scope": row["memory_scope"],
            "scope": row["memory_scope"],
            "transferability": row["transferability"],
            "status": row["status"],
            "action": row["action"],
            "condition": _json_loads(row["condition_json"], {}),
            "effect": _json_loads(row["effect_json"], {}),
            "reason": row["reason"],
            "evidence": _json_loads(row["evidence_json"], {}),
            "source_scorecard": source_scorecard,
            "file_path": row["file_path"],
            "created_at": row["created_at"],
            "activated_at": row["activated_at"],
            "retired_at": row["retired_at"],
        }

    @staticmethod
    def _row_to_period_review(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "memory_scope": row["memory_scope"],
            "scope": row["memory_scope"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "status": row["status"],
            "mode": row["mode"],
            "metrics": _json_loads(row["metrics_json"], {}),
            "review_md": row["review_md"],
            "policy_revision_ids": _json_loads(row["policy_revision_ids_json"], []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_historical_replay(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "memory_scope": row["memory_scope"],
            "scope": row["memory_scope"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "status": row["status"],
            "mode": row["mode"],
            "case_count": int(row["case_count"] or 0),
            "metrics": _json_loads(row["metrics_json"], {}),
            "replay_md": row["replay_md"],
            "case_reviews": _json_loads(row["case_reviews_json"], []),
            "policy_revision_ids": _json_loads(row["policy_revision_ids_json"], []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_policy_revision(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "revision_id": row["revision_id"],
            "memory_scope": row["memory_scope"],
            "transferability": row["transferability"],
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "policy_id": row["policy_id"],
            "action": row["action"],
            "status": row["status"],
            "scope": row["scope"],
            "condition": _json_loads(row["condition_json"], {}),
            "effect": _json_loads(row["effect_json"], {}),
            "evidence": _json_loads(row["evidence_json"], {}),
            "reason_md": row["reason_md"],
            "confidence": float(row["confidence"] or 0),
            "created_at": row["created_at"],
            "activated_at": row["activated_at"],
            "retired_at": row["retired_at"],
        }

    @staticmethod
    def _row_to_policy_outcome(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "policy_id": row["policy_id"],
            "rule_id": row["rule_id"],
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "memory_scope": row["memory_scope"],
            "transferability": row["transferability"],
            "sample_count": int(row["sample_count"] or 0),
            "avg_pnl_pct": float(row["avg_pnl_pct"] or 0),
            "win_rate": float(row["win_rate"] or 0),
            "expectancy_pct": float(row["expectancy_pct"] or 0),
            "max_drawdown_pct": float(row["max_drawdown_pct"] or 0),
            "rule_follow_rate": float(row["rule_follow_rate"] or 0),
            "helped_count": int(row["helped_count"] or 0),
            "hurt_count": int(row["hurt_count"] or 0),
            "notes_md": row["notes_md"],
            "updated_at": row["updated_at"],
        }


class InvestmentMemoryService:
    def __init__(
        self,
        *,
        config: InvestmentMemoryConfig,
        codex_runtime: CodexNativeRuntime | None = None,
        telegram: TelegramSender | None = None,
        calendar: KRXHolidayCalendar | None = None,
        wiki_context_provider: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.root = Path(config.root_path)
        self.repository = InvestmentMemoryRepository(config.db_path)
        self.lifecycle_repository = JueLifecycleRepository(config.db_path)
        self.codex_runtime = codex_runtime
        self.telegram = telegram
        self.calendar = calendar or KRXHolidayCalendar()
        self.wiki_context_provider = wiki_context_provider
        self._validation_repair_ops_cache: dict[
            tuple[str, int],
            tuple[float, dict[str, Any]],
        ] = {}

    def _clear_validation_repair_ops_cache(self) -> None:
        self._validation_repair_ops_cache.clear()

    def _ensure_dirs(self) -> None:
        directories = [
            self.root,
            self.root / "policies",
            self.root / "policies" / "rules",
            self.root / "journals",
            self.root / "symbols",
            self.root / "sectors",
            self.root / "regimes",
            self.root / "blocks",
            self.root / "skills",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def initialize(self, *, force: bool = False) -> dict[str, Any]:
        self._ensure_dirs()

        written: list[str] = []
        for path, content in self._default_memory_files().items():
            if force or not path.exists():
                path.write_text(content, encoding="utf-8")
                written.append(str(path))

        legacy = self._legacy_strategy_extract()
        legacy_path = self.root / "policies" / "legacy_strategy_extract.md"
        if legacy and (force or not legacy_path.exists()):
            legacy_path.write_text(legacy, encoding="utf-8")
            written.append(str(legacy_path))

        return {
            "status": "ok",
            "root_path": str(self.root),
            "db_path": str(self.repository.path),
            "written": written,
            "written_count": len(written),
        }

    def _status_memory_scopes(self, target_scope: str) -> list[str]:
        if target_scope:
            return [target_scope]
        return ["kis", "binance"]

    @staticmethod
    def _period_coverage_item(
        row: dict[str, Any],
        *,
        fallback_scope: str,
    ) -> dict[str, Any]:
        return {
            "status": str(row.get("status") or "missing"),
            "period_key": str(row.get("period_key") or ""),
            "period_type": str(row.get("period_type") or ""),
            "memory_scope": str(row.get("memory_scope") or fallback_scope),
            "end_date": str(row.get("end_date") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "case_count": _safe_int(row.get("case_count")),
        }

    def _period_memory_coverage(self, *, target_scope: str) -> dict[str, Any]:
        scopes = self._status_memory_scopes(target_scope)
        weekly_reviews: dict[str, dict[str, Any]] = {}
        weekly_replays: dict[str, dict[str, Any]] = {}
        monthly_reviews: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for scope in scopes:
            weekly_review = self.repository.latest_period_review(
                "weekly",
                target_scope=scope,
            )
            weekly_replay = self.repository.latest_historical_replay(
                "weekly",
                target_scope=scope,
            )
            monthly_review = self.repository.latest_period_review(
                "monthly",
                target_scope=scope,
            )
            weekly_reviews[scope] = self._period_coverage_item(
                weekly_review,
                fallback_scope=scope,
            )
            weekly_replays[scope] = self._period_coverage_item(
                weekly_replay,
                fallback_scope=scope,
            )
            monthly_reviews[scope] = self._period_coverage_item(
                monthly_review,
                fallback_scope=scope,
            )
            if not self._period_work_complete(weekly_review):
                missing.append(f"{scope}:weekly_review")
            if not self._period_work_complete(weekly_replay):
                missing.append(f"{scope}:weekly_replay")
            if not self._period_work_complete(monthly_review):
                missing.append(f"{scope}:monthly_review")
        return {
            "status": "needs_attention" if missing else "ok",
            "scopes": scopes,
            "missing": missing,
            "weekly_reviews": weekly_reviews,
            "weekly_replays": weekly_replays,
            "monthly_reviews": monthly_reviews,
        }

    def status(
        self,
        *,
        scope: str | None = None,
        compact: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        self.sync_policy_rules()
        normalized_scope = _normalize_memory_scope(scope)
        repo_status = self.repository.status(target_scope=normalized_scope)
        decision_skills = self._decision_skills()
        today = self._trading_day()
        validation_repair_backlog = self.validation_repair_backlog(
            target_scope=normalized_scope,
            limit=8,
        )
        scope_label = (
            "KIS 쥬 메모리"
            if normalized_scope == "kis"
            else "Binance 쥬 메모리"
            if normalized_scope == "binance"
            else "통합 쥬 메모리"
        )
        period_memory_coverage = self._period_memory_coverage(
            target_scope=normalized_scope
        )
        action_reference_recovery = _jue_wiki_action_reference_recovery_summary(
            self.repository.list_policy_scorecards(limit=200),
            target_scope=normalized_scope,
        )
        if compact:
            today_journals = _journal_rows_for_target_scope(
                self.repository.journals_for_day(today),
                target_scope=normalized_scope,
                limit=4,
            )
            active_policies = _active_policies_for_target_scope(
                self.active_policies(limit=40),
                target_scope=normalized_scope,
            )[:8]
            policy_scorecards = _policy_rows_for_target_scope(
                self.repository.list_policy_scorecards(limit=80),
                target_scope=normalized_scope,
                memory_type="policy_scorecard",
            )[:8]
            policy_rules = _policy_rows_for_target_scope(
                self.repository.list_policy_rules(limit=80),
                target_scope=normalized_scope,
                memory_type="policy_rule",
            )[:8]
            recent_reflections = _reflection_rows_for_target_scope(
                self.repository.list_block_reflections(limit=80),
                target_scope=normalized_scope,
                limit=5,
            )
            compact_backlog = _compact_memory_validation_backlog(
                validation_repair_backlog,
                limit=6,
            )
            return {
                **repo_status,
                "compact": True,
                "root_path": str(self.root),
                "model": str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                "reasoning_effort": str(
                    getattr(self.codex_runtime, "resolved_reasoning_effort", "")
                ),
                "llm_ready": bool(getattr(self.codex_runtime, "ready", False)),
                "policy_mode": self.config.policy_mode,
                "persona_tone": self.config.persona_tone,
                "memory_scope": normalized_scope or "all",
                "scope_label": scope_label,
                "today": today,
                "period_memory_coverage": period_memory_coverage,
                "jue_wiki_action_reference_recovery": action_reference_recovery,
                "today_journals": _compact_memory_journals(
                    today_journals,
                    limit=4,
                    message_limit=260,
                ),
                "active_policies": _compact_policy_summaries_for_budget(
                    active_policies,
                    limit=8,
                    summary_limit=180,
                ),
                "policy_scorecards": _compact_policy_summaries_for_budget(
                    policy_scorecards,
                    limit=8,
                    summary_limit=180,
                ),
                "policy_rules": _compact_memory_policy_rules(policy_rules, limit=8),
                "recent_reflections": _compact_memory_reflections(
                    recent_reflections,
                    limit=5,
                ),
                "scoped_journal_count": len(today_journals),
                "scoped_policy_scorecard_count": len(policy_scorecards),
                "scoped_policy_rule_count": len(policy_rules),
                "scoped_reflection_count": len(recent_reflections),
                "latest_historical_replay": _compact_memory_historical_replay(
                    self.repository.latest_historical_replay(
                        "weekly",
                        target_scope=normalized_scope,
                    )
                ),
                "validation_repair_backlog": compact_backlog,
                "validation_repair_backlog_status": compact_backlog.get("status"),
                "validation_repair_backlog_count": int(
                    compact_backlog.get("item_count") or 0
                ),
                "block_design_constraints": {
                    "status": "compact",
                    "source": "validation_repair_backlog",
                    "item_count": compact_backlog.get("item_count", 0),
                },
                "validation_recovery_summary": {
                    "status": compact_backlog.get("status"),
                    "item_count": compact_backlog.get("item_count", 0),
                },
                **_decision_skill_status_payload(decision_skills),
            }
        rule_evaluation = self.evaluate_policy_rules(target_scope=normalized_scope)
        block_design_constraints = _build_block_design_constraints(
            validation_repair_backlog=validation_repair_backlog,
            rule_evaluation=rule_evaluation,
            limit=8,
        )
        validation_recovery_summary = _build_validation_recovery_summary(
            validation_repair_backlog=validation_repair_backlog,
            block_design_constraints=block_design_constraints,
            rule_evaluation=rule_evaluation,
            limit=8,
        )
        next_block_design_playbook = _build_next_block_design_playbook(
            validation_recovery_summary=validation_recovery_summary,
            block_design_constraints=block_design_constraints,
            rule_evaluation=rule_evaluation,
            limit=8,
        )
        today_journals = _journal_rows_for_target_scope(
            self.repository.journals_for_day(today),
            target_scope=normalized_scope,
        )
        active_policies = _active_policies_for_target_scope(
            self.active_policies(limit=80),
            target_scope=normalized_scope,
        )[:12]
        policy_scorecards = _policy_rows_for_target_scope(
            self.repository.list_policy_scorecards(limit=80),
            target_scope=normalized_scope,
            memory_type="policy_scorecard",
        )[:12]
        policy_rules = _policy_rows_for_target_scope(
            self.repository.list_policy_rules(limit=80),
            target_scope=normalized_scope,
            memory_type="policy_rule",
        )[:12]
        recent_reflections = _reflection_rows_for_target_scope(
            self.repository.list_block_reflections(limit=80),
            target_scope=normalized_scope,
            limit=6,
        )
        return {
            **repo_status,
            "root_path": str(self.root),
            "model": str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            "reasoning_effort": str(
                getattr(self.codex_runtime, "resolved_reasoning_effort", "")
            ),
            "llm_ready": bool(getattr(self.codex_runtime, "ready", False)),
            "policy_mode": self.config.policy_mode,
            "persona_tone": self.config.persona_tone,
            "memory_scope": normalized_scope or "all",
            "scope_label": scope_label,
            "today": today,
            "period_memory_coverage": period_memory_coverage,
            "jue_wiki_action_reference_recovery": action_reference_recovery,
            "today_journals": today_journals,
            "active_policies": active_policies,
            "policy_scorecards": policy_scorecards,
            "policy_rules": policy_rules,
            "recent_reflections": recent_reflections,
            "scoped_journal_count": len(today_journals),
            "scoped_policy_scorecard_count": len(policy_scorecards),
            "scoped_policy_rule_count": len(policy_rules),
            "scoped_reflection_count": len(recent_reflections),
            "latest_historical_replay": self.repository.latest_historical_replay(
                "weekly",
                target_scope=normalized_scope,
            ),
            "validation_repair_backlog": validation_repair_backlog,
            "validation_repair_backlog_status": validation_repair_backlog.get("status"),
            "validation_repair_backlog_count": int(
                validation_repair_backlog.get("item_count") or 0
            ),
            "block_design_constraints": block_design_constraints,
            "validation_recovery_summary": validation_recovery_summary,
            "next_block_design_playbook": next_block_design_playbook,
            **_decision_skill_status_payload(decision_skills),
        }

    def compact_runtime_storage(
        self,
        *,
        policy_retired_keep: int = 2,
        validation_event_min_payload_chars: int = 8000,
        validation_event_max_rows: int = 1000,
        validation_event_retained_rows_per_venue: int = 720,
        memory_run_recent_rows_per_group: int = 24,
        memory_run_min_payload_chars: int = 20_000,
        symbol_analysis_recent_rows_per_symbol: int = 3,
        symbol_analysis_min_payload_chars: int = 20_000,
        vacuum: bool = True,
    ) -> dict[str, Any]:
        self.initialize()
        rules_dir = (self.root / "policies" / "rules").resolve()
        deleted_rules = self.repository.prune_retired_policy_rules(
            retain_per_policy=policy_retired_keep,
        )
        compacted_validation_events = (
            self.repository.compact_processed_validation_events(
                min_payload_chars=validation_event_min_payload_chars,
                max_rows=validation_event_max_rows,
            )
        )
        pruned_validation_events = self.repository.prune_processed_validation_events(
            retain_rows_per_venue=validation_event_retained_rows_per_venue,
        )
        compacted_memory_runs = self.repository.compact_old_memory_run_payloads(
            recent_rows_per_group=memory_run_recent_rows_per_group,
            min_payload_chars=memory_run_min_payload_chars,
        )
        compacted_symbol_analyses = self.repository.compact_old_symbol_analysis_payloads(
            recent_rows_per_symbol=symbol_analysis_recent_rows_per_symbol,
            min_payload_chars=symbol_analysis_min_payload_chars,
        )
        deleted_file_count = 0
        skipped_file_count = 0
        for row in deleted_rules:
            file_path = str(row.get("file_path") or "").strip()
            if not file_path:
                continue
            try:
                candidate = Path(file_path).resolve()
            except OSError:
                skipped_file_count += 1
                continue
            if candidate == rules_dir or rules_dir not in candidate.parents:
                skipped_file_count += 1
                continue
            try:
                if candidate.exists():
                    candidate.unlink()
                    deleted_file_count += 1
            except OSError:
                skipped_file_count += 1
        vacuumed = False
        if vacuum and (
            deleted_rules
            or int(compacted_validation_events.get("compacted_count") or 0) > 0
            or int(pruned_validation_events.get("deleted_count") or 0) > 0
            or int(compacted_memory_runs.get("compacted_count") or 0) > 0
            or int(compacted_symbol_analyses.get("compacted_count") or 0) > 0
        ):
            self.repository.vacuum()
            vacuumed = True
        return {
            "status": "ok",
            "policy_rules": {
                "deleted_count": len(deleted_rules),
                "deleted_file_count": deleted_file_count,
                "skipped_file_count": skipped_file_count,
                "retained_retired_per_policy": max(int(policy_retired_keep), 0),
            },
            "validation_events": compacted_validation_events,
            "validation_event_retention": pruned_validation_events,
            "memory_runs": compacted_memory_runs,
            "symbol_analyses": compacted_symbol_analyses,
            "vacuum": vacuumed,
        }

    def today(
        self,
        *,
        now: datetime | None = None,
        scope: str | None = None,
        compact: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        self.sync_policy_rules()
        trading_day = self._trading_day(now)
        normalized_scope = _normalize_memory_scope(scope)
        decision_skills = self._decision_skills()
        validation_repair_backlog = self.validation_repair_backlog(
            target_scope=normalized_scope,
            limit=8,
        )
        if compact:
            policy_scorecards = _policy_rows_for_target_scope(
                self.repository.list_policy_scorecards(limit=80),
                target_scope=normalized_scope,
                memory_type="policy_scorecard",
            )[:8]
            policy_rules = _policy_rows_for_target_scope(
                self.repository.list_policy_rules(limit=80),
                target_scope=normalized_scope,
                memory_type="policy_rule",
            )[:8]
            recent_reflections = _reflection_rows_for_target_scope(
                self.repository.list_block_reflections(limit=80),
                target_scope=normalized_scope,
                limit=5,
            )
            latest_journals = _journal_rows_for_target_scope(
                self.repository.latest_journals(limit=16),
                target_scope=normalized_scope,
                limit=5,
            )
            today_journals = _journal_rows_for_target_scope(
                self.repository.journals_for_day(trading_day),
                target_scope=normalized_scope,
                limit=4,
            )
            active_policies = _active_policies_for_target_scope(
                self.active_policies(limit=40),
                target_scope=normalized_scope,
            )[:8]
            persona = self._read_memory_file("persona.md", limit=700)
            compact_backlog = _compact_memory_validation_backlog(
                validation_repair_backlog,
                limit=6,
            )
            return {
                "status": "ok",
                "compact": True,
                "trading_day": trading_day,
                "memory_scope": normalized_scope or "all",
                "scope_label": (
                    "KIS 쥬 메모리"
                    if normalized_scope == "kis"
                    else "Binance 쥬 메모리"
                    if normalized_scope == "binance"
                    else "통합 쥬 메모리"
                ),
                "journals": _compact_memory_journals(
                    today_journals,
                    limit=4,
                    message_limit=520,
                ),
                "active_policies": _compact_policy_summaries_for_budget(
                    active_policies,
                    limit=8,
                    summary_limit=180,
                ),
                "policy_scorecards": _compact_policy_summaries_for_budget(
                    policy_scorecards,
                    limit=8,
                    summary_limit=180,
                ),
                "policy_rules": _compact_memory_policy_rules(policy_rules, limit=8),
                "recent_reflections": _compact_memory_reflections(
                    recent_reflections,
                    limit=5,
                ),
                "latest_historical_replay": _compact_memory_historical_replay(
                    self.repository.latest_historical_replay(
                        "weekly",
                        target_scope=normalized_scope,
                    )
                ),
                "latest_journals": _compact_memory_journals(
                    latest_journals,
                    limit=5,
                    message_limit=260,
                ),
                "validation_repair_backlog": compact_backlog,
                "validation_recovery_summary": {
                    "status": compact_backlog.get("status"),
                    "item_count": compact_backlog.get("item_count", 0),
                },
                "context_pack": {
                    "status": "compact",
                    "memory_scope": normalized_scope or "all",
                    "persona": persona,
                },
                **_decision_skill_status_payload(decision_skills),
            }
        context_pack = _public_context_pack(
            self.context_pack(
                target_scope=normalized_scope,
                max_chars=3600,
            )
        )
        policy_scorecards = _policy_rows_for_target_scope(
            self.repository.list_policy_scorecards(limit=80),
            target_scope=normalized_scope,
            memory_type="policy_scorecard",
        )[:12]
        policy_rules = _policy_rows_for_target_scope(
            self.repository.list_policy_rules(limit=80),
            target_scope=normalized_scope,
            memory_type="policy_rule",
        )[:12]
        recent_reflections = _reflection_rows_for_target_scope(
            self.repository.list_block_reflections(limit=80),
            target_scope=normalized_scope,
            limit=8,
        )
        latest_journals = _journal_rows_for_target_scope(
            self.repository.latest_journals(limit=24),
            target_scope=normalized_scope,
            limit=6,
        )
        today_journals = _journal_rows_for_target_scope(
            self.repository.journals_for_day(trading_day),
            target_scope=normalized_scope,
        )
        return {
            "status": "ok",
            "trading_day": trading_day,
            "memory_scope": normalized_scope or "all",
            "scope_label": (
                "KIS 쥬 메모리"
                if normalized_scope == "kis"
                else "Binance 쥬 메모리"
                if normalized_scope == "binance"
                else "통합 쥬 메모리"
            ),
            "journals": today_journals,
            "active_policies": _active_policies_for_target_scope(
                self.active_policies(limit=80),
                target_scope=normalized_scope,
            )[:20],
            "policy_scorecards": policy_scorecards,
            "policy_rules": policy_rules,
            "recent_reflections": recent_reflections,
            "latest_historical_replay": self.repository.latest_historical_replay(
                "weekly",
                target_scope=normalized_scope,
            ),
            "latest_journals": latest_journals,
            "validation_repair_backlog": validation_repair_backlog,
            "validation_recovery_summary": context_pack.get("validation_recovery_summary"),
            "context_pack": context_pack,
            **_decision_skill_status_payload(decision_skills),
        }

    def validation_repair_backlog(
        self,
        *,
        target_scope: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        self.initialize()
        return _build_validation_repair_backlog(
            self.repository.list_memory_events(status="processed", limit=30),
            self.repository.list_policy_scorecards(limit=80),
            target_scope=target_scope,
            limit=limit,
        )

    def validation_repair_ops_summary(
        self,
        *,
        target_scope: str | None = None,
        limit: int = 4,
    ) -> dict[str, Any]:
        normalized_target_scope = _normalize_memory_scope(target_scope)
        item_limit = max(int(limit), 1)
        cache_key = (normalized_target_scope or "core", item_limit)
        cache_ttl_sec = max(_safe_int(self.config.ops_summary_cache_ttl_sec), 0)
        now_monotonic = time_module.monotonic()
        if cache_ttl_sec > 0:
            cached = self._validation_repair_ops_cache.get(cache_key)
            if cached and now_monotonic - cached[0] <= cache_ttl_sec:
                return copy.deepcopy(cached[1])
        backlog = self.validation_repair_backlog(
            target_scope=normalized_target_scope,
            limit=item_limit,
        )
        rule_evaluation = self.evaluate_policy_rules(
            target_scope=normalized_target_scope,
        )
        constraints = _build_block_design_constraints(
            validation_repair_backlog=backlog,
            rule_evaluation=rule_evaluation,
            limit=item_limit,
        )
        recovery = _build_validation_recovery_summary(
            validation_repair_backlog=backlog,
            block_design_constraints=constraints,
            rule_evaluation=rule_evaluation,
            limit=item_limit,
        )
        backlog_items = [
            row
            for row in list(backlog.get("items") or [])[:item_limit]
            if isinstance(row, dict)
        ]
        constraint_items = [
            row
            for row in list(constraints.get("items") or [])[:item_limit]
            if isinstance(row, dict)
        ]
        summary = {
            "version": "validation_repair_ops_summary_v1",
            "scope": normalized_target_scope or "core",
            "target_scope": normalized_target_scope or "core",
            "status": backlog.get("status") or constraints.get("status") or "clear",
            "backlog_count": int(backlog.get("item_count") or 0),
            "constraint_count": int(constraints.get("item_count") or 0),
            "top_backlog": [
                {
                    key: value
                    for key, value in {
                        "policy_id": row.get("policy_id"),
                        "discipline_id": row.get("discipline_id"),
                        "priority": row.get("priority"),
                        "status": row.get("status"),
                        "automation_hook": row.get("automation_hook"),
                        "execution_weight": row.get("execution_weight"),
                        "entry_bias": row.get("entry_bias"),
                        "sizing_policy": row.get("sizing_policy"),
                        "target_stop_review": row.get("target_stop_review"),
                        "risk_budget_multiplier": row.get("risk_budget_multiplier"),
                        "max_budget_multiplier": row.get("max_budget_multiplier"),
                        "min_reward_risk": row.get("min_reward_risk"),
                        "required_checks": list(row.get("required_checks") or [])[:6],
                    }.items()
                    if value not in (None, "", [], {})
                }
                for row in backlog_items
            ],
            "top_constraints": [
                {
                    key: value
                    for key, value in {
                        "policy_id": row.get("policy_id"),
                        "discipline_id": row.get("discipline_id"),
                        "scale_blocker": row.get("scale_blocker"),
                        "entry_bias": row.get("entry_bias"),
                        "sizing_policy": row.get("sizing_policy"),
                        "target_stop_review": row.get("target_stop_review"),
                        "risk_budget_multiplier": row.get("risk_budget_multiplier"),
                        "max_budget_multiplier": row.get("max_budget_multiplier"),
                        "min_reward_risk": row.get("min_reward_risk"),
                        "required_checks": list(row.get("required_checks") or [])[:6],
                    }.items()
                    if value not in (None, "", [], {})
                }
                for row in constraint_items
            ],
            "recovery": {
                "status": recovery.get("status"),
                "item_count": recovery.get("item_count"),
                "items": [
                    {
                        key: value
                        for key, value in {
                            "policy_id": row.get("policy_id"),
                            "discipline_id": row.get("discipline_id"),
                            "current_jue_response": row.get("current_jue_response"),
                        }.items()
                        if value not in (None, "", [], {})
                    }
                    for row in list(recovery.get("items") or [])[:item_limit]
                    if isinstance(row, dict)
                ],
            },
        }
        if cache_ttl_sec > 0:
            self._validation_repair_ops_cache[cache_key] = (
                now_monotonic,
                copy.deepcopy(summary),
            )
        return summary

    def active_policies(
        self,
        *,
        limit: int = 20,
        target_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_target = _normalize_memory_scope(target_scope)
        policies: list[dict[str, Any]] = []
        scorecards = self.repository.list_policy_scorecards(limit=limit)
        if normalized_target:
            target_scorecards = self.repository.list_policy_scorecards_for_scope(
                target_scope=normalized_target,
                limit=limit,
            )
            merged_scorecards: dict[str, dict[str, Any]] = {}
            for row in [*target_scorecards, *scorecards]:
                policy_id = _clean_text(row.get("policy_id"), limit=160)
                if not policy_id or policy_id in merged_scorecards:
                    continue
                merged_scorecards[policy_id] = row
            scorecards = list(merged_scorecards.values())
        for row in scorecards:
            if str(row.get("status") or "").startswith("active"):
                policies.append(
                    {
                        "policy_id": row.get("policy_id"),
                        "action": row.get("action"),
                        "strength": row.get("status"),
                        "status": "active",
                        "reason": row.get("reason"),
                        "confidence": row.get("confidence"),
                        "scorecard": row,
                    }
                )
        policy_changes = self.repository.list_policy_changes(status="active", limit=limit)
        if normalized_target:
            target_policy_changes = self.repository.list_policy_changes_for_scope(
                target_scope=normalized_target,
                status="active",
                limit=limit,
            )
            merged_changes: dict[str, dict[str, Any]] = {}
            for row in [*target_policy_changes, *policy_changes]:
                key = _clean_text(
                    row.get("policy_id") or f"policy_change:{row.get('id') or ''}",
                    limit=180,
                )
                if not key or key in merged_changes:
                    continue
                merged_changes[key] = row
            policy_changes = list(merged_changes.values())
        policies.extend(policy_changes)
        return policies[: max(int(limit), 1)]

    def symbol_memory(self, symbol: str) -> dict[str, Any]:
        self.initialize()
        target = str(symbol or "").strip()
        if not _is_symbol(target):
            return {"status": "invalid_symbol", "symbol": target}
        path = self.root / "symbols" / f"{target}.md"
        insights = self.repository.list_insights(
            memory_type="symbol",
            key=target,
            limit=20,
        )
        return {
            "status": "ok",
            "symbol": target,
            "path": str(path),
            "exists": path.exists(),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
            "insights": insights,
        }

    def record_symbol_analysis_memory(self, analysis: dict[str, Any]) -> dict[str, Any]:
        target = str(analysis.get("symbol") or "").strip()
        if not _is_symbol(target):
            return {"status": "invalid_symbol", "symbol": target}
        symbols_dir = self.root / "symbols"
        symbols_dir.mkdir(parents=True, exist_ok=True)
        path = symbols_dir / f"{target}.md"
        if not path.exists():
            path.write_text(f"# {target}.md\n\n", encoding="utf-8")
        created_at = _clean_text(analysis.get("created_at") or utc_now_iso(), limit=80)
        name = _clean_text(analysis.get("name"), limit=120)
        stance = _clean_text(analysis.get("stance"), limit=80)
        confidence = _safe_float(analysis.get("confidence"))
        summary = _clean_text(analysis.get("summary"), limit=4000)
        section = (
            f"## {created_at} · instant analysis\n"
            f"{name}({target}) · {stance} · confidence {confidence:.2f}\n"
            f"{summary}\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(section)
        return {"status": "ok", "symbol": target, "path": str(path)}

    def block_memory(self, block_id: str) -> dict[str, Any]:
        self.initialize()
        target = _clean_text(block_id, limit=160)
        path = self.root / "blocks" / f"{target}.md"
        insights = self.repository.list_insights(
            memory_type="block",
            key=target,
            limit=20,
        )
        return {
            "status": "ok" if target else "invalid_block_id",
            "block_id": target,
            "path": str(path),
            "exists": path.exists(),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
            "insights": insights,
            "reflection": self.repository.get_block_reflection(target) or {"status": "missing"},
        }

    def policy_scorecards(self, *, limit: int = 30) -> dict[str, Any]:
        return {
            "status": "ok",
            "items": self.repository.list_policy_scorecards(limit=limit),
        }

    def ingest_evidence_scorecards(self, scorecards: list[dict[str, Any]]) -> dict[str, Any]:
        self.initialize()
        ingested: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in scorecards:
            if not isinstance(row, dict):
                skipped.append({"reason": "invalid_scorecard"})
                continue
            policy_id = _clean_text(row.get("policy_id"), limit=160)
            if not policy_id:
                skipped.append({"reason": "invalid_policy_id", "scorecard": row})
                continue
            scope = _clean_text(row.get("scope") or "global", limit=80) or "global"
            confidence = _safe_float(row.get("confidence"))
            expectancy_r = _safe_float(row.get("expectancy_r"))
            sample_count = _safe_int(row.get("sample_count")) or _safe_int(row.get("fresh_count"))
            action = "observe"
            status = "candidate"
            if sample_count >= 5 and confidence >= 0.65 and expectancy_r > 0:
                action = "prefer"
                status = "active_preference"
            elif sample_count >= 5 and confidence >= 0.65 and expectancy_r < 0:
                action = "caution"
                status = "active_caution"
            reason = _clean_text(
                row.get("reason")
                or (
                    f"Evidence scorecard for {scope}: sample_count={sample_count}, "
                    f"confidence={confidence:.2f}, expectancy_r={expectancy_r:+.2f}"
                ),
                limit=1200,
            )
            payload = {
                **row,
                "policy_id": policy_id,
                "scope": scope,
                "action": action,
                "status": status,
                "sample_count": max(sample_count, 0),
                "win_rate": _safe_float(row.get("win_rate")),
                "avg_pnl_pct": _safe_float(row.get("avg_pnl_pct")),
                "expectancy_pct": _safe_float(row.get("expectancy_pct") or expectancy_r),
                "rule_follow_rate": _safe_float(row.get("rule_follow_rate")),
                "confidence": confidence,
                "reason": reason,
            }
            ingested.append(self.repository.upsert_policy_scorecard(payload))
        sync_result = self.sync_policy_rules()
        return {
            "status": "ok",
            "ingested": len(ingested),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "sync": sync_result,
        }

    def ingest_validation_repair_execution(
        self,
        repair_execution: dict[str, Any],
    ) -> dict[str, Any]:
        self.initialize()
        self._clear_validation_repair_ops_cache()
        if not isinstance(repair_execution, dict):
            return {"status": "skipped", "reason": "repair_execution_missing"}
        actions = [
            row
            for row in list(repair_execution.get("actions") or [])
            if isinstance(row, dict)
        ]
        if not actions:
            return {"status": "ok", "processed_count": 0, "policy_scorecards": []}

        saved: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for action in actions:
            venue = _normalize_memory_scope(action.get("venue")) or "core"
            discipline_id = self._policy_key_fragment(
                action.get("discipline_id") or action.get("validation_mode")
            )
            if not discipline_id:
                continue
            status = _clean_text(action.get("status") or "queued", limit=60).lower()
            mode = _clean_text(action.get("validation_mode"), limit=100)
            artifact = _clean_text(action.get("artifact"), limit=160)
            repair_action_id = _clean_text(
                action.get("repair_action_id"),
                limit=140,
            )
            automation_hook = _clean_text(
                action.get("automation_hook"),
                limit=120,
            )
            execution_weight = _clean_text(
                action.get("execution_weight"),
                limit=80,
            )
            policy_id = f"validation_repair.{venue}.{discipline_id}"
            event_key = f"validation_repair_execution:{venue}:{discipline_id}"
            payload = {
                "venue": venue,
                "memory_scope": venue,
                "transferability": "translated",
                "discipline_id": discipline_id,
                "repair_action_id": repair_action_id,
                "status": status,
                "automation_hook": automation_hook,
                "execution_weight": execution_weight,
                "validation_mode": mode,
                "artifact": artifact,
                "scale_up_blocked": bool(action.get("scale_up_blocked")),
                "live_shadow_required": bool(action.get("live_shadow_required")),
                "reason": _clean_text(action.get("reason"), limit=260),
                "runner_status": _clean_text(action.get("runner_status"), limit=80),
                "repair_execution_status": _clean_text(
                    repair_execution.get("status"),
                    limit=60,
                ),
                "status_counts": (
                    repair_execution.get("status_counts")
                    if isinstance(repair_execution.get("status_counts"), dict)
                    else {}
                ),
            }
            event = self.repository.save_memory_event(
                event_key=event_key,
                event_type="validation_repair_execution",
                block_id="__system__",
                status="pending",
                payload=payload,
            )
            self.repository.mark_memory_event_processed(event_key)
            events.append(event)

            executed = status in {"executed", "observed_external_runner"}
            errored = status in {"error", "failed", "blocked"}
            queued = status.startswith("queued") or status in {"pending", "running"}
            confidence = 0.62 if errored else 0.50 if queued else 0.42
            scorecard_status = "active_caution" if (errored or queued) else "candidate"
            scorecard_action = "caution" if (errored or queued) else "observe"
            reason = (
                f"{venue} {discipline_id} 복구 실행 상태: {status}. "
                f"{mode or artifact or 'validation repair'}"
            )
            if executed:
                reason += "; 복구 artifact는 확인됐지만 scale-up은 다음 19검증 pass로만 해제한다."
            elif errored:
                reason += "; 복구 오류가 남아 해당 lane은 소액/대기 중심으로 유지한다."
            else:
                reason += "; 복구 대기 중이라 해당 lane의 증액은 보류한다."
            saved.append(
                self.repository.upsert_policy_scorecard(
                    {
                        "policy_id": policy_id,
                        "action": scorecard_action,
                        "status": scorecard_status,
                        "sample_count": 1,
                        "win_rate": 0.0,
                        "avg_pnl_pct": 0.0,
                        "expectancy_pct": 0.0,
                        "rule_follow_rate": 1.0,
                        "confidence": confidence,
                        "reason": reason,
                        "source": "validation_repair_execution",
                        "venue": venue,
                        "memory_scope": venue,
                        "transferability": "translated",
                        "discipline_id": discipline_id,
                        "repair_action_id": repair_action_id,
                        "repair_status": status,
                        "automation_hook": automation_hook,
                        "execution_weight": execution_weight,
                        "validation_mode": mode,
                        "artifact": artifact,
                        "scale_up_blocked": bool(action.get("scale_up_blocked")),
                        "live_shadow_required": bool(action.get("live_shadow_required")),
                        "scope_evidence": [payload],
                    }
                )
            )
            self.repository.save_insight(
                memory_type="policy_signal",
                key=policy_id,
                status="active",
                confidence=confidence,
                summary_md=reason,
                evidence=[payload],
                source_run_id=None,
            )

        sync_result = self.sync_policy_rules()
        return {
            "status": "ok",
            "processed_count": len(saved),
            "events": events,
            "policy_scorecards": saved,
            "policy_rule_sync": sync_result,
        }

    def policy_rules(self, *, limit: int = 30, active_only: bool = False) -> dict[str, Any]:
        self.sync_policy_rules()
        return {
            "status": "ok",
            "active_only": bool(active_only),
            "items": self.repository.list_policy_rules(
                active_only=active_only,
                limit=limit,
            ),
        }

    def latest_period_review(self, period_type: str) -> dict[str, Any]:
        self.initialize()
        normalized = "monthly" if str(period_type) == "monthly" else "weekly"
        return self.repository.latest_period_review(normalized)

    def period_reviews(self, *, period_type: str = "", limit: int = 12) -> dict[str, Any]:
        self.initialize()
        normalized = str(period_type) if str(period_type) in {"weekly", "monthly"} else ""
        return {
            "status": "ok",
            "period_type": normalized,
            "items": self.repository.list_period_reviews(
                period_type=normalized,
                limit=limit,
            ),
        }

    def latest_historical_replay(self, period_type: str) -> dict[str, Any]:
        self.initialize()
        normalized = "monthly" if str(period_type) == "monthly" else "weekly"
        return self.repository.latest_historical_replay(normalized)

    def historical_replays(self, *, period_type: str = "", limit: int = 12) -> dict[str, Any]:
        self.initialize()
        normalized = str(period_type) if str(period_type) in {"weekly", "monthly"} else ""
        return {
            "status": "ok",
            "period_type": normalized,
            "items": self.repository.list_historical_replays(
                period_type=normalized,
                limit=limit,
            ),
        }

    def policy_revisions(self, *, status: str = "", limit: int = 30) -> dict[str, Any]:
        self.initialize()
        return {
            "status": "ok",
            "filter_status": status,
            "items": self.repository.list_policy_revisions(status=status, limit=limit),
        }

    def activate_policy_revision(self, revision_id: str) -> dict[str, Any]:
        self.initialize()
        target = str(revision_id or "").strip()
        rows = self.repository.list_policy_revisions(revision_id=target, limit=1)
        if not rows:
            return {"status": "missing", "revision_id": target}
        saved = self.repository.upsert_policy_revision(
            {
                **rows[0],
                "status": "active_caution",
                "activated_at": utc_now_iso(),
            }
        )
        sync = self._sync_revisions_to_policy_rules()
        return {
            "status": "ok",
            "revision_id": target,
            "activated": True,
            "revision": saved,
            "sync": sync,
        }

    def reject_policy_revision(self, revision_id: str) -> dict[str, Any]:
        self.initialize()
        target = str(revision_id or "").strip()
        rows = self.repository.list_policy_revisions(revision_id=target, limit=1)
        if not rows:
            return {"status": "missing", "revision_id": target}
        saved = self.repository.upsert_policy_revision({**rows[0], "status": "rejected"})
        return {
            "status": "ok",
            "revision_id": target,
            "rejected": True,
            "revision": saved,
        }

    def block_reflection_statuses(self, block_ids: list[str]) -> dict[str, dict[str, Any]]:
        return self.repository.reflection_statuses(block_ids)

    def build_period_metrics(
        self,
        *,
        period_type: str,
        period_key: str,
        start_date: str,
        end_date: str,
        target_scope: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_memory_scope(target_scope)
        reflections = self.repository.list_block_reflections(limit=5000)
        rows = [
            row
            for row in reflections
            if isinstance(row, dict)
            and self._reflection_outcome_date(row) is not None
            and start_date <= str(self._reflection_outcome_date(row)) <= end_date
        ]
        if normalized_scope:
            rows = _reflection_rows_for_target_scope(
                rows,
                target_scope=normalized_scope,
                limit=5000,
            )
        if not rows:
            return {
                "period_type": period_type,
                "period_key": period_key,
                "memory_scope": normalized_scope or "all",
                "start_date": start_date,
                "end_date": end_date,
                "closed_blocks": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "by_horizon": {},
                "by_source": {},
                "by_venue": {},
                "by_pattern": {},
                "by_lane": {},
                "policy_impacts": {},
            }
        pnl_values = [_safe_float(row.get("pnl_pct")) for row in rows]
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        return {
            "period_type": period_type,
            "period_key": period_key,
            "memory_scope": normalized_scope or "all",
            "start_date": start_date,
            "end_date": end_date,
            "closed_blocks": len(rows),
            "win_rate": len(wins) / len(rows),
            "avg_pnl_pct": sum(pnl_values) / len(rows),
            "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
            "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
            "expectancy_pct": sum(pnl_values) / len(rows),
            "by_horizon": self._group_reflection_metrics(rows, key="horizon"),
            "by_source": self._group_reflection_metrics(rows, key="created_by"),
            "by_venue": self._group_reflection_metrics(rows, key="memory_scope"),
            "by_pattern": self._group_reflection_metrics(rows, key="pattern_key"),
            "by_lane": self._group_reflection_lanes(rows),
            "policy_impacts": self._group_reflection_metrics(rows, key="policy_id"),
            "exit_reasons": self._count_reflection_values(rows, key="exit_reason"),
        }

    @staticmethod
    def _reflection_outcome_date(row: dict[str, Any]) -> str | None:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        outcome_date = str(metrics.get("outcome_date") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", outcome_date):
            return outcome_date
        closed = _parse_datetime(metrics.get("closed_at"))
        if closed:
            return closed.astimezone(KST).date().isoformat()
        return None

    def period_window(
        self,
        *,
        period_type: str,
        now: datetime | None = None,
    ) -> dict[str, str]:
        local = (now or datetime.now(KST)).astimezone(KST)
        normalized = "monthly" if str(period_type) == "monthly" else "weekly"
        if normalized == "monthly":
            first = local.date().replace(day=1)
            next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            last = next_month - timedelta(days=1)
            return {
                "period_type": "monthly",
                "period_key": first.strftime("%Y-%m"),
                "start_date": first.isoformat(),
                "end_date": last.isoformat(),
            }
        iso = local.date().isocalendar()
        monday = local.date() - timedelta(days=local.weekday())
        friday = monday + timedelta(days=4)
        return {
            "period_type": "weekly",
            "period_key": f"{iso.year}-W{iso.week:02d}",
            "start_date": monday.isoformat(),
            "end_date": friday.isoformat(),
        }

    async def run_period_review(
        self,
        *,
        period_type: str,
        now: datetime | None = None,
        force: bool = False,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        normalized = "monthly" if str(period_type) == "monthly" else "weekly"
        window = self.period_window(period_type=normalized, now=now)
        context_payload = context if isinstance(context, dict) else {}
        context_scope = _normalize_memory_scope(
            context_payload.get("memory_scope") or context_payload.get("target_scope")
        )
        existing = self.repository.get_period_review(
            window["period_key"],
            normalized,
            target_scope=context_scope or None,
        )
        if (
            existing
            and not force
            and str(existing.get("status") or "") not in {"llm_unavailable", "error"}
        ):
            return {
                "status": "skipped",
                "reason": "period_review_already_exists",
                "period_type": normalized,
                "period_key": window["period_key"],
                "review": existing,
            }

        metrics = self.build_period_metrics(
            **window,
            target_scope=context_scope or None,
        )
        prompt = self._build_period_review_prompt(
            period_type=normalized,
            window=window,
            metrics=metrics,
            context=context_payload,
        )
        output, mode, error = await self._complete_json(prompt)
        if not isinstance(output, dict):
            mode = "error"
            status = "error"
            run_id = self.repository.save_run(
                kind="period_review",
                slot=normalized,
                status=status,
                mode=mode,
                model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                error_message=error,
                input_payload=prompt,
                output_payload={},
            )
            review = self.repository.upsert_period_review(
                {
                    **window,
                    "status": status,
                    "mode": mode,
                    "metrics": metrics,
                    "review_md": "",
                    "policy_revision_ids": [],
                }
            )
            return {
                "status": status,
                "period_type": normalized,
                "period_key": window["period_key"],
                "run_id": run_id,
                "review": review,
                "revision_count": 0,
                "revisions": [],
                "policy_rule_sync": {"status": "skipped", "reason": "llm_error"},
            }
        else:
            output, validation_error = self._validate_period_review_output(output)
            if validation_error:
                error = validation_error
                mode = "error"
                status = "error"
                run_id = self.repository.save_run(
                    kind="period_review",
                    slot=normalized,
                    status=status,
                    mode=mode,
                    model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                    error_message=error,
                    input_payload=prompt,
                    output_payload=output if isinstance(output, dict) else {},
                )
                review = self.repository.upsert_period_review(
                    {
                        **window,
                        "status": status,
                        "mode": mode,
                        "metrics": metrics,
                        "review_md": "",
                        "policy_revision_ids": [],
                    }
                )
                return {
                    "status": status,
                    "period_type": normalized,
                    "period_key": window["period_key"],
                    "run_id": run_id,
                    "review": review,
                    "revision_count": 0,
                    "revisions": [],
                    "policy_rule_sync": {"status": "skipped", "reason": "validation_error"},
                }

        status = "ok"
        run_id = self.repository.save_run(
            kind="period_review",
            slot=normalized,
            status=status,
            mode=mode,
            model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            error_message=error,
            input_payload=prompt,
            output_payload=output,
        )
        revisions = self._save_policy_revisions(
            output.get("policy_revisions")
            if isinstance(output.get("policy_revisions"), list)
            else [],
            period_type=normalized,
            period_key=window["period_key"],
            metrics=metrics,
        )
        review = self.repository.upsert_period_review(
            {
                **window,
                "status": status,
                "mode": mode,
                "metrics": metrics,
                "review_md": str(output.get("review_md") or ""),
                "policy_revision_ids": [str(row.get("revision_id") or "") for row in revisions],
            }
        )
        self._apply_memory_updates(output, source_run_id=run_id)
        rule_result = self._sync_revisions_to_policy_rules()
        return {
            "status": status,
            "period_type": normalized,
            "period_key": window["period_key"],
            "run_id": run_id,
            "review": review,
            "revision_count": len(revisions),
            "revisions": revisions,
            "policy_rule_sync": rule_result,
        }

    def _build_period_review_prompt(
        self,
        *,
        period_type: str,
        window: dict[str, str],
        metrics: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "system": (
                "You are Jue, the HERMES investment partner. Analyze reflections "
                "and policy revisions in English, draft conclusions in English, "
                "then translate operator-visible review text into Korean."
            ),
            "task": "Return JSON only. Create weekly/monthly reflection and policy revisions.",
            "language_policy": jue_language_policy(),
            "period_type": period_type,
            "window": window,
            "metrics": metrics,
            "context": _compact_ritual_context(context, limit=6000),
            "allowed_revision_status": [
                "candidate",
                "active_caution",
                "active_preference",
                "retired",
                "rejected",
            ],
            "allowed_actions": ["create", "strengthen", "weaken", "retire", "keep"],
            "allowed_scopes": [
                "short",
                "mid",
                "long",
                "core_etf",
                "cash",
                "user_position",
                "discovery",
                "general",
            ],
            "hard_filter_policy": "Do not create hard filters. Safety gates remain separate.",
            "output_schema": {
                "review_title": "string",
                "review_md": "markdown string",
                "observations": ["string"],
                "policy_revisions": [
                    {
                        "policy_id": "string",
                        "action": "create|strengthen|weaken|retire|keep",
                        "scope": (
                            "short|mid|long|core_etf|cash|user_position|"
                            "discovery|general"
                        ),
                        "condition": {},
                        "effect": {},
                        "reason_md": "string",
                        "confidence": 0.0,
                    }
                ],
                "memory_updates": {"notes": [], "symbols": [], "blocks": []},
            },
        }

    @staticmethod
    def _validate_period_review_output(output: dict[str, Any]) -> tuple[dict[str, Any], str]:
        review_md = _clean_text(output.get("review_md"), limit=12000)
        if not review_md:
            return output, "period_review_missing_review_md"
        revisions = output.get("policy_revisions")
        if revisions is not None and not isinstance(revisions, list):
            return output, "period_review_invalid_policy_revisions"
        memory_updates = output.get("memory_updates")
        if memory_updates is not None and not isinstance(memory_updates, dict):
            return output, "period_review_invalid_memory_updates"
        cleaned = dict(output)
        cleaned["review_md"] = review_md
        cleaned["observations"] = [
            _clean_text(row, limit=600)
            for row in list(output.get("observations") or [])[:12]
            if _clean_text(row, limit=600)
        ]
        cleaned["policy_revisions"] = [
            row for row in list(revisions or []) if isinstance(row, dict)
        ][:12]
        cleaned["memory_updates"] = memory_updates if isinstance(memory_updates, dict) else {}
        return cleaned, ""

    def _deterministic_period_review(
        self,
        *,
        period_type: str,
        window: dict[str, str],
        metrics: dict[str, Any],
        error_message: str = "",
    ) -> dict[str, Any]:
        closed = _safe_int(metrics.get("closed_blocks"))
        avg = _safe_float(metrics.get("avg_pnl_pct"))
        win_rate = _safe_float(metrics.get("win_rate")) * 100.0
        review_md = (
            f"## {window['period_key']} {period_type} review\n\n"
            f"- 닫힌 블록: {closed}개\n"
            f"- 승률: {win_rate:.1f}%\n"
            f"- 평균 손익: {avg:+.2f}%\n"
            f"- LLM 오류: {error_message or '-'}\n"
            "- 자동 정책 승격은 보류한다.\n"
        )
        return {
            "review_title": f"{window['period_key']} {period_type} review",
            "review_md": review_md,
            "observations": [],
            "policy_revisions": [],
            "memory_updates": {"notes": []},
        }

    async def run_historical_replay(
        self,
        *,
        period_type: str,
        now: datetime | None = None,
        force: bool = False,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        normalized = "monthly" if str(period_type) == "monthly" else "weekly"
        window = self.period_window(period_type=normalized, now=now)
        context_payload = context if isinstance(context, dict) else {}
        context_scope = _normalize_memory_scope(
            context_payload.get("memory_scope") or context_payload.get("target_scope")
        )
        existing = self.repository.get_historical_replay(
            window["period_key"],
            normalized,
            target_scope=context_scope or None,
        )
        if (
            existing
            and not force
            and str(existing.get("status") or "") not in {"llm_unavailable", "error"}
        ):
            return {
                "status": "skipped",
                "reason": "historical_replay_already_exists",
                "period_type": normalized,
                "period_key": window["period_key"],
                "replay": existing,
            }

        metrics = self.build_period_metrics(
            **window,
            target_scope=context_scope or None,
        )
        cases = self.build_historical_replay_cases(
            window=window,
            context=context_payload,
            limit=8,
        )
        if not cases:
            output = self._deterministic_historical_replay(
                period_type=normalized,
                window=window,
                metrics=metrics,
                cases=cases,
                error_message="no_replay_cases",
            )
            replay = self.repository.upsert_historical_replay(
                {
                    **window,
                    "status": "no_cases",
                    "mode": "deterministic",
                    "case_count": 0,
                    "metrics": {**metrics, "case_count": 0},
                    "replay_md": str(output.get("replay_md") or ""),
                    "case_reviews": [],
                    "policy_revision_ids": [],
                }
            )
            return {
                "status": "no_cases",
                "period_type": normalized,
                "period_key": window["period_key"],
                "case_count": 0,
                "replay": replay,
                "revision_count": 0,
                "revisions": [],
                "policy_rule_sync": {"status": "skipped"},
            }
        prompt = self._build_historical_replay_prompt(
            period_type=normalized,
            window=window,
            metrics=metrics,
            cases=cases,
            context=context_payload,
        )
        output, mode, error = await self._complete_json(prompt)
        if not isinstance(output, dict):
            mode = "error"
            status = "error"
            run_id = self.repository.save_run(
                kind="historical_replay",
                slot=normalized,
                status=status,
                mode=mode,
                model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                error_message=error,
                input_payload=prompt,
                output_payload={},
            )
            replay = self.repository.upsert_historical_replay(
                {
                    **window,
                    "status": status,
                    "mode": mode,
                    "case_count": len(cases),
                    "metrics": {**metrics, "case_count": len(cases)},
                    "replay_md": "",
                    "case_reviews": [],
                    "policy_revision_ids": [],
                }
            )
            return {
                "status": status,
                "period_type": normalized,
                "period_key": window["period_key"],
                "case_count": len(cases),
                "run_id": run_id,
                "replay": replay,
                "revision_count": 0,
                "revisions": [],
                "policy_rule_sync": {"status": "skipped", "reason": "llm_error"},
            }
        else:
            output, validation_error = self._validate_historical_replay_output(output)
            if validation_error:
                error = validation_error
                mode = "error"
                status = "error"
                run_id = self.repository.save_run(
                    kind="historical_replay",
                    slot=normalized,
                    status=status,
                    mode=mode,
                    model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                    error_message=error,
                    input_payload=prompt,
                    output_payload=output if isinstance(output, dict) else {},
                )
                replay = self.repository.upsert_historical_replay(
                    {
                        **window,
                        "status": status,
                        "mode": mode,
                        "case_count": len(cases),
                        "metrics": {**metrics, "case_count": len(cases)},
                        "replay_md": "",
                        "case_reviews": [],
                        "policy_revision_ids": [],
                    }
                )
                return {
                    "status": status,
                    "period_type": normalized,
                    "period_key": window["period_key"],
                    "case_count": len(cases),
                    "run_id": run_id,
                    "replay": replay,
                    "revision_count": 0,
                    "revisions": [],
                    "policy_rule_sync": {"status": "skipped", "reason": "validation_error"},
                }

        status = "ok"
        run_id = self.repository.save_run(
            kind="historical_replay",
            slot=normalized,
            status=status,
            mode=mode,
            model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            error_message=error,
            input_payload=prompt,
            output_payload=output,
        )
        revision_period_type = f"{normalized}_replay"
        revision_period_key = f"{window['period_key']}:{revision_period_type}"
        revisions = self._save_policy_revisions(
            output.get("policy_revisions")
            if isinstance(output.get("policy_revisions"), list)
            else [],
            period_type=revision_period_type,
            period_key=revision_period_key,
            metrics={**metrics, "closed_blocks": max(len(cases), _safe_int(metrics.get("closed_blocks")))},
        )
        replay = self.repository.upsert_historical_replay(
            {
                **window,
                "memory_scope": context_scope or _policy_revision_memory_scope({"metrics": metrics}),
                "status": status,
                "mode": mode,
                "case_count": len(cases),
                "metrics": {
                    **metrics,
                    "case_count": len(cases),
                    "source": "as_of_decision_replay",
                },
                "replay_md": str(output.get("replay_md") or ""),
                "case_reviews": output.get("case_reviews") or [],
                "policy_revision_ids": [
                    str(row.get("revision_id") or "") for row in revisions
                ],
            }
        )
        self.repository.save_insight(
            memory_type="historical_replay",
            key=f"{normalized}:{window['period_key']}",
            status="active",
            confidence=0.7 if status == "ok" else 0.2,
            summary_md=_truncate(output.get("replay_md"), 3000),
            evidence=[
                {
                    "memory_scope": context_scope
                    or _policy_revision_memory_scope({"metrics": metrics}),
                    "transferability": "direct",
                    "source": "historical_replay",
                    "period_key": window["period_key"],
                    "case_count": len(cases),
                }
            ],
            source_run_id=run_id,
        )
        self._apply_memory_updates(output, source_run_id=run_id)
        rule_result = self._sync_revisions_to_policy_rules()
        return {
            "status": status,
            "period_type": normalized,
            "period_key": window["period_key"],
            "run_id": run_id,
            "case_count": len(cases),
            "replay": replay,
            "revision_count": len(revisions),
            "revisions": revisions,
            "policy_rule_sync": rule_result,
        }

    def build_historical_replay_cases(
        self,
        *,
        window: dict[str, str],
        context: dict[str, Any] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        context_payload = context if isinstance(context, dict) else {}
        target_scope = _normalize_memory_scope(
            context_payload.get("memory_scope") or context_payload.get("target_scope")
        )
        supplied = (
            context_payload.get("historical_replay", {}).get("cases")
            if isinstance(context_payload.get("historical_replay"), dict)
            else None
        )
        if isinstance(supplied, list):
            supplied_cases = [
                self._normalize_replay_case(row, window=window)
                for row in supplied[: max(int(limit), 1)]
                if isinstance(row, dict)
            ]
            if not target_scope:
                return supplied_cases
            return [
                row
                for row in supplied_cases
                if _scope_matches_target(
                    target_scope=target_scope,
                    item_scope=_infer_memory_scope(
                        memory_type="block_reflection",
                        key=row.get("case_id") or row.get("symbol"),
                    ),
                    transferability="direct",
                    memory_type="block_reflection",
                )
            ][: max(int(limit), 1)]
        reflections = [
            row
            for row in self.repository.list_block_reflections(limit=5000)
            if self._reflection_outcome_date(row) is not None
            and window["start_date"] <= str(self._reflection_outcome_date(row)) <= window["end_date"]
        ]
        if target_scope:
            reflections = _reflection_rows_for_target_scope(
                reflections,
                target_scope=target_scope,
                limit=5000,
            )
        reflections.sort(
            key=lambda row: (
                abs(_safe_float(row.get("pnl_pct"))),
                str(row.get("updated_at") or ""),
            ),
            reverse=True,
        )
        return [
            self._case_from_reflection(row, window=window)
            for row in reflections[: max(int(limit), 1)]
        ]

    def _case_from_reflection(
        self,
        row: dict[str, Any],
        *,
        window: dict[str, str],
    ) -> dict[str, Any]:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        as_of = (
            _clean_text(metrics.get("as_of"), limit=80)
            or _clean_text(metrics.get("entry_at"), limit=80)
            or _clean_text(metrics.get("created_at"), limit=80)
            or f"{self._reflection_outcome_date(row) or window['start_date']}T09:00:00+09:00"
        )
        case_id = _clean_text(row.get("block_id"), limit=180)
        return {
            "case_id": case_id,
            "as_of": as_of,
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or ""),
            "decision_context_as_of": {
                "block_id": case_id,
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "horizon": metrics.get("horizon"),
                "created_by": metrics.get("created_by"),
                "entry_thesis": metrics.get("entry_thesis") or row.get("lesson_md"),
                "entry_price": metrics.get("entry_price"),
                "target_price": metrics.get("target_price"),
                "stop_price": metrics.get("stop_price"),
                "research_snapshot": (
                    metrics.get("research_snapshot")
                    if isinstance(metrics.get("research_snapshot"), dict)
                    else {}
                ),
                "strategy_snapshot": (
                    metrics.get("strategy_snapshot")
                    if isinstance(metrics.get("strategy_snapshot"), dict)
                    else {}
                ),
            },
            "outcome_after_as_of": {
                "status": row.get("status"),
                "exit_reason": row.get("exit_reason"),
                "pnl_pct": row.get("pnl_pct"),
                "pnl_krw": row.get("pnl_krw"),
                "mfe_pct": row.get("mfe_pct"),
                "mae_pct": row.get("mae_pct"),
                "hold_seconds": row.get("hold_seconds"),
                "rule_followed": row.get("rule_followed"),
                "lesson_md": row.get("lesson_md"),
                "outcome_summary": metrics.get("outcome_summary"),
                "outcome_date": self._reflection_outcome_date(row),
            },
        }

    @staticmethod
    def _normalize_replay_case(
        row: dict[str, Any],
        *,
        window: dict[str, str],
    ) -> dict[str, Any]:
        case_id = _clean_text(row.get("case_id") or row.get("block_id"), limit=180)
        decision_context = (
            row.get("decision_context_as_of")
            if isinstance(row.get("decision_context_as_of"), dict)
            else {}
        )
        outcome = (
            row.get("outcome_after_as_of")
            if isinstance(row.get("outcome_after_as_of"), dict)
            else {}
        )
        return {
            "case_id": case_id,
            "as_of": _clean_text(row.get("as_of") or window["start_date"], limit=80),
            "symbol": _clean_text(row.get("symbol") or decision_context.get("symbol"), limit=40),
            "name": _clean_text(row.get("name") or decision_context.get("name"), limit=120),
            "decision_context_as_of": decision_context,
            "outcome_after_as_of": outcome,
        }

    def _build_historical_replay_prompt(
        self,
        *,
        period_type: str,
        window: dict[str, str],
        metrics: dict[str, Any],
        cases: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        target_scope = _normalize_memory_scope(
            context.get("memory_scope")
            or context.get("target_scope")
            or metrics.get("memory_scope")
        )
        return {
            "system": (
                "You are Jue, the HERMES investment partner. Replay historical "
                "as_of decisions in English, learn from outcomes, draft memory "
                "revisions in English, then translate operator-visible text into Korean."
            ),
            "task": "Return JSON only. Replay historical decisions and create memory revisions.",
            "language_policy": jue_language_policy(),
            "slot": "historical_replay",
            "period_type": period_type,
            "window": window,
            "metrics": metrics,
            "future_data_guard": (
                "decision_context_as_of만 과거 판단 재생에 사용할 수 있다. "
                "outcome_after_as_of는 replay_decision을 작성한 뒤 반성과 정책 학습에만 사용한다."
            ),
            "cases": cases,
            "current_memory": _public_context_pack(
                self.context_pack(
                    target_scope=target_scope or None,
                    max_chars=3600,
                )
            ),
            "context": _compact_ritual_context(context, limit=3600),
            "hard_filter_policy": "Do not create hard filters. Safety gates remain separate.",
            "output_schema": {
                "replay_title": "string",
                "replay_md": "markdown string",
                "case_reviews": [
                    {
                        "case_id": "string",
                        "as_of": "iso timestamp",
                        "replay_decision": "what Jue should have decided then",
                        "outcome_review": "what actually happened later",
                        "lesson": "memory-grade lesson",
                    }
                ],
                "policy_revisions": [
                    {
                        "policy_id": "string",
                        "action": "create|strengthen|weaken|retire|keep",
                        "scope": "short|mid|long|core_etf|cash|user_position|discovery|general",
                        "condition": {},
                        "effect": {},
                        "reason_md": "string",
                        "confidence": 0.0,
                    }
                ],
                "memory_updates": {"notes": [], "symbols": [], "blocks": []},
            },
        }

    @staticmethod
    def _validate_historical_replay_output(output: dict[str, Any]) -> tuple[dict[str, Any], str]:
        replay_md = _clean_text(output.get("replay_md"), limit=12000)
        if not replay_md:
            return output, "historical_replay_missing_replay_md"
        case_reviews = output.get("case_reviews")
        if case_reviews is not None and not isinstance(case_reviews, list):
            return output, "historical_replay_invalid_case_reviews"
        revisions = output.get("policy_revisions")
        if revisions is not None and not isinstance(revisions, list):
            return output, "historical_replay_invalid_policy_revisions"
        memory_updates = output.get("memory_updates")
        if memory_updates is not None and not isinstance(memory_updates, dict):
            return output, "historical_replay_invalid_memory_updates"
        cleaned = dict(output)
        cleaned["replay_md"] = replay_md
        cleaned["case_reviews"] = [
            row for row in list(case_reviews or []) if isinstance(row, dict)
        ][:12]
        cleaned["policy_revisions"] = [
            row for row in list(revisions or []) if isinstance(row, dict)
        ][:12]
        cleaned["memory_updates"] = memory_updates if isinstance(memory_updates, dict) else {}
        return cleaned, ""

    @staticmethod
    def _deterministic_historical_replay(
        *,
        period_type: str,
        window: dict[str, str],
        metrics: dict[str, Any],
        cases: list[dict[str, Any]],
        error_message: str = "",
    ) -> dict[str, Any]:
        avg = _safe_float(metrics.get("avg_pnl_pct"))
        review_md = (
            f"## {window['period_key']} {period_type} decision replay\n\n"
            f"- 리플레이 케이스: {len(cases)}개\n"
            f"- 평균 손익: {avg:+.2f}%\n"
            f"- LLM 오류: {error_message or '-'}\n"
            "- 자동 정책 개정은 보류한다.\n"
        )
        return {
            "replay_title": f"{window['period_key']} decision replay",
            "replay_md": review_md,
            "case_reviews": [],
            "policy_revisions": [],
            "memory_updates": {"notes": []},
        }

    def _save_policy_revisions(
        self,
        revisions: list[Any],
        *,
        period_type: str,
        period_key: str,
        metrics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        for index, raw in enumerate(revisions, start=1):
            if not isinstance(raw, dict):
                continue
            row = self._normalize_policy_revision(
                raw,
                period_type=period_type,
                period_key=period_key,
                index=index,
                metrics=metrics,
            )
            saved.append(self.repository.upsert_policy_revision(row))
        return saved

    def _normalize_policy_revision(
        self,
        raw: dict[str, Any],
        *,
        period_type: str,
        period_key: str,
        index: int,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        base_policy_id = self._normalize_policy_id(raw.get("policy_id")) or f"period_revision_{index}"
        memory_scope = (
            _normalize_memory_scope(
                raw.get("memory_scope") or raw.get("venue") or raw.get("market")
            )
            or _memory_scope_from_evidence(raw.get("evidence"))
            or _memory_scope_from_evidence(metrics)
            or "core"
        )
        transferability = _normalize_transferability(raw.get("transferability")) or "direct"
        policy_id = _scoped_policy_revision_id(
            memory_scope=memory_scope,
            policy_id=base_policy_id,
        )
        action = str(raw.get("action") or "keep")
        if action not in {"create", "strengthen", "weaken", "retire", "keep"}:
            action = "keep"
        scope = str(raw.get("scope") or "general")
        if scope not in {"short", "mid", "long", "core_etf", "cash", "user_position", "discovery", "general"}:
            scope = "general"
        confidence = min(max(_safe_float(raw.get("confidence")), 0.0), 0.95)
        condition = raw.get("condition") if isinstance(raw.get("condition"), dict) else {}
        raw_effect = raw.get("effect") if isinstance(raw.get("effect"), dict) else {}
        reason_md = _clean_text(raw.get("reason_md") or raw.get("reason"), limit=2400)
        unsafe = self._is_unsafe_policy_revision(
            policy_id=policy_id,
            action=action,
            condition=condition,
            effect=raw_effect,
            reason_md=reason_md,
        )
        status = self._revision_status(
            action=action,
            confidence=confidence,
            effect=raw_effect,
            metrics=metrics,
            requested_status=str(raw.get("status") or ""),
            unsafe=unsafe,
        )
        now = utc_now_iso()
        revision_id_prefix = f"{memory_scope}:" if memory_scope in {"kis", "binance"} else ""
        revision_id = f"{revision_id_prefix}{period_key}:{policy_id}:{index}"
        effect = {
            **raw_effect,
            "hard_filter": False,
            "safety_gate_override": False,
        }
        evidence = {
            "memory_scope": memory_scope,
            "transferability": transferability,
            "period_key": period_key,
            "period_type": period_type,
            "metrics": metrics,
            "raw_confidence": confidence,
        }
        if unsafe:
            evidence["validation_error"] = "unsafe_or_hard_filter_like_revision"
            effect["ban"] = False
        return {
            "revision_id": revision_id,
            "memory_scope": memory_scope,
            "transferability": transferability,
            "period_key": period_key,
            "period_type": period_type,
            "policy_id": policy_id,
            "action": action,
            "status": status,
            "scope": scope,
            "condition": condition,
            "effect": effect,
            "evidence": evidence,
            "reason_md": reason_md,
            "confidence": confidence,
            "activated_at": now if status in {"active_caution", "active_preference"} else "",
            "retired_at": now if status == "retired" else "",
        }

    @staticmethod
    def _normalize_policy_id(value: Any) -> str:
        text = _clean_text(value, limit=120).lower()
        text = re.sub(r"[^a-z0-9_]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text[:120]

    @staticmethod
    def _is_unsafe_policy_revision(
        *,
        policy_id: str,
        action: str,
        condition: dict[str, Any],
        effect: dict[str, Any],
        reason_md: str,
    ) -> bool:
        if action == "retire":
            return False
        blob = _json_dumps(
            {
                "policy_id": policy_id,
                "condition": condition,
                "effect": effect,
                "reason_md": reason_md,
            }
        ).lower()
        hard_markers = (
            "hard_filter",
            "safety_gate_override",
            "ban",
            "blacklist",
            "whitelist",
            "block_all",
            "reject_all",
            "never_trade",
            "must_not_trade",
            "always_sell",
            "always_buy",
            "forbid",
            "prohibit",
            "deny",
            "절대",
            "무조건",
            "항상",
            "금지",
            "차단",
        )
        if any(marker in blob for marker in hard_markers):
            return True
        return bool(effect.get("hard_filter")) or bool(effect.get("safety_gate_override"))

    @staticmethod
    def _revision_status(
        *,
        action: str,
        confidence: float,
        effect: dict[str, Any],
        metrics: dict[str, Any],
        requested_status: str = "",
        unsafe: bool = False,
    ) -> str:
        if unsafe or bool(effect.get("hard_filter")) or bool(effect.get("safety_gate_override")):
            return "rejected"
        if action == "retire":
            return "retired"
        sample_count = _safe_int(metrics.get("closed_blocks"))
        if sample_count >= 3 and confidence >= 0.65 and action in {"create", "strengthen", "weaken"}:
            if requested_status == "active_preference" and sample_count >= 8 and confidence >= 0.8:
                return "active_preference"
            return "active_caution"
        return "candidate"

    def _sync_revisions_to_policy_rules(self) -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        revisions, revision_skips = self._canonical_active_policy_revisions(
            self.repository.list_policy_revisions(limit=200)
        )
        skipped.extend(revision_skips)
        for revision in revisions:
            status = str(revision.get("status") or "")
            rule = {
                "policy_id": revision["policy_id"],
                "status": status,
                "action": "prefer" if status == "active_preference" else "caution",
                "condition": revision.get("condition") or {},
                "effect": {
                    **(revision.get("effect") or {}),
                    "policy_mode": "soft_period_revision",
                    "hard_filter": False,
                    "safety_gate_override": False,
                },
                "reason": revision.get("reason_md") or "",
                "evidence": revision.get("evidence") or {},
                "source_scorecard": {
                    "source": "policy_revision",
                    "revision_id": revision["revision_id"],
                    "memory_scope": revision.get("memory_scope") or "core",
                    "transferability": revision.get("transferability") or "direct",
                    "period_key": revision.get("period_key") or "",
                    "period_type": revision.get("period_type") or "",
                    "confidence": revision.get("confidence"),
                },
            }
            latest = self.repository.latest_policy_rule(str(rule["policy_id"]))
            if latest and self._policy_rule_signature(latest) == self._policy_rule_signature(rule):
                skipped.append(latest)
                continue
            version = int(latest.get("version") or 0) + 1 if latest else 1
            rule["version"] = version
            rule["rule_id"] = f"{rule['policy_id']}@v{version}"
            file_path = self._write_policy_rule_file(rule)
            rule["file_path"] = str(file_path)
            saved = self.repository.upsert_policy_rule(rule)
            if latest:
                self.repository.retire_policy_rule(
                    str(latest.get("policy_id") or ""),
                    _safe_int(latest.get("version")),
                )
            created.append(saved)
        return {
            "status": "ok",
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created": created,
        }

    @staticmethod
    def _canonical_active_policy_revisions(
        revisions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        active_by_policy: dict[str, dict[str, Any]] = {}
        skipped: list[dict[str, Any]] = []
        for revision in revisions:
            status = str(revision.get("status") or "")
            policy_id = str(revision.get("policy_id") or "")
            if status not in {"active_caution", "active_preference"} or not policy_id:
                skipped.append(revision)
                continue
            current = active_by_policy.get(policy_id)
            if current is None:
                active_by_policy[policy_id] = revision
                continue
            if InvestmentMemoryService._policy_revision_sort_key(
                revision
            ) > InvestmentMemoryService._policy_revision_sort_key(current):
                skipped.append(current)
                active_by_policy[policy_id] = revision
            else:
                skipped.append(revision)
        canonical = sorted(
            active_by_policy.values(),
            key=InvestmentMemoryService._policy_revision_sort_key,
            reverse=True,
        )
        return canonical, skipped

    @staticmethod
    def _policy_revision_sort_key(revision: dict[str, Any]) -> tuple[str, float, str]:
        return (
            str(revision.get("created_at") or ""),
            _safe_float(revision.get("confidence")),
            str(revision.get("revision_id") or ""),
        )

    def sync_policy_rules(self) -> dict[str, Any]:
        self.initialize()
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        revision_result = self._sync_revisions_to_policy_rules()
        created.extend(list(revision_result.get("created") or []))
        active_revisions, revision_skips = self._canonical_active_policy_revisions(
            self.repository.list_policy_revisions(limit=200)
        )
        skipped.extend(revision_skips)
        revision_policy_ids = {
            str(row.get("policy_id") or "")
            for row in active_revisions
            if str(row.get("policy_id") or "")
        }
        for scorecard in self.repository.list_policy_scorecards(limit=200):
            policy_id = str(scorecard.get("policy_id") or "")
            if policy_id in revision_policy_ids:
                skipped.append(scorecard)
                continue
            if str(scorecard.get("status") or "") in {"resolved", "inactive", "retired"}:
                latest = self.repository.latest_policy_rule(policy_id)
                if latest and str(latest.get("status") or "") != "retired":
                    self.repository.retire_policy_rule(
                        str(latest.get("policy_id") or ""),
                        _safe_int(latest.get("version")),
                    )
                    skipped.append({**latest, "retired_by_scorecard_status": True})
                else:
                    skipped.append(scorecard)
                continue
            rule = self._policy_rule_from_scorecard(scorecard)
            if not rule:
                continue
            latest = self.repository.latest_policy_rule(str(rule.get("policy_id") or ""))
            if latest and self._policy_rule_signature(latest) == self._policy_rule_signature(rule):
                skipped.append(latest)
                continue
            version = int(latest.get("version") or 0) + 1 if latest else 1
            rule["version"] = version
            rule["rule_id"] = f"{rule['policy_id']}@v{version}"
            file_path = self._write_policy_rule_file(rule)
            rule["file_path"] = str(file_path)
            saved = self.repository.upsert_policy_rule(rule)
            if latest:
                self.repository.retire_policy_rule(
                    str(latest.get("policy_id") or ""),
                    _safe_int(latest.get("version")),
                )
            created.append(saved)
        return {
            "status": "ok",
            "created_count": len(created),
            "skipped_count": len(skipped) + _safe_int(revision_result.get("skipped_count")),
            "created": created,
        }

    def evaluate_policy_rules(
        self,
        *,
        symbols: list[str] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        account: dict[str, Any] | None = None,
        quotes: list[dict[str, Any]] | None = None,
        strategy: dict[str, Any] | None = None,
        market_judgment: dict[str, Any] | None = None,
        allocation: dict[str, Any] | None = None,
        target_scope: str | None = None,
    ) -> dict[str, Any]:
        _ = (account, market_judgment, allocation)
        normalized_target_scope = _normalize_memory_scope(target_scope)
        all_active_rules = self.repository.list_policy_rules(active_only=True, limit=80)
        if normalized_target_scope:
            target_active_rules = self.repository.list_policy_rules_for_scope(
                target_scope=normalized_target_scope,
                active_only=True,
                limit=80,
            )
            merged_active_rules: dict[str, dict[str, Any]] = {}
            for row in [*target_active_rules, *all_active_rules]:
                if not isinstance(row, dict):
                    continue
                rule_key = _clean_text(
                    row.get("rule_id")
                    or f"{row.get('policy_id') or ''}@v{row.get('version') or ''}",
                    limit=240,
                )
                if not rule_key or rule_key in merged_active_rules:
                    continue
                merged_active_rules[rule_key] = row
            all_active_rules = list(merged_active_rules.values())
        active_rules = _policy_rows_for_target_scope(
            all_active_rules,
            target_scope=normalized_target_scope,
            memory_type="policy_rule",
        )
        quote_by_symbol = {
            str(row.get("symbol") or ""): row
            for row in list(quotes or [])
            if isinstance(row, dict)
        }
        def is_policy_symbol(value: Any) -> bool:
            return _is_symbol(value) or _is_crypto_symbol(value)

        candidate_symbols = [
            str(row.get("symbol") or "")
            for row in list((strategy or {}).get("candidates") or [])
            if isinstance(row, dict) and is_policy_symbol(row.get("symbol"))
        ]
        symbol_set = {
            symbol for symbol in list(symbols or []) + candidate_symbols if is_policy_symbol(symbol)
        }
        by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbol_set}
        by_block: dict[str, list[dict[str, Any]]] = {}
        global_impacts: list[dict[str, Any]] = []

        for rule in active_rules:
            policy_id = str(rule.get("policy_id") or "")
            base = self._rule_impact(rule)
            matched = False
            if policy_id == "respect_defined_stops":
                for block in list(blocks or []):
                    if not isinstance(block, dict):
                        continue
                    if str(block.get("status") or "") not in {"open", "entry_pending"}:
                        continue
                    if _safe_float(block.get("stop_price")) > 0:
                        continue
                    block_id = str(block.get("block_id") or "")
                    if block_id:
                        by_block.setdefault(block_id, []).append(base)
                        matched = True
                for symbol in symbol_set:
                    by_symbol.setdefault(symbol, []).append(base)
                    matched = True
            elif policy_id == "protect_winning_blocks":
                threshold = _safe_float((rule.get("condition") or {}).get("unrealized_pnl_pct_gte")) or 2.0
                for block in list(blocks or []):
                    if not isinstance(block, dict) or str(block.get("status") or "") != "open":
                        continue
                    symbol = str(block.get("symbol") or "")
                    quote = quote_by_symbol.get(symbol) or {}
                    current = _safe_float(block.get("current_price") or quote.get("price"))
                    entry = _safe_float(block.get("entry_price"))
                    pnl_pct = ((current - entry) / entry * 100.0) if current > 0 and entry > 0 else 0.0
                    if pnl_pct >= threshold:
                        block_id = str(block.get("block_id") or "")
                        if block_id:
                            impact = {**base, "matched_metric": {"unrealized_pnl_pct": pnl_pct}}
                            by_block.setdefault(block_id, []).append(impact)
                            matched = True
            elif policy_id == "review_order_and_data_failures":
                for block in list(blocks or []):
                    if not isinstance(block, dict):
                        continue
                    if str(block.get("status") or "") != "error":
                        continue
                    block_id = str(block.get("block_id") or "")
                    if block_id:
                        by_block.setdefault(block_id, []).append(base)
                        matched = True
                for symbol, quote in quote_by_symbol.items():
                    if str(quote.get("status") or "") == "error":
                        by_symbol.setdefault(symbol, []).append(base)
                        matched = True
            elif policy_id == "wait_for_clean_block_validation":
                for symbol in symbol_set:
                    by_symbol.setdefault(symbol, []).append(base)
                    matched = True
            elif policy_id.startswith("validation_attribution."):
                condition = rule.get("condition") if isinstance(rule.get("condition"), dict) else {}
                attribution = (
                    condition.get("live_authority_failure_attribution")
                    if isinstance(condition.get("live_authority_failure_attribution"), dict)
                    else {}
                )
                group_type = str(attribution.get("group_type") or "").strip()
                group = str(attribution.get("group") or "").strip()
                impact = {
                    **base,
                    "matched_metric": {
                        "group_type": group_type,
                        "group": group,
                    },
                }
                for candidate in list((strategy or {}).get("candidates") or []):
                    if not isinstance(candidate, dict):
                        continue
                    candidate_value = str(candidate.get(group_type) or "").strip()
                    symbol = str(candidate.get("symbol") or "").strip()
                    if group_type and group and candidate_value == group and is_policy_symbol(symbol):
                        by_symbol.setdefault(symbol, []).append(impact)
                        matched = True
                if not matched:
                    global_impacts.append(impact)
                    matched = True
            elif policy_id.startswith("validation."):
                condition = rule.get("condition") if isinstance(rule.get("condition"), dict) else {}
                discipline_id = str(
                    condition.get("live_authority_failed_discipline") or ""
                ).strip()
                venue = _normalize_memory_scope(condition.get("live_authority_venue"))
                if not discipline_id:
                    remainder = policy_id.removeprefix("validation.")
                    parts = remainder.split(".", 1)
                    if parts and parts[0] in MEMORY_SCOPES and len(parts) > 1:
                        venue = venue or parts[0]
                        discipline_id = parts[1]
                    else:
                        discipline_id = remainder
                if not venue:
                    venue = normalized_target_scope
                impact = {
                    **base,
                    "matched_metric": {
                        "discipline_id": discipline_id,
                        "venue": venue,
                        "target_scope": normalized_target_scope,
                        "match_scope": "venue_validation",
                    },
                }
                global_impacts.append(impact)
                matched = True
                for symbol in sorted(symbol_set):
                    by_symbol.setdefault(symbol, []).append(
                        {
                            **impact,
                            "matched_metric": {
                                **impact["matched_metric"],
                                "symbol": symbol,
                                "match_scope": "candidate_under_validation_policy",
                            },
                        }
                    )
            else:
                global_impacts.append(base)
                matched = True
            if matched and base not in global_impacts and policy_id in {
                "respect_defined_stops",
                "wait_for_clean_block_validation",
            }:
                global_impacts.append(base)

        applied_count = (
            len(global_impacts)
            + sum(len(rows) for rows in by_symbol.values())
            + sum(len(rows) for rows in by_block.values())
        )
        return {
            "status": "ok",
            "active_rule_count": len(active_rules),
            "applied_count": applied_count,
            "active_rules": active_rules[:20],
            "global": global_impacts[:20],
            "by_symbol": by_symbol,
            "by_block": by_block,
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        block_ids: list[str] | None = None,
        context: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        account: dict[str, Any] | None = None,
        quotes: list[dict[str, Any]] | None = None,
        strategy: dict[str, Any] | None = None,
        market_pulse: dict[str, Any] | None = None,
        market_judgment: dict[str, Any] | None = None,
        allocation: dict[str, Any] | None = None,
        portfolio_balance: dict[str, Any] | None = None,
        horizon_allocation: dict[str, Any] | None = None,
        etf_research: dict[str, Any] | None = None,
        decision_packet_v2: dict[str, Any] | None = None,
        target_scope: str | None = None,
        source_scope: str | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        self.sync_policy_rules()
        _ = source_scope
        enforce_budget = True
        limit = max(int(max_chars or self.config.context_max_chars), 1000)
        normalized_target_scope = _normalize_memory_scope(target_scope)
        policy_fetch_limit = 80 if normalized_target_scope else 12
        scorecard_fetch_limit = 80 if normalized_target_scope else 8
        persona = self._read_memory_file("persona.md", limit=1800)
        trading = self._read_memory_file("policies/trading.md", limit=2200)
        active = self.active_policies(
            limit=policy_fetch_limit,
            target_scope=normalized_target_scope,
        )
        latest = self.repository.latest_journals(limit=12)
        seed_notes = self.repository.list_insights(memory_type="seed", limit=2)
        all_active_insights = self.repository.list_insights(status="active", limit=80)
        if normalized_target_scope:
            target_active_insights = self.repository.list_insights_for_scope(
                target_scope=normalized_target_scope,
                status="active",
                limit=80,
            )
            merged_insights: dict[str, dict[str, Any]] = {}
            for row in [*target_active_insights, *all_active_insights]:
                if not isinstance(row, dict):
                    continue
                insight_key = _clean_text(
                    f"{row.get('memory_type') or ''}:{row.get('key') or ''}:{row.get('status') or ''}",
                    limit=260,
                )
                if not insight_key or insight_key in merged_insights:
                    continue
                merged_insights[insight_key] = row
            all_active_insights = list(merged_insights.values())
        active_insights = _insight_rows_for_target_scope(
            all_active_insights,
            target_scope=normalized_target_scope,
            limit=80,
        )
        all_recent_reflections = self.repository.list_block_reflections(limit=80)
        recent_reflections = _reflection_rows_for_target_scope(
            all_recent_reflections,
            target_scope=normalized_target_scope,
            limit=8,
        )
        all_scorecards = self.repository.list_policy_scorecards(limit=scorecard_fetch_limit)
        if normalized_target_scope:
            target_scorecards = self.repository.list_policy_scorecards_for_scope(
                target_scope=normalized_target_scope,
                limit=scorecard_fetch_limit,
            )
            merged_scorecards: dict[str, dict[str, Any]] = {}
            for row in [*target_scorecards, *all_scorecards]:
                if not isinstance(row, dict):
                    continue
                policy_id = _clean_text(row.get("policy_id"), limit=160)
                if not policy_id or policy_id in merged_scorecards:
                    continue
                merged_scorecards[policy_id] = row
            all_scorecards = list(merged_scorecards.values())
        translated_scorecards = all_scorecards
        if normalized_target_scope:
            scope_translated_scorecards = (
                self.repository.list_translated_policy_scorecards(
                    target_scope=normalized_target_scope,
                    limit=24,
                )
            )
            merged_scorecards: dict[str, dict[str, Any]] = {}
            for row in [*all_scorecards, *scope_translated_scorecards]:
                if not isinstance(row, dict):
                    continue
                policy_id = _clean_text(row.get("policy_id"), limit=160)
                if not policy_id or policy_id in merged_scorecards:
                    continue
                merged_scorecards[policy_id] = row
            translated_scorecards = list(merged_scorecards.values())
        repair_scorecards = self.repository.list_policy_scorecards(limit=80)
        all_rules = self.repository.list_policy_rules(limit=policy_fetch_limit)
        if normalized_target_scope:
            target_rules = self.repository.list_policy_rules_for_scope(
                target_scope=normalized_target_scope,
                limit=policy_fetch_limit,
            )
            merged_rules: dict[str, dict[str, Any]] = {}
            for row in [*target_rules, *all_rules]:
                if not isinstance(row, dict):
                    continue
                rule_key = _clean_text(
                    row.get("rule_id")
                    or f"{row.get('policy_id') or ''}@v{row.get('version') or ''}",
                    limit=240,
                )
                if not rule_key or rule_key in merged_rules:
                    continue
                merged_rules[rule_key] = row
            all_rules = list(merged_rules.values())
        validation_events = self.repository.list_memory_events(
            status="processed",
            limit=12,
        )
        scorecards = _policy_rows_for_target_scope(
            all_scorecards,
            target_scope=normalized_target_scope,
            memory_type="policy_scorecard",
        )
        jue_wiki_selection_memory = _compact_jue_wiki_selection_memory(
            scorecards,
            target_scope=normalized_target_scope,
            limit=4,
        )
        jue_wiki_context_gap_memory = _compact_jue_wiki_context_gap_memory(
            scorecards,
            target_scope=normalized_target_scope,
            limit=4,
        )
        jue_wiki_action_reference_memory = (
            _compact_jue_wiki_action_reference_memory(
                scorecards,
                target_scope=normalized_target_scope,
                limit=4,
            )
        )
        jue_wiki_usage_contract_memory = (
            _compact_jue_wiki_usage_contract_memory(
                scorecards,
                target_scope=normalized_target_scope,
                limit=4,
            )
        )
        jue_wiki_action_reference_recovery = (
            _jue_wiki_action_reference_recovery_summary(
                scorecards,
                target_scope=normalized_target_scope,
            )
        )
        translated_policy_context = _translated_policy_context_for_target_scope(
            translated_scorecards,
            target_scope=normalized_target_scope,
            limit=4,
        )
        rules = _policy_rows_for_target_scope(
            all_rules,
            target_scope=normalized_target_scope,
            memory_type="policy_rule",
        )
        active = _active_policies_for_target_scope(
            active,
            target_scope=normalized_target_scope,
        )
        validation_repair_backlog = _build_validation_repair_backlog(
            validation_events,
            repair_scorecards,
            target_scope=normalized_target_scope,
            limit=8,
        )
        latest = _journal_rows_for_target_scope(
            latest,
            target_scope=normalized_target_scope,
            limit=4,
        )
        period_reviews = {
            "weekly": self.repository.latest_period_review(
                "weekly",
                target_scope=normalized_target_scope,
            ),
            "monthly": self.repository.latest_period_review(
                "monthly",
                target_scope=normalized_target_scope,
            ),
        }
        historical_replays = {
            "weekly": self.repository.latest_historical_replay(
                "weekly",
                target_scope=normalized_target_scope,
            ),
            "monthly": self.repository.latest_historical_replay(
                "monthly",
                target_scope=normalized_target_scope,
            ),
        }
        period_memory_coverage = self._period_memory_coverage(
            target_scope=normalized_target_scope
        )
        policy_revisions = self.repository.list_policy_revisions(
            limit=12,
            target_scope=normalized_target_scope,
        )
        policy_outcomes = self.repository.list_policy_outcomes(
            limit=12,
            target_scope=normalized_target_scope,
        )
        decision_skills = self._decision_skills()
        missing_skills = [
            key
            for key, value in decision_skills.items()
            if not str(value.get("content_md") or "").strip()
        ]
        rule_evaluation = self.evaluate_policy_rules(
            symbols=symbols or [],
            blocks=blocks or [],
            account=account or {},
            quotes=quotes or [],
            strategy=strategy or {},
            market_judgment=market_judgment or {},
            allocation=allocation or {},
            target_scope=normalized_target_scope,
        )
        block_design_constraints = _build_block_design_constraints(
            validation_repair_backlog=validation_repair_backlog,
            rule_evaluation=rule_evaluation,
            limit=8,
        )
        validation_recovery_summary = _build_validation_recovery_summary(
            validation_repair_backlog=validation_repair_backlog,
            block_design_constraints=block_design_constraints,
            rule_evaluation=rule_evaluation,
            limit=8,
        )
        next_block_design_playbook = _build_next_block_design_playbook(
            validation_recovery_summary=validation_recovery_summary,
            block_design_constraints=block_design_constraints,
            rule_evaluation=rule_evaluation,
            limit=8,
        )
        symbol_notes: dict[str, str] = {}
        symbol_analyses: dict[str, list[dict[str, Any]]] = {}
        lifecycle_symbols = [
            symbol
            for symbol in (_clean_text(value, limit=40) for value in symbols or [])
            if symbol
        ]
        wiki_context: dict[str, Any] = {
            "status": "disabled",
            "available": False,
            "reason": "wiki_context_provider_unavailable",
        }
        if self.wiki_context_provider is not None:
            wiki_context_budget = min(max(limit // 3, 4000), 24000)
            try:
                provider_payload = self.wiki_context_provider(
                    target_scope=normalized_target_scope,
                    symbols=lifecycle_symbols,
                    max_chars=wiki_context_budget,
                )
                wiki_context = (
                    provider_payload
                    if isinstance(provider_payload, dict)
                    else {
                        "status": "error",
                        "available": False,
                        "target_scope": normalized_target_scope or "all",
                        "symbols": lifecycle_symbols,
                        "reason": "wiki_context_provider_returned_non_dict",
                    }
                )
            except Exception as exc:
                wiki_context = {
                    "status": "error",
                    "available": False,
                    "target_scope": normalized_target_scope or "all",
                    "symbols": lifecycle_symbols,
                    "reason": str(exc) or exc.__class__.__name__,
                }
        lifecycle_artifacts = [
            _compact_lifecycle_artifact(row)
            for row in self.lifecycle_repository.list_artifacts(
                symbols=lifecycle_symbols or None,
                limit=8,
            )
        ]
        lifecycle_artifact_ids = {
            row.get("artifact_id")
            for row in lifecycle_artifacts
            if isinstance(row.get("artifact_id"), str)
        }
        for symbol in symbols or []:
            if not _is_symbol(symbol):
                continue
            path = self.root / "symbols" / f"{symbol}.md"
            if path.exists():
                symbol_notes[symbol] = _truncate(path.read_text(encoding="utf-8"), 1200)
            analyses = self.repository.list_symbol_analyses(symbol, limit=3)
            analysis_rows = [
                row
                for row in list(analyses.get("items") or [])[:3]
                if isinstance(row, dict)
            ]
            items = [
                _compact_symbol_analysis(row)
                for row in analysis_rows
            ]
            items = [row for row in items if row]
            if items:
                symbol_analyses[symbol] = items
            for row in analysis_rows[:2]:
                if len(lifecycle_artifacts) >= 8:
                    break
                artifact = _compact_lifecycle_artifact(
                    _symbol_analysis_lifecycle_artifact(row)
                )
                artifact_id = artifact.get("artifact_id")
                if artifact and artifact_id not in lifecycle_artifact_ids:
                    lifecycle_artifacts.append(artifact)
                    lifecycle_artifact_ids.add(artifact_id)
        block_notes: dict[str, str] = {}
        for block_id in block_ids or []:
            key = _clean_text(block_id, limit=160)
            if not key:
                continue
            path = self.root / "blocks" / f"{key}.md"
            if path.exists():
                block_notes[key] = _truncate(path.read_text(encoding="utf-8"), 1200)
        etf_core = _compact_etf_core_context(
            etf_research=etf_research,
            blocks=blocks or [],
            allocation=portfolio_balance or horizon_allocation or allocation,
        )
        context_payload = context if isinstance(context, dict) else {}
        compact_decision_packet = _compact_decision_packet_v2(
            decision_packet_v2
            or (
                context_payload.get("decision_packet_v2")
                if isinstance(context_payload.get("decision_packet_v2"), dict)
                else None
            )
        )
        compact_daily_discovery = _compact_daily_discovery(
            context_payload.get("daily_discovery")
            if isinstance(context_payload.get("daily_discovery"), dict)
            else None
        )

        payload = {
            "status": "ok",
            "memory_scope": normalized_target_scope or "all",
            "persona": persona,
            "trading_policy": trading,
            "seed_memory": [
                {
                    "key": row.get("key"),
                    "summary_md": _truncate(row.get("summary_md"), 900),
                    "updated_at": row.get("updated_at"),
                }
                for row in seed_notes
            ],
            "active_insights": [
                {
                    "key": row.get("key"),
                    "memory_type": row.get("memory_type"),
                    "scope": row.get("scope"),
                    "summary_md": _truncate(row.get("summary_md"), 700),
                    "confidence": row.get("confidence"),
                    "updated_at": row.get("updated_at"),
                }
                for row in active_insights[:12]
            ],
            "active_policies": active,
            "policy_scorecards": scorecards,
            "jue_wiki_selection_memory": jue_wiki_selection_memory,
            "jue_wiki_context_gap_memory": jue_wiki_context_gap_memory,
            "jue_wiki_action_reference_memory": jue_wiki_action_reference_memory,
            "jue_wiki_usage_contract_memory": jue_wiki_usage_contract_memory,
            "jue_wiki_action_reference_recovery": (
                jue_wiki_action_reference_recovery
            ),
            "translated_policy_context": translated_policy_context,
            "policy_rules": rules,
            "validation_repair_backlog": validation_repair_backlog,
            "block_design_constraints": block_design_constraints,
            "validation_recovery_summary": validation_recovery_summary,
            "period_memory_coverage": period_memory_coverage,
            "period_reviews": {
                key: {
                    "period_key": value.get("period_key"),
                    "memory_scope": value.get("memory_scope") or value.get("scope"),
                    "status": value.get("status"),
                    "metrics": value.get("metrics") or {},
                    "review_md": _truncate(value.get("review_md"), 900),
                    "updated_at": value.get("updated_at"),
                }
                for key, value in period_reviews.items()
                if value.get("status") != "missing"
            },
            "historical_replays": {
                key: {
                    "period_key": value.get("period_key"),
                    "memory_scope": value.get("memory_scope") or value.get("scope"),
                    "status": value.get("status"),
                    "case_count": value.get("case_count"),
                    "replay_md": _truncate(value.get("replay_md"), 900),
                    "updated_at": value.get("updated_at"),
                }
                for key, value in historical_replays.items()
                if value.get("status") != "missing"
            },
            "policy_revisions": [
                {
                    "revision_id": row.get("revision_id"),
                    "memory_scope": row.get("memory_scope"),
                    "transferability": row.get("transferability"),
                    "policy_id": row.get("policy_id"),
                    "action": row.get("action"),
                    "status": row.get("status"),
                    "scope": row.get("scope"),
                    "effect": row.get("effect") or {},
                    "reason_md": _truncate(row.get("reason_md"), 500),
                    "confidence": row.get("confidence"),
                }
                for row in policy_revisions
            ],
            "policy_outcomes": policy_outcomes,
            "policy_rule_evaluation": rule_evaluation,
            "decision_skills": decision_skills,
            "decision_skill_status": {
                "count": len(decision_skills),
                "missing": missing_skills,
            },
            "market_pulse": _compact_market_pulse(market_pulse),
            "etf_core": etf_core or {"status": "not_relevant"},
            "jue_wiki": wiki_context,
            "recent_reflections": [
                {
                    "block_id": row.get("block_id"),
                    "symbol": row.get("symbol"),
                    "scope": row.get("scope"),
                    "pnl_pct": row.get("pnl_pct"),
                    "exit_reason": row.get("exit_reason"),
                    "lesson_md": _truncate(row.get("lesson_md"), 500),
                }
                for row in recent_reflections
            ],
            "latest_journals": [
                {
                    "trading_day": row.get("trading_day"),
                    "slot": row.get("slot"),
                    "title": row.get("title"),
                    "message_md": _truncate(row.get("message_md"), 900),
                }
                for row in latest
            ],
            "symbol_notes": symbol_notes,
            "block_notes": block_notes,
            "safety_note": (
                "Memory guides live trading decisions. Kill switch, cash limits, "
                "position limits, and duplicate-order guards always override memory policies."
            ),
        }
        if next_block_design_playbook.get("status") != "normal":
            payload["next_block_design_playbook"] = next_block_design_playbook
        if compact_decision_packet:
            payload["decision_packet_v2"] = compact_decision_packet
        if compact_daily_discovery:
            payload["daily_discovery"] = compact_daily_discovery
        if lifecycle_artifacts:
            payload["lifecycle_artifacts"] = lifecycle_artifacts
        if normalized_target_scope:
            payload["scoped_memory"] = _build_scoped_memory_payload(
                target_scope=normalized_target_scope,
                insights=all_active_insights,
                reflections=all_recent_reflections,
                scorecards=all_scorecards,
                rules=all_rules,
            )
        if symbol_analyses:
            payload["symbol_analyses"] = symbol_analyses
        text = _json_dumps(payload)
        if len(text) <= limit:
            return payload
        payload["latest_journals"] = payload["latest_journals"][:2]
        payload["symbol_notes"] = {
            key: _truncate(value, 500)
            for key, value in symbol_notes.items()
        }
        if symbol_analyses:
            payload["symbol_analyses"] = {
                key: [
                    {
                        **row,
                        "summary": _clean_text(row.get("summary"), limit=240),
                        "risks": list(row.get("risks") or [])[:2],
                        "data_gaps": list(row.get("data_gaps") or [])[:2],
                    }
                    for row in rows[:2]
                ]
                for key, rows in symbol_analyses.items()
            }
        payload["block_notes"] = {
            key: _truncate(value, 500)
            for key, value in block_notes.items()
        }
        payload["decision_skills"] = {
            key: {
                **value,
                "content_md": _truncate(value.get("content_md"), 160),
            }
            for key, value in decision_skills.items()
        }
        text = _json_dumps(payload)
        if len(text) <= limit:
            return payload
        payload["decision_skills"] = {
            key: {
                **value,
                "content_md": "",
            }
            for key, value in decision_skills.items()
        }
        if enforce_budget:
            return _enforce_context_pack_budget(payload, limit=limit)
        return payload

    def build_ritual_context(
        self,
        *,
        slot: str,
        trading_day: str,
        account: dict[str, Any] | None = None,
        blocks: dict[str, Any] | None = None,
        etf_research: dict[str, Any] | None = None,
        allocation: dict[str, Any] | None = None,
        llm_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_slot = str(slot or "").strip()
        workflow_id = RITUAL_WORKFLOW_BY_SLOT.get(
            normalized_slot,
            "kis_intraday_manager",
        )
        context: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "trading_day": trading_day,
            "account": account or {},
            "blocks": blocks or {},
            "jue_workflow": _jue_workflow_pack(workflow_id),
        }
        if etf_research is not None:
            context["etf_research"] = etf_research
        if allocation is not None:
            context["allocation"] = allocation
        if llm_usage is not None:
            context["llm_usage"] = _compact_llm_usage_context(llm_usage)
        return _compact_ritual_context(context, limit=self.config.context_max_chars)

    def build_block_reflection_context(
        self,
        *,
        block: dict[str, Any],
        orders: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return _compact_ritual_context(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "blocks": {
                    "blocks": [block],
                    "orders": orders or [],
                    "events": events or [],
                },
                "jue_workflow": _jue_workflow_pack("block_reflection"),
            },
            limit=self.config.context_max_chars,
        )

    async def run_ritual(
        self,
        *,
        slot: str,
        context: dict[str, Any] | None = None,
        send_telegram: bool = False,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        normalized_slot = self._normalize_slot(slot)
        trading_day = self._trading_day(now)
        existing = self.repository.get_journal(trading_day, normalized_slot)
        if existing and not force:
            telegram_result = {}
            existing_context = existing.get("context") if isinstance(existing.get("context"), dict) else {}
            existing_message = _prefer_symbol_names_in_message(
                str(existing.get("message_md") or ""),
                _collect_symbol_display_names(existing_context),
            )
            if send_telegram and not existing.get("sent_telegram"):
                telegram_result = await self._send_telegram_once(
                    trading_day=trading_day,
                    slot=normalized_slot,
                    message=existing_message,
                )
                existing = self.repository.upsert_journal(
                    trading_day=trading_day,
                    slot=normalized_slot,
                    title=existing["title"],
                    message_md=existing_message,
                    file_path=existing["file_path"],
                    context=existing_context,
                    sent_telegram=bool(telegram_result.get("ok")),
                    telegram_result=telegram_result,
                )
            return {
                "status": "skipped",
                "reason": "journal_already_exists",
                "trading_day": trading_day,
                "slot": normalized_slot,
                "journal": existing,
                "telegram_result": telegram_result,
            }

        compact_context = _compact_ritual_context(
            context or {},
            limit=self.config.context_max_chars,
        )
        prompt = self._build_ritual_prompt(
            slot=normalized_slot,
            trading_day=trading_day,
            context=compact_context,
        )
        prompt = _enforce_ritual_prompt_budget(prompt)
        output, mode, error = await self._complete_json(prompt)
        if not isinstance(output, dict):
            mode = "error"
            run_id = self.repository.save_run(
                kind="ritual",
                slot=normalized_slot,
                status="error",
                mode=mode,
                model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                error_message=error,
                input_payload=prompt,
                output_payload={},
            )
            return {
                "status": "error",
                "trading_day": trading_day,
                "slot": normalized_slot,
                "run_id": run_id,
                "mode": mode,
                "error_message": error,
                "journal": {},
                "telegram_result": {},
                "memory_updates": {},
                "policy_changes": [],
            }
        title = _clean_text(
            output.get("title") or SLOT_LABELS.get(normalized_slot, normalized_slot),
            limit=160,
        )
        message = str(output.get("message_md") or output.get("message") or "").strip()
        if not message:
            error = error or "ritual_message_missing"
            mode = "error"
            run_id = self.repository.save_run(
                kind="ritual",
                slot=normalized_slot,
                status="error",
                mode=mode,
                model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
                error_message=error,
                input_payload=prompt,
                output_payload=output,
            )
            return {
                "status": "error",
                "trading_day": trading_day,
                "slot": normalized_slot,
                "run_id": run_id,
                "mode": mode,
                "error_message": error,
                "journal": {},
                "telegram_result": {},
                "memory_updates": {},
                "policy_changes": [],
            }
        message = _prefer_symbol_names_in_message(
            message,
            _collect_symbol_display_names(compact_context),
        )

        run_id = self.repository.save_run(
            kind="ritual",
            slot=normalized_slot,
            status="ok",
            mode=mode,
            model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            error_message=error,
            input_payload=prompt,
            output_payload=output,
        )
        self._apply_memory_updates(output, source_run_id=run_id)
        file_path = self._write_journal_file(
            trading_day=trading_day,
            slot=normalized_slot,
            title=title,
            message_md=message,
            context=compact_context,
            output=output,
        )
        telegram_result: dict[str, Any] = {}
        if send_telegram:
            telegram_result = await self._send_telegram_once(
                trading_day=trading_day,
                slot=normalized_slot,
                message=message,
            )
        journal = self.repository.upsert_journal(
            trading_day=trading_day,
            slot=normalized_slot,
            title=title,
            message_md=message,
            file_path=str(file_path),
            context=compact_context,
            sent_telegram=bool(telegram_result.get("ok")),
            telegram_result=telegram_result,
        )
        return {
            "status": "ok",
            "trading_day": trading_day,
            "slot": normalized_slot,
            "run_id": run_id,
            "mode": mode,
            "error_message": error,
            "journal": journal,
            "telegram_result": telegram_result,
            "memory_updates": output.get("memory_updates") or {},
            "policy_changes": output.get("policy_changes") or [],
        }

    async def run_update(
        self,
        *,
        context: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        payload = dict(context or {})
        payload["update_reason"] = payload.get("update_reason") or "manual_memory_update"
        return await self.run_ritual(
            slot="weekly",
            context=payload,
            send_telegram=False,
            force=force,
        )

    def seed_current(
        self,
        *,
        context: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        payload = context or {}
        trading_day = self._trading_day()
        existing = self.repository.get_journal(trading_day, "seed")
        if existing and not force:
            return {
                "status": "skipped",
                "reason": "seed_already_exists",
                "trading_day": trading_day,
                "journal": existing,
            }

        output = self._build_seed_output(payload, trading_day=trading_day)
        run_id = self.repository.save_run(
            kind="seed",
            slot="seed",
            status="ok",
            mode="deterministic",
            model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            error_message="",
            input_payload=payload,
            output_payload=output,
        )
        self._apply_memory_updates(output, source_run_id=run_id)
        title = str(output.get("title") or SLOT_LABELS["seed"])
        message = str(output.get("message_md") or "").strip()
        file_path = self._write_journal_file(
            trading_day=trading_day,
            slot="seed",
            title=title,
            message_md=message,
            context=payload,
            output=output,
        )
        journal = self.repository.upsert_journal(
            trading_day=trading_day,
            slot="seed",
            title=title,
            message_md=message,
            file_path=str(file_path),
            context=payload,
        )
        return {
            "status": "ok",
            "run_id": run_id,
            "trading_day": trading_day,
            "journal": journal,
            "memory_updates": output.get("memory_updates") or {},
        }

    def run_due_reflections(
        self,
        *,
        context: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        payload = context or {}
        reflection_context = {
            **payload,
            "jue_workflow": _jue_workflow_pack("block_reflection"),
        }
        source_payloads = [
            payload.get("blocks"),
            payload.get("binance_blocks"),
        ]
        blocks_by_id: dict[str, dict[str, Any]] = {}
        orders: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []

        def rows_from_keys(
            source_payload: dict[str, Any],
            keys: tuple[str, ...],
        ) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for key in keys:
                value = source_payload.get(key)
                if not isinstance(value, list):
                    continue
                rows.extend([row for row in value if isinstance(row, dict)])
            return rows

        for source in source_payloads:
            source_payload = source if isinstance(source, dict) else {}
            for row in rows_from_keys(
                source_payload,
                (
                    "blocks",
                    "active_blocks",
                    "recent_closed_blocks",
                    "block_history",
                ),
            ):
                block_id = str(row.get("block_id") or "").strip()
                if not block_id:
                    continue
                block = dict(row)
                block.setdefault("memory_scope", _block_memory_scope(block))
                blocks_by_id[block_id] = block
            for row in rows_from_keys(
                source_payload,
                ("orders", "recent_orders", "block_orders"),
            ):
                order = dict(row)
                order.setdefault("memory_scope", _block_memory_scope(order))
                orders.append(order)
            for row in rows_from_keys(
                source_payload,
                ("events", "recent_events", "block_events"),
            ):
                event = dict(row)
                event.setdefault("memory_scope", _block_memory_scope(event))
                events.append(event)
        blocks = list(blocks_by_id.values())
        eligible = [
            row
            for row in blocks
            if self._block_needs_reflection(row, force=force)
        ]
        rejected_event_result = self._ingest_rejected_create_events(events)
        contract_error_result = self._ingest_manager_contract_error_runs(payload)
        contract_resolution_result = self._ingest_manager_contract_resolution_runs(
            payload
        )
        selection_audit_result = self._ingest_jue_wiki_selection_audit_runs(payload)
        context_gap_result = self._ingest_jue_wiki_context_gap_runs(payload)
        action_reference_result = self._ingest_jue_wiki_action_reference_gap_runs(
            payload
        )
        usage_contract_result = self._ingest_jue_wiki_usage_contract_gap_runs(
            payload
        )
        if not eligible:
            if (
                int(rejected_event_result.get("processed_count") or 0) > 0
                or int(contract_error_result.get("processed_count") or 0) > 0
                or int(contract_resolution_result.get("processed_count") or 0) > 0
                or int(selection_audit_result.get("processed_count") or 0) > 0
                or int(context_gap_result.get("processed_count") or 0) > 0
                or int(action_reference_result.get("processed_count") or 0) > 0
                or int(usage_contract_result.get("processed_count") or 0) > 0
            ):
                return {
                    "status": "ok",
                    "checked": len(blocks),
                    "created": [],
                    "created_count": 0,
                    "rejected_event_count": int(
                        rejected_event_result.get("processed_count") or 0
                    ),
                    "rejected_events": rejected_event_result.get("events") or [],
                    "contract_error_event_count": int(
                        contract_error_result.get("processed_count") or 0
                    ),
                    "contract_error_events": contract_error_result.get("events") or [],
                    "contract_resolution_event_count": int(
                        contract_resolution_result.get("processed_count") or 0
                    ),
                    "contract_resolution_events": (
                        contract_resolution_result.get("events") or []
                    ),
                    "jue_wiki_selection_audit_event_count": int(
                        selection_audit_result.get("processed_count") or 0
                    ),
                    "jue_wiki_selection_audit_events": (
                        selection_audit_result.get("events") or []
                    ),
                    "jue_wiki_context_gap_event_count": int(
                        context_gap_result.get("processed_count") or 0
                    ),
                    "jue_wiki_context_gap_events": (
                        context_gap_result.get("events") or []
                    ),
                    "jue_wiki_action_reference_gap_event_count": int(
                        action_reference_result.get("gap_processed_count") or 0
                    ),
                    "jue_wiki_action_reference_gap_events": (
                        action_reference_result.get("gap_events") or []
                    ),
                    "jue_wiki_action_reference_resolution_event_count": int(
                        action_reference_result.get("resolution_processed_count") or 0
                    ),
                    "jue_wiki_action_reference_resolution_events": (
                        action_reference_result.get("resolution_events") or []
                    ),
                    "jue_wiki_usage_contract_gap_event_count": int(
                        usage_contract_result.get("processed_count") or 0
                    ),
                    "jue_wiki_usage_contract_gap_events": (
                        usage_contract_result.get("events") or []
                    ),
                    "policy_scorecards": self.repository.list_policy_scorecards(limit=12),
                }
            return {
                "status": "skipped",
                "reason": "no_due_reflections",
                "checked": len(blocks),
                "created": [],
            }

        reflections = [
            self._build_block_reflection(row, orders=orders)
            for row in eligible
        ]
        run_id = self.repository.save_run(
            kind="reflection",
            slot="block_reflection",
            status="ok",
            mode="deterministic",
            model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            error_message="",
            input_payload=reflection_context,
            output_payload={"reflections": reflections},
        )
        saved: list[dict[str, Any]] = []
        memory_updates = {"symbols": [], "blocks": [], "notes": []}
        for reflection in reflections:
            saved_row = self.repository.upsert_block_reflection(
                reflection,
                source_run_id=run_id,
            )
            saved.append(saved_row)
            event_key = f"block_reflection:{reflection['block_id']}"
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="block_reflection",
                block_id=str(reflection.get("block_id") or ""),
                status="pending",
                payload=reflection,
            )
            self.repository.mark_memory_event_processed(event_key)
            summary = str(reflection.get("lesson_md") or "").strip()
            block_id = str(reflection.get("block_id") or "")
            symbol = str(reflection.get("symbol") or "")
            if block_id and summary:
                memory_updates["blocks"].append(
                    {"block_id": block_id, "summary_md": summary, "confidence": 0.72}
                )
            if _is_symbol(symbol) and summary:
                memory_updates["symbols"].append(
                    {
                        "symbol": symbol,
                        "summary_md": f"{reflection.get('name') or symbol}: {summary}",
                        "confidence": 0.64,
                    }
                )
            scorecard = self._scorecard_from_reflections([*saved])
            if scorecard:
                self.repository.upsert_policy_scorecard(scorecard)

        self._apply_memory_updates(
            {
                "memory_updates": memory_updates,
                "policy_changes": [],
            },
            source_run_id=run_id,
        )
        self._refresh_policy_scorecards()
        trading_day = self._trading_day()
        message = self._reflection_message(saved)
        file_path = self._write_journal_file(
            trading_day=trading_day,
            slot="block_reflection",
            title=SLOT_LABELS["block_reflection"],
            message_md=message,
            context=reflection_context,
            output={"reflections": saved},
        )
        journal = self.repository.upsert_journal(
            trading_day=trading_day,
            slot="block_reflection",
            title=SLOT_LABELS["block_reflection"],
            message_md=message,
            file_path=str(file_path),
            context=reflection_context,
        )
        return {
            "status": "ok",
            "run_id": run_id,
            "checked": len(blocks),
            "created": saved,
            "created_count": len(saved),
            "rejected_event_count": int(rejected_event_result.get("processed_count") or 0),
            "rejected_events": rejected_event_result.get("events") or [],
            "contract_error_event_count": int(
                contract_error_result.get("processed_count") or 0
            ),
            "contract_error_events": contract_error_result.get("events") or [],
            "contract_resolution_event_count": int(
                contract_resolution_result.get("processed_count") or 0
            ),
            "contract_resolution_events": contract_resolution_result.get("events") or [],
            "jue_wiki_selection_audit_event_count": int(
                selection_audit_result.get("processed_count") or 0
            ),
            "jue_wiki_selection_audit_events": (
                selection_audit_result.get("events") or []
            ),
            "jue_wiki_context_gap_event_count": int(
                context_gap_result.get("processed_count") or 0
            ),
            "jue_wiki_context_gap_events": (
                context_gap_result.get("events") or []
            ),
            "jue_wiki_action_reference_gap_event_count": int(
                action_reference_result.get("gap_processed_count") or 0
            ),
            "jue_wiki_action_reference_gap_events": (
                action_reference_result.get("gap_events") or []
            ),
            "jue_wiki_action_reference_resolution_event_count": int(
                action_reference_result.get("resolution_processed_count") or 0
            ),
            "jue_wiki_action_reference_resolution_events": (
                action_reference_result.get("resolution_events") or []
            ),
            "jue_wiki_usage_contract_gap_event_count": int(
                usage_contract_result.get("processed_count") or 0
            ),
            "jue_wiki_usage_contract_gap_events": (
                usage_contract_result.get("events") or []
            ),
            "journal": journal,
            "policy_scorecards": self.repository.list_policy_scorecards(limit=12),
        }

    def _ingest_rejected_create_events(
        self,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for event in events:
            if str(event.get("event_type") or "") != "manager_create_rejected":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if not payload:
                payload = _json_loads(event.get("payload_json"), {})
                if not isinstance(payload, dict):
                    payload = {}
            signal = self._rejected_create_policy_signal(event, payload)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="manager_create_rejected",
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_rejected_create_scorecard(signal)
            processed.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
        }

    def _ingest_manager_contract_error_runs(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for venue, run in self._manager_contract_error_run_sources(context):
            signal = self._manager_contract_error_signal(venue, run)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="manager_contract_error",
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_manager_contract_error_scorecard(signal)
            processed.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
        }

    def _ingest_manager_contract_resolution_runs(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for venue, run in self._manager_contract_error_run_sources(context):
            signal = self._manager_contract_resolution_signal(venue, run)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="manager_contract_resolution",
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_manager_contract_resolution_scorecard(signal)
            processed.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
        }

    def _ingest_jue_wiki_selection_audit_runs(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for venue, run in self._manager_contract_error_run_sources(context):
            signal = self._jue_wiki_selection_audit_signal(venue, run)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="jue_wiki_selection_audit",
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_jue_wiki_selection_audit_scorecard(signal)
            processed.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
        }

    def _ingest_jue_wiki_context_gap_runs(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for venue, run in self._manager_contract_error_run_sources(context):
            signal = self._jue_wiki_context_gap_signal(venue, run)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="jue_wiki_context_gap",
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_jue_wiki_context_gap_scorecard(signal)
            processed.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
        }

    def _ingest_jue_wiki_action_reference_gap_runs(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        gap_events: list[dict[str, Any]] = []
        resolution_events: list[dict[str, Any]] = []
        for venue, run in self._manager_contract_error_run_sources(context):
            signal = self._jue_wiki_action_reference_gap_signal(venue, run)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            is_resolution = bool(signal.get("is_resolution"))
            event_type = (
                "jue_wiki_action_reference_resolution"
                if is_resolution
                else "jue_wiki_action_reference_gap"
            )
            self.repository.save_memory_event(
                event_key=event_key,
                event_type=event_type,
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_jue_wiki_action_reference_gap_scorecard(signal)
            processed.append(signal)
            if is_resolution:
                resolution_events.append(signal)
            else:
                gap_events.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
            "gap_processed_count": len(gap_events),
            "gap_events": gap_events,
            "resolution_processed_count": len(resolution_events),
            "resolution_events": resolution_events,
        }

    def _ingest_jue_wiki_usage_contract_gap_runs(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        for venue, run in self._manager_contract_error_run_sources(context):
            signal = self._jue_wiki_usage_contract_gap_signal(venue, run)
            if not signal:
                continue
            event_key = str(signal.get("event_key") or "")
            existing = self.repository.get_memory_event(event_key)
            if existing and str(existing.get("status") or "") == "processed":
                continue
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="jue_wiki_usage_contract_gap",
                block_id="__system__",
                status="pending",
                payload=signal,
            )
            self.repository.mark_memory_event_processed(event_key)
            self._upsert_jue_wiki_usage_contract_gap_scorecard(signal)
            processed.append(signal)
        return {
            "processed_count": len(processed),
            "events": processed,
        }

    def _manager_contract_error_run_sources(
        self,
        context: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        sources: list[tuple[str, dict[str, Any]]] = []

        def add(venue: str, value: Any) -> None:
            if isinstance(value, dict) and value:
                sources.append((venue, value))

        def add_runs(venue: str, value: Any) -> None:
            if not isinstance(value, list):
                return
            for row in value[:5]:
                add(venue, row)

        add("kis", context.get("latest_manager_run"))
        add_runs("kis", context.get("manager_runs"))
        blocks = context.get("blocks") if isinstance(context.get("blocks"), dict) else {}
        add("kis", blocks.get("latest_manager_run"))
        add_runs("kis", blocks.get("manager_runs"))
        binance_blocks = (
            context.get("binance_blocks")
            if isinstance(context.get("binance_blocks"), dict)
            else {}
        )
        add("binance", binance_blocks.get("latest_manager_run"))
        add_runs("binance", binance_blocks.get("manager_runs"))

        unique: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        for venue, run in sources:
            marker = f"{venue}:{run.get('id') or run.get('run_id') or run.get('run_at')}"
            if marker in seen:
                continue
            seen.add(marker)
            unique.append((venue, run))
        return unique

    def _jue_wiki_selection_audit_signal(
        self,
        venue: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        application = self._jue_wiki_application_from_manager_run(run)
        audit = (
            application.get("selection_audit")
            if isinstance(application.get("selection_audit"), dict)
            else {}
        )
        if not audit:
            audit = self._jue_wiki_selection_audit_from_diagnostics(
                run,
                application=application,
            )
        if not audit:
            return {}
        clean_venue = _normalize_memory_scope(venue)
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(run.get("id") or run.get("run_id"), limit=80)
        run_seed = run_id or self._normalize_policy_id(
            f"{run.get('run_at')}:{application.get('selection_run_id')}"
        )
        if not run_seed:
            return {}
        reason_counts = self._compact_count_map(audit.get("reason_counts"), limit=8)
        penalty_counts = self._compact_count_map(audit.get("penalty_counts"), limit=6)
        primary_reason = self._primary_wiki_selection_reason(reason_counts)
        if not primary_reason:
            return {}
        reason_token = self._normalize_policy_id(primary_reason) or "unknown"
        workflow_provenance = self._manager_run_workflow_provenance_for_memory(run)
        selected_page_ids = [
            _clean_text(page_id, limit=120)
            for page_id in list(application.get("selected_page_ids") or [])[:12]
            if _clean_text(page_id, limit=120)
        ]
        top_pages: list[dict[str, Any]] = []
        for page in list(audit.get("top_pages") or [])[:4]:
            if not isinstance(page, dict):
                continue
            page_id = _clean_text(page.get("page_id"), limit=120)
            if not page_id:
                continue
            top_pages.append(
                {
                    "page_id": page_id,
                    "rank": _safe_int(page.get("rank")),
                    "selection_reasons": [
                        _clean_text(reason, limit=120)
                        for reason in list(page.get("selection_reasons") or [])[:5]
                        if _clean_text(reason, limit=120)
                    ],
                }
            )
        event_key = f"jue_wiki_selection_audit:{clean_venue}:{run_seed}"
        return {
            "event_key": event_key,
            "policy_id": f"jue_wiki_selection.{clean_venue}.{reason_token}",
            "venue": clean_venue,
            "memory_scope": clean_venue,
            "transferability": "direct",
            "scope_evidence": [
                {
                    "memory_scope": clean_venue,
                    "transferability": "direct",
                    "source": "jue_wiki_selection_audit",
                }
            ],
            "manager_run_id": _safe_int(run_id),
            "run_at": _clean_text(run.get("run_at"), limit=80),
            "selection_run_id": _clean_text(
                application.get("selection_run_id"),
                limit=120,
            ),
            "status": _clean_text(application.get("status"), limit=80),
            "primary_reason": primary_reason,
            "selected_page_count": _safe_int(audit.get("selected_page_count")),
            "selected_page_ids": selected_page_ids,
            "reason_counts": reason_counts,
            "penalty_counts": penalty_counts,
            "top_pages": top_pages,
            **workflow_provenance,
        }

    def _jue_wiki_context_gap_signal(
        self,
        venue: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        diagnostics = self._jue_wiki_diagnostics_from_manager_run(run)
        if not diagnostics:
            return {}
        gap_status = _clean_text(
            diagnostics.get("jue_wiki_context_gap_status"),
            limit=80,
        )
        resolution_status = _clean_text(
            diagnostics.get("jue_wiki_context_gap_resolution_status"),
            limit=80,
        )
        blocker_tags = (
            diagnostics.get("blocker_tags")
            if isinstance(diagnostics.get("blocker_tags"), dict)
            else {}
        )
        active_gap = gap_status == "active" or _safe_int(
            blocker_tags.get("unresolved_jue_wiki_context_gap")
        )
        unresolved = resolution_status in {"", "unresolved"}
        if not active_gap or not unresolved:
            return {}
        clean_venue = _normalize_memory_scope(venue)
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(run.get("id") or run.get("run_id"), limit=80)
        run_seed = run_id or self._normalize_policy_id(
            f"{run.get('run_at')}:{gap_status}:{resolution_status}"
        )
        if not run_seed:
            return {}
        wiki_context = self._jue_wiki_context_from_manager_run(run)
        reason = _clean_text(
            wiki_context.get("reason")
            or diagnostics.get("jue_wiki_context_gap_reason")
            or gap_status
            or "wiki_context_gap",
            limit=160,
        )
        reason_token = self._normalize_policy_id(reason) or "unknown"
        event_key = f"jue_wiki_context_gap:{clean_venue}:{run_seed}"
        workflow_provenance = self._manager_run_workflow_provenance_for_memory(run)
        return {
            "event_key": event_key,
            "policy_id": f"jue_wiki_context_gap.{clean_venue}.{reason_token}",
            "venue": clean_venue,
            "memory_scope": clean_venue,
            "transferability": "direct",
            "scope_evidence": [
                {
                    "memory_scope": clean_venue,
                    "transferability": "direct",
                    "source": "jue_wiki_context_gap",
                }
            ],
            "manager_run_id": _safe_int(run_id),
            "run_at": _clean_text(run.get("run_at"), limit=80),
            "status": gap_status or "active",
            "resolution_status": resolution_status or "unresolved",
            "reason": reason,
            "wiki_status": _clean_text(wiki_context.get("status"), limit=80),
            "available": wiki_context.get("available"),
            "blocker_count": _safe_int(
                blocker_tags.get("unresolved_jue_wiki_context_gap")
            ),
            **workflow_provenance,
        }

    def _jue_wiki_action_reference_gap_signal(
        self,
        venue: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        diagnostics = self._jue_wiki_diagnostics_from_manager_run(run)
        if not diagnostics:
            return {}
        status = _clean_text(
            diagnostics.get("jue_wiki_action_reference_status"),
            limit=80,
        )
        memory_status = _clean_text(
            diagnostics.get("jue_wiki_action_reference_memory_status"),
            limit=80,
        )
        resolution_status = _clean_text(
            diagnostics.get("jue_wiki_action_reference_memory_resolution_status"),
            limit=80,
        )
        blocker_tags = (
            diagnostics.get("blocker_tags")
            if isinstance(diagnostics.get("blocker_tags"), dict)
            else {}
        )
        blocker_count = _safe_int(blocker_tags.get("missing_jue_wiki_action_reference"))
        unresolved_memory_blocker_count = _safe_int(
            blocker_tags.get("unresolved_jue_wiki_action_reference_memory")
        )
        recovery_blocker_count = _safe_int(
            blocker_tags.get("unresolved_jue_wiki_action_reference_recovery")
        )
        recovery_status = _clean_text(
            diagnostics.get("jue_wiki_action_reference_recovery_status"),
            limit=80,
        )
        recovery_open_gap_count = _safe_int(
            diagnostics.get("jue_wiki_action_reference_recovery_open_gap_count")
        )
        recovery_latest_resolution_status = _clean_text(
            diagnostics.get(
                "jue_wiki_action_reference_recovery_latest_resolution_status"
            ),
            limit=80,
        )
        recovery_latest_status = _clean_text(
            diagnostics.get("jue_wiki_action_reference_recovery_latest_status"),
            limit=80,
        )
        memory_unresolved = (
            resolution_status == "unresolved"
            or unresolved_memory_blocker_count > 0
        )
        recovery_unresolved = (
            recovery_blocker_count > 0
            or recovery_open_gap_count > 0
            or recovery_status in {"open_gaps", "unresolved"}
            or recovery_latest_resolution_status == "unresolved"
        )
        resolved_memory_status = _jue_wiki_action_reference_resolution_status(
            resolution_status
        )
        memory_resolved = memory_status == "active" and bool(resolved_memory_status)
        if (
            status != "missing"
            and blocker_count <= 0
            and not memory_unresolved
            and not recovery_unresolved
            and not memory_resolved
        ):
            return {}
        clean_venue = _normalize_memory_scope(venue)
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(run.get("id") or run.get("run_id"), limit=80)
        run_seed = run_id or self._normalize_policy_id(
            f"{run.get('run_at')}:{status}:wiki_action_reference"
        )
        if not run_seed:
            return {}
        resolved_policy_ids = (
            _jue_wiki_action_reference_policy_ids_from_payload(run)
            if memory_resolved
            else []
        )
        status_token = (
            "resolved"
            if memory_resolved
            else
            "unresolved_memory"
            if memory_unresolved and status != "missing"
            else
            "unresolved_recovery"
            if recovery_unresolved and status != "missing"
            else self._normalize_policy_id(status or "missing") or "missing"
        )
        event_kind = (
            "jue_wiki_action_reference_resolution"
            if memory_resolved
            else "jue_wiki_action_reference_gap"
        )
        event_key = f"{event_kind}:{clean_venue}:{run_seed}"
        workflow_provenance = self._manager_run_workflow_provenance_for_memory(run)
        return {
            "event_key": event_key,
            "policy_id": (
                f"jue_wiki_action_reference_gap.{clean_venue}.{status_token}"
            ),
            "resolved_policy_ids": resolved_policy_ids,
            "is_resolution": memory_resolved,
            "venue": clean_venue,
            "memory_scope": clean_venue,
            "transferability": "direct",
            "scope_evidence": [
                {
                    "memory_scope": clean_venue,
                    "transferability": "direct",
                    "source": "jue_wiki_action_reference_gap",
                }
            ],
            "manager_run_id": _safe_int(run_id),
            "run_at": _clean_text(run.get("run_at"), limit=80),
            "status": status or "missing",
            "memory_status": memory_status,
            "resolution_status": resolution_status,
            "action_count": _safe_int(diagnostics.get("action_count")),
            "reference_count": _safe_int(
                diagnostics.get("jue_wiki_action_reference_count")
            ),
            "reference_ratio": _safe_float(
                diagnostics.get("jue_wiki_action_reference_ratio")
            ),
            "missing_actions": _compact_jue_wiki_missing_actions(
                diagnostics.get("jue_wiki_action_reference_missing_actions")
            ),
            "blocker_count": blocker_count,
            "unresolved_memory_blocker_count": unresolved_memory_blocker_count,
            "recovery_blocker_count": recovery_blocker_count,
            "recovery_status": recovery_status,
            "recovery_open_gap_count": recovery_open_gap_count,
            "recovery_latest_resolution_status": recovery_latest_resolution_status,
            "recovery_latest_status": recovery_latest_status,
            **workflow_provenance,
        }

    def _jue_wiki_usage_contract_gap_signal(
        self,
        venue: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        diagnostics = self._jue_wiki_diagnostics_from_manager_run(run)
        if not diagnostics:
            return {}
        status = _clean_text(
            diagnostics.get("jue_wiki_usage_contract_status"),
            limit=80,
        )
        blocker_tags = (
            diagnostics.get("blocker_tags")
            if isinstance(diagnostics.get("blocker_tags"), dict)
            else {}
        )
        missing_blocker_count = _safe_int(
            blocker_tags.get("missing_jue_wiki_usage_contract_resolution")
        )
        partial_blocker_count = _safe_int(
            blocker_tags.get("partial_jue_wiki_usage_contract_resolution")
        )
        if status not in {"missing", "partial"} and missing_blocker_count <= 0 and partial_blocker_count <= 0:
            return {}
        clean_venue = _normalize_memory_scope(venue)
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(run.get("id") or run.get("run_id"), limit=80)
        run_seed = run_id or self._normalize_policy_id(
            f"{run.get('run_at')}:{status}:wiki_usage_contract"
        )
        if not run_seed:
            return {}
        status_token = self._normalize_policy_id(status or "missing") or "missing"
        event_key = f"jue_wiki_usage_contract_gap:{clean_venue}:{run_seed}"
        workflow_provenance = self._manager_run_workflow_provenance_for_memory(run)
        return {
            "event_key": event_key,
            "policy_id": f"jue_wiki_usage_contract_gap.{clean_venue}.{status_token}",
            "venue": clean_venue,
            "memory_scope": clean_venue,
            "transferability": "direct",
            "scope_evidence": [
                {
                    "memory_scope": clean_venue,
                    "transferability": "direct",
                    "source": "jue_wiki_usage_contract_gap",
                }
            ],
            "manager_run_id": _safe_int(run_id),
            "run_at": _clean_text(run.get("run_at"), limit=80),
            "status": status or "missing",
            "action_count": _safe_int(diagnostics.get("action_count")),
            "resolution_count": _safe_int(
                diagnostics.get("jue_wiki_usage_contract_resolution_count")
            ),
            "resolution_ratio": _safe_float(
                diagnostics.get("jue_wiki_usage_contract_resolution_ratio")
            ),
            "missing_blocker_count": missing_blocker_count,
            "partial_blocker_count": partial_blocker_count,
            **workflow_provenance,
        }

    def _jue_wiki_selection_audit_from_diagnostics(
        self,
        run: dict[str, Any],
        *,
        application: dict[str, Any],
    ) -> dict[str, Any]:
        diagnostics = self._jue_wiki_diagnostics_from_manager_run(run)
        if not diagnostics:
            return {}
        guidance_status = _clean_text(
            diagnostics.get("jue_wiki_selection_guidance_status"),
            limit=80,
        )
        resolution_status = _clean_text(
            diagnostics.get("jue_wiki_selection_guidance_resolution_status"),
            limit=80,
        )
        blocker_tags = (
            diagnostics.get("blocker_tags")
            if isinstance(diagnostics.get("blocker_tags"), dict)
            else {}
        )
        active_guidance = guidance_status == "active" or _safe_int(
            blocker_tags.get("unresolved_jue_wiki_selection_guidance")
        )
        unresolved = resolution_status in {"", "unresolved"}
        if not active_guidance or not unresolved:
            return {}
        selected_page_ids = [
            _clean_text(page_id, limit=120)
            for page_id in list(application.get("selected_page_ids") or [])[:12]
            if _clean_text(page_id, limit=120)
        ]
        return {
            "selected_page_count": len(selected_page_ids),
            "reason_counts": {"diagnostics:selection_guidance": 1},
            "penalty_counts": {
                "freshness:stale": 2,
                "selection_guidance:unresolved": 1,
            },
            "top_pages": [
                {
                    "page_id": page_id,
                    "rank": index + 1,
                    "selection_reasons": ["diagnostics:selection_guidance"],
                }
                for index, page_id in enumerate(selected_page_ids[:4])
            ],
        }

    @staticmethod
    def _jue_wiki_application_from_manager_run(
        run: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = run.get("prompt") if isinstance(run.get("prompt"), dict) else {}
        decision_context = (
            run.get("decision_context")
            if isinstance(run.get("decision_context"), dict)
            else {}
        )
        prompt_compact = (
            prompt.get("compact_manager_context")
            if isinstance(prompt.get("compact_manager_context"), dict)
            else {}
        )
        run_compact = (
            run.get("compact_manager_context")
            if isinstance(run.get("compact_manager_context"), dict)
            else {}
        )
        for candidate in (
            prompt.get("jue_wiki_application"),
            prompt_compact.get("jue_wiki_application"),
            run.get("jue_wiki_application"),
            run_compact.get("jue_wiki_application"),
            decision_context.get("jue_wiki_application"),
        ):
            if isinstance(candidate, dict) and candidate:
                return candidate
        return {}

    @staticmethod
    def _jue_wiki_diagnostics_from_manager_run(
        run: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = run.get("prompt") if isinstance(run.get("prompt"), dict) else {}
        decision_context = (
            run.get("decision_context")
            if isinstance(run.get("decision_context"), dict)
            else {}
        )
        prompt_compact = (
            prompt.get("compact_manager_context")
            if isinstance(prompt.get("compact_manager_context"), dict)
            else {}
        )
        run_compact = (
            run.get("compact_manager_context")
            if isinstance(run.get("compact_manager_context"), dict)
            else {}
        )
        for candidate in (
            prompt.get("diagnostics"),
            prompt_compact.get("diagnostics"),
            run.get("diagnostics"),
            run_compact.get("diagnostics"),
            decision_context.get("diagnostics"),
        ):
            if isinstance(candidate, dict) and candidate:
                return candidate
        return {}

    @staticmethod
    def _jue_wiki_context_from_manager_run(
        run: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = run.get("prompt") if isinstance(run.get("prompt"), dict) else {}
        decision_context = (
            run.get("decision_context")
            if isinstance(run.get("decision_context"), dict)
            else {}
        )
        prompt_compact = (
            prompt.get("compact_manager_context")
            if isinstance(prompt.get("compact_manager_context"), dict)
            else {}
        )
        run_compact = (
            run.get("compact_manager_context")
            if isinstance(run.get("compact_manager_context"), dict)
            else {}
        )
        memory = prompt.get("investment_memory")
        memory = memory if isinstance(memory, dict) else {}
        binance_memory = prompt.get("memory")
        binance_memory = binance_memory if isinstance(binance_memory, dict) else {}
        for candidate in (
            prompt.get("jue_wiki"),
            memory.get("jue_wiki"),
            binance_memory.get("jue_wiki"),
            prompt_compact.get("jue_wiki"),
            run.get("jue_wiki"),
            run_compact.get("jue_wiki"),
            decision_context.get("jue_wiki"),
        ):
            if isinstance(candidate, dict) and candidate:
                return candidate
        return {}

    @staticmethod
    def _manager_run_workflow_provenance_for_memory(
        run: dict[str, Any],
    ) -> dict[str, Any]:
        decision_context = (
            run.get("decision_context")
            if isinstance(run.get("decision_context"), dict)
            else {}
        )
        workflow_id = _clean_text(
            run.get("workflow_id") or decision_context.get("workflow_id"),
            limit=120,
        )
        workflow_version = _safe_int(
            run.get("workflow_version") or decision_context.get("workflow_version")
        )
        skill_ids = _compact_text_list(
            run.get("skill_ids") or decision_context.get("skill_ids"),
            limit=8,
            item_limit=120,
        )
        contract_ids = _compact_text_list(
            run.get("contract_ids") or decision_context.get("contract_ids"),
            limit=12,
            item_limit=160,
        )
        return {
            key: value
            for key, value in {
                "workflow_id": workflow_id,
                "workflow_version": workflow_version if workflow_version > 0 else None,
                "skill_ids": skill_ids,
                "contract_ids": contract_ids,
            }.items()
            if value not in (None, "", [], {})
        }

    @staticmethod
    def _merged_workflow_provenance_lists(
        previous: dict[str, Any],
        signal: dict[str, Any],
    ) -> dict[str, list[str]]:
        workflow_ids = [
            _clean_text(value, limit=120)
            for value in list(previous.get("workflow_ids") or [])
            if _clean_text(value, limit=120)
        ]
        workflow_id = _clean_text(signal.get("workflow_id"), limit=120)
        if workflow_id and workflow_id not in workflow_ids:
            workflow_ids.append(workflow_id)
        skill_ids = [
            _clean_text(value, limit=120)
            for value in list(previous.get("skill_ids") or [])
            if _clean_text(value, limit=120)
        ]
        for value in list(signal.get("skill_ids") or []):
            clean_value = _clean_text(value, limit=120)
            if clean_value and clean_value not in skill_ids:
                skill_ids.append(clean_value)
        contract_ids = [
            _clean_text(value, limit=160)
            for value in list(previous.get("contract_ids") or [])
            if _clean_text(value, limit=160)
        ]
        for value in list(signal.get("contract_ids") or []):
            clean_value = _clean_text(value, limit=160)
            if clean_value and clean_value not in contract_ids:
                contract_ids.append(clean_value)
        return {
            "workflow_ids": _compact_text_list(
                workflow_ids,
                limit=8,
                item_limit=120,
            ),
            "skill_ids": _compact_text_list(skill_ids, limit=12, item_limit=120),
            "contract_ids": _compact_text_list(
                contract_ids,
                limit=16,
                item_limit=160,
            ),
        }

    @staticmethod
    def _compact_count_map(value: Any, *, limit: int) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        rows: list[tuple[str, int]] = []
        for key, raw_count in value.items():
            clean_key = _clean_text(key, limit=120)
            if clean_key:
                rows.append((clean_key, _safe_int(raw_count)))
        rows.sort(key=lambda item: (-item[1], item[0]))
        return {key: count for key, count in rows[: max(int(limit), 1)]}

    @staticmethod
    def _primary_wiki_selection_reason(reason_counts: dict[str, int]) -> str:
        if not reason_counts:
            return ""
        for key in reason_counts:
            if "manager_contract_recovery" in key:
                return key
        for key in reason_counts:
            if not key.startswith("scope_match:"):
                return key
        return next(iter(reason_counts), "")

    def _manager_contract_error_signal(
        self,
        venue: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        response = run.get("response") if isinstance(run.get("response"), dict) else {}
        latest = (
            run.get("latest_decision_input")
            if isinstance(run.get("latest_decision_input"), dict)
            else response.get("latest_input_summary")
            if isinstance(response.get("latest_input_summary"), dict)
            else {}
        )
        memory_contract = (
            latest.get("memory_contract")
            if isinstance(latest.get("memory_contract"), dict)
            else {}
        )
        diagnostic_rows = self._diagnostic_memory_contract_rows(
            run,
            status="unresolved",
        )
        error = _clean_text(
            response.get("contract_error")
            or run.get("error_message")
            or memory_contract.get("error"),
            limit=180,
        )
        if not error and diagnostic_rows:
            error = _clean_text(
                self._first_memory_contract_row_value(diagnostic_rows, "errors")
                or "diagnostic_memory_contract_unresolved",
                limit=180,
            )
        if not error:
            return {}
        if (
            not diagnostic_rows
            and str(run.get("mode") or "").strip() != "contract_error"
            and not error.endswith("_missing_from_model")
        ):
            return {}
        clean_venue = _normalize_memory_scope(venue)
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(run.get("id") or run.get("run_id"), limit=80)
        run_seed = run_id or self._normalize_policy_id(
            f"{run.get('run_at')}:{error}"
        )
        if not run_seed:
            return {}
        error_token = self._normalize_policy_id(error) or "unknown"
        event_key = f"manager_contract_error:{clean_venue}:{run_seed}"
        policy_id = f"manager_contract_error.{clean_venue}.{error_token}"
        return {
            "event_key": event_key,
            "policy_id": policy_id,
            "venue": clean_venue,
            "manager_run_id": _safe_int(run_id),
            "run_at": _clean_text(run.get("run_at"), limit=80),
            "error": error,
            "contract": _clean_text(
                memory_contract.get("contract")
                or self._first_memory_contract_row_value(
                    diagnostic_rows,
                    "contracts",
                ),
                limit=160,
            ),
            "impacted_symbols": [
                _clean_text(symbol, limit=40)
                for symbol in list(
                    memory_contract.get("impacted_symbols")
                    or self._memory_contract_row_symbols(diagnostic_rows)
                )[:12]
                if _clean_text(symbol, limit=40)
            ],
            "memory_packet_count": (
                _safe_int(memory_contract.get("memory_packet_count"))
                or len(diagnostic_rows)
            ),
            "resolution_status": _clean_text(
                memory_contract.get("resolution_status")
                or ("unresolved" if diagnostic_rows else ""),
                limit=80,
            ),
            "memory_contract_rows": diagnostic_rows[:12],
        }

    def _manager_contract_resolution_signal(
        self,
        venue: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        latest = (
            run.get("latest_decision_input")
            if isinstance(run.get("latest_decision_input"), dict)
            else {}
        )
        response = run.get("response") if isinstance(run.get("response"), dict) else {}
        if not latest and isinstance(response.get("latest_input_summary"), dict):
            latest = response.get("latest_input_summary") or {}
        memory_contract = (
            latest.get("memory_contract")
            if isinstance(latest.get("memory_contract"), dict)
            else {}
        )
        diagnostic_rows = self._diagnostic_memory_contract_rows(
            run,
            status="resolved",
        )
        if not memory_contract and not diagnostic_rows:
            return {}
        resolution_status = _clean_text(
            memory_contract.get("resolution_status"),
            limit=80,
        )
        resolved_candidates = [
            row
            for row in list(memory_contract.get("resolved_candidates") or [])[:8]
            if isinstance(row, dict)
            and _clean_text(row.get("memory_contract_resolution"), limit=320)
        ]
        if diagnostic_rows and not resolved_candidates:
            resolved_candidates = [
                {
                    "symbol": _clean_text(row.get("symbol"), limit=40),
                    "memory_contract": self._first_memory_contract_row_value(
                        [row],
                        "contracts",
                    ),
                    "memory_contract_error": self._first_memory_contract_row_value(
                        [row],
                        "errors",
                    ),
                    "memory_contract_resolution": ", ".join(
                        [
                            _clean_text(mode, limit=80)
                            for mode in list(row.get("resolution_modes") or [])[:4]
                            if _clean_text(mode, limit=80)
                        ]
                    ),
                }
                for row in diagnostic_rows[:8]
                if isinstance(row, dict)
            ]
        if resolution_status != "resolved" and not resolved_candidates:
            return {}
        clean_venue = _normalize_memory_scope(venue)
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(run.get("id") or run.get("run_id"), limit=80)
        run_seed = run_id or self._normalize_policy_id(
            f"{run.get('run_at')}:{resolution_status}"
        )
        if not run_seed:
            return {}
        contract_errors = [
            _clean_text(error, limit=180)
            for error in list(memory_contract.get("memory_contract_errors") or [])[:8]
            if _clean_text(error, limit=180)
        ]
        if not contract_errors:
            for row in resolved_candidates:
                error = _clean_text(row.get("memory_contract_error"), limit=180)
                if error and error not in contract_errors:
                    contract_errors.append(error)
        if not contract_errors:
            for row in diagnostic_rows:
                error = self._first_memory_contract_row_value([row], "errors")
                if error and error not in contract_errors:
                    contract_errors.append(error)
        contract = _clean_text(memory_contract.get("contract"), limit=160)
        if not contract:
            for row in resolved_candidates:
                contract = _clean_text(row.get("memory_contract"), limit=160)
                if contract:
                    break
        error_token = self._normalize_policy_id(
            contract_errors[0] if contract_errors else contract or "resolved"
        )
        event_key = f"manager_contract_resolution:{clean_venue}:{run_seed}"
        policy_id = f"manager_contract_resolution.{clean_venue}.{error_token}"
        impacted_symbols = [
            _clean_text(symbol, limit=40)
            for symbol in list(memory_contract.get("impacted_symbols") or [])[:12]
            if _clean_text(symbol, limit=40)
        ]
        if not impacted_symbols:
            for row in resolved_candidates:
                symbol = _clean_text(row.get("symbol"), limit=40)
                if symbol and symbol not in impacted_symbols:
                    impacted_symbols.append(symbol)
        if not impacted_symbols:
            impacted_symbols = self._memory_contract_row_symbols(diagnostic_rows)
        latest_resolution = ""
        if resolved_candidates:
            latest_resolution = _clean_text(
                resolved_candidates[0].get("memory_contract_resolution"),
                limit=320,
            )
        return {
            "event_key": event_key,
            "policy_id": policy_id,
            "venue": clean_venue,
            "manager_run_id": _safe_int(run_id),
            "run_at": _clean_text(run.get("run_at"), limit=80),
            "contract": contract,
            "memory_contract_errors": contract_errors[:8],
            "impacted_symbols": impacted_symbols[:12],
            "memory_packet_count": (
                _safe_int(memory_contract.get("memory_packet_count"))
                or len(diagnostic_rows)
            ),
            "resolution_status": "resolved",
            "latest_symbol": impacted_symbols[0] if impacted_symbols else "",
            "latest_resolution": latest_resolution,
            "resolved_candidates": resolved_candidates,
            "memory_contract_rows": diagnostic_rows[:12],
        }

    def _diagnostic_memory_contract_rows(
        self,
        run: dict[str, Any],
        *,
        status: str,
    ) -> list[dict[str, Any]]:
        diagnostics = self._jue_wiki_diagnostics_from_manager_run(run)
        rows = (
            diagnostics.get("memory_contract_rows")
            if isinstance(diagnostics.get("memory_contract_rows"), list)
            else []
        )
        clean_status = _clean_text(status, limit=40)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_status = _clean_text(row.get("status"), limit=40)
            if clean_status and row_status != clean_status:
                continue
            symbol = _clean_text(row.get("symbol"), limit=40)
            if not symbol:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "status": row_status,
                    "contracts": _compact_text_list(
                        row.get("contracts"),
                        limit=4,
                        item_limit=160,
                    ),
                    "errors": _compact_text_list(
                        row.get("errors"),
                        limit=4,
                        item_limit=180,
                    ),
                    "resolution_modes": _compact_text_list(
                        row.get("resolution_modes"),
                        limit=4,
                        item_limit=80,
                    ),
                }
            )
        return out[:12]

    @staticmethod
    def _first_memory_contract_row_value(
        rows: list[dict[str, Any]],
        key: str,
    ) -> str:
        for row in rows:
            if not isinstance(row, dict):
                continue
            for value in list(row.get(key) or []):
                clean = _clean_text(value, limit=180)
                if clean:
                    return clean
        return ""

    @staticmethod
    def _memory_contract_row_symbols(rows: list[dict[str, Any]]) -> list[str]:
        symbols: list[str] = []
        for row in rows:
            symbol = _clean_text(row.get("symbol"), limit=40)
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return symbols[:12]

    def _upsert_manager_contract_error_scorecard(
        self,
        signal: dict[str, Any],
    ) -> None:
        policy_id = _clean_text(signal.get("policy_id"), limit=180)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.35 + sample_count * 0.10)
        action = "observe"
        status = "candidate"
        if sample_count >= 3 and confidence >= 0.65:
            action = "caution"
            status = "active_caution"
        venue = _clean_text(signal.get("venue"), limit=40)
        error = _clean_text(signal.get("error"), limit=180)
        symbols = [
            _clean_text(symbol, limit=40)
            for symbol in list(signal.get("impacted_symbols") or [])[:6]
            if _clean_text(symbol, limit=40)
        ]
        summary = (
            f"{venue.upper()} 매니저가 메모리/위키 계약을 충족하지 못한 표본 "
            f"{sample_count}건. 최근 오류: {error}, 영향 심볼: "
            f"{', '.join(symbols) if symbols else '-'}."
        )
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "venue": venue,
                "manager_run_id": signal.get("manager_run_id"),
                "contract": signal.get("contract"),
                "error": error,
                "impacted_symbols": symbols,
                "memory_packet_count": signal.get("memory_packet_count"),
                "memory_contract_rows": _compact_memory_contract_rows(
                    signal.get("memory_contract_rows"),
                    limit=6,
                ),
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": action,
                "status": status,
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "manager_contract_error",
                "latest_symbol": symbols[0] if symbols else "",
                "latest_reason": error,
                "latest_error": error,
                "contract": _clean_text(signal.get("contract"), limit=160),
                "impacted_symbols": symbols,
                "memory_contract_rows": _compact_memory_contract_rows(
                    signal.get("memory_contract_rows"),
                    limit=6,
                ),
                "memory_packet_count": _safe_int(signal.get("memory_packet_count")),
                "resolution_status": _clean_text(
                    signal.get("resolution_status"),
                    limit=80,
                ),
            }
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def _upsert_manager_contract_resolution_scorecard(
        self,
        signal: dict[str, Any],
    ) -> None:
        policy_id = _clean_text(signal.get("policy_id"), limit=180)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.40 + sample_count * 0.10)
        venue = _clean_text(signal.get("venue"), limit=40)
        contract = _clean_text(signal.get("contract"), limit=160)
        latest_resolution = _clean_text(signal.get("latest_resolution"), limit=320)
        symbols = [
            _clean_text(symbol, limit=40)
            for symbol in list(signal.get("impacted_symbols") or [])[:6]
            if _clean_text(symbol, limit=40)
        ]
        contract_errors = [
            _clean_text(error, limit=180)
            for error in list(signal.get("memory_contract_errors") or [])[:8]
            if _clean_text(error, limit=180)
        ]
        summary = (
            f"{venue.upper()} 매니저가 메모리/위키 계약 복구를 기록한 표본 "
            f"{sample_count}건. 계약: {contract or '-'}, 영향 심볼: "
            f"{', '.join(symbols) if symbols else '-'}."
        )
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "venue": venue,
                "manager_run_id": signal.get("manager_run_id"),
                "contract": contract,
                "memory_contract_errors": contract_errors,
                "impacted_symbols": symbols,
                "latest_resolution": latest_resolution,
                "resolved_candidates": signal.get("resolved_candidates") or [],
                "memory_contract_rows": _compact_memory_contract_rows(
                    signal.get("memory_contract_rows"),
                    limit=6,
                ),
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": "observe",
                "status": "active_observation",
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "manager_contract_resolution",
                "latest_symbol": symbols[0] if symbols else "",
                "latest_reason": latest_resolution,
                "latest_resolution": latest_resolution,
                "contract": contract,
                "memory_contract_errors": contract_errors,
                "impacted_symbols": symbols,
                "memory_contract_rows": _compact_memory_contract_rows(
                    signal.get("memory_contract_rows"),
                    limit=6,
                ),
                "memory_packet_count": _safe_int(signal.get("memory_packet_count")),
                "resolution_status": "resolved",
                "resolved_candidates": signal.get("resolved_candidates") or [],
            }
        )
        self._mark_matching_manager_contract_errors_resolved(
            signal=signal,
            resolution_policy_id=policy_id,
            confidence=confidence,
            latest_resolution=latest_resolution,
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def _mark_matching_manager_contract_errors_resolved(
        self,
        *,
        signal: dict[str, Any],
        resolution_policy_id: str,
        confidence: float,
        latest_resolution: str,
    ) -> None:
        venue = _clean_text(signal.get("venue"), limit=40) or "core"
        errors = [
            _clean_text(error, limit=180)
            for error in list(signal.get("memory_contract_errors") or [])[:8]
            if _clean_text(error, limit=180)
        ]
        if not errors:
            contract = _clean_text(signal.get("contract"), limit=160)
            if contract:
                errors.append(contract)
        for error in errors:
            error_token = self._normalize_policy_id(error) or "resolved"
            error_policy_id = f"manager_contract_error.{venue}.{error_token}"
            previous = self.repository.get_policy_scorecard(error_policy_id)
            if not previous:
                continue
            previous_reason = _clean_text(previous.get("reason"), limit=900)
            resolution_note = (
                f"복구 기록: {latest_resolution or 'manager memory contract resolved'}"
            )
            reason = (
                f"{previous_reason} {resolution_note}"
                if previous_reason
                else resolution_note
            )
            self.repository.upsert_policy_scorecard(
                {
                    "policy_id": error_policy_id,
                    "action": "observe",
                    "status": "resolved",
                    "sample_count": _safe_int(previous.get("sample_count")),
                    "win_rate": _safe_float(previous.get("win_rate")),
                    "avg_pnl_pct": _safe_float(previous.get("avg_pnl_pct")),
                    "expectancy_pct": _safe_float(previous.get("expectancy_pct")),
                    "rule_follow_rate": _safe_float(
                        previous.get("rule_follow_rate"),
                    ),
                    "confidence": max(
                        _safe_float(previous.get("confidence")),
                        _safe_float(confidence),
                    ),
                    "reason": _clean_text(reason, limit=1200),
                    "source": previous.get("source") or "manager_contract_error",
                    "venue": venue,
                    "memory_scope": previous.get("memory_scope") or venue,
                    "scope": previous.get("scope") or venue,
                    "transferability": previous.get("transferability") or "direct",
                    "latest_symbol": previous.get("latest_symbol")
                    or signal.get("latest_symbol"),
                    "latest_reason": previous.get("latest_reason") or error,
                    "latest_error": previous.get("latest_error") or error,
                    "contract": previous.get("contract") or signal.get("contract"),
                    "impacted_symbols": _compact_text_list(
                        previous.get("impacted_symbols")
                        or signal.get("impacted_symbols"),
                        limit=8,
                        item_limit=40,
                    ),
                    "memory_contract_rows": _compact_memory_contract_rows(
                        signal.get("memory_contract_rows")
                        or previous.get("memory_contract_rows"),
                        limit=6,
                    ),
                    "memory_packet_count": _safe_int(
                        signal.get("memory_packet_count")
                        or previous.get("memory_packet_count"),
                    ),
                    "resolution_status": "resolved",
                    "resolution_policy_id": resolution_policy_id,
                    "latest_resolution": latest_resolution,
                    "resolved_at": utc_now_iso(),
                }
            )

    def _upsert_jue_wiki_selection_audit_scorecard(
        self,
        signal: dict[str, Any],
    ) -> None:
        policy_id = _clean_text(signal.get("policy_id"), limit=180)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.45 + sample_count * 0.08)
        workflow_provenance = self._merged_workflow_provenance_lists(
            previous,
            signal,
        )
        venue = _clean_text(signal.get("venue"), limit=40)
        primary_reason = _clean_text(signal.get("primary_reason"), limit=160)
        selected_page_ids = [
            _clean_text(page_id, limit=120)
            for page_id in list(signal.get("selected_page_ids") or [])[:8]
            if _clean_text(page_id, limit=120)
        ]
        penalty_counts = (
            signal.get("penalty_counts")
            if isinstance(signal.get("penalty_counts"), dict)
            else {}
        )
        stale_count = _safe_int(penalty_counts.get("freshness:stale"))
        action = "observe"
        status = "active_observation"
        if stale_count >= 2:
            action = "caution"
            status = "active_caution"
        summary = (
            f"{venue.upper()} 위키 선택 감사 표본 {sample_count}건. "
            f"주요 선택 이유: {primary_reason or '-'}, 선택 페이지: "
            f"{', '.join(selected_page_ids[:4]) if selected_page_ids else '-'}."
        )
        if stale_count:
            summary += f" stale 패널티 {stale_count}건은 위키 최신성 점검 신호다."
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "venue": venue,
                "memory_scope": venue,
                "transferability": "direct",
                "manager_run_id": signal.get("manager_run_id"),
                "run_at": signal.get("run_at"),
                "selection_run_id": signal.get("selection_run_id"),
                "primary_reason": primary_reason,
                "selected_page_count": signal.get("selected_page_count"),
                "selected_page_ids": selected_page_ids,
                "reason_counts": signal.get("reason_counts") or {},
                "penalty_counts": penalty_counts,
                "top_pages": signal.get("top_pages") or [],
                **workflow_provenance,
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": action,
                "status": status,
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "jue_wiki_selection_audit",
                "memory_scope": venue,
                "scope": venue,
                "transferability": "direct",
                "scope_evidence": [
                    {
                        "memory_scope": venue,
                        "transferability": "direct",
                        "source": "jue_wiki_selection_audit",
                    }
                ],
                "latest_reason": primary_reason,
                "latest_symbol": "",
                "selected_page_ids": selected_page_ids,
                "selected_page_count": signal.get("selected_page_count"),
                "reason_counts": signal.get("reason_counts") or {},
                "penalty_counts": penalty_counts,
                "top_pages": signal.get("top_pages") or [],
                "selection_run_id": signal.get("selection_run_id"),
                "manager_run_id": signal.get("manager_run_id"),
                **workflow_provenance,
            }
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def _upsert_jue_wiki_context_gap_scorecard(
        self,
        signal: dict[str, Any],
    ) -> None:
        policy_id = _clean_text(signal.get("policy_id"), limit=180)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.55 + sample_count * 0.08)
        workflow_provenance = self._merged_workflow_provenance_lists(
            previous,
            signal,
        )
        venue = _clean_text(signal.get("venue"), limit=40)
        reason = _clean_text(signal.get("reason"), limit=160)
        wiki_status = _clean_text(signal.get("wiki_status"), limit=80)
        summary = (
            f"{venue.upper()} 매니저가 위키 컨텍스트 공백을 해결하지 못한 표본 "
            f"{sample_count}건. 최근 원인: {reason or '-'}, "
            f"위키 상태: {wiki_status or '-'}."
        )
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "venue": venue,
                "memory_scope": venue,
                "transferability": "direct",
                "manager_run_id": signal.get("manager_run_id"),
                "run_at": signal.get("run_at"),
                "reason": reason,
                "wiki_status": wiki_status,
                "available": signal.get("available"),
                "blocker_count": signal.get("blocker_count"),
                **workflow_provenance,
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": "caution",
                "status": "active_caution",
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "jue_wiki_context_gap",
                "memory_scope": venue,
                "scope": venue,
                "transferability": "direct",
                "scope_evidence": [
                    {
                        "memory_scope": venue,
                        "transferability": "direct",
                        "source": "jue_wiki_context_gap",
                    }
                ],
                "latest_reason": reason,
                "latest_error": reason,
                "latest_symbol": "",
                "wiki_status": wiki_status,
                "available": signal.get("available"),
                "blocker_count": signal.get("blocker_count"),
                "resolution_status": _clean_text(
                    signal.get("resolution_status"),
                    limit=80,
                ),
                **workflow_provenance,
            }
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def _upsert_jue_wiki_action_reference_gap_scorecard(
        self,
        signal: dict[str, Any],
    ) -> None:
        if signal.get("is_resolution"):
            self._resolve_jue_wiki_action_reference_gap_scorecards(signal)
            return
        policy_id = _clean_text(signal.get("policy_id"), limit=180)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.52 + sample_count * 0.08)
        workflow_provenance = self._merged_workflow_provenance_lists(
            previous,
            signal,
        )
        venue = _clean_text(signal.get("venue"), limit=40)
        status = _clean_text(signal.get("status"), limit=80) or "missing"
        memory_status = _clean_text(signal.get("memory_status"), limit=80)
        resolution_status = _clean_text(signal.get("resolution_status"), limit=80)
        action_count = _safe_int(signal.get("action_count"))
        reference_count = _safe_int(signal.get("reference_count"))
        reference_ratio = _safe_float(signal.get("reference_ratio"))
        unresolved_memory_blocker_count = _safe_int(
            signal.get("unresolved_memory_blocker_count")
        )
        recovery_blocker_count = _safe_int(signal.get("recovery_blocker_count"))
        recovery_status = _clean_text(signal.get("recovery_status"), limit=80)
        recovery_open_gap_count = _safe_int(signal.get("recovery_open_gap_count"))
        recovery_latest_resolution_status = _clean_text(
            signal.get("recovery_latest_resolution_status"),
            limit=80,
        )
        recovery_latest_status = _clean_text(
            signal.get("recovery_latest_status"),
            limit=80,
        )
        missing_actions = _compact_jue_wiki_missing_actions(
            signal.get("missing_actions")
        )
        summary = (
            f"{venue.upper()} 매니저가 선택된 위키를 액션 근거로 남기지 않은 "
            f"표본 {sample_count}건. 최근 액션 {action_count}개 중 "
            f"위키 참조 {reference_count}개, 참조율 {reference_ratio:.1%}."
        )
        if resolution_status == "unresolved":
            summary += " 이전 위키 근거 누락 메모리가 아직 해결되지 않았음."
        if recovery_blocker_count > 0 or recovery_open_gap_count > 0:
            summary += (
                f" 위키 action-reference 회복 공백 {recovery_open_gap_count or recovery_blocker_count}건 "
                "추적 필요."
            )
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "venue": venue,
                "memory_scope": venue,
                "transferability": "direct",
                "manager_run_id": signal.get("manager_run_id"),
                "run_at": signal.get("run_at"),
                "status": status,
                "action_count": action_count,
                "reference_count": reference_count,
                "reference_ratio": reference_ratio,
                "missing_actions": missing_actions,
                "blocker_count": signal.get("blocker_count"),
                "memory_status": memory_status,
                "resolution_status": resolution_status,
                "unresolved_memory_blocker_count": unresolved_memory_blocker_count,
                "recovery_blocker_count": recovery_blocker_count,
                "recovery_status": recovery_status,
                "recovery_open_gap_count": recovery_open_gap_count,
                "recovery_latest_resolution_status": (
                    recovery_latest_resolution_status
                ),
                "recovery_latest_status": recovery_latest_status,
                **workflow_provenance,
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": "caution",
                "status": "active_caution",
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "jue_wiki_action_reference_gap",
                "memory_scope": venue,
                "scope": venue,
                "transferability": "direct",
                "scope_evidence": [
                    {
                        "memory_scope": venue,
                        "transferability": "direct",
                        "source": "jue_wiki_action_reference_gap",
                    }
                ],
                "latest_status": status,
                "memory_status": memory_status,
                "resolution_status": resolution_status,
                "latest_reason": status,
                "latest_symbol": "",
                "action_count": action_count,
                "reference_count": reference_count,
                "reference_ratio": reference_ratio,
                "missing_actions": missing_actions,
                "blocker_count": signal.get("blocker_count"),
                "unresolved_memory_blocker_count": unresolved_memory_blocker_count,
                "recovery_blocker_count": recovery_blocker_count,
                "recovery_status": recovery_status,
                "recovery_open_gap_count": recovery_open_gap_count,
                "recovery_latest_resolution_status": (
                    recovery_latest_resolution_status
                ),
                "recovery_latest_status": recovery_latest_status,
                **workflow_provenance,
            }
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def _resolve_jue_wiki_action_reference_gap_scorecards(
        self,
        signal: dict[str, Any],
    ) -> None:
        venue = _clean_text(signal.get("venue"), limit=40)
        target_policy_ids = [
            _clean_text(policy_id, limit=180)
            for policy_id in list(signal.get("resolved_policy_ids") or [])[:12]
            if _clean_text(policy_id, limit=180)
        ]
        fallback_policy_id = _clean_text(signal.get("policy_id"), limit=180)
        target_policy_ids = [
            policy_id
            for policy_id in target_policy_ids
            if policy_id.startswith("jue_wiki_action_reference_gap.")
        ]
        if not target_policy_ids:
            target_policy_ids = (
                self._active_jue_wiki_action_reference_gap_policy_ids_for_venue(
                    venue,
                )
            )
        if (
            not target_policy_ids
            and fallback_policy_id.startswith("jue_wiki_action_reference_gap.")
        ):
            target_policy_ids.append(fallback_policy_id)
        if not target_policy_ids:
            return
        action_count = _safe_int(signal.get("action_count"))
        reference_count = _safe_int(signal.get("reference_count"))
        reference_ratio = _safe_float(signal.get("reference_ratio"))
        resolution_status = _clean_text(signal.get("resolution_status"), limit=80)
        memory_status = _clean_text(signal.get("memory_status"), limit=80)
        recovery_resolution_status = _clean_text(
            signal.get("recovery_latest_resolution_status"),
            limit=80,
        )
        recovery_latest_status = _clean_text(
            signal.get("recovery_latest_status"),
            limit=80,
        )
        recovery_status = _clean_text(signal.get("recovery_status"), limit=80)
        recovery_open_gap_count = _safe_int(signal.get("recovery_open_gap_count"))
        for policy_id in target_policy_ids:
            previous = self.repository.get_policy_scorecard(policy_id) or {}
            sample_count = max(_safe_int(previous.get("sample_count")), 0)
            resolution_count = max(
                _safe_int(previous.get("resolution_count")),
                0,
            ) + 1
            confidence = max(_safe_float(previous.get("confidence")), 0.55)
            workflow_provenance = self._merged_workflow_provenance_lists(
                previous,
                signal,
            )
            summary = (
                f"{venue.upper()} 매니저가 위키 action-reference 메모리 "
                f"`{policy_id}`를 해결한 표본 {resolution_count}건. "
                f"최근 액션 {action_count}개 중 위키 참조 {reference_count}개, "
                f"참조율 {reference_ratio:.1%}."
            )
            if resolution_status == "hold_trigger":
                summary += " 신규 블록 없이 관망 판단의 회복 근거로 해결됨."
            elif resolution_status == "action_metadata":
                summary += " 액션 메타데이터의 회복 근거로 해결됨."
            evidence = [
                {
                    "event_key": signal.get("event_key"),
                    "venue": venue,
                    "memory_scope": venue,
                    "transferability": "direct",
                    "manager_run_id": signal.get("manager_run_id"),
                    "run_at": signal.get("run_at"),
                    "status": signal.get("status"),
                    "memory_status": memory_status,
                    "resolution_status": resolution_status,
                    "recovery_status": recovery_status,
                    "recovery_resolution_status": recovery_resolution_status,
                    "recovery_latest_status": recovery_latest_status,
                    "recovery_open_gap_count": recovery_open_gap_count,
                    "action_count": action_count,
                    "reference_count": reference_count,
                    "reference_ratio": reference_ratio,
                    **workflow_provenance,
                }
            ]
            self.repository.upsert_policy_scorecard(
                {
                    "policy_id": policy_id,
                    "action": "observe",
                    "status": "resolved",
                    "sample_count": sample_count,
                    "win_rate": _safe_float(previous.get("win_rate")),
                    "avg_pnl_pct": _safe_float(previous.get("avg_pnl_pct")),
                    "expectancy_pct": _safe_float(previous.get("expectancy_pct")),
                    "rule_follow_rate": 1.0,
                    "confidence": confidence,
                    "reason": summary,
                    "source": "jue_wiki_action_reference_gap",
                    "memory_scope": venue,
                    "scope": venue,
                    "transferability": "direct",
                    "scope_evidence": [
                        {
                            "memory_scope": venue,
                            "transferability": "direct",
                            "source": "jue_wiki_action_reference_gap",
                        }
                    ],
                    "latest_status": signal.get("status"),
                    "memory_status": memory_status,
                    "resolution_status": resolution_status,
                    "latest_reason": resolution_status,
                    "latest_symbol": "",
                    "action_count": action_count,
                    "reference_count": reference_count,
                    "reference_ratio": reference_ratio,
                    "blocker_count": 0,
                    "unresolved_memory_blocker_count": 0,
                    "recovery_blocker_count": 0,
                    "recovery_status": recovery_status,
                    "recovery_open_gap_count": recovery_open_gap_count,
                    "recovery_resolution_status": recovery_resolution_status,
                    "recovery_latest_resolution_status": (
                        recovery_resolution_status
                    ),
                    "recovery_latest_status": recovery_latest_status,
                    "resolution_count": resolution_count,
                    **workflow_provenance,
                }
            )
            self.repository.save_insight(
                memory_type="policy_signal",
                key=policy_id,
                status="resolved",
                confidence=confidence,
                summary_md=summary,
                evidence=evidence,
                source_run_id=None,
            )

    def _upsert_jue_wiki_usage_contract_gap_scorecard(
        self,
        signal: dict[str, Any],
    ) -> None:
        policy_id = _clean_text(signal.get("policy_id"), limit=180)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.52 + sample_count * 0.08)
        venue = _clean_text(signal.get("venue"), limit=40)
        status = _clean_text(signal.get("status"), limit=80) or "missing"
        action_count = _safe_int(signal.get("action_count"))
        resolution_count = _safe_int(signal.get("resolution_count"))
        resolution_ratio = _safe_float(signal.get("resolution_ratio"))
        missing_blocker_count = _safe_int(signal.get("missing_blocker_count"))
        partial_blocker_count = _safe_int(signal.get("partial_blocker_count"))
        workflow_ids = [
            _clean_text(value, limit=120)
            for value in list(previous.get("workflow_ids") or [])
            if _clean_text(value, limit=120)
        ]
        workflow_id = _clean_text(signal.get("workflow_id"), limit=120)
        if workflow_id and workflow_id not in workflow_ids:
            workflow_ids.append(workflow_id)
        skill_ids = [
            _clean_text(value, limit=120)
            for value in list(previous.get("skill_ids") or [])
            if _clean_text(value, limit=120)
        ]
        for value in list(signal.get("skill_ids") or []):
            clean_value = _clean_text(value, limit=120)
            if clean_value and clean_value not in skill_ids:
                skill_ids.append(clean_value)
        contract_ids = [
            _clean_text(value, limit=160)
            for value in list(previous.get("contract_ids") or [])
            if _clean_text(value, limit=160)
        ]
        for value in list(signal.get("contract_ids") or []):
            clean_value = _clean_text(value, limit=160)
            if clean_value and clean_value not in contract_ids:
                contract_ids.append(clean_value)
        summary = (
            f"{venue.upper()} 매니저가 위키 사용계약 해소 근거를 남기지 않은 "
            f"표본 {sample_count}건. 최근 액션 {action_count}개 중 "
            f"해소 metadata {resolution_count}개, 해소율 {resolution_ratio:.1%}."
        )
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "venue": venue,
                "memory_scope": venue,
                "transferability": "direct",
                "manager_run_id": signal.get("manager_run_id"),
                "run_at": signal.get("run_at"),
                "status": status,
                "action_count": action_count,
                "resolution_count": resolution_count,
                "resolution_ratio": resolution_ratio,
                "missing_blocker_count": missing_blocker_count,
                "partial_blocker_count": partial_blocker_count,
                "workflow_id": workflow_id,
                "workflow_version": signal.get("workflow_version"),
                "skill_ids": _compact_text_list(skill_ids, limit=8, item_limit=120),
                "contract_ids": _compact_text_list(
                    contract_ids,
                    limit=12,
                    item_limit=160,
                ),
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": "caution",
                "status": "active_caution",
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "jue_wiki_usage_contract_gap",
                "memory_scope": venue,
                "scope": venue,
                "transferability": "direct",
                "scope_evidence": [
                    {
                        "memory_scope": venue,
                        "transferability": "direct",
                        "source": "jue_wiki_usage_contract_gap",
                    }
                ],
                "latest_status": status,
                "latest_reason": status,
                "latest_symbol": "",
                "action_count": action_count,
                "resolution_count": resolution_count,
                "resolution_ratio": resolution_ratio,
                "missing_blocker_count": missing_blocker_count,
                "partial_blocker_count": partial_blocker_count,
                "workflow_ids": _compact_text_list(
                    workflow_ids,
                    limit=8,
                    item_limit=120,
                ),
                "skill_ids": _compact_text_list(skill_ids, limit=12, item_limit=120),
                "contract_ids": _compact_text_list(
                    contract_ids,
                    limit=16,
                    item_limit=160,
                ),
            }
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def _active_jue_wiki_action_reference_gap_policy_ids_for_venue(
        self,
        venue: str,
        *,
        limit: int = 12,
    ) -> list[str]:
        clean_venue = _normalize_memory_scope(venue)
        policy_ids: list[str] = []
        for row in self.repository.list_policy_scorecards(limit=120):
            if not isinstance(row, dict):
                continue
            if str(row.get("source") or "") != "jue_wiki_action_reference_gap":
                continue
            if _policy_row_scope(row) != clean_venue:
                continue
            status = _clean_text(row.get("status"), limit=80)
            if status in {"resolved", "inactive", "retired"}:
                continue
            policy_id = _clean_text(row.get("policy_id"), limit=180)
            if not policy_id.startswith("jue_wiki_action_reference_gap."):
                continue
            resolution_status = _clean_text(row.get("resolution_status"), limit=80)
            blocker_count = _safe_int(row.get("blocker_count"))
            unresolved_blocker_count = _safe_int(
                row.get("unresolved_memory_blocker_count")
            )
            unresolved = (
                resolution_status in {"", "unresolved"}
                or blocker_count > 0
                or unresolved_blocker_count > 0
                or policy_id.endswith(".missing")
                or policy_id.endswith(".unresolved_memory")
            )
            if not unresolved:
                continue
            if policy_id not in policy_ids:
                policy_ids.append(policy_id)
            if len(policy_ids) >= max(int(limit), 1):
                break
        return policy_ids

    def _rejected_create_policy_signal(
        self,
        event: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        row = payload.get("row") if isinstance(payload.get("row"), dict) else {}
        symbol = _clean_text(
            payload.get("symbol") or row.get("symbol"),
            limit=40,
        )
        reason = _clean_text(payload.get("reason"), limit=160) or "unknown"
        reason_token = self._normalize_policy_id(reason) or "unknown"
        policy_id = f"rejected_entry.{reason_token}"
        event_id = str(event.get("id") or "").strip()
        manager_run_id = str(payload.get("manager_run_id") or "").strip()
        created_at = _clean_text(event.get("created_at"), limit=80)
        event_key = (
            f"manager_create_rejected:{event_id}"
            if event_id
            else "manager_create_rejected:"
            + self._normalize_policy_id(
                f"{manager_run_id}:{symbol}:{reason}:{created_at}"
            )
        )
        gate = (
            row.get("entry_quality_gate")
            if isinstance(row.get("entry_quality_gate"), dict)
            else {}
        )
        return {
            "event_key": event_key,
            "policy_id": policy_id,
            "symbol": symbol,
            "reason": reason,
            "manager_run_id": _safe_int(payload.get("manager_run_id")),
            "horizon": _clean_text(row.get("horizon"), limit=40),
            "entry_style": _clean_text(row.get("entry_style"), limit=80),
            "entry_quality_gate": gate,
            "created_at": created_at,
        }

    def _upsert_rejected_create_scorecard(self, signal: dict[str, Any]) -> None:
        policy_id = _clean_text(signal.get("policy_id"), limit=160)
        if not policy_id:
            return
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        confidence = min(0.95, 0.35 + sample_count * 0.10)
        action = "observe"
        status = "candidate"
        if sample_count >= 3 and confidence >= 0.65:
            action = "caution"
            status = "active_caution"
        reason = _clean_text(signal.get("reason"), limit=160) or "unknown"
        symbol = _clean_text(signal.get("symbol"), limit=40)
        gate = (
            signal.get("entry_quality_gate")
            if isinstance(signal.get("entry_quality_gate"), dict)
            else {}
        )
        gate_reasons = [
            _clean_text(item, limit=120)
            for item in list(gate.get("reasons") or [])[:8]
            if _clean_text(item, limit=120)
        ]
        summary = (
            f"고점 추격/진입품질 문제로 신규 블록이 거절된 표본 {sample_count}건. "
            f"최근 사유: {reason}, 최근 종목: {symbol or '-'}."
        )
        evidence = [
            {
                "event_key": signal.get("event_key"),
                "symbol": symbol,
                "reason": reason,
                "manager_run_id": signal.get("manager_run_id"),
                "horizon": signal.get("horizon"),
                "entry_style": signal.get("entry_style"),
                "entry_quality_gate": gate,
            }
        ]
        self.repository.upsert_policy_scorecard(
            {
                "policy_id": policy_id,
                "action": action,
                "status": status,
                "sample_count": sample_count,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "rule_follow_rate": 1.0,
                "confidence": confidence,
                "reason": summary,
                "source": "manager_create_rejected",
                "latest_symbol": symbol,
                "latest_reason": reason,
                "gate_reasons": gate_reasons,
            }
        )
        self.repository.save_insight(
            memory_type="policy_signal",
            key=policy_id,
            status="active",
            confidence=confidence,
            summary_md=summary,
            evidence=evidence,
            source_run_id=None,
        )

    def ingest_trading_validation_signals(
        self,
        *,
        venue: str,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        self.initialize()
        self._clear_validation_repair_ops_cache()
        if not isinstance(validation, dict):
            return {"status": "skipped", "reason": "validation_payload_missing"}
        payload = (
            validation.get("payload")
            if isinstance(validation.get("payload"), dict)
            else validation
        )
        if not isinstance(payload, dict) or str(payload.get("status") or "") == "empty":
            return {"status": "skipped", "reason": "validation_payload_empty"}
        clean_venue = _normalize_memory_scope(
            venue or payload.get("venue") or validation.get("venue")
        )
        if clean_venue not in {"kis", "binance"}:
            clean_venue = "core"
        run_id = _clean_text(
            payload.get("run_id") or validation.get("run_id"),
            limit=120,
        )
        computed_at = _clean_text(
            payload.get("computed_at") or validation.get("computed_at"),
            limit=80,
        )
        event_seed = run_id or self._normalize_policy_id(
            f"{clean_venue}:{computed_at}:{payload.get('discipline_count')}"
        )
        if not event_seed:
            return {"status": "skipped", "reason": "validation_run_identity_missing"}
        event_key = f"trading_validation:{clean_venue}:{event_seed}"
        existing = self.repository.get_memory_event(event_key)
        if existing and str(existing.get("status") or "") == "processed":
            return {
                "status": "skipped",
                "reason": "validation_run_already_ingested",
                "event_key": event_key,
            }
        disciplines = [
            row
            for row in list(payload.get("disciplines") or [])
            if isinstance(row, dict)
        ]
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        should_complete_missing_disciplines = (
            _safe_float(summary.get("missing_count")) > 0
            or str(payload.get("status") or "").strip().lower()
            in {"validation_incomplete", "incomplete"}
        )
        if should_complete_missing_disciplines:
            disciplines = _complete_validation_disciplines_for_memory(disciplines)
        weak_disciplines = [
            row
            for row in disciplines
            if str(row.get("status") or "").strip().lower() in {
                "fail",
                "failed",
                "missing",
                "warn",
                "warning",
            }
        ]
        if not weak_disciplines:
            self.repository.save_memory_event(
                event_key=event_key,
                event_type="trading_validation_clean",
                block_id="__system__",
                status="pending",
                payload={
                    "venue": clean_venue,
                    "run_id": run_id,
                    "computed_at": computed_at,
                    "summary": payload.get("summary") or {},
                },
            )
            self.repository.mark_memory_event_processed(event_key)
            return {
                "status": "ok",
                "event_key": event_key,
                "processed_count": 0,
                "reason": "no_weak_validation_disciplines",
            }

        saved: list[dict[str, Any]] = []
        for row in weak_disciplines:
            scorecard = self._validation_signal_scorecard(
                venue=clean_venue,
                row=row,
                payload=payload,
            )
            if not scorecard:
                continue
            saved.append(self.repository.upsert_policy_scorecard(scorecard))
        self.repository.save_memory_event(
            event_key=event_key,
            event_type="trading_validation_signal",
            block_id="__system__",
            status="pending",
            payload={
                "venue": clean_venue,
                "run_id": run_id,
                "computed_at": computed_at,
                "summary": payload.get("summary") or {},
                "weak_disciplines": [
                    {
                        "id": row.get("id"),
                        "label": row.get("label") or row.get("id"),
                        "status": row.get("status"),
                        "action": row.get("action") or row.get("purpose"),
                    }
                    for row in weak_disciplines[:19]
                ],
                "remediation_plan": payload.get("remediation_plan") or {},
            },
        )
        self.repository.mark_memory_event_processed(event_key)
        self.repository.save_insight(
            memory_type="policy_signal",
            key=f"trading_validation.{clean_venue}",
            status="active",
            confidence=min(0.95, 0.35 + len(saved) * 0.03),
            summary_md=(
                f"{clean_venue} 19검증 약점 {len(saved)}건을 쥬 정책 후보로 반영. "
                "다음 블록은 해당 discipline이 복구될 때까지 수량, 진입 방식, "
                "target/stop을 더 치밀하게 재점검한다."
            ),
            evidence=[
                {
                    "event_key": event_key,
                    "venue": clean_venue,
                    "run_id": run_id,
                    "weak_count": len(saved),
                }
            ],
            source_run_id=None,
        )
        sync = self.sync_policy_rules()
        return {
            "status": "ok",
            "event_key": event_key,
            "processed_count": len(saved),
            "policy_scorecards": saved,
            "policy_rule_sync": sync,
        }

    def _validation_signal_scorecard(
        self,
        *,
        venue: str,
        row: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        discipline_id = self._policy_key_fragment(row.get("id") or row.get("label"))
        if not discipline_id:
            return {}
        policy_id = f"validation.{venue}.{discipline_id}"
        previous = self.repository.get_policy_scorecard(policy_id) or {}
        sample_count = max(_safe_int(previous.get("sample_count")), 0) + 1
        raw_status = str(row.get("status") or "missing").strip().lower()
        status_weight = 1.0 if raw_status in {"fail", "failed", "missing"} else 0.6
        confidence = min(0.95, 0.30 + sample_count * 0.12 * status_weight)
        action = "observe"
        scorecard_status = "candidate"
        if raw_status in {"fail", "failed", "missing"} and sample_count >= 3 and confidence >= 0.65:
            action = "caution"
            scorecard_status = "active_caution"
        elif raw_status in {"warn", "warning"} and sample_count >= 5 and confidence >= 0.60:
            action = "caution"
            scorecard_status = "active_caution"
        label = _clean_text(row.get("label") or discipline_id, limit=120)
        remediation = (
            payload.get("remediation_plan")
            if isinstance(payload.get("remediation_plan"), dict)
            else {}
        )
        work_queue = (
            remediation.get("work_queue")
            if isinstance(remediation.get("work_queue"), list)
            else []
        )
        work_item = next(
            (
                item
                for item in work_queue
                if isinstance(item, dict)
                and str(item.get("discipline_id") or "") == discipline_id
            ),
            {},
        )
        lane_policy_hint = _clean_text(
            work_item.get("lane_policy_hint")
            if isinstance(work_item, dict)
            else "",
            limit=160,
        )
        blocks_scaling = _clean_text(
            work_item.get("blocks_scaling") if isinstance(work_item, dict) else "",
            limit=160,
        )
        pass_path = _compact_validation_pass_path(
            work_item.get("pass_path") if isinstance(work_item, dict) else {}
        )
        pass_criteria = _clean_text(pass_path.get("pass_criteria"), limit=180)
        pass_hook = _clean_text(pass_path.get("collection_hook"), limit=120)
        reason = (
            f"{venue} 19검증 `{label}` {raw_status or 'weak'} 반복 표본 "
            f"{sample_count}건. {lane_policy_hint or '검증 복구 전 probe/대기 중심'}; "
            f"{blocks_scaling or 'scale-up 보류'}."
        )
        if pass_criteria or pass_hook:
            reason += (
                f" 통과경로: {pass_hook or 'refresh_validation'}"
                f" -> {pass_criteria or 'next validation pass'}."
            )
        return {
            "policy_id": policy_id,
            "action": action,
            "status": scorecard_status,
            "sample_count": sample_count,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "expectancy_pct": 0.0,
            "rule_follow_rate": 1.0,
            "confidence": confidence,
            "reason": reason,
            "source": "trading_validation_signal",
            "discipline_id": discipline_id,
            "discipline_label": label,
            "discipline_status": raw_status,
            "venue": venue,
            "memory_scope": venue,
            "transferability": "direct",
            "lane_policy_hint": lane_policy_hint,
            "blocks_scaling": blocks_scaling,
            "validation_pass_path": pass_path,
            "pass_current_gap": pass_path.get("current_gap"),
            "pass_collection_hook": pass_path.get("collection_hook"),
            "pass_criteria": pass_path.get("pass_criteria"),
            "pass_required_evidence": pass_path.get("required_evidence"),
            "pass_jue_behavior_until_pass": pass_path.get(
                "jue_behavior_until_pass"
            ),
            "pass_m1_runtime_profile": pass_path.get("m1_runtime_profile"),
            "scope_evidence": [
                {
                    "memory_scope": venue,
                    "transferability": "direct",
                    "source": "trading_validation_run",
                    "discipline_id": discipline_id,
                    "discipline_status": raw_status,
                }
            ],
        }

    @staticmethod
    def _period_work_complete(row: dict[str, Any] | None) -> bool:
        if not row:
            return False
        status = str(row.get("status") or "").strip().lower()
        return status not in {"", "missing", "llm_unavailable", "error", "failed"}

    @staticmethod
    def _normalized_due_memory_scopes(memory_scopes: list[str] | tuple[str, ...] | None) -> list[str]:
        scopes: list[str] = []
        for value in memory_scopes or []:
            scope = _normalize_memory_scope(value)
            if scope and scope not in scopes:
                scopes.append(scope)
        return scopes

    def _period_review_due(
        self,
        *,
        period_key: str,
        period_type: str,
        memory_scopes: list[str],
    ) -> bool:
        if not memory_scopes:
            return self.repository.get_period_review(period_key, period_type) is None
        return any(
            not self._period_work_complete(
                self.repository.get_period_review(
                    period_key,
                    period_type,
                    target_scope=scope,
                )
            )
            for scope in memory_scopes
        )

    def _historical_replay_due(
        self,
        *,
        period_key: str,
        period_type: str,
        memory_scopes: list[str],
    ) -> bool:
        if not memory_scopes:
            return self.repository.get_historical_replay(period_key, period_type) is None
        return any(
            not self._period_work_complete(
                self.repository.get_historical_replay(
                    period_key,
                    period_type,
                    target_scope=scope,
                )
            )
            for scope in memory_scopes
        )

    def due_slots(
        self,
        *,
        now: datetime | None = None,
        memory_scopes: list[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        local = (now or datetime.now(KST)).astimezone(KST)
        if not self._is_open_day(local.date()):
            return []
        due_memory_scopes = self._normalized_due_memory_scopes(memory_scopes)
        slots: list[str] = []
        schedule = [
            ("pre_open", time(8, 30), time(8, 55)),
            ("midday", time(11, 40), time(12, 10)),
            ("post_close", time(15, 45), time(16, 30)),
        ]
        trading_day = self._trading_day(local)
        current = local.time()
        for slot, window_start, window_end in schedule:
            if (
                window_start <= current <= window_end
                and self.repository.get_journal(trading_day, slot) is None
            ):
                slots.append(slot)
        if local.weekday() == 4 and time(16, 0) <= current <= time(17, 30):
            window = self.period_window(period_type="weekly", now=local)
            if self._period_review_due(
                period_key=window["period_key"],
                period_type="weekly",
                memory_scopes=due_memory_scopes,
            ):
                slots.append("weekly_review")
            if self._historical_replay_due(
                period_key=window["period_key"],
                period_type="weekly",
                memory_scopes=due_memory_scopes,
            ):
                slots.append("weekly_replay")
        if time(16, 0) <= current <= time(18, 0):
            next_open_day = self._next_open_day(local.date())
            is_last_open_day_of_month = next_open_day.month != local.date().month
        else:
            is_last_open_day_of_month = False
        if is_last_open_day_of_month:
            window = self.period_window(period_type="monthly", now=local)
            if self._period_review_due(
                period_key=window["period_key"],
                period_type="monthly",
                memory_scopes=due_memory_scopes,
            ):
                slots.append("monthly_review")
        return slots

    def _is_open_day(self, value: date) -> bool:
        try:
            return bool(self.calendar.is_open_day(value))
        except Exception:
            return value.weekday() < 5

    def _next_open_day(self, value: date) -> date:
        probe = value + timedelta(days=1)
        for _ in range(14):
            if self._is_open_day(probe):
                return probe
            probe += timedelta(days=1)
        return probe

    def _build_seed_output(self, context: dict[str, Any], *, trading_day: str) -> dict[str, Any]:
        blocks_payload = context.get("blocks") if isinstance(context.get("blocks"), dict) else {}
        blocks = [
            row
            for row in list((blocks_payload or {}).get("blocks") or [])
            if isinstance(row, dict)
        ]
        open_blocks = [
            row
            for row in blocks
            if str(row.get("status") or "") in {"open", "entry_pending", "exit_pending"}
        ]
        account = context.get("account") if isinstance(context.get("account"), dict) else {}
        reports = context.get("reports_status") if isinstance(context.get("reports_status"), dict) else {}
        strategy = context.get("strategy") if isinstance(context.get("strategy"), dict) else {}
        valuation = context.get("valuation_status") if isinstance(context.get("valuation_status"), dict) else {}
        source_status = context.get("strategy_source_status") if isinstance(context.get("strategy_source_status"), list) else []

        lines = [
            "# 쥬 초기 운용 메모리",
            "",
            f"- 기준일: {trading_day}",
            f"- 국장1 현금: {_safe_float(account.get('cash_krw')):,.0f}원",
            f"- 보유 종목: {_safe_int(account.get('position_count'))}개",
            f"- 활성/대기 블록: {len(open_blocks)}개",
            f"- 리포트 DB: {reports.get('report_count') or reports.get('total_reports') or reports.get('status') or '-'}",
            f"- 밸류 DB: {valuation.get('total_count') or valuation.get('snapshot_count') or valuation.get('status') or '-'}",
            f"- 전략 후보: {len(list(strategy.get('candidates') or [])) if isinstance(strategy, dict) else 0}개",
        ]
        if source_status:
            labels = [
                f"{row.get('source_id') or row.get('label')}: {row.get('status')}"
                for row in source_status[:4]
                if isinstance(row, dict)
            ]
            lines.append(f"- 외부 인사이트: {', '.join(labels)}")
        lines.append("")
        lines.append("## 블록별 시작 기억")
        for block in open_blocks[:20]:
            lines.append(
                "- "
                + self._block_summary_line(block)
            )

        memory_updates: dict[str, list[dict[str, Any]]] = {
            "symbols": [],
            "blocks": [],
            "notes": [
                {
                    "key": "regime:seed",
                    "summary_md": "\n".join(lines),
                    "confidence": 0.72,
                }
            ],
        }
        for block in open_blocks:
            block_id = str(block.get("block_id") or "")
            symbol = str(block.get("symbol") or "")
            summary = self._block_seed_summary(block)
            if block_id:
                memory_updates["blocks"].append(
                    {"block_id": block_id, "summary_md": summary, "confidence": 0.7}
                )
            if _is_symbol(symbol):
                memory_updates["symbols"].append(
                    {
                        "symbol": symbol,
                        "summary_md": summary,
                        "confidence": 0.62,
                    }
                )
        return {
            "title": SLOT_LABELS["seed"],
            "message_md": "\n".join(lines),
            "memory_updates": memory_updates,
            "policy_changes": [],
        }

    def _block_summary_line(self, block: dict[str, Any]) -> str:
        return (
            f"{_symbol_display_label(block)}, "
            f"{_safe_int(block.get('qty_open') or block.get('qty_initial'))}주, "
            f"진입 {self._price_text(block.get('entry_price'))}, "
            f"목표 {self._price_text(block.get('target_price'))}, "
            f"손절 {self._price_text(block.get('stop_price'))}, "
            f"상태 {block.get('status') or '-'}"
        )

    def _block_seed_summary(self, block: dict[str, Any]) -> str:
        parts = [
            self._block_summary_line(block),
            f"가설: {_truncate(block.get('thesis') or block.get('llm_reason'), 700)}",
        ]
        risk = _clean_text(block.get("risk_note"), limit=700)
        if risk:
            parts.append(f"리스크: {risk}")
        return "\n".join(parts)

    @staticmethod
    def _price_text(value: Any) -> str:
        price = _safe_float(value)
        return f"{price:,.0f}원" if price > 0 else "-"

    def _block_needs_reflection(self, block: dict[str, Any], *, force: bool) -> bool:
        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            return False
        status = str(block.get("status") or "")
        if status not in {"closed", "error"}:
            return False
        if force:
            return True
        existing = self.repository.get_block_reflection(block_id)
        if existing is None:
            return True
        if status == "closed" and str(existing.get("status") or "") != "closed":
            return True
        block_terminal_at = _parse_datetime(block.get("closed_at") or block.get("updated_at"))
        if block_terminal_at is None:
            return False
        existing_metrics = (
            existing.get("metrics") if isinstance(existing.get("metrics"), dict) else {}
        )
        reflected_terminal_at = _parse_datetime(existing_metrics.get("closed_at"))
        if reflected_terminal_at is not None and block_terminal_at > reflected_terminal_at:
            return True
        reflection_updated_at = _parse_datetime(
            existing.get("updated_at") or existing.get("created_at")
        )
        return bool(
            reflection_updated_at is not None
            and block_terminal_at > reflection_updated_at
        )

    @staticmethod
    def _block_direction(block: dict[str, Any]) -> float:
        side = str(block.get("side") or "long").strip().lower()
        return -1.0 if side == "short" else 1.0

    @staticmethod
    def _numeric_cost_from_payload(payload: dict[str, Any], keys: tuple[str, ...]) -> float:
        total = 0.0
        for key in keys:
            if key in payload:
                total += _safe_float(payload.get(key))
        return total

    @staticmethod
    def _block_performance_payload(block: dict[str, Any]) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        for payload in (
            block.get("performance_reflection"),
            block.get("performance"),
            metadata.get("performance_reflection"),
            metadata.get("performance"),
        ):
            if isinstance(payload, dict):
                return payload
        return {}

    @classmethod
    def _numeric_cost_component_from_payload(
        cls,
        payload: dict[str, Any],
        keys: tuple[str, ...],
        *,
        component: str,
    ) -> float:
        total = cls._numeric_cost_from_payload(payload, keys)
        nested = (
            payload.get("cost_components")
            if isinstance(payload.get("cost_components"), dict)
            else {}
        )
        if nested:
            total += _safe_float(nested.get(component))
        return total

    @staticmethod
    def _cost_component_presence_from_payload(
        payload: dict[str, Any],
        *,
        component_keys: dict[str, tuple[str, ...]],
    ) -> set[str]:
        present: set[str] = set()
        components = (
            payload.get("cost_components")
            or payload.get("cost_breakdown")
            or payload.get("cost_component_sources")
            or payload.get("component_sources")
        )
        components = (
            _json_loads(components, {})
            if isinstance(components, str)
            else components
        )
        if isinstance(components, dict):
            for raw_key, raw_value in components.items():
                if _is_absent_cost_component_marker(raw_value):
                    continue
                if component := _cost_component_label(raw_key):
                    present.add(component)
        for declaration_key in _COST_COMPONENT_DECLARATION_KEYS:
            present.update(_declared_cost_components(payload.get(declaration_key)))
        for component, keys in component_keys.items():
            for key in keys:
                if key in payload and not _is_absent_cost_component_marker(
                    payload.get(key)
                ):
                    present.add(component)
        for raw_key, component in _COST_COMPONENT_ALIASES.items():
            if (
                raw_key in payload
                and not _is_absent_cost_component_marker(payload.get(raw_key))
            ):
                present.add(component)
        return present

    @staticmethod
    def _required_cost_components_for_block(
        block: dict[str, Any],
        metadata: dict[str, Any],
        performance: dict[str, Any],
    ) -> set[str]:
        memory_scope = _block_memory_scope(block)
        market = _clean_key(
            block.get("market")
            or metadata.get("market")
            or performance.get("market")
        )
        lane = _clean_key(
            block.get("lane")
            or metadata.get("lane")
            or performance.get("lane")
        )
        side = _clean_key(
            block.get("side")
            or metadata.get("side")
            or performance.get("side")
        )
        if memory_scope == "binance":
            is_futures = bool(
                market in {"futures", "perp", "perpetual"}
                or lane in {
                    "futures",
                    "futures_long",
                    "futures_short",
                    "volatile_attack",
                }
                or lane.startswith("futures")
                or side == "short"
            )
            return (
                {"fees", "funding", "spread", "slippage"}
                if is_futures
                else {"fees", "spread", "slippage"}
            )
        if memory_scope == "kis":
            return {"fees", "taxes", "spread", "slippage"}
        return {"fees"}

    def _reflection_costs(
        self,
        block: dict[str, Any],
        *,
        orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        block_id = str(block.get("block_id") or "")
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        performance = self._block_performance_payload(block)
        fee_keys = (
            "fee",
            "fees",
            "fee_usdt",
            "fees_usdt",
            "fee_krw",
            "fees_krw",
            "commission",
            "commission_usdt",
            "commission_krw",
        )
        tax_keys = ("tax", "taxes", "tax_usdt", "taxes_usdt", "tax_krw", "taxes_krw")
        funding_keys = ("funding", "funding_fee", "funding_usdt", "funding_fee_usdt")
        slippage_keys = ("slippage", "slippage_usdt", "slippage_krw")
        spread_keys = ("spread", "spread_usdt", "spread_krw")
        component_keys = {
            "fees": fee_keys,
            "taxes": tax_keys,
            "funding": funding_keys,
            "slippage": slippage_keys,
            "spread": spread_keys,
        }
        present_components: set[str] = set()
        fee = self._numeric_cost_component_from_payload(
            metadata,
            fee_keys,
            component="fees",
        )
        taxes = self._numeric_cost_component_from_payload(
            metadata,
            tax_keys,
            component="taxes",
        )
        funding = self._numeric_cost_component_from_payload(
            metadata,
            funding_keys,
            component="funding",
        )
        slippage = self._numeric_cost_component_from_payload(
            metadata,
            slippage_keys,
            component="slippage",
        )
        spread = self._numeric_cost_component_from_payload(
            metadata,
            spread_keys,
            component="spread",
        )
        present_components.update(
            self._cost_component_presence_from_payload(
                metadata,
                component_keys=component_keys,
            )
        )
        fee += self._numeric_cost_component_from_payload(
            performance,
            fee_keys,
            component="fees",
        )
        taxes += self._numeric_cost_component_from_payload(
            performance,
            tax_keys,
            component="taxes",
        )
        funding += self._numeric_cost_component_from_payload(
            performance,
            funding_keys,
            component="funding",
        )
        slippage += self._numeric_cost_component_from_payload(
            performance,
            slippage_keys,
            component="slippage",
        )
        spread += self._numeric_cost_component_from_payload(
            performance,
            spread_keys,
            component="spread",
        )
        present_components.update(
            self._cost_component_presence_from_payload(
                performance,
                component_keys=component_keys,
            )
        )
        for order in orders:
            if str(order.get("block_id") or "") != block_id:
                continue
            response = order.get("response") if isinstance(order.get("response"), dict) else {}
            present_components.update(
                self._cost_component_presence_from_payload(
                    order,
                    component_keys=component_keys,
                )
            )
            present_components.update(
                self._cost_component_presence_from_payload(
                    response,
                    component_keys=component_keys,
                )
            )
            fee += self._numeric_cost_component_from_payload(order, fee_keys, component="fees")
            taxes += self._numeric_cost_component_from_payload(order, tax_keys, component="taxes")
            funding += self._numeric_cost_component_from_payload(
                order,
                funding_keys,
                component="funding",
            )
            slippage += self._numeric_cost_component_from_payload(
                order,
                slippage_keys,
                component="slippage",
            )
            spread += self._numeric_cost_component_from_payload(
                order,
                spread_keys,
                component="spread",
            )
            fee += self._numeric_cost_component_from_payload(response, fee_keys, component="fees")
            taxes += self._numeric_cost_component_from_payload(
                response,
                tax_keys,
                component="taxes",
            )
            funding += self._numeric_cost_component_from_payload(
                response,
                funding_keys,
                component="funding",
            )
            slippage += self._numeric_cost_component_from_payload(
                response,
                slippage_keys,
                component="slippage",
            )
            spread += self._numeric_cost_component_from_payload(
                response,
                spread_keys,
                component="spread",
            )
        explicit_total = self._numeric_cost_from_payload(
            performance,
            (
                "total_cost",
                "total_cost_usdt",
                "total_cost_krw",
                "cost_total",
                "cost_total_usdt",
                "cost_total_krw",
            ),
        )
        component_total = fee + taxes + funding + slippage + spread
        total = explicit_total if explicit_total > 0 else component_total
        cost_source = _clean_text(performance.get("cost_source"), limit=80)
        component_amounts = {
            "fees": fee,
            "taxes": taxes,
            "funding": funding,
            "slippage": slippage,
            "spread": spread,
        }
        present_components.update(
            component
            for component, amount in component_amounts.items()
            if abs(amount) > 0
        )
        required_components = self._required_cost_components_for_block(
            block,
            metadata,
            performance,
        )
        missing_components = required_components - present_components
        has_any_cost_evidence = bool(
            present_components
            or abs(total) > 0
            or abs(explicit_total) > 0
            or cost_source
        )
        if missing_components and has_any_cost_evidence:
            cost_precision = "partial"
            cost_precision_reason = "recorded_cost_missing_required_components"
        elif has_any_cost_evidence:
            cost_precision = "recorded"
            cost_precision_reason = "cost_components_audited"
        else:
            cost_precision = "missing"
            cost_precision_reason = "missing_cost_evidence"
        source = cost_source or (
            "explicit"
            if any(
                abs(value) > 0
                for value in (fee, taxes, funding, slippage, spread, explicit_total)
            )
            else "audited_zero"
            if present_components
            else "missing"
        )
        return {
            "fee": fee,
            "taxes": taxes,
            "funding": funding,
            "slippage": slippage,
            "spread": spread,
            "total": total,
            "component_total": component_total,
            "source": source,
            "cost_precision": cost_precision,
            "cost_precision_reason": cost_precision_reason,
            "required_cost_components": sorted(required_components),
            "present_cost_components": sorted(present_components),
            "missing_cost_components": sorted(missing_components),
        }

    def _build_block_reflection(
        self,
        block: dict[str, Any],
        *,
        orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        block_id = str(block.get("block_id") or "")
        symbol = str(block.get("symbol") or "")
        memory_scope = _block_memory_scope(block)
        transferability = "translated" if memory_scope in {"kis", "binance"} else "direct"
        status = str(block.get("status") or "").strip().lower()
        entry = _safe_float(block.get("entry_price"))
        qty = _safe_float(block.get("qty_initial") or block.get("qty_open"))
        if qty <= 0:
            qty = 1.0
        performance = self._block_performance_payload(block)
        order_exit_price, exit_reason = self._exit_from_orders(block, orders)
        performance_exit_price = _safe_float(performance.get("exit_price"))
        exit_price = order_exit_price
        exit_price_source = "order" if order_exit_price > 0 else ""
        if exit_price <= 0:
            exit_price = performance_exit_price
            exit_price_source = "performance" if performance_exit_price > 0 else ""
        real_opened = _parse_datetime(block.get("opened_at"))
        has_recorded_exit = order_exit_price > 0 or performance_exit_price > 0
        pnl_status = "realized"
        pnl_status_reason = ""
        if status in {"error", "proposed", "entry_pending"} and not real_opened and not has_recorded_exit:
            exit_price = entry
            exit_price_source = "none"
            pnl_status = "not_executed"
            pnl_status_reason = f"{status or 'unknown'}_without_open_or_exit_fill"
        elif exit_price <= 0:
            exit_price = _safe_float(block.get("current_price") or (block.get("quote") or {}).get("price")) or entry
            exit_price_source = "current_price_fallback"
        direction = self._block_direction(block)
        gross_pnl = direction * (exit_price - entry) * qty if entry > 0 and exit_price > 0 else 0.0
        costs = self._reflection_costs(block, orders=orders)
        net_pnl = gross_pnl - _safe_float(costs.get("total"))
        notional = entry * qty if entry > 0 and qty > 0 else 0.0
        pnl_krw = net_pnl
        pnl_pct = net_pnl / notional * 100.0 if notional > 0 else 0.0
        gross_pnl_pct = gross_pnl / notional * 100.0 if notional > 0 else 0.0
        quote = block.get("quote") if isinstance(block.get("quote"), dict) else {}
        high = _safe_float(quote.get("high_price") or exit_price)
        low = _safe_float(quote.get("low_price") or exit_price)
        if direction < 0:
            favorable = min(low or exit_price, exit_price)
            adverse = max(high or exit_price, exit_price)
            mfe_pct = ((entry - favorable) / entry * 100.0) if entry > 0 else 0.0
            mae_pct = ((entry - adverse) / entry * 100.0) if entry > 0 else 0.0
        else:
            mfe_pct = ((max(high, exit_price) - entry) / entry * 100.0) if entry > 0 else 0.0
            mae_pct = ((min(low, exit_price) - entry) / entry * 100.0) if entry > 0 else 0.0
        if pnl_status == "not_executed":
            gross_pnl = 0.0
            net_pnl = 0.0
            pnl_krw = 0.0
            pnl_pct = 0.0
            gross_pnl_pct = 0.0
            mfe_pct = 0.0
            mae_pct = 0.0
            costs = {
                **costs,
                "fee": 0.0,
                "taxes": 0.0,
                "funding": 0.0,
                "slippage": 0.0,
                "spread": 0.0,
                "total": 0.0,
                "component_total": 0.0,
                "source": "not_applicable",
                "cost_precision": "not_applicable",
                "cost_precision_reason": "not_executed",
                "required_cost_components": [],
                "present_cost_components": [],
                "missing_cost_components": [],
            }
        opened = real_opened or _parse_datetime(block.get("created_at"))
        closed = _parse_datetime(block.get("closed_at") or block.get("updated_at"))
        closed_at = closed.isoformat() if closed else ""
        outcome_date = closed.astimezone(KST).date().isoformat() if closed else ""
        hold_seconds = int((closed - opened).total_seconds()) if opened and closed and closed > opened else 0
        if not exit_reason:
            exit_reason = _clean_text(block.get("llm_reason") or status, limit=160)
        rule_followed = status == "closed" and any(
            token in exit_reason
            for token in ["target", "stop", "force", "manual", "reconciled", "closed"]
        )
        policy_id, policy_action = self._policy_for_reflection(
            status=status,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
        )
        live_authority = _compact_live_authority_for_reflection(block)
        validation_repair_enforcement = (
            _compact_validation_repair_enforcement_for_reflection(block)
        )
        lane_authority_gate = _compact_lane_authority_gate_for_reflection(block)
        jue_wiki_execution_hint_audit = self._jue_wiki_execution_hint_audit(block)
        jue_wiki_usage_contract_audit = self._jue_wiki_usage_contract_audit(block)
        period_memory_coverage_audit = self._period_memory_coverage_audit(block)
        validation_lines: list[str] = []
        if live_authority:
            discipline_matrix = (
                live_authority.get("discipline_matrix")
                if isinstance(live_authority.get("discipline_matrix"), dict)
                else {}
            )
            matrix_summary = (
                discipline_matrix.get("summary")
                if isinstance(discipline_matrix.get("summary"), dict)
                else {}
            )
            failed_labels = [
                str(row.get("label") or row.get("id") or "").strip()
                for row in list(live_authority.get("failed_disciplines") or [])
                if isinstance(row, dict)
                and str(row.get("label") or row.get("id") or "").strip()
            ]
            if not failed_labels and discipline_matrix:
                failed_labels = [
                    str(row.get("label") or row.get("id") or "").strip()
                    for row in list(discipline_matrix.get("statuses") or [])
                    if isinstance(row, dict)
                    and str(row.get("status") or "").strip().lower() == "fail"
                    and str(row.get("label") or row.get("id") or "").strip()
                ]
            capacity = (
                live_authority.get("capacity_bottleneck")
                if isinstance(live_authority.get("capacity_bottleneck"), dict)
                else {}
            )
            attribution = (
                live_authority.get("failure_attribution")
                if isinstance(live_authority.get("failure_attribution"), dict)
                else {}
            )
            recovery_focus = (
                attribution.get("recovery_focus")
                if isinstance(attribution.get("recovery_focus"), list)
                else []
            )
            validation_recovery_focus = (
                live_authority.get("validation_recovery_focus")
                if isinstance(live_authority.get("validation_recovery_focus"), list)
                else []
            )
            validation_passport = (
                live_authority.get("validation_passport")
                if isinstance(live_authority.get("validation_passport"), dict)
                else {}
            )
            validation_pressure = (
                live_authority.get("validation_pressure")
                if isinstance(live_authority.get("validation_pressure"), dict)
                else {}
            )
            if validation_passport:
                passport_actual = validation_passport.get("actual_count")
                passport_expected = validation_passport.get("expected_count") or 19
                passport_score = validation_passport.get("score")
                passport_state = (
                    "재검증"
                    if validation_passport.get("requires_revalidation")
                    else "통과"
                )
                passport_failed = [
                    str(value).strip()
                    for value in list(validation_passport.get("failed_ids") or [])[:4]
                    if str(value).strip()
                ]
                validation_lines.append(
                    "검증 여권: "
                    f"{passport_state}, "
                    f"{passport_actual or 0}/{passport_expected}"
                    + (
                        f", 점수 {float(passport_score):.1f}"
                        if passport_score not in (None, "")
                        else ""
                    )
                    + (
                        f", 실패 {', '.join(passport_failed)}"
                        if passport_failed
                        else ""
                    )
                    + "."
                )
            if validation_pressure:
                first_action = (
                    validation_pressure.get("discipline_actions")[0]
                    if isinstance(validation_pressure.get("discipline_actions"), list)
                    and validation_pressure.get("discipline_actions")
                    and isinstance(validation_pressure.get("discipline_actions")[0], dict)
                    else {}
                )
                validation_lines.append(
                    "검증 압력: "
                    f"{validation_pressure.get('severity') or '-'}, "
                    f"진입 {validation_pressure.get('entry_posture') or '-'}, "
                    f"수량 {validation_pressure.get('sizing_posture') or '-'}"
                    + (
                        ", 조치 "
                        f"{first_action.get('id') or '-'}→"
                        f"{first_action.get('entry_constraint') or first_action.get('repair_action') or '-'}"
                        if first_action
                        else ""
                    )
                    + "."
                )
            if failed_labels or capacity or recovery_focus:
                validation_lines.append(
                    "검증 게이트: "
                    f"{live_authority.get('validation_gate_status') or '-'}"
                    + (
                        f", 실패 {', '.join(failed_labels[:4])}"
                        if failed_labels
                        else ""
                    )
                    + (
                        ", 용량 병목 "
                        f"{capacity.get('tightest_symbol') or '-'} "
                        f"({capacity.get('min_capacity_ratio') or '-'}x)"
                        if capacity
                        else ""
                    )
                    + (
                        f", 실패 귀속 {_truncate(str(recovery_focus[0]), 180)}"
                        if recovery_focus
                        else ""
                    )
                    + "."
                )
            if discipline_matrix:
                validation_lines.append(
                    "19검증 matrix: "
                    f"P {matrix_summary.get('pass_count', '-')}, "
                    f"W {matrix_summary.get('warn_count', '-')}, "
                    f"F {matrix_summary.get('fail_count', '-')}, "
                    f"M {matrix_summary.get('missing_count', '-')}, "
                    f"{discipline_matrix.get('actual_count') or len(discipline_matrix.get('statuses') or [])}/"
                    f"{discipline_matrix.get('expected_count') or 19}."
                )
            if validation_recovery_focus:
                first_recovery = (
                    validation_recovery_focus[0]
                    if isinstance(validation_recovery_focus[0], dict)
                    else {}
                )
                validation_lines.append(
                    "검증 복구: "
                    f"{first_recovery.get('source') or '-'} "
                    f"{first_recovery.get('reason') or '-'}"
                    + (
                        f", 조치 {_truncate(first_recovery.get('action'), 180)}"
                        if first_recovery.get("action")
                        else ""
                    )
                    + "."
                )
            guidance = list(live_authority.get("operator_guidance") or [])
            if guidance:
                validation_lines.append(f"검증 조치: {_truncate(guidance[0], 220)}")
        if lane_authority_gate:
            scale_blockers = [
                str(value).strip()
                for value in list(lane_authority_gate.get("scale_blockers") or [])[:3]
                if str(value).strip()
            ]
            scale_repairs = [
                str(value).strip()
                for value in list(
                    lane_authority_gate.get("scale_repair_targets") or []
                )[:3]
                if str(value).strip()
            ]
            if not scale_repairs:
                for key in ("cost_repair_targets", "entry_repair_targets"):
                    scale_repairs.extend(
                        str(value).strip()
                        for value in list(lane_authority_gate.get(key) or [])[:3]
                        if str(value).strip()
                    )
            validation_lines.append(
                "Lane 권한: "
                f"{lane_authority_gate.get('scale_decision') or '-'}, "
                f"action {lane_authority_gate.get('action') or '-'}"
                + (
                    f", 차단 {', '.join(scale_blockers)}"
                    if scale_blockers
                    else ""
                )
                + (
                    f", 복구 {', '.join(scale_repairs[:3])}"
                    if scale_repairs
                    else ""
                )
                + "."
            )
        if validation_repair_enforcement:
            repair_actions = [
                str(value).strip()
                for value in list(
                    validation_repair_enforcement.get("repair_action_ids") or []
                )[:3]
                if str(value).strip()
            ]
            adjustment_labels = [
                (
                    f"{row.get('field')} {row.get('from')}→{row.get('to')}"
                    if row.get("field") and row.get("from") not in (None, "")
                    else str(row.get("reason") or "").strip()
                )
                for row in list(validation_repair_enforcement.get("adjustments") or [])[:3]
                if isinstance(row, dict)
            ]
            validation_lines.append(
                "검증 수리 강제: "
                + (
                    "증액 차단, "
                    if validation_repair_enforcement.get("scale_up_blocked")
                    else ""
                )
                + (
                    "대기진입 요구, "
                    if validation_repair_enforcement.get("waiting_entry_required")
                    else ""
                )
                + (
                    f"budget x{_safe_float(validation_repair_enforcement.get('budget_multiplier')):.2f}, "
                    if validation_repair_enforcement.get("budget_multiplier")
                    not in (None, "")
                    else ""
                )
                + (
                    f"조정 {', '.join(adjustment_labels)}, "
                    if adjustment_labels
                    else ""
                )
                + (
                    f"수리 {', '.join(repair_actions)}, "
                    if repair_actions
                    else ""
                )
                + (
                    f"거절 {validation_repair_enforcement.get('reason')}"
                    if validation_repair_enforcement.get("rejected")
                    else "다음 판단에서 이 강제 조치의 성과를 별도 평가한다"
                )
                + "."
            )
        if jue_wiki_execution_hint_audit:
            validation_lines.append(
                "위키 실행 힌트: "
                f"{jue_wiki_execution_hint_audit.get('execution_hint') or '-'}, "
                f"기대 {jue_wiki_execution_hint_audit.get('expected') or '-'}, "
                f"실제 {jue_wiki_execution_hint_audit.get('actual') or '-'}, "
                f"판정 {jue_wiki_execution_hint_audit.get('status') or '-'}."
            )
        if jue_wiki_usage_contract_audit:
            cross_checks = [
                str(value).strip()
                for value in list(
                    jue_wiki_usage_contract_audit.get("cross_checks") or []
                )
                if str(value).strip()
            ]
            validation_lines.append(
                "위키 사용계약: "
                f"{jue_wiki_usage_contract_audit.get('status') or '-'}, "
                "단독 매매권한 "
                + (
                    "없음"
                    if jue_wiki_usage_contract_audit.get(
                        "standalone_trade_authority"
                    )
                    is False
                    else "불명"
                )
                + (
                    f", 교차확인 {', '.join(cross_checks)}"
                    if cross_checks
                    else ", 교차확인 미기록"
                )
                + (
                    f", 해결 {_truncate(jue_wiki_usage_contract_audit.get('resolution'), 220)}"
                    if jue_wiki_usage_contract_audit.get("resolution")
                    else ""
                )
                + "."
            )
        if period_memory_coverage_audit:
            repair_action = _clean_text(
                period_memory_coverage_audit.get("repair_action"),
                limit=160,
            )
            audit_resolution = _clean_text(
                period_memory_coverage_audit.get("metadata_contract_audit_resolution"),
                limit=300,
            )
            repair_note = _clean_text(
                period_memory_coverage_audit.get("metadata_contract_repair_note"),
                limit=500,
            )
            validation_lines.append(
                "메모리 커버리지: "
                f"{period_memory_coverage_audit.get('status') or '-'}, "
                f"{period_memory_coverage_audit.get('gap') or '-'}"
                + (
                    ", override "
                    f"{period_memory_coverage_audit.get('override_reason')}"
                    if period_memory_coverage_audit.get("override_reason")
                    else ""
                )
                + (f", 수리 {repair_action}" if repair_action else "")
                + (f", 해결 {audit_resolution}" if audit_resolution else "")
                + (f", 수리노트 {repair_note}" if repair_note else "")
                + "."
            )
        if pnl_status == "not_executed":
            result_line = (
                "결과: 미체결/에러 블록으로 실현손익 없음 "
                f"(사유: {exit_reason or status})."
            )
            path_line = "가격 경로: 실제 진입/청산 체결이 없어 MFE/MAE를 성과로 반영하지 않음."
        else:
            result_line = f"결과: {pnl_pct:+.2f}% / {pnl_krw:,.0f}원, 사유: {exit_reason or status}."
            path_line = f"가격 경로: MFE {mfe_pct:+.2f}%, MAE {mae_pct:+.2f}%."
        lesson = "\n".join(
            [
                f"{_symbol_display_label(block, fallback_symbol=symbol)} 블록 복기.",
                f"진입 가설: {_truncate(block.get('thesis') or block.get('llm_reason'), 500)}",
                result_line,
                path_line,
                *validation_lines,
                f"적용 범위: {memory_scope} 경험이며, 다른 거래장에는 `{transferability}` 메모리로만 전달한다.",
                "다음 원칙: 목표/손절 약속과 실제 가격 경로를 분리해서 보고, 같은 유형의 블록은 "
                f"`{policy_id}` 정책 후보로 누적 평가한다.",
            ]
        )
        return {
            "block_id": block_id,
            "symbol": symbol,
            "name": str(block.get("name") or symbol),
            "status": status,
            "exit_reason": exit_reason,
            "pnl_krw": pnl_krw,
            "pnl_pct": pnl_pct,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "hold_seconds": hold_seconds,
            "rule_followed": rule_followed,
            "lesson_md": lesson,
            "metrics": {
                "entry_price": entry,
                "exit_price": exit_price,
                "qty": qty,
                "target_price": _safe_float(block.get("target_price")),
                "stop_price": _safe_float(block.get("stop_price")),
                "gross_pnl": gross_pnl,
                "gross_pnl_pct": gross_pnl_pct,
                "net_pnl": net_pnl,
                "net_pnl_pct": pnl_pct,
                "pnl_status": pnl_status,
                "pnl_status_reason": pnl_status_reason,
                "realized_pnl_available": pnl_status == "realized",
                "exit_price_source": exit_price_source,
                "costs": costs,
                "created_by": str(block.get("created_by") or ""),
                "closed_at": closed_at,
                "outcome_date": outcome_date,
                "policy_id": policy_id,
                "policy_action": policy_action,
                "memory_scope": memory_scope,
                "venue": memory_scope,
                "transferability": transferability,
                "market": str(block.get("market") or ""),
                "side": str(block.get("side") or "long"),
                "live_authority": live_authority,
                "validation_repair_enforcement": validation_repair_enforcement,
                "lane_authority_gate": lane_authority_gate,
                "jue_wiki_execution_hint_audit": jue_wiki_execution_hint_audit,
                "jue_wiki_usage_contract_audit": jue_wiki_usage_contract_audit,
                "period_memory_coverage_audit": period_memory_coverage_audit,
            },
        }

    @classmethod
    def _jue_wiki_usage_contract_audit(
        cls,
        block: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        resolution = _clean_text(
            metadata.get("jue_wiki_usage_contract_resolution")
            or block.get("jue_wiki_usage_contract_resolution"),
            limit=800,
        )
        if not resolution:
            return {}
        resolution_lower = resolution.lower()
        cross_checks: list[str] = []
        cross_check_tokens = {
            "live_quote": ("live_quote", "live quote", "현재가", "시세"),
            "account_state": ("account_state", "account state", "계좌", "잔고"),
            "risk_gate": ("risk_gate", "risk gate", "리스크", "게이트"),
            "fresh_research_conflicts": (
                "fresh_research_conflicts",
                "fresh research",
                "research conflict",
                "리서치",
                "자료 충돌",
            ),
            "current_price_structure": (
                "current_price_structure",
                "price structure",
                "가격 구조",
            ),
            "live_spread": ("live_spread", "live spread", "스프레드"),
            "funding": ("funding", "펀딩"),
            "liquidation_distance": (
                "liquidation_distance",
                "liquidation distance",
                "청산거리",
                "청산 거리",
            ),
            "orderbook_depth": ("orderbook_depth", "orderbook", "호가", "오더북"),
        }
        for canonical, aliases in cross_check_tokens.items():
            if any(alias.lower() in resolution_lower for alias in aliases):
                cross_checks.append(canonical)
        standalone_false = any(
            token in resolution_lower
            for token in (
                "단독 매매권한이 아니",
                "단독 매매 권한이 아니",
                "standalone_trade_authority=false",
                "no standalone trade authority",
                "not standalone trade authority",
                "not a standalone trade authority",
            )
        )
        if standalone_false and cross_checks:
            status = "resolved"
        elif not standalone_false:
            status = "missing_authority_statement"
        elif not cross_checks:
            status = "missing_cross_check"
        else:
            status = "unresolved"
        return {
            "status": status,
            "standalone_trade_authority": False if standalone_false else None,
            "cross_checks": cross_checks,
            "resolution": resolution,
            "policy_id": (
                "jue_wiki_usage_contract."
                f"{cls._policy_key_fragment(status) or 'unresolved'}"
            ),
        }

    @staticmethod
    def _period_memory_coverage_audit(block: dict[str, Any]) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        contract_audit = (
            metadata.get("period_memory_contract_audit")
            if isinstance(metadata.get("period_memory_contract_audit"), dict)
            else block.get("period_memory_contract_audit")
            if isinstance(block.get("period_memory_contract_audit"), dict)
            else {}
        )
        if contract_audit:
            status = _clean_text(contract_audit.get("status"), limit=120)
            status = status or "metadata_contract_gap"
            required_metadata = [
                _clean_text(value, limit=80)
                for value in list(contract_audit.get("required_metadata") or [])[:6]
                if _clean_text(value, limit=80)
            ]
            missing_metadata = [
                _clean_text(value, limit=80)
                for value in list(contract_audit.get("missing_metadata") or [])[:6]
                if _clean_text(value, limit=80)
            ]
            audit = {
                "status": status,
                "gap": _clean_text(contract_audit.get("gap"), limit=300),
                "override_reason": _clean_text(
                    contract_audit.get("override_reason"),
                    limit=300,
                ),
                "missing_metadata": missing_metadata,
                "required_metadata": required_metadata,
                "repair_action": _clean_text(
                    contract_audit.get("repair_action"),
                    limit=160,
                ),
                "policy_id": (
                    "period_memory_coverage."
                    f"{InvestmentMemoryService._policy_key_fragment(status) or 'metadata_contract_gap'}"
                ),
            }
            repair_note = _clean_text(
                contract_audit.get("metadata_contract_repair_note"),
                limit=500,
            )
            audit_resolution = _clean_text(
                contract_audit.get("metadata_contract_audit_resolution"),
                limit=300,
            )
            if repair_note:
                audit["metadata_contract_repair_note"] = repair_note
            if audit_resolution:
                audit["metadata_contract_audit_resolution"] = audit_resolution
            return audit
        gap = _clean_text(
            metadata.get("period_memory_coverage_gap")
            or block.get("period_memory_coverage_gap"),
            limit=300,
        )
        override_reason = _clean_text(
            metadata.get("period_memory_override_reason")
            or block.get("period_memory_override_reason"),
            limit=300,
        )
        repair_note = _clean_text(
            metadata.get("metadata_contract_repair_note")
            or block.get("metadata_contract_repair_note"),
            limit=500,
        )
        audit_resolution = _clean_text(
            metadata.get("metadata_contract_audit_resolution")
            or block.get("metadata_contract_audit_resolution"),
            limit=300,
        )
        if not gap and not override_reason:
            return {}
        status = "gap_overridden" if gap and override_reason else "gap_unresolved"
        policy_id = (
            "period_memory_coverage.gap_overridden"
            if status == "gap_overridden"
            else "period_memory_coverage.gap_unresolved"
        )
        audit = {
            "status": status,
            "gap": gap,
            "override_reason": override_reason,
            "policy_id": policy_id,
        }
        if repair_note:
            audit["metadata_contract_repair_note"] = repair_note
        if audit_resolution:
            audit["metadata_contract_audit_resolution"] = audit_resolution
        return audit

    @classmethod
    def _jue_wiki_execution_hint_audit(
        cls,
        block: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        adjustments = cls._jue_wiki_decision_adjustments_from_metadata(metadata)
        adjustment = next(
            (
                row
                for row in adjustments
                if isinstance(row, dict)
                and cls._jue_wiki_adjustment_execution_hint(row)
            ),
            {},
        )
        if not adjustment:
            return {}
        execution_hint = cls._jue_wiki_adjustment_execution_hint(adjustment)
        if not execution_hint:
            return {}
        resolution = (
            metadata.get("jue_wiki_decision_adjustment_resolution")
            if isinstance(metadata.get("jue_wiki_decision_adjustment_resolution"), dict)
            else metadata.get("decision_adjustment_resolution")
            if isinstance(metadata.get("decision_adjustment_resolution"), dict)
            else metadata.get("jue_wiki_execution_hint_resolution")
            if isinstance(metadata.get("jue_wiki_execution_hint_resolution"), dict)
            else {}
        )
        evidence_grade = (
            adjustment.get("evidence_grade")
            if isinstance(adjustment.get("evidence_grade"), dict)
            else {}
        )
        expected = cls._expected_execution_for_wiki_hint(
            execution_hint,
            adjustment=adjustment,
        )
        actual = _clean_text(
            resolution.get("status")
            or resolution.get("action")
            or resolution.get("decision")
            or metadata.get("execution_mode")
            or metadata.get("entry_mode")
            or block.get("created_by"),
            limit=120,
        )
        audit_status = cls._jue_wiki_execution_hint_status(
            execution_hint=execution_hint,
            expected=expected,
            actual=actual,
            resolution=resolution,
            block=block,
        )
        policy_fragment = cls._policy_key_fragment(execution_hint)
        if not policy_fragment:
            return {}
        return {
            "execution_hint": execution_hint,
            "status": audit_status,
            "expected": expected,
            "actual": actual or "unknown",
            "evidence_grade_status": _clean_text(
                evidence_grade.get("status"),
                limit=80,
            ),
            "policy_id": f"jue_wiki_execution_hint.{policy_fragment}",
        }

    @staticmethod
    def _jue_wiki_adjustment_execution_hint(
        adjustment: dict[str, Any],
    ) -> str:
        direct = _clean_text(adjustment.get("execution_hint"), limit=120)
        if direct:
            return direct
        for key in (
            "decision_adjustment_audit_effectiveness",
            "decision_adjustment_effectiveness",
        ):
            payload = adjustment.get(key)
            if not isinstance(payload, dict):
                continue
            nested = _clean_text(payload.get("execution_hint"), limit=120)
            if nested:
                return nested
        evidence_grade = adjustment.get("evidence_grade")
        if not isinstance(evidence_grade, dict):
            return ""
        instruction = str(evidence_grade.get("instruction") or "").strip().lower()
        if instruction == "audit_or_repair_probe_only":
            return "cap_to_audit_or_repair_probe"
        if instruction == "usable_with_live_cross_check":
            return "allow_live_cross_checked_execution"
        return ""

    @staticmethod
    def _jue_wiki_decision_adjustments_from_metadata(
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates: list[Any] = [
            metadata.get("jue_wiki_decision_adjustments"),
            metadata.get("decision_adjustments"),
        ]
        jue_wiki = metadata.get("jue_wiki") if isinstance(metadata.get("jue_wiki"), dict) else {}
        candidates.append(jue_wiki.get("decision_adjustments"))
        application = (
            metadata.get("jue_wiki_application")
            if isinstance(metadata.get("jue_wiki_application"), dict)
            else {}
        )
        candidates.append(application.get("decision_adjustments"))
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            if isinstance(candidate, list):
                rows.extend(row for row in candidate if isinstance(row, dict))
            elif isinstance(candidate, dict):
                rows.append(candidate)
        return rows

    @staticmethod
    def _expected_execution_for_wiki_hint(
        execution_hint: str,
        *,
        adjustment: dict[str, Any],
    ) -> str:
        clean_hint = str(execution_hint or "").strip().lower()
        action = _clean_text(adjustment.get("action"), limit=120)
        if clean_hint == "cap_to_audit_or_repair_probe":
            return action or "audit_or_repair_probe_only"
        if clean_hint == "allow_live_cross_checked_execution":
            return "live_cross_checked_execution"
        if clean_hint == "reduce_size_and_require_live_cross_check":
            return "reduced_size_live_cross_check"
        return action or clean_hint

    @staticmethod
    def _jue_wiki_execution_hint_status(
        *,
        execution_hint: str,
        expected: str,
        actual: str,
        resolution: dict[str, Any],
        block: dict[str, Any],
    ) -> str:
        clean_hint = str(execution_hint or "").strip().lower()
        clean_actual = str(actual or "").strip().lower()
        clean_expected = str(expected or "").strip().lower()
        resolution_text = " ".join(
            str(value or "").strip().lower()
            for value in (
                resolution.get("status"),
                resolution.get("action"),
                resolution.get("decision"),
                resolution.get("reason"),
            )
            if value not in (None, "", [], {})
        )
        combined = f"{clean_actual} {resolution_text}".strip()
        if not combined:
            return "unknown"
        if clean_hint == "cap_to_audit_or_repair_probe":
            if any(token in combined for token in ("audit", "repair", "probe")):
                return "followed"
            if any(
                token in combined
                for token in ("live", "create", "normal", "full", "scale")
            ):
                return "violated"
            return "unknown"
        if clean_hint == "allow_live_cross_checked_execution":
            if "cross" in combined or "checked" in combined or "live" in combined:
                return "followed"
            return "unknown"
        if clean_hint == "reduce_size_and_require_live_cross_check":
            qty = _safe_float(block.get("qty_initial") or block.get("qty_open"))
            adjusted_from = _safe_float(
                (block.get("metadata") if isinstance(block.get("metadata"), dict) else {})
                .get("policy_adjusted_qty_from")
            )
            if (
                "cross" in combined
                or "checked" in combined
                or (adjusted_from > 0 and qty > 0 and qty < adjusted_from)
            ):
                return "followed"
            if "live" in combined:
                return "violated"
            return "unknown"
        if clean_expected and clean_expected in combined:
            return "followed"
        return "unknown"

    def _exit_from_orders(
        self,
        block: dict[str, Any],
        orders: list[dict[str, Any]],
    ) -> tuple[float, str]:
        block_id = str(block.get("block_id") or "")
        side = str(block.get("side") or "long").strip().lower()
        exit_side = "buy" if side == "short" else "sell"
        exit_orders = [
            row
            for row in orders
            if str(row.get("block_id") or "") == block_id
            and str(row.get("side") or "").lower() == exit_side
            and str(row.get("reason") or "").strip() != "entry_order"
        ]
        if not exit_orders:
            exit_orders = [
                row
                for row in orders
                if str(row.get("block_id") or "") == block_id
                and str(row.get("reason") or "").strip() != "entry_order"
            ]
        if not exit_orders:
            return 0.0, _clean_text(block.get("llm_reason"), limit=160)
        latest = exit_orders[0]
        for row in exit_orders:
            if str(row.get("created_at") or "") > str(latest.get("created_at") or ""):
                latest = row
        response = latest.get("response") if isinstance(latest.get("response"), dict) else {}
        price = _safe_float(
            latest.get("avg_fill_price")
            or response.get("avg_fill_price")
            or response.get("avgPrice")
            or latest.get("limit_price")
        )
        reason = _clean_text(latest.get("reason") or block.get("llm_reason"), limit=160)
        return price, reason

    @staticmethod
    def _policy_for_reflection(*, status: str, pnl_pct: float, exit_reason: str) -> tuple[str, str]:
        reason = str(exit_reason or "").lower()
        if status == "error":
            return "review_order_and_data_failures", "caution"
        if pnl_pct > 0 and ("target" in reason or "profit" in reason):
            return "protect_winning_blocks", "preference"
        if pnl_pct < 0:
            return "respect_defined_stops", "caution"
        return "wait_for_clean_block_validation", "observation"

    def _reflection_message(self, reflections: list[dict[str, Any]]) -> str:
        lines = ["# 블록 거래 반성", ""]
        for row in reflections:
            lines.append(
                f"- {_symbol_display_label(row)}: "
                f"{_safe_float(row.get('pnl_pct')):+.2f}% · {row.get('exit_reason') or row.get('status')}"
            )
        lines.extend(["", "쥬는 결과보다 블록 약속 준수, 가격 경로, 다음 원칙 후보를 분리해서 기억합니다."])
        return "\n".join(lines)

    def _refresh_policy_scorecards(self) -> None:
        reflections = self.repository.list_block_reflections(limit=1000)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for reflection in reflections:
            metrics = reflection.get("metrics") if isinstance(reflection.get("metrics"), dict) else {}
            policy_id = str(metrics.get("policy_id") or "wait_for_clean_block_validation")
            policy_ids = [
                policy_id,
                *self._validation_policy_ids_from_reflection(reflection),
                *self._jue_wiki_execution_hint_policy_ids_from_reflection(reflection),
                *self._jue_wiki_usage_contract_policy_ids_from_reflection(reflection),
                *self._period_memory_coverage_policy_ids_from_reflection(reflection),
            ]
            for row_policy_id in dict.fromkeys(policy_ids):
                grouped.setdefault(row_policy_id, []).append(reflection)
        for policy_id, rows in grouped.items():
            scorecard = self._scorecard_from_reflections(rows, policy_id=policy_id)
            if scorecard:
                self.repository.upsert_policy_scorecard(scorecard)
        self.sync_policy_rules()

    @staticmethod
    def _validation_policy_ids_from_reflection(reflection: dict[str, Any]) -> list[str]:
        metrics = reflection.get("metrics") if isinstance(reflection.get("metrics"), dict) else {}
        live_authority = (
            metrics.get("live_authority")
            if isinstance(metrics.get("live_authority"), dict)
            else {}
        )
        failed_disciplines = live_authority.get("failed_disciplines")
        if not isinstance(failed_disciplines, list):
            failed_disciplines = []
        discipline_matrix = (
            live_authority.get("discipline_matrix")
            if isinstance(live_authority.get("discipline_matrix"), dict)
            else {}
        )
        matrix_statuses = (
            discipline_matrix.get("statuses")
            if isinstance(discipline_matrix.get("statuses"), list)
            else []
        )
        matrix_failed_disciplines = [
            row
            for row in matrix_statuses
            if isinstance(row, dict)
            and str(row.get("status") or "").strip().lower() == "fail"
        ]
        validation_passport = (
            live_authority.get("validation_passport")
            if isinstance(live_authority.get("validation_passport"), dict)
            else {}
        )
        passport_failed_ids = (
            validation_passport.get("failed_ids")
            if isinstance(validation_passport.get("failed_ids"), list)
            else []
        )
        policy_ids: list[str] = []
        for row in [*failed_disciplines, *matrix_failed_disciplines]:
            if not isinstance(row, dict):
                continue
            discipline_id = re.sub(
                r"[^a-z0-9_]+",
                "_",
                str(row.get("id") or row.get("label") or "").strip().lower(),
            ).strip("_")
            if not discipline_id:
                continue
            policy_ids.append(f"validation.{discipline_id}")
        for raw_id in passport_failed_ids:
            discipline_id = InvestmentMemoryService._policy_key_fragment(raw_id)
            if not discipline_id:
                continue
            policy_ids.append(f"validation.{discipline_id}")
        attribution = (
            live_authority.get("failure_attribution")
            if isinstance(live_authority.get("failure_attribution"), dict)
            else {}
        )
        worst_groups = (
            attribution.get("worst_groups")
            if isinstance(attribution.get("worst_groups"), list)
            else []
        )
        for row in worst_groups:
            if not isinstance(row, dict):
                continue
            group_type = InvestmentMemoryService._policy_key_fragment(row.get("group_type"))
            group = InvestmentMemoryService._policy_key_fragment(row.get("group"))
            if not group_type or not group:
                continue
            policy_ids.append(f"validation_attribution.{group_type}.{group}")
        recovery_focus = live_authority.get("validation_recovery_focus")
        if isinstance(recovery_focus, list):
            for row in recovery_focus:
                if not isinstance(row, dict):
                    continue
                source = InvestmentMemoryService._policy_key_fragment(row.get("source"))
                reason = InvestmentMemoryService._policy_key_fragment(row.get("reason"))
                if not source or not reason:
                    continue
                policy_ids.append(f"validation_recovery.{source}.{reason}")
        repair_enforcement = (
            metrics.get("validation_repair_enforcement")
            if isinstance(metrics.get("validation_repair_enforcement"), dict)
            else {}
        )
        repair_action_ids = (
            repair_enforcement.get("repair_action_ids")
            if isinstance(repair_enforcement.get("repair_action_ids"), list)
            else []
        )
        for raw_action_id in repair_action_ids:
            action_fragment = InvestmentMemoryService._policy_key_fragment(raw_action_id)
            if action_fragment:
                policy_ids.append(
                    f"validation_repair_enforcement.{action_fragment}"
                )
        if not repair_action_ids:
            repair_reason = InvestmentMemoryService._policy_key_fragment(
                repair_enforcement.get("reason")
            )
            if repair_reason:
                policy_ids.append(f"validation_repair_enforcement.{repair_reason}")
        lane_authority_gate = (
            metrics.get("lane_authority_gate")
            if isinstance(metrics.get("lane_authority_gate"), dict)
            else {}
        )
        scale_blockers = (
            lane_authority_gate.get("scale_blockers")
            if isinstance(lane_authority_gate.get("scale_blockers"), list)
            else []
        )
        passport = (
            lane_authority_gate.get("risk_budget_passport")
            if isinstance(lane_authority_gate.get("risk_budget_passport"), dict)
            else {}
        )
        passport_blockers = (
            passport.get("scale_blockers")
            if isinstance(passport.get("scale_blockers"), list)
            else []
        )
        for raw_blocker in [*scale_blockers, *passport_blockers]:
            blocker_fragment = InvestmentMemoryService._policy_key_fragment(raw_blocker)
            if blocker_fragment:
                policy_ids.append(f"lane_scale.{blocker_fragment}")
        weak_lane_sources = (
            lane_authority_gate.get("weak_lane_sources")
            if isinstance(lane_authority_gate.get("weak_lane_sources"), list)
            else []
        )
        for raw_source in weak_lane_sources:
            source_fragment = InvestmentMemoryService._policy_key_fragment(raw_source)
            blocker = LANE_WEAK_SOURCE_SCALE_BLOCKERS.get(source_fragment, "")
            if blocker:
                policy_ids.append(f"lane_scale.{blocker}")
        costs = metrics.get("costs") if isinstance(metrics.get("costs"), dict) else {}
        cost_precision = _clean_text(costs.get("cost_precision"), limit=80).lower()
        cost_source = _clean_text(costs.get("source"), limit=80).lower()
        missing_cost_components = (
            costs.get("missing_cost_components")
            if isinstance(costs.get("missing_cost_components"), list)
            else []
        )
        if (
            (missing_cost_components and cost_source != "missing")
            or cost_precision in {
                "partial",
                "unverified_cost",
                "estimated",
            }
        ):
            policy_ids.append("lane_scale.cost_evidence_repair")
        return list(dict.fromkeys(policy_ids))

    @staticmethod
    def _jue_wiki_execution_hint_policy_ids_from_reflection(
        reflection: dict[str, Any],
    ) -> list[str]:
        metrics = reflection.get("metrics") if isinstance(reflection.get("metrics"), dict) else {}
        audit = (
            metrics.get("jue_wiki_execution_hint_audit")
            if isinstance(metrics.get("jue_wiki_execution_hint_audit"), dict)
            else {}
        )
        policy_id = _clean_text(audit.get("policy_id"), limit=180)
        if not policy_id.startswith("jue_wiki_execution_hint."):
            return []
        return [policy_id]

    @staticmethod
    def _period_memory_coverage_policy_ids_from_reflection(
        reflection: dict[str, Any],
    ) -> list[str]:
        metrics = reflection.get("metrics") if isinstance(reflection.get("metrics"), dict) else {}
        audit = (
            metrics.get("period_memory_coverage_audit")
            if isinstance(metrics.get("period_memory_coverage_audit"), dict)
            else {}
        )
        policy_id = _clean_text(audit.get("policy_id"), limit=180)
        if not policy_id.startswith("period_memory_coverage."):
            return []
        return [policy_id]

    @staticmethod
    def _jue_wiki_usage_contract_policy_ids_from_reflection(
        reflection: dict[str, Any],
    ) -> list[str]:
        metrics = reflection.get("metrics") if isinstance(reflection.get("metrics"), dict) else {}
        audit = (
            metrics.get("jue_wiki_usage_contract_audit")
            if isinstance(metrics.get("jue_wiki_usage_contract_audit"), dict)
            else {}
        )
        policy_id = _clean_text(audit.get("policy_id"), limit=180)
        if not policy_id.startswith("jue_wiki_usage_contract."):
            return []
        return [policy_id]

    @staticmethod
    def _policy_key_fragment(value: Any) -> str:
        return re.sub(
            r"[^a-z0-9_]+",
            "_",
            str(value or "").strip().lower(),
        ).strip("_")

    def _group_reflection_metrics(self, rows: list[dict[str, Any]], *, key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            value = str(metrics.get(key) or row.get(key) or "unknown")
            grouped.setdefault(value, []).append(row)
        return {
            group_key: self._reflection_group_stats(group_rows)
            for group_key, group_rows in grouped.items()
        }

    def _group_reflection_lanes(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            market = str(metrics.get("market") or row.get("market") or "unknown")
            side = str(metrics.get("side") or row.get("side") or "unknown")
            grouped.setdefault(f"{market}:{side}", []).append(row)
        return {
            group_key: self._reflection_group_stats(group_rows)
            for group_key, group_rows in grouped.items()
        }

    @staticmethod
    def _reflection_group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pnl_values = [_safe_float(row.get("pnl_pct")) for row in rows]
        wins = [value for value in pnl_values if value > 0]
        return {
            "sample_count": len(rows),
            "win_rate": len(wins) / len(rows) if rows else 0.0,
            "avg_pnl_pct": sum(pnl_values) / len(rows) if rows else 0.0,
            "rule_follow_rate": (
                sum(1 for row in rows if bool(row.get("rule_followed"))) / len(rows)
                if rows
                else 0.0
            ),
        }

    @staticmethod
    def _count_reflection_values(rows: list[dict[str, Any]], *, key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts

    @staticmethod
    def _top_count_map(counts: dict[str, int], *, limit: int = 6) -> dict[str, int]:
        return {
            key: value
            for key, value in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[: max(int(limit), 1)]
            if key
        }

    @classmethod
    def _lane_scale_evidence(
        cls,
        rows: list[dict[str, Any]],
        *,
        scale_blocker: str,
    ) -> dict[str, Any]:
        clean_blocker = _clean_text(scale_blocker, limit=120)
        if not clean_blocker:
            return {}
        blocker_counts: dict[str, int] = {}
        repair_counts: dict[str, int] = {}
        action_counts: dict[str, int] = {}
        decision_counts: dict[str, int] = {}
        weak_source_counts: dict[str, int] = {}
        representative_gate: dict[str, Any] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            costs = metrics.get("costs") if isinstance(metrics.get("costs"), dict) else {}
            if clean_blocker == "cost_evidence_repair" and costs:
                cost_precision = _clean_text(costs.get("cost_precision"), limit=80)
                cost_source = _clean_text(costs.get("source"), limit=80).lower()
                missing_components = [
                    _clean_text(value, limit=80)
                    for value in list(costs.get("missing_cost_components") or [])
                    if _clean_text(value, limit=80)
                ]
                cost_reason = _clean_text(
                    costs.get("cost_precision_reason"),
                    limit=140,
                )
                cost_audit_matches = bool(
                    (missing_components and cost_source != "missing")
                    or cost_precision.lower()
                    in {"partial", "unverified_cost", "estimated"}
                )
                if cost_audit_matches:
                    blocker_counts[clean_blocker] = (
                        blocker_counts.get(clean_blocker, 0) + 1
                    )
                    if cost_precision:
                        decision_counts[f"cost_precision:{cost_precision}"] = (
                            decision_counts.get(f"cost_precision:{cost_precision}", 0)
                            + 1
                        )
                    if cost_reason:
                        action_counts[f"cost_reason:{cost_reason}"] = (
                            action_counts.get(f"cost_reason:{cost_reason}", 0) + 1
                        )
                    for component in missing_components:
                        repair = f"record_missing_cost_component:{component}"
                        repair_counts[repair] = repair_counts.get(repair, 0) + 1
                    if not representative_gate:
                        representative_gate = {
                            "source": "reflection_cost_audit",
                            "cost_precision": cost_precision,
                            "cost_precision_reason": cost_reason,
                            "required_cost_components": costs.get(
                                "required_cost_components"
                            )
                            or [],
                            "present_cost_components": costs.get(
                                "present_cost_components"
                            )
                            or [],
                            "missing_cost_components": missing_components,
                        }
            gate = (
                metrics.get("lane_authority_gate")
                if isinstance(metrics.get("lane_authority_gate"), dict)
                else {}
            )
            if not gate:
                continue
            blockers = [
                _clean_text(value, limit=120)
                for value in list(gate.get("scale_blockers") or [])
                if _clean_text(value, limit=120)
            ]
            passport = (
                gate.get("risk_budget_passport")
                if isinstance(gate.get("risk_budget_passport"), dict)
                else {}
            )
            blockers.extend(
                _clean_text(value, limit=120)
                for value in list(passport.get("scale_blockers") or [])
                if _clean_text(value, limit=120)
            )
            blockers = list(dict.fromkeys(blockers))
            weak_sources = [
                cls._policy_key_fragment(value)
                for value in list(gate.get("weak_lane_sources") or [])
                if cls._policy_key_fragment(value)
            ]
            source_blockers = [
                LANE_WEAK_SOURCE_SCALE_BLOCKERS.get(source, "")
                for source in weak_sources
            ]
            source_blockers = [value for value in source_blockers if value]
            if clean_blocker not in blockers and clean_blocker not in source_blockers:
                continue
            for blocker in blockers:
                blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
            if clean_blocker in source_blockers:
                blocker_counts[clean_blocker] = blocker_counts.get(clean_blocker, 0) + 1
            for source in weak_sources:
                if LANE_WEAK_SOURCE_SCALE_BLOCKERS.get(source) == clean_blocker:
                    weak_source_counts[source] = weak_source_counts.get(source, 0) + 1
            row_repairs: list[str] = []
            for key in (
                "scale_repair_targets",
                "cost_repair_targets",
                "entry_repair_targets",
            ):
                for target in list(gate.get(key) or []):
                    repair = _clean_text(target, limit=180)
                    if repair:
                        row_repairs.append(repair)
            for target in list(passport.get("scale_repair_targets") or []):
                repair = _clean_text(target, limit=180)
                if repair:
                    row_repairs.append(repair)
            for repair in dict.fromkeys(row_repairs):
                    repair_counts[repair] = repair_counts.get(repair, 0) + 1
            for key, counts in (
                ("action", action_counts),
                ("scale_decision", decision_counts),
            ):
                value = _clean_text(gate.get(key), limit=120)
                if value:
                    counts[value] = counts.get(value, 0) + 1
            if not representative_gate:
                representative_gate = {
                    key: gate.get(key)
                    for key in (
                        "matched_lane",
                        "action",
                        "reason",
                        "scale_decision",
                        "scale_up_allowed",
                        "requires_waiting_entry",
                        "applied_max_budget_multiplier",
                        "max_budget_multiplier",
                    )
                    if gate.get(key) not in (None, "", [], {})
                }
                if weak_sources:
                    representative_gate["weak_lane_sources"] = weak_sources[:6]
        evidence = {
            "scale_blocker_counts": cls._top_count_map(blocker_counts),
            "weak_lane_source_counts": cls._top_count_map(weak_source_counts),
            "scale_repair_target_counts": cls._top_count_map(repair_counts),
            "lane_action_counts": cls._top_count_map(action_counts),
            "scale_decision_counts": cls._top_count_map(decision_counts),
            "representative_gate": representative_gate,
        }
        return {
            key: value
            for key, value in evidence.items()
            if value not in ({}, [], "", None)
        }

    @classmethod
    def _jue_wiki_execution_hint_evidence(
        cls,
        rows: list[dict[str, Any]],
        *,
        execution_hint: str,
    ) -> dict[str, Any]:
        clean_hint = _clean_text(execution_hint, limit=120)
        if not clean_hint:
            return {}
        status_counts: dict[str, int] = {}
        expected_counts: dict[str, int] = {}
        actual_counts: dict[str, int] = {}
        evidence_grade_counts: dict[str, int] = {}
        representative: dict[str, Any] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            audit = (
                metrics.get("jue_wiki_execution_hint_audit")
                if isinstance(metrics.get("jue_wiki_execution_hint_audit"), dict)
                else {}
            )
            if _clean_text(audit.get("execution_hint"), limit=120) != clean_hint:
                continue
            status = _clean_text(audit.get("status"), limit=80) or "unknown"
            expected = _clean_text(audit.get("expected"), limit=120) or "unknown"
            actual = _clean_text(audit.get("actual"), limit=120) or "unknown"
            grade_status = (
                _clean_text(audit.get("evidence_grade_status"), limit=80)
                or "unknown"
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            expected_counts[expected] = expected_counts.get(expected, 0) + 1
            actual_counts[actual] = actual_counts.get(actual, 0) + 1
            evidence_grade_counts[grade_status] = (
                evidence_grade_counts.get(grade_status, 0) + 1
            )
            if not representative:
                representative = {
                    "block_id": row.get("block_id"),
                    "symbol": row.get("symbol"),
                    "status": status,
                    "expected": expected,
                    "actual": actual,
                    "pnl_pct": row.get("pnl_pct"),
                }
        total = sum(status_counts.values())
        followed = status_counts.get("followed", 0)
        violated = status_counts.get("violated", 0)
        evidence = {
            "hint_status_counts": cls._top_count_map(status_counts),
            "hint_expected_counts": cls._top_count_map(expected_counts),
            "hint_actual_counts": cls._top_count_map(actual_counts),
            "hint_evidence_grade_counts": cls._top_count_map(evidence_grade_counts),
            "hint_followed_count": followed,
            "hint_violation_count": violated,
            "hint_follow_rate": followed / total if total else 0.0,
            "representative_hint_audit": representative,
        }
        return {
            key: value
            for key, value in evidence.items()
            if value not in ({}, [], "", None)
        }

    @classmethod
    def _jue_wiki_usage_contract_evidence(
        cls,
        rows: list[dict[str, Any]],
        *,
        status: str,
    ) -> dict[str, Any]:
        clean_status = _clean_text(status, limit=120)
        if not clean_status:
            return {}
        status_counts: dict[str, int] = {}
        cross_check_counts: dict[str, int] = {}
        authority_counts: dict[str, int] = {}
        representative: dict[str, Any] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            audit = (
                metrics.get("jue_wiki_usage_contract_audit")
                if isinstance(metrics.get("jue_wiki_usage_contract_audit"), dict)
                else {}
            )
            if _clean_text(audit.get("status"), limit=120) != clean_status:
                continue
            audit_status = _clean_text(audit.get("status"), limit=120) or "unknown"
            status_counts[audit_status] = status_counts.get(audit_status, 0) + 1
            authority_key = (
                "no_standalone_authority"
                if audit.get("standalone_trade_authority") is False
                else "authority_statement_missing"
            )
            authority_counts[authority_key] = authority_counts.get(authority_key, 0) + 1
            for raw_value in list(audit.get("cross_checks") or [])[:12]:
                value = _clean_text(raw_value, limit=80)
                if value:
                    cross_check_counts[value] = cross_check_counts.get(value, 0) + 1
            if not representative:
                representative = {
                    "block_id": row.get("block_id"),
                    "symbol": row.get("symbol"),
                    "status": audit_status,
                    "cross_checks": list(audit.get("cross_checks") or [])[:8],
                    "standalone_trade_authority": audit.get(
                        "standalone_trade_authority"
                    ),
                    "pnl_pct": row.get("pnl_pct"),
                }
        total = sum(status_counts.values())
        evidence = {
            "usage_contract_status_counts": cls._top_count_map(status_counts),
            "usage_contract_cross_check_counts": cls._top_count_map(cross_check_counts),
            "usage_contract_authority_counts": cls._top_count_map(authority_counts),
            "usage_contract_resolved_count": status_counts.get("resolved", 0),
            "usage_contract_missing_cross_check_count": status_counts.get(
                "missing_cross_check",
                0,
            ),
            "usage_contract_missing_authority_count": status_counts.get(
                "missing_authority_statement",
                0,
            ),
            "usage_contract_resolution_rate": (
                status_counts.get("resolved", 0) / total if total else 0.0
            ),
            "representative_usage_contract_audit": representative,
        }
        return {
            key: value
            for key, value in evidence.items()
            if value not in ({}, [], "", None)
        }

    @classmethod
    def _validation_pressure_action_evidence(
        cls,
        rows: list[dict[str, Any]],
        *,
        discipline_id: str,
    ) -> dict[str, Any]:
        clean_discipline = _clean_text(discipline_id, limit=100)
        if "." in clean_discipline:
            scope, scoped_discipline = clean_discipline.split(".", 1)
            if scope in MEMORY_SCOPES:
                clean_discipline = scoped_discipline
        if not clean_discipline:
            return {}
        representative: dict[str, Any] = {}
        entry_counts: dict[str, int] = {}
        sizing_counts: dict[str, int] = {}
        repair_counts: dict[str, int] = {}
        focus_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        entry_posture_counts: dict[str, int] = {}
        sizing_posture_counts: dict[str, int] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            live_authority = (
                metrics.get("live_authority")
                if isinstance(metrics.get("live_authority"), dict)
                else {}
            )
            pressure = (
                live_authority.get("validation_pressure")
                if isinstance(live_authority.get("validation_pressure"), dict)
                else {}
            )
            if not pressure:
                continue
            for key, counts in (
                ("severity", severity_counts),
                ("entry_posture", entry_posture_counts),
                ("sizing_posture", sizing_posture_counts),
            ):
                value = _clean_text(pressure.get(key), limit=100)
                if value:
                    counts[value] = counts.get(value, 0) + 1
            actions = (
                pressure.get("discipline_actions")
                if isinstance(pressure.get("discipline_actions"), list)
                else []
            )
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_id = _clean_text(action.get("id"), limit=100)
                if action_id != clean_discipline:
                    continue
                compact_action = {
                    key: _clean_text(action.get(key), limit=180)
                    for key in (
                        "id",
                        "status",
                        "entry_constraint",
                        "sizing_constraint",
                        "repair_action",
                        "block_design_focus",
                    )
                    if _clean_text(action.get(key), limit=180)
                }
                if compact_action and not representative:
                    representative = compact_action
                for key, counts in (
                    ("entry_constraint", entry_counts),
                    ("sizing_constraint", sizing_counts),
                    ("repair_action", repair_counts),
                    ("block_design_focus", focus_counts),
                    ("status", status_counts),
                ):
                    value = _clean_text(action.get(key), limit=180)
                    if value:
                        counts[value] = counts.get(value, 0) + 1
        evidence = {
            "discipline_action": representative,
            "entry_constraint_counts": cls._top_count_map(entry_counts),
            "sizing_constraint_counts": cls._top_count_map(sizing_counts),
            "repair_action_counts": cls._top_count_map(repair_counts),
            "block_design_focus_counts": cls._top_count_map(focus_counts),
            "status_counts": cls._top_count_map(status_counts),
            "pressure_severity_counts": cls._top_count_map(severity_counts),
            "entry_posture_counts": cls._top_count_map(entry_posture_counts),
            "sizing_posture_counts": cls._top_count_map(sizing_posture_counts),
        }
        return {
            key: value
            for key, value in evidence.items()
            if value not in ({}, [], "", None)
        }

    @staticmethod
    def _merge_validation_pressure_effect(
        effect: dict[str, Any],
        scorecard: dict[str, Any],
    ) -> dict[str, Any]:
        action = (
            scorecard.get("validation_pressure_action")
            if isinstance(scorecard.get("validation_pressure_action"), dict)
            else {}
        )
        if not action:
            return effect
        merged = dict(effect)
        field_map = {
            "entry_constraint": "validation_pressure_entry_constraint",
            "sizing_constraint": "validation_pressure_sizing_constraint",
            "repair_action": "validation_pressure_repair_action",
            "block_design_focus": "validation_pressure_block_design_focus",
            "status": "validation_pressure_status",
        }
        for source_key, target_key in field_map.items():
            value = _clean_text(action.get(source_key), limit=220)
            if value:
                merged[target_key] = value
        repair_action = _clean_text(action.get("repair_action"), limit=180)
        focus = _clean_text(action.get("block_design_focus"), limit=180)
        note_parts = []
        if repair_action:
            note_parts.append(f"복구 과제: {repair_action}")
        if focus:
            note_parts.append(f"블록 설계 초점: {focus}")
        if note_parts:
            current_note = _clean_text(merged.get("risk_note"), limit=500)
            pressure_note = " / ".join(note_parts)
            merged["risk_note"] = (
                f"{current_note} {pressure_note}"
                if current_note
                else pressure_note
            )
        return merged

    @staticmethod
    def _merge_validation_pass_path_effect(
        effect: dict[str, Any],
        scorecard: dict[str, Any],
    ) -> dict[str, Any]:
        pass_path = (
            scorecard.get("validation_pass_path")
            if isinstance(scorecard.get("validation_pass_path"), dict)
            else {}
        )
        if not pass_path:
            return effect
        merged = dict(effect)
        behavior = (
            pass_path.get("jue_behavior_until_pass")
            if isinstance(pass_path.get("jue_behavior_until_pass"), dict)
            else {}
        )
        required_evidence = (
            pass_path.get("required_evidence")
            if isinstance(pass_path.get("required_evidence"), dict)
            else {}
        )
        runtime = (
            pass_path.get("m1_runtime_profile")
            if isinstance(pass_path.get("m1_runtime_profile"), dict)
            else {}
        )
        current_gap = _clean_text(pass_path.get("current_gap"), limit=100)
        collection_hook = _clean_text(pass_path.get("collection_hook"), limit=140)
        pass_criteria = _clean_text(pass_path.get("pass_criteria"), limit=260)
        allowed_posture = _clean_text(
            behavior.get("allowed_entry_posture"),
            limit=140,
        )
        blocks_scaling = _clean_text(behavior.get("blocks_scaling"), limit=160)
        blocks_new_entries = _clean_text(
            behavior.get("blocks_new_entries"),
            limit=160,
        )
        if current_gap:
            merged["validation_pass_current_gap"] = current_gap
        if collection_hook:
            merged["pass_collection_hook"] = collection_hook
        if pass_criteria:
            merged["pass_criteria"] = pass_criteria
        if required_evidence:
            merged["pass_required_evidence"] = required_evidence
        if allowed_posture:
            merged["pass_allowed_entry_posture"] = allowed_posture
        if blocks_scaling:
            merged["pass_blocks_scaling"] = blocks_scaling
        if blocks_new_entries:
            merged["pass_blocks_new_entries"] = blocks_new_entries
        if bool(behavior.get("scale_up_blocked")):
            merged["scale_up_blocked_until_pass_path"] = True
        if bool(behavior.get("live_shadow_required")):
            merged["require_shadow_validation"] = True
        if runtime:
            merged["m1_runtime_profile"] = runtime
        note_parts: list[str] = []
        if collection_hook:
            note_parts.append(f"수집 hook: {collection_hook}")
        if pass_criteria:
            note_parts.append(f"통과 기준: {pass_criteria}")
        if allowed_posture:
            note_parts.append(f"통과 전 진입: {allowed_posture}")
        if note_parts:
            current_note = _clean_text(merged.get("risk_note"), limit=500)
            pass_note = " / ".join(note_parts)
            merged["risk_note"] = (
                f"{current_note} {pass_note}"
                if current_note
                else pass_note
            )
        return merged

    @staticmethod
    def _merge_period_memory_repair_effect(
        effect: dict[str, Any],
        scorecard: dict[str, Any],
    ) -> dict[str, Any]:
        missing_metadata = _compact_text_list(
            scorecard.get("period_memory_missing_metadata"),
            limit=6,
            item_limit=80,
        )
        repair_actions = _compact_text_list(
            scorecard.get("period_memory_repair_actions"),
            limit=6,
            item_limit=160,
        )
        audit_resolutions = _compact_text_list(
            scorecard.get("metadata_contract_audit_resolutions"),
            limit=6,
            item_limit=180,
        )
        repair_notes = _compact_text_list(
            scorecard.get("metadata_contract_repair_notes"),
            limit=4,
            item_limit=300,
        )
        contract_gap_count = (
            _safe_int(scorecard.get("period_memory_contract_gap_count"))
            if scorecard.get("period_memory_contract_gap_count") not in (None, "")
            else 0
        )
        if (
            not missing_metadata
            and not repair_actions
            and not audit_resolutions
            and not repair_notes
            and not contract_gap_count
        ):
            return effect
        merged = dict(effect)
        if contract_gap_count:
            merged["period_memory_contract_gap_count"] = contract_gap_count
        if missing_metadata:
            merged["period_memory_missing_metadata"] = missing_metadata
        if repair_actions:
            merged["period_memory_repair_actions"] = repair_actions
        if audit_resolutions:
            merged["metadata_contract_audit_resolutions"] = audit_resolutions
        if repair_notes:
            merged["metadata_contract_repair_notes"] = repair_notes
        if (
            str(scorecard.get("policy_id") or "")
            == "period_memory_coverage.gap_overridden"
            and str(scorecard.get("status") or "") == "active_preference"
            and str(scorecard.get("action") or "") == "prefer"
            and _safe_int(scorecard.get("sample_count")) >= 5
            and _safe_float(scorecard.get("confidence")) >= 0.7
            and _safe_float(scorecard.get("win_rate")) >= 0.6
            and _safe_float(scorecard.get("avg_pnl_pct")) > 0
            and _safe_float(scorecard.get("expectancy_pct")) > 0
            and repair_notes
            and audit_resolutions
        ):
            merged.update({
                "period_memory_repair_quality": "successful_repair",
                "entry_bias": "repaired_gap_cross_checked_execution",
                "sizing_policy": "normal_size_after_successful_period_memory_repair",
                "risk_budget_multiplier": 1.0,
                "max_budget_multiplier": 1.0,
                "target_stop_review": "keep_gap_override_and_live_cross_check_visible",
                "risk_note": (
                    "기간 메모리 공백을 명시하고 override 사유와 수리 노트를 남긴 "
                    "케이스의 성과가 안정적이면 자동 축소로 고정하지 않는다. "
                    "다만 gap, override, live cross-check는 계속 metadata에 남긴다."
                ),
            })
        return merged

    def _policy_rule_from_scorecard(self, scorecard: dict[str, Any]) -> dict[str, Any]:
        policy_id = _clean_text(scorecard.get("policy_id"), limit=160)
        if not policy_id:
            return {}
        status = str(scorecard.get("status") or "candidate")
        action = str(scorecard.get("action") or "observe")
        sample_count = _safe_int(scorecard.get("sample_count"))
        confidence = _safe_float(scorecard.get("confidence"))
        rule_status = status if status in {"active_caution", "active_preference"} else "candidate"
        condition, effect = self._policy_rule_template(
            policy_id=policy_id,
            action=action,
            status=rule_status,
        )
        if policy_id.startswith("validation."):
            effect = self._merge_validation_pressure_effect(effect, scorecard)
            effect = self._merge_validation_pass_path_effect(effect, scorecard)
        if policy_id.startswith("period_memory_coverage."):
            effect = self._merge_period_memory_repair_effect(effect, scorecard)
        if policy_id.startswith("manager_contract_error."):
            memory_contract_rows = _compact_memory_contract_rows(
                scorecard.get("memory_contract_rows"),
                limit=6,
            )
            if memory_contract_rows:
                effect = {
                    **effect,
                    "memory_contract_rows": memory_contract_rows,
                }
            contract = _clean_text(scorecard.get("contract"), limit=160)
            if contract:
                effect = {**effect, "memory_contract": contract}
        reason = _clean_text(scorecard.get("reason"), limit=1200)
        return {
            "policy_id": policy_id,
            "status": rule_status,
            "action": action if action in {"observe", "prefer", "caution"} else "observe",
            "condition": {
                **condition,
                "min_sample_count": sample_count,
                "min_confidence": round(confidence, 4),
            },
            "effect": effect,
            "reason": reason,
            "evidence": {
                "source": "policy_scorecard",
                "sample_count": sample_count,
                "win_rate": scorecard.get("win_rate"),
                "avg_pnl_pct": scorecard.get("avg_pnl_pct"),
                "expectancy_pct": scorecard.get("expectancy_pct"),
                "rule_follow_rate": scorecard.get("rule_follow_rate"),
                "workflow_ids": _compact_text_list(
                    scorecard.get("workflow_ids"),
                    limit=8,
                    item_limit=120,
                ),
                "skill_ids": _compact_text_list(
                    scorecard.get("skill_ids"),
                    limit=12,
                    item_limit=120,
                ),
                "contract_ids": _compact_text_list(
                    scorecard.get("contract_ids"),
                    limit=16,
                    item_limit=160,
                ),
            },
            "source_scorecard": scorecard,
        }

    @staticmethod
    def _policy_rule_template(
        *,
        policy_id: str,
        action: str,
        status: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        base_effect = {
            "policy_mode": "soft_data_rule",
            "hard_filter": False,
            "safety_gate_override": False,
            "action": action,
            "status": status,
        }
        if policy_id == "respect_defined_stops":
            return (
                {
                    "block_status_in": ["open", "entry_pending"],
                    "requires_stop_review": True,
                },
                {
                    **base_effect,
                    "entry_bias": "reduce_if_stop_unclear",
                    "require_stop_price": True,
                    "risk_note": "손절 약속이 흐린 블록은 수량보다 stop과 무효화 조건을 먼저 확정한다.",
                },
            )
        if policy_id == "protect_winning_blocks":
            return (
                {"block_status_in": ["open"], "unrealized_pnl_pct_gte": 2.0},
                {
                    **base_effect,
                    "exit_bias": "protect_profit",
                    "target_stop_review": "raise_stop_or_trim_watch",
                    "risk_note": "수익 블록은 목표가만 기다리지 말고 이익 보호 조건을 함께 본다.",
                },
            )
        if policy_id == "review_order_and_data_failures":
            return (
                {"block_status_in": ["error"], "quote_status_in": ["error"]},
                {
                    **base_effect,
                    "entry_bias": "reduce_on_data_or_order_error",
                    "require_operator_review": True,
                    "risk_note": "주문/데이터 오류가 반복되면 새 진입보다 원인 확인을 우선한다.",
                },
            )
        if policy_id == "wait_for_clean_block_validation":
            return (
                {"new_entry": True, "requires_confirmation": True},
                {
                    **base_effect,
                    "entry_bias": "selective",
                    "require_confirmation": True,
                    "risk_note": "깨끗한 가격/수급 확인 전에는 진입 근거, 목표, 손절, 계좌 비중을 더 엄격히 설명한다.",
                },
            )
        if policy_id.startswith("jue_wiki_execution_hint."):
            execution_hint = policy_id.removeprefix("jue_wiki_execution_hint.")
            hint_effects: dict[str, dict[str, Any]] = {
                "cap_to_audit_or_repair_probe": {
                    "entry_bias": "audit_or_repair_probe_only",
                    "execution_hint": "cap_to_audit_or_repair_probe",
                    "require_jue_wiki_execution_hint_audit": True,
                    "target_stop_review": "rebuild_block_design_before_live_execution",
                    "required_evidence": [
                        "jue_wiki_execution_hint_audit",
                        "decision_adjustment_resolution",
                        "repair_or_probe_thesis",
                        "post_trade_reflection_link",
                    ],
                    "sizing_policy": "micro_probe_until_wiki_hint_compliance_recovers",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "위키가 audit/repair/probe로 제한한 힌트를 최근 live 실행으로 "
                        "위반했다. 다음 같은 유형 블록은 금지가 아니라 감사 가능한 "
                        "소액 probe 또는 수리 블록으로 설계하고, metadata에 힌트 준수 "
                        "근거를 남긴다."
                    ),
                },
                "reduce_size_and_require_live_cross_check": {
                    "entry_bias": "reduced_size_live_cross_check",
                    "execution_hint": "reduce_size_and_require_live_cross_check",
                    "require_jue_wiki_execution_hint_audit": True,
                    "target_stop_review": "require_cross_checked_trigger_target_stop",
                    "required_evidence": [
                        "live_cross_check",
                        "decision_adjustment_resolution",
                        "reduced_size_reason",
                    ],
                    "sizing_policy": "reduced_probe_until_cross_check_positive",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "위키가 수량 축소와 live cross-check를 요구한 힌트는 "
                        "가격/수급/비용 교차확인과 축소 수량 근거를 함께 남긴다."
                    ),
                },
                "allow_live_cross_checked_execution": {
                    "entry_bias": "live_cross_checked_execution_allowed",
                    "execution_hint": "allow_live_cross_checked_execution",
                    "require_jue_wiki_execution_hint_audit": True,
                    "target_stop_review": "keep_cross_checked_trigger_target_stop",
                    "required_evidence": [
                        "live_cross_check",
                        "decision_adjustment_resolution",
                        "post_trade_outcome_sample",
                    ],
                    "sizing_policy": "normal_size_only_with_live_cross_check",
                    "risk_budget_multiplier": 1.0,
                    "max_budget_multiplier": 1.0,
                    "risk_note": (
                        "위키 힌트가 live cross-check 실행을 허용한 경우에도 "
                        "교차확인 근거와 사후 성과 표본을 남긴다."
                    ),
                },
            }
            hint_effect = hint_effects.get(
                execution_hint,
                {
                    "entry_bias": "wiki_hint_audited_probe",
                    "execution_hint": execution_hint,
                    "require_jue_wiki_execution_hint_audit": True,
                    "target_stop_review": "document_wiki_hint_resolution",
                    "required_evidence": [
                        "jue_wiki_execution_hint_audit",
                        "decision_adjustment_resolution",
                    ],
                    "sizing_policy": "probe_until_wiki_hint_effectiveness_known",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "위키 실행 힌트의 성과 표본이 충분히 검증될 때까지 "
                        "감사 가능한 probe 중심으로 운용한다."
                    ),
                },
            )
            return (
                {
                    "new_entry": True,
                    "jue_wiki_execution_hint": execution_hint,
                },
                {
                    **base_effect,
                    **hint_effect,
                },
            )
        if policy_id.startswith("jue_wiki_usage_contract."):
            status_key = policy_id.removeprefix("jue_wiki_usage_contract.")
            return (
                {
                    "new_entry": True,
                    "jue_wiki_usage_contract_status": status_key,
                },
                {
                    **base_effect,
                    "entry_bias": "wiki_memory_requires_live_cross_check",
                    "require_jue_wiki_usage_contract_audit": True,
                    "target_stop_review": "document_wiki_contract_resolution",
                    "required_evidence": [
                        "jue_wiki_usage_contract_resolution",
                        "live_quote",
                        "account_state",
                        "risk_gate",
                        "fresh_research_conflicts",
                        "current_price_structure",
                    ],
                    "sizing_policy": "do_not_scale_on_wiki_memory_alone",
                    "risk_budget_multiplier": 0.5
                    if status_key != "resolved"
                    else 1.0,
                    "max_budget_multiplier": 0.5
                    if status_key != "resolved"
                    else 1.0,
                    "risk_note": (
                        "위키는 장기 기억/가설 저장소이며 단독 매매권한이 아니다. "
                        "위키가 action에 영향을 주면 live quote, 계좌, 리스크 게이트, "
                        "최신 리서치 충돌, 현재 가격 구조 중 무엇이 확인/축소/무효화했는지 "
                        "반드시 metadata에 남긴다."
                    ),
                },
            )
        if policy_id.startswith("jue_wiki_selection."):
            parts = policy_id.split(".", 2)
            venue = parts[1] if len(parts) == 3 else ""
            reason_key = parts[2] if len(parts) == 3 else policy_id.removeprefix(
                "jue_wiki_selection."
            )
            return (
                {
                    "new_entry": True,
                    "jue_wiki_selection_reason": reason_key,
                    "jue_wiki_selection_venue": venue,
                },
                {
                    **base_effect,
                    "discipline_id": "wiki_freshness",
                    "scale_blocker": "jue_wiki_freshness_repair",
                    "validation_effect_profile": "wiki_freshness",
                    "entry_bias": "fresh_wiki_cross_checked_probe_or_wait",
                    "sizing_policy": (
                        "no_size_increase_until_wiki_freshness_repaired"
                    ),
                    "target_stop_review": (
                        "refresh_or_cross_check_wiki_before_target_stop"
                    ),
                    "require_fresh_wiki_context": True,
                    "required_evidence": [
                        "fresh_jue_wiki_context",
                        "selection_audit_resolution",
                        "live_cross_check",
                    ],
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "쥬가 stale 위키 페이지를 판단 입력으로 선택했다. 금지는 "
                        "아니지만 다음 같은 scope 판단은 위키 최신화, live cross-check, "
                        "선택 감사 해소 근거를 남기고 대기/probe 중심으로 설계한다."
                    ),
                },
            )
        if policy_id.startswith("manager_contract_error."):
            parts = policy_id.split(".", 2)
            venue = parts[1] if len(parts) == 3 else ""
            error_key = parts[2] if len(parts) == 3 else policy_id.removeprefix(
                "manager_contract_error."
            )
            return (
                {
                    "new_entry": True,
                    "manager_contract_error": error_key,
                    "manager_contract_venue": venue,
                },
                {
                    **base_effect,
                    "discipline_id": "memory_contract",
                    "scale_blocker": "memory_contract_repair",
                    "validation_effect_profile": "memory_contract",
                    "entry_bias": "memory_contract_resolved_probe_or_wait",
                    "sizing_policy": (
                        "no_size_increase_until_memory_contract_repaired"
                    ),
                    "target_stop_review": "cite_or_reject_memory_before_target_stop",
                    "memory_contract_error": error_key,
                    "require_memory_contract_resolution": True,
                    "required_evidence": [
                        "memory_contract_resolution",
                        "memory_evidence_reference",
                        "rejection_reason_if_ignored",
                    ],
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "쥬가 메모리/위키 근거를 받았는데도 인용하거나 명시적으로 "
                        "거절하지 않은 manager run이 반복됐다. 다음 같은 scope 판단은 "
                        "메모리 근거를 cite하거나, 왜 쓰지 않는지 기록한 뒤 대기/probe "
                        "중심으로 설계한다."
                    ),
                },
            )
        if policy_id.startswith("period_memory_coverage."):
            period_memory_status = policy_id.removeprefix("period_memory_coverage.")
            if period_memory_status == "gap_overridden":
                period_effect = {
                    "entry_bias": "cross_checked_probe_or_wait_on_memory_gap",
                    "period_memory_status": period_memory_status,
                    "require_period_memory_override_audit": True,
                    "target_stop_review": "require_gap_override_and_current_evidence_before_entry",
                    "required_evidence": [
                        "period_memory_coverage_gap",
                        "period_memory_override_reason",
                        "fresh_period_review_or_replay",
                        "current_live_cross_check",
                    ],
                    "sizing_policy": "reduce_without_fresh_period_review_or_replay",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "주간/월간 리뷰 또는 리플레이 공백을 live 근거로 override한 "
                        "성과가 불안정하면 금지하지 않는다. 대신 공백 내용, override "
                        "근거, 현재 시세/리서치 교차확인을 metadata와 risk_note에 남기고 "
                        "fresh period review/replay가 없으면 수량을 낮추거나 대기한다."
                    ),
                }
            elif period_memory_status == "gap_unresolved":
                period_effect = {
                    "entry_bias": "wait_or_micro_probe_until_period_memory_gap_resolved",
                    "period_memory_status": period_memory_status,
                    "require_period_memory_fresh_review_or_replay": True,
                    "target_stop_review": "defer_or_rebuild_after_period_memory_repair",
                    "required_evidence": [
                        "period_memory_coverage_gap",
                        "fresh_period_review_or_replay",
                        "current_live_cross_check",
                        "explicit_gap_resolution_plan",
                    ],
                    "sizing_policy": "micro_probe_until_period_memory_gap_repaired",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "기간 메모리 공백을 override하지 못한 케이스는 금지가 아니라 "
                        "리뷰/리플레이 복구, live 교차확인, 소액 probe 중심으로 다룬다."
                    ),
                }
            elif period_memory_status == "missing_override_reason":
                period_effect = {
                    "entry_bias": (
                        "metadata_repair_probe_or_wait_until_override_reason_present"
                    ),
                    "period_memory_status": period_memory_status,
                    "require_period_memory_metadata_contract_repair": True,
                    "target_stop_review": (
                        "repair_period_memory_override_metadata_before_scaling"
                    ),
                    "required_evidence": [
                        "period_memory_coverage_gap",
                        "period_memory_override_reason",
                        "metadata_contract_audit_resolution",
                        "current_live_cross_check",
                    ],
                    "sizing_policy": "metadata_repair_micro_probe_until_contract_clean",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "기간 메모리 공백을 override했다면 그 이유가 block metadata에 "
                        "남아야 한다. override_reason이 없으면 금지하지는 않되, "
                        "metadata 계약을 복구하고 live 근거를 교차확인할 때까지 "
                        "소액 probe 또는 대기로 제한한다."
                    ),
                }
            elif period_memory_status == "missing_coverage_gap":
                period_effect = {
                    "entry_bias": (
                        "metadata_repair_probe_or_wait_until_coverage_gap_named"
                    ),
                    "period_memory_status": period_memory_status,
                    "require_period_memory_metadata_contract_repair": True,
                    "target_stop_review": (
                        "name_period_memory_gap_before_using_override"
                    ),
                    "required_evidence": [
                        "period_memory_coverage_gap",
                        "period_memory_override_reason",
                        "metadata_contract_audit_resolution",
                        "current_live_cross_check",
                    ],
                    "sizing_policy": "metadata_repair_micro_probe_until_gap_named",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "기간 메모리 공백을 override하려면 어떤 공백인지 먼저 "
                        "명시해야 한다. coverage_gap이 없으면 금지하지는 않되, "
                        "공백 이름과 override 근거를 복구할 때까지 소액 probe "
                        "또는 대기로 제한한다."
                    ),
                }
            else:
                period_effect = {
                    "entry_bias": "period_memory_audited_probe",
                    "period_memory_status": period_memory_status,
                    "require_period_memory_override_audit": True,
                    "target_stop_review": "document_period_memory_status_before_entry",
                    "required_evidence": [
                        "period_memory_coverage_gap",
                        "period_memory_override_reason",
                        "current_live_cross_check",
                    ],
                    "sizing_policy": "probe_until_period_memory_effectiveness_known",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "기간 메모리 커버리지 상태가 성과에 미친 영향을 다음 블록 "
                        "metadata에 남기고, 표본이 충분해질 때까지 감사 가능한 probe로 운용한다."
                    ),
                }
            return (
                {
                    "new_entry": True,
                    "period_memory_status": period_memory_status,
                },
                {
                    **base_effect,
                    **period_effect,
                },
            )
        if policy_id.startswith("lane_scale."):
            scale_blocker = policy_id.removeprefix("lane_scale.")
            lane_scale_effects: dict[str, dict[str, Any]] = {
                "verified_edge_sample_cap": {
                    "entry_bias": "recorded_cost_alpha_waiting_probe",
                    "require_scale_repair_review": True,
                    "require_positive_net_edge": True,
                    "target_stop_review": "require_net_edge_after_recorded_costs_before_pressing",
                    "required_evidence": [
                        "recorded_entry_fill",
                        "recorded_exit_fill",
                        "fee",
                        "spread",
                        "slippage",
                        "funding_or_tax",
                        "positive_net_edge",
                    ],
                    "min_reward_risk": 2.0,
                    "sizing_policy": "recorded_alpha_probe_until_min_samples",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "검증된 순알파 표본이 부족한 lane은 금지가 아니라 기록형 소액 "
                        "probe로 실제 진입/청산/비용 차감 후 양의 엣지를 먼저 쌓는다."
                    ),
                },
                "verified_edge_net_pnl_cap": {
                    "entry_bias": "positive_recorded_edge_waiting_probe",
                    "require_scale_repair_review": True,
                    "require_positive_net_edge": True,
                    "target_stop_review": "reprice_or_wait_until_recorded_cost_alpha_positive",
                    "required_evidence": [
                        "recorded_entry_fill",
                        "recorded_exit_fill",
                        "fee",
                        "spread",
                        "slippage",
                        "funding_or_tax",
                        "positive_recorded_cost_alpha_net_pnl",
                    ],
                    "min_reward_risk": 2.2,
                    "sizing_policy": "micro_probe_until_recorded_alpha_positive",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "기록비용 표본 수는 충분하지만 순손익이 음수인 lane은 "
                        "금지하지 않고 micro/probe와 대기진입으로 낮춘다. "
                        "다음 블록은 target/stop을 비용 이후 양의 순엣지가 남도록 "
                        "재가격화하고, 기록비용 기준 순손익이 양수로 회복되기 전까지 "
                        "크기를 키우지 않는다."
                    ),
                },
                "cost_evidence_repair": {
                    "entry_bias": "cost_verified_waiting_entry",
                    "require_scale_repair_review": True,
                    "require_positive_net_edge": True,
                    "target_stop_review": "widen_expected_move_or_wait_for_price_improvement",
                    "required_evidence": [
                        "recorded_fill_book",
                        "fee",
                        "spread",
                        "slippage",
                        "tax_or_funding",
                        "net_pnl_after_costs",
                    ],
                    "min_reward_risk": 1.8,
                    "sizing_policy": "reduce_cost_weak_lane",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "비용 근거가 약한 lane은 기대 움직임이 비용을 충분히 이기거나 "
                        "가격 개선 대기 조건이 있을 때만 블록화한다."
                    ),
                },
                "entry_quality_repair": {
                    "entry_bias": "pullback_reclaim_or_value_waiting_entry",
                    "require_scale_repair_review": True,
                    "require_regime_match": True,
                    "target_stop_review": "reprice_trigger_target_stop_around_better_risk_location",
                    "required_evidence": [
                        "entry_quality_score",
                        "pullback_or_value_location",
                        "regime_match",
                        "flow_recovery",
                        "recent_failed_chase_check",
                    ],
                    "min_reward_risk": 1.7,
                    "max_stop_risk_pct": 5.0,
                    "sizing_policy": "probe_only_until_entry_quality_repairs",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "진입 품질 약점은 고점 추격을 줄이고 눌림, 저평가 위치, "
                        "수급 회복, 레짐 정합성이 맞을 때만 소액·대기진입을 우선한다."
                    ),
                },
                "validation_evidence_repair": {
                    "entry_bias": "shadow_or_waiting_entry_until_validation_complete",
                    "require_scale_repair_review": True,
                    "require_shadow_validation": True,
                    "target_stop_review": "rebuild_backtest_wfa_oos_before_scale_up",
                    "required_evidence": [
                        "backtest",
                        "walk_forward",
                        "out_of_sample",
                        "live_shadow",
                    ],
                    "min_reward_risk": 1.8,
                    "sizing_policy": "shadow_or_probe_until_validation_evidence_complete",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "검증 근거가 빈 lane은 실거래 증액 전에 백테스트, WFA, OOS, "
                        "live shadow를 다시 채우고 대기 또는 shadow 중심으로 운용한다."
                    ),
                },
                "validation_backtest_wfa_oos_shadow_cap": {
                    "entry_bias": "shadow_or_waiting_entry_until_validation_complete",
                    "require_scale_repair_review": True,
                    "require_shadow_validation": True,
                    "target_stop_review": "rebuild_backtest_wfa_oos_before_scale_up",
                    "required_evidence": [
                        "backtest",
                        "walk_forward",
                        "out_of_sample",
                        "live_shadow",
                    ],
                    "min_reward_risk": 2.0,
                    "sizing_policy": "no_size_increase_until_wfa_oos_shadow_pass",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": (
                        "백테스트/WFA/OOS/live shadow 핵심 근거가 부족하면 "
                        "증액을 멈추고 검증 통과 전까지 소액·대기 중심으로 둔다."
                    ),
                },
            }
            lane_effect = lane_scale_effects.get(
                scale_blocker,
                {
                    "entry_bias": "waiting_or_probe_until_lane_scale_repairs",
                    "require_scale_repair_review": True,
                    "target_stop_review": "rebuild_scale_repair_targets_before_pressing",
                    "required_evidence": [
                        "lane_scorecard",
                        "cost_evidence",
                        "entry_quality",
                        "live_shadow_or_recent_trade_sample",
                    ],
                    "sizing_policy": "no_size_increase_until_scale_blockers_clear",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "Lane scale blocker는 금지 규칙이 아니라 해당 lane을 키우기 전 "
                        "비용, 진입품질, live/shadow 근거를 복구하게 만드는 소프트 캡이다."
                    ),
                },
            )
            return (
                {
                    "new_entry": True,
                    "lane_scale_blocker": scale_blocker,
                },
                {
                    **base_effect,
                    "scale_blocker": scale_blocker,
                    **lane_effect,
                },
            )
        if policy_id.startswith("validation."):
            remainder = policy_id.removeprefix("validation.")
            parts = remainder.split(".", 1)
            venue = parts[0] if parts and parts[0] in MEMORY_SCOPES else ""
            discipline_id = parts[1] if venue and len(parts) > 1 else remainder
            discipline_effects = {
                "data_validation": {
                    "validation_effect_profile": "data_quality",
                    "entry_bias": "quote_verified_waiting_entry",
                    "require_fresh_data": True,
                    "required_evidence": [
                        "fresh_quote",
                        "clean_symbol_identity",
                        "fill_or_cost_source",
                    ],
                    "target_stop_review": "wait_until_quote_and_fill_evidence_clean",
                    "sizing_policy": "no_size_increase_until_data_clean",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": "데이터 검증 약점은 가격/종목/체결 근거가 깨끗해질 때까지 진입을 기다리고 수량 확대를 늦춘다.",
                },
                "cost_simulation": {
                    "validation_effect_profile": "cost_drag",
                    "entry_bias": "cost_verified_waiting_entry",
                    "require_positive_net_edge": True,
                    "required_evidence": [
                        "fee",
                        "spread",
                        "slippage",
                        "tax_or_funding",
                    ],
                    "target_stop_review": "widen_expected_move_or_wait_for_price_improvement",
                    "min_reward_risk": 1.8,
                    "sizing_policy": "reduce_cost_weak_lane",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": "비용 약점은 수수료/스프레드/슬리피지/세금·펀딩 이후에도 순엣지가 남을 때만 블록화한다.",
                },
                "capacity_analysis": {
                    "validation_effect_profile": "capacity_depth",
                    "entry_bias": "depth_checked_waiting_entry",
                    "require_capacity_check": True,
                    "required_evidence": ["turnover", "depth", "spread"],
                    "target_stop_review": "scale_by_depth_and_liquidity",
                    "sizing_policy": "cap_by_capacity_until_depth_verified",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": "용량 약점은 유동성/호가/거래대금이 확인된 크기까지만 진입한다.",
                },
            }
            for discipline in (
                "overfit_validation",
                "walk_forward_analysis",
                "out_of_sample_test",
            ):
                discipline_effects[discipline] = {
                    "validation_effect_profile": "research_revalidation",
                    "entry_bias": "shadow_or_waiting_entry",
                    "require_shadow_validation": True,
                    "required_evidence": [
                        "backtest",
                        "walk_forward",
                        "out_of_sample",
                        "live_shadow",
                    ],
                    "target_stop_review": "rebuild_evidence_before_scale_up",
                    "sizing_policy": "no_scale_up_until_wfa_oos_live_shadow_clear",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": "백테스트/WFA/OOS 약점은 실거래 증액 전에 shadow와 미사용 구간 검증을 다시 채운다.",
                }
            for discipline in ("stress_test", "regime_test"):
                discipline_effects[discipline] = {
                    "validation_effect_profile": "scenario_regime",
                    "entry_bias": "regime_confirmed_waiting_entry",
                    "require_regime_match": True,
                    "required_evidence": ["current_regime", "scenario_stress"],
                    "target_stop_review": "reprice_for_current_regime",
                    "min_reward_risk": 1.5,
                    "sizing_policy": "regime_mismatch_probe_only",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": "레짐/스트레스 약점은 현재 시장국면과 맞는 블록만 소액·대기 중심으로 다룬다.",
                }
            for discipline in (
                "monte_carlo",
                "kelly_sizing",
                "mdd_limit",
                "sharpe_ratio",
                "sortino_ratio",
                "calmar_ratio",
                "profit_factor",
                "recovery_factor",
                "risk_of_ruin",
            ):
                discipline_effects[discipline] = {
                    "validation_effect_profile": "risk_adjusted_sizing",
                    "entry_bias": "fractional_kelly_probe_entry",
                    "require_risk_budget_review": True,
                    "required_evidence": [
                        "fractional_kelly",
                        "mdd_usage",
                        "ruin_probability",
                        "lane_confidence",
                    ],
                    "target_stop_review": "risk_reward_and_drawdown_review",
                    "min_reward_risk": 2.0,
                    "max_stop_risk_pct": 6.0,
                    "sizing_policy": "fractional_kelly_probe_only",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "risk_note": "위험조정 성과 약점은 raw Kelly 대신 fractional Kelly, MDD, 파산확률, lane 신뢰도로 수량을 축소한다.",
                }
            for discipline in ("correlation", "factor_exposure"):
                discipline_effects[discipline] = {
                    "validation_effect_profile": "portfolio_concentration",
                    "entry_bias": "concentration_checked_waiting_entry",
                    "require_exposure_review": True,
                    "required_evidence": [
                        "correlation_cluster",
                        "factor_exposure",
                        "active_block_exposure",
                    ],
                    "target_stop_review": "rebalance_exposure_or_wait",
                    "min_reward_risk": 1.5,
                    "sizing_policy": "cap_correlated_exposure",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": "상관/팩터 쏠림 약점은 같은 방향·같은 factor 노출을 늘리기 전에 분산 또는 대기진입으로 낮춘다.",
                }
            discipline_effect = discipline_effects.get(
                discipline_id,
                {
                    "validation_effect_profile": "generic_validation",
                    "entry_bias": "reduce_or_wait_on_validation_failure",
                    "require_validation_review": True,
                    "target_stop_review": "tighten_or_wait_for_fresh_validation",
                    "min_reward_risk": 1.5,
                    "sizing_policy": "probe_until_validation_repaired",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "반복되는 19검증 실패는 금지 규칙이 아니라 수량, 진입 대기, "
                        "목표/손절 재점검 신호로 반영한다."
                    ),
                },
            )
            condition = {
                "new_entry": True,
                "live_authority_failed_discipline": discipline_id,
            }
            if venue in {"kis", "binance"}:
                condition["live_authority_venue"] = venue
            return (
                condition,
                {
                    **base_effect,
                    "require_validation_review": True,
                    "discipline_id": discipline_id,
                    **discipline_effect,
                },
            )
        if policy_id.startswith("validation_attribution."):
            remainder = policy_id.removeprefix("validation_attribution.")
            parts = remainder.split(".", 1)
            group_type = parts[0] if parts else ""
            group = parts[1] if len(parts) > 1 else ""
            return (
                {
                    "new_entry": True,
                    "live_authority_failure_attribution": {
                        "group_type": group_type,
                        "group": group,
                    },
                },
                {
                    **base_effect,
                    "entry_bias": "reduce_or_wait_on_repeated_attribution",
                    "require_validation_review": True,
                    "target_stop_review": "reprice_or_wait_when_attribution_repeats",
                    "min_reward_risk": 1.8,
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "반복 손실 귀속은 금지 규칙이 아니라 진입 가격, 기간, "
                        "수량, target/stop을 다시 계산하게 만드는 소프트 신호다."
                    ),
                },
            )
        if policy_id.startswith("validation_recovery."):
            remainder = policy_id.removeprefix("validation_recovery.")
            parts = remainder.split(".", 1)
            source = parts[0] if parts else ""
            reason = parts[1] if len(parts) > 1 else ""
            return (
                {
                    "new_entry": True,
                    "validation_recovery_focus": {
                        "source": source,
                        "reason": reason,
                    },
                },
                {
                    **base_effect,
                    "entry_bias": "reduce_or_wait_until_validation_repaired",
                    "require_validation_repair_review": True,
                    "target_stop_review": "rebuild_evidence_before_scale_up",
                    "min_reward_risk": 1.5,
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "반복되는 검증 복구 항목은 금지 규칙이 아니라 백테스트, "
                        "WFA, OOS 근거를 다시 채우고 수량 확대를 늦추는 소프트 신호다."
                    ),
                },
            )
        if policy_id.startswith("validation_repair_enforcement."):
            repair_action = policy_id.removeprefix("validation_repair_enforcement.")
            return (
                {
                    "new_entry": True,
                    "validation_repair_enforcement": {
                        "repair_action": repair_action,
                    },
                },
                {
                    **base_effect,
                    "entry_bias": "respect_repair_waiting_or_probe_mode",
                    "require_validation_repair_review": True,
                    "target_stop_review": "keep_trigger_target_stop_consistent_with_repair",
                    "required_evidence": [
                        "repair_status",
                        "validation_refresh",
                        "precise_costs",
                        "live_shadow_if_required",
                    ],
                    "sizing_policy": "keep_probe_until_repair_passes",
                    "risk_budget_multiplier": 0.5,
                    "max_budget_multiplier": 0.5,
                    "risk_note": (
                        "검증 수리 강제가 반복된 lane은 금지가 아니라 대기진입, "
                        "소액 probe, 비용/검증 재확인 상태로 유지한다."
                    ),
                },
            )
        effect = {
            **base_effect,
            "entry_bias": "prefer" if action == "prefer" else "review",
            "risk_note": "성과 기반 정책 룰을 sizing, target/stop, 확인 조건 보정 신호로만 사용한다.",
        }
        return ({"policy_id": policy_id}, effect)

    @staticmethod
    def _policy_rule_signature(rule: dict[str, Any]) -> str:
        return _json_dumps(
            {
                "status": rule.get("status"),
                "action": rule.get("action"),
                "condition": rule.get("condition"),
                "effect": rule.get("effect"),
                "reason": rule.get("reason"),
                "evidence": rule.get("evidence"),
                "source_scorecard": InvestmentMemoryService._stable_policy_source(
                    rule.get("source_scorecard")
                ),
            }
        )

    @staticmethod
    def _stable_policy_source(value: Any) -> Any:
        volatile_keys = {
            "created_at",
            "crawled_at",
            "generated_at",
            "last_seen_at",
            "scored_at",
            "updated_at",
        }
        if isinstance(value, dict):
            return {
                key: InvestmentMemoryService._stable_policy_source(item)
                for key, item in value.items()
                if key not in volatile_keys
            }
        if isinstance(value, list):
            return [InvestmentMemoryService._stable_policy_source(item) for item in value]
        return value

    def _write_policy_rule_file(self, rule: dict[str, Any]) -> Path:
        policy_id = _clean_text(rule.get("policy_id"), limit=160) or "policy"
        version = max(_safe_int(rule.get("version")), 1)
        path = self.root / "policies" / "rules" / f"{policy_id}_v{version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "hermes.policy_rule.v1",
            "policy_id": policy_id,
            "version": version,
            "rule_id": rule.get("rule_id") or f"{policy_id}@v{version}",
            "status": rule.get("status"),
            "action": rule.get("action"),
            "condition": rule.get("condition") or {},
            "effect": rule.get("effect") or {},
            "reason": rule.get("reason") or "",
            "evidence": rule.get("evidence") or {},
            "source_scorecard": rule.get("source_scorecard") or {},
        }
        path.write_text(_json_dumps(payload) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _rule_impact(rule: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_id": rule.get("policy_id"),
            "version": rule.get("version"),
            "rule_id": rule.get("rule_id"),
            "status": rule.get("status"),
            "action": rule.get("action"),
            "effect": rule.get("effect") or {},
            "evidence": rule.get("evidence") if isinstance(rule.get("evidence"), dict) else {},
            "reason": rule.get("reason"),
        }

    def _scorecard_from_reflections(
        self,
        reflections: list[dict[str, Any]],
        *,
        policy_id: str = "",
    ) -> dict[str, Any]:
        rows = [row for row in reflections if isinstance(row, dict)]
        if not rows:
            return {}
        resolved_policy_id = policy_id
        if not resolved_policy_id:
            metrics = rows[0].get("metrics") if isinstance(rows[0].get("metrics"), dict) else {}
            resolved_policy_id = str(metrics.get("policy_id") or "wait_for_clean_block_validation")
        sample_count = len(rows)
        is_validation_policy = resolved_policy_id.startswith("validation.")
        is_attribution_policy = resolved_policy_id.startswith("validation_attribution.")
        is_recovery_policy = resolved_policy_id.startswith("validation_recovery.")
        is_repair_enforcement_policy = resolved_policy_id.startswith(
            "validation_repair_enforcement."
        )
        is_lane_scale_policy = resolved_policy_id.startswith("lane_scale.")
        is_jue_wiki_execution_hint_policy = resolved_policy_id.startswith(
            "jue_wiki_execution_hint."
        )
        is_jue_wiki_usage_contract_policy = resolved_policy_id.startswith(
            "jue_wiki_usage_contract."
        )
        is_period_memory_policy = resolved_policy_id.startswith(
            "period_memory_coverage."
        )
        is_diagnostic_policy = (
            resolved_policy_id == "review_order_and_data_failures"
            or is_validation_policy
            or is_attribution_policy
            or is_recovery_policy
            or is_repair_enforcement_policy
            or is_lane_scale_policy
            or is_jue_wiki_execution_hint_policy
            or is_jue_wiki_usage_contract_policy
            or is_period_memory_policy
        )
        discipline_id = resolved_policy_id.removeprefix("validation.") if is_validation_policy else ""
        attribution_group_type = ""
        attribution_group = ""
        if is_attribution_policy:
            remainder = resolved_policy_id.removeprefix("validation_attribution.")
            parts = remainder.split(".", 1)
            attribution_group_type = parts[0] if parts else ""
            attribution_group = parts[1] if len(parts) > 1 else ""
        recovery_source = ""
        recovery_reason = ""
        if is_recovery_policy:
            remainder = resolved_policy_id.removeprefix("validation_recovery.")
            parts = remainder.split(".", 1)
            recovery_source = parts[0] if parts else ""
            recovery_reason = parts[1] if len(parts) > 1 else ""
        repair_action_fragment = ""
        if is_repair_enforcement_policy:
            repair_action_fragment = resolved_policy_id.removeprefix(
                "validation_repair_enforcement."
            )
        scale_blocker = (
            resolved_policy_id.removeprefix("lane_scale.")
            if is_lane_scale_policy
            else ""
        )
        jue_wiki_execution_hint = (
            resolved_policy_id.removeprefix("jue_wiki_execution_hint.")
            if is_jue_wiki_execution_hint_policy
            else ""
        )
        jue_wiki_usage_contract_status = (
            resolved_policy_id.removeprefix("jue_wiki_usage_contract.")
            if is_jue_wiki_usage_contract_policy
            else ""
        )
        period_memory_status = (
            resolved_policy_id.removeprefix("period_memory_coverage.")
            if is_period_memory_policy
            else ""
        )
        pnl_values = [_safe_float(row.get("pnl_pct")) for row in rows]
        wins = [value for value in pnl_values if value > 0]
        win_rate = len(wins) / sample_count if sample_count else 0.0
        avg_pnl = sum(pnl_values) / sample_count if sample_count else 0.0
        rule_follow_rate = (
            sum(1 for row in rows if bool(row.get("rule_followed"))) / sample_count
            if sample_count
            else 0.0
        )
        confidence = min(0.95, 0.35 + sample_count * 0.08 + min(abs(avg_pnl), 8.0) / 40.0)
        if (
            is_validation_policy
            or is_attribution_policy
            or is_recovery_policy
            or is_repair_enforcement_policy
            or is_lane_scale_policy
            or is_jue_wiki_execution_hint_policy
            or is_jue_wiki_usage_contract_policy
            or is_period_memory_policy
        ) and sample_count >= 3:
            confidence = max(confidence, 0.65)
        action = "observe"
        status = "candidate"
        if is_diagnostic_policy:
            action = "caution"
            if resolved_policy_id == "review_order_and_data_failures":
                status = "active_caution"
            elif sample_count >= 3 and confidence >= 0.65:
                status = "active_caution"
        elif sample_count >= 5 and avg_pnl > 0:
            action = "prefer"
            status = "active_preference"
        elif sample_count >= 3 and confidence >= 0.65:
            action = "caution"
            status = "active_caution"
        reason = (
            f"표본 {sample_count}건, 승률 {win_rate:.0%}, 평균손익 {avg_pnl:+.2f}%, "
            f"룰준수 {rule_follow_rate:.0%}"
        )
        if is_validation_policy:
            pressure_evidence = self._validation_pressure_action_evidence(
                rows,
                discipline_id=discipline_id,
            )
            pressure_action = (
                pressure_evidence.get("discipline_action")
                if isinstance(pressure_evidence.get("discipline_action"), dict)
                else {}
            )
            pressure_reason = ""
            if pressure_action:
                entry_constraint = _clean_text(
                    pressure_action.get("entry_constraint"),
                    limit=140,
                )
                sizing_constraint = _clean_text(
                    pressure_action.get("sizing_constraint"),
                    limit=140,
                )
                if entry_constraint or sizing_constraint:
                    pressure_reason = (
                        f", 진입제약 `{entry_constraint or '-'}`, "
                        f"수량제약 `{sizing_constraint or '-'}`"
                    )
            reason = (
                f"19검증 `{discipline_id}` 실패가 포함된 반성 표본 {sample_count}건, "
                f"승률 {win_rate:.0%}, 평균손익 {avg_pnl:+.2f}%, 룰준수 {rule_follow_rate:.0%}"
                f"{pressure_reason}"
            )
        if is_attribution_policy:
            reason = (
                f"19검증 실패 귀속 `{attribution_group_type}={attribution_group}` "
                f"반성 표본 {sample_count}건, 승률 {win_rate:.0%}, "
                f"평균손익 {avg_pnl:+.2f}%, 룰준수 {rule_follow_rate:.0%}"
            )
        if is_recovery_policy:
            reason = (
                f"19검증 복구 `{recovery_source}:{recovery_reason}` "
                f"반성 표본 {sample_count}건, 승률 {win_rate:.0%}, "
                f"평균손익 {avg_pnl:+.2f}%, 룰준수 {rule_follow_rate:.0%}"
            )
        if is_repair_enforcement_policy:
            reason = (
                f"검증 수리 강제 `{repair_action_fragment}` 적용 반성 표본 "
                f"{sample_count}건, 승률 {win_rate:.0%}, 평균손익 {avg_pnl:+.2f}%, "
                f"룰준수 {rule_follow_rate:.0%}"
            )
        if is_lane_scale_policy:
            evidence = self._lane_scale_evidence(rows, scale_blocker=scale_blocker)
            repair_counts = (
                evidence.get("scale_repair_target_counts")
                if isinstance(evidence.get("scale_repair_target_counts"), dict)
                else {}
            )
            top_repair = next(iter(repair_counts), "")
            reason = (
                f"Lane scale blocker `{scale_blocker}` 포함 반성 표본 {sample_count}건, "
                f"승률 {win_rate:.0%}, 평균손익 {avg_pnl:+.2f}%, 룰준수 {rule_follow_rate:.0%}"
                + (f", 우선 복구 `{top_repair}`" if top_repair else "")
            )
        if is_jue_wiki_execution_hint_policy:
            evidence = self._jue_wiki_execution_hint_evidence(
                rows,
                execution_hint=jue_wiki_execution_hint,
            )
            followed_count = _safe_int(evidence.get("hint_followed_count"))
            violation_count = _safe_int(evidence.get("hint_violation_count"))
            reason = (
                f"위키 실행 힌트 `{jue_wiki_execution_hint}` 반성 표본 "
                f"{sample_count}건, 준수 {followed_count}건, 위반 {violation_count}건, "
                f"승률 {win_rate:.0%}, 평균손익 {avg_pnl:+.2f}%"
            )
        if is_jue_wiki_usage_contract_policy:
            evidence = self._jue_wiki_usage_contract_evidence(
                rows,
                status=jue_wiki_usage_contract_status,
            )
            resolved_count = _safe_int(evidence.get("usage_contract_resolved_count"))
            missing_cross_check_count = _safe_int(
                evidence.get("usage_contract_missing_cross_check_count")
            )
            missing_authority_count = _safe_int(
                evidence.get("usage_contract_missing_authority_count")
            )
            reason = (
                f"위키 사용계약 `{jue_wiki_usage_contract_status}` 반성 표본 "
                f"{sample_count}건, 해소 {resolved_count}건, "
                f"교차확인 누락 {missing_cross_check_count}건, "
                f"단독권한 문구 누락 {missing_authority_count}건, "
                f"승률 {win_rate:.0%}, 평균손익 {avg_pnl:+.2f}%"
            )
        if is_period_memory_policy:
            period_evidence = self._period_memory_coverage_evidence(rows)
            repair_actions = period_evidence.get("period_memory_repair_actions")
            audit_resolutions = period_evidence.get("metadata_contract_audit_resolutions")
            top_repair_action = (
                repair_actions[0]
                if isinstance(repair_actions, list) and repair_actions
                else ""
            )
            top_audit_resolution = (
                audit_resolutions[0]
                if isinstance(audit_resolutions, list) and audit_resolutions
                else ""
            )
            reason = (
                f"기간 메모리 커버리지 `{period_memory_status}` 반성 표본 "
                f"{sample_count}건, 공백 {period_evidence['period_memory_gap_count']}건, "
                f"override {period_evidence['period_memory_override_count']}건, 승률 {win_rate:.0%}, "
                f"평균손익 {avg_pnl:+.2f}%"
                + (f", 우선 수리 `{top_repair_action}`" if top_repair_action else "")
                + (
                    f", 해결 기록 `{top_audit_resolution}`"
                    if top_audit_resolution
                    else ""
                )
            )
        payload = {
            "policy_id": resolved_policy_id,
            "action": action,
            "status": status,
            "sample_count": sample_count,
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "expectancy_pct": avg_pnl,
            "rule_follow_rate": rule_follow_rate,
            "confidence": confidence,
            "reason": reason,
        }
        if is_validation_policy:
            payload.update({
                "source": "live_authority_validation",
                "discipline_id": discipline_id,
            })
            pressure_evidence = self._validation_pressure_action_evidence(
                rows,
                discipline_id=discipline_id,
            )
            pressure_action = (
                pressure_evidence.get("discipline_action")
                if isinstance(pressure_evidence.get("discipline_action"), dict)
                else {}
            )
            if pressure_action:
                payload["validation_pressure_action"] = pressure_action
            pressure_counts = {
                key: value
                for key, value in pressure_evidence.items()
                if key != "discipline_action" and value not in ({}, [], "", None)
            }
            if pressure_counts:
                payload["validation_pressure_evidence"] = pressure_counts
        if is_attribution_policy:
            payload.update({
                "source": "live_authority_failure_attribution",
                "attribution_group_type": attribution_group_type,
                "attribution_group": attribution_group,
            })
        if is_recovery_policy:
            payload.update({
                "source": "live_authority_validation_recovery",
                "recovery_source": recovery_source,
                "recovery_reason": recovery_reason,
            })
        if is_repair_enforcement_policy:
            payload.update({
                "source": "validation_repair_enforcement",
                "repair_action_fragment": repair_action_fragment,
            })
        if is_lane_scale_policy:
            lane_evidence = self._lane_scale_evidence(
                rows,
                scale_blocker=scale_blocker,
            )
            representative_gate = (
                lane_evidence.get("representative_gate")
                if isinstance(lane_evidence.get("representative_gate"), dict)
                else {}
            )
            source = (
                "cost_component_audit"
                if representative_gate.get("source") == "reflection_cost_audit"
                else "lane_authority_scale_blocker"
            )
            payload.update({
                "source": source,
                "scale_blocker": scale_blocker,
            })
            if lane_evidence:
                payload["lane_scale_evidence"] = lane_evidence
        if is_jue_wiki_execution_hint_policy:
            hint_evidence = self._jue_wiki_execution_hint_evidence(
                rows,
                execution_hint=jue_wiki_execution_hint,
            )
            payload.update({
                "source": "jue_wiki_execution_hint_audit",
                "execution_hint": jue_wiki_execution_hint,
                **hint_evidence,
            })
            if _safe_int(hint_evidence.get("hint_violation_count")) > 0:
                payload["action"] = "caution"
                payload["status"] = "active_caution"
        if is_jue_wiki_usage_contract_policy:
            usage_evidence = self._jue_wiki_usage_contract_evidence(
                rows,
                status=jue_wiki_usage_contract_status,
            )
            payload.update({
                "source": "jue_wiki_usage_contract_audit",
                "usage_contract_status": jue_wiki_usage_contract_status,
                **usage_evidence,
            })
            if (
                _safe_int(usage_evidence.get("usage_contract_missing_cross_check_count"))
                > 0
                or _safe_int(usage_evidence.get("usage_contract_missing_authority_count"))
                > 0
            ):
                payload["action"] = "caution"
                payload["status"] = "active_caution"
        if is_period_memory_policy:
            payload.update({
                "source": "period_memory_coverage_audit",
                "period_memory_status": period_memory_status,
                **self._period_memory_coverage_evidence(rows),
            })
        return payload

    @staticmethod
    def _period_memory_coverage_evidence(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        audits: list[dict[str, Any]] = []
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            audit = (
                metrics.get("period_memory_coverage_audit")
                if isinstance(metrics.get("period_memory_coverage_audit"), dict)
                else {}
            )
            if audit:
                audits.append(audit)
        missing_metadata: list[str] = []
        repair_actions: list[str] = []
        audit_resolutions: list[str] = []
        repair_notes: list[str] = []
        for audit in audits:
            for raw_value in list(audit.get("missing_metadata") or [])[:6]:
                value = _clean_text(raw_value, limit=80)
                if value and value not in missing_metadata:
                    missing_metadata.append(value)
            repair_action = _clean_text(audit.get("repair_action"), limit=160)
            if repair_action and repair_action not in repair_actions:
                repair_actions.append(repair_action)
            audit_resolution = _clean_text(
                audit.get("metadata_contract_audit_resolution"),
                limit=180,
            )
            if audit_resolution and audit_resolution not in audit_resolutions:
                audit_resolutions.append(audit_resolution)
            repair_note = _clean_text(
                audit.get("metadata_contract_repair_note"),
                limit=300,
            )
            if repair_note and repair_note not in repair_notes:
                repair_notes.append(repair_note)
        return {
            "period_memory_gap_count": sum(1 for audit in audits if audit.get("gap")),
            "period_memory_override_count": sum(
                1 for audit in audits if audit.get("override_reason")
            ),
            "period_memory_contract_gap_count": sum(
                1
                for audit in audits
                if str(audit.get("status") or "").strip()
                in {"missing_override_reason", "missing_coverage_gap"}
            ),
            "period_memory_missing_metadata": missing_metadata,
            "period_memory_repair_actions": repair_actions,
            "metadata_contract_audit_resolutions": audit_resolutions,
            "metadata_contract_repair_notes": repair_notes,
        }

    def _normalize_slot(self, slot: str) -> str:
        normalized = str(slot or "").strip().lower().replace("-", "_")
        if normalized in {"mindset", "morning", "open"}:
            normalized = "pre_open"
        if normalized in {"noon", "lunch", "mid"}:
            normalized = "midday"
        if normalized in {"close", "closing", "review"}:
            normalized = "post_close"
        if normalized in {"reflect", "reflection"}:
            normalized = "block_reflection"
        if normalized not in VALID_RITUAL_SLOTS:
            return "pre_open"
        return normalized

    def _trading_day(self, now: datetime | None = None) -> str:
        return (now or datetime.now(KST)).astimezone(KST).date().isoformat()

    def _default_memory_files(self) -> dict[Path, str]:
        return {
            self.root / "persona.md": "\n".join(
                [
                    "# HERMES Persona",
                    "",
                    "너의 이름은 쥬다.",
                    "쥬는 HERMES 안에서 사용자의 한국장 투자 파트너로 행동한다.",
                    "말투는 친근하지만, 판단은 적극적으로 수익 기회를 찾고 검증된 가격 구조에서는 과감하다.",
                    "과열 매매와 무근거 추격은 피하되, 근거·반론·자료 공백·오늘의 마음가짐을 함께 보며 실행 가능한 블록을 놓치지 않는다.",
                    "블록 트레이딩에서는 각 블록을 독립된 약속으로 보고, 목표가/손절가/논리를 끝까지 추적한다.",
                    "모든 블록을 단기처럼 취급하지 않는다. 단기, 중기, 장기, ETF/core, 현금은 서로 다른 역할과 청산 기준을 가진다.",
                    "정규장 30분 매니저 루프에서는 모든 horizon을 함께 검토하지만, 단기·중기·장기·ETF/core의 행동 권한은 다르게 적용한다.",
                    "기존 보유분도 새 매수 주문이 아니라 “보유 잔고를 블록 원장에 배정하는 일”로 보고, 쥬가 평단/수량/현재 손익/리스크를 바탕으로 블록화 제안을 만든다.",
                    "모든 메시지는 실거래 판단과 블록 매매 운영을 위해 작성한다.",
                    "",
                ]
            ),
            self.root / "policies" / "init.md": "\n".join(
                [
                    "# Memory Init Policy",
                    "",
                    "- 리포트, RAG, 밸류, 고래/세시반, KIS 계좌, 블록 거래 결과를 원천 근거로 사용한다.",
                    "- 새 기억은 원본을 그대로 복사하지 않고 짧은 판단 단위로 압축한다.",
                    "- 신뢰도가 낮은 내용은 active policy가 아니라 observation 또는 candidate로 둔다.",
                    "",
                ]
            ),
            self.root / "policies" / "update.md": "\n".join(
                [
                    "# Memory Update Policy",
                    "",
                    "- 매일 장전/장중/마감 저널을 남긴다.",
                    "- 닫힌 블록은 진입 가설, 룰 준수, 청산 품질, 놓친 리스크로 반성한다.",
                    "- 반복 확인된 교훈만 운용 정책으로 승격한다.",
                    "",
                ]
            ),
            self.root / "policies" / "trading.md": "\n".join(
                [
                    "# Trading Memory Policy",
                    "",
                    "- 메모리는 LLM 블록 매니저의 판단 보조 자료다.",
                    "- kill switch, 현금 초과 금지, 보유수량 초과 금지, 중복주문 방지는 항상 우선한다.",
                    "- 목표는 실거래 수익 창출이다. 쥬는 매 루프마다 실행 가능한 신규 블록, 대기진입 블록, 기존 블록 증액·보호·청산 후보를 적극적으로 찾는다.",
                    "- 검증 경고는 거래 중지가 아니다. 하드 안전 게이트가 막지 않는 한 probe/대기진입으로 표본을 축적하고, 검증될수록 sizing을 확대한다.",
                    "- 저평가/고평가 판단은 단독 진입 근거가 아니라 가격 부담 보조 신호다.",
                    "- 손절/목표가 도달은 LLM 없이 룰 실행기가 처리한다.",
                    "- 모든 블록을 단기처럼 취급하지 않는다. 단기, 중기, 장기, ETF/core, 현금은 서로 다른 역할과 청산 기준을 가진다.",
                    "- 정규장 30분 매니저 루프에서는 모든 horizon을 함께 검토하지만, 단기·중기·장기·ETF/core의 행동 권한은 다르게 적용한다.",
                    "- 현금은 쉬고 있는 돈이 아니라 변동성 방어와 다음 기회 대기를 위한 관리 대상이다.",
                    "- 기본 성향은 저평가·양질·눌림 가격을 기다리는 value-pullback 운용이다. 강한 모멘텀은 예외로 다루고, 좋은 가격 구조가 없으면 대기 블록이나 관찰로 남긴다.",
                    "",
                ]
            ),
            self.root / "policies" / "telegram.md": "\n".join(
                [
                    "# Telegram Ritual Policy",
                    "",
                    "- 08:30 장전 마음가짐: 오늘 조심할 점과 집중할 블록을 정리한다.",
                    "- 11:40 장중 점검: 오전 판단 유효성과 과매매 위험을 확인한다.",
                    "- 15:45 마감 리뷰: 성과, 실수, 다음 장으로 넘길 기억을 정리한다.",
                    "- 메시지는 짧고 따뜻하게 쓰되, 진입/청산 판단·조건·무효화 기준을 분명히 남긴다.",
                    "",
                ]
            ),
            self.root / "skills" / "block_manager.md": "\n".join(
                [
                    "---",
                    "skill_id: jue.block_manager.v1",
                    "owner: HERMES",
                    "purpose: KIS block manager live trading decisions",
                    "---",
                    "# 쥬 블록 매니저 스킬",
                    "",
                    "- 각 블록은 같은 종목이라도 독립된 약속이다.",
                    "- 신규 블록은 근거, 목표가, 손절가, 무효화 조건, 수량 이유를 함께 가져야 한다.",
                    "- 새 블록에는 horizon(short, mid, long, core_etf)과 allocation_reason을 붙인다.",
                    "- 매니저 루프마다 create-now, wait-for-price, add, protect, close 후보를 적극적으로 검토한다.",
                    "- validation/probe 상태는 관망 명령이 아니다. 안전 게이트가 허용하면 작은 탐색 블록이나 대기진입 블록으로 실거래 표본을 쌓는다.",
                    "- 단기 블록은 장중 수급과 가격 확인을 적극 반영하고, 중기·장기·ETF/core 블록은 비중과 thesis를 중심으로 본다.",
                    "- 정규장 30분 매니저 루프에서는 모든 horizon을 함께 검토한다.",
                    "- 신규 KIS 진입은 기본적으로 value-pullback 관점에서 설계한다. 저평가/적정가 대비 할인, 낮은 리스크 가격 위치, 명확한 무효화 가격이 없으면 즉시매수보다 wait_for_price를 우선한다.",
                    "- 기존 블록 수정은 가격 경로, 마켓 펄스, 보유 비중, 메모리 정책을 비교해서 제안한다.",
                    "- 청산 의도는 이유와 트리거를 남기고, 실제 주문은 안전 게이트와 룰 실행기가 검증한다.",
                    "- 불확실하면 큰 결론보다 작은 블록, 관찰, 목표/손절 재확인을 우선한다.",
                ]
            ),
            self.root / "skills" / "market_judge.md": "\n".join(
                [
                    "---",
                    "skill_id: jue.market_judge.v1",
                    "owner: HERMES",
                    "purpose: intraday account and market judgment",
                    "---",
                    "# 쥬 장중 판단 스킬",
                    "",
                    "- 국장1 현금, 보유 비중, 평가손익, 가용 수량을 먼저 확인한다.",
                    "- 판단은 보유 블록, 신규 후보, 시장 국면을 분리해서 쓴다.",
                    "- 마켓 펄스 v3의 지수, 수급, 프로그램, 환율, 선물 베이시스, 섹터, 블록 노출을 반영한다.",
                    "- 장중 판단은 현금 방치가 아니라 다음 수익 기회의 가격·수급·증거 조건을 좁히는 작업이다.",
                    "- 결과는 stance, account_action, horizon, confidence, reasons, risks, triggers, data_gaps로 정리한다.",
                    "- 수량과 주문가는 블록 트레이더가 검증하므로 장중 판단은 트리거와 운영 의도에 집중한다.",
                ]
            ),
            self.root / "skills" / "risk_manager.md": "\n".join(
                [
                    "---",
                    "skill_id: jue.risk_manager.v1",
                    "owner: HERMES",
                    "purpose: risk checks for live block trading",
                    "---",
                    "# 쥬 리스크 매니저 스킬",
                    "",
                    "- kill switch, 현금 초과 금지, 보유수량 초과 금지, 중복주문 방지는 항상 우선한다.",
                    "- 수익 중인 블록은 목표가 근접, 급락 반전, 시장 압박을 함께 본다.",
                    "- 손실 중인 블록은 손절가까지 거리, thesis 훼손, 데이터 공백을 분리한다.",
                    "- 리스크 관리는 소극적 회피가 아니라 기대값이 좋은 블록에 위험 예산을 집중하기 위한 선별 장치다.",
                    "- short 블록은 목표/손절 가격 터치 시 룰 실행기 자동 청산이 가능하다.",
                    "- mid, long, core_etf 블록은 가격 터치만으로 단기처럼 자동 청산하지 않고 쥬의 30분 전체 판단에서 행동을 고른다.",
                    "- 블록 노출이 특정 섹터나 시장에 몰리면 신규 진입보다 비중 점검을 우선한다.",
                    "- 정책 룰은 진입 금지가 아니라 수량, 확인 조건, 목표/손절, 리스크 노트 보정으로 사용한다.",
                ]
            ),
            self.root / "skills" / "reflection.md": "\n".join(
                [
                    "---",
                    "skill_id: jue.reflection.v1",
                    "owner: HERMES",
                    "purpose: post-trade reflection and memory update",
                    "---",
                    "# 쥬 거래 반성 스킬",
                    "",
                    "- 닫힌 블록은 진입 가설, 가격 경로, 룰 준수, 청산 품질, 놓친 위험을 분리해서 기록한다.",
                    "- 한 번의 손익으로 종목을 단정하지 않는다.",
                    "- 반복된 교훈만 observation, caution, preference 정책 후보로 승격한다.",
                    "- 반성은 다음 블록의 수량, 확인 조건, 목표/손절 보정, 수익 기회 탐색 범위 확대에 쓰일 수 있어야 한다.",
                ]
            ),
        }

    def _legacy_strategy_extract(self) -> str:
        path = Path(self.config.strategy_md_path)
        if not path.exists():
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        picked: list[str] = [
            "# Legacy Strategy Extract",
            "",
            "기존 strategy_krx.md에서 제목/원칙처럼 보이는 줄만 추린 참고 메모리입니다.",
            "원본 전체를 복사하지 않고, 이후 업데이트에서 구조화 메모리로 재평가합니다.",
            "",
        ]
        for line in lines:
            text = line.strip()
            if not text:
                continue
            if text.startswith("#") or text.startswith(("-", "*")):
                picked.append(text[:240])
            if len(picked) >= 80:
                break
        return "\n".join(picked).strip() + "\n" if len(picked) > 4 else ""

    def _read_memory_file(self, relative: str, *, limit: int) -> str:
        path = self.root / relative
        if not path.exists():
            return ""
        try:
            return _truncate(path.read_text(encoding="utf-8"), limit)
        except OSError:
            return ""

    def _decision_skills(self) -> dict[str, dict[str, Any]]:
        skills: dict[str, dict[str, Any]] = {}
        for skill_id in ("block_manager", "market_judge", "risk_manager", "reflection"):
            relative = f"skills/{skill_id}.md"
            content = self._read_memory_file(relative, limit=1800)
            version = (
                f"jue.{skill_id}.v1"
                if f"skill_id: jue.{skill_id}.v1" in content
                else ""
            )
            skills[skill_id] = {
                "skill_id": skill_id,
                "version": version,
                "content_md": content,
            }
        return skills

    def _build_ritual_prompt(
        self,
        *,
        slot: str,
        trading_day: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "task": "Write and update HERMES investment memory. Return JSON only.",
            "language_policy": jue_language_policy(),
            "slot": slot,
            "slot_label": SLOT_LABELS.get(slot, slot),
            "trading_day": trading_day,
            "persona": self._read_memory_file("persona.md", limit=2400),
            "policies": {
                "trading": self._read_memory_file("policies/trading.md", limit=2400),
                "update": self._read_memory_file("policies/update.md", limit=1800),
                "telegram": self._read_memory_file("policies/telegram.md", limit=1400),
                "active": self.active_policies(limit=20),
            },
            "context": _compact_ritual_context(
                context,
                limit=self.config.context_max_chars,
            ),
            "output_schema": {
                "title": "short Korean title",
                "message_md": "Telegram-ready Korean markdown, warm partner tone",
                "memory_updates": {
                    "symbols": [{"symbol": "000000", "summary_md": "string", "confidence": 0.0}],
                    "blocks": [{"block_id": "string", "summary_md": "string", "confidence": 0.0}],
                    "notes": [{"key": "regime|sector|general", "summary_md": "string", "confidence": 0.0}],
                },
                "policy_changes": [
                    {
                        "policy_id": "stable-id",
                        "action": "observe|prefer|caution",
                        "strength": "soft|observation|preference|caution",
                        "reason": "string",
                        "confidence": 0.0,
                    }
                ],
            },
            "telegram_display_policy": [
                "종목 표기는 종목명 우선으로 쓴다. 예: 삼성전자 (005930).",
                "컨텍스트에 종목명이 있으면 005930처럼 코드만 단독으로 쓰지 않는다.",
                "종목명이 불명확할 때만 코드를 단독 표기한다.",
            ],
            "safety": [
                "Write for HERMES live block-trading judgment, not generic commentary.",
                "When a trading action is implied, express it as block intent, validation trigger, target, stop, or policy memory.",
                "Do not create hard strategy filters; safety gates are separate from memory policy.",
            ],
        }

    async def _complete_json(self, prompt: dict[str, Any]) -> tuple[Any | None, str, str]:
        if not self.codex_runtime or not getattr(self.codex_runtime, "ready", False):
            return None, "error", "codex_runtime_unavailable"
        payload = {
            "model": getattr(self.codex_runtime, "resolved_model", "gpt-5.5"),
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
            "telemetry": {
                "component": "investment_memory",
                "operation": str(prompt.get("slot") or "memory_update"),
            },
            "messages": [
                {"role": "system", "content": "Return only JSON matching the requested schema."},
                {"role": "user", "content": _json_dumps(prompt)},
            ],
        }
        result = await self.codex_runtime.complete(payload)
        if not bool(result.get("ok")):
            return None, "error", _compact_error_message(
                result.get("error") or "llm_failed"
            )
        text = str(result.get("content") or "").strip()
        try:
            return json.loads(text), "llm", ""
        except json.JSONDecodeError as exc:
            return None, "error", f"llm_json_error:{exc}"

    def _deterministic_ritual(
        self,
        *,
        slot: str,
        trading_day: str,
        context: dict[str, Any],
        error_message: str = "",
    ) -> dict[str, Any]:
        account = context.get("account") if isinstance(context.get("account"), dict) else {}
        blocks_payload = context.get("blocks") if isinstance(context.get("blocks"), dict) else {}
        blocks = list(blocks_payload.get("blocks") or []) if isinstance(blocks_payload, dict) else []
        open_blocks = [
            row
            for row in blocks
            if str(row.get("status") or "") in {"open", "entry_pending", "exit_pending"}
        ]
        cash = _safe_float(account.get("cash_krw"))
        position_count = int(_safe_float(account.get("position_count")))
        label = SLOT_LABELS.get(slot, slot)
        lines = [
            f"HERMES {label}",
            "",
            f"오늘 기준일: {trading_day}",
            f"국장1 현금: {cash:,.0f}원 · 보유 {position_count}종목 · 활성 블록 {len(open_blocks)}개",
            "",
            "오늘은 근거가 분명한 블록만 차분히 보고, 자료 공백이 있는 판단은 한 박자 늦춥니다.",
            "목표가와 손절가는 약속이고, 룰 실행기의 신호를 감정으로 덮지 않습니다.",
        ]
        etf_core = context.get("etf_core") if isinstance(context.get("etf_core"), dict) else {}
        research = etf_core.get("research") if isinstance(etf_core.get("research"), dict) else {}
        etf_items = research.get("items") if isinstance(research.get("items"), list) else []
        has_etf_alert = any(
            isinstance(row, dict) and str(row.get("state") or "") in {"stale", "error", "missing"}
            for row in etf_items
        )
        core_blocks = (
            etf_core.get("active_core_blocks")
            if isinstance(etf_core.get("active_core_blocks"), list)
            else []
        )
        if has_etf_alert or core_blocks:
            detail = "리서치 stale/error/missing 확인" if has_etf_alert else "활성 ETF/Core 블록 확인"
            lines.append(
                "ETF/Core은 종목 밸류에이션이 아니라 노출·분산·리밸런스 관점으로 봅니다. "
                f"{detail}."
            )
        if slot == "midday":
            lines.append("오전 판단이 아직 유효한지, 추격 매수 욕심이 생긴 블록은 없는지 확인합니다.")
        elif slot == "post_close":
            lines.append("마감 후에는 수익보다 과정 품질을 먼저 보고, 내일로 넘길 교훈만 남깁니다.")
            llm_usage = context.get("llm_usage") if isinstance(context.get("llm_usage"), dict) else {}
            usage_total = llm_usage.get("total") if isinstance(llm_usage.get("total"), dict) else {}
            usage_rows = llm_usage.get("by_component") if isinstance(llm_usage.get("by_component"), list) else []
            top_component = "-"
            if usage_rows:
                top_row = max(
                    [row for row in usage_rows if isinstance(row, dict)],
                    key=lambda row: _safe_float(row.get("total_tokens")),
                    default={},
                )
                top_component = str(top_row.get("component") or "-")
            if usage_total:
                lines.extend(
                    [
                        f"오늘 LLM 호출/토큰: {int(_safe_float(usage_total.get('call_count'))):,}회 / "
                        f"{int(_safe_float(usage_total.get('total_tokens'))):,} tokens.",
                        f"가장 많이 쓴 컴포넌트: {top_component}.",
                    ]
                )
        elif slot == "block_reflection":
            lines.append("닫힌 블록은 종목 탓으로 넘기지 않고, 진입 가설과 청산 규칙을 분리해서 복기합니다.")
        if error_message:
            lines.extend(
                [
                    "",
                    f"LLM 메모리 생성은 실패했습니다: {_compact_error_message(error_message)}",
                ]
            )
        lines.extend(["", "실거래 판단용입니다. 주문은 HERMES 안전 게이트와 블록 규칙을 통과한 경우에만 실행됩니다."])
        return {
            "title": label,
            "message_md": "\n".join(lines),
            "memory_updates": {},
            "policy_changes": [],
        }

    def _apply_memory_updates(self, output: dict[str, Any], *, source_run_id: int) -> None:
        updates = output.get("memory_updates") if isinstance(output.get("memory_updates"), dict) else {}
        for row in list(updates.get("symbols") or []):
            if not isinstance(row, dict) or not _is_symbol(row.get("symbol")):
                continue
            symbol = str(row.get("symbol")).strip()
            summary = _clean_text(row.get("summary_md") or row.get("summary"), limit=3000)
            if not summary:
                continue
            confidence = _safe_float(row.get("confidence"))
            evidence = self._memory_update_evidence(
                row,
                memory_type="symbol",
                key=symbol,
            )
            self.repository.save_insight(
                memory_type="symbol",
                key=symbol,
                status="active",
                confidence=confidence,
                summary_md=summary,
                evidence=evidence,
                source_run_id=source_run_id,
            )
            self._append_memory_note("symbols", f"{symbol}.md", summary, source_run_id=source_run_id)

        for row in list(updates.get("blocks") or []):
            if not isinstance(row, dict):
                continue
            block_id = _clean_text(row.get("block_id"), limit=160)
            summary = _clean_text(row.get("summary_md") or row.get("summary"), limit=3000)
            if not block_id or not summary:
                continue
            confidence = _safe_float(row.get("confidence"))
            evidence = self._memory_update_evidence(
                row,
                memory_type="block",
                key=block_id,
            )
            self.repository.save_insight(
                memory_type="block",
                key=block_id,
                status="active",
                confidence=confidence,
                summary_md=summary,
                evidence=evidence,
                source_run_id=source_run_id,
            )
            self._append_memory_note("blocks", f"{block_id}.md", summary, source_run_id=source_run_id)

        for row in list(updates.get("notes") or []):
            if not isinstance(row, dict):
                continue
            key = _clean_text(row.get("key") or "general", limit=120)
            summary = _clean_text(row.get("summary_md") or row.get("summary"), limit=3000)
            if not summary:
                continue
            memory_type = "general"
            if key.startswith("sector:"):
                memory_type = "sector"
            elif key.startswith("regime:"):
                memory_type = "regime"
            evidence = self._memory_update_evidence(
                row,
                memory_type=memory_type,
                key=key,
            )
            self.repository.save_insight(
                memory_type=memory_type,
                key=key,
                status="active",
                confidence=_safe_float(row.get("confidence")),
                summary_md=summary,
                evidence=evidence,
                source_run_id=source_run_id,
            )

        for row in list(output.get("policy_changes") or []):
            if not isinstance(row, dict):
                continue
            policy_id = _clean_text(row.get("policy_id") or row.get("id"), limit=160)
            if not policy_id:
                continue
            strength = str(row.get("strength") or "soft").strip().lower()
            action = str(row.get("action") or "observe").strip().lower()
            confidence = _safe_float(row.get("confidence"))
            status = self._policy_status(strength=strength, action=action, confidence=confidence)
            reason = _clean_text(row.get("reason"), limit=1200)
            self.repository.save_policy_change(
                policy_id=policy_id,
                action=action,
                strength=strength,
                status=status,
                reason=reason,
                confidence=confidence,
                source_run_id=source_run_id,
            )
            self.repository.save_insight(
                memory_type="policy",
                key=policy_id,
                status=status,
                confidence=confidence,
                summary_md=reason or action,
                evidence=self._memory_update_evidence(
                    row,
                    memory_type="policy",
                    key=policy_id,
                ),
                source_run_id=source_run_id,
            )

    @staticmethod
    def _memory_update_evidence(
        row: dict[str, Any],
        *,
        memory_type: str,
        key: str,
    ) -> list[dict[str, Any]]:
        evidence = _evidence_items(row.get("evidence"))
        scope = _normalize_memory_scope(row.get("memory_scope") or row.get("scope") or row.get("venue"))
        if not scope:
            scope = _infer_memory_scope(memory_type=memory_type, key=key, evidence=evidence)
        transferability = _normalize_transferability(row.get("transferability"))
        if not transferability:
            transferability = _default_transferability(memory_type=memory_type, scope=scope)
        metadata = {
            "memory_scope": scope,
            "transferability": transferability,
            "source": "investment_memory_update",
        }
        if evidence and evidence[0].get("memory_scope") == scope:
            evidence[0] = {**metadata, **evidence[0]}
            return evidence
        return [metadata, *evidence]

    def _policy_status(self, *, strength: str, action: str, confidence: float) -> str:
        _ = (strength, action, confidence)
        return "candidate"

    def _append_memory_note(
        self,
        directory: str,
        filename: str,
        summary: str,
        *,
        source_run_id: int,
    ) -> None:
        path = self.root / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {filename}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fp:
            fp.write(
                "\n".join(
                    [
                        f"\n## {utc_now_iso()} · run {source_run_id}",
                        "",
                        summary.strip(),
                        "",
                    ]
                )
            )

    def _write_journal_file(
        self,
        *,
        trading_day: str,
        slot: str,
        title: str,
        message_md: str,
        context: dict[str, Any],
        output: dict[str, Any],
    ) -> Path:
        directory = self.root / "journals" / trading_day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{slot}.md"
        body = "\n".join(
            [
                "---",
                f"trading_day: {trading_day}",
                f"slot: {slot}",
                f"title: {title}",
                f"created_at: {utc_now_iso()}",
                "---",
                "",
                f"# {title}",
                "",
                message_md.strip(),
                "",
                "## Context Digest",
                "",
                "```json",
                _truncate(_json_dumps(context), 6000),
                "```",
                "",
                "## Memory Output",
                "",
                "```json",
                _truncate(_json_dumps(output), 6000),
                "```",
                "",
            ]
        )
        path.write_text(body, encoding="utf-8")
        return path

    async def _send_telegram_once(
        self,
        *,
        trading_day: str,
        slot: str,
        message: str,
    ) -> dict[str, Any]:
        if not self.config.telegram_enabled:
            result = {"ok": False, "detail": "investment_memory_telegram_disabled"}
            self.repository.record_telegram_send(
                trading_day=trading_day,
                slot=slot,
                status="disabled",
                result=result,
            )
            return result
        if self.telegram is None:
            result = {"ok": False, "detail": "telegram_bridge_missing"}
            self.repository.record_telegram_send(
                trading_day=trading_day,
                slot=slot,
                status="missing",
                result=result,
            )
            return result
        result = await self.telegram.send_message(message)
        self.repository.record_telegram_send(
            trading_day=trading_day,
            slot=slot,
            status="sent" if bool(result.get("ok")) else "failed",
            result=result,
        )
        return result
