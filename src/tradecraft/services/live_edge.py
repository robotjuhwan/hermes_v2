from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EDGE_FRACTIONAL_KELLY = 0.25
EDGE_REFERENCE_RISK_FRACTION = 0.02


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    venue: str
    strategy_family: str
    evidence_key: str
    net_pnl_pct: float
    r_multiple: float
    rule_followed: bool
    strategy_revision_id: str = ""
    execution_error: bool = False
    gross_pnl: float = 0.0
    cost_total: float = 0.0
    cost_precision: str = "recorded"
    fill_evidence_status: str = ""
    entry_quality_score: float = 0.0
    entry_quality_label: str = ""
    backtest_passed: bool | None = None
    walk_forward_passed: bool | None = None
    out_of_sample_passed: bool | None = None
    live_shadow_passed: bool | None = None


def _cost_precision_bucket(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "recorded":
        return "recorded"
    if "hybrid" in normalized:
        return "hybrid"
    if "estimated" in normalized:
        return "estimated"
    return "missing"


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(min(float(value), upper), lower)


def _validation_evidence_profile(
    outcomes: list[EvidenceOutcome],
    *,
    min_samples_for_grade: int,
) -> dict[str, Any]:
    checks = {
        "backtest": "backtest_passed",
        "walk_forward": "walk_forward_passed",
        "out_of_sample": "out_of_sample_passed",
        "live_shadow": "live_shadow_passed",
    }
    sample_count = len(outcomes)
    pass_counts: dict[str, int] = {}
    fail_counts: dict[str, int] = {}
    missing_counts: dict[str, int] = {}
    for label, attr in checks.items():
        values = [getattr(outcome, attr) for outcome in outcomes]
        pass_counts[label] = sum(1 for value in values if value is True)
        fail_counts[label] = sum(1 for value in values if value is False)
        missing_counts[label] = sum(1 for value in values if value is None)

    evidence_sample_count = sum(
        1
        for outcome in outcomes
        if any(getattr(outcome, attr) is not None for attr in checks.values())
    )
    failed_dimensions = [
        label for label, count in fail_counts.items() if count > 0
    ]
    missing_dimensions = [
        label
        for label, count in pass_counts.items()
        if count <= 0 and fail_counts[label] <= 0
    ]
    passed_dimensions = [
        label
        for label, count in pass_counts.items()
        if count > 0 and fail_counts[label] <= 0
    ]
    if sample_count < min_samples_for_grade:
        status = "not_required_until_min_samples"
    elif failed_dimensions:
        status = "failed"
    elif len(passed_dimensions) == len(checks):
        status = "validated"
    elif evidence_sample_count > 0:
        status = "partial"
    else:
        status = "missing"
    return {
        "validation_evidence_status": status,
        "validation_evidence_sample_count": evidence_sample_count,
        "validation_passed_dimension_count": len(passed_dimensions),
        "validation_failed_dimension_count": len(failed_dimensions),
        "validation_missing_dimension_count": len(missing_dimensions),
        "validation_backtest_pass_count": pass_counts["backtest"],
        "validation_walk_forward_pass_count": pass_counts["walk_forward"],
        "validation_out_of_sample_pass_count": pass_counts["out_of_sample"],
        "validation_live_shadow_pass_count": pass_counts["live_shadow"],
        "validation_backtest_fail_count": fail_counts["backtest"],
        "validation_walk_forward_fail_count": fail_counts["walk_forward"],
        "validation_out_of_sample_fail_count": fail_counts["out_of_sample"],
        "validation_live_shadow_fail_count": fail_counts["live_shadow"],
        "validation_missing_dimensions": missing_dimensions,
        "validation_failed_dimensions": failed_dimensions,
        "scale_blocked_by_validation_evidence": (
            sample_count >= min_samples_for_grade and status != "validated"
        ),
    }


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += float(value)
        peak = max(peak, cumulative)
        drawdown = cumulative - peak
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def _edge_risk_profile(
    *,
    sample_count: int,
    min_samples_for_grade: int,
    pnl_values: list[float],
    win_rate: float,
    expectancy: float,
    rule_follow_rate: float,
    execution_error_rate: float,
    max_drawdown_pct: float,
    profit_factor: float,
    recovery_factor: float,
    cost_drag_pct: float,
) -> dict[str, float]:
    min_samples = max(int(min_samples_for_grade or 1), 1)
    sample_confidence = _clamp(sample_count / min_samples, 0.0, 1.0)
    pf_quality = _clamp(profit_factor / 2.0, 0.0, 1.0) if profit_factor < 900 else 1.0
    recovery_quality = (
        _clamp(recovery_factor / 2.0, 0.0, 1.0)
        if recovery_factor < 900
        else 1.0
    )
    rule_quality = _clamp(rule_follow_rate / 100.0, 0.0, 1.0)
    execution_quality = 1.0 - _clamp(execution_error_rate / 100.0, 0.0, 1.0)
    drawdown_quality = 1.0 - _clamp(abs(max_drawdown_pct) / 10.0, 0.0, 1.0)
    lane_confidence = (
        sample_confidence * 0.30
        + rule_quality * 0.20
        + execution_quality * 0.15
        + pf_quality * 0.15
        + recovery_quality * 0.10
        + drawdown_quality * 0.10
    )
    wins = [value for value in pnl_values if value > 0]
    losses = [abs(value) for value in pnl_values if value < 0]
    win_probability = _clamp(win_rate / 100.0, 0.0, 1.0)
    loss_probability = 1.0 - win_probability
    avg_win = _avg(wins)
    avg_loss = _avg(losses)
    payoff_ratio = avg_win / avg_loss if avg_win > 0 and avg_loss > 0 else 0.0
    raw_kelly = (
        max(win_probability - loss_probability / payoff_ratio, 0.0)
        if payoff_ratio > 0
        else 0.0
    )
    fractional_kelly = raw_kelly * EDGE_FRACTIONAL_KELLY

    pf_penalty = 1.0 if profit_factor <= 0 else _clamp((1.5 - profit_factor) / 1.5, 0.0, 1.0)
    expectancy_penalty = (
        1.0
        if expectancy <= 0
        else _clamp((0.5 - expectancy) / 0.5, 0.0, 1.0)
    )
    drawdown_penalty = _clamp(abs(max_drawdown_pct) / 10.0, 0.0, 1.0)
    cost_penalty = _clamp(cost_drag_pct / 100.0, 0.0, 1.0)
    error_penalty = _clamp(execution_error_rate / 30.0, 0.0, 1.0)
    confidence_gap = 1.0 - lane_confidence
    risk_of_ruin_pct = 100.0 * (
        confidence_gap * 0.25
        + pf_penalty * 0.20
        + expectancy_penalty * 0.20
        + drawdown_penalty * 0.15
        + cost_penalty * 0.10
        + error_penalty * 0.10
    )
    if (
        sample_confidence >= 1.0
        and profit_factor >= 2.0
        and expectancy >= 0.4
        and win_rate >= 55.0
        and max_drawdown_pct > -2.0
        and execution_error_rate <= 5.0
    ):
        risk_of_ruin_pct *= 0.35
    risk_of_ruin_pct = _clamp(risk_of_ruin_pct, 0.0, 100.0)

    if max_drawdown_pct <= -7.0:
        drawdown_cap = 0.50
    elif max_drawdown_pct <= -4.0:
        drawdown_cap = 0.75
    else:
        drawdown_cap = 1.0
    if risk_of_ruin_pct >= 20.0:
        ruin_cap = 0.25
    elif risk_of_ruin_pct >= 10.0:
        ruin_cap = 0.50
    elif risk_of_ruin_pct >= 5.0:
        ruin_cap = 0.75
    else:
        ruin_cap = 1.0
    max_risk_cap_fraction = EDGE_REFERENCE_RISK_FRACTION * min(
        sample_confidence,
        drawdown_cap,
        ruin_cap,
    )
    if expectancy <= 0 or profit_factor < 1.0 or sample_count <= 0:
        recommended = 0.0
    elif sample_count < min_samples:
        recommended = min(
            fractional_kelly,
            EDGE_REFERENCE_RISK_FRACTION * 0.25,
            max_risk_cap_fraction,
        )
    else:
        recommended = min(
            fractional_kelly,
            max_risk_cap_fraction,
            EDGE_REFERENCE_RISK_FRACTION * lane_confidence,
        )

    return {
        "lane_confidence_score": round(_clamp(lane_confidence, 0.0, 1.0), 6),
        "risk_of_ruin_pct": round(risk_of_ruin_pct, 6),
        "raw_kelly_fraction": round(raw_kelly, 8),
        "fractional_kelly_fraction": round(fractional_kelly, 8),
        "recommended_risk_fraction": round(max(recommended, 0.0), 8),
        "max_risk_cap_fraction": round(max(max_risk_cap_fraction, 0.0), 8),
    }


def compute_edge_scorecard(
    outcomes: list[EvidenceOutcome],
    *,
    min_samples_for_grade: int = 5,
) -> dict[str, Any]:
    sample_count = len(outcomes)
    pnl_values = [float(item.net_pnl_pct) for item in outcomes]
    wins = [item for item in outcomes if item.net_pnl_pct > 0]
    rule_follow = [item for item in outcomes if item.rule_followed]
    execution_errors = [item for item in outcomes if item.execution_error]
    expectancy = _avg(pnl_values)
    win_rate = (len(wins) / sample_count * 100.0) if sample_count else 0.0
    rule_follow_rate = (
        len(rule_follow) / sample_count * 100.0 if sample_count else 0.0
    )
    execution_error_rate = (
        len(execution_errors) / sample_count * 100.0 if sample_count else 0.0
    )
    total_gain_pct = sum(value for value in pnl_values if value > 0)
    total_loss_pct = sum(value for value in pnl_values if value < 0)
    cumulative_return_pct = sum(pnl_values)
    total_gross_pnl = sum(float(item.gross_pnl or 0.0) for item in outcomes)
    total_cost = sum(float(item.cost_total or 0.0) for item in outcomes)
    recorded_cost_count = sum(
        1 for item in outcomes if _cost_precision_bucket(item.cost_precision) == "recorded"
    )
    hybrid_cost_count = sum(
        1 for item in outcomes if _cost_precision_bucket(item.cost_precision) == "hybrid"
    )
    estimated_cost_count = sum(
        1 for item in outcomes if _cost_precision_bucket(item.cost_precision) == "estimated"
    )
    missing_cost_count = (
        sample_count - recorded_cost_count - hybrid_cost_count - estimated_cost_count
    )
    cost_verified_outcomes = [
        item
        for item in outcomes
        if _cost_precision_bucket(item.cost_precision) == "recorded"
    ]
    cost_hybrid_outcomes = [
        item
        for item in outcomes
        if _cost_precision_bucket(item.cost_precision) == "hybrid"
    ]
    cost_unverified_outcomes = [
        item
        for item in outcomes
        if _cost_precision_bucket(item.cost_precision) != "recorded"
    ]
    revision_counts: dict[str, int] = {}
    for item in outcomes:
        revision_id = str(item.strategy_revision_id or "").strip()
        if not revision_id:
            revision_id = "legacy"
        revision_counts[revision_id] = revision_counts.get(revision_id, 0) + 1
    cost_precision_verified_rate = (
        recorded_cost_count / sample_count * 100.0 if sample_count else 0.0
    )
    entry_quality_samples = [
        float(item.entry_quality_score or 0.0)
        for item in outcomes
        if float(item.entry_quality_score or 0.0) > 0
    ]
    entry_quality_sample_count = len(entry_quality_samples)
    avg_entry_quality_score = _avg(entry_quality_samples)
    low_entry_quality_sample_count = sum(
        1 for value in entry_quality_samples if value < 60.0
    )
    bad_entry_quality_rate_pct = (
        low_entry_quality_sample_count / entry_quality_sample_count * 100.0
        if entry_quality_sample_count
        else 0.0
    )
    gross_abs = abs(total_gross_pnl)
    if gross_abs > 0:
        cost_drag_pct = total_cost / gross_abs * 100.0
    elif total_cost > 0:
        cost_drag_pct = 999.0
    else:
        cost_drag_pct = 0.0
    profit_factor = (
        total_gain_pct / abs(total_loss_pct)
        if total_loss_pct < 0
        else 999.0
        if total_gain_pct > 0
        else 0.0
    )
    max_drawdown_pct = _max_drawdown(pnl_values)
    recovery_factor = (
        cumulative_return_pct / abs(max_drawdown_pct)
        if max_drawdown_pct < 0
        else 999.0
        if cumulative_return_pct > 0
        else 0.0
    )
    validation_evidence = _validation_evidence_profile(
        outcomes,
        min_samples_for_grade=min_samples_for_grade,
    )
    base = {
        "sample_count": sample_count,
        "expectancy_pct": expectancy,
        "win_rate": win_rate,
        "rule_follow_rate": rule_follow_rate,
        "execution_error_rate": execution_error_rate,
        "max_drawdown_pct": max_drawdown_pct,
        "profit_factor": profit_factor,
        "recovery_factor": recovery_factor,
        "total_gain_pct": total_gain_pct,
        "total_loss_pct": total_loss_pct,
        "cumulative_return_pct": cumulative_return_pct,
        "total_gross_pnl": total_gross_pnl,
        "total_cost": total_cost,
        "cost_drag_pct_of_gross_pnl": cost_drag_pct,
        "recorded_cost_sample_count": recorded_cost_count,
        "hybrid_cost_sample_count": hybrid_cost_count,
        "estimated_cost_sample_count": estimated_cost_count,
        "missing_cost_sample_count": missing_cost_count,
        "cost_precision_verified_rate": cost_precision_verified_rate,
        "cost_verified_alpha_count": len(cost_verified_outcomes),
        "cost_hybrid_alpha_count": len(cost_hybrid_outcomes),
        "cost_unverified_alpha_count": len(cost_unverified_outcomes),
        "cost_verified_alpha_net_pnl": round(
            sum(float(item.net_pnl_pct) for item in cost_verified_outcomes),
            8,
        ),
        "cost_hybrid_alpha_net_pnl": round(
            sum(float(item.net_pnl_pct) for item in cost_hybrid_outcomes),
            8,
        ),
        "cost_unverified_alpha_net_pnl": round(
            sum(float(item.net_pnl_pct) for item in cost_unverified_outcomes),
            8,
        ),
        "entry_quality_sample_count": entry_quality_sample_count,
        "avg_entry_quality_score": avg_entry_quality_score,
        "low_entry_quality_sample_count": low_entry_quality_sample_count,
        "bad_entry_quality_rate_pct": bad_entry_quality_rate_pct,
        "strategy_revision_id": (
            max(revision_counts.items(), key=lambda item: item[1])[0]
            if revision_counts
            else ""
        ),
        "strategy_revision_counts": revision_counts,
    }
    base.update(validation_evidence)
    base.update(
        _edge_risk_profile(
            sample_count=sample_count,
            min_samples_for_grade=min_samples_for_grade,
            pnl_values=pnl_values,
            win_rate=win_rate,
            expectancy=expectancy,
            rule_follow_rate=rule_follow_rate,
            execution_error_rate=execution_error_rate,
            max_drawdown_pct=max_drawdown_pct,
            profit_factor=profit_factor,
            recovery_factor=recovery_factor,
            cost_drag_pct=cost_drag_pct,
        )
    )
    return {
        **base,
        **live_grade_from_scorecard(
            base,
            min_samples_for_grade=min_samples_for_grade,
        ),
    }


def live_grade_from_scorecard(
    scorecard: dict[str, Any],
    *,
    min_samples_for_grade: int = 5,
) -> dict[str, Any]:
    sample_count = int(scorecard.get("sample_count") or 0)
    expectancy = float(scorecard.get("expectancy_pct") or 0.0)
    win_rate = float(scorecard.get("win_rate") or 0.0)
    drawdown = float(scorecard.get("max_drawdown_pct") or 0.0)
    rule_follow = float(scorecard.get("rule_follow_rate") or 0.0)
    execution_errors = float(scorecard.get("execution_error_rate") or 0.0)
    profit_factor = float(scorecard.get("profit_factor") or 0.0)
    recovery_factor = float(scorecard.get("recovery_factor") or 0.0)
    cost_drag = float(scorecard.get("cost_drag_pct_of_gross_pnl") or 0.0)
    cost_precision_verified_rate = float(
        scorecard.get("cost_precision_verified_rate")
        if scorecard.get("cost_precision_verified_rate") is not None
        else 100.0
    )
    entry_quality_sample_count = int(scorecard.get("entry_quality_sample_count") or 0)
    avg_entry_quality_score = float(scorecard.get("avg_entry_quality_score") or 0.0)
    bad_entry_quality_rate = float(scorecard.get("bad_entry_quality_rate_pct") or 0.0)
    validation_evidence_status = str(
        scorecard.get("validation_evidence_status") or ""
    ).strip()
    scale_blocked_by_validation_evidence = bool(
        scorecard.get("scale_blocked_by_validation_evidence")
    )
    scale_blocked_by_cost_precision = (
        sample_count >= min_samples_for_grade
        and cost_precision_verified_rate < 60.0
    )
    raw_verified_alpha_count = scorecard.get("cost_verified_alpha_count")
    has_verified_alpha_count = raw_verified_alpha_count not in (None, "", [], {})
    verified_alpha_count = int(raw_verified_alpha_count or 0)
    scale_blocked_by_verified_edge_samples = (
        sample_count >= min_samples_for_grade
        and has_verified_alpha_count
        and verified_alpha_count < min_samples_for_grade
    )
    entry_quality_weak = (
        entry_quality_sample_count >= min_samples_for_grade
        and (avg_entry_quality_score < 55.0 or bad_entry_quality_rate >= 50.0)
    )

    if sample_count < min_samples_for_grade:
        return {"grade": "insufficient", "authority_multiplier": 0.75}
    if (
        execution_errors >= 15.0
        or rule_follow < 60.0
        or drawdown <= -7.0
        or cost_drag >= 60.0
        or entry_quality_weak
    ):
        payload: dict[str, Any] = {"grade": "restricted", "authority_multiplier": 0.5}
        if entry_quality_weak:
            payload["scale_blocked_by_entry_quality"] = True
        return payload
    if (
        expectancy > 0.4
        and win_rate >= 52.0
        and rule_follow >= 80.0
        and profit_factor >= 1.5
        and recovery_factor >= 1.0
        and cost_drag <= 35.0
        and (entry_quality_sample_count <= 0 or avg_entry_quality_score >= 65.0)
    ):
        if scale_blocked_by_cost_precision or scale_blocked_by_verified_edge_samples:
            return {
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "scale_blocked_by_cost_precision": scale_blocked_by_cost_precision,
                "scale_blocked_by_cost_evidence": True,
                "scale_blocked_by_verified_edge_samples": (
                    scale_blocked_by_verified_edge_samples
                ),
            }
        if scale_blocked_by_validation_evidence:
            return {
                "grade": "qualified",
                "authority_multiplier": 1.0,
                "scale_blocked_by_validation_evidence": True,
                "validation_evidence_status": validation_evidence_status,
            }
        return {"grade": "scale_candidate", "authority_multiplier": 1.25}
    if (
        expectancy > 0.0
        and win_rate >= 48.0
        and profit_factor >= 1.05
        and recovery_factor >= 0.5
        and cost_drag <= 55.0
    ):
        return {"grade": "qualified", "authority_multiplier": 1.0}
    return {"grade": "observe_only", "authority_multiplier": 0.5}


class LiveEdgeRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_edge_scorecards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT NOT NULL,
                    strategy_family TEXT NOT NULL,
                    evidence_key TEXT NOT NULL DEFAULT '',
                    strategy_revision_id TEXT NOT NULL DEFAULT '',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    expectancy_pct REAL NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    rule_follow_rate REAL NOT NULL DEFAULT 0,
                    execution_error_rate REAL NOT NULL DEFAULT 0,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0,
                    profit_factor REAL NOT NULL DEFAULT 0,
                    recovery_factor REAL NOT NULL DEFAULT 0,
                    cumulative_return_pct REAL NOT NULL DEFAULT 0,
                    total_gross_pnl REAL NOT NULL DEFAULT 0,
                    total_cost REAL NOT NULL DEFAULT 0,
                    cost_drag_pct_of_gross_pnl REAL NOT NULL DEFAULT 0,
                    recorded_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                    hybrid_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                    missing_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                    cost_precision_verified_rate REAL NOT NULL DEFAULT 0,
                    scale_blocked_by_cost_precision INTEGER NOT NULL DEFAULT 0,
                    scale_blocked_by_cost_evidence INTEGER NOT NULL DEFAULT 0,
                    cost_verified_alpha_count INTEGER NOT NULL DEFAULT 0,
                    cost_hybrid_alpha_count INTEGER NOT NULL DEFAULT 0,
                    cost_unverified_alpha_count INTEGER NOT NULL DEFAULT 0,
                    cost_verified_alpha_net_pnl REAL NOT NULL DEFAULT 0,
                    cost_hybrid_alpha_net_pnl REAL NOT NULL DEFAULT 0,
                    cost_unverified_alpha_net_pnl REAL NOT NULL DEFAULT 0,
                    scale_blocked_by_verified_edge_samples INTEGER NOT NULL DEFAULT 0,
                    entry_quality_sample_count INTEGER NOT NULL DEFAULT 0,
                    avg_entry_quality_score REAL NOT NULL DEFAULT 0,
                    low_entry_quality_sample_count INTEGER NOT NULL DEFAULT 0,
                    bad_entry_quality_rate_pct REAL NOT NULL DEFAULT 0,
                    scale_blocked_by_entry_quality INTEGER NOT NULL DEFAULT 0,
                    lane_confidence_score REAL NOT NULL DEFAULT 0,
                    risk_of_ruin_pct REAL NOT NULL DEFAULT 100,
                    raw_kelly_fraction REAL NOT NULL DEFAULT 0,
                    fractional_kelly_fraction REAL NOT NULL DEFAULT 0,
                    recommended_risk_fraction REAL NOT NULL DEFAULT 0,
                    max_risk_cap_fraction REAL NOT NULL DEFAULT 0,
                    grade TEXT NOT NULL DEFAULT 'insufficient',
                    authority_multiplier REAL NOT NULL DEFAULT 1,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL,
                    UNIQUE(venue, strategy_family, evidence_key, strategy_revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_live_edge_venue_grade
                    ON live_edge_scorecards(venue, grade, computed_at DESC);
                """
            )
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(live_edge_scorecards)")
        }
        column_types = {
            "strategy_revision_id": "TEXT NOT NULL DEFAULT ''",
            "profit_factor": "REAL NOT NULL DEFAULT 0",
            "recovery_factor": "REAL NOT NULL DEFAULT 0",
            "cumulative_return_pct": "REAL NOT NULL DEFAULT 0",
            "total_gross_pnl": "REAL NOT NULL DEFAULT 0",
            "total_cost": "REAL NOT NULL DEFAULT 0",
            "cost_drag_pct_of_gross_pnl": "REAL NOT NULL DEFAULT 0",
            "recorded_cost_sample_count": "INTEGER NOT NULL DEFAULT 0",
            "hybrid_cost_sample_count": "INTEGER NOT NULL DEFAULT 0",
            "estimated_cost_sample_count": "INTEGER NOT NULL DEFAULT 0",
            "missing_cost_sample_count": "INTEGER NOT NULL DEFAULT 0",
            "cost_precision_verified_rate": "REAL NOT NULL DEFAULT 0",
            "scale_blocked_by_cost_precision": "INTEGER NOT NULL DEFAULT 0",
            "scale_blocked_by_cost_evidence": "INTEGER NOT NULL DEFAULT 0",
            "cost_verified_alpha_count": "INTEGER NOT NULL DEFAULT 0",
            "cost_hybrid_alpha_count": "INTEGER NOT NULL DEFAULT 0",
            "cost_unverified_alpha_count": "INTEGER NOT NULL DEFAULT 0",
            "cost_verified_alpha_net_pnl": "REAL NOT NULL DEFAULT 0",
            "cost_hybrid_alpha_net_pnl": "REAL NOT NULL DEFAULT 0",
            "cost_unverified_alpha_net_pnl": "REAL NOT NULL DEFAULT 0",
            "scale_blocked_by_verified_edge_samples": "INTEGER NOT NULL DEFAULT 0",
            "entry_quality_sample_count": "INTEGER NOT NULL DEFAULT 0",
            "avg_entry_quality_score": "REAL NOT NULL DEFAULT 0",
            "low_entry_quality_sample_count": "INTEGER NOT NULL DEFAULT 0",
            "bad_entry_quality_rate_pct": "REAL NOT NULL DEFAULT 0",
            "scale_blocked_by_entry_quality": "INTEGER NOT NULL DEFAULT 0",
            "lane_confidence_score": "REAL NOT NULL DEFAULT 0",
            "risk_of_ruin_pct": "REAL NOT NULL DEFAULT 0",
            "raw_kelly_fraction": "REAL NOT NULL DEFAULT 0",
            "fractional_kelly_fraction": "REAL NOT NULL DEFAULT 0",
            "recommended_risk_fraction": "REAL NOT NULL DEFAULT 0",
            "max_risk_cap_fraction": "REAL NOT NULL DEFAULT 0",
        }
        for column, column_type in column_types.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE live_edge_scorecards "
                    f"ADD COLUMN {column} {column_type}"
                )
        if not LiveEdgeRepository._has_revision_unique_index(conn):
            LiveEdgeRepository._rebuild_with_revision_unique(conn)

    @staticmethod
    def _has_revision_unique_index(conn: sqlite3.Connection) -> bool:
        expected = [
            "venue",
            "strategy_family",
            "evidence_key",
            "strategy_revision_id",
        ]
        for row in conn.execute("PRAGMA index_list(live_edge_scorecards)"):
            if not int(row["unique"] or 0):
                continue
            index_name = str(row["name"])
            columns = [
                str(index_row["name"])
                for index_row in conn.execute(f"PRAGMA index_info({index_name})")
            ]
            if columns == expected:
                return True
        return False

    @staticmethod
    def _rebuild_with_revision_unique(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_edge_scorecards_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue TEXT NOT NULL,
                strategy_family TEXT NOT NULL,
                evidence_key TEXT NOT NULL DEFAULT '',
                strategy_revision_id TEXT NOT NULL DEFAULT '',
                sample_count INTEGER NOT NULL DEFAULT 0,
                expectancy_pct REAL NOT NULL DEFAULT 0,
                win_rate REAL NOT NULL DEFAULT 0,
                rule_follow_rate REAL NOT NULL DEFAULT 0,
                execution_error_rate REAL NOT NULL DEFAULT 0,
                max_drawdown_pct REAL NOT NULL DEFAULT 0,
                profit_factor REAL NOT NULL DEFAULT 0,
                recovery_factor REAL NOT NULL DEFAULT 0,
                cumulative_return_pct REAL NOT NULL DEFAULT 0,
                total_gross_pnl REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0,
                cost_drag_pct_of_gross_pnl REAL NOT NULL DEFAULT 0,
                recorded_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                hybrid_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                estimated_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                missing_cost_sample_count INTEGER NOT NULL DEFAULT 0,
                cost_precision_verified_rate REAL NOT NULL DEFAULT 0,
                scale_blocked_by_cost_precision INTEGER NOT NULL DEFAULT 0,
                scale_blocked_by_cost_evidence INTEGER NOT NULL DEFAULT 0,
                cost_verified_alpha_count INTEGER NOT NULL DEFAULT 0,
                cost_hybrid_alpha_count INTEGER NOT NULL DEFAULT 0,
                cost_unverified_alpha_count INTEGER NOT NULL DEFAULT 0,
                cost_verified_alpha_net_pnl REAL NOT NULL DEFAULT 0,
                cost_hybrid_alpha_net_pnl REAL NOT NULL DEFAULT 0,
                cost_unverified_alpha_net_pnl REAL NOT NULL DEFAULT 0,
                scale_blocked_by_verified_edge_samples INTEGER NOT NULL DEFAULT 0,
                entry_quality_sample_count INTEGER NOT NULL DEFAULT 0,
                avg_entry_quality_score REAL NOT NULL DEFAULT 0,
                low_entry_quality_sample_count INTEGER NOT NULL DEFAULT 0,
                bad_entry_quality_rate_pct REAL NOT NULL DEFAULT 0,
                scale_blocked_by_entry_quality INTEGER NOT NULL DEFAULT 0,
                lane_confidence_score REAL NOT NULL DEFAULT 0,
                risk_of_ruin_pct REAL NOT NULL DEFAULT 100,
                raw_kelly_fraction REAL NOT NULL DEFAULT 0,
                fractional_kelly_fraction REAL NOT NULL DEFAULT 0,
                recommended_risk_fraction REAL NOT NULL DEFAULT 0,
                max_risk_cap_fraction REAL NOT NULL DEFAULT 0,
                grade TEXT NOT NULL DEFAULT 'insufficient',
                authority_multiplier REAL NOT NULL DEFAULT 1,
                raw_json TEXT NOT NULL DEFAULT '{}',
                computed_at TEXT NOT NULL,
                UNIQUE(venue, strategy_family, evidence_key, strategy_revision_id)
            );
            INSERT OR REPLACE INTO live_edge_scorecards_new (
                id, venue, strategy_family, evidence_key, strategy_revision_id,
                sample_count, expectancy_pct, win_rate, rule_follow_rate,
                execution_error_rate, max_drawdown_pct, profit_factor,
                recovery_factor, cumulative_return_pct, total_gross_pnl,
                total_cost, cost_drag_pct_of_gross_pnl,
                recorded_cost_sample_count, hybrid_cost_sample_count,
                estimated_cost_sample_count,
                missing_cost_sample_count, cost_precision_verified_rate,
                scale_blocked_by_cost_precision,
                scale_blocked_by_cost_evidence,
                cost_verified_alpha_count, cost_hybrid_alpha_count,
                cost_unverified_alpha_count, cost_verified_alpha_net_pnl,
                cost_hybrid_alpha_net_pnl, cost_unverified_alpha_net_pnl,
                scale_blocked_by_verified_edge_samples,
                entry_quality_sample_count,
                avg_entry_quality_score, low_entry_quality_sample_count,
                bad_entry_quality_rate_pct, scale_blocked_by_entry_quality,
                lane_confidence_score, risk_of_ruin_pct, raw_kelly_fraction,
                fractional_kelly_fraction, recommended_risk_fraction,
                max_risk_cap_fraction, grade, authority_multiplier,
                raw_json, computed_at
            )
            SELECT
                id, venue, strategy_family, evidence_key,
                COALESCE(strategy_revision_id, ''),
                sample_count, expectancy_pct, win_rate, rule_follow_rate,
                execution_error_rate, max_drawdown_pct, profit_factor,
                recovery_factor, cumulative_return_pct, total_gross_pnl,
                total_cost, cost_drag_pct_of_gross_pnl,
                recorded_cost_sample_count,
                COALESCE(hybrid_cost_sample_count, 0),
                estimated_cost_sample_count,
                missing_cost_sample_count, cost_precision_verified_rate,
                scale_blocked_by_cost_precision,
                COALESCE(scale_blocked_by_cost_evidence, 0),
                COALESCE(cost_verified_alpha_count, 0),
                COALESCE(cost_hybrid_alpha_count, 0),
                COALESCE(cost_unverified_alpha_count, 0),
                COALESCE(cost_verified_alpha_net_pnl, 0),
                COALESCE(cost_hybrid_alpha_net_pnl, 0),
                COALESCE(cost_unverified_alpha_net_pnl, 0),
                COALESCE(scale_blocked_by_verified_edge_samples, 0),
                entry_quality_sample_count,
                avg_entry_quality_score, low_entry_quality_sample_count,
                bad_entry_quality_rate_pct, scale_blocked_by_entry_quality,
                lane_confidence_score, risk_of_ruin_pct, raw_kelly_fraction,
                fractional_kelly_fraction, recommended_risk_fraction,
                max_risk_cap_fraction, grade, authority_multiplier,
                raw_json, computed_at
            FROM live_edge_scorecards;
            DROP TABLE live_edge_scorecards;
            ALTER TABLE live_edge_scorecards_new RENAME TO live_edge_scorecards;
            CREATE INDEX IF NOT EXISTS idx_live_edge_venue_grade
                ON live_edge_scorecards(venue, grade, computed_at DESC);
            """
        )

    def upsert_scorecard(
        self,
        *,
        venue: str,
        strategy_family: str,
        evidence_key: str,
        scorecard: dict[str, Any],
    ) -> None:
        strategy_revision_id = str(scorecard.get("strategy_revision_id") or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_edge_scorecards (
                    venue, strategy_family, evidence_key, strategy_revision_id,
                    sample_count,
                    expectancy_pct, win_rate, rule_follow_rate,
                    execution_error_rate, max_drawdown_pct,
                    profit_factor, recovery_factor, cumulative_return_pct,
                    total_gross_pnl, total_cost, cost_drag_pct_of_gross_pnl,
                    recorded_cost_sample_count, hybrid_cost_sample_count,
                    estimated_cost_sample_count,
                    missing_cost_sample_count, cost_precision_verified_rate,
                    scale_blocked_by_cost_precision,
                    scale_blocked_by_cost_evidence,
                    cost_verified_alpha_count, cost_hybrid_alpha_count,
                    cost_unverified_alpha_count, cost_verified_alpha_net_pnl,
                    cost_hybrid_alpha_net_pnl, cost_unverified_alpha_net_pnl,
                    scale_blocked_by_verified_edge_samples,
                    entry_quality_sample_count, avg_entry_quality_score,
                    low_entry_quality_sample_count, bad_entry_quality_rate_pct,
                    scale_blocked_by_entry_quality,
                    lane_confidence_score, risk_of_ruin_pct, raw_kelly_fraction,
                    fractional_kelly_fraction, recommended_risk_fraction,
                    max_risk_cap_fraction,
                    grade,
                    authority_multiplier, raw_json, computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, strategy_family, evidence_key, strategy_revision_id) DO UPDATE SET
                    strategy_revision_id=excluded.strategy_revision_id,
                    sample_count=excluded.sample_count,
                    expectancy_pct=excluded.expectancy_pct,
                    win_rate=excluded.win_rate,
                    rule_follow_rate=excluded.rule_follow_rate,
                    execution_error_rate=excluded.execution_error_rate,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    profit_factor=excluded.profit_factor,
                    recovery_factor=excluded.recovery_factor,
                    cumulative_return_pct=excluded.cumulative_return_pct,
                    total_gross_pnl=excluded.total_gross_pnl,
                    total_cost=excluded.total_cost,
                    cost_drag_pct_of_gross_pnl=excluded.cost_drag_pct_of_gross_pnl,
                    recorded_cost_sample_count=excluded.recorded_cost_sample_count,
                    hybrid_cost_sample_count=excluded.hybrid_cost_sample_count,
                    estimated_cost_sample_count=excluded.estimated_cost_sample_count,
                    missing_cost_sample_count=excluded.missing_cost_sample_count,
                    cost_precision_verified_rate=excluded.cost_precision_verified_rate,
                    scale_blocked_by_cost_precision=excluded.scale_blocked_by_cost_precision,
                    scale_blocked_by_cost_evidence=excluded.scale_blocked_by_cost_evidence,
                    cost_verified_alpha_count=excluded.cost_verified_alpha_count,
                    cost_hybrid_alpha_count=excluded.cost_hybrid_alpha_count,
                    cost_unverified_alpha_count=excluded.cost_unverified_alpha_count,
                    cost_verified_alpha_net_pnl=excluded.cost_verified_alpha_net_pnl,
                    cost_hybrid_alpha_net_pnl=excluded.cost_hybrid_alpha_net_pnl,
                    cost_unverified_alpha_net_pnl=excluded.cost_unverified_alpha_net_pnl,
                    scale_blocked_by_verified_edge_samples=excluded.scale_blocked_by_verified_edge_samples,
                    entry_quality_sample_count=excluded.entry_quality_sample_count,
                    avg_entry_quality_score=excluded.avg_entry_quality_score,
                    low_entry_quality_sample_count=excluded.low_entry_quality_sample_count,
                    bad_entry_quality_rate_pct=excluded.bad_entry_quality_rate_pct,
                    scale_blocked_by_entry_quality=excluded.scale_blocked_by_entry_quality,
                    lane_confidence_score=excluded.lane_confidence_score,
                    risk_of_ruin_pct=excluded.risk_of_ruin_pct,
                    raw_kelly_fraction=excluded.raw_kelly_fraction,
                    fractional_kelly_fraction=excluded.fractional_kelly_fraction,
                    recommended_risk_fraction=excluded.recommended_risk_fraction,
                    max_risk_cap_fraction=excluded.max_risk_cap_fraction,
                    grade=excluded.grade,
                    authority_multiplier=excluded.authority_multiplier,
                    raw_json=excluded.raw_json,
                    computed_at=excluded.computed_at
                """,
                (
                    venue,
                    strategy_family,
                    evidence_key,
                    strategy_revision_id,
                    int(scorecard.get("sample_count") or 0),
                    float(scorecard.get("expectancy_pct") or 0.0),
                    float(scorecard.get("win_rate") or 0.0),
                    float(scorecard.get("rule_follow_rate") or 0.0),
                    float(scorecard.get("execution_error_rate") or 0.0),
                    float(scorecard.get("max_drawdown_pct") or 0.0),
                    float(scorecard.get("profit_factor") or 0.0),
                    float(scorecard.get("recovery_factor") or 0.0),
                    float(scorecard.get("cumulative_return_pct") or 0.0),
                    float(scorecard.get("total_gross_pnl") or 0.0),
                    float(scorecard.get("total_cost") or 0.0),
                    float(scorecard.get("cost_drag_pct_of_gross_pnl") or 0.0),
                    int(scorecard.get("recorded_cost_sample_count") or 0),
                    int(scorecard.get("hybrid_cost_sample_count") or 0),
                    int(scorecard.get("estimated_cost_sample_count") or 0),
                    int(scorecard.get("missing_cost_sample_count") or 0),
                    float(scorecard.get("cost_precision_verified_rate") or 0.0),
                    int(bool(scorecard.get("scale_blocked_by_cost_precision"))),
                    int(bool(scorecard.get("scale_blocked_by_cost_evidence"))),
                    int(scorecard.get("cost_verified_alpha_count") or 0),
                    int(scorecard.get("cost_hybrid_alpha_count") or 0),
                    int(scorecard.get("cost_unverified_alpha_count") or 0),
                    float(scorecard.get("cost_verified_alpha_net_pnl") or 0.0),
                    float(scorecard.get("cost_hybrid_alpha_net_pnl") or 0.0),
                    float(scorecard.get("cost_unverified_alpha_net_pnl") or 0.0),
                    int(bool(scorecard.get("scale_blocked_by_verified_edge_samples"))),
                    int(scorecard.get("entry_quality_sample_count") or 0),
                    float(scorecard.get("avg_entry_quality_score") or 0.0),
                    int(scorecard.get("low_entry_quality_sample_count") or 0),
                    float(scorecard.get("bad_entry_quality_rate_pct") or 0.0),
                    int(bool(scorecard.get("scale_blocked_by_entry_quality"))),
                    float(scorecard.get("lane_confidence_score") or 0.0),
                    float(scorecard.get("risk_of_ruin_pct") or 0.0),
                    float(scorecard.get("raw_kelly_fraction") or 0.0),
                    float(scorecard.get("fractional_kelly_fraction") or 0.0),
                    float(scorecard.get("recommended_risk_fraction") or 0.0),
                    float(scorecard.get("max_risk_cap_fraction") or 0.0),
                    str(scorecard.get("grade") or "insufficient"),
                    float(scorecard.get("authority_multiplier") or 1.0),
                    json.dumps(scorecard, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_scorecards(
        self,
        *,
        venue: str = "",
        strategy_revision_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 500), 1)
        params: list[Any] = []
        where_clauses: list[str] = []
        if venue:
            where_clauses.append("venue = ?")
            params.append(venue)
        if strategy_revision_id:
            where_clauses.append("strategy_revision_id = ?")
            params.append(strategy_revision_id)
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM live_edge_scorecards
                {where}
                ORDER BY computed_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        out: list[dict[str, Any]] = []
        bool_fields = {
            "scale_blocked_by_cost_precision",
            "scale_blocked_by_cost_evidence",
            "scale_blocked_by_verified_edge_samples",
            "scale_blocked_by_entry_quality",
            "scale_blocked_by_validation_evidence",
        }
        for row in rows:
            payload = dict(row)
            try:
                raw = json.loads(str(payload.get("raw_json") or "{}"))
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict):
                for key in (
                    "profit_factor",
                    "recovery_factor",
                    "total_gain_pct",
                    "total_loss_pct",
                    "cumulative_return_pct",
                    "total_gross_pnl",
                    "total_cost",
                    "cost_drag_pct_of_gross_pnl",
                    "recorded_cost_sample_count",
                    "hybrid_cost_sample_count",
                    "estimated_cost_sample_count",
                    "missing_cost_sample_count",
                    "cost_precision_verified_rate",
                    "scale_blocked_by_cost_precision",
                    "scale_blocked_by_cost_evidence",
                    "cost_verified_alpha_count",
                    "cost_hybrid_alpha_count",
                    "cost_unverified_alpha_count",
                    "cost_verified_alpha_net_pnl",
                    "cost_hybrid_alpha_net_pnl",
                    "cost_unverified_alpha_net_pnl",
                    "scale_blocked_by_verified_edge_samples",
                    "entry_quality_sample_count",
                    "avg_entry_quality_score",
                    "low_entry_quality_sample_count",
                    "bad_entry_quality_rate_pct",
                    "scale_blocked_by_entry_quality",
                    "lane_confidence_score",
                    "risk_of_ruin_pct",
                    "raw_kelly_fraction",
                    "fractional_kelly_fraction",
                    "recommended_risk_fraction",
                    "max_risk_cap_fraction",
                    "strategy_revision_id",
                    "strategy_revision_counts",
                    "validation_evidence_status",
                    "validation_evidence_sample_count",
                    "validation_passed_dimension_count",
                    "validation_failed_dimension_count",
                    "validation_missing_dimension_count",
                    "validation_backtest_pass_count",
                    "validation_walk_forward_pass_count",
                    "validation_out_of_sample_pass_count",
                    "validation_live_shadow_pass_count",
                    "validation_backtest_fail_count",
                    "validation_walk_forward_fail_count",
                    "validation_out_of_sample_fail_count",
                    "validation_live_shadow_fail_count",
                    "validation_missing_dimensions",
                    "validation_failed_dimensions",
                    "scale_blocked_by_validation_evidence",
                ):
                    if key in raw:
                        payload[key] = raw[key]
            for key in bool_fields:
                if key in payload:
                    payload[key] = bool(payload.get(key))
            out.append(payload)
        return out

    def delete_scorecards_not_in(
        self,
        *,
        venue: str,
        active_keys: set[tuple[str, str, str]],
    ) -> int:
        clean_venue = str(venue or "").strip()
        if not clean_venue:
            return 0
        deleted = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT strategy_family, evidence_key, strategy_revision_id
                FROM live_edge_scorecards
                WHERE venue = ?
                """,
                (clean_venue,),
            ).fetchall()
            for row in rows:
                key = (
                    str(row["strategy_family"]),
                    str(row["evidence_key"]),
                    str(row["strategy_revision_id"] or ""),
                )
                if key in active_keys:
                    continue
                conn.execute(
                    """
                    DELETE FROM live_edge_scorecards
                    WHERE venue = ?
                      AND strategy_family = ?
                      AND evidence_key = ?
                      AND strategy_revision_id = ?
                    """,
                    (clean_venue, key[0], key[1], key[2]),
                )
                deleted += 1
        return deleted

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS scorecard_count, MAX(computed_at) AS latest_at
                FROM live_edge_scorecards
                """
            ).fetchone()
        return {
            "status": "ok",
            "db_path": str(self.path),
            "scorecard_count": int((row or {})["scorecard_count"] or 0),
            "latest_at": (row or {})["latest_at"] or "",
        }
