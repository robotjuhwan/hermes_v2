from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.live_authority import (
    LiveAuthorityConfig,
    apply_active_revision_evidence_gate,
    apply_trading_validation_gate,
    build_authority_packet,
    compact_live_authority_for_status,
    performance_lanes_for_venue,
)
from tradecraft.services.live_edge import (
    EvidenceOutcome,
    LiveEdgeRepository,
    compute_edge_scorecard,
)
from tradecraft.services.kr_equity_pattern_lab import (
    KREquityPatternLabConfig,
    KREquityPatternLabService,
)
from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
)
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.trading_validation import (
    TradingValidationConfig,
    TradingValidationRepository,
    TradingValidationService,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_age_sec(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds(), 0.0)


def _annotate_trading_validation_freshness(
    latest: dict[str, Any],
    settings: Any,
) -> dict[str, Any]:
    max_age_sec = max(int(getattr(settings, "trading_validation_max_age_sec", 1800)), 1)
    age_sec = _iso_age_sec(latest.get("computed_at"))
    latest["max_age_sec"] = max_age_sec
    latest["age_sec"] = round(age_sec, 3) if age_sec is not None else None
    if str(latest.get("status") or "").strip().lower() == "empty":
        latest["stale"] = False
        latest["stale_reason"] = ""
        return latest
    stale = age_sec is None or age_sec > max_age_sec
    latest["stale"] = stale
    latest["stale_reason"] = (
        "invalid_or_missing_computed_at"
        if age_sec is None
        else f"age_sec={int(age_sec)},max_age_sec={max_age_sec}"
        if stale
        else ""
    )
    return latest


def _authority_config(settings: Any) -> LiveAuthorityConfig:
    return LiveAuthorityConfig(
        max_scale_multiplier=float(
            getattr(settings, "live_authority_max_scale_multiplier", 1.5)
        ),
        min_samples_to_scale=int(
            getattr(settings, "live_authority_min_samples_to_scale", 10)
        ),
    )


def _trading_validation_context(settings: Any, *, venue: str) -> dict[str, Any]:
    db_path = str(
        getattr(settings, "trading_validation_db_path", ".runtime/trading_validation.db")
    )
    active_revision_id = _compact_text(
        getattr(settings, "jue_strategy_revision_id", ""),
        limit=120,
    )
    try:
        latest = TradingValidationRepository(db_path).latest(
            venue=venue,
            strategy_revision_id=active_revision_id,
        )
    except Exception as exc:
        return {"status": "error", "db_path": db_path, "error_message": str(exc)}
    return _annotate_trading_validation_freshness({
        "status": latest.get("status"),
        "db_path": db_path,
        "run_id": latest.get("run_id", ""),
        "computed_at": latest.get("computed_at", ""),
        "strategy_revision_id": latest.get(
            "strategy_revision_id",
            active_revision_id,
        ),
        "summary": latest.get("summary", {}),
        "payload": latest.get("payload", {}),
    }, settings)


def _apply_trading_validation_gate(
    packet: dict[str, Any],
    validation: dict[str, Any],
    *,
    config: LiveAuthorityConfig,
) -> dict[str, Any]:
    return apply_trading_validation_gate(packet, validation, config=config)


def _active_revision_evidence_packet(
    *,
    strategy_revision_id: str,
    venue: str,
    packet: dict[str, Any],
    validation: dict[str, Any],
    performance_lanes: list[dict[str, Any]],
    pending_block_evidence: dict[str, Any] | None = None,
    min_samples_to_scale: int,
) -> dict[str, Any]:
    if not strategy_revision_id:
        return {}
    payload = validation.get("payload") if isinstance(validation.get("payload"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    validation_summary = (
        validation.get("summary")
        if isinstance(validation.get("summary"), dict)
        else payload.get("summary")
        if isinstance(payload.get("summary"), dict)
        else {}
    )
    active_evidence = (
        metrics.get("active_revision_evidence")
        if isinstance(metrics.get("active_revision_evidence"), dict)
        else {}
    )
    active_mode = _compact_text(
        active_evidence.get("status")
        or validation_summary.get("active_revision_sample_mode"),
        limit=100,
    )
    validation_sample_role = _compact_text(
        active_evidence.get("validation_sample_role")
        or validation_summary.get("validation_sample_role"),
        limit=100,
    )
    active_sample_count = _safe_int(
        active_evidence.get("active_sample_count")
        if active_evidence
        else validation_summary.get("active_revision_sample_count")
    )
    if not active_evidence and "active_revision_sample_count" not in validation_summary:
        active_sample_count = _safe_int(metrics.get("sample_count"))
    validation_sample_count = active_sample_count
    legacy_proxy_sample_count = _safe_int(
        active_evidence.get("legacy_proxy_sample_count")
    )
    lane_alpha_count = sum(int(row.get("alpha_count") or 0) for row in performance_lanes)
    scorecard_count = len(packet.get("scorecards") or [])
    hard_blocking_count = int(validation_summary.get("hard_blocking_count") or 0)
    fail_count = int(validation_summary.get("fail_count") or 0)
    missing_count = int(validation_summary.get("missing_count") or 0)
    min_samples = max(int(min_samples_to_scale or 1), 1)
    effective_sample_count = max(active_sample_count, lane_alpha_count)
    pending_evidence = (
        pending_block_evidence
        if isinstance(pending_block_evidence, dict)
        else {}
    )
    pending_block_count = int(pending_evidence.get("pending_block_count") or 0)
    has_proxy_only_evidence = bool(
        active_mode == "no_active_revision_samples_with_proxy"
        or validation_sample_role == "legacy_proxy_metrics_no_scale"
        or legacy_proxy_sample_count > 0
    )
    if effective_sample_count <= 0:
        if pending_block_count > 0:
            status = (
                "active_revision_samples_pending_close_with_proxy"
                if has_proxy_only_evidence
                else "active_revision_samples_pending_close"
            )
            authority_posture = "small_probe_until_pending_blocks_close"
        elif active_mode:
            status = active_mode
            authority_posture = (
                active_evidence.get("authority_posture")
                or "probe_only_until_active_revision_samples_close"
            )
        else:
            status = "no_active_revision_samples"
            authority_posture = "observe_only_until_new_revision_trades_close"
    elif effective_sample_count < min_samples:
        status = (
            "active_revision_sample_building"
            if active_mode == "active_revision_sample_building"
            else "insufficient_active_revision_samples"
        )
        authority_posture = "small_probe_until_min_samples"
    elif fail_count > 0 or hard_blocking_count > 0:
        status = "active_revision_blocked_by_validation"
        authority_posture = "repair_before_scale_up"
    elif scorecard_count <= 0:
        status = "active_revision_scorecards_missing"
        authority_posture = "refresh_live_edge_before_scale_up"
    else:
        status = "active_revision_evidence_present"
        authority_posture = "follow_lane_authority"
    scale_up_allowed = bool(
        status == "active_revision_evidence_present"
        and not hard_blocking_count
        and not fail_count
        and scorecard_count > 0
        and not has_proxy_only_evidence
        and bool(
            active_evidence.get("scale_up_allowed", True)
            if active_evidence
            else True
        )
    )
    evidence_packet = {
        "version": "active_revision_evidence_v1",
        "venue": venue,
        "strategy_revision_id": strategy_revision_id,
        "status": status,
        "validation_sample_role": validation_sample_role,
        "legacy_proxy_gate_mode": active_evidence.get("legacy_proxy_gate_mode", ""),
        "authority_posture": authority_posture,
        "active_sample_count": active_sample_count,
        "effective_sample_count": effective_sample_count,
        "validation_sample_count": validation_sample_count,
        "legacy_proxy_sample_count": legacy_proxy_sample_count,
        "lane_alpha_count": lane_alpha_count,
        "pending_block_count": pending_block_count,
        "pending_block_status_counts": pending_evidence.get(
            "pending_block_status_counts",
            {},
        ),
        "pending_block_lane_counts": pending_evidence.get(
            "pending_block_lane_counts",
            {},
        ),
        "missing_revision_nonterminal_count": int(
            pending_evidence.get("missing_revision_nonterminal_count") or 0
        ),
        "pending_evidence_role": pending_evidence.get("evidence_role", ""),
        "min_samples_to_scale": min_samples,
        "scorecard_count": scorecard_count,
        "performance_lane_count": len(performance_lanes),
        "validation_fail_count": fail_count,
        "validation_missing_count": missing_count,
        "hard_blocking_count": hard_blocking_count,
        "scale_up_allowed": scale_up_allowed,
        "can_scale_from_proxy": bool(active_evidence.get("can_scale_from_proxy")),
        "block_design_requirement": (
            active_evidence.get("block_design_requirement")
            or "Use only small probe or waiting-entry blocks until this active "
            "strategy revision has its own closed-trade evidence, scorecards, "
            "and clear validation gate."
        ),
        "next_action": (
            active_evidence.get("next_action")
            or "collect_active_revision_probe_samples_before_scaling"
        ),
    }
    for key in (
        "legacy_proxy_failed_discipline_ids",
        "legacy_proxy_missing_core_discipline_ids",
        "active_revision_sample_building_failed_discipline_ids",
        "sample_building_gate_mode",
    ):
        if active_evidence.get(key):
            evidence_packet[key] = active_evidence[key]
    return evidence_packet


def build_live_authority_payload(settings: Any) -> dict[str, Any]:
    edge_repo = LiveEdgeRepository(settings.live_evaluator_db_path)
    performance_db_path = getattr(settings, "live_performance_db_path", None)
    if not performance_db_path:
        performance_db_path = Path(settings.live_evaluator_db_path).with_name(
            "live_performance.db"
        )
    performance_repo = LivePerformanceRepository(performance_db_path)
    config = _authority_config(settings)
    active_revision_id = _compact_text(
        getattr(settings, "jue_strategy_revision_id", ""),
        limit=120,
    )
    performance_summary = performance_repo.summary(
        strategy_revision_id=active_revision_id,
    )

    def scorecards_for_venue(venue: str) -> list[dict[str, Any]]:
        if active_revision_id:
            return edge_repo.list_scorecards(
                venue=venue,
                strategy_revision_id=active_revision_id,
                limit=50,
            )
        return edge_repo.list_scorecards(venue=venue, limit=50)

    kis_packet = build_authority_packet(
        venue="kis",
        scorecards=scorecards_for_venue("kis"),
        config=config,
    )
    binance_packet = build_authority_packet(
        venue="binance",
        scorecards=scorecards_for_venue("binance"),
        config=config,
    )
    if active_revision_id:
        kis_packet["active_strategy_revision_id"] = active_revision_id
        binance_packet["active_strategy_revision_id"] = active_revision_id
    kis_validation = _trading_validation_context(settings, venue="kis")
    binance_validation = _trading_validation_context(
        settings,
        venue="binance",
    )
    kis_packet = _apply_trading_validation_gate(
        kis_packet,
        kis_validation,
        config=config,
    )
    binance_packet = _apply_trading_validation_gate(
        binance_packet,
        binance_validation,
        config=config,
    )
    kis_lanes = performance_lanes_for_venue(performance_summary, "kis", limit=20)
    binance_lanes = performance_lanes_for_venue(
        performance_summary,
        "binance",
        limit=20,
    )
    if kis_lanes:
        kis_packet["performance_lanes"] = kis_lanes
    if binance_lanes:
        binance_packet["performance_lanes"] = binance_lanes
    if active_revision_id:
        kis_pending_revision_blocks = _pending_active_revision_block_evidence(
            getattr(settings, "kis_block_trader_db_path", ""),
            strategy_revision_id=active_revision_id,
            venue="kis",
        )
        binance_pending_revision_blocks = _pending_active_revision_block_evidence(
            getattr(settings, "binance_block_trader_db_path", ""),
            strategy_revision_id=active_revision_id,
            venue="binance",
        )
        kis_packet["pending_active_revision_blocks"] = kis_pending_revision_blocks
        binance_packet["pending_active_revision_blocks"] = (
            binance_pending_revision_blocks
        )
        kis_active_revision_evidence = _active_revision_evidence_packet(
            strategy_revision_id=active_revision_id,
            venue="kis",
            packet=kis_packet,
            validation=kis_validation,
            performance_lanes=kis_lanes,
            pending_block_evidence=kis_pending_revision_blocks,
            min_samples_to_scale=int(config.min_samples_to_scale),
        )
        kis_packet = apply_active_revision_evidence_gate(
            kis_packet,
            kis_active_revision_evidence,
        )
        binance_active_revision_evidence = _active_revision_evidence_packet(
            strategy_revision_id=active_revision_id,
            venue="binance",
            packet=binance_packet,
            validation=binance_validation,
            performance_lanes=binance_lanes,
            pending_block_evidence=binance_pending_revision_blocks,
            min_samples_to_scale=int(config.min_samples_to_scale),
        )
        binance_packet = apply_active_revision_evidence_gate(
            binance_packet,
            binance_active_revision_evidence,
        )
    latest_state = RuntimeStateStore(
        getattr(settings, "live_evaluator_state_path", ".runtime/live_evaluator.json")
    ).read_snapshot() or {}
    latest_repair_execution = (
        latest_state.get("repair_execution")
        if isinstance(latest_state.get("repair_execution"), dict)
        else {}
    )
    kis_repair_execution = compact_repair_execution_for_venue(
        latest_repair_execution,
        "kis",
    )
    binance_repair_execution = compact_repair_execution_for_venue(
        latest_repair_execution,
        "binance",
    )
    if kis_repair_execution:
        kis_packet["repair_execution"] = kis_repair_execution
    if binance_repair_execution:
        binance_packet["repair_execution"] = binance_repair_execution
    return {
        "status": "ok",
        "edge": edge_repo.status(),
        "performance": performance_summary,
        "venues": {
            "kis": kis_packet,
            "binance": binance_packet,
        },
    }


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_size(value: Any) -> int:
    try:
        return len(_json_dumps(value))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "pass", "passed", "ok"}:
        return True
    if normalized in {
        "0",
        "false",
        "no",
        "n",
        "off",
        "fail",
        "failed",
        "blocked",
        "needs_revalidation",
        "rejected",
    }:
        return False
    return None


def _compact_text(value: Any, *, limit: int = 160) -> str:
    return str(value or "").strip()[: max(int(limit), 1)]


def _compact_state_scalar(value: Any, *, limit: int = 220) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _compact_text(value, limit=limit)


def _compact_state_value(
    value: Any,
    *,
    list_limit: int = 6,
    string_limit: int = 220,
    depth: int = 0,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _compact_text(value, limit=string_limit)
    if depth >= 3:
        return {
            "_compacted": True,
            "_type": type(value).__name__,
            "_chars": _json_size(value),
        }
    if isinstance(value, (list, tuple)):
        rows = [
            _compact_state_value(
                item,
                list_limit=list_limit,
                string_limit=string_limit,
                depth=depth + 1,
            )
            for item in list(value)[:list_limit]
        ]
        if len(value) > list_limit:
            rows.append({"_omitted_item_count": len(value) - list_limit})
        return rows
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        omitted = 0
        for index, (key, raw) in enumerate(value.items()):
            key_text = _compact_text(key, limit=80)
            lower_key = key_text.lower()
            if not key_text:
                continue
            if index >= 24 or lower_key in {"raw", "payload", "raw_json"}:
                omitted += 1
                continue
            if any(
                hint in lower_key
                for hint in ("prompt", "response", "context", "packet")
            ):
                omitted += 1
                continue
            if _json_size(raw) > 8_000:
                out[key_text] = {
                    "_compacted": True,
                    "_type": type(raw).__name__,
                    "_chars": _json_size(raw),
                }
                continue
            out[key_text] = _compact_state_value(
                raw,
                list_limit=list_limit,
                string_limit=string_limit,
                depth=depth + 1,
            )
        if omitted:
            out["_omitted_key_count"] = omitted
        return out
    return _compact_text(value, limit=string_limit)


def _compact_validation_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for venue, payload in value.items():
        if not isinstance(payload, dict):
            continue
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        out[str(venue)] = {
            key: child
            for key, child in {
                "status": _compact_state_scalar(payload.get("status"), limit=80),
                "run_id": _compact_state_scalar(payload.get("run_id"), limit=120),
                "computed_at": _compact_state_scalar(payload.get("computed_at"), limit=80),
                "readiness": _compact_state_scalar(
                    payload.get("readiness") or summary.get("readiness"),
                    limit=80,
                ),
                "score": payload.get("score"),
                "discipline_count": payload.get("discipline_count"),
                "expected_discipline_count": payload.get(
                    "expected_discipline_count"
                ),
                "fail_count": summary.get("fail_count"),
                "warn_count": summary.get("warn_count"),
                "missing_count": summary.get("missing_count"),
                "summary": _compact_state_value(summary, list_limit=6, string_limit=180),
            }.items()
            if child not in (None, "", [], {})
        }
    return out


def _compact_memory_signal_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: child
        for key, child in {
            "status": _compact_state_scalar(value.get("status"), limit=80),
            "repair_manifest": _compact_state_value(
                value.get("repair_manifest"),
                list_limit=6,
                string_limit=180,
            ),
            "repair_backlog": _compact_state_value(
                value.get("repair_backlog"),
                list_limit=6,
                string_limit=180,
            ),
            "venues": _compact_state_value(
                value.get("venues"),
                list_limit=4,
                string_limit=160,
            ),
        }.items()
        if child not in (None, "", [], {})
    }


def _compact_performance_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    lanes = value.get("lanes")
    lane_count = len(lanes) if isinstance(lanes, list) else 0
    return {
        key: child
        for key, child in {
            "status": _compact_state_scalar(value.get("status"), limit=80),
            "db_path": _compact_state_scalar(value.get("db_path"), limit=160),
            "strategy_revision_id": _compact_state_scalar(
                value.get("strategy_revision_id"),
                limit=120,
            ),
            "venue_count": len(value.get("venues"))
            if isinstance(value.get("venues"), list)
            else None,
            "lane_count": lane_count,
            "lanes": _compact_state_value(lanes, list_limit=12, string_limit=180),
        }.items()
        if child not in (None, "", [], {})
    }


def _compact_authority_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    venues = value.get("venues") if isinstance(value.get("venues"), dict) else {}
    compact_venues = {
        str(venue): compact_live_authority_for_status(payload)
        for venue, payload in venues.items()
        if isinstance(payload, dict)
    }
    return {
        key: child
        for key, child in {
            "status": _compact_state_scalar(value.get("status"), limit=80),
            "edge": _compact_state_value(
                value.get("edge"),
                list_limit=4,
                string_limit=160,
            ),
            "performance": _compact_performance_state(value.get("performance")),
            "venues": compact_venues,
        }.items()
        if child not in (None, "", [], {})
    }


def _compact_live_evaluator_state(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        "service": _compact_state_scalar(result.get("service"), limit=80),
        "status": _compact_state_scalar(result.get("status"), limit=80),
        "ran_at": _compact_state_scalar(result.get("ran_at"), limit=80),
        "updated_at": _now(),
        "enabled": bool(result.get("enabled", True)),
        "state_compacted": True,
        "sync": _compact_state_value(result.get("sync"), list_limit=6, string_limit=180),
        "validation": _compact_validation_state(result.get("validation")),
        "memory_signals": _compact_memory_signal_state(result.get("memory_signals")),
        "repair_execution": _compact_state_value(
            result.get("repair_execution"),
            list_limit=10,
            string_limit=180,
        ),
        "repair_memory": _compact_state_value(
            result.get("repair_memory"),
            list_limit=6,
            string_limit=180,
        ),
        "performance": _compact_performance_state(result.get("performance")),
        "authority": _compact_authority_state(result.get("authority")),
    }


def _validation_repair_mode_for_item(item: dict[str, Any]) -> str:
    explicit = _compact_text(item.get("validation_mode"), limit=100)
    if explicit:
        return explicit
    discipline_id = _compact_text(item.get("discipline_id"), limit=80)
    if discipline_id == "data_validation":
        return "data_repair_before_trade"
    if discipline_id == "cost_simulation":
        return "cost_evidence_repair"
    if discipline_id in {
        "overfit_validation",
        "walk_forward_analysis",
        "out_of_sample_test",
    }:
        return "backtest_wfa_oos_rebuild"
    if discipline_id in {"stress_test", "regime_test"}:
        return "scenario_regime_replay"
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
    }:
        return "risk_budget_recalibration"
    if discipline_id == "capacity_analysis":
        return "capacity_depth_check"
    if discipline_id in {"correlation", "factor_exposure"}:
        return "portfolio_exposure_check"
    return "refresh_validation"


def _validation_repair_entry_posture_for_item(item: dict[str, Any]) -> str:
    explicit = _compact_text(item.get("allowed_entry_posture"), limit=120)
    if explicit:
        return explicit
    mode = _validation_repair_mode_for_item(item)
    if mode == "data_repair_before_trade":
        return "verified_quote_waiting_entry"
    if mode == "cost_evidence_repair":
        return "cost_verified_waiting_entry"
    if mode == "backtest_wfa_oos_rebuild":
        return "shadow_or_waiting_entry_only"
    if mode == "scenario_regime_replay":
        return "regime_matched_probe"
    if mode == "risk_budget_recalibration":
        return "fractional_kelly_probe"
    if mode == "capacity_depth_check":
        return "depth_checked_probe"
    if mode == "portfolio_exposure_check":
        return "exposure_capped_probe"
    return "probe_or_waiting_entry"


def _compact_repair_evidence_targets(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, raw in list(value.items())[:12]:
        clean_key = _compact_text(key, limit=80)
        if not clean_key:
            continue
        if isinstance(raw, (str, int, float, bool)):
            out[clean_key] = raw
        elif isinstance(raw, list):
            out[clean_key] = [
                item
                if isinstance(item, (int, float, bool))
                else _compact_text(item, limit=80)
                for item in raw[:6]
            ]
    return out


def _build_validation_repair_manifest(
    repair_backlog: dict[str, Any],
    *,
    limit: int = 12,
) -> dict[str, Any]:
    if not isinstance(repair_backlog, dict):
        return {
            "version": "validation_repair_manifest_v1",
            "status": "clear",
            "items": [],
            "queues": {},
        }
    raw_items: list[dict[str, Any]] = []
    venues = repair_backlog.get("venues")
    if isinstance(venues, dict):
        for venue, backlog in venues.items():
            if not isinstance(backlog, dict):
                continue
            for item in list(backlog.get("items") or []):
                if isinstance(item, dict):
                    raw_items.append({**item, "venue": item.get("venue") or venue})
    for item in list(repair_backlog.get("primary_items") or []):
        if isinstance(item, dict):
            raw_items.append(item)

    priority_rank = {"p0": 0, "p1": 1, "p2": 2}
    status_rank = {"fail": 0, "failed": 0, "missing": 1, "warn": 2, "warning": 2}
    deduped: dict[str, dict[str, Any]] = {}
    for raw in raw_items:
        venue = _compact_text(raw.get("venue"), limit=40) or "core"
        discipline_id = _compact_text(raw.get("discipline_id"), limit=80)
        if not discipline_id:
            continue
        priority = _compact_text(raw.get("priority"), limit=20).lower() or "p2"
        status = _compact_text(raw.get("status"), limit=40).lower() or "missing"
        mode = _validation_repair_mode_for_item(raw)
        posture = _validation_repair_entry_posture_for_item(raw)
        item = {
            "venue": venue,
            "discipline_id": discipline_id,
            "repair_action_id": _compact_text(raw.get("repair_action_id"), limit=140),
            "policy_id": _compact_text(raw.get("policy_id"), limit=120),
            "priority": priority,
            "status": status,
            "owner": _compact_text(raw.get("owner"), limit=80)
            or "validation_lab",
            "cadence": _compact_text(raw.get("cadence"), limit=80)
            or "next_validation_run",
            "automation_hook": _compact_text(raw.get("automation_hook"), limit=120),
            "execution_weight": _compact_text(raw.get("execution_weight"), limit=80),
            "validation_mode": mode,
            "allowed_entry_posture": posture,
            "scale_up_blocked": (
                _safe_bool(raw.get("scale_up_blocked"))
                or priority == "p0"
                or status in {"fail", "failed", "missing"}
            ),
            "live_shadow_required": (
                _safe_bool(raw.get("live_shadow_required"))
                or mode == "backtest_wfa_oos_rebuild"
            ),
            "runner_hint": _compact_text(raw.get("runner_hint"), limit=220),
            "verification_artifact": _compact_text(
                raw.get("verification_artifact"),
                limit=260,
            ),
            "exit_criteria": _compact_text(raw.get("exit_criteria"), limit=220),
            "evidence_targets": _compact_repair_evidence_targets(
                raw.get("evidence_targets")
            ),
        }
        key = f"{venue}:{discipline_id}"
        previous = deduped.get(key)
        item_rank = (
            priority_rank.get(priority, 9),
            status_rank.get(status, 9),
            _compact_text(item.get("owner"), limit=80),
        )
        previous_rank = (
            priority_rank.get(str(previous.get("priority") or ""), 9),
            status_rank.get(str(previous.get("status") or ""), 9),
            _compact_text(previous.get("owner"), limit=80),
        ) if previous else None
        if previous and previous_rank <= item_rank:
            continue
        deduped[key] = {
            key: value
            for key, value in item.items()
            if value not in (None, "", [], {})
        }

    items = sorted(
        deduped.values(),
        key=lambda row: (
            priority_rank.get(str(row.get("priority") or ""), 9),
            status_rank.get(str(row.get("status") or ""), 9),
            str(row.get("venue") or ""),
            str(row.get("discipline_id") or ""),
        ),
    )[: max(int(limit), 1)]
    queues: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        cadence = _compact_text(item.get("cadence"), limit=80) or "next_validation_run"
        queues.setdefault(cadence, []).append(
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "venue",
                    "discipline_id",
                    "repair_action_id",
                    "priority",
                    "status",
                    "owner",
                    "automation_hook",
                    "execution_weight",
                    "validation_mode",
                    "allowed_entry_posture",
                    "scale_up_blocked",
                    "live_shadow_required",
                    "runner_hint",
                    "evidence_targets",
                }
                and value not in (None, "", [], {})
            }
        )
    scale_blocked_count = sum(1 for item in items if _safe_bool(item.get("scale_up_blocked")))
    shadow_required_count = sum(
        1 for item in items if _safe_bool(item.get("live_shadow_required"))
    )
    return {
        "version": "validation_repair_manifest_v1",
        "status": "needs_repair" if items else "clear",
        "item_count": len(items),
        "scale_up_blocked_count": scale_blocked_count,
        "live_shadow_required_count": shadow_required_count,
        "m1_execution_posture": "sequential_priority_queue",
        "next_cadences": list(queues.keys())[:8],
        "queues": queues,
        "items": items,
    }


def _read_json_file(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    payload = _json_loads(raw)
    return payload if isinstance(payload, dict) else {}


def _repair_target_int(
    targets: dict[str, Any],
    key: str,
    default: int,
) -> int:
    value = targets.get(key) if isinstance(targets, dict) else None
    parsed = _safe_int(value)
    return parsed if parsed > 0 else int(default)


LANE_TARGET_BROAD_TOKENS = {
    "all",
    "unknown",
    "futures",
    "spot",
    "upbit_spot",
    "volatile_attack",
    "short",
    "mid",
    "long",
    "core_etf",
}


def _lane_target_tokens(value: Any) -> list[str]:
    raw = _compact_text(value, limit=160).lower()
    if not raw:
        return []
    normalized = raw
    for char in ("/", "|", ",", " "):
        normalized = normalized.replace(char, ":")
    return [
        token.strip()
        for token in normalized.split(":")
        if token.strip() and token.strip() not in {"-", "none", "null"}
    ]


def _pattern_set_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in (
        "market",
        "lane",
        "horizon",
        "strategy_family",
        "family",
        "direction",
        "side",
        "pattern_key",
        "pattern_id",
        "symbol",
    ):
        tokens.update(_lane_target_tokens(row.get(key)))
    parameter_set = (
        row.get("parameter_set")
        if isinstance(row.get("parameter_set"), dict)
        else {}
    )
    tokens.update(_lane_target_tokens(parameter_set.get("family")))
    tokens.update(_lane_target_tokens(parameter_set.get("horizon")))
    tokens.update(_lane_target_tokens(parameter_set.get("lane")))
    return tokens


def _pattern_set_matches_target(row: dict[str, Any], target_lane: str) -> bool:
    target_tokens = _lane_target_tokens(target_lane)
    if not target_tokens:
        return False
    candidate_tokens = _pattern_set_tokens(row)
    if not candidate_tokens:
        return False

    evidence_tokens = [
        token for token in target_tokens if token not in LANE_TARGET_BROAD_TOKENS
    ]
    if evidence_tokens and not any(token in candidate_tokens for token in evidence_tokens):
        return False

    target_direction = next(
        (token for token in target_tokens if token in {"long", "short"}),
        "",
    )
    candidate_directions = candidate_tokens.intersection({"long", "short"})
    if target_direction and candidate_directions and target_direction not in candidate_tokens:
        return False

    target_market = next(
        (token for token in target_tokens if token in {"futures", "spot", "upbit_spot"}),
        "",
    )
    candidate_markets = candidate_tokens.intersection({"futures", "spot", "upbit_spot"})
    if target_market and candidate_markets and target_market not in candidate_tokens:
        return False

    if not evidence_tokens:
        return any(token in candidate_tokens for token in target_tokens)
    return True


def _pattern_lab_target_lane_evidence(
    payload: dict[str, Any],
    targets: dict[str, Any],
) -> dict[str, Any]:
    target_lanes = [
        _compact_text(row, limit=160)
        for row in list(targets.get("target_lanes") or [])
        if _compact_text(row, limit=160)
    ]
    if not target_lanes:
        return {"required": False, "matched_lanes": [], "missing_lanes": []}
    optimized_sets = [
        row
        for row in list(payload.get("optimized_strategy_sets") or [])
        if isinstance(row, dict)
    ]
    matched: list[str] = []
    missing: list[str] = []
    for target_lane in target_lanes:
        if any(_pattern_set_matches_target(row, target_lane) for row in optimized_sets):
            matched.append(target_lane)
        else:
            missing.append(target_lane)
    return {
        "required": True,
        "matched_lanes": matched,
        "missing_lanes": missing,
        "target_lane_count": len(target_lanes),
        "matched_target_lane_count": len(matched),
    }


def _pattern_lab_repair_evidence(
    payload: dict[str, Any],
    targets: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "passed": False,
            "status": "missing",
            "reasons": ["pattern_lab_payload_missing"],
        }
    min_active_sets = _repair_target_int(targets, "min_active_strategy_sets", 1)
    active_sets = _safe_int(
        payload.get("active_optimized_set_count")
        or payload.get("active_set_count")
        or payload.get("optimized_set_count")
    )
    total_sets = _safe_int(
        payload.get("total_optimized_set_count")
        or payload.get("optimized_set_count")
        or payload.get("active_set_count")
    )
    status = _compact_text(payload.get("status"), limit=60)
    validation_hint = (
        payload.get("validation_hint")
        if isinstance(payload.get("validation_hint"), dict)
        else {}
    )
    hint_reasons = [
        _compact_text(reason, limit=100)
        for reason in list(validation_hint.get("reasons") or [])[:8]
        if _compact_text(reason, limit=100)
    ]
    reasons: list[str] = []
    if status and status not in {"ok", "pass", "passed"}:
        reasons.append(f"pattern_lab_status:{status}")
    if active_sets < min_active_sets:
        reasons.append(
            f"active_strategy_sets:{active_sets}/{min_active_sets}"
        )
    lane_evidence = _pattern_lab_target_lane_evidence(payload, targets)
    missing_lanes = list(lane_evidence.get("missing_lanes") or [])
    if missing_lanes:
        reasons.append(
            "target_lane_evidence_missing:"
            + ",".join(_compact_text(row, limit=60) for row in missing_lanes[:3])
        )
    required_dimensions = {
        _compact_text(row, limit=80)
        for row in [
            *list(targets.get("missing_dimensions") or []),
            *list(targets.get("failed_dimensions") or []),
        ]
        if _compact_text(row, limit=80)
    }
    live_shadow_required = "live_shadow" in required_dimensions
    live_shadow_passed = _first_optional_bool(
        payload.get("live_shadow_passed"),
        payload.get("live_shadow_status"),
        validation_hint.get("live_shadow_passed"),
        validation_hint.get("live_shadow"),
    )
    if live_shadow_required and live_shadow_passed is not True:
        reasons.append("live_shadow_evidence_missing")
    reasons.extend(hint_reasons)
    evidence = {
        "passed": not reasons,
        "status": "passed" if not reasons else "insufficient_evidence",
        "active_optimized_set_count": active_sets,
        "total_optimized_set_count": total_sets,
        "min_active_strategy_sets": min_active_sets,
        "target_lane_count": lane_evidence.get("target_lane_count", 0),
        "matched_target_lane_count": lane_evidence.get("matched_target_lane_count", 0),
        "matched_target_lanes": list(lane_evidence.get("matched_lanes") or [])[:8],
        "missing_target_lanes": missing_lanes[:8],
        "live_shadow_required": live_shadow_required,
        "live_shadow_passed": live_shadow_passed,
        "validation_hint_status": _compact_text(
            validation_hint.get("status"),
            limit=80,
        ),
        "reasons": reasons[:8],
    }
    return {key: value for key, value in evidence.items() if value not in (None, "", [], {})}


def _validation_discipline(
    validation: dict[str, Any],
    venue: str,
    discipline_id: str,
) -> dict[str, Any]:
    venue_payload = (
        validation.get(venue)
        if isinstance(validation.get(venue), dict)
        else {}
    )
    for row in list(venue_payload.get("disciplines") or []):
        if (
            isinstance(row, dict)
            and _compact_text(row.get("id"), limit=80) == discipline_id
        ):
            return row
    return {}


def _discipline_metric(row: dict[str, Any]) -> dict[str, Any]:
    metric = row.get("metric") if isinstance(row.get("metric"), dict) else {}
    return metric


def _cost_repair_evidence(
    row: dict[str, Any],
    targets: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {
            "passed": False,
            "status": "missing",
            "reasons": ["cost_discipline_missing"],
        }
    metric = _discipline_metric(row)
    sample_count = _safe_float(metric.get("sample_count"))
    recorded_count = _safe_float(metric.get("recorded_cost_sample_count"))
    coverage_pct = (
        recorded_count / sample_count * 100.0
        if sample_count > 0
        else 0.0
    )
    min_coverage_pct = _safe_float(
        targets.get("min_recorded_cost_coverage_pct"),
        60.0,
    )
    cost_by_component = (
        metric.get("cost_by_component")
        if isinstance(metric.get("cost_by_component"), dict)
        else {}
    )
    present_cost_component_counts = (
        metric.get("present_cost_component_counts")
        if isinstance(metric.get("present_cost_component_counts"), dict)
        else {}
    )
    required_components = [
        _compact_text(value, limit=40)
        for value in list(targets.get("required_cost_components") or [])
        if _compact_text(value, limit=40)
    ]
    missing_components: list[str] = []
    for component in required_components:
        if component == "taxes_or_funding":
            present = any(
                _safe_float(cost_by_component.get(key)) > 0
                or _safe_float(present_cost_component_counts.get(key)) > 0
                for key in ("taxes", "funding")
            )
        else:
            present = (
                _safe_float(cost_by_component.get(component)) > 0
                or _safe_float(present_cost_component_counts.get(component)) > 0
            )
        if not present:
            missing_components.append(component)
    stressed = (
        metric.get("stressed_net_pnl_by_cost_multiplier")
        if isinstance(metric.get("stressed_net_pnl_by_cost_multiplier"), dict)
        else {}
    )
    stress_2x = _safe_float(stressed.get("2x"))
    status = _compact_text(row.get("status"), limit=40)
    reasons: list[str] = []
    if status not in {"pass", "warn"}:
        reasons.append(f"discipline_status:{status or 'missing'}")
    if coverage_pct < min_coverage_pct:
        reasons.append(
            f"recorded_cost_coverage:{coverage_pct:.2f}/{min_coverage_pct:.2f}"
        )
    if missing_components:
        reasons.append("missing_cost_components:" + ",".join(missing_components[:4]))
    if (
        targets.get("min_cost_stress_net_pnl_multiplier") == "2x_positive"
        and stress_2x <= 0
    ):
        reasons.append(f"cost_stress_2x_net_pnl:{stress_2x:.6f}")
    return {
        "passed": not reasons,
        "status": "passed" if not reasons else "insufficient_evidence",
        "discipline_status": status or "missing",
        "recorded_cost_coverage_pct": round(coverage_pct, 6),
        "cost_stress_2x_net_pnl": round(stress_2x, 6),
        "reasons": reasons[:8],
    }


def _risk_budget_repair_evidence(
    row: dict[str, Any],
    targets: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {
            "passed": False,
            "status": "missing",
            "reasons": ["risk_discipline_missing"],
        }
    discipline_id = _compact_text(row.get("id"), limit=80)
    metric = _discipline_metric(row)
    status = _compact_text(row.get("status"), limit=40)
    reasons: list[str] = []
    if status not in {"pass", "warn"}:
        reasons.append(f"discipline_status:{status or 'missing'}")

    has_risk_of_ruin = metric.get("risk_of_ruin_pct") not in (None, "")
    risk_of_ruin = (
        _safe_float(metric.get("risk_of_ruin_pct"))
        if has_risk_of_ruin
        else None
    )
    max_ruin = _safe_float(targets.get("max_risk_of_ruin_pct"), 5.0)
    if risk_of_ruin is None and discipline_id in {
        "kelly_sizing",
        "monte_carlo",
        "risk_of_ruin",
    }:
        reasons.append("risk_of_ruin:missing")
    elif risk_of_ruin is not None and risk_of_ruin > max_ruin:
        reasons.append(f"risk_of_ruin:{risk_of_ruin:.2f}/{max_ruin:.2f}")

    max_mdd = _safe_float(targets.get("max_mdd_pct"), 20.0)
    has_drawdown = metric.get("max_drawdown_pct") not in (None, "")
    drawdown = abs(_safe_float(metric.get("max_drawdown_pct"))) if has_drawdown else 0.0
    if drawdown and drawdown > max_mdd:
        reasons.append(f"max_drawdown:{drawdown:.2f}/{max_mdd:.2f}")

    min_profit_factor = _safe_float(targets.get("min_profit_factor"), 1.05)
    has_profit_factor = metric.get("profit_factor") not in (None, "")
    profit_factor = (
        _safe_float(metric.get("profit_factor"))
        if has_profit_factor
        else None
    )
    if profit_factor is None and discipline_id in {"kelly_sizing", "profit_factor"}:
        reasons.append("profit_factor:missing")
    elif profit_factor is not None and profit_factor < min_profit_factor:
        reasons.append(
            f"profit_factor:{profit_factor:.3f}/{min_profit_factor:.3f}"
        )

    min_recovery_factor = _safe_float(targets.get("min_recovery_factor"), 1.0)
    has_recovery_factor = metric.get("recovery_factor") not in (None, "")
    recovery_factor = (
        _safe_float(metric.get("recovery_factor"))
        if has_recovery_factor
        else None
    )
    if recovery_factor is None and discipline_id == "recovery_factor":
        reasons.append("recovery_factor:missing")
    elif recovery_factor is not None and recovery_factor < min_recovery_factor:
        reasons.append(
            f"recovery_factor:{recovery_factor:.3f}/{min_recovery_factor:.3f}"
        )

    has_recommended_risk = metric.get("recommended_risk_fraction") not in (None, "")
    recommended_risk = (
        _safe_float(metric.get("recommended_risk_fraction"))
        if has_recommended_risk
        else None
    )
    if discipline_id == "kelly_sizing" and (
        recommended_risk is None or recommended_risk <= 0
    ):
        reasons.append("recommended_risk_fraction:0")

    evidence = {
        "passed": not reasons,
        "status": "passed" if not reasons else "insufficient_evidence",
        "discipline_status": status or "missing",
        "reasons": reasons[:8],
    }
    if risk_of_ruin is not None:
        evidence["risk_of_ruin_pct"] = round(risk_of_ruin, 6)
    if profit_factor is not None:
        evidence["profit_factor"] = round(profit_factor, 6)
    if recovery_factor is not None:
        evidence["recovery_factor"] = round(recovery_factor, 6)
    if recommended_risk is not None:
        evidence["recommended_risk_fraction"] = round(recommended_risk, 6)
    return evidence


def _generic_discipline_repair_evidence(
    row: dict[str, Any],
    targets: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {
            "passed": False,
            "status": "missing",
            "discipline_status": "missing",
            "reasons": ["discipline_missing"],
        }
    discipline_id = _compact_text(row.get("id"), limit=80)
    metric = _discipline_metric(row)
    status = _compact_text(row.get("status"), limit=40)
    reasons: list[str] = []
    if status not in {"pass", "warn"}:
        reasons.append(f"discipline_status:{status or 'missing'}")

    max_stress_mdd = targets.get("max_stress_mdd_pct")
    if max_stress_mdd not in (None, "", [], {}):
        observed = None
        for key in (
            "stress_mdd_pct",
            "max_stress_mdd_pct",
            "stress_max_drawdown_pct",
            "max_drawdown_pct",
        ):
            if metric.get(key) not in (None, ""):
                observed = abs(_safe_float(metric.get(key)))
                break
        if observed is None:
            reasons.append("stress_mdd_pct:missing")
        elif observed > _safe_float(max_stress_mdd):
            reasons.append(
                f"stress_mdd_pct:{observed:.2f}/{_safe_float(max_stress_mdd):.2f}"
            )

    min_capacity_ratio = targets.get("min_capacity_ratio")
    if min_capacity_ratio not in (None, "", [], {}):
        observed = None
        for key in (
            "capacity_ratio",
            "min_capacity_ratio",
            "depth_capacity_ratio",
            "turnover_capacity_ratio",
        ):
            if metric.get(key) not in (None, ""):
                observed = _safe_float(metric.get(key))
                break
        if observed is None:
            reasons.append("capacity_ratio:missing")
        elif observed < _safe_float(min_capacity_ratio):
            reasons.append(
                f"capacity_ratio:{observed:.2f}/{_safe_float(min_capacity_ratio):.2f}"
            )

    max_top_cluster_share = targets.get("max_top_cluster_share_pct")
    if max_top_cluster_share not in (None, "", [], {}):
        observed = None
        for key in (
            "top_cluster_share_pct",
            "max_cluster_share_pct",
            "cluster_concentration_pct",
        ):
            if metric.get(key) not in (None, ""):
                observed = _safe_float(metric.get(key))
                break
        if observed is None and discipline_id == "correlation":
            reasons.append("top_cluster_share_pct:missing")
        elif observed is not None and observed > _safe_float(max_top_cluster_share):
            reasons.append(
                "top_cluster_share_pct:"
                f"{observed:.2f}/{_safe_float(max_top_cluster_share):.2f}"
            )

    max_top_factor_share = targets.get("max_top_factor_share_pct")
    if max_top_factor_share not in (None, "", [], {}):
        observed = None
        for key in (
            "top_factor_share_pct",
            "max_factor_share_pct",
            "factor_concentration_pct",
        ):
            if metric.get(key) not in (None, ""):
                observed = _safe_float(metric.get(key))
                break
        if observed is None and discipline_id == "factor_exposure":
            reasons.append("top_factor_share_pct:missing")
        elif observed is not None and observed > _safe_float(max_top_factor_share):
            reasons.append(
                "top_factor_share_pct:"
                f"{observed:.2f}/{_safe_float(max_top_factor_share):.2f}"
            )

    if _safe_bool(targets.get("requires_current_regime_coverage")):
        covered = _safe_bool(metric.get("current_regime_covered"))
        sample_count = _safe_int(
            metric.get("current_regime_sample_count")
            or metric.get("current_regime_covered_sample_count")
            or metric.get("covered_sample_count")
        )
        if covered is False or (covered is None and sample_count <= 0):
            reasons.append("current_regime_coverage:missing")

    return {
        "passed": not reasons,
        "status": "passed" if not reasons else "insufficient_evidence",
        "discipline_status": status or "missing",
        "reasons": reasons[:8],
    }


def _execute_validation_repair_manifest(
    settings: Any,
    manifest: dict[str, Any],
    *,
    sync: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    items = [
        row
        for row in list(manifest.get("items") or [])
        if isinstance(row, dict)
    ]
    max_items = max(
        min(
            int(getattr(settings, "live_evaluator_repair_execution_max_items", 8)),
            20,
        ),
        1,
    )
    actions: list[dict[str, Any]] = []
    for item in items[:max_items]:
        venue = _compact_text(item.get("venue"), limit=40) or "core"
        discipline_id = _compact_text(item.get("discipline_id"), limit=80)
        mode = _validation_repair_mode_for_item(item)
        action = {
            "venue": venue,
            "discipline_id": discipline_id,
            "repair_action_id": _compact_text(
                item.get("repair_action_id"),
                limit=140,
            ),
            "priority": _compact_text(item.get("priority"), limit=20),
            "automation_hook": _compact_text(
                item.get("automation_hook"),
                limit=120,
            ),
            "execution_weight": _compact_text(
                item.get("execution_weight"),
                limit=80,
            ),
            "validation_mode": mode,
            "scale_up_blocked": _safe_bool(item.get("scale_up_blocked")),
            "live_shadow_required": _safe_bool(item.get("live_shadow_required")),
            "status": "queued",
            "artifact": "",
            "reason": "",
        }
        if mode == "data_repair_before_trade":
            sync_status = _compact_text(sync.get("status"), limit=40)
            action.update(
                {
                    "status": "executed" if sync_status == "ok" else "error",
                    "artifact": "sync_live_performance_and_edges",
                    "reason": (
                        "live performance and edge scorecards refreshed before "
                        "validation"
                    )
                    if sync_status == "ok"
                    else _compact_text(sync.get("error_message"), limit=220)
                    or "sync_status_not_ok",
                    "sync_status": sync_status,
                }
            )
        elif mode == "cost_evidence_repair":
            sync_status = _compact_text(sync.get("status"), limit=40)
            discipline = _validation_discipline(validation, venue, discipline_id)
            evidence = _cost_repair_evidence(
                discipline,
                item.get("evidence_targets")
                if isinstance(item.get("evidence_targets"), dict)
                else {},
            )
            evidence_passed = bool(evidence.get("passed"))
            action.update(
                {
                    "status": (
                        "executed"
                        if sync_status == "ok" and evidence_passed
                        else "error"
                        if sync_status and sync_status != "ok"
                        else "queued_cost_repair"
                    ),
                    "artifact": "sync_live_performance_and_edges",
                    "reason": (
                        "cost evidence passes coverage/component/stress targets"
                        if sync_status == "ok" and evidence_passed
                        else _compact_text(sync.get("error_message"), limit=220)
                        if sync_status and sync_status != "ok"
                        else "cost evidence still fails coverage/component/stress targets"
                    ),
                    "sync_status": sync_status,
                    "discipline_status": _compact_text(
                        evidence.get("discipline_status"),
                        limit=40,
                    ),
                    "evidence_status": _compact_text(
                        evidence.get("status"),
                        limit=80,
                    ),
                    "evidence_reasons": list(evidence.get("reasons") or [])[:6],
                    "recorded_cost_coverage_pct": _safe_float(
                        evidence.get("recorded_cost_coverage_pct")
                    ),
                    "cost_stress_2x_net_pnl": _safe_float(
                        evidence.get("cost_stress_2x_net_pnl")
                    ),
                }
            )
        elif mode == "backtest_wfa_oos_rebuild" and venue == "kis":
            kr_lab = (
                validation.get("kr_equity_pattern_lab")
                if isinstance(validation.get("kr_equity_pattern_lab"), dict)
                else {}
            )
            kr_status = _compact_text(kr_lab.get("status"), limit=40)
            evidence = _pattern_lab_repair_evidence(
                kr_lab,
                item.get("evidence_targets")
                if isinstance(item.get("evidence_targets"), dict)
                else {},
            )
            evidence_passed = bool(evidence.get("passed"))
            action.update(
                {
                    "status": (
                        "executed"
                        if kr_status == "ok" and evidence_passed
                        else "queued"
                    ),
                    "artifact": "kr_equity_pattern_lab",
                    "reason": (
                        "kr_equity_pattern_lab has active WFA/OOS evidence"
                        if kr_status == "ok" and evidence_passed
                        else "kr_equity_pattern_lab must rebuild active WFA/OOS evidence"
                    ),
                    "lab_status": kr_status or "missing",
                    "pattern_count": _safe_int(
                        kr_lab.get("pattern_count") or kr_lab.get("group_count")
                    ),
                    "backtest_count": _safe_int(
                        kr_lab.get("backtest_count")
                        or kr_lab.get("eligible_sample_count")
                    ),
                    "active_optimized_set_count": _safe_int(
                        kr_lab.get("active_optimized_set_count")
                        or kr_lab.get("active_set_count")
                        or kr_lab.get("optimized_set_count")
                    ),
                    "evidence_status": _compact_text(
                        evidence.get("status"),
                        limit=80,
                    ),
                    "evidence_reasons": list(evidence.get("reasons") or [])[:6],
                    "matched_target_lane_count": _safe_int(
                        evidence.get("matched_target_lane_count")
                    ),
                    "missing_target_lanes": list(
                        evidence.get("missing_target_lanes") or []
                    )[:6],
                    "live_shadow_evidence_required": _safe_bool(
                        evidence.get("live_shadow_required")
                    ),
                    "live_shadow_evidence_passed": _safe_bool(
                        evidence.get("live_shadow_passed")
                    ),
                }
            )
        elif mode == "backtest_wfa_oos_rebuild" and venue == "binance":
            state_path = getattr(
                settings,
                "crypto_pattern_lab_state_path",
                ".runtime/crypto_pattern_lab.json",
            )
            state = _read_json_file(state_path)
            state_status = _compact_text(state.get("status"), limit=40)
            service_status = (
                state.get("service_status")
                if isinstance(state.get("service_status"), dict)
                else {}
            )
            evidence = _pattern_lab_repair_evidence(
                service_status,
                item.get("evidence_targets")
                if isinstance(item.get("evidence_targets"), dict)
                else {},
            )
            evidence_passed = bool(evidence.get("passed"))
            action.update(
                {
                    "status": (
                        "observed_external_runner"
                        if state_status == "ok" and evidence_passed
                        else "queued_external_runner"
                    ),
                    "artifact": "crypto_pattern_lab_runner",
                    "reason": (
                        "crypto_pattern_lab runner has active WFA/OOS evidence"
                        if state_status == "ok" and evidence_passed
                        else "crypto_pattern_lab runner must rebuild WFA/OOS evidence"
                    ),
                    "state_path": str(state_path),
                    "runner_status": state_status or "missing",
                    "updated_at": _compact_text(state.get("updated_at"), limit=80),
                    "active_optimized_set_count": _safe_int(
                        service_status.get("active_optimized_set_count")
                        or service_status.get("optimized_set_count")
                    ),
                    "evidence_status": _compact_text(
                        evidence.get("status"),
                        limit=80,
                    ),
                    "evidence_reasons": list(evidence.get("reasons") or [])[:6],
                    "matched_target_lane_count": _safe_int(
                        evidence.get("matched_target_lane_count")
                    ),
                    "missing_target_lanes": list(
                        evidence.get("missing_target_lanes") or []
                    )[:6],
                    "live_shadow_evidence_required": _safe_bool(
                        evidence.get("live_shadow_required")
                    ),
                    "live_shadow_evidence_passed": _safe_bool(
                        evidence.get("live_shadow_passed")
                    ),
                }
            )
        elif mode == "risk_budget_recalibration":
            venue_validation = (
                validation.get(venue)
                if isinstance(validation.get(venue), dict)
                else {}
            )
            summary = (
                venue_validation.get("summary")
                if isinstance(venue_validation.get("summary"), dict)
                else {}
            )
            discipline = _validation_discipline(validation, venue, discipline_id)
            evidence = _risk_budget_repair_evidence(
                discipline,
                item.get("evidence_targets")
                if isinstance(item.get("evidence_targets"), dict)
                else {},
            )
            evidence_passed = bool(evidence.get("passed"))
            risk_update: dict[str, Any] = {
                "status": (
                    "executed"
                    if evidence_passed
                    else "queued_risk_rebuild"
                ),
                "artifact": "trading_validation_refresh",
                "reason": (
                    "risk metrics pass repair evidence targets"
                    if evidence_passed
                    else "risk metrics still fail repair evidence targets"
                ),
                "readiness": _compact_text(summary.get("readiness"), limit=80),
                "fail_count": _safe_int(summary.get("fail_count")),
                "warn_count": _safe_int(summary.get("warn_count")),
                "discipline_status": _compact_text(
                    evidence.get("discipline_status"),
                    limit=40,
                ),
                "evidence_status": _compact_text(
                    evidence.get("status"),
                    limit=80,
                ),
                "evidence_reasons": list(evidence.get("reasons") or [])[:6],
            }
            for key in (
                "recommended_risk_fraction",
                "risk_of_ruin_pct",
                "profit_factor",
                "recovery_factor",
            ):
                if evidence.get(key) is not None:
                    risk_update[key] = _safe_float(evidence.get(key))
            action.update(risk_update)
        elif mode in {
            "scenario_regime_replay",
            "capacity_depth_check",
            "portfolio_exposure_check",
            "refresh_validation",
        }:
            discipline = _validation_discipline(validation, venue, discipline_id)
            evidence = _generic_discipline_repair_evidence(
                discipline,
                item.get("evidence_targets")
                if isinstance(item.get("evidence_targets"), dict)
                else {},
            )
            evidence_passed = bool(evidence.get("passed"))
            action.update(
                {
                    "status": (
                        "executed"
                        if evidence_passed
                        else f"queued_{mode}"
                    ),
                    "artifact": "trading_validation_refresh",
                    "reason": (
                        "validation discipline passes latest evidence targets"
                        if evidence_passed
                        else "validation discipline still fails latest evidence targets"
                    ),
                    "discipline_status": _compact_text(
                        evidence.get("discipline_status"),
                        limit=40,
                    ),
                    "evidence_status": _compact_text(
                        evidence.get("status"),
                        limit=80,
                    ),
                    "evidence_reasons": list(evidence.get("reasons") or [])[:6],
                }
            )
        else:
            action.update(
                {
                    "status": "queued",
                    "reason": "no_lightweight_executor_for_validation_mode",
                }
            )
        actions.append(
            {
                key: value
                for key, value in action.items()
                if value not in (None, "", [], {})
            }
        )

    status_counts: dict[str, int] = {}
    for action in actions:
        status = _compact_text(action.get("status"), limit=60) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    executed_count = sum(
        count
        for status, count in status_counts.items()
        if status in {"executed", "observed_external_runner"}
    )
    queued_count = sum(
        count
        for status, count in status_counts.items()
        if status.startswith("queued")
    )
    error_count = status_counts.get("error", 0)
    return {
        "version": "validation_repair_execution_v1",
        "status": (
            "clear"
            if not actions
            else "error"
            if error_count
            else "queued"
            if queued_count
            else "executed"
        ),
        "item_count": len(actions),
        "executed_count": executed_count,
        "queued_count": queued_count,
        "error_count": error_count,
        "status_counts": status_counts,
        "m1_execution_posture": "sequential_priority_queue",
        "actions": actions,
    }


def compact_repair_execution_for_venue(
    repair_execution: dict[str, Any],
    venue: str,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    if not isinstance(repair_execution, dict):
        return {}
    clean_venue = _compact_text(venue, limit=40).lower()
    if not clean_venue:
        return {}
    actions = [
        row
        for row in list(repair_execution.get("actions") or [])
        if isinstance(row, dict)
        and _compact_text(row.get("venue"), limit=40).lower() == clean_venue
    ][: max(int(limit), 1)]
    if not actions:
        return {}
    compact_actions: list[dict[str, Any]] = []
    for row in actions:
        compact_actions.append(
            {
                key: value
                for key, value in {
                    "discipline_id": _compact_text(
                        row.get("discipline_id"),
                        limit=80,
                    ),
                    "priority": _compact_text(row.get("priority"), limit=20),
                    "status": _compact_text(row.get("status"), limit=60),
                    "validation_mode": _compact_text(
                        row.get("validation_mode"),
                        limit=100,
                    ),
                    "scale_up_blocked": row.get("scale_up_blocked"),
                    "live_shadow_required": row.get("live_shadow_required"),
                    "artifact": _compact_text(row.get("artifact"), limit=120),
                    "reason": _compact_text(row.get("reason"), limit=180),
                    "runner_status": _compact_text(
                        row.get("runner_status"),
                        limit=60,
                    ),
                    "discipline_status": _compact_text(
                        row.get("discipline_status"),
                        limit=60,
                    ),
                    "active_optimized_set_count": _safe_int(
                        row.get("active_optimized_set_count")
                    ),
                    "evidence_status": _compact_text(
                        row.get("evidence_status"),
                        limit=80,
                    ),
                    "evidence_reasons": [
                        _compact_text(reason, limit=100)
                        for reason in list(row.get("evidence_reasons") or [])[:4]
                        if _compact_text(reason, limit=100)
                    ],
                    "recorded_cost_coverage_pct": _safe_float(
                        row.get("recorded_cost_coverage_pct")
                    ),
                    "cost_stress_2x_net_pnl": _safe_float(
                        row.get("cost_stress_2x_net_pnl")
                    ),
                    "recommended_risk_fraction": _safe_float(
                        row.get("recommended_risk_fraction")
                    ),
                    "risk_of_ruin_pct": _safe_float(row.get("risk_of_ruin_pct")),
                    "profit_factor": _safe_float(row.get("profit_factor")),
                    "recovery_factor": _safe_float(row.get("recovery_factor")),
                    "updated_at": _compact_text(row.get("updated_at"), limit=80),
                }.items()
                if value not in (None, "", [], {})
            }
        )
    status_counts: dict[str, int] = {}
    for row in compact_actions:
        status = _compact_text(row.get("status"), limit=60) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "version": "validation_repair_execution_compact_v1",
        "source_version": _compact_text(repair_execution.get("version"), limit=80),
        "venue": clean_venue,
        "status": _compact_text(repair_execution.get("status"), limit=60),
        "item_count": len(compact_actions),
        "executed_count": sum(
            count
            for status, count in status_counts.items()
            if status in {"executed", "observed_external_runner"}
        ),
        "queued_count": sum(
            count
            for status, count in status_counts.items()
            if status.startswith("queued")
        ),
        "status_counts": status_counts,
        "m1_execution_posture": _compact_text(
            repair_execution.get("m1_execution_posture"),
            limit=80,
        ),
        "actions": compact_actions,
    }


def _clean_lane_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _binance_live_edge_family(metadata: dict[str, Any]) -> str:
    market = _clean_lane_token(metadata.get("market")) or "binance"
    lane = _clean_lane_token(metadata.get("lane"))
    side = _clean_lane_token(metadata.get("side"))
    horizon = _clean_lane_token(metadata.get("horizon"))
    volatile_attack = metadata.get("volatile_attack")
    if isinstance(volatile_attack, str):
        volatile_attack_enabled = volatile_attack.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
    else:
        volatile_attack_enabled = bool(volatile_attack)
    if lane == "volatile_attack" or volatile_attack_enabled:
        return f"{market}:volatile_attack"
    if market == "futures":
        return f"futures:{side or lane or horizon or 'unknown'}"
    if market in {"spot", "upbit_spot"}:
        spot_side = side or "long"
        spot_lane = lane or horizon
        if spot_lane in {"short", "mid", "long"}:
            return f"{market}:{spot_side}:{spot_lane}"
        if spot_lane in {"", "spot", "upbit_spot", market}:
            return f"{market}:{spot_side}"
        if ":" in spot_lane:
            return spot_lane
        return f"{market}:{spot_side}:{spot_lane}"
    return f"{market}:{lane or side or horizon or 'unknown'}"


def _kis_live_edge_family(source: dict[str, Any], metadata: dict[str, Any]) -> str:
    block = source.get("block") if isinstance(source.get("block"), dict) else {}
    if _is_kis_etf_block(block=block, metadata=metadata):
        return "core_etf"
    horizon = _clean_lane_token(metadata.get("horizon"))
    horizon_aliases = {
        "short_term": "short",
        "short-term": "short",
        "intraday": "short",
        "day": "short",
        "mid_term": "mid",
        "mid-term": "mid",
        "medium": "mid",
        "swing": "mid",
        "long_term": "long",
        "long-term": "long",
        "position": "long",
    }
    return horizon_aliases.get(horizon, horizon or "unknown")


def _live_edge_evidence_key(metadata: dict[str, Any]) -> str:
    for key in (
        "strategy_family",
        "evidence_key",
        "entry_setup",
        "setup",
        "pattern",
        "lane",
    ):
        value = _clean_lane_token(metadata.get(key))
        if value and value not in {"unknown", "none", "null"}:
            return value
    return "all"


def _strategy_revision_id_from_metadata(metadata: dict[str, Any]) -> str:
    for key in (
        "strategy_revision_id",
        "jue_strategy_revision_id",
        "revision_id",
    ):
        value = _compact_text(metadata.get(key), limit=120)
        if value:
            return value
    return ""


def _nested_dict(source: dict[str, Any], *path: str) -> dict[str, Any]:
    value: Any = source
    for key in path:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _first_optional_bool(*values: Any) -> bool | None:
    for value in values:
        parsed = _optional_bool(value)
        if parsed is not None:
            return parsed
    return None


def _walk_forward_passed_from_quality(quality: dict[str, Any]) -> bool | None:
    if not quality:
        return None
    explicit = _first_optional_bool(
        quality.get("passed"),
        quality.get("status"),
        quality.get("walk_forward_passed"),
    )
    if explicit is not None:
        return explicit
    window_count = _safe_int(quality.get("window_count"))
    passed_count = _safe_int(quality.get("passed_window_count"))
    pass_rate = _safe_float(
        quality.get("pass_rate_pct")
        if quality.get("pass_rate_pct") is not None
        else quality.get("window_pass_rate")
    )
    if 0 < pass_rate <= 1:
        pass_rate *= 100.0
    if window_count > 0:
        return passed_count > 0 and pass_rate >= 70.0
    return None


def _out_of_sample_passed_from_metadata(metadata: dict[str, Any]) -> bool | None:
    explicit = _first_optional_bool(
        metadata.get("out_of_sample_passed"),
        metadata.get("oos_passed"),
        _nested_dict(metadata, "validation_evidence").get("out_of_sample_passed"),
        _nested_dict(metadata, "validation_evidence").get("out_of_sample"),
    )
    if explicit is not None:
        return explicit
    trade_count = _safe_int(metadata.get("out_of_sample_trade_count"))
    expectancy = _safe_float(metadata.get("out_of_sample_expectancy_r"))
    profit_factor = _safe_float(metadata.get("out_of_sample_profit_factor"))
    if trade_count > 0:
        return expectancy > 0 and profit_factor >= 1.05
    return None


def _scale_validation_evidence_from_metadata(
    metadata: dict[str, Any],
) -> dict[str, bool | None]:
    validation = _nested_dict(metadata, "validation_evidence")
    pattern_prior = _nested_dict(metadata, "pattern_prior")
    pattern_inputs_prior = _nested_dict(metadata, "pattern_inputs", "prior")
    prior = pattern_prior or pattern_inputs_prior
    walk_forward_quality = (
        _nested_dict(metadata, "walk_forward_quality")
        or _nested_dict(prior, "walk_forward_quality")
    )
    oos_source = dict(prior or {})
    for key in (
        "out_of_sample_trade_count",
        "out_of_sample_expectancy_r",
        "out_of_sample_profit_factor",
    ):
        if key not in oos_source and key in metadata:
            oos_source[key] = metadata.get(key)
    return {
        "backtest_passed": _first_optional_bool(
            metadata.get("backtest_passed"),
            metadata.get("backtest_status"),
            validation.get("backtest_passed"),
            validation.get("backtest"),
            (
                _safe_int(prior.get("trade_count")) > 0
                and _safe_float(prior.get("expectancy_r")) > 0
                and _safe_float(prior.get("profit_factor")) >= 1.05
            )
            if prior
            else None,
        ),
        "walk_forward_passed": _first_optional_bool(
            metadata.get("walk_forward_passed"),
            metadata.get("wfa_passed"),
            validation.get("walk_forward_passed"),
            validation.get("walk_forward"),
            _walk_forward_passed_from_quality(walk_forward_quality),
        ),
        "out_of_sample_passed": _out_of_sample_passed_from_metadata(
            {**oos_source, "validation_evidence": validation}
        ),
        "live_shadow_passed": _first_optional_bool(
            metadata.get("live_shadow_passed"),
            metadata.get("live_shadow_status"),
            validation.get("live_shadow_passed"),
            validation.get("live_shadow"),
        ),
    }


def _list_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _validation_repair_disciplines(metadata: dict[str, Any]) -> list[str]:
    repair = (
        metadata.get("validation_repair")
        if isinstance(metadata.get("validation_repair"), dict)
        else {}
    )
    if not repair:
        return []
    discipline_ids: list[str] = []

    def add(value: Any) -> None:
        token = _clean_lane_token(value)
        if token and token not in discipline_ids:
            discipline_ids.append(token)

    for value in _list_values(repair.get("discipline_ids")):
        add(value)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _list_values(repair.get(section)):
            if isinstance(row, dict):
                add(row.get("discipline_id"))
    return discipline_ids[:8]


BINANCE_MAJOR_BASE_ASSETS = {
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "ADA",
    "DOGE",
    "TRX",
    "AVAX",
    "LINK",
}


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _connect_existing(path: str | Path) -> sqlite3.Connection | None:
    if not str(path or "").strip():
        return None
    resolved = Path(path)
    if not resolved.exists() or resolved.is_dir():
        return None
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    return conn


def _repair_open_block_strategy_revision_metadata(
    db_path: str | Path,
    *,
    strategy_revision_id: str,
    venue: str,
) -> dict[str, Any]:
    revision_id = _compact_text(strategy_revision_id, limit=120)
    if not revision_id:
        return {
            "status": "skipped",
            "venue": venue,
            "reason": "strategy_revision_id_missing",
            "updated_count": 0,
        }
    conn = _connect_existing(db_path)
    if conn is None:
        return {
            "status": "skipped",
            "venue": venue,
            "reason": "block_db_missing",
            "db_path": str(db_path),
            "updated_count": 0,
        }
    updated = 0
    inspected = 0
    now = _now()
    with conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "blocks" not in tables:
            return {
                "status": "skipped",
                "venue": venue,
                "reason": "blocks_table_missing",
                "db_path": str(db_path),
                "updated_count": 0,
            }
        columns = _table_columns(conn, "blocks")
        if not {"block_id", "status", "metadata_json"}.issubset(columns):
            return {
                "status": "skipped",
                "venue": venue,
                "reason": "required_columns_missing",
                "db_path": str(db_path),
                "updated_count": 0,
            }
        created_by_filter = (
            "AND COALESCE(created_by, '') IN ('llm', 'existing_position')"
            if "created_by" in columns
            else ""
        )
        rows = conn.execute(
            f"""
            SELECT block_id, status, metadata_json
            FROM blocks
            WHERE COALESCE(status, '') NOT IN ('closed', 'error')
              {created_by_filter}
            ORDER BY COALESCE(updated_at, created_at, '') DESC
            LIMIT 1000
            """
        ).fetchall()
        for row in rows:
            inspected += 1
            metadata = _json_loads(row["metadata_json"])
            if not isinstance(metadata, dict):
                metadata = {}
            existing_revision = _compact_text(
                metadata.get("strategy_revision_id")
                or metadata.get("jue_strategy_revision_id")
                or metadata.get("revision_id"),
                limit=120,
            )
            if existing_revision:
                continue
            metadata["strategy_revision_id"] = revision_id
            metadata["strategy_revision_source"] = (
                "live_evaluator_open_block_metadata_repair"
            )
            metadata["strategy_revision_repaired_at"] = now
            conn.execute(
                """
                UPDATE blocks
                SET metadata_json = ?
                WHERE block_id = ?
                """,
                (_json_dumps(metadata), str(row["block_id"] or "")),
            )
            if "block_events" in tables:
                event_columns = _table_columns(conn, "block_events")
                if {
                    "block_id",
                    "event_type",
                    "message",
                    "payload_json",
                    "created_at",
                }.issubset(event_columns):
                    conn.execute(
                        """
                        INSERT INTO block_events (
                            block_id, event_type, message, payload_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(row["block_id"] or ""),
                            "strategy_revision_repaired",
                            "open block strategy revision metadata repaired",
                            _json_dumps(
                                {
                                    "strategy_revision_id": revision_id,
                                    "venue": venue,
                                }
                            ),
                            now,
                        ),
                    )
            updated += 1
    return {
        "status": "ok",
        "venue": venue,
        "db_path": str(db_path),
        "strategy_revision_id": revision_id,
        "inspected_count": inspected,
        "updated_count": updated,
    }


def _pending_revision_lane(
    *,
    venue: str,
    metadata: dict[str, Any],
) -> str:
    if venue == "kis":
        horizon = _compact_text(
            metadata.get("horizon")
            or metadata.get("block_horizon")
            or metadata.get("time_horizon"),
            limit=60,
        ).lower()
        lane = _compact_text(metadata.get("lane"), limit=80).lower()
        name = _compact_text(
            metadata.get("name") or metadata.get("symbol_name"),
            limit=120,
        ).upper()
        asset_type = _compact_text(
            metadata.get("asset_type")
            or metadata.get("asset_class")
            or metadata.get("instrument_type"),
            limit=80,
        ).lower()
        if (
            lane in {"core_etf", "etf"}
            or horizon in {"core", "core_etf", "etf"}
            or asset_type in {"etf", "etn"}
            or any(
                name.startswith(prefix)
                for prefix in (
                    "KODEX",
                    "TIGER",
                    "ACE",
                    "KBSTAR",
                    "SOL",
                    "RISE",
                    "HANARO",
                    "ARIRANG",
                    "KOSEF",
                    "TIMEFOLIO",
                    "PLUS",
                )
            )
        ):
            return "core_etf"
        aliases = {
            "short_term": "short",
            "intraday": "short",
            "day": "short",
            "mid_term": "mid",
            "medium": "mid",
            "swing": "mid",
            "long_term": "long",
            "position": "long",
        }
        return aliases.get(horizon, horizon or "unknown")
    lane = _compact_text(metadata.get("lane"), limit=80).lower()
    if lane == "volatile_attack":
        return lane
    market = _compact_text(metadata.get("market"), limit=40).lower()
    side = _compact_text(metadata.get("side"), limit=40).lower() or "long"
    if market:
        return f"{market}:{side}"
    if lane in {
        "spot:long",
        "futures:long",
        "futures:short",
        "upbit_spot:long",
    }:
        return lane
    if lane:
        return lane
    return f"spot:{side}"


def _pending_active_revision_block_evidence(
    db_path: str | Path,
    *,
    strategy_revision_id: str,
    venue: str,
) -> dict[str, Any]:
    revision_id = _compact_text(strategy_revision_id, limit=120)
    if not revision_id:
        return {
            "status": "skipped",
            "venue": venue,
            "reason": "strategy_revision_id_missing",
            "pending_block_count": 0,
        }
    conn = _connect_existing(db_path)
    if conn is None:
        return {
            "status": "missing",
            "venue": venue,
            "reason": "block_db_missing",
            "db_path": str(db_path),
            "pending_block_count": 0,
        }
    status_counts: dict[str, int] = {}
    lane_counts: dict[str, int] = {}
    created_by_counts: dict[str, int] = {}
    pending_blocks: list[dict[str, Any]] = []
    missing_revision_count = 0
    inspected = 0
    with conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "blocks" not in tables:
            return {
                "status": "missing",
                "venue": venue,
                "reason": "blocks_table_missing",
                "db_path": str(db_path),
                "pending_block_count": 0,
            }
        columns = _table_columns(conn, "blocks")
        if not {"block_id", "status", "metadata_json"}.issubset(columns):
            return {
                "status": "missing",
                "venue": venue,
                "reason": "required_columns_missing",
                "db_path": str(db_path),
                "pending_block_count": 0,
            }
        select_columns = [
            column
            for column in (
                "block_id",
                "symbol",
                "status",
                "created_by",
                "market",
                "side",
                "metadata_json",
                "created_at",
                "updated_at",
            )
            if column in columns
        ]
        rows = conn.execute(
            f"""
            SELECT {', '.join(select_columns)}
            FROM blocks
            WHERE COALESCE(status, '') NOT IN ('closed', 'error')
            ORDER BY COALESCE(updated_at, created_at, '') DESC
            LIMIT 1000
            """
        ).fetchall()
    for raw_row in rows:
        inspected += 1
        row = dict(raw_row)
        created_by = _compact_text(row.get("created_by"), limit=80)
        if created_by not in {"llm", "existing_position"}:
            continue
        metadata = _json_loads(row.get("metadata_json"))
        if not isinstance(metadata, dict):
            metadata = {}
        if venue == "binance":
            if row.get("market"):
                metadata["market"] = _compact_text(row.get("market"), limit=40)
            if row.get("side"):
                metadata["side"] = _compact_text(row.get("side"), limit=40)
        row_revision = _compact_text(
            metadata.get("strategy_revision_id")
            or metadata.get("jue_strategy_revision_id")
            or metadata.get("revision_id"),
            limit=120,
        )
        if not row_revision:
            missing_revision_count += 1
            continue
        if row_revision != revision_id:
            continue
        status = _compact_text(row.get("status"), limit=60) or "unknown"
        lane = _pending_revision_lane(venue=venue, metadata=metadata)
        status_counts[status] = status_counts.get(status, 0) + 1
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        created_by_counts[created_by] = created_by_counts.get(created_by, 0) + 1
        if len(pending_blocks) < 12:
            pending_blocks.append(
                {
                    "block_id": _compact_text(row.get("block_id"), limit=120),
                    "symbol": _compact_text(row.get("symbol"), limit=40),
                    "status": status,
                    "lane": lane,
                    "created_by": created_by,
                    "created_at": _compact_text(row.get("created_at"), limit=80),
                    "updated_at": _compact_text(row.get("updated_at"), limit=80),
                }
            )
    pending_count = sum(status_counts.values())
    return {
        "status": "ok" if pending_count else "empty",
        "venue": venue,
        "db_path": str(db_path),
        "strategy_revision_id": revision_id,
        "inspected_count": inspected,
        "pending_block_count": pending_count,
        "pending_block_status_counts": dict(sorted(status_counts.items())),
        "pending_block_lane_counts": dict(sorted(lane_counts.items())),
        "pending_block_created_by_counts": dict(sorted(created_by_counts.items())),
        "missing_revision_nonterminal_count": missing_revision_count,
        "sample_blocks": pending_blocks,
        "evidence_role": (
            "future_active_revision_samples_after_close"
            if pending_count
            else "no_pending_active_revision_blocks"
        ),
    }


def _order_row_price(row_map: dict[str, Any]) -> float:
    price = _safe_float(row_map.get("avg_fill_price"))
    payload = _json_loads(row_map.get("response_json"))
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    if price <= 0:
        price = (
            _safe_float(payload.get("avgPrice"))
            or _safe_float(payload.get("avg_price"))
            or _safe_float(raw.get("avgPrice"))
            or _safe_float(raw.get("avg_price"))
        )
    if price <= 0:
        cum_quote = (
            _safe_float(payload.get("cum_quote"))
            or _safe_float(payload.get("cumQuote"))
            or _safe_float(payload.get("cummulativeQuoteQty"))
            or _safe_float(raw.get("cum_quote"))
            or _safe_float(raw.get("cumQuote"))
            or _safe_float(raw.get("cummulativeQuoteQty"))
        )
        executed_qty = (
            _safe_float(payload.get("executed_qty"))
            or _safe_float(payload.get("executedQty"))
            or _safe_float(payload.get("filled_qty"))
            or _safe_float(payload.get("filledQty"))
            or _safe_float(raw.get("executed_qty"))
            or _safe_float(raw.get("executedQty"))
            or _safe_float(raw.get("filled_qty"))
            or _safe_float(raw.get("filledQty"))
        )
        if cum_quote > 0 and executed_qty > 0:
            price = cum_quote / executed_qty
    if price <= 0:
        fills = payload.get("fills")
        if not isinstance(fills, list):
            fills = raw.get("fills") if isinstance(raw.get("fills"), list) else []
        notional = 0.0
        qty = 0.0
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            fill_price = _safe_float(fill.get("price"))
            fill_qty = _safe_float(fill.get("qty") or fill.get("quantity"))
            if fill_price > 0 and fill_qty > 0:
                notional += fill_price * fill_qty
                qty += fill_qty
        if notional > 0 and qty > 0:
            price = notional / qty
    if price <= 0:
        price = (
            _safe_float(payload.get("price"))
            or _safe_float(raw.get("price"))
            or _safe_float(row_map.get("limit_price"))
        )
    return price


def _latest_order_price_evidence(
    conn: sqlite3.Connection,
    *,
    block_id: str,
    side: str,
    default: float = 0.0,
) -> dict[str, Any]:
    columns = _table_columns(conn, "block_orders")
    select_columns = [
        column
        for column in (
            "id",
            "side",
            "status",
            "filled_qty",
            "avg_fill_price",
            "limit_price",
            "response_json",
        )
        if column in columns
    ]
    if not select_columns:
        return {
            "price": default,
            "filled": False,
            "has_order": False,
            "status": "order_price_columns_missing",
            "price_source": "default" if default > 0 else "",
        }
    row = conn.execute(
        f"""
        SELECT {', '.join(select_columns)}
        FROM block_orders
        WHERE block_id = ? AND side = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (block_id, side),
    ).fetchone()
    if not row:
        return {
            "price": default,
            "filled": False,
            "has_order": False,
            "status": "order_missing",
            "price_source": "default" if default > 0 else "",
        }
    row_map = dict(row)
    price = _order_row_price(row_map)
    price_source = "order_response_or_fill"
    if price <= 0 and default > 0:
        price = default
        price_source = "default"
    payload = _json_loads(row_map.get("response_json"))
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    response_status = str(
        payload.get("status")
        or payload.get("order_status")
        or raw.get("status")
        or raw.get("ord_status")
        or ""
    ).strip()
    return {
        "price": price,
        "filled": _order_row_has_fill(row_map),
        "has_order": True,
        "status": str(row_map.get("status") or response_status or ""),
        "response_status": response_status,
        "price_source": price_source,
    }


def _latest_order_price(
    conn: sqlite3.Connection,
    *,
    block_id: str,
    side: str,
    default: float,
) -> float:
    evidence = _latest_order_price_evidence(
        conn,
        block_id=block_id,
        side=side,
        default=default,
    )
    return _safe_float(evidence.get("price")) or default


def _order_row_has_fill(row_map: dict[str, Any]) -> bool:
    status = str(row_map.get("status") or "").strip().lower()
    filled_qty = _safe_float(row_map.get("filled_qty"))
    avg_fill_price = _safe_float(row_map.get("avg_fill_price"))
    payload = _json_loads(row_map.get("response_json"))
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    payload_status = str(
        payload.get("status")
        or payload.get("order_status")
        or raw.get("status")
        or raw.get("ord_status")
        or ""
    ).strip().lower()
    payload_filled_qty = (
        _safe_float(payload.get("filled_qty"))
        or _safe_float(payload.get("filledQty"))
        or _safe_float(payload.get("executedQty"))
        or _safe_float(raw.get("filled_qty"))
        or _safe_float(raw.get("filledQty"))
        or _safe_float(raw.get("executedQty"))
        or _safe_float(raw.get("tot_ccld_qty"))
    )
    payload_avg_price = (
        _safe_float(payload.get("avg_fill_price"))
        or _safe_float(payload.get("avgPrice"))
        or _safe_float(raw.get("avg_fill_price"))
        or _safe_float(raw.get("avgPrice"))
        or _safe_float(raw.get("avg_prvs"))
    )
    filled_statuses = {"filled", "partially_filled", "partial", "executed", "done"}
    return (
        filled_qty > 0
        or avg_fill_price > 0 and status in filled_statuses
        or payload_filled_qty > 0
        or payload_avg_price > 0 and payload_status in filled_statuses
    )


def _order_row_filled_qty(row_map: dict[str, Any]) -> float:
    qty = _safe_float(row_map.get("filled_qty"))
    if qty > 0:
        return qty
    payload = _json_loads(row_map.get("response_json"))
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    qty = (
        _safe_float(payload.get("filled_qty"))
        or _safe_float(payload.get("filledQty"))
        or _safe_float(payload.get("executed_qty"))
        or _safe_float(payload.get("executedQty"))
        or _safe_float(raw.get("filled_qty"))
        or _safe_float(raw.get("filledQty"))
        or _safe_float(raw.get("executed_qty"))
        or _safe_float(raw.get("executedQty"))
        or _safe_float(raw.get("tot_ccld_qty"))
        or _safe_float(raw.get("ccld_qty"))
    )
    if qty > 0:
        return qty
    fills = payload.get("fills")
    if not isinstance(fills, list):
        fills = raw.get("fills") if isinstance(raw.get("fills"), list) else []
    qty = sum(
        _safe_float(fill.get("qty") or fill.get("quantity"))
        for fill in fills
        if isinstance(fill, dict)
    )
    if qty > 0:
        return qty
    if _order_row_has_fill(row_map):
        return _safe_float(row_map.get("qty"))
    return 0.0


def _order_fill_summary(rows: list[dict[str, Any]], *, side: str) -> dict[str, Any]:
    clean_side = str(side or "").strip().lower()
    fill_count = 0
    filled_qty = 0.0
    notional = 0.0
    for row in rows:
        if str(row.get("side") or "").strip().lower() != clean_side:
            continue
        if not _order_row_has_fill(row):
            continue
        qty = _order_row_filled_qty(row)
        price = _order_row_price(row)
        fill_count += 1
        if qty > 0:
            filled_qty += qty
            if price > 0:
                notional += price * qty
    avg_fill_price = notional / filled_qty if filled_qty > 0 and notional > 0 else 0.0
    return {
        "fill_count": fill_count,
        "filled_qty": filled_qty,
        "avg_fill_price": avg_fill_price,
    }


def _kis_block_fill_evidence(
    conn: sqlite3.Connection,
    *,
    block: dict[str, Any],
    metadata: dict[str, Any],
    entry_price: float,
    exit_price: float,
    qty: float,
) -> dict[str, Any]:
    block_id = str(block.get("block_id") or "")
    status = str(block.get("status") or "").strip().lower()
    trigger_status = str(metadata.get("entry_trigger_status") or "").strip().lower()
    cancelled_statuses = {
        "cancelled",
        "canceled",
        "expired",
        "skipped",
        "invalidated",
    }
    if trigger_status in cancelled_statuses or metadata.get("entry_cancelled_at"):
        return {
            "filled": False,
            "status": "cancelled_before_fill",
            "reason": trigger_status or "entry_cancelled_at",
            "buy_fill_count": 0,
            "sell_fill_count": 0,
        }
    if status != "closed" or entry_price <= 0 or exit_price <= 0 or qty <= 0:
        return {
            "filled": False,
            "status": "not_closed_round_trip",
            "reason": f"status={status},entry={entry_price},exit={exit_price},qty={qty}",
            "buy_fill_count": 0,
            "sell_fill_count": 0,
        }

    columns = _table_columns(conn, "block_orders")
    required = {"block_id", "side"}
    if not required.issubset(columns):
        return {
            "filled": False,
            "status": "order_evidence_missing",
            "reason": "block_orders_missing_required_columns",
            "buy_fill_count": 0,
            "sell_fill_count": 0,
        }
    select_columns = [
        column
        for column in (
            "id",
            "side",
            "status",
            "qty",
            "limit_price",
            "filled_qty",
            "avg_fill_price",
            "response_json",
        )
        if column in columns
    ]
    order_rows = conn.execute(
        f"""
        SELECT {', '.join(select_columns)}
        FROM block_orders
        WHERE block_id = ?
        ORDER BY id ASC
        """,
        (block_id,),
    ).fetchall()
    order_maps = [dict(row) for row in order_rows]
    buy_summary = _order_fill_summary(order_maps, side="buy")
    sell_summary = _order_fill_summary(order_maps, side="sell")
    buy_fill_count = int(buy_summary["fill_count"])
    sell_fill_count = int(sell_summary["fill_count"])

    if buy_fill_count <= 0:
        return {
            "filled": False,
            "status": "missing_buy_fill",
            "reason": "no_filled_buy_order",
            "buy_fill_count": buy_fill_count,
            "sell_fill_count": sell_fill_count,
        }
    if sell_fill_count <= 0:
        return {
            "filled": False,
            "status": "missing_sell_fill",
            "reason": "no_filled_sell_order",
            "buy_fill_count": buy_fill_count,
            "sell_fill_count": sell_fill_count,
        }
    return {
        "filled": True,
        "status": "round_trip_filled",
        "reason": "",
        "buy_fill_count": buy_fill_count,
        "sell_fill_count": sell_fill_count,
        "buy_filled_qty": round(_safe_float(buy_summary.get("filled_qty")), 8),
        "sell_filled_qty": round(_safe_float(sell_summary.get("filled_qty")), 8),
        "filled_qty": round(
            min(
                _safe_float(buy_summary.get("filled_qty")),
                _safe_float(sell_summary.get("filled_qty")),
            ),
            8,
        ),
        "buy_avg_fill_price": round(
            _safe_float(buy_summary.get("avg_fill_price")),
            8,
        ),
        "sell_avg_fill_price": round(
            _safe_float(sell_summary.get("avg_fill_price")),
            8,
        ),
    }


def _binance_order_sides(block: dict[str, Any]) -> tuple[str, str]:
    side = str(block.get("side") or "long").strip().lower()
    market = str(block.get("market") or "spot").strip().lower()
    if market in {"spot", "upbit_spot"}:
        return "buy", "sell"
    if side == "short":
        return "sell", "buy"
    return "buy", "sell"


def _binance_block_fill_evidence(
    conn: sqlite3.Connection,
    *,
    block: dict[str, Any],
    metadata: dict[str, Any],
    entry_price: float,
    exit_price: float,
    qty: float,
    reflection_exit_price: float = 0.0,
) -> dict[str, Any]:
    status = str(block.get("status") or "").strip().lower()
    if status != "closed" or entry_price <= 0 or qty <= 0:
        return {
            "filled": False,
            "status": "not_closed_round_trip",
            "reason": f"status={status},entry={entry_price},qty={qty}",
            "entry_order_filled": False,
            "exit_order_filled": False,
        }
    block_id = str(block.get("block_id") or "")
    entry_side, exit_side = _binance_order_sides(block)
    entry_evidence = _latest_order_price_evidence(
        conn,
        block_id=block_id,
        side=entry_side,
    )
    entry_filled = bool(entry_evidence.get("filled")) or bool(
        str(block.get("opened_at") or "").strip()
    )
    if reflection_exit_price > 0:
        if not entry_filled:
            return {
                "filled": False,
                "status": "missing_entry_evidence",
                "reason": "no_opened_at_or_entry_fill",
                "entry_order_filled": False,
                "exit_order_filled": True,
                "entry_order_status": entry_evidence.get("status"),
                "exit_order_status": "performance_reflection",
                "entry_price_source": entry_evidence.get("price_source"),
                "exit_price_source": "reflection",
            }
        return {
            "filled": True,
            "status": "reflection_round_trip_recorded",
            "reason": "",
            "entry_order_filled": bool(entry_evidence.get("filled")),
            "exit_order_filled": True,
            "entry_order_status": entry_evidence.get("status"),
            "exit_order_status": "performance_reflection",
            "entry_price_source": (
                entry_evidence.get("price_source") or "block_opened_at"
            ),
            "exit_price_source": "reflection",
        }
    exit_evidence = _latest_order_price_evidence(
        conn,
        block_id=block_id,
        side=exit_side,
    )
    exit_filled = bool(exit_evidence.get("filled"))
    if entry_filled and exit_filled:
        if exit_price <= 0:
            return {
                "filled": False,
                "status": "missing_exit_price",
                "reason": "exit_order_filled_without_price",
                "entry_order_filled": True,
                "exit_order_filled": True,
                "entry_order_status": entry_evidence.get("status"),
                "exit_order_status": exit_evidence.get("status"),
                "entry_price_source": entry_evidence.get("price_source"),
                "exit_price_source": exit_evidence.get("price_source"),
            }
        return {
            "filled": True,
            "status": "order_round_trip_filled",
            "reason": "",
            "entry_order_filled": True,
            "exit_order_filled": True,
            "entry_order_status": entry_evidence.get("status"),
            "exit_order_status": exit_evidence.get("status"),
            "entry_price_source": entry_evidence.get("price_source"),
            "exit_price_source": exit_evidence.get("price_source"),
        }
    return {
        "filled": False,
        "status": "missing_exit_fill" if entry_filled else "missing_entry_fill",
        "reason": (
            f"entry_side={entry_side},entry_filled={entry_filled},"
            f"exit_side={exit_side},exit_filled={exit_filled}"
        ),
        "entry_order_filled": entry_filled,
        "exit_order_filled": exit_filled,
        "entry_order_status": entry_evidence.get("status"),
        "exit_order_status": exit_evidence.get("status"),
        "entry_price_source": entry_evidence.get("price_source"),
        "exit_price_source": exit_evidence.get("price_source"),
    }


def _binance_base_asset(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    for suffix in ("USDT", "BUSD", "USDC", "BTC", "ETH", "KRW"):
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _clean_factor_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")


def _first_positive_float(*values: Any) -> float:
    for value in values:
        parsed = _safe_float(value)
        if parsed > 0:
            return parsed
    return 0.0


def _latest_crypto_research_context(settings: Any, *, symbol: str) -> dict[str, Any]:
    db_path = str(
        getattr(settings, "crypto_market_research_db_path", ".runtime/crypto_market_research.db")
        or ""
    )
    conn = _connect_existing(db_path) if db_path else None
    if conn is None:
        return {}
    context: dict[str, Any] = {"db_path": db_path}
    clean_symbol = str(symbol or "").upper().strip()
    try:
        with conn:
            if "crypto_features" in {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }:
                row = conn.execute(
                    """
                    SELECT symbol, feature_json, score, regime, updated_at
                    FROM crypto_features
                    WHERE symbol = ?
                    """,
                    (clean_symbol,),
                ).fetchone()
                if row:
                    feature = _json_loads(row["feature_json"])
                    if not isinstance(feature, dict):
                        feature = {}
                    context["feature"] = feature
                    context["feature_score"] = _safe_float(row["score"])
                    context["feature_regime"] = str(row["regime"] or "").strip()
                    context["feature_updated_at"] = str(row["updated_at"] or "")
            if "crypto_regime_snapshots" in {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }:
                row = conn.execute(
                    """
                    SELECT regime, payload_json, captured_at
                    FROM crypto_regime_snapshots
                    ORDER BY captured_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    payload = _json_loads(row["payload_json"])
                    context["global_regime"] = str(row["regime"] or "").strip()
                    context["global_regime_payload"] = payload if isinstance(payload, dict) else {}
                    context["global_regime_captured_at"] = str(row["captured_at"] or "")
    except sqlite3.Error as exc:
        return {"db_path": db_path, "error_message": f"crypto_research_context_error:{exc}"}
    return context


def _latest_kis_market_pulse_context(settings: Any) -> dict[str, Any]:
    db_path = str(getattr(settings, "market_pulse_db_path", ".runtime/market_pulse.db") or "")
    conn = _connect_existing(db_path) if db_path else None
    if conn is None:
        return {}
    try:
        with conn:
            if "market_pulse_snapshots" not in {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }:
                return {}
            row = conn.execute(
                """
                SELECT captured_at, trading_day, status, regime, score,
                       indices_json, sector_json, block_alignment_json,
                       risk_flags_json, data_gaps_json
                FROM market_pulse_snapshots
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error as exc:
        return {"db_path": db_path, "error_message": f"market_pulse_context_error:{exc}"}
    if not row:
        return {}
    return {
        "db_path": db_path,
        "captured_at": str(row["captured_at"] or ""),
        "trading_day": str(row["trading_day"] or ""),
        "status": str(row["status"] or ""),
        "regime": str(row["regime"] or ""),
        "score": _safe_float(row["score"]),
        "indices": _json_loads(row["indices_json"]),
        "sectors": _json_loads(row["sector_json"]),
        "block_alignment": _json_loads(row["block_alignment_json"]),
        "risk_flags": _json_loads(row["risk_flags_json"]),
        "data_gaps": _json_loads(row["data_gaps_json"]),
    }


def _sector_for_symbol(market_pulse: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    sector_payload = market_pulse.get("sectors")
    if not isinstance(sector_payload, dict):
        return {}
    for item in list(sector_payload.get("items") or []):
        if not isinstance(item, dict):
            continue
        symbols = {str(value).strip() for value in list(item.get("symbols") or [])}
        if symbol in symbols:
            return item
    return {}


def _latest_kis_valuation_context(settings: Any, *, symbol: str) -> dict[str, Any]:
    db_path = str(getattr(settings, "valuation_db_path", ".runtime/symbol_fundamentals.db") or "")
    conn = _connect_existing(db_path) if db_path else None
    if conn is None:
        return {}
    context: dict[str, Any] = {"db_path": db_path}
    try:
        with conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "valuation_snapshots" in tables:
                row = conn.execute(
                    """
                    SELECT *
                    FROM valuation_snapshots
                    WHERE symbol = ? AND status = 'ok'
                    ORDER BY crawled_at DESC, snapshot_id DESC
                    LIMIT 1
                    """,
                    (symbol,),
                ).fetchone()
                if row:
                    context["snapshot"] = dict(row)
            if "valuation_scores" in tables:
                row = conn.execute(
                    """
                    SELECT *
                    FROM valuation_scores
                    WHERE symbol = ?
                    """,
                    (symbol,),
                ).fetchone()
                if row:
                    context["score"] = dict(row)
    except sqlite3.Error as exc:
        return {"db_path": db_path, "error_message": f"valuation_context_error:{exc}"}
    return context


def _latest_kis_quote_context(settings: Any, *, symbol: str) -> dict[str, Any]:
    clean_symbol = str(symbol or "").strip()
    if not clean_symbol:
        return {}
    candidates = [
        (
            str(getattr(settings, "market_judge_db_path", ".runtime/market_judgment.db") or ""),
            "market_judgment_quote_turnover",
        ),
        (
            str(getattr(settings, "kis_block_trader_db_path", ".runtime/kis_blocks.db") or ""),
            "kis_block_quote_turnover",
        ),
    ]
    for db_path, source in candidates:
        conn = _connect_existing(db_path) if db_path else None
        if conn is None:
            continue
        try:
            with conn:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "quote_snapshots" not in tables:
                    continue
                columns = _table_columns(conn, "quote_snapshots")
                select_columns = [
                    column
                    for column in (
                        "symbol",
                        "name",
                        "price",
                        "volume",
                        "trading_value",
                        "source",
                        "fetched_at",
                        "status",
                        "error_message",
                        "raw_json",
                    )
                    if column in columns
                ]
                if not select_columns:
                    continue
                status_filter = "AND status = 'ok'" if "status" in columns else ""
                row = conn.execute(
                    f"""
                    SELECT {', '.join(select_columns)}
                    FROM quote_snapshots
                    WHERE symbol = ? {status_filter}
                    ORDER BY fetched_at DESC
                    LIMIT 1
                    """,
                    (clean_symbol,),
                ).fetchone()
                if not row:
                    continue
        except sqlite3.Error as exc:
            return {"db_path": db_path, "source": source, "error_message": f"quote_context_error:{exc}"}
        row_map = dict(row)
        raw = _json_loads(row_map.get("raw_json"))
        if not isinstance(raw, dict):
            raw = {}
        trading_value = _first_positive_float(
            row_map.get("trading_value"),
            raw.get("acml_tr_pbmn"),
            raw.get("accumulated_trading_value"),
        )
        volume = _first_positive_float(
            row_map.get("volume"),
            raw.get("acml_vol"),
            raw.get("accumulated_volume"),
        )
        price = _first_positive_float(
            row_map.get("price"),
            raw.get("stck_prpr"),
            raw.get("askp1"),
        )
        return_window_pct: list[float] = []
        history_select_columns = [
            column
            for column in ("price", "raw_json", "fetched_at")
            if column in columns
        ]
        if history_select_columns:
            history_rows = conn.execute(
                f"""
                SELECT {', '.join(history_select_columns)}
                FROM quote_snapshots
                WHERE symbol = ? {status_filter}
                ORDER BY fetched_at DESC
                LIMIT 80
                """,
                (clean_symbol,),
            ).fetchall()
            prices: list[float] = []
            for history_row in reversed(history_rows):
                history_map = dict(history_row)
                history_raw = _json_loads(history_map.get("raw_json"))
                if not isinstance(history_raw, dict):
                    history_raw = {}
                history_price = _first_positive_float(
                    history_map.get("price"),
                    history_raw.get("stck_prpr"),
                    history_raw.get("askp1"),
                )
                if history_price > 0:
                    prices.append(history_price)
            previous = 0.0
            for current in prices:
                if previous > 0 and current > 0:
                    return_window_pct.append(
                        round((current - previous) / previous * 100.0, 6)
                    )
                previous = current
        return {
            "db_path": db_path,
            "source": source,
            "symbol": clean_symbol,
            "name": str(row_map.get("name") or clean_symbol),
            "price": price,
            "volume": volume,
            "trading_value": trading_value,
            "quote_source": str(row_map.get("source") or source),
            "fetched_at": str(row_map.get("fetched_at") or ""),
            "status": str(row_map.get("status") or "ok"),
            "error_message": str(row_map.get("error_message") or ""),
            **(
                {
                    "return_window_pct": return_window_pct,
                    "return_window_sample_count": len(return_window_pct),
                    "return_window_source": source,
                }
                if len(return_window_pct) >= 3
                else {}
            ),
        }
    return {}


def _extract_binance_regime(metadata: dict[str, Any]) -> str:
    for key in ("regime", "market_regime", "regime_label"):
        value = metadata.get(key)
        if isinstance(value, dict):
            nested = value.get("regime") or value.get("label") or value.get("state")
            if nested:
                return str(nested).strip()
        elif value:
            return str(value).strip()
    for key in ("crypto_market_pulse", "market_pulse", "crypto_research"):
        value = metadata.get(key)
        if not isinstance(value, dict):
            continue
        direct = value.get("regime")
        if direct:
            return str(direct).strip()
        regime_brief = value.get("regime_brief")
        if isinstance(regime_brief, dict):
            nested = regime_brief.get("regime") or regime_brief.get("label")
            if nested:
                return str(nested).strip()
        market_regime = value.get("market_regime")
        if isinstance(market_regime, dict):
            nested = market_regime.get("regime") or market_regime.get("label")
            if nested:
                return str(nested).strip()
        elif market_regime:
            return str(market_regime).strip()
    return ""


def _binance_spread_cost_from_metadata(
    *,
    block: dict[str, Any],
    metadata: dict[str, Any],
    round_trip_notional: float,
) -> dict[str, Any]:
    cost_components = (
        metadata.get("cost_components")
        if isinstance(metadata.get("cost_components"), dict)
        else {}
    )
    explicit_spread = _first_positive_float(
        metadata.get("spread_cost_usdt"),
        metadata.get("spread_usdt"),
        cost_components.get("spread"),
        cost_components.get("spread_cost"),
    )
    if explicit_spread > 0:
        return {
            "spread": explicit_spread,
            "spread_source": "explicit_spread_cost",
        }
    spread_bps = _first_positive_float(
        metadata.get("spread_bps"),
        block.get("spread_bps"),
    )
    if spread_bps <= 0:
        bid = _first_positive_float(metadata.get("bid_price"), block.get("bid_price"))
        ask = _first_positive_float(metadata.get("ask_price"), block.get("ask_price"))
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        if mid > 0 and ask >= bid:
            spread_bps = abs(ask - bid) / mid * 10_000.0
    spread = round_trip_notional * max(spread_bps, 0.0) / 10_000.0
    return {
        "spread": spread,
        "spread_bps": spread_bps,
        "spread_source": "market_spread_bps" if spread > 0 else "",
    }


def _binance_validation_costs(
    *,
    block: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    entry_price: float,
    exit_price: float,
    qty: float,
    reflection_fee: float,
    reflection_funding: float,
    reflection_slippage: float,
    reflection_spread: float = 0.0,
    reflection_cost_source: str = "",
    settings: Any,
) -> dict[str, Any]:
    metadata = metadata or {}
    market = str(block.get("market") or "spot").strip().lower()
    round_trip_notional = abs(entry_price * qty) + abs(exit_price * qty)
    spread_payload = _binance_spread_cost_from_metadata(
        block=block,
        metadata=metadata,
        round_trip_notional=round_trip_notional,
    )
    explicit_spread = max(_safe_float(reflection_spread), 0.0)
    spread = max(explicit_spread, _safe_float(spread_payload.get("spread")))
    explicit_trade_cost_total = reflection_fee + reflection_funding + reflection_slippage
    clean_cost_source = _compact_text(reflection_cost_source, limit=80).lower()
    explicit_source = clean_cost_source in {"explicit", "recorded", "exchange_fill"}
    partial_source = "partial" in clean_cost_source or "unconverted" in clean_cost_source
    if (
        explicit_trade_cost_total > 0
        or explicit_source
        or partial_source
        or market not in {"spot", "futures"}
    ):
        status = (
            "partial_unconverted_fee"
            if partial_source
            else "recorded"
            if explicit_trade_cost_total > 0 or explicit_source
            else "missing"
        )
        return {
            **spread_payload,
            "fees": max(reflection_fee, 0.0),
            "funding": max(reflection_funding, 0.0),
            "slippage": max(reflection_slippage, 0.0),
            "spread": spread,
            "status": status,
            "source": clean_cost_source or "reflection",
            "round_trip_notional_usdt": round(round_trip_notional, 6),
        }
    fee_rate = (
        _safe_float(getattr(settings, "binance_validation_futures_fee_rate", 0.0005))
        if market == "futures"
        else _safe_float(getattr(settings, "binance_validation_spot_fee_rate", 0.001))
    )
    slippage_bps = _safe_float(getattr(settings, "binance_validation_slippage_bps", 2.0))
    fees = round_trip_notional * max(fee_rate, 0.0)
    slippage = round_trip_notional * max(slippage_bps, 0.0) / 10_000.0
    return {
        **spread_payload,
        "fees": fees,
        "funding": 0.0,
        "slippage": slippage,
        "spread": spread,
        "status": "estimated_from_notional",
        "source": "estimated_round_trip_notional",
        "fee_rate": fee_rate,
        "slippage_bps": slippage_bps,
        "round_trip_notional_usdt": round(round_trip_notional, 6),
    }


def _kis_validation_costs(
    *,
    block: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    entry_price: float,
    exit_price: float,
    qty: float,
    settings: Any,
) -> dict[str, Any]:
    block = block or {}
    metadata = metadata or {}
    recorded = (
        metadata.get("performance")
        if isinstance(metadata.get("performance"), dict)
        else {}
    )
    if recorded:
        components = (
            recorded.get("cost_components")
            if isinstance(recorded.get("cost_components"), dict)
            else {}
        )
        fees = _safe_float(components.get("fees")) or _safe_float(recorded.get("fees"))
        taxes = _safe_float(components.get("taxes")) or _safe_float(recorded.get("taxes"))
        slippage = _safe_float(components.get("slippage")) or _safe_float(
            recorded.get("slippage")
        )
        spread = _safe_float(components.get("spread")) or _safe_float(recorded.get("spread"))
        funding = _safe_float(components.get("funding")) or _safe_float(recorded.get("funding"))
        total = (
            _safe_float(recorded.get("total_cost_krw"))
            or fees + taxes + slippage + spread + funding
        )
        if total > 0 or any(value > 0 for value in (fees, taxes, slippage, spread, funding)):
            status = str(
                recorded.get("cost_model_status")
                or recorded.get("status")
                or "recorded"
            ).strip()
            source = str(
                recorded.get("cost_source")
                or recorded.get("source")
                or "block_metadata_performance"
            ).strip()
            payload: dict[str, Any] = {
                "fees": fees,
                "taxes": taxes,
                "slippage": slippage,
                "spread": spread,
                "funding": funding,
                "total": total,
                "status": status or "recorded",
                "source": source or "block_metadata_performance",
                "performance_metadata_source": "block_metadata_performance",
                "round_trip_notional_krw": recorded.get("round_trip_notional_krw"),
                "buy_notional_krw": recorded.get("buy_notional_krw"),
                "sell_notional_krw": recorded.get("sell_notional_krw"),
            }
            for key in (
                "explicit_components",
                "estimated_components",
                "component_sources",
            ):
                value = recorded.get(key)
                if isinstance(value, dict) and value:
                    payload[key] = value
            return payload
    buy_notional = abs(entry_price * qty)
    sell_notional = abs(exit_price * qty)
    round_trip_notional = buy_notional + sell_notional
    if buy_notional <= 0 or sell_notional <= 0 or qty <= 0:
        return {
            "fees": 0.0,
            "taxes": 0.0,
            "slippage": 0.0,
            "spread": 0.0,
            "funding": 0.0,
            "status": "missing",
            "source": "missing_price_or_qty",
            "round_trip_notional_krw": round(round_trip_notional, 6),
        }
    buy_fee_rate = _safe_float(
        getattr(settings, "kis_validation_buy_fee_rate", 0.00015)
    )
    sell_fee_rate = _safe_float(
        getattr(settings, "kis_validation_sell_fee_rate", 0.00015)
    )
    sell_tax_rate = _safe_float(
        getattr(settings, "kis_validation_sell_tax_rate", 0.002)
    )
    slippage_bps = _safe_float(getattr(settings, "kis_validation_slippage_bps", 5.0))
    spread_bps = _safe_float(getattr(settings, "kis_validation_spread_bps", 0.0))
    is_etf = _is_kis_etf_block(block=block, metadata=metadata)
    fees = buy_notional * max(buy_fee_rate, 0.0) + sell_notional * max(
        sell_fee_rate,
        0.0,
    )
    taxes = 0.0 if is_etf else sell_notional * max(sell_tax_rate, 0.0)
    slippage = round_trip_notional * max(slippage_bps, 0.0) / 10_000.0
    spread = round_trip_notional * max(spread_bps, 0.0) / 10_000.0
    payload = {
        "fees": fees,
        "taxes": taxes,
        "slippage": slippage,
        "spread": spread,
        "funding": 0.0,
        "total": fees + taxes + slippage + spread,
        "status": "estimated_from_notional",
        "source": "estimated_round_trip_notional",
        "buy_fee_rate": buy_fee_rate,
        "sell_fee_rate": sell_fee_rate,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "round_trip_notional_krw": round(round_trip_notional, 6),
        "buy_notional_krw": round(buy_notional, 6),
        "sell_notional_krw": round(sell_notional, 6),
    }
    if is_etf:
        payload["tax_exempt_reason"] = "etf"
    return payload


def _is_kis_etf_block(
    *,
    block: dict[str, Any],
    metadata: dict[str, Any],
) -> bool:
    for key in ("is_etf", "etf", "is_exchange_traded_fund"):
        if _safe_bool(metadata.get(key)) or _safe_bool(block.get(key)):
            return True
    type_text = " ".join(
        str(value or "")
        for value in (
            metadata.get("asset_type"),
            metadata.get("asset_class"),
            metadata.get("security_type"),
            metadata.get("instrument_type"),
            block.get("asset_type"),
            block.get("asset_class"),
            block.get("security_type"),
        )
    ).upper()
    if "ETF" in type_text:
        return True
    name = str(
        metadata.get("name")
        or metadata.get("symbol_name")
        or block.get("name")
        or ""
    ).upper()
    etf_prefixes = (
        "KODEX",
        "TIGER",
        "ACE",
        "KBSTAR",
        "SOL",
        "RISE",
        "HANARO",
        "ARIRANG",
        "KOSEF",
        "TIMEFOLIO",
        "PLUS",
    )
    return name.startswith(etf_prefixes) or " ETF" in name


def _apply_binance_research_context(
    enriched: dict[str, Any],
    *,
    context: dict[str, Any],
) -> None:
    if not context:
        return
    if context.get("error_message"):
        enriched.setdefault("error_message", context.get("error_message"))
        return
    feature = context.get("feature") if isinstance(context.get("feature"), dict) else {}
    for key in (
        "bid_price",
        "ask_price",
        "spread_bps",
        "funding_rate",
        "open_interest",
        "volume_expansion_ratio",
        "wick_risk_score",
        "timeframe_alignment",
        "squeeze_risk",
        "entry_quality",
    ):
        if key in feature and enriched.get(key) in (None, ""):
            enriched[key] = feature.get(key)
    quote_volume = _first_positive_float(
        enriched.get("quote_volume_usdt"),
        enriched.get("daily_turnover_usdt"),
        feature.get("quote_volume_usdt"),
    )
    if quote_volume > 0:
        enriched.setdefault("quote_volume_usdt", quote_volume)
        enriched.setdefault("daily_turnover_usdt", quote_volume)
        enriched.setdefault("max_participation_rate", 0.01)
        enriched.setdefault("capacity_source", "crypto_market_research_features")
    feature_regime = (
        str(feature.get("regime") or "").strip()
        or str(context.get("feature_regime") or "").strip()
    )
    global_regime = str(context.get("global_regime") or "").strip()
    if feature_regime and not enriched.get("regime"):
        enriched["regime"] = feature_regime
    if global_regime and not enriched.get("market_regime"):
        enriched["market_regime"] = global_regime
    if (feature_regime or global_regime) and not enriched.get("market_regime_source"):
        enriched["market_regime_source"] = "crypto_market_research_db"
    if context.get("feature_updated_at"):
        enriched.setdefault("crypto_feature_updated_at", context.get("feature_updated_at"))
    if context.get("global_regime_captured_at"):
        enriched.setdefault(
            "crypto_regime_captured_at",
            context.get("global_regime_captured_at"),
        )
    if context.get("feature_score") is not None:
        enriched.setdefault("crypto_feature_score", context.get("feature_score"))


def _apply_binance_cost_research_context(
    enriched: dict[str, Any],
    *,
    context: dict[str, Any],
) -> None:
    feature = context.get("feature") if isinstance(context.get("feature"), dict) else {}
    for key in ("bid_price", "ask_price", "spread_bps"):
        if key in feature and enriched.get(key) in (None, ""):
            enriched[key] = feature.get(key)


def _enriched_binance_validation_metadata(
    *,
    block: dict[str, Any],
    metadata: dict[str, Any],
    entry_price: float,
    exit_price: float,
    qty: float,
    costs: dict[str, Any],
    research_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(metadata)
    symbol = str(block.get("symbol") or "").upper().strip()
    market = str(block.get("market") or "spot").strip().lower()
    side = str(block.get("side") or "long").strip().lower()
    base_asset = _binance_base_asset(symbol)
    major_bucket = "major_crypto" if base_asset in BINANCE_MAJOR_BASE_ASSETS else "alt_crypto"
    notional = abs(entry_price * qty)
    enriched.update(
        {
            "symbol": symbol,
            "market": market,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "block_notional_usdt": round(notional, 6),
            "notional_usdt": round(notional, 6),
            "round_trip_notional_usdt": costs.get("round_trip_notional_usdt"),
            "cost_model_status": costs.get("status"),
            "cost_source": costs.get("source"),
            "cost_components": {
                "fees": round(_safe_float(costs.get("fees")), 8),
                "funding": round(_safe_float(costs.get("funding")), 8),
                "slippage": round(_safe_float(costs.get("slippage")), 8),
                "spread": round(_safe_float(costs.get("spread")), 8),
            },
        }
    )
    metadata_regime = _extract_binance_regime(enriched)
    if metadata_regime and not any(enriched.get(key) for key in ("regime", "market_regime", "regime_label")):
        enriched["regime"] = metadata_regime
        enriched["regime_source"] = "nested_market_pulse"
    _apply_binance_research_context(enriched, context=research_context or {})
    if costs.get("fee_rate") is not None:
        enriched["fee_rate"] = costs.get("fee_rate")
    if costs.get("slippage_bps") is not None:
        enriched["slippage_bps"] = costs.get("slippage_bps")
    if costs.get("spread_bps") is not None:
        enriched["spread_bps"] = costs.get("spread_bps")
    if costs.get("spread_source"):
        enriched["spread_source"] = costs.get("spread_source")

    regime = _extract_binance_regime(enriched)
    if regime and not any(enriched.get(key) for key in ("regime", "market_regime", "regime_label")):
        enriched["regime"] = regime
        enriched["regime_source"] = "nested_market_pulse"

    enriched.setdefault("asset_cluster", major_bucket)
    enriched.setdefault("correlation_cluster", major_bucket)
    if not isinstance(enriched.get("factor_exposures"), dict) and not isinstance(enriched.get("factors"), dict):
        horizon = str(enriched.get("horizon") or enriched.get("lane") or market or "unknown").strip().lower()
        exposures = {
            "crypto_beta": 1.0,
            major_bucket: 0.7,
            f"{market}_lane": 0.5,
            f"{side}_direction": 0.4,
        }
        if horizon:
            exposures[f"{horizon}_horizon"] = 0.3
        enriched["factor_exposures"] = exposures
        enriched["factor_exposure_source"] = "derived_block_structure"
    return enriched


def _enriched_kis_validation_metadata(
    *,
    block: dict[str, Any],
    metadata: dict[str, Any],
    entry_price: float,
    exit_price: float,
    qty: float,
    costs: dict[str, Any],
    market_pulse: dict[str, Any],
    valuation_context: dict[str, Any],
    quote_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(metadata)
    symbol = str(block.get("symbol") or "").strip()
    notional = abs(entry_price * qty)
    enriched.update(
        {
            "symbol": symbol,
            "name": str(block.get("name") or enriched.get("name") or ""),
            "side": "long",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "notional_krw": round(notional, 6),
            "position_notional": round(notional, 6),
            "round_trip_notional_krw": costs.get("round_trip_notional_krw"),
            "cost_model_status": costs.get("status"),
            "cost_source": costs.get("source"),
            "cost_components": {
                "fees": round(_safe_float(costs.get("fees")), 6),
                "taxes": round(_safe_float(costs.get("taxes")), 6),
                "slippage": round(_safe_float(costs.get("slippage")), 6),
                "spread": round(_safe_float(costs.get("spread")), 6),
                "funding": round(_safe_float(costs.get("funding")), 6),
            },
        }
    )
    for key in (
        "buy_fee_rate",
        "sell_fee_rate",
        "sell_tax_rate",
        "slippage_bps",
        "spread_bps",
        "tax_exempt_reason",
        "performance_metadata_source",
    ):
        if costs.get(key) is not None:
            enriched[key] = costs.get(key)
    for key in (
        "explicit_components",
        "estimated_components",
        "component_sources",
    ):
        value = costs.get(key)
        if isinstance(value, dict) and value:
            enriched[key] = value
    if market_pulse.get("error_message"):
        enriched.setdefault("error_message", market_pulse.get("error_message"))
    regime = str(market_pulse.get("regime") or "").strip()
    if regime and not enriched.get("regime"):
        enriched["regime"] = regime
        enriched["market_regime_source"] = "market_pulse_db"
    if market_pulse:
        enriched.setdefault(
            "market_pulse",
            {
                "status": market_pulse.get("status"),
                "regime": market_pulse.get("regime"),
                "score": market_pulse.get("score"),
                "captured_at": market_pulse.get("captured_at"),
                "trading_day": market_pulse.get("trading_day"),
            },
        )
    sector = _sector_for_symbol(market_pulse, symbol=symbol) if market_pulse else {}
    if sector:
        sector_name = str(sector.get("name") or "").strip()
        if sector_name:
            enriched.setdefault("sector", sector_name)
            enriched.setdefault("asset_cluster", sector_name)
            enriched.setdefault("correlation_cluster", sector_name)
        enriched.setdefault("sector_direction", sector.get("direction"))
        enriched.setdefault("sector_strength", _safe_float(sector.get("avg_strength")))
    if valuation_context.get("error_message"):
        enriched.setdefault("error_message", valuation_context.get("error_message"))
    quote_context = quote_context or {}
    if quote_context.get("error_message"):
        enriched.setdefault("quote_error", quote_context.get("error_message"))
    trading_value = _first_positive_float(quote_context.get("trading_value"))
    if trading_value > 0:
        participation = _safe_float(enriched.get("max_participation_rate")) or 0.01
        if participation <= 0:
            participation = 0.01
        enriched.setdefault("daily_turnover_krw", round(trading_value, 6))
        enriched.setdefault("capacity_krw", round(trading_value * participation, 6))
        enriched.setdefault("max_participation_rate", participation)
        enriched.setdefault("capacity_source", str(quote_context.get("source") or "kis_quote_turnover"))
        enriched.setdefault("quote_fetched_at", quote_context.get("fetched_at"))
        enriched.setdefault("quote_source", quote_context.get("quote_source"))
        if _safe_float(quote_context.get("price")) > 0:
            enriched.setdefault("quote_price", quote_context.get("price"))
        if _safe_float(quote_context.get("volume")) > 0:
            enriched.setdefault("quote_volume", quote_context.get("volume"))
    return_window_pct = quote_context.get("return_window_pct")
    if (
        isinstance(return_window_pct, list)
        and len(return_window_pct) >= 3
        and not isinstance(enriched.get("return_window_pct"), list)
        and not isinstance(enriched.get("returns_window_pct"), list)
        and not isinstance(enriched.get("rolling_returns_pct"), list)
    ):
        enriched["return_window_pct"] = [
            round(_safe_float(value), 6) for value in return_window_pct[-80:]
        ]
        enriched["return_window_sample_count"] = len(enriched["return_window_pct"])
        enriched["return_window_source"] = str(
            quote_context.get("return_window_source")
            or quote_context.get("source")
            or "kis_quote_snapshots"
        )
    snapshot = (
        valuation_context.get("snapshot")
        if isinstance(valuation_context.get("snapshot"), dict)
        else {}
    )
    score = (
        valuation_context.get("score")
        if isinstance(valuation_context.get("score"), dict)
        else {}
    )
    if snapshot:
        for key in (
            "market_cap_krw",
            "per",
            "pbr",
            "industry_per",
            "industry_name",
            "dividend_yield_pct",
        ):
            if snapshot.get(key) is not None and enriched.get(key) in (None, ""):
                enriched[key] = snapshot.get(key)
        if snapshot.get("industry_name") and not enriched.get("sector"):
            industry = str(snapshot.get("industry_name") or "").strip()
            enriched["sector"] = industry
            enriched.setdefault("asset_cluster", industry)
            enriched.setdefault("correlation_cluster", industry)
        if snapshot.get("name") and not enriched.get("name"):
            enriched["name"] = snapshot.get("name")
    if score:
        enriched.setdefault("valuation_label", score.get("label"))
        enriched.setdefault("undervalued_score", _safe_float(score.get("undervalued_score")))
        enriched.setdefault("overvalued_risk", _safe_float(score.get("overvalued_risk")))
        enriched.setdefault("quality_score", _safe_float(score.get("quality_score")))
        enriched.setdefault("growth_score", _safe_float(score.get("growth_score")))
        enriched.setdefault(
            "relative_per_discount_pct",
            _safe_float(score.get("relative_per_discount_pct")),
        )
    if not isinstance(enriched.get("factor_exposures"), dict) and not isinstance(enriched.get("factors"), dict):
        factor_exposures: dict[str, float] = {"kr_equity_beta": 1.0}
        sector_name = str(enriched.get("sector") or "").strip()
        if sector_name:
            factor_exposures[f"sector_{_clean_factor_key(sector_name) or 'unknown'}"] = 0.7
        label = str(enriched.get("valuation_label") or "").strip().lower()
        undervalued = _safe_float(enriched.get("undervalued_score")) / 100.0
        overvalued = _safe_float(enriched.get("overvalued_risk")) / 100.0
        quality = _safe_float(enriched.get("quality_score")) / 100.0
        growth = _safe_float(enriched.get("growth_score")) / 100.0
        if label:
            factor_exposures[f"valuation_{_clean_factor_key(label)}"] = max(
                undervalued or overvalued,
                0.1,
            )
        if quality > 0:
            factor_exposures["quality"] = min(quality, 1.0)
        if growth > 0:
            factor_exposures["growth"] = min(growth, 1.0)
        if str(enriched.get("sector_direction") or "").strip().lower() == "positive":
            factor_exposures["sector_rotation_positive"] = 0.5
        enriched["factor_exposures"] = factor_exposures
        enriched["factor_exposure_source"] = "market_pulse_and_valuation_db"
    return enriched


def _sync_kis_performance(
    repository: LivePerformanceRepository,
    *,
    settings: Any,
) -> int:
    db_path = getattr(settings, "kis_block_trader_db_path", "")
    if not db_path:
        return 0
    conn = _connect_existing(db_path)
    if conn is None:
        return 0
    count = 0
    market_pulse = _latest_kis_market_pulse_context(settings)
    valuation_contexts: dict[str, dict[str, Any]] = {}
    quote_contexts: dict[str, dict[str, Any]] = {}
    with conn:
        rows = conn.execute(
            """
            SELECT *
            FROM blocks
            WHERE status IN ('closed', 'error')
            ORDER BY updated_at DESC
            LIMIT 500
            """
        ).fetchall()
        for row in rows:
            block = dict(row)
            metadata = _json_loads(block.get("metadata_json"))
            if not isinstance(metadata, dict):
                metadata = {}
            entry = _safe_float(block.get("entry_price"))
            exit_price = _latest_order_price(
                conn,
                block_id=str(block.get("block_id") or ""),
                side="sell",
                default=(
                    _safe_float(block.get("target_price"))
                    or _safe_float(block.get("stop_price"))
                    or entry
                ),
            )
            qty = _safe_float(block.get("qty_initial")) or _safe_float(
                block.get("qty_open")
            )
            status = str(block.get("status") or "")
            symbol = str(block.get("symbol") or "")
            if symbol not in valuation_contexts:
                valuation_contexts[symbol] = _latest_kis_valuation_context(
                    settings,
                    symbol=symbol,
                )
            if symbol not in quote_contexts:
                quote_contexts[symbol] = _latest_kis_quote_context(
                    settings,
                    symbol=symbol,
                )
            fill_evidence = _kis_block_fill_evidence(
                conn,
                block=block,
                metadata=metadata,
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
            )
            if bool(fill_evidence.get("filled")):
                filled_qty = _safe_float(fill_evidence.get("filled_qty"))
                buy_avg_fill_price = _safe_float(
                    fill_evidence.get("buy_avg_fill_price")
                )
                sell_avg_fill_price = _safe_float(
                    fill_evidence.get("sell_avg_fill_price")
                )
                if filled_qty > 0:
                    qty = filled_qty
                if buy_avg_fill_price > 0:
                    entry = buy_avg_fill_price
                if sell_avg_fill_price > 0:
                    exit_price = sell_avg_fill_price
            if bool(fill_evidence.get("filled")):
                costs = _kis_validation_costs(
                    block=block,
                    metadata=metadata,
                    entry_price=entry,
                    exit_price=exit_price,
                    qty=qty,
                    settings=settings,
                )
            else:
                exit_price = entry
                costs = {
                    "fees": 0.0,
                    "taxes": 0.0,
                    "slippage": 0.0,
                    "spread": 0.0,
                    "funding": 0.0,
                    "status": "missing",
                    "source": str(fill_evidence.get("status") or "unfilled"),
                    "round_trip_notional_krw": round(
                        abs(entry * qty) + abs(exit_price * qty),
                        6,
                    ),
                }
            enriched_metadata = _enriched_kis_validation_metadata(
                block=block,
                metadata=metadata,
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
                costs=costs,
                market_pulse=market_pulse,
                valuation_context=valuation_contexts[symbol],
                quote_context=quote_contexts[symbol],
            )
            enriched_metadata.update(
                {
                    "fill_evidence_status": str(fill_evidence.get("status") or ""),
                    "fill_evidence_reason": str(fill_evidence.get("reason") or ""),
                    "buy_fill_count": int(fill_evidence.get("buy_fill_count") or 0),
                    "sell_fill_count": int(fill_evidence.get("sell_fill_count") or 0),
                    "buy_filled_qty": _safe_float(
                        fill_evidence.get("buy_filled_qty")
                    ),
                    "sell_filled_qty": _safe_float(
                        fill_evidence.get("sell_filled_qty")
                    ),
                    "filled_qty": _safe_float(fill_evidence.get("filled_qty")),
                    "entry_price_source": (
                        "buy_fill_avg"
                        if _safe_float(fill_evidence.get("buy_avg_fill_price")) > 0
                        else "block_entry_price"
                    ),
                    "exit_price_source": (
                        "sell_fill_avg"
                        if _safe_float(fill_evidence.get("sell_avg_fill_price")) > 0
                        else "sell_order_or_default"
                    ),
                }
            )
            repository.upsert_performance(
                BlockPerformanceInput(
                    venue="kis",
                    block_id=str(block.get("block_id") or ""),
                    symbol=str(block.get("symbol") or ""),
                    created_by=str(block.get("created_by") or ""),
                    status=status,
                    entry_price=entry,
                    exit_price=exit_price,
                    qty=qty,
                    fees=_safe_float(costs.get("fees")),
                    taxes=_safe_float(costs.get("taxes")),
                    funding=_safe_float(costs.get("funding")),
                    slippage=_safe_float(costs.get("slippage")),
                    spread=_safe_float(costs.get("spread")),
                    filled=bool(fill_evidence.get("filled")),
                    error_type=str(block.get("llm_reason") or "") if status == "error" else "",
                    metadata=enriched_metadata,
                ),
                source={"block": block, "metadata": enriched_metadata},
            )
            count += 1
    return count


def _sync_binance_performance(
    repository: LivePerformanceRepository,
    *,
    settings: Any,
) -> int:
    db_path = getattr(settings, "binance_block_trader_db_path", "")
    if not db_path:
        return 0
    conn = _connect_existing(db_path)
    if conn is None:
        return 0
    count = 0
    crypto_contexts: dict[str, dict[str, Any]] = {}
    with conn:
        reflection_columns = _table_columns(conn, "block_performance_reflections")
        reflection_spread_expr = (
            "r.spread_usdt AS reflection_spread"
            if "spread_usdt" in reflection_columns
            else "0.0 AS reflection_spread"
        )
        reflection_cost_source_expr = (
            "r.cost_source AS reflection_cost_source"
            if "cost_source" in reflection_columns
            else "'' AS reflection_cost_source"
        )
        rows = conn.execute(
            f"""
            SELECT
                b.*,
                r.exit_price AS reflection_exit_price,
                r.fee_usdt AS reflection_fee,
                r.funding_usdt AS reflection_funding,
                r.slippage_usdt AS reflection_slippage,
                {reflection_spread_expr},
                {reflection_cost_source_expr}
            FROM blocks b
            LEFT JOIN block_performance_reflections r ON r.block_id = b.block_id
            WHERE b.status IN ('closed', 'error')
            ORDER BY b.updated_at DESC
            LIMIT 500
            """
        ).fetchall()
        for row in rows:
            block = dict(row)
            metadata = _json_loads(block.get("metadata_json"))
            if not isinstance(metadata, dict):
                metadata = {}
            entry = _safe_float(block.get("entry_price"))
            reflection_exit_price = _safe_float(block.get("reflection_exit_price"))
            entry_side, exit_side = _binance_order_sides(block)
            exit_order_evidence = _latest_order_price_evidence(
                conn,
                block_id=str(block.get("block_id") or ""),
                side=exit_side,
            )
            exit_price = reflection_exit_price or _safe_float(exit_order_evidence.get("price"))
            qty = _safe_float(block.get("qty_initial")) or _safe_float(
                block.get("qty_open")
            )
            status = str(block.get("status") or "")
            symbol = str(block.get("symbol") or "").upper().strip()
            fill_evidence = _binance_block_fill_evidence(
                conn,
                block=block,
                metadata=metadata,
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
                reflection_exit_price=reflection_exit_price,
            )
            synthetic_exit_reference = (
                _safe_float(block.get("target_price"))
                or _safe_float(block.get("stop_price"))
                or entry
            )
            if not bool(fill_evidence.get("filled")):
                exit_price = entry
            if symbol not in crypto_contexts:
                crypto_contexts[symbol] = _latest_crypto_research_context(
                    settings,
                    symbol=symbol,
                )
            cost_metadata = dict(metadata)
            _apply_binance_cost_research_context(
                cost_metadata,
                context=crypto_contexts[symbol],
            )
            if bool(fill_evidence.get("filled")):
                costs = _binance_validation_costs(
                    block=block,
                    metadata=cost_metadata,
                    entry_price=entry,
                    exit_price=exit_price,
                    qty=qty,
                    reflection_fee=_safe_float(block.get("reflection_fee")),
                    reflection_funding=_safe_float(block.get("reflection_funding")),
                    reflection_slippage=_safe_float(block.get("reflection_slippage")),
                    reflection_spread=_safe_float(block.get("reflection_spread")),
                    reflection_cost_source=str(
                        block.get("reflection_cost_source") or ""
                    ),
                    settings=settings,
                )
            else:
                costs = {
                    "fees": 0.0,
                    "funding": 0.0,
                    "slippage": 0.0,
                    "spread": 0.0,
                    "status": "missing",
                    "source": str(fill_evidence.get("status") or "unfilled"),
                    "round_trip_notional_usdt": 0.0,
                }
            enriched_metadata = _enriched_binance_validation_metadata(
                block=block,
                metadata=cost_metadata,
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
                costs=costs,
                research_context=crypto_contexts[symbol],
            )
            enriched_metadata.update(
                {
                    "fill_evidence_status": str(fill_evidence.get("status") or ""),
                    "fill_evidence_reason": str(fill_evidence.get("reason") or ""),
                    "entry_order_filled": fill_evidence.get("entry_order_filled"),
                    "exit_order_filled": fill_evidence.get("exit_order_filled"),
                    "entry_order_status": fill_evidence.get("entry_order_status"),
                    "exit_order_status": fill_evidence.get("exit_order_status"),
                    "entry_price_source": fill_evidence.get("entry_price_source"),
                    "exit_price_source": (
                        "reflection"
                        if reflection_exit_price > 0
                        else fill_evidence.get("exit_price_source")
                    ),
                    "requested_exit_side": exit_side,
                    "requested_entry_side": entry_side,
                }
            )
            if not bool(fill_evidence.get("filled")):
                enriched_metadata["synthetic_exit_reference_price"] = synthetic_exit_reference
                enriched_metadata["synthetic_exit_reference_source"] = "target_stop_or_entry"
            repository.upsert_performance(
                BlockPerformanceInput(
                    venue="binance",
                    block_id=str(block.get("block_id") or ""),
                    symbol=str(block.get("symbol") or ""),
                    created_by=str(block.get("created_by") or ""),
                    status=status,
                    entry_price=entry,
                    exit_price=exit_price,
                    qty=qty,
                    fees=_safe_float(costs.get("fees")),
                    funding=_safe_float(costs.get("funding")),
                    slippage=_safe_float(costs.get("slippage")),
                    spread=_safe_float(costs.get("spread")),
                    filled=bool(fill_evidence.get("filled")),
                    error_type=str(block.get("llm_reason") or "") if status == "error" else "",
                    metadata=enriched_metadata,
                ),
                source={"block": block, "metadata": enriched_metadata},
            )
            count += 1
    return count


def _outcomes_from_performance(
    rows: list[dict[str, Any]],
    *,
    venue: str,
) -> dict[tuple[str, str], list[EvidenceOutcome]]:
    grouped: dict[tuple[str, str], list[EvidenceOutcome]] = {}
    for row in rows:
        if str(row.get("venue") or "") != venue:
            continue
        attribution = str(row.get("attribution") or "")
        include_alpha = bool(int(row.get("include_in_jue_alpha") or 0))
        execution_error = attribution == "operational_failure_pre_fill"
        if not include_alpha and not execution_error:
            continue
        source = _json_loads(row.get("source_json"))
        metadata = source.get("metadata") if isinstance(source, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        family = (
            _binance_live_edge_family(metadata)
            if venue == "binance"
            else _kis_live_edge_family(source, metadata)
        )
        key = _live_edge_evidence_key(metadata)
        validation_evidence = _scale_validation_evidence_from_metadata(metadata)
        outcome = EvidenceOutcome(
            venue=venue,
            strategy_family=family,
            evidence_key=key,
            net_pnl_pct=_safe_float(row.get("pnl_pct")),
            r_multiple=_safe_float(metadata.get("r_multiple")),
            rule_followed=not execution_error,
            strategy_revision_id=(
                str(row.get("strategy_revision_id") or "").strip()
                or _strategy_revision_id_from_metadata(metadata)
            ),
            execution_error=execution_error,
            gross_pnl=_safe_float(row.get("gross_pnl")),
            cost_total=_safe_float(row.get("cost_total")),
            cost_precision=str(
                row.get("cost_precision")
                or metadata.get("cost_precision")
                or metadata.get("cost_model_status")
                or ""
            ),
            fill_evidence_status=str(
                row.get("fill_evidence_status")
                or metadata.get("fill_evidence_status")
                or ""
            ),
            entry_quality_score=_safe_float(
                row.get("entry_quality_score")
                or metadata.get("entry_quality_score")
                or 0.0
            ),
            entry_quality_label=str(
                row.get("entry_quality_label")
                or metadata.get("entry_quality")
                or metadata.get("entry_setup")
                or ""
            ),
            backtest_passed=validation_evidence["backtest_passed"],
            walk_forward_passed=validation_evidence["walk_forward_passed"],
            out_of_sample_passed=validation_evidence["out_of_sample_passed"],
            live_shadow_passed=validation_evidence["live_shadow_passed"],
        )
        grouped.setdefault((family, key), []).append(outcome)
        for discipline_id in _validation_repair_disciplines(metadata):
            repair_family = f"{family}:validation:{discipline_id}"
            grouped.setdefault((repair_family, "all"), []).append(
                EvidenceOutcome(
                    venue=venue,
                    strategy_family=repair_family,
                    evidence_key="all",
                    net_pnl_pct=outcome.net_pnl_pct,
                    r_multiple=outcome.r_multiple,
                    rule_followed=outcome.rule_followed,
                    strategy_revision_id=outcome.strategy_revision_id,
                    execution_error=outcome.execution_error,
                    gross_pnl=outcome.gross_pnl,
                    cost_total=outcome.cost_total,
                    cost_precision=outcome.cost_precision,
                    fill_evidence_status=outcome.fill_evidence_status,
                    entry_quality_score=outcome.entry_quality_score,
                    entry_quality_label=outcome.entry_quality_label,
                    backtest_passed=outcome.backtest_passed,
                    walk_forward_passed=outcome.walk_forward_passed,
                    out_of_sample_passed=outcome.out_of_sample_passed,
                    live_shadow_passed=outcome.live_shadow_passed,
                )
            )
    return grouped


def _refresh_live_edge_scorecards(
    performance_repo: LivePerformanceRepository,
    edge_repo: LiveEdgeRepository,
    *,
    settings: Any,
) -> dict[str, Any]:
    rows = performance_repo.latest(limit=500)
    updated = 0
    deleted = 0
    for venue in ("kis", "binance"):
        grouped = _outcomes_from_performance(rows, venue=venue)
        active_keys: set[tuple[str, str, str]] = set()
        for (family, key), outcomes in grouped.items():
            by_revision: dict[str, list[EvidenceOutcome]] = {}
            for outcome in outcomes:
                by_revision.setdefault(
                    str(outcome.strategy_revision_id or "").strip(),
                    [],
                ).append(outcome)
            for revision_id, revision_outcomes in by_revision.items():
                scorecard = compute_edge_scorecard(
                    revision_outcomes,
                    min_samples_for_grade=int(
                        getattr(settings, "live_authority_min_samples_to_scale", 10)
                    ),
                )
                scorecard["strategy_revision_id"] = revision_id
                edge_repo.upsert_scorecard(
                    venue=venue,
                    strategy_family=family,
                    evidence_key=key,
                    scorecard=scorecard,
                )
                active_keys.add((family, key, revision_id))
                updated += 1
        deleted += edge_repo.delete_scorecards_not_in(
            venue=venue,
            active_keys=active_keys,
        )
    return {"status": "ok", "updated_scorecards": updated, "deleted_scorecards": deleted}


def sync_live_performance_and_edges(settings: Any) -> dict[str, Any]:
    performance_repo = LivePerformanceRepository(settings.live_performance_db_path)
    edge_repo = LiveEdgeRepository(settings.live_evaluator_db_path)
    active_revision_id = str(
        getattr(settings, "jue_strategy_revision_id", "")
        or ""
    ).strip()
    revision_repair = {
        "kis": _repair_open_block_strategy_revision_metadata(
            getattr(settings, "kis_block_trader_db_path", ""),
            strategy_revision_id=active_revision_id,
            venue="kis",
        ),
        "binance": _repair_open_block_strategy_revision_metadata(
            getattr(settings, "binance_block_trader_db_path", ""),
            strategy_revision_id=active_revision_id,
            venue="binance",
        ),
    }
    synced = {
        "kis": _sync_kis_performance(performance_repo, settings=settings),
        "binance": _sync_binance_performance(performance_repo, settings=settings),
    }
    edge = _refresh_live_edge_scorecards(
        performance_repo,
        edge_repo,
        settings=settings,
    )
    return {
        "status": "ok",
        "strategy_revision_metadata_repair": revision_repair,
        "synced_blocks": synced,
        "edge": edge,
    }


def refresh_trading_validation(settings: Any) -> dict[str, Any]:
    kr_pattern_lab: dict[str, Any] = {"status": "disabled"}
    if bool(getattr(settings, "kr_equity_pattern_lab_enabled", False)):
        kr_pattern_lab = KREquityPatternLabService(
            KREquityPatternLabConfig(
                db_path=str(
                    getattr(
                        settings,
                        "kr_equity_pattern_lab_db_path",
                        ".runtime/kr_equity_pattern_lab.db",
                    )
                ),
                live_performance_db_path=str(
                    getattr(
                        settings,
                        "live_performance_db_path",
                        ".runtime/live_performance.db",
                    )
                ),
                market_judgment_db_path=str(
                    getattr(
                        settings,
                        "market_judge_db_path",
                        ".runtime/market_judgment.db",
                    )
                ),
                min_samples=int(
                    getattr(settings, "kr_equity_pattern_lab_min_samples", 3)
                ),
            )
        ).run_once()

    def service_for(initial_equity: float) -> TradingValidationService:
        return TradingValidationService(
            TradingValidationConfig(
                validation_db_path=str(
                    getattr(
                        settings,
                        "trading_validation_db_path",
                        ".runtime/trading_validation.db",
                    )
                ),
                live_performance_db_path=str(
                    getattr(
                        settings,
                        "live_performance_db_path",
                        ".runtime/live_performance.db",
                    )
                ),
                crypto_pattern_lab_db_path=str(
                    getattr(
                        settings,
                        "crypto_pattern_lab_db_path",
                        ".runtime/crypto_pattern_lab.db",
                    )
                ),
                kr_equity_pattern_lab_db_path=str(
                    getattr(
                        settings,
                        "kr_equity_pattern_lab_db_path",
                        ".runtime/kr_equity_pattern_lab.db",
                    )
                ),
                strategy_revision_id=str(
                    getattr(settings, "jue_strategy_revision_id", "")
                ).strip(),
                initial_equity=max(float(initial_equity), 1.0),
            )
        )

    kis_service = service_for(
        _safe_float(
            getattr(settings, "kis_validation_initial_equity_krw", 4_000_000.0)
        )
        or 4_000_000.0
    )
    binance_service = service_for(
        _safe_float(
            getattr(settings, "binance_validation_initial_equity_usdt", 1_000.0)
        )
        or 1_000.0
    )
    validation = {
        "kr_equity_pattern_lab": kr_pattern_lab,
        "kis": kis_service.run_once(venue="kis"),
        "binance": binance_service.run_once(venue="binance"),
    }
    if bool(getattr(settings, "trading_validation_payload_compaction_enabled", True)):
        try:
            validation["history_compaction"] = TradingValidationRepository(
                getattr(
                    settings,
                    "trading_validation_db_path",
                    ".runtime/trading_validation.db",
                )
            ).compact_history(
                recent_rows_per_group=int(
                    getattr(
                        settings,
                        "trading_validation_payload_recent_rows_per_group",
                        48,
                    )
                ),
                max_rows_per_group=int(
                    getattr(
                        settings,
                        "trading_validation_payload_max_rows_per_group",
                        720,
                    )
                ),
                min_payload_chars=int(
                    getattr(
                        settings,
                        "trading_validation_payload_compact_min_chars",
                        20_000,
                    )
                ),
                vacuum=False,
            )
        except Exception as exc:
            validation["history_compaction"] = {
                "status": "error",
                "error_message": str(exc),
            }
            logger.warning("trading validation history compaction failed: %s", exc)
    else:
        validation["history_compaction"] = {"status": "disabled"}
    return validation


def _lane_key_from_edge_scorecard(row: dict[str, Any]) -> str:
    family = _compact_text(row.get("strategy_family"), limit=80)
    evidence_key = _compact_text(row.get("evidence_key"), limit=80)
    if family and family != "all" and evidence_key and evidence_key != "all":
        return f"{family}:{evidence_key}"
    return family or evidence_key or "all"


def _live_edge_validation_repair_items(
    settings: Any,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    db_path = str(getattr(settings, "live_evaluator_db_path", ".runtime/live_edge.db"))
    strategy_revision_id = _compact_text(
        getattr(settings, "jue_strategy_revision_id", ""),
        limit=120,
    )
    try:
        repo = LiveEdgeRepository(db_path)
    except Exception:
        return []

    items: list[dict[str, Any]] = []
    for venue in ("kis", "binance"):
        try:
            rows = repo.list_scorecards(
                venue=venue,
                strategy_revision_id=strategy_revision_id,
                limit=max(int(limit) * 4, 20),
            )
        except Exception:
            continue
        weak_rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and bool(row.get("scale_blocked_by_validation_evidence"))
        ]
        if not weak_rows:
            continue

        target_lanes: list[str] = []
        missing_dimensions: list[str] = []
        failed_dimensions: list[str] = []

        def add_unique(target: list[str], value: Any, *, text_limit: int = 80) -> None:
            clean = _compact_text(value, limit=text_limit)
            if clean and clean not in target:
                target.append(clean)

        for row in weak_rows[: max(int(limit), 1)]:
            add_unique(target_lanes, _lane_key_from_edge_scorecard(row), text_limit=120)
            for dimension in list(row.get("validation_missing_dimensions") or []):
                add_unique(missing_dimensions, dimension)
            for dimension in list(row.get("validation_failed_dimensions") or []):
                add_unique(failed_dimensions, dimension)

        status = "fail" if failed_dimensions else "missing"
        priority = "p0" if failed_dimensions else "p1"
        artifact = (
            "crypto_pattern_lab_runner"
            if venue == "binance"
            else "kr_equity_pattern_lab"
        )
        label = (
            "Lane validation evidence failed"
            if failed_dimensions
            else "Lane validation evidence missing"
        )
        items.append(
            {
                "venue": venue,
                "discipline_id": "lane_validation_evidence",
                "label": label,
                "status": status,
                "priority": priority,
                "owner": "live_edge_validator",
                "cadence": "next_validation_run",
                "validation_mode": "backtest_wfa_oos_rebuild",
                "allowed_entry_posture": "shadow_or_waiting_entry_only",
                "scale_up_blocked": True,
                "live_shadow_required": True,
                "runner_hint": (
                    f"{artifact} must rebuild active backtest/WFA/OOS/live-shadow "
                    "evidence for validation-evidence-weak lanes"
                ),
                "verification_artifact": artifact,
                "exit_criteria": (
                    "lane scorecards expose validated backtest, walk-forward, "
                    "out-of-sample, and live-shadow evidence before scale-up"
                ),
                "evidence_targets": {
                    "target_lanes": target_lanes[: max(int(limit), 1)],
                    "missing_dimensions": missing_dimensions[:8],
                    "failed_dimensions": failed_dimensions[:8],
                    "scorecard_count": len(weak_rows),
                    "strategy_revision_id": strategy_revision_id,
                },
            }
        )
    return items


def _append_live_edge_validation_repair_items(
    settings: Any,
    repair_backlog: dict[str, Any],
) -> dict[str, Any]:
    items = _live_edge_validation_repair_items(settings)
    if not items:
        return repair_backlog
    merged = {
        "status": repair_backlog.get("status") or "clear",
        "total_item_count": int(repair_backlog.get("total_item_count") or 0),
        "venues": dict(repair_backlog.get("venues") or {}),
        "primary_items": list(repair_backlog.get("primary_items") or []),
    }
    for item in items:
        venue = _compact_text(item.get("venue"), limit=40) or "core"
        venue_backlog = (
            dict(merged["venues"].get(venue))
            if isinstance(merged["venues"].get(venue), dict)
            else {"status": "clear", "item_count": 0, "items": []}
        )
        venue_items = [
            row for row in list(venue_backlog.get("items") or []) if isinstance(row, dict)
        ]
        discipline_id = _compact_text(item.get("discipline_id"), limit=80)
        if not any(
            _compact_text(row.get("discipline_id"), limit=80) == discipline_id
            for row in venue_items
        ):
            venue_items.append(item)
            merged["primary_items"].append(item)
        venue_backlog["items"] = venue_items[:8]
        venue_backlog["item_count"] = len(venue_items)
        venue_backlog["status"] = "needs_repair" if venue_items else "clear"
        merged["venues"][venue] = venue_backlog
    merged["primary_items"] = merged["primary_items"][:8]
    merged["total_item_count"] = sum(
        int(backlog.get("item_count") or len(backlog.get("items") or []))
        for backlog in merged["venues"].values()
        if isinstance(backlog, dict)
    )
    merged["status"] = "needs_repair" if merged["total_item_count"] else "clear"
    return merged


def _ingest_trading_validation_memory_signals(
    settings: Any,
    validation: dict[str, Any],
) -> dict[str, Any]:
    empty_repair_backlog = {
        "status": "clear",
        "total_item_count": 0,
        "venues": {},
        "primary_items": [],
    }
    if not bool(getattr(settings, "investment_memory_enabled", False)):
        repair_backlog = _append_live_edge_validation_repair_items(
            settings,
            empty_repair_backlog,
        )
        return {
            "status": "skipped",
            "reason": "investment_memory_disabled",
            "repair_backlog": repair_backlog,
            "repair_manifest": _build_validation_repair_manifest(repair_backlog),
        }
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(
                getattr(
                    settings,
                    "investment_memory_root_path",
                    ".runtime/investment_memory",
                )
            ),
            db_path=str(
                getattr(
                    settings,
                    "investment_memory_db_path",
                    ".runtime/investment_memory.db",
                )
            ),
            policy_mode=str(
                getattr(settings, "investment_memory_policy_mode", "soft_auto")
            ),
        )
    )
    results: dict[str, Any] = {}
    repair_backlogs: dict[str, Any] = {}
    for venue in ("kis", "binance"):
        payload = validation.get(venue) if isinstance(validation.get(venue), dict) else {}
        results[venue] = service.ingest_trading_validation_signals(
            venue=venue,
            validation=payload,
        )
        repair_backlogs[venue] = service.validation_repair_backlog(
            target_scope=venue,
            limit=6,
        )
    primary_items: list[dict[str, Any]] = []
    for venue, backlog in repair_backlogs.items():
        if not isinstance(backlog, dict):
            continue
        for item in list(backlog.get("items") or [])[:3]:
            if not isinstance(item, dict):
                continue
            primary_items.append({**item, "venue": item.get("venue") or venue})
    priority_rank = {"p0": 0, "p1": 1, "p2": 2}
    primary_items = sorted(
        primary_items,
        key=lambda row: (
            priority_rank.get(str(row.get("priority") or "").lower(), 9),
            str(row.get("venue") or ""),
            str(row.get("discipline_id") or ""),
        ),
    )[:6]
    total_item_count = sum(
        int(backlog.get("item_count") or len(backlog.get("items") or []))
        for backlog in repair_backlogs.values()
        if isinstance(backlog, dict)
    )
    repair_backlog = {
        "status": "needs_repair" if total_item_count else "clear",
        "total_item_count": total_item_count,
        "venues": repair_backlogs,
        "primary_items": primary_items,
    }
    repair_backlog = _append_live_edge_validation_repair_items(
        settings,
        repair_backlog,
    )
    repair_manifest = _build_validation_repair_manifest(repair_backlog)
    return {
        "status": "ok",
        "venues": results,
        "repair_backlog": repair_backlog,
        "repair_manifest": repair_manifest,
    }


def _ingest_validation_repair_execution_memory(
    settings: Any,
    repair_execution: dict[str, Any],
) -> dict[str, Any]:
    if not bool(getattr(settings, "investment_memory_enabled", False)):
        return {"status": "skipped", "reason": "investment_memory_disabled"}
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(
                getattr(
                    settings,
                    "investment_memory_root_path",
                    ".runtime/investment_memory",
                )
            ),
            db_path=str(
                getattr(
                    settings,
                    "investment_memory_db_path",
                    ".runtime/investment_memory.db",
                )
            ),
            policy_mode=str(
                getattr(settings, "investment_memory_policy_mode", "soft_auto")
            ),
        )
    )
    return service.ingest_validation_repair_execution(repair_execution)


async def run_live_evaluator_once(settings: Any) -> dict[str, Any]:
    performance_repo = LivePerformanceRepository(settings.live_performance_db_path)
    sync = sync_live_performance_and_edges(settings)
    validation = refresh_trading_validation(settings)
    memory_signals = _ingest_trading_validation_memory_signals(settings, validation)
    repair_execution = _execute_validation_repair_manifest(
        settings,
        memory_signals.get("repair_manifest")
        if isinstance(memory_signals.get("repair_manifest"), dict)
        else {},
        sync=sync,
        validation=validation,
    )
    repair_memory = _ingest_validation_repair_execution_memory(
        settings,
        repair_execution,
    )
    authority = build_live_authority_payload(settings)
    current_kis_repair_execution = compact_repair_execution_for_venue(
        repair_execution,
        "kis",
    )
    current_binance_repair_execution = compact_repair_execution_for_venue(
        repair_execution,
        "binance",
    )
    if current_kis_repair_execution:
        authority["venues"]["kis"]["repair_execution"] = current_kis_repair_execution
    if current_binance_repair_execution:
        authority["venues"]["binance"]["repair_execution"] = (
            current_binance_repair_execution
        )
    result = {
        "service": "tradecraft-live-evaluator",
        "status": "ok",
        "ran_at": _now(),
        "enabled": bool(getattr(settings, "live_evaluator_enabled", True)),
        "sync": sync,
        "validation": validation,
        "memory_signals": memory_signals,
        "repair_execution": repair_execution,
        "repair_memory": repair_memory,
        "performance": performance_repo.summary(),
        "authority": authority,
    }
    RuntimeStateStore(settings.live_evaluator_state_path).write_snapshot(
        _compact_live_evaluator_state(result)
    )
    return result


async def run_live_evaluator_loop(settings: AppSettings | None = None) -> None:
    settings = settings or AppSettings()
    interval = max(int(settings.live_evaluator_interval_sec), 30)
    while True:
        try:
            if bool(settings.live_evaluator_enabled):
                result = await run_live_evaluator_once(settings)
                validation = (
                    result.get("validation")
                    if isinstance(result.get("validation"), dict)
                    else {}
                )
                kis_validation = (
                    validation.get("kis")
                    if isinstance(validation.get("kis"), dict)
                    else {}
                )
                binance_validation = (
                    validation.get("binance")
                    if isinstance(validation.get("binance"), dict)
                    else {}
                )
                logger.info(
                    "live evaluator cycle status=%s kis_validation=%s "
                    "binance_validation=%s",
                    str(result.get("status") or "unknown"),
                    str(kis_validation.get("status") or "unknown"),
                    str(binance_validation.get("status") or "unknown"),
                )
            if bool(getattr(settings, "live_evaluator_once", False)):
                return
        except Exception:
            logger.exception("live evaluator cycle failed")
            if bool(getattr(settings, "live_evaluator_once", False)):
                raise
        await asyncio.sleep(interval)


def run() -> None:
    write_current_runner_pid("live_evaluator")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(run_live_evaluator_loop())
    except KeyboardInterrupt:
        logger.info("live evaluator runner interrupted; stopping")
