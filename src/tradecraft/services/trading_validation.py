from __future__ import annotations

import json
import math
import random
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class TradingValidationConfig:
    validation_db_path: str | Path = ".runtime/trading_validation.db"
    live_performance_db_path: str | Path = ".runtime/live_performance.db"
    crypto_pattern_lab_db_path: str | Path = ".runtime/crypto_pattern_lab.db"
    kr_equity_pattern_lab_db_path: str | Path = ".runtime/kr_equity_pattern_lab.db"
    strategy_revision_id: str = ""
    initial_equity: float = 10_000.0
    min_sample_count: int = 30
    max_drawdown_limit_pct: float = 20.0
    profit_factor_good: float = 1.5
    profit_factor_min: float = 1.05
    sharpe_min: float = 0.5
    sortino_min: float = 0.75
    calmar_min: float = 0.5
    recovery_factor_min: float = 1.0
    monte_carlo_iterations: int = 500
    monte_carlo_seed: int = 42
    ruin_drawdown_pct: float = 30.0


DISCIPLINE_DEFINITIONS: list[dict[str, str]] = [
    {
        "id": "data_validation",
        "label": "데이터 검증",
        "purpose": "가격, 체결, 비용, 성과 데이터가 쥬 판단에 넣을 만큼 깨끗한지 확인",
    },
    {
        "id": "overfit_validation",
        "label": "과최적화 검증",
        "purpose": "특정 기간/파라미터에만 잘 맞는 전략인지 확인",
    },
    {
        "id": "walk_forward_analysis",
        "label": "Walk Forward Analysis",
        "purpose": "학습 구간 뒤 다음 구간에서도 성과가 유지되는지 반복 검증",
    },
    {
        "id": "out_of_sample_test",
        "label": "Out-of-sample Test",
        "purpose": "학습에 쓰지 않은 표본에서 전략이 살아남는지 확인",
    },
    {
        "id": "monte_carlo",
        "label": "몬테카를로 시뮬레이션",
        "purpose": "거래 순서와 결과 흔들림에도 계좌가 생존하는지 확인",
    },
    {
        "id": "stress_test",
        "label": "스트레스 테스트",
        "purpose": "폭락장, 급등락, 유동성 고갈 같은 위기 구간에서 견디는지 확인",
    },
    {
        "id": "cost_simulation",
        "label": "거래비용 시뮬레이션",
        "purpose": "수수료, 세금, 펀딩, 슬리피지, 스프레드를 성과에서 차감",
    },
    {
        "id": "capacity_analysis",
        "label": "용량 분석",
        "purpose": "시드가 커져도 같은 전략을 무리 없이 집행할 수 있는지 확인",
    },
    {
        "id": "kelly_sizing",
        "label": "켈리 공식",
        "purpose": "승률과 손익비 기반으로 적정 베팅 크기를 산출",
    },
    {
        "id": "mdd_limit",
        "label": "MDD 제한",
        "purpose": "최대 낙폭이 쥬 운용 한도 안에 있는지 확인",
    },
    {
        "id": "sharpe_ratio",
        "label": "샤프 비율",
        "purpose": "총 변동성 대비 수익 효율 확인",
    },
    {
        "id": "sortino_ratio",
        "label": "소르티노 비율",
        "purpose": "하방 변동성 대비 수익 효율 확인",
    },
    {
        "id": "calmar_ratio",
        "label": "Calmar Ratio",
        "purpose": "수익이 최대 낙폭 대비 충분한지 확인",
    },
    {
        "id": "profit_factor",
        "label": "수익팩터",
        "purpose": "총이익이 총손실보다 충분히 큰지 확인",
    },
    {
        "id": "recovery_factor",
        "label": "Recovery Factor",
        "purpose": "손실 이후 회복력이 충분한지 확인",
    },
    {
        "id": "risk_of_ruin",
        "label": "파산확률",
        "purpose": "현재 베팅 구조에서 계좌가 치명적으로 훼손될 확률 확인",
    },
    {
        "id": "regime_test",
        "label": "Regime Test",
        "purpose": "상승장, 하락장, 횡보장, 순환매 장세별 성과 확인",
    },
    {
        "id": "correlation",
        "label": "상관관계",
        "purpose": "동시에 같은 방향으로 무너질 포지션이 몰렸는지 확인",
    },
    {
        "id": "factor_exposure",
        "label": "팩터 익스포저",
        "purpose": "가치, 성장, 모멘텀, 퀄리티, 섹터, 베타 노출 확인",
    },
]

OPERATIONAL_READINESS_DISCIPLINE_IDS: tuple[str, ...] = (
    "data_validation",
    "capacity_analysis",
    "mdd_limit",
)
BAD_ENTRY_QUALITY_TOKENS: tuple[str, ...] = (
    "chase",
    "extended",
    "failed_breakout",
    "high_chase",
    "late_chase",
    "overextended",
    "고점",
    "추격",
)
KIS_ETF_NAME_PREFIXES: tuple[str, ...] = (
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
    "KINDEX",
    "TREX",
    "FOCUS",
    "KOACT",
)
LANE_KELLY_FRACTION = 0.25
LANE_KELLY_REFERENCE_RISK_FRACTION = 0.02
LANE_COST_PRECISION_VERIFIED_MIN_PCT = 60.0
GOOD_ENTRY_QUALITY_TOKENS: tuple[str, ...] = (
    "pullback",
    "pullback_reclaim",
    "reclaim",
    "support",
    "undervalued",
    "value_pullback",
    "wait_for_price",
    "wait_pullback",
    "눌림",
    "저점",
)
PATTERN_REPAIR_GUIDANCE: dict[str, dict[str, str]] = {
    "out_of_sample_expectancy_negative": {
        "focus": "oos_expectancy",
        "action": (
            "Require positive holdout/live-shadow expectancy after costs before "
            "treating the family as a scalable edge."
        ),
    },
    "out_of_sample_profit_factor_low": {
        "focus": "oos_profit_factor",
        "action": (
            "Increase target-to-cost room or move the setup to patient waiting "
            "entry; reject thin targets that cannot survive costs."
        ),
    },
    "walk_forward_pass_rate_low": {
        "focus": "walk_forward",
        "action": (
            "Split by regime, horizon, and trigger; keep the family probe-only "
            "until multiple forward windows pass."
        ),
    },
    "train_test_gap_large": {
        "focus": "overfit_gap",
        "action": (
            "Apply a multiple-testing penalty and prefer simpler conditions "
            "with smaller train/test expectancy gaps."
        ),
    },
    "walk_forward_windows_missing": {
        "focus": "missing_wfa",
        "action": "Collect dated samples and rebuild rolling WFA windows.",
    },
    "out_of_sample_missing": {
        "focus": "missing_oos",
        "action": "Create a chronological holdout split before scale-up.",
    },
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _avg(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def _pearson_correlation(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size < 3:
        return 0.0
    left_values = left[:size]
    right_values = right[:size]
    left_mean = _avg(left_values)
    right_mean = _avg(right_values)
    numerator = sum(
        (left_values[index] - left_mean) * (right_values[index] - right_mean)
        for index in range(size)
    )
    left_var = sum((value - left_mean) ** 2 for value in left_values)
    right_var = sum((value - right_mean) ** 2 for value in right_values)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator > 0 else 0.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(min(float(pct), 100.0), 0.0) / 100.0 * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    peak = equity_curve[0] if equity_curve else 0.0
    max_drawdown_cash = 0.0
    max_drawdown_pct = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak <= 0:
            continue
        drawdown_cash = value - peak
        drawdown_pct = drawdown_cash / peak * 100.0
        if drawdown_pct < max_drawdown_pct:
            max_drawdown_pct = drawdown_pct
            max_drawdown_cash = drawdown_cash
    return max_drawdown_cash, max_drawdown_pct


def _expected_shortfall(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    threshold = _percentile(values, pct)
    tail = [value for value in values if value <= threshold]
    return _avg(tail) if tail else threshold


def _max_consecutive_losses(values: list[float]) -> int:
    max_streak = 0
    current = 0
    for value in values:
        if value < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _first_drawdown_breach_index(
    equity_curve: list[float],
    threshold_pct: float,
) -> int:
    running_peak = equity_curve[0] if equity_curve else 0.0
    threshold = -abs(float(threshold_pct))
    for index, value in enumerate(equity_curve):
        running_peak = max(running_peak, value)
        if index == 0 or running_peak <= 0:
            continue
        drawdown_pct = (value - running_peak) / running_peak * 100.0
        if drawdown_pct <= threshold:
            return index
    return 0


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _nested_dict(source: dict[str, Any], *path: str) -> dict[str, Any]:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _first_optional_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"pass", "passed", "ok", "true", "yes", "1", "validated"}:
            return True
        if text in {"fail", "failed", "error", "false", "no", "0", "missing"}:
            return False
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
    validation = _nested_dict(metadata, "validation_evidence")
    explicit = _first_optional_bool(
        metadata.get("out_of_sample_passed"),
        metadata.get("oos_passed"),
        validation.get("out_of_sample_passed"),
        validation.get("out_of_sample"),
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
    prior = _nested_dict(metadata, "pattern_prior") or _nested_dict(
        metadata,
        "pattern_inputs",
        "prior",
    )
    walk_forward_quality = _nested_dict(metadata, "walk_forward_quality") or _nested_dict(
        prior,
        "walk_forward_quality",
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


def _lane_validation_evidence_profile(
    rows: list[dict[str, Any]],
    *,
    metadata_by_id: dict[int, dict[str, Any]],
    min_samples_for_grade: int,
) -> dict[str, Any]:
    checks = {
        "backtest": "backtest_passed",
        "walk_forward": "walk_forward_passed",
        "out_of_sample": "out_of_sample_passed",
        "live_shadow": "live_shadow_passed",
    }
    pass_counts = dict.fromkeys(checks, 0)
    fail_counts = dict.fromkeys(checks, 0)
    missing_counts = dict.fromkeys(checks, 0)
    evidence_sample_count = 0
    for row in rows:
        evidence = _scale_validation_evidence_from_metadata(
            metadata_by_id.get(id(row), {})
        )
        if any(value is not None for value in evidence.values()):
            evidence_sample_count += 1
        for label, attr in checks.items():
            value = evidence.get(attr)
            if value is True:
                pass_counts[label] += 1
            elif value is False:
                fail_counts[label] += 1
            else:
                missing_counts[label] += 1

    failed_dimensions = [
        label for label, count in fail_counts.items() if count > 0
    ]
    missing_dimensions = [
        label
        for label, count in pass_counts.items()
        if count <= 0 and fail_counts[label] <= 0
    ]
    thin_dimensions = [
        label
        for label, count in pass_counts.items()
        if 0 < count < max(int(min_samples_for_grade or 1), 1)
        and fail_counts[label] <= 0
    ]
    passed_dimensions = [
        label
        for label, count in pass_counts.items()
        if count >= max(int(min_samples_for_grade or 1), 1)
        and fail_counts[label] <= 0
    ]
    sample_count = len(rows)
    min_samples = max(int(min_samples_for_grade or 1), 1)
    evidence_coverage_rate = (
        evidence_sample_count / sample_count * 100.0 if sample_count else 0.0
    )
    if sample_count < min_samples:
        status = "not_required_until_min_samples"
    elif failed_dimensions:
        status = "failed"
    elif len(passed_dimensions) == len(checks):
        status = "validated"
    elif thin_dimensions:
        status = "thin"
    elif evidence_sample_count > 0:
        status = "partial"
    else:
        status = "missing"
    return {
        "validation_evidence_status": status,
        "validation_evidence_sample_count": evidence_sample_count,
        "validation_evidence_coverage_rate_pct": round(evidence_coverage_rate, 6),
        "validation_passed_dimension_count": len(passed_dimensions),
        "validation_failed_dimension_count": len(failed_dimensions),
        "validation_missing_dimension_count": len(missing_dimensions),
        "validation_thin_dimension_count": len(thin_dimensions),
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
        "validation_thin_dimensions": thin_dimensions,
        "scale_blocked_by_validation_evidence": (
            sample_count >= min_samples and status != "validated"
        ),
    }


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(min(float(value), high), low)


def _raw_kelly_fraction_from_lane(
    *,
    win_rate_pct: float,
    profit_factor: float,
) -> float:
    if win_rate_pct <= 0.0 or profit_factor <= 0.0:
        return 0.0
    win_probability = _clamp_float(win_rate_pct / 100.0, 0.0, 1.0)
    loss_probability = max(1.0 - win_probability, 0.0)
    if win_probability <= 0.0:
        return 0.0
    if loss_probability <= 0.0:
        return win_probability if profit_factor >= 1.0 else 0.0
    payoff_ratio = profit_factor * loss_probability / win_probability
    if payoff_ratio <= 0.0:
        return 0.0
    return max(win_probability - (loss_probability / payoff_ratio), 0.0)


def _lane_risk_budget_summary(
    *,
    sample_count: int,
    min_samples: int,
    expectancy_pct: float,
    win_rate_pct: float,
    profit_factor: float,
    max_drawdown_pct: float,
    recovery_factor: float,
    risk_of_ruin_pct: float,
    cost_precision_verified_rate: float,
    cost_evidence_weak: bool,
    entry_quality_sample_count: int,
    avg_entry_quality_score: float,
    entry_quality_weak: bool,
    validation_evidence_profile: dict[str, Any],
    validation_evidence_weak: bool,
    validation_repair_weak: bool,
    validation_repair_avg_budget_multiplier: float,
) -> dict[str, Any]:
    min_sample_count = max(int(min_samples or 1), 1)
    sample_confidence = _clamp_float(sample_count / min_sample_count, 0.0, 1.0)
    sample_cap = 1.0 if sample_count >= min_sample_count else max(sample_confidence, 0.25)

    raw_kelly = _raw_kelly_fraction_from_lane(
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
    )
    fractional_kelly = raw_kelly * LANE_KELLY_FRACTION
    kelly_cap = (
        min(fractional_kelly / LANE_KELLY_REFERENCE_RISK_FRACTION, 1.25)
        if fractional_kelly > 0
        else 0.25
    )

    if max_drawdown_pct <= -12.0:
        drawdown_cap = 0.25
    elif max_drawdown_pct <= -7.0:
        drawdown_cap = 0.5
    elif max_drawdown_pct <= -4.0:
        drawdown_cap = 0.75
    else:
        drawdown_cap = 1.0

    if sample_count >= min_sample_count and recovery_factor <= 0.0:
        recovery_cap = 0.25
    elif sample_count >= min_sample_count and recovery_factor < 0.5:
        recovery_cap = 0.5
    elif sample_count >= min_sample_count and recovery_factor < 1.0:
        recovery_cap = 0.75
    else:
        recovery_cap = 1.0

    if risk_of_ruin_pct >= 20.0:
        ruin_cap = 0.25
    elif risk_of_ruin_pct >= 10.0:
        ruin_cap = 0.5
    elif risk_of_ruin_pct >= 5.0:
        ruin_cap = 0.75
    else:
        ruin_cap = 1.0

    cost_precision_cap = 0.5 if cost_evidence_weak else 1.0
    entry_quality_cap = 0.5 if entry_quality_weak else 1.0
    validation_evidence_cap = 0.5 if validation_evidence_weak else 1.0
    validation_repair_cap = 1.0
    if validation_repair_weak:
        validation_repair_cap = (
            validation_repair_avg_budget_multiplier
            if 0.0 < validation_repair_avg_budget_multiplier < 1.0
            else 0.5
        )

    validation_pass_rate = _clamp_float(
        _safe_float(validation_evidence_profile.get("validation_passed_dimension_count"))
        / 4.0,
        0.0,
        1.0,
    )
    cost_confidence = _clamp_float(cost_precision_verified_rate / 100.0, 0.0, 1.0)
    entry_confidence = (
        _clamp_float(avg_entry_quality_score / 100.0, 0.0, 1.0)
        if entry_quality_sample_count > 0
        else sample_confidence
    )
    edge_confidence = _avg(
        [
            1.0 if expectancy_pct > 0.0 else 0.0,
            _clamp_float(win_rate_pct / 52.0, 0.0, 1.0),
            _clamp_float(profit_factor / 1.5, 0.0, 1.0),
            _clamp_float(recovery_factor / 1.0, 0.0, 1.0)
            if recovery_factor < 999.0
            else 1.0,
        ]
    )
    lane_confidence = _clamp_float(
        sample_confidence * 0.25
        + cost_confidence * 0.20
        + validation_pass_rate * 0.25
        + entry_confidence * 0.15
        + edge_confidence * 0.15,
        0.0,
        1.0,
    )
    lane_confidence_cap = 1.0 if lane_confidence >= 0.85 else max(lane_confidence, 0.25)

    cap_values = {
        "sample_cap": sample_cap,
        "kelly_cap": kelly_cap,
        "drawdown_cap": drawdown_cap,
        "recovery_cap": recovery_cap,
        "ruin_cap": ruin_cap,
        "cost_precision_cap": cost_precision_cap,
        "entry_quality_cap": entry_quality_cap,
        "validation_evidence_cap": validation_evidence_cap,
        "validation_repair_cap": validation_repair_cap,
        "lane_confidence_cap": lane_confidence_cap,
    }
    active_caps = [
        kelly_cap,
        *[value for key, value in cap_values.items() if key != "kelly_cap" and value < 1.0],
    ]
    risk_budget_multiplier = min(active_caps) if active_caps else 1.0
    blockers: list[str] = []
    repair_targets: list[str] = []

    def add_blocker(condition: bool, blocker: str, repair_target: str) -> None:
        if not condition:
            return
        if blocker not in blockers:
            blockers.append(blocker)
        if repair_target not in repair_targets:
            repair_targets.append(repair_target)

    add_blocker(
        sample_cap < 1.0,
        "insufficient_closed_samples",
        "close_more_verified_lane_samples_before_scale_up",
    )
    add_blocker(
        kelly_cap < 1.0,
        "fractional_kelly_cap",
        "improve_win_rate_payoff_or_cost_adjusted_expectancy",
    )
    add_blocker(
        drawdown_cap < 1.0,
        "drawdown_cap",
        "reduce_mdd_before_size_increase",
    )
    add_blocker(
        recovery_cap < 1.0,
        "recovery_factor_cap",
        "improve_recovery_factor_before_size_increase",
    )
    add_blocker(
        ruin_cap < 1.0,
        "risk_of_ruin_cap",
        "lower_risk_of_ruin_before_size_increase",
    )
    add_blocker(
        cost_precision_cap < 1.0,
        "cost_precision_cap",
        "record_fee_tax_spread_slippage_funding_before_scale_up",
    )
    add_blocker(
        entry_quality_cap < 1.0,
        "entry_quality_cap",
        "prefer_pullback_value_or_reclaim_entry_before_scale_up",
    )
    add_blocker(
        validation_evidence_cap < 1.0,
        "validation_evidence_cap",
        "pass_backtest_walk_forward_oos_live_shadow_before_scale_up",
    )
    add_blocker(
        validation_repair_cap < 1.0,
        "validation_repair_cap",
        "clear_validation_repair_enforcement_before_scale_up",
    )
    add_blocker(
        lane_confidence_cap < 1.0,
        "lane_confidence_cap",
        "raise_lane_confidence_before_size_increase",
    )

    if risk_budget_multiplier <= 0.0:
        scale_decision = "blocked"
    elif blockers:
        scale_decision = "capped_until_repairs"
    elif risk_budget_multiplier > 1.0:
        scale_decision = "eligible_to_scale"
    else:
        scale_decision = "normal_size_no_scale"
    return {
        "risk_budget_multiplier": round(risk_budget_multiplier, 6),
        "risk_budget_scale_decision": scale_decision,
        "risk_budget_blockers": blockers[:10],
        "risk_budget_repair_targets": repair_targets[:10],
        "sample_confidence": round(sample_confidence, 6),
        "lane_confidence_score": round(lane_confidence, 6),
        "raw_kelly_fraction": round(raw_kelly, 8),
        "fractional_kelly_fraction": round(fractional_kelly, 8),
        "kelly_cap_multiplier": round(kelly_cap, 6),
        "drawdown_cap_multiplier": round(drawdown_cap, 6),
        "recovery_factor_cap_multiplier": round(recovery_cap, 6),
        "ruin_cap_multiplier": round(ruin_cap, 6),
        "sample_cap_multiplier": round(sample_cap, 6),
        "cost_precision_cap_multiplier": round(cost_precision_cap, 6),
        "entry_quality_cap_multiplier": round(entry_quality_cap, 6),
        "validation_evidence_cap_multiplier": round(validation_evidence_cap, 6),
        "validation_repair_cap_multiplier": round(validation_repair_cap, 6),
        "lane_confidence_cap_multiplier": round(lane_confidence_cap, 6),
        "validation_pass_rate": round(validation_pass_rate, 6),
        "edge_confidence_score": round(edge_confidence, 6),
    }


def _clean_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _entry_quality_is_bad(label: Any, score: float) -> bool:
    if 0 < score < 55.0:
        return True
    text = _clean_key(label)
    if any(token in text for token in GOOD_ENTRY_QUALITY_TOKENS):
        return False
    return any(token in text for token in BAD_ENTRY_QUALITY_TOKENS)


def _pattern_repair_priorities(
    failed_reasons: dict[str, int],
    *,
    active_set_count: int,
    rejected_set_count: int,
) -> list[dict[str, Any]]:
    priorities: list[dict[str, Any]] = []
    if active_set_count <= 0 and rejected_set_count > 0:
        priorities.append(
            {
                "priority": "active_edge_rebuild",
                "reason": "no_active_pattern_sets",
                "count": rejected_set_count,
                "focus": "validated_edge_absent",
                "action": (
                    "Keep this venue in probe/waiting-entry mode while pattern "
                    "lab rebuilds at least one active set from OOS/WFA evidence."
                ),
            }
        )
    for reason, count in sorted(
        failed_reasons.items(),
        key=lambda item: (-int(item[1]), item[0]),
    )[:6]:
        guidance = PATTERN_REPAIR_GUIDANCE.get(
            reason,
            {
                "focus": "unknown_pattern_failure",
                "action": (
                    "Inspect rejected samples and convert the failure into a "
                    "specific entry, sizing, target, or stop adjustment."
                ),
            },
        )
        priorities.append(
            {
                "priority": f"repair_{reason}",
                "reason": reason,
                "count": int(count),
                **guidance,
            }
        )
    return priorities[:6]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "stale"}


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


def _cost_component_label(value: Any) -> str:
    key = _clean_key(value)
    component = _COST_COMPONENT_ALIASES.get(key, key)
    return component if component in _CANONICAL_COST_COMPONENTS else ""


def _is_absent_component_marker(value: Any) -> bool:
    return value is None or value == "" or value is False


def _declared_cost_components(value: Any) -> set[str]:
    raw_value = value
    if isinstance(value, str):
        parsed = _json_loads(value)
        raw_value = parsed if parsed else value
    present: set[str] = set()
    if isinstance(raw_value, dict):
        for raw_key, raw_marker in raw_value.items():
            if _is_absent_component_marker(raw_marker):
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
                if _is_absent_component_marker(marker):
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
        normalized = (
            raw_value.replace("\n", ",")
            .replace(";", ",")
            .replace("|", ",")
        )
        pieces = normalized.split(",") if "," in normalized else normalized.split()
        for piece in pieces:
            if component := _cost_component_label(piece):
                present.add(component)
    return present


def _cost_component_presence(row: dict[str, Any], metadata: dict[str, Any]) -> set[str]:
    present: set[str] = set()
    components = (
        metadata.get("cost_components")
        or metadata.get("cost_breakdown")
        or metadata.get("cost_component_sources")
        or metadata.get("component_sources")
    )
    components = _json_loads(components)
    if isinstance(components, dict):
        for raw_key, raw_value in components.items():
            if raw_value in (None, ""):
                continue
            if component := _cost_component_label(raw_key):
                present.add(component)
    elif isinstance(components, (list, tuple, set, str)):
        present.update(_declared_cost_components(components))
    for declaration_key in _COST_COMPONENT_DECLARATION_KEYS:
        present.update(_declared_cost_components(metadata.get(declaration_key)))
    for raw_key, component in _COST_COMPONENT_ALIASES.items():
        if raw_key in metadata and metadata.get(raw_key) not in (None, ""):
            present.add(component)
    for column in ("fees", "taxes", "funding", "slippage", "spread"):
        if _safe_float(row.get(column)) > 0:
            present.add(column)
    return present


def _required_cost_components(row: dict[str, Any], metadata: dict[str, Any]) -> set[str]:
    venue = _clean_key(row.get("venue"))
    market = _clean_key(metadata.get("market"))
    lane = _clean_key(metadata.get("lane"))
    side = _clean_key(metadata.get("side"))
    if venue in {"binance", "upbit"}:
        is_futures = bool(
            market in {"futures", "perp", "perpetual"}
            or lane in {"futures", "futures_long", "futures_short", "volatile_attack"}
            or lane.startswith("futures")
            or side == "short"
        )
        if is_futures:
            return {"fees", "funding", "spread", "slippage"}
        return {"fees", "spread", "slippage"}
    if venue == "kis":
        return {"fees", "taxes", "spread", "slippage"}
    return {"fees"}


def _cost_precision_bucket(row: dict[str, Any], metadata: dict[str, Any]) -> str:
    normalized = _clean_key(
        row.get("cost_precision") or metadata.get("cost_precision")
    )
    cost_status = _clean_key(
        metadata.get("cost_model_status") or row.get("cost_model_status")
    )
    cost_source = _clean_key(
        metadata.get("cost_source") or row.get("cost_source")
    )
    combined = f"{normalized} {cost_status} {cost_source}"
    if (
        normalized == "hybrid"
        or "hybrid" in combined
        or "explicit_order_costs_plus_estimated" in combined
    ):
        bucket = "hybrid"
    elif normalized == "partial" or "partial" in combined or "unconverted" in combined:
        bucket = "partial"
    elif "estimated" in combined:
        bucket = "estimated"
    elif normalized == "recorded" or any(
        token in combined
        for token in (
            "actual",
            "exchange_fill",
            "explicit",
            "order_payload",
            "recorded",
        )
    ):
        bucket = "recorded"
    elif any(token in combined for token in ("missing", "unknown", "error")):
        bucket = "missing"
    elif _safe_float(row.get("cost_total")) > 0:
        bucket = "unverified_cost"
    else:
        bucket = "missing"
    if bucket == "recorded":
        missing = _required_cost_components(row, metadata) - _cost_component_presence(
            row,
            metadata,
        )
        if missing:
            return "partial"
    return bucket


def _normalize_discipline_status(value: Any) -> str:
    status = _clean_key(value)
    if status in {"pass", "passed", "ok", "clear", "green"}:
        return "pass"
    if status in {"warn", "warning", "stale", "weak", "yellow"}:
        return "warn"
    if status in {
        "fail",
        "failed",
        "error",
        "blocked",
        "blocked_by_validation",
        "validation_error",
        "red",
    }:
        return "fail"
    if status in {"missing", "unknown", "none", "null", "n/a", "na", ""}:
        return "missing"
    return "missing"


def _with_absent_disciplines_as_missing(
    disciplines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = list(disciplines)
    present_ids = {
        str(row.get("id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    if len(rows) >= len(DISCIPLINE_DEFINITIONS) or not present_ids:
        return rows
    for definition in DISCIPLINE_DEFINITIONS:
        discipline_id = definition["id"]
        if discipline_id in present_ids:
            continue
        rows.append(
            {
                **definition,
                "status": "missing",
                "evidence": "검증 row가 payload에 없습니다.",
                "action": (
                    f"{definition['label']} 검증 결과를 생성하고 "
                    "validation payload에 포함해야 합니다."
                ),
                "metric": {
                    "status": "missing",
                    "reason": "absent_discipline_row",
                },
            }
        )
    return rows


class TradingValidationRepository:
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
                CREATE TABLE IF NOT EXISTS validation_runs (
                    run_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL DEFAULT 'live',
                    strategy_revision_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    total_score REAL NOT NULL DEFAULT 0,
                    pass_count INTEGER NOT NULL DEFAULT 0,
                    warn_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_runs_venue_time
                    ON validation_runs(venue, computed_at DESC);
                """
            )
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(validation_runs)")
        }
        if "strategy_revision_id" not in existing:
            conn.execute(
                "ALTER TABLE validation_runs "
                "ADD COLUMN strategy_revision_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_validation_runs_venue_revision_time
                ON validation_runs(venue, strategy_revision_id, computed_at DESC)
            """
        )

    def save_run(self, payload: dict[str, Any]) -> None:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        strategy_revision_id = str(payload.get("strategy_revision_id") or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO validation_runs (
                    run_id, venue, scope, strategy_revision_id, status, total_score,
                    pass_count, warn_count, fail_count, missing_count,
                    payload_json, computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    venue=excluded.venue,
                    scope=excluded.scope,
                    strategy_revision_id=excluded.strategy_revision_id,
                    status=excluded.status,
                    total_score=excluded.total_score,
                    pass_count=excluded.pass_count,
                    warn_count=excluded.warn_count,
                    fail_count=excluded.fail_count,
                    missing_count=excluded.missing_count,
                    payload_json=excluded.payload_json,
                    computed_at=excluded.computed_at
                """,
                (
                    str(payload.get("run_id") or ""),
                    str(payload.get("venue") or ""),
                    str(payload.get("scope") or "live"),
                    strategy_revision_id,
                    str(payload.get("status") or "ok"),
                    _safe_float(summary.get("total_score")),
                    int(summary.get("pass_count") or 0),
                    int(summary.get("warn_count") or 0),
                    int(summary.get("fail_count") or 0),
                    int(summary.get("missing_count") or 0),
                    json.dumps(payload, ensure_ascii=False),
                    str(payload.get("computed_at") or utc_now_iso()),
                ),
            )

    @staticmethod
    def _compact_payload_from_row(row: sqlite3.Row, *, compacted_at: str) -> str:
        raw = str(row["payload_json"] or "{}")
        payload = _json_loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        payload_summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        summary = {
            "total_score": _safe_float(row["total_score"]),
            "pass_count": int(row["pass_count"] or 0),
            "warn_count": int(row["warn_count"] or 0),
            "fail_count": int(row["fail_count"] or 0),
            "missing_count": int(row["missing_count"] or 0),
            "readiness": str(payload_summary.get("readiness") or ""),
        }
        for key in (
            "diagnostic_pass_count",
            "diagnostic_warn_count",
            "diagnostic_fail_count",
            "diagnostic_missing_count",
            "core_gate_ids",
            "core_expected_count",
            "core_pass_count",
            "core_warn_count",
            "core_fail_count",
            "core_missing_count",
            "hard_fail_count",
            "hard_missing_count",
            "hard_blocking_count",
            "active_revision_sample_mode",
            "active_revision_sample_count",
            "min_samples_to_scale",
            "scale_up_allowed",
        ):
            if key in payload_summary:
                summary[key] = payload_summary[key]
        compacted = {
            "status": str(row["status"] or "ok"),
            "version": "jue_validation_compacted_v1",
            "compacted": True,
            "run_id": str(row["run_id"] or ""),
            "scope": str(row["scope"] or "live"),
            "venue": str(row["venue"] or ""),
            "strategy_revision_id": str(row["strategy_revision_id"] or ""),
            "computed_at": str(row["computed_at"] or ""),
            "discipline_count": int(payload.get("discipline_count") or 0),
            "summary": summary,
            "compaction": {
                "compacted_at": compacted_at,
                "original_payload_chars": len(raw),
            },
        }
        return json.dumps(compacted, ensure_ascii=False, sort_keys=True)

    def compact_history(
        self,
        *,
        recent_rows_per_group: int = 48,
        max_rows_per_group: int = 720,
        min_payload_chars: int = 20_000,
        vacuum: bool = False,
    ) -> dict[str, Any]:
        keep = max(int(recent_rows_per_group or 0), 1)
        max_rows = max(int(max_rows_per_group or 0), keep)
        threshold = max(int(min_payload_chars or 0), 1)
        compacted_at = utc_now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM validation_runs
                ORDER BY venue, scope, strategy_revision_id, computed_at DESC
                """
            ).fetchall()
            group_counts: dict[tuple[str, str, str], int] = {}
            candidates: list[sqlite3.Row] = []
            skipped_recent = 0
            skipped_small = 0
            skipped_compacted = 0
            before_chars = 0
            after_chars = 0
            delete_run_ids: list[str] = []
            for row in rows:
                group_key = (
                    str(row["venue"] or ""),
                    str(row["scope"] or "live"),
                    str(row["strategy_revision_id"] or ""),
                )
                group_counts[group_key] = group_counts.get(group_key, 0) + 1
                if group_counts[group_key] > max_rows:
                    delete_run_ids.append(str(row["run_id"] or ""))
                    continue
                raw_payload = str(row["payload_json"] or "{}")
                if group_counts[group_key] <= keep:
                    skipped_recent += 1
                    continue
                if len(raw_payload) < threshold:
                    skipped_small += 1
                    continue
                payload = _json_loads(raw_payload)
                if isinstance(payload, dict) and payload.get("compacted") is True:
                    skipped_compacted += 1
                    continue
                candidates.append(row)

            for row in candidates:
                raw_payload = str(row["payload_json"] or "{}")
                compacted_payload = self._compact_payload_from_row(
                    row,
                    compacted_at=compacted_at,
                )
                before_chars += len(raw_payload)
                after_chars += len(compacted_payload)
                conn.execute(
                    """
                    UPDATE validation_runs
                    SET payload_json = ?
                    WHERE run_id = ?
                    """,
                    (compacted_payload, str(row["run_id"] or "")),
                )
            for run_id in delete_run_ids:
                if not run_id:
                    continue
                conn.execute(
                    "DELETE FROM validation_runs WHERE run_id = ?",
                    (run_id,),
                )
        if vacuum and (candidates or delete_run_ids):
            with self._connect() as conn:
                conn.execute("VACUUM")
        return {
            "status": "ok",
            "db_path": str(self.path),
            "recent_rows_per_group": keep,
            "max_rows_per_group": max_rows,
            "min_payload_chars": threshold,
            "compacted_count": len(candidates),
            "deleted_count": len(delete_run_ids),
            "skipped_recent_count": skipped_recent,
            "skipped_small_count": skipped_small,
            "skipped_already_compacted_count": skipped_compacted,
            "before_payload_chars": before_chars,
            "after_payload_chars": after_chars,
            "saved_payload_chars": max(before_chars - after_chars, 0),
            "vacuum": bool(vacuum and (candidates or delete_run_ids)),
            "compacted_at": compacted_at,
        }

    def latest(self, *, venue: str = "", strategy_revision_id: str = "") -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if venue:
            where = "WHERE venue = ?"
            params.append(venue)
        revision_id = str(strategy_revision_id or "").strip()
        if revision_id:
            where += " AND " if where else "WHERE "
            where += "COALESCE(NULLIF(strategy_revision_id, ''), 'legacy') = ?"
            params.append(revision_id)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT *
                FROM validation_runs
                {where}
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        if row is None:
            return {
                "status": "empty",
                "db_path": str(self.path),
                "venue": venue,
                "strategy_revision_id": revision_id,
                "payload": {},
            }
        payload = json.loads(str(row["payload_json"] or "{}"))
        payload_summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        summary = {
            "status": str(row["status"] or "ok"),
            "db_path": str(self.path),
            "run_id": str(row["run_id"] or ""),
            "venue": str(row["venue"] or ""),
            "strategy_revision_id": str(row["strategy_revision_id"] or ""),
            "computed_at": str(row["computed_at"] or ""),
            "summary": {
                "total_score": _safe_float(row["total_score"]),
                "pass_count": int(row["pass_count"] or 0),
                "warn_count": int(row["warn_count"] or 0),
                "fail_count": int(row["fail_count"] or 0),
                "missing_count": int(row["missing_count"] or 0),
                "readiness": str(payload_summary.get("readiness") or ""),
            },
            "payload": payload,
        }
        for key in (
            "diagnostic_pass_count",
            "diagnostic_warn_count",
            "diagnostic_fail_count",
            "diagnostic_missing_count",
            "core_gate_ids",
            "core_expected_count",
            "core_pass_count",
            "core_warn_count",
            "core_fail_count",
            "core_missing_count",
            "hard_fail_count",
            "hard_missing_count",
            "hard_blocking_count",
        ):
            if key in payload_summary:
                summary["summary"][key] = payload_summary[key]
        return summary


class TradingValidationService:
    def __init__(self, config: TradingValidationConfig) -> None:
        self.config = config
        self.repository = TradingValidationRepository(config.validation_db_path)

    def run_once(self, *, venue: str = "", scope: str = "live") -> dict[str, Any]:
        active_outcomes = self._load_live_outcomes(venue=venue)
        active_revision_evidence = self._active_revision_evidence(
            active_outcomes=active_outcomes,
            venue=venue,
        )
        outcomes = active_outcomes
        if (
            not active_outcomes
            and active_revision_evidence.get("legacy_proxy_available")
        ):
            outcomes = self._load_live_outcomes(
                venue=venue,
                strategy_revision_id="",
            )
            active_revision_evidence["validation_sample_role"] = (
                "legacy_proxy_metrics_no_scale"
            )
            active_revision_evidence["proxy_sample_used_for_metrics"] = True
        metrics = self._compute_metrics(outcomes)
        if active_revision_evidence:
            metrics["active_revision_evidence"] = active_revision_evidence
        metrics.update(
            self._compute_metadata_diagnostics(outcomes, metrics, venue=venue)
        )
        monte_carlo = self._monte_carlo(metrics["returns_pct"])
        metrics["risk_of_ruin_pct"] = monte_carlo["risk_of_ruin_pct"]
        metrics["ruin_profile"] = self._ruin_profile_metrics(monte_carlo)
        metrics["fractional_kelly_025"] = round(metrics["kelly_fraction"] * 0.25, 6)
        metrics["validation_quality_pressure"] = self._validation_quality_pressure(
            metrics
        )
        metrics["kelly_sizing"] = self._kelly_sizing_metrics(metrics)
        disciplines = self._build_disciplines(metrics=metrics, monte_carlo=monte_carlo)
        disciplines = self._adapt_disciplines_for_active_revision_sample_mode(
            disciplines,
            active_revision_evidence=active_revision_evidence,
        )
        if active_revision_evidence:
            metrics["active_revision_evidence"] = active_revision_evidence
        summary = self._summarize_disciplines(disciplines)
        if active_revision_evidence:
            if (
                active_revision_evidence.get("status")
                == "active_revision_sample_building"
                and summary.get("readiness") not in {"blocked_by_validation", "research_only"}
            ):
                summary["readiness"] = "probe"
            summary["active_revision_sample_mode"] = active_revision_evidence[
                "status"
            ]
            summary["active_revision_sample_count"] = active_revision_evidence[
                "active_sample_count"
            ]
            summary["min_samples_to_scale"] = active_revision_evidence[
                "min_samples_to_scale"
            ]
            summary["scale_up_allowed"] = bool(
                active_revision_evidence.get("scale_up_allowed")
            )
        if active_revision_evidence.get("validation_sample_role"):
            if (
                active_revision_evidence.get("validation_sample_role")
                == "legacy_proxy_metrics_no_scale"
                and int(active_revision_evidence.get("active_sample_count") or 0) == 0
                and summary.get("readiness") not in {"blocked_by_validation", "research_only"}
            ):
                summary["readiness"] = "probe"
            summary["validation_sample_role"] = active_revision_evidence[
                "validation_sample_role"
            ]
            summary["active_revision_sample_mode"] = active_revision_evidence[
                "status"
            ]
            summary["can_scale_from_proxy"] = bool(
                active_revision_evidence.get("can_scale_from_proxy")
            )
            summary["legacy_proxy_failed_discipline_ids"] = list(
                active_revision_evidence.get("legacy_proxy_failed_discipline_ids")
                or []
            )
            summary["legacy_proxy_missing_core_discipline_ids"] = list(
                active_revision_evidence.get(
                    "legacy_proxy_missing_core_discipline_ids"
                )
                or []
            )
        if active_revision_evidence.get(
            "active_revision_sample_building_failed_discipline_ids"
        ):
            summary["active_revision_sample_building_failed_discipline_ids"] = list(
                active_revision_evidence.get(
                    "active_revision_sample_building_failed_discipline_ids"
                )
                or []
            )
        remediation_plan = self._build_remediation_plan(
            disciplines,
            config=self.config,
        )
        if active_revision_evidence:
            remediation_plan["active_revision_evidence"] = active_revision_evidence
            lane_hints = (
                remediation_plan.get("lane_policy_hints")
                if isinstance(remediation_plan.get("lane_policy_hints"), dict)
                else {}
            )
            lane_hints["active_revision_sample_mode"] = active_revision_evidence[
                "status"
            ]
            lane_hints["active_revision_sample_count"] = active_revision_evidence[
                "active_sample_count"
            ]
            lane_hints["legacy_proxy_sample_count"] = active_revision_evidence[
                "legacy_proxy_sample_count"
            ]
            lane_hints["can_scale_from_proxy"] = False
            remediation_plan["lane_policy_hints"] = lane_hints
        payload = {
            "status": "ok",
            "version": "jue_validation_lab_v1",
            "run_id": f"validation-{uuid.uuid4().hex[:12]}",
            "scope": scope,
            "venue": venue or "all",
            "strategy_revision_id": str(self.config.strategy_revision_id or "").strip(),
            "computed_at": utc_now_iso(),
            "discipline_count": len(disciplines),
            "disciplines": disciplines,
            "summary": summary,
            "metrics": {key: value for key, value in metrics.items() if key != "returns_pct"},
            "monte_carlo": monte_carlo,
            "remediation_plan": remediation_plan,
            "operator_guidance": self._operator_guidance(disciplines, metrics),
        }
        self.repository.save_run(payload)
        return payload

    @staticmethod
    def _adapt_disciplines_for_active_revision_sample_mode(
        disciplines: list[dict[str, Any]],
        *,
        active_revision_evidence: dict[str, Any],
    ) -> list[dict[str, Any]]:
        core_ids = set(OPERATIONAL_READINESS_DISCIPLINE_IDS)
        active_count = int(active_revision_evidence.get("active_sample_count") or 0)
        if (
            active_revision_evidence.get("validation_sample_role")
            == "legacy_proxy_metrics_no_scale"
            and active_count <= 0
        ):
            failed_ids: list[str] = []
            missing_core_ids: list[str] = []
            adapted: list[dict[str, Any]] = []
            for row in disciplines:
                discipline_id = str(row.get("id") or "").strip()
                original_status = _normalize_discipline_status(row.get("status"))
                next_row = dict(row)
                demote_to_warn = original_status == "fail" or (
                    original_status == "missing" and discipline_id in core_ids
                )
                if original_status == "fail":
                    failed_ids.append(discipline_id)
                elif original_status == "missing" and discipline_id in core_ids:
                    missing_core_ids.append(discipline_id)
                if demote_to_warn:
                    metric = (
                        dict(next_row.get("metric"))
                        if isinstance(next_row.get("metric"), dict)
                        else {}
                    )
                    metric["legacy_proxy_status"] = original_status
                    metric["validation_sample_role"] = "legacy_proxy_metrics_no_scale"
                    metric["active_revision_sample_mode"] = (
                        active_revision_evidence.get("status")
                    )
                    metric["can_scale_from_proxy"] = False
                    next_row["metric"] = metric
                    next_row["status"] = "warn"
                    next_row["legacy_proxy_status"] = original_status
                    next_row["validation_sample_role"] = (
                        "legacy_proxy_metrics_no_scale"
                    )
                    next_row["evidence"] = (
                        f"현재 전략 revision 표본은 아직 0건입니다. "
                        f"이 행의 원래 판정은 legacy proxy 기준 {original_status}이며, "
                        "스케일업 근거로 쓰지 않고 probe-only 수리 경고로만 사용합니다. "
                        f"{next_row.get('evidence') or ''}"
                    ).strip()
                    next_row["action"] = (
                        "현재 revision은 즉시 스케일업하지 말고 소액/대기진입 "
                        "샘플을 쌓은 뒤 이 항목을 active revision 기준으로 재검증합니다. "
                        f"{next_row.get('action') or ''}"
                    ).strip()
                adapted.append(next_row)

            active_revision_evidence["legacy_proxy_failed_discipline_ids"] = (
                failed_ids
            )
            active_revision_evidence["legacy_proxy_missing_core_discipline_ids"] = (
                missing_core_ids
            )
            if failed_ids or missing_core_ids:
                active_revision_evidence["legacy_proxy_gate_mode"] = "probe_only"
                active_revision_evidence["next_action"] = (
                    "collect_active_revision_probe_samples_before_scaling"
                )
            return adapted

        if active_revision_evidence.get("status") != "active_revision_sample_building":
            return disciplines

        min_count = int(active_revision_evidence.get("min_sample_count") or 0)
        if active_count <= 0 or min_count <= 0 or active_count >= min_count:
            return disciplines

        sample_building_failed_ids: list[str] = []
        sample_adapted: list[dict[str, Any]] = []
        for row in disciplines:
            discipline_id = str(row.get("id") or "").strip()
            original_status = _normalize_discipline_status(row.get("status"))
            next_row = dict(row)
            if original_status == "fail" and discipline_id not in core_ids:
                sample_building_failed_ids.append(discipline_id)
                metric = (
                    dict(next_row.get("metric"))
                    if isinstance(next_row.get("metric"), dict)
                    else {}
                )
                metric["active_revision_sample_building_status"] = original_status
                metric["active_revision_sample_count"] = active_count
                metric["min_samples_to_scale"] = min_count
                metric["scale_up_allowed"] = False
                next_row["metric"] = metric
                next_row["status"] = "warn"
                next_row["active_revision_sample_building_status"] = original_status
                next_row["evidence"] = (
                    f"현재 전략 revision 표본은 {active_count}/{min_count}건으로 "
                    "성과 통계를 fail로 확정하기에는 부족합니다. "
                    f"원래 판정은 {original_status}이며, 스케일업은 금지하고 "
                    "probe 경고로 유지합니다. "
                    f"{next_row.get('evidence') or ''}"
                ).strip()
                next_row["action"] = (
                    "최소 표본이 쌓일 때까지 이 항목은 실패 확정이 아니라 "
                    "소액/대기진입 probe 경고로 관리합니다. "
                    f"{next_row.get('action') or ''}"
                ).strip()
            sample_adapted.append(next_row)

        active_revision_evidence[
            "active_revision_sample_building_failed_discipline_ids"
        ] = sample_building_failed_ids
        if sample_building_failed_ids:
            active_revision_evidence["sample_building_gate_mode"] = "probe_only"
            active_revision_evidence["next_action"] = (
                "collect_more_active_revision_samples_before_fail_or_scale"
            )
        return sample_adapted

    def latest(self, *, venue: str = "") -> dict[str, Any]:
        return self.repository.latest(
            venue=venue,
            strategy_revision_id=str(self.config.strategy_revision_id or "").strip(),
        )

    def _load_live_outcomes(
        self,
        *,
        venue: str = "",
        strategy_revision_id: str | None = None,
    ) -> list[dict[str, Any]]:
        path = Path(self.config.live_performance_db_path)
        if not path.exists():
            return []
        params: list[Any] = []
        where = "WHERE filled = 1 AND include_in_jue_alpha = 1"
        if venue:
            where += " AND venue = ?"
            params.append(venue)
        revision_id = (
            str(self.config.strategy_revision_id or "").strip()
            if strategy_revision_id is None
            else str(strategy_revision_id or "").strip()
        )
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if revision_id:
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(live_block_performance)")
                }
                if "strategy_revision_id" not in columns:
                    return []
                where += " AND COALESCE(NULLIF(strategy_revision_id, ''), 'legacy') = ?"
                params.append(revision_id)
            rows = conn.execute(
                f"""
                SELECT *
                FROM live_block_performance
                {where}
                ORDER BY computed_at ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _active_revision_evidence(
        self,
        *,
        active_outcomes: list[dict[str, Any]],
        venue: str = "",
    ) -> dict[str, Any]:
        revision_id = str(self.config.strategy_revision_id or "").strip()
        if not revision_id:
            return {}
        reference_outcomes = self._load_live_outcomes(
            venue=venue,
            strategy_revision_id="",
        )
        proxy_outcomes = [
            row
            for row in reference_outcomes
            if (str(row.get("strategy_revision_id") or "").strip() or "legacy")
            != revision_id
        ]
        proxy_metrics = self._compute_metrics(proxy_outcomes) if proxy_outcomes else {}
        active_count = len(active_outcomes)
        proxy_count = len(proxy_outcomes)
        if active_count <= 0:
            status = (
                "no_active_revision_samples_with_proxy"
                if proxy_count > 0
                else "no_active_revision_samples"
            )
            evidence_role = "proxy_only_not_scale_up"
            next_action = "close_live_or_shadow_blocks_for_active_revision"
        elif active_count < int(self.config.min_sample_count):
            status = "active_revision_sample_building"
            evidence_role = "active_samples_probe_only"
            next_action = "continue_probe_until_min_sample_count"
        else:
            status = "active_revision_samples_ready"
            evidence_role = "active_revision_validation"
            next_action = "use_active_revision_validation_metrics"
        sample_scale_up_allowed = bool(active_count >= int(self.config.min_sample_count))
        authority_posture = (
            "probe_only_until_active_revision_samples_close"
            if active_count <= 0
            else "small_probe_until_min_sample_count"
            if not sample_scale_up_allowed
            else "active_revision_samples_ready_for_validation_gate"
        )
        return {
            "version": "active_revision_evidence_v1",
            "venue": _clean_key(venue) or "all",
            "status": status,
            "strategy_revision_id": revision_id,
            "active_sample_count": active_count,
            "effective_sample_count": active_count,
            "validation_sample_count": active_count,
            "min_sample_count": int(self.config.min_sample_count),
            "min_samples_to_scale": int(self.config.min_sample_count),
            "all_revision_sample_count": len(reference_outcomes),
            "legacy_proxy_sample_count": proxy_count,
            "legacy_proxy_available": proxy_count > 0,
            "legacy_proxy_win_rate_pct": proxy_metrics.get("win_rate_pct", 0.0),
            "legacy_proxy_profit_factor": proxy_metrics.get("profit_factor", 0.0),
            "legacy_proxy_expectancy_pct": proxy_metrics.get("expectancy_pct", 0.0),
            "legacy_proxy_total_net_pnl": proxy_metrics.get("total_net_pnl", 0.0),
            "evidence_role": evidence_role,
            "can_scale_from_proxy": False,
            "scale_up_allowed": sample_scale_up_allowed,
            "authority_posture": authority_posture,
            "block_design_requirement": (
                "active revision 표본이 최소 기준에 도달하기 전까지 "
                "즉시진입 대신 소액/대기진입 probe만 허용"
                if not sample_scale_up_allowed
                else "active revision 표본은 최소 기준에 도달했지만, "
                "전체 19검증과 lane authority를 통과해야 증액 가능"
            ),
            "next_action": next_action,
        }

    def _compute_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        initial_equity = max(float(self.config.initial_equity), 1.0)
        net_pnls = [_safe_float(row.get("net_pnl")) for row in outcomes]
        pnl_pct_values = [_safe_float(row.get("pnl_pct")) for row in outcomes]
        returns = [value / 100.0 for value in pnl_pct_values]
        wins = [value for value in net_pnls if value > 0]
        losses = [value for value in net_pnls if value < 0]
        win_pct_values = [value for value in pnl_pct_values if value > 0]
        loss_pct_values = [value for value in pnl_pct_values if value < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        sample_count = len(outcomes)
        win_rate = len(wins) / sample_count if sample_count else 0.0
        avg_win_pct = _avg(win_pct_values)
        avg_loss_pct = abs(_avg(loss_pct_values))
        payoff_ratio = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 0.0
        kelly = win_rate - ((1.0 - win_rate) / payoff_ratio) if payoff_ratio > 0 else 0.0
        kelly = max(min(kelly, 1.0), 0.0)
        equity = initial_equity
        equity_curve = [equity]
        for pnl in net_pnls:
            equity += pnl
            equity_curve.append(equity)
        max_drawdown_cash, max_drawdown_pct = _max_drawdown(equity_curve)
        total_net_pnl = sum(net_pnls)
        total_return_pct = total_net_pnl / initial_equity * 100.0
        mean_return = _avg(returns)
        volatility = _stddev(returns)
        downside = _stddev([min(value, 0.0) for value in returns if value < 0.0])
        periods = math.sqrt(sample_count) if sample_count > 0 else 1.0
        sharpe = (mean_return / volatility * periods) if volatility > 0 else 0.0
        sortino = (mean_return / downside * periods) if downside > 0 else 0.0
        calmar = (
            total_return_pct / abs(max_drawdown_pct)
            if max_drawdown_pct < 0
            else (999.0 if total_return_pct > 0 else 0.0)
        )
        recovery = (
            total_net_pnl / abs(max_drawdown_cash)
            if max_drawdown_cash < 0
            else (999.0 if total_net_pnl > 0 else 0.0)
        )
        cost_total = sum(_safe_float(row.get("cost_total")) for row in outcomes)
        symbols = sorted({str(row.get("symbol") or "") for row in outcomes if row.get("symbol")})
        venues = sorted({str(row.get("venue") or "") for row in outcomes if row.get("venue")})
        revision_counts: dict[str, int] = {}
        for row in outcomes:
            revision_id = str(row.get("strategy_revision_id") or "").strip() or "legacy"
            revision_counts[revision_id] = revision_counts.get(revision_id, 0) + 1
        return {
            "sample_count": sample_count,
            "strategy_revision_id": str(self.config.strategy_revision_id or "").strip(),
            "strategy_revision_counts": revision_counts,
            "symbol_count": len(symbols),
            "symbols": symbols[:50],
            "venues": venues,
            "total_net_pnl": round(total_net_pnl, 6),
            "total_return_pct": round(total_return_pct, 6),
            "expectancy_pct": round(_avg(pnl_pct_values), 6),
            "win_rate_pct": round(win_rate * 100.0, 6),
            "avg_win_pct": round(avg_win_pct, 6),
            "avg_loss_pct": round(avg_loss_pct, 6),
            "payoff_ratio": round(payoff_ratio, 6),
            "profit_factor": round(profit_factor, 6),
            "max_drawdown_cash": round(max_drawdown_cash, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "sharpe_ratio": round(sharpe, 6),
            "sortino_ratio": round(sortino, 6),
            "calmar_ratio": round(calmar, 6),
            "recovery_factor": round(recovery, 6),
            "kelly_fraction": round(kelly, 6),
            "cost_total": round(cost_total, 6),
            "returns_pct": pnl_pct_values,
        }

    def _metadata_for_outcome(self, row: dict[str, Any]) -> dict[str, Any]:
        source = _json_loads(row.get("source_json"))
        metadata: dict[str, Any] = {}
        if isinstance(source, dict):
            source_metadata = source.get("metadata")
            if isinstance(source_metadata, dict):
                metadata.update(source_metadata)
            else:
                block = source.get("block")
                if isinstance(block, dict):
                    block_metadata = _json_loads(block.get("metadata_json"))
                    if isinstance(block_metadata, dict):
                        metadata.update(block_metadata)
        for key in (
            "cost_model_status",
            "cost_source",
            "cost_precision",
            "fill_evidence_status",
            "entry_price_source",
            "exit_price_source",
        ):
            value = row.get(key)
            if value not in (None, ""):
                metadata.setdefault(key, value)
        return metadata

    def _compute_metadata_diagnostics(
        self,
        outcomes: list[dict[str, Any]],
        metrics: dict[str, Any],
        *,
        venue: str = "",
    ) -> dict[str, Any]:
        return {
            "data_quality": self._data_quality_metrics(outcomes),
            "cost_simulation": self._cost_simulation_metrics(outcomes),
            "drawdown_budget": self._drawdown_budget_metrics(outcomes),
            "risk_adjusted_performance": self._risk_adjusted_performance_metrics(
                metrics
            ),
            "profitability_quality": self._profitability_quality_metrics(metrics),
            "recovery_profile": self._recovery_profile_metrics(outcomes, metrics),
            "regime_scorecards": self._regime_scorecards(outcomes),
            "lane_scorecards": self._lane_scorecards(outcomes),
            "stress": self._stress_metrics(metrics, outcomes),
            "capacity": self._capacity_metrics(outcomes),
            "correlation_proxy": self._correlation_proxy(outcomes),
            "factor_exposure": self._factor_exposure_metrics(outcomes),
            "failure_attribution": self._failure_attribution_metrics(outcomes),
            "pattern_lab": self._pattern_lab_metrics(
                venue=venue,
                outcomes=outcomes,
                metrics=metrics,
            ),
        }

    def _failure_attribution_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        if not outcomes:
            return {
                "status": "missing",
                "reason": "no_live_outcomes",
                "sample_count": 0,
                "worst_groups": [],
                "best_groups": [],
                "recovery_focus": [],
            }

        min_samples = int(self.config.min_sample_count)
        group_specs = {
            "symbol": ("row", ("symbol",)),
            "horizon": (
                "metadata",
                ("horizon", "time_horizon", "holding_period", "block_horizon"),
            ),
            "strategy_family": (
                "metadata",
                ("strategy_family", "family", "lane", "entry_setup", "setup"),
            ),
            "market_regime": (
                "metadata",
                ("market_regime", "regime", "regime_label"),
            ),
        }
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

        def group_value(
            row: dict[str, Any],
            metadata: dict[str, Any],
            *,
            source: str,
            keys: tuple[str, ...],
        ) -> str:
            payload = row if source == "row" else metadata
            for key in keys:
                raw = payload.get(key)
                if raw is None:
                    continue
                value = str(raw).strip()
                if value:
                    return value
            return "unknown"

        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            for group_type, (source, keys) in group_specs.items():
                value = group_value(row, metadata, source=source, keys=keys)
                groups.setdefault((group_type, value), []).append(row)

        def summarize_group(group_type: str, group: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
            net_pnls = [_safe_float(row.get("net_pnl")) for row in rows]
            pnl_pct = [_safe_float(row.get("pnl_pct")) for row in rows]
            gross_pnl = sum(_safe_float(row.get("gross_pnl")) for row in rows)
            total_cost = sum(_safe_float(row.get("cost_total")) for row in rows)
            wins = [value for value in net_pnls if value > 0]
            losses = [value for value in net_pnls if value < 0]
            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))
            profit_factor = (
                gross_profit / gross_loss
                if gross_loss > 0
                else (999.0 if gross_profit > 0 else 0.0)
            )
            equity = max(float(self.config.initial_equity), 1.0)
            curve = [equity]
            for pnl in net_pnls:
                equity += pnl
                curve.append(equity)
            _drawdown_cash, max_drawdown_pct = _max_drawdown(curve)
            sample_count = len(rows)
            win_rate = len(wins) / sample_count * 100.0 if sample_count else 0.0
            cost_drag = abs(total_cost) / abs(gross_pnl) * 100.0 if gross_pnl else 0.0
            expectancy = _avg(pnl_pct)
            total_net = sum(net_pnls)
            risk_score = 0.0
            if total_net < 0:
                risk_score += min(abs(total_net) / max(float(self.config.initial_equity), 1.0) * 100.0, 40.0)
            if expectancy < 0:
                risk_score += min(abs(expectancy) * 10.0, 25.0)
            if profit_factor < 1.0:
                risk_score += (1.0 - profit_factor) * 25.0
            if cost_drag >= 50.0:
                risk_score += 10.0
            if sample_count < min_samples:
                risk_score += 5.0
            return {
                "group_type": group_type,
                "group": group,
                "sample_count": sample_count,
                "evidence_quality": "sufficient" if sample_count >= min_samples else "weak",
                "total_net_pnl": round(total_net, 6),
                "expectancy_pct": round(expectancy, 6),
                "win_rate_pct": round(win_rate, 6),
                "profit_factor": round(profit_factor, 6),
                "max_drawdown_pct": round(max_drawdown_pct, 6),
                "cost_drag_pct_of_gross_pnl": round(cost_drag, 6),
                "risk_score": round(max(risk_score, 0.0), 6),
            }

        summaries = [
            summarize_group(group_type, group, rows)
            for (group_type, group), rows in groups.items()
            if group
        ]
        worst_groups = sorted(
            summaries,
            key=lambda row: (
                -_safe_float(row.get("risk_score")),
                _safe_float(row.get("total_net_pnl")),
                row.get("group_type", ""),
                row.get("group", ""),
            ),
        )[:8]
        best_groups = sorted(
            summaries,
            key=lambda row: (
                -_safe_float(row.get("total_net_pnl")),
                -_safe_float(row.get("expectancy_pct")),
                row.get("group_type", ""),
                row.get("group", ""),
            ),
        )[:8]
        recovery_focus = [
            (
                f"{row['group_type']}={row['group']} "
                f"net {row['total_net_pnl']:.2f}, PF {row['profit_factor']:.2f}, "
                f"expectancy {row['expectancy_pct']:.2f}%"
            )
            for row in worst_groups
            if _safe_float(row.get("risk_score")) > 0
        ][:4]
        return {
            "status": "ok",
            "sample_count": len(outcomes),
            "group_count": len(summaries),
            "worst_groups": worst_groups,
            "best_groups": best_groups,
            "recovery_focus": recovery_focus,
        }

    def _data_quality_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        sample_count = len(outcomes)
        invalid_price_count = 0
        stale_count = 0
        upstream_error_count = 0
        missing_cost_count = 0
        fallback_source_count = 0
        missing_metadata_count = 0
        affected_sample_count = 0
        hard_affected_sample_count = 0
        examples: list[dict[str, Any]] = []
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            block_id = str(row.get("block_id") or "")
            symbol = str(row.get("symbol") or "")
            issues: list[str] = []

            source = _json_loads(row.get("source_json"))
            source_status = ""
            if isinstance(source, dict):
                source_status = _clean_key(source.get("status"))
            metadata_status = (
                _clean_key(metadata.get("quote_status"))
                or _clean_key(metadata.get("data_status"))
                or _clean_key(metadata.get("status"))
                or source_status
            )
            source_name = (
                _clean_key(metadata.get("quote_source"))
                or _clean_key(metadata.get("book_source"))
                or _clean_key(metadata.get("source"))
            )

            entry_price_raw = metadata.get("entry_price") or row.get("entry_price")
            exit_price_raw = metadata.get("exit_price") or row.get("exit_price")
            qty_raw = metadata.get("qty") or row.get("qty")
            price_or_qty_present = any(
                value is not None and str(value).strip() != ""
                for value in (entry_price_raw, exit_price_raw, qty_raw)
            )
            entry_price = _safe_float(entry_price_raw)
            exit_price = _safe_float(exit_price_raw)
            qty = _safe_float(qty_raw, default=1.0)
            if price_or_qty_present and (
                entry_price <= 0 or exit_price <= 0 or qty <= 0
            ):
                invalid_price_count += 1
                issues.append("invalid_price_or_qty")

            if (
                metadata_status in {"stale", "expired"}
                or _truthy(metadata.get("quote_stale"))
                or _truthy(metadata.get("stale"))
            ):
                stale_count += 1
                issues.append("stale_source")

            error_message = (
                str(metadata.get("error_message") or "")
                or str(metadata.get("quote_error") or "")
            )
            if metadata_status in {"error", "failed"} or error_message:
                upstream_error_count += 1
                issues.append("upstream_error")

            cost_model_status = _clean_key(metadata.get("cost_model_status"))
            if _safe_float(row.get("cost_total")) <= 0 or cost_model_status in {
                "missing",
                "unknown",
                "error",
            }:
                missing_cost_count += 1
                issues.append("missing_cost")

            if source_name.startswith(("fallback", "stale", "proxy")) or any(
                token in source_name for token in ("fallback", "stale", "proxy")
            ):
                fallback_source_count += 1
                issues.append("fallback_source")

            if not metadata:
                missing_metadata_count += 1
                issues.append("missing_metadata")

            if issues:
                affected_sample_count += 1
            if "invalid_price_or_qty" in issues or "upstream_error" in issues:
                hard_affected_sample_count += 1
            if issues and len(examples) < 8:
                examples.append(
                    {
                        "block_id": block_id,
                        "symbol": symbol,
                        "issues": issues,
                    }
                )

        hard_issue_count = invalid_price_count + upstream_error_count
        soft_issue_count = (
            stale_count
            + missing_cost_count
            + fallback_source_count
            + missing_metadata_count
        )
        issue_count = hard_issue_count + soft_issue_count
        issue_rate = affected_sample_count / sample_count * 100.0 if sample_count else 0.0
        hard_issue_rate = (
            hard_affected_sample_count / sample_count * 100.0 if sample_count else 0.0
        )
        if not sample_count:
            status = "missing"
        elif invalid_price_count:
            status = "fail"
        elif upstream_error_count:
            status = "fail" if hard_issue_rate > 20.0 else "warn"
        elif stale_count or missing_cost_count or fallback_source_count:
            status = "warn" if issue_rate >= 20.0 else "pass"
        else:
            status = "pass"
        return {
            "status": status,
            "sample_count": sample_count,
            "issue_count": issue_count,
            "issue_rate_pct": round(issue_rate, 6),
            "affected_sample_count": affected_sample_count,
            "hard_affected_sample_count": hard_affected_sample_count,
            "hard_issue_rate_pct": round(hard_issue_rate, 6),
            "invalid_price_count": invalid_price_count,
            "stale_count": stale_count,
            "upstream_error_count": upstream_error_count,
            "missing_cost_count": missing_cost_count,
            "fallback_source_count": fallback_source_count,
            "missing_metadata_count": missing_metadata_count,
            "examples": examples,
        }

    def _drawdown_budget_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        initial_equity = max(float(self.config.initial_equity), 1.0)
        drawdown_limit_pct = -abs(float(self.config.max_drawdown_limit_pct))
        equity = initial_equity
        equity_curve = [equity]
        for row in outcomes:
            equity += _safe_float(row.get("net_pnl"))
            equity_curve.append(equity)
        peak_equity = max(equity_curve) if equity_curve else initial_equity
        current_equity = equity_curve[-1] if equity_curve else initial_equity
        _drawdown_cash, max_drawdown_pct = _max_drawdown(equity_curve)
        current_drawdown_pct = (
            (current_equity - peak_equity) / peak_equity * 100.0
            if peak_equity > 0
            else 0.0
        )
        recovery_to_peak_pct = (
            (peak_equity / current_equity - 1.0) * 100.0
            if current_equity > 0 and peak_equity > current_equity
            else 0.0
        )
        limit_abs = abs(drawdown_limit_pct)
        current_abs = abs(min(current_drawdown_pct, 0.0))
        remaining_budget = limit_abs - current_abs
        usage_ratio = current_abs / limit_abs if limit_abs > 0 else 0.0
        if not outcomes:
            status = "missing"
            governor_action = "no_samples"
            risk_multiplier = 0.0
        elif usage_ratio >= 1.0:
            status = "fail"
            governor_action = "risk_off"
            risk_multiplier = 0.0
        elif usage_ratio >= 0.75:
            status = "warn"
            governor_action = "de_risk"
            risk_multiplier = 0.5
        elif usage_ratio >= 0.5:
            status = "pass"
            governor_action = "reduced"
            risk_multiplier = 0.75
        else:
            status = "pass"
            governor_action = "normal"
            risk_multiplier = 1.0
        if max_drawdown_pct < drawdown_limit_pct * 1.5:
            status = "fail"
            governor_action = "halt_new_risk"
            risk_multiplier = 0.0
        return {
            "status": status,
            "sample_count": len(outcomes),
            "initial_equity": round(initial_equity, 6),
            "peak_equity": round(peak_equity, 6),
            "current_equity": round(current_equity, 6),
            "current_drawdown_pct": round(current_drawdown_pct, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "drawdown_limit_pct": round(drawdown_limit_pct, 6),
            "remaining_budget_pct": round(remaining_budget, 6),
            "drawdown_usage_ratio": round(usage_ratio, 6),
            "recovery_to_peak_pct": round(recovery_to_peak_pct, 6),
            "risk_multiplier": round(risk_multiplier, 6),
            "governor_action": governor_action,
        }

    def _cost_simulation_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        sample_count = len(outcomes)
        total_gross_pnl = sum(_safe_float(row.get("gross_pnl")) for row in outcomes)
        total_net_pnl = sum(_safe_float(row.get("net_pnl")) for row in outcomes)
        total_cost = sum(_safe_float(row.get("cost_total")) for row in outcomes)
        recorded_cost_sample_count = 0
        hybrid_cost_sample_count = 0
        estimated_cost_sample_count = 0
        partial_cost_sample_count = 0
        missing_cost_sample_count = 0
        component_totals: dict[str, float] = {}
        present_component_counts: dict[str, int] = {}
        missing_examples: list[dict[str, str]] = []
        cost_rows: list[dict[str, Any]] = []
        cost_groups: dict[tuple[str, str], dict[str, Any]] = {}

        def metadata_label(
            metadata: dict[str, Any],
            *keys: str,
            default: str = "unknown",
        ) -> str:
            for key in keys:
                value = str(metadata.get(key) or "").strip()
                if value:
                    return value
            return default

        def add_cost_group(
            group_type: str,
            group: str,
            *,
            row: dict[str, Any],
            gross_pnl: float,
            net_pnl: float,
            cost_total: float,
        ) -> None:
            if not group:
                return
            key = (group_type, group)
            target = cost_groups.setdefault(
                key,
                {
                    "group_type": group_type,
                    "group": group,
                    "sample_count": 0,
                    "total_gross_pnl": 0.0,
                    "total_net_pnl": 0.0,
                    "total_cost": 0.0,
                    "symbols": set(),
                    "block_ids": [],
                },
            )
            target["sample_count"] += 1
            target["total_gross_pnl"] += gross_pnl
            target["total_net_pnl"] += net_pnl
            target["total_cost"] += cost_total
            if str(row.get("symbol") or ""):
                target["symbols"].add(str(row.get("symbol")))
            if len(target["block_ids"]) < 6 and str(row.get("block_id") or ""):
                target["block_ids"].append(str(row.get("block_id")))

        def add_component(raw_key: Any, raw_value: Any) -> None:
            value = _safe_float(raw_value)
            if value <= 0:
                return
            key = _clean_key(raw_key)
            aliases = {
                "fee": "fees",
                "fees": "fees",
                "commission": "fees",
                "commissions": "fees",
                "tax": "taxes",
                "taxes": "taxes",
                "funding_fee": "funding",
                "funding": "funding",
                "slippage": "slippage",
                "spread": "spread",
                "spread_cost": "spread",
                "book_spread": "spread",
            }
            component = aliases.get(key, key or "unclassified")
            component_totals[component] = component_totals.get(component, 0.0) + value

        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            source = _json_loads(row.get("source_json"))
            if not isinstance(source, dict):
                source = {}
            cost_total = _safe_float(row.get("cost_total"))
            gross_pnl = _safe_float(row.get("gross_pnl"))
            net_pnl = _safe_float(row.get("net_pnl"))
            cost_precision_bucket = _cost_precision_bucket(row, metadata)
            abs_gross_pnl = abs(gross_pnl)
            cost_drag_row = (
                cost_total / abs_gross_pnl * 100.0
                if abs_gross_pnl > 0
                else (999.0 if cost_total > 0 else 0.0)
            )
            horizon = metadata_label(
                metadata,
                "horizon",
                "time_horizon",
                "block_horizon",
                "block_color",
            )
            strategy_family = metadata_label(
                metadata,
                "strategy_family",
                "lane",
                "entry_setup",
                "setup",
                "decision_class",
            )
            cost_rows.append(
                {
                    "block_id": str(row.get("block_id") or ""),
                    "symbol": str(row.get("symbol") or ""),
                    "horizon": horizon,
                    "strategy_family": strategy_family,
                    "gross_pnl": round(gross_pnl, 6),
                    "net_pnl": round(net_pnl, 6),
                    "cost_total": round(cost_total, 6),
                    "cost_precision": cost_precision_bucket,
                    "cost_drag_pct_of_abs_gross_pnl": round(cost_drag_row, 6),
                    "net_negative_after_cost": bool(net_pnl < 0 <= gross_pnl),
                }
            )
            add_cost_group(
                "symbol",
                str(row.get("symbol") or ""),
                row=row,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                cost_total=cost_total,
            )
            add_cost_group(
                "horizon",
                horizon,
                row=row,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                cost_total=cost_total,
            )
            add_cost_group(
                "strategy_family",
                strategy_family,
                row=row,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                cost_total=cost_total,
            )
            cost_model_status = _clean_key(
                metadata.get("cost_model_status") or row.get("cost_model_status")
            )
            for component in _cost_component_presence(row, metadata):
                present_component_counts[component] = (
                    present_component_counts.get(component, 0) + 1
                )
            if cost_precision_bucket == "recorded":
                recorded_cost_sample_count += 1
            elif cost_precision_bucket == "hybrid":
                hybrid_cost_sample_count += 1
            elif cost_precision_bucket == "estimated":
                estimated_cost_sample_count += 1
            elif cost_precision_bucket == "partial":
                partial_cost_sample_count += 1
            else:
                missing_cost_sample_count += 1
                if len(missing_examples) < 8:
                    missing_examples.append(
                        {
                            "block_id": str(row.get("block_id") or ""),
                            "symbol": str(row.get("symbol") or ""),
                            "reason": cost_model_status or "zero_cost_total",
                        }
                    )

            components = (
                metadata.get("cost_components")
                or metadata.get("cost_breakdown")
                or source.get("cost_components")
                or source.get("cost_breakdown")
            )
            components = _json_loads(components)
            before_count = len(component_totals)
            before_total = sum(component_totals.values())
            if isinstance(components, dict):
                for key, value in components.items():
                    add_component(key, value)
            else:
                for key in (
                    "fees",
                    "fee",
                    "taxes",
                    "tax",
                    "funding",
                    "funding_fee",
                    "slippage",
                    "spread",
                    "spread_cost",
                ):
                    if key in metadata:
                        add_component(key, metadata.get(key))
            added_total = sum(component_totals.values()) - before_total
            if cost_total > 0 and added_total <= 0 and len(component_totals) == before_count:
                add_component("unclassified", cost_total)

        gross_basis = abs(total_gross_pnl)
        cost_drag = total_cost / gross_basis * 100.0 if gross_basis > 0 else 0.0
        retention = total_net_pnl / total_gross_pnl * 100.0 if total_gross_pnl > 0 else 0.0
        stressed = {
            f"{multiplier}x": round(total_gross_pnl - total_cost * multiplier, 6)
            for multiplier in (1, 2, 3)
        }
        breakeven_multiplier = (
            total_gross_pnl / total_cost
            if total_gross_pnl > 0 and total_cost > 0
            else (999.0 if total_gross_pnl > 0 else 0.0)
        )
        missing_rate = (
            missing_cost_sample_count / sample_count * 100.0 if sample_count else 0.0
        )
        estimated_rate = (
            estimated_cost_sample_count / sample_count * 100.0 if sample_count else 0.0
        )
        hybrid_rate = (
            hybrid_cost_sample_count / sample_count * 100.0 if sample_count else 0.0
        )
        partial_rate = (
            partial_cost_sample_count / sample_count * 100.0 if sample_count else 0.0
        )
        verified_rate = (
            recorded_cost_sample_count / sample_count * 100.0 if sample_count else 0.0
        )
        usable_rate = (
            (
                recorded_cost_sample_count
                + hybrid_cost_sample_count
                + estimated_cost_sample_count
                + partial_cost_sample_count
            )
            / sample_count
            * 100.0
            if sample_count
            else 0.0
        )
        if not sample_count:
            status = "missing"
        elif missing_rate >= 50.0:
            status = "fail"
        elif total_gross_pnl > 0 and total_net_pnl <= 0:
            status = "fail"
        elif stressed["2x"] < 0 or cost_drag >= 50.0:
            status = "fail"
        elif (
            verified_rate < 60.0
            or missing_cost_sample_count
            or cost_drag >= 25.0
            or stressed["3x"] < 0
        ):
            status = "warn"
        else:
            status = "pass"

        worst_cost_rows = sorted(
            cost_rows,
            key=lambda item: (
                -_safe_float(item.get("cost_drag_pct_of_abs_gross_pnl")),
                -_safe_float(item.get("cost_total")),
                str(item.get("block_id") or ""),
            ),
        )[:8]

        def finalize_group(row: dict[str, Any]) -> dict[str, Any]:
            gross = _safe_float(row.get("total_gross_pnl"))
            net = _safe_float(row.get("total_net_pnl"))
            cost = _safe_float(row.get("total_cost"))
            drag = (
                cost / abs(gross) * 100.0
                if abs(gross) > 0
                else (999.0 if cost > 0 else 0.0)
            )
            return {
                "group_type": row["group_type"],
                "group": row["group"],
                "sample_count": int(row["sample_count"]),
                "total_gross_pnl": round(gross, 6),
                "total_net_pnl": round(net, 6),
                "total_cost": round(cost, 6),
                "cost_drag_pct_of_abs_gross_pnl": round(drag, 6),
                "net_negative_after_cost": bool(net < 0 <= gross),
                "symbols": sorted(row["symbols"])[:12],
                "block_ids": list(row["block_ids"])[:6],
            }

        worst_cost_groups = sorted(
            [finalize_group(row) for row in cost_groups.values()],
            key=lambda item: (
                -_safe_float(item.get("cost_drag_pct_of_abs_gross_pnl")),
                -_safe_float(item.get("total_cost")),
                str(item.get("group_type") or ""),
                str(item.get("group") or ""),
            ),
        )[:12]
        return {
            "status": status,
            "sample_count": sample_count,
            "recorded_cost_sample_count": recorded_cost_sample_count,
            "hybrid_cost_sample_count": hybrid_cost_sample_count,
            "estimated_cost_sample_count": estimated_cost_sample_count,
            "partial_cost_sample_count": partial_cost_sample_count,
            "missing_cost_sample_count": missing_cost_sample_count,
            "cost_precision_counts": {
                "recorded": recorded_cost_sample_count,
                "hybrid": hybrid_cost_sample_count,
                "estimated": estimated_cost_sample_count,
                "partial": partial_cost_sample_count,
                "missing": missing_cost_sample_count,
            },
            "missing_cost_sample_rate_pct": round(missing_rate, 6),
            "hybrid_cost_sample_rate_pct": round(hybrid_rate, 6),
            "estimated_cost_sample_rate_pct": round(estimated_rate, 6),
            "partial_cost_sample_rate_pct": round(partial_rate, 6),
            "cost_precision_verified_rate": round(verified_rate, 6),
            "cost_precision_verified_rate_pct": round(verified_rate, 6),
            "cost_precision_usable_rate_pct": round(usable_rate, 6),
            "total_gross_pnl": round(total_gross_pnl, 6),
            "total_net_pnl": round(total_net_pnl, 6),
            "total_cost": round(total_cost, 6),
            "cost_drag_pct_of_gross_pnl": round(cost_drag, 6),
            "net_retention_pct_of_gross_pnl": round(retention, 6),
            "breakeven_cost_multiplier": round(breakeven_multiplier, 6),
            "stressed_net_pnl_by_cost_multiplier": stressed,
            "cost_by_component": {
                key: round(value, 6)
                for key, value in sorted(component_totals.items())
            },
            "present_cost_component_counts": {
                key: int(value)
                for key, value in sorted(present_component_counts.items())
            },
            "worst_cost_rows": worst_cost_rows,
            "worst_cost_groups": worst_cost_groups,
            "missing_examples": missing_examples,
        }

    def _kelly_sizing_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        sample_count = int(metrics.get("sample_count") or 0)
        min_samples = int(self.config.min_sample_count)
        full_kelly = max(_safe_float(metrics.get("kelly_fraction")), 0.0)
        fractional_kelly = max(_safe_float(metrics.get("fractional_kelly_025")), 0.0)
        risk_of_ruin = _safe_float(metrics.get("risk_of_ruin_pct"), 100.0)
        max_drawdown_pct = _safe_float(metrics.get("max_drawdown_pct"))
        quality_pressure = (
            metrics.get("validation_quality_pressure")
            if isinstance(metrics.get("validation_quality_pressure"), dict)
            else {}
        )
        quality_fail_count = int(quality_pressure.get("fail_count") or 0)
        quality_warn_count = int(quality_pressure.get("warn_count") or 0)
        quality_missing_count = int(quality_pressure.get("missing_count") or 0)
        max_risk_cap = 0.02
        weak_sample_cap = 0.005
        validation_warning_cap = 0.01
        evidence_quality = (
            "missing"
            if not sample_count
            else ("sufficient" if sample_count >= min_samples else "weak")
        )
        drawdown_blocked = max_drawdown_pct < -abs(float(self.config.max_drawdown_limit_pct))
        if not sample_count:
            recommended = 0.0
            cap_reason = "no_samples"
            status = "missing"
        elif full_kelly <= 0:
            recommended = 0.0
            cap_reason = "no_positive_edge"
            status = "fail" if sample_count >= min_samples else "warn"
        elif drawdown_blocked:
            recommended = 0.0
            cap_reason = "mdd_limit"
            status = "fail"
        elif risk_of_ruin > 10.0:
            recommended = 0.0
            cap_reason = "risk_of_ruin_limit"
            status = "fail"
        elif quality_fail_count:
            recommended = 0.0
            cap_reason = "validation_quality_fail"
            status = "fail"
        else:
            active_cap = max_risk_cap if sample_count >= min_samples else weak_sample_cap
            if quality_warn_count or quality_missing_count:
                active_cap = min(active_cap, validation_warning_cap)
            recommended = min(fractional_kelly, active_cap)
            if sample_count < min_samples and fractional_kelly > weak_sample_cap:
                cap_reason = "insufficient_sample_cap"
            elif quality_warn_count and fractional_kelly > active_cap:
                cap_reason = "validation_quality_warning_cap"
            elif quality_missing_count and fractional_kelly > active_cap:
                cap_reason = "validation_quality_missing_cap"
            elif fractional_kelly > max_risk_cap:
                cap_reason = "max_per_block_cap"
            else:
                cap_reason = "fractional_kelly"
            status = (
                "warn"
                if (
                    sample_count < min_samples
                    or risk_of_ruin > 5.0
                    or quality_warn_count
                    or quality_missing_count
                )
                else "pass"
            )
        return {
            "status": status,
            "sample_count": sample_count,
            "min_sample_count": min_samples,
            "evidence_quality": evidence_quality,
            "win_rate_pct": _safe_float(metrics.get("win_rate_pct")),
            "payoff_ratio": _safe_float(metrics.get("payoff_ratio")),
            "expectancy_pct": _safe_float(metrics.get("expectancy_pct")),
            "profit_factor": _safe_float(metrics.get("profit_factor")),
            "full_kelly_fraction": round(full_kelly, 6),
            "fractional_kelly_025": round(fractional_kelly, 6),
            "max_risk_cap_fraction": round(max_risk_cap, 6),
            "weak_sample_cap_fraction": round(weak_sample_cap, 6),
            "validation_quality_warning_cap_fraction": round(
                validation_warning_cap,
                6,
            ),
            "recommended_risk_fraction": round(recommended, 6),
            "recommended_risk_pct": round(recommended * 100.0, 6),
            "cap_reason": cap_reason,
            "risk_of_ruin_pct": round(risk_of_ruin, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "validation_quality_pressure": quality_pressure,
        }

    @staticmethod
    def _validation_quality_pressure(metrics: dict[str, Any]) -> dict[str, Any]:
        checks = [
            ("data_quality", metrics.get("data_quality")),
            ("cost_simulation", metrics.get("cost_simulation")),
            ("stress", metrics.get("stress")),
            ("capacity", metrics.get("capacity")),
            ("regime_scorecards", metrics.get("regime_scorecards")),
            ("correlation_proxy", metrics.get("correlation_proxy")),
            ("factor_exposure", metrics.get("factor_exposure")),
            ("pattern_lab", metrics.get("pattern_lab")),
        ]
        failures: list[str] = []
        warnings: list[str] = []
        missing: list[str] = []
        for source, payload in checks:
            if not isinstance(payload, dict):
                continue
            status = _clean_key(
                payload.get("validation_status") or payload.get("status")
            )
            if status == "fail":
                failures.append(source)
            elif status == "warn":
                warnings.append(source)
            elif status == "missing":
                missing.append(source)
        return {
            "status": "fail" if failures else ("warn" if warnings or missing else "pass"),
            "fail_count": len(failures),
            "warn_count": len(warnings),
            "missing_count": len(missing),
            "failures": failures,
            "warnings": warnings,
            "missing": missing,
        }

    def _ruin_profile_metrics(self, monte_carlo: dict[str, Any]) -> dict[str, Any]:
        iterations = int(monte_carlo.get("iterations") or 0)
        sample_count = int(monte_carlo.get("sample_count") or 0)
        ruin_probability = _safe_float(monte_carlo.get("risk_of_ruin_pct"), 100.0)
        ruin_events = int(monte_carlo.get("ruin_event_count") or 0)
        if not sample_count or not iterations:
            status = "missing"
            severity = "critical"
            governor_action = "no_samples"
        elif ruin_probability >= 20.0:
            status = "fail"
            severity = "critical"
            governor_action = "halt_new_risk"
        elif ruin_probability >= 10.0:
            status = "fail"
            severity = "high"
            governor_action = "risk_off"
        elif ruin_probability >= 5.0:
            status = "warn"
            severity = "medium"
            governor_action = "de_risk"
        elif ruin_probability > 0:
            status = "warn"
            severity = "low"
            governor_action = "de_risk"
        else:
            status = "pass"
            severity = "low"
            governor_action = "normal"
        return {
            "status": status,
            "sample_count": sample_count,
            "monte_carlo_iterations": iterations,
            "ruin_drawdown_pct": float(self.config.ruin_drawdown_pct),
            "risk_of_ruin_pct": round(ruin_probability, 6),
            "ruin_event_count": ruin_events,
            "earliest_trade_index_to_ruin": int(
                monte_carlo.get("earliest_trade_index_to_ruin") or 0
            ),
            "median_trade_index_to_ruin": int(
                monte_carlo.get("median_trade_index_to_ruin") or 0
            ),
            "ruin_severity": severity,
            "governor_action": governor_action,
        }

    def _risk_adjusted_performance_metrics(
        self,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        sample_count = int(metrics.get("sample_count") or 0)
        min_samples = int(self.config.min_sample_count)
        sample_adequacy = (
            "missing"
            if not sample_count
            else ("sufficient" if sample_count >= min_samples else "weak")
        )
        returns_pct = [
            _safe_float(value)
            for value in list(metrics.get("returns_pct") or [])
        ]
        loss_returns_pct = [value for value in returns_pct if value < 0]
        volatility_pct = _stddev(returns_pct)
        downside_deviation_pct = (
            _stddev(loss_returns_pct)
            if len(loss_returns_pct) > 1
            else abs(loss_returns_pct[0])
            if loss_returns_pct
            else 0.0
        )
        sharpe = _safe_float(metrics.get("sharpe_ratio"))
        sortino = _safe_float(metrics.get("sortino_ratio"))
        calmar = _safe_float(metrics.get("calmar_ratio"))
        total_return = _safe_float(metrics.get("total_return_pct"))
        expectancy = _safe_float(metrics.get("expectancy_pct"))
        max_drawdown_pct = _safe_float(metrics.get("max_drawdown_pct"))
        return_to_drawdown = calmar
        if not sample_count:
            status = "missing"
            quality_grade = "F"
            primary_risk_flag = "negative_edge"
        elif total_return <= 0 or expectancy <= 0:
            status = "fail"
            quality_grade = "F"
            primary_risk_flag = "negative_edge"
        elif (
            sharpe >= float(self.config.sharpe_min)
            and sortino >= float(self.config.sortino_min)
            and calmar >= float(self.config.calmar_min)
        ):
            status = "pass"
            if sharpe >= 1.0 and sortino >= 1.5 and calmar >= 1.0:
                quality_grade = "A"
            elif sortino >= 1.0 and calmar >= 0.75:
                quality_grade = "B"
            else:
                quality_grade = "C"
            primary_risk_flag = "none"
            if sample_adequacy == "weak":
                status = "warn"
                quality_grade = "C"
                primary_risk_flag = "sample_adequacy"
        else:
            status = "warn"
            quality_grade = "D"
            if calmar < float(self.config.calmar_min):
                primary_risk_flag = "drawdown_efficiency"
            elif sortino < float(self.config.sortino_min):
                primary_risk_flag = "downside_volatility"
            elif sharpe < float(self.config.sharpe_min):
                primary_risk_flag = "total_volatility"
            else:
                primary_risk_flag = "none"
        return {
            "status": status,
            "sample_count": sample_count,
            "min_sample_count": min_samples,
            "sample_adequacy": sample_adequacy,
            "total_return_pct": round(total_return, 6),
            "expectancy_pct": round(expectancy, 6),
            "volatility_pct": round(volatility_pct, 6),
            "downside_deviation_pct": round(downside_deviation_pct, 6),
            "sharpe_ratio": round(sharpe, 6),
            "sortino_ratio": round(sortino, 6),
            "calmar_ratio": round(calmar, 6),
            "return_to_drawdown_ratio": round(return_to_drawdown, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "quality_grade": quality_grade,
            "primary_risk_flag": primary_risk_flag,
            "thresholds": {
                "sharpe_min": float(self.config.sharpe_min),
                "sortino_min": float(self.config.sortino_min),
                "calmar_min": float(self.config.calmar_min),
            },
        }

    def _profitability_quality_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        sample_count = int(metrics.get("sample_count") or 0)
        min_samples = int(self.config.min_sample_count)
        sample_adequacy = (
            "missing"
            if not sample_count
            else ("sufficient" if sample_count >= min_samples else "weak")
        )
        total_net_pnl = _safe_float(metrics.get("total_net_pnl"))
        profit_factor = _safe_float(metrics.get("profit_factor"))
        win_rate = _safe_float(metrics.get("win_rate_pct"))
        payoff_ratio = _safe_float(metrics.get("payoff_ratio"))
        expectancy = _safe_float(metrics.get("expectancy_pct"))
        gross_loss = 0.0
        gross_profit = 0.0
        if profit_factor > 0 and total_net_pnl:
            if profit_factor == 999.0:
                gross_profit = max(total_net_pnl, 0.0)
            else:
                gross_loss = abs(total_net_pnl / (profit_factor - 1.0)) if profit_factor != 1.0 else 0.0
                gross_profit = gross_loss * profit_factor
        loss_absorption = profit_factor if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        win_count = round(sample_count * win_rate / 100.0)
        loss_count = max(sample_count - int(win_count), 0)
        average_win = gross_profit / win_count if win_count else 0.0
        average_loss = gross_loss / loss_count if loss_count else 0.0
        if not sample_count:
            status = "missing"
            edge_grade = "none"
        elif total_net_pnl <= 0 or profit_factor < 1.0:
            status = "fail"
            edge_grade = "negative"
        elif profit_factor >= 2.0:
            status = "pass"
            edge_grade = "excellent"
        elif profit_factor >= float(self.config.profit_factor_good):
            status = "pass"
            edge_grade = "good"
        elif profit_factor >= float(self.config.profit_factor_min):
            status = "warn"
            edge_grade = "thin"
        else:
            status = "fail"
            edge_grade = "weak"
        if status == "pass" and sample_adequacy == "weak":
            status = "warn"
            edge_grade = "thin_sample"
        return {
            "status": status,
            "sample_count": sample_count,
            "min_sample_count": min_samples,
            "sample_adequacy": sample_adequacy,
            "total_net_pnl": round(total_net_pnl, 6),
            "gross_profit": round(gross_profit, 6),
            "gross_loss": round(gross_loss, 6),
            "profit_factor": round(profit_factor, 6),
            "loss_absorption_ratio": round(loss_absorption, 6),
            "win_rate_pct": round(win_rate, 6),
            "payoff_ratio": round(payoff_ratio, 6),
            "expectancy_pct": round(expectancy, 6),
            "average_win": round(average_win, 6),
            "average_loss": round(average_loss, 6),
            "edge_grade": edge_grade,
            "thresholds": {
                "profit_factor_min": float(self.config.profit_factor_min),
                "profit_factor_good": float(self.config.profit_factor_good),
            },
        }

    def _recovery_profile_metrics(
        self,
        outcomes: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        sample_count = len(outcomes)
        min_samples = int(self.config.min_sample_count)
        sample_adequacy = (
            "missing"
            if not sample_count
            else ("sufficient" if sample_count >= min_samples else "weak")
        )
        initial_equity = max(float(self.config.initial_equity), 1.0)
        equity = initial_equity
        equity_curve = [equity]
        for row in outcomes:
            equity += _safe_float(row.get("net_pnl"))
            equity_curve.append(equity)
        max_drawdown_cash, max_drawdown_pct = _max_drawdown(equity_curve)
        recovery_factor = _safe_float(metrics.get("recovery_factor"))
        peak_before_trough = initial_equity
        trough_equity = initial_equity
        trough_trade_index = 0
        recovery_trade_index = 0
        max_drawdown_seen = 0.0
        running_peak = initial_equity
        running_peak_index = 0
        peak_index_before_trough = 0
        for index, value in enumerate(equity_curve):
            if value > running_peak:
                running_peak = value
                running_peak_index = index
            drawdown_cash = value - running_peak
            if drawdown_cash < max_drawdown_seen:
                max_drawdown_seen = drawdown_cash
                trough_equity = value
                trough_trade_index = index
                peak_before_trough = running_peak
                peak_index_before_trough = running_peak_index
        if max_drawdown_seen < 0:
            for index in range(trough_trade_index + 1, len(equity_curve)):
                if equity_curve[index] >= peak_before_trough:
                    recovery_trade_index = index
                    break
        recovered = bool(max_drawdown_seen < 0 and recovery_trade_index)
        recovery_trade_count = (
            recovery_trade_index - trough_trade_index if recovered else None
        )
        required_gain = (
            (peak_before_trough / trough_equity - 1.0) * 100.0
            if trough_equity > 0 and peak_before_trough > trough_equity
            else 0.0
        )
        if not outcomes:
            status = "missing"
            recovery_state = "no_samples"
        elif recovery_factor >= float(self.config.recovery_factor_min) and recovered:
            status = "pass"
            recovery_state = "recovered"
        elif recovery_factor > 0 and recovered:
            status = "warn"
            recovery_state = "recovered_but_thin"
        elif recovery_factor > 0:
            status = "warn"
            recovery_state = "not_recovered"
        else:
            status = "fail"
            recovery_state = "negative_or_flat"
        if status == "pass" and sample_adequacy == "weak":
            status = "warn"
            recovery_state = "recovered_thin_sample"
        return {
            "status": status,
            "sample_count": sample_count,
            "min_sample_count": min_samples,
            "sample_adequacy": sample_adequacy,
            "initial_equity": round(initial_equity, 6),
            "current_equity": round(equity_curve[-1] if equity_curve else initial_equity, 6),
            "peak_before_trough": round(peak_before_trough, 6),
            "peak_trade_index_before_trough": peak_index_before_trough,
            "trough_equity": round(trough_equity, 6),
            "trough_trade_index": trough_trade_index,
            "recovery_trade_index": recovery_trade_index,
            "recovery_trade_count": recovery_trade_count,
            "recovered_from_max_drawdown": recovered,
            "required_gain_from_trough_pct": round(required_gain, 6),
            "max_drawdown_cash": round(max_drawdown_cash, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "recovery_factor": round(recovery_factor, 6),
            "recovery_state": recovery_state,
            "thresholds": {
                "recovery_factor_min": float(self.config.recovery_factor_min),
            },
        }

    def _live_forward_pattern_proxy_metrics(
        self,
        *,
        venue: str,
        outcomes: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_venue = _clean_key(venue) or "all"
        sample_count = int(metrics.get("sample_count") or 0)
        source_scope = (
            "kis_live_forward_proxy"
            if normalized_venue == "kis"
            else f"{normalized_venue}_live_forward_proxy"
        )
        if sample_count <= 0:
            return {
                "status": "missing",
                "source_scope": source_scope,
                "db_path": "",
                "reason": "live_forward_samples_missing",
                "sample_count": 0,
            }
        symbols = sorted(
            {
                str(row.get("symbol") or "").strip()
                for row in outcomes
                if str(row.get("symbol") or "").strip()
            }
        )
        return {
            "status": "proxy",
            "source_scope": source_scope,
            "db_path": "",
            "sample_count": sample_count,
            "symbol_count": len(symbols),
            "symbols": symbols[:50],
            "proxy_expectancy_pct": _safe_float(metrics.get("expectancy_pct")),
            "proxy_profit_factor": _safe_float(metrics.get("profit_factor")),
            "proxy_win_rate_pct": _safe_float(metrics.get("win_rate_pct")),
            "proxy_max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
            "proxy_total_return_pct": _safe_float(metrics.get("total_return_pct")),
            "note": (
                "Live-forward samples are execution evidence, not a replacement "
                "for venue-native rolling walk-forward or out-of-sample research."
            ),
        }

    def _pattern_lab_metrics(
        self,
        *,
        venue: str = "",
        outcomes: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_venue = _clean_key(venue)
        source_scope = "crypto_pattern_lab"
        path = Path(self.config.crypto_pattern_lab_db_path)
        missing_reason = "crypto_pattern_lab_db_missing"
        if normalized_venue == "kis":
            source_scope = "kr_equity_pattern_lab"
            path = Path(self.config.kr_equity_pattern_lab_db_path)
            missing_reason = "kr_equity_pattern_lab_db_missing"
            if not path.exists():
                return self._live_forward_pattern_proxy_metrics(
                    venue=normalized_venue,
                    outcomes=outcomes or [],
                    metrics=metrics or {},
                )
        if normalized_venue and normalized_venue != "binance":
            if normalized_venue != "kis":
                return self._live_forward_pattern_proxy_metrics(
                    venue=normalized_venue,
                    outcomes=outcomes or [],
                    metrics=metrics or {},
                )
        if not path.exists():
            return {
                "status": "missing",
                "db_path": str(path),
                "source_scope": source_scope,
                "reason": missing_reason,
            }
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                table = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'optimized_strategy_sets'
                    """
                ).fetchone()
                if table is None:
                    return {
                        "status": "missing",
                        "db_path": str(path),
                        "source_scope": source_scope,
                        "reason": "optimized_strategy_sets_missing",
                    }
                rows = conn.execute(
                    """
                    SELECT
                        set_id, symbol, interval, family, direction, status,
                        objective_score, in_sample_expectancy_r,
                        out_of_sample_trade_count, out_of_sample_expectancy_r,
                        out_of_sample_profit_factor,
                        out_of_sample_max_drawdown_r, overfit_risk,
                        walk_forward_quality_json, promoted_at
                    FROM optimized_strategy_sets
                    ORDER BY promoted_at DESC, objective_score DESC
                    LIMIT 500
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            return {
                "status": "error",
                "db_path": str(path),
                "source_scope": source_scope,
                "error_message": str(exc),
            }
        if not rows:
            return {
                "status": "missing",
                "db_path": str(path),
                "source_scope": source_scope,
                "reason": "optimized_strategy_sets_empty",
            }

        items = [dict(row) for row in rows]
        active = [row for row in items if str(row.get("status") or "") == "active"]
        rejected = [
            row for row in items if str(row.get("status") or "") == "rejected"
        ]
        passed = 0
        high_overfit = 0
        low_overfit = 0
        unknown_overfit = 0
        active_high_overfit = 0
        active_unknown_overfit = 0
        oos_trade_counts: list[int] = []
        oos_expectancies: list[float] = []
        oos_profit_factors: list[float] = []
        oos_drawdowns: list[float] = []
        train_test_gaps: list[float] = []
        walk_forward_set_count = 0
        active_walk_forward_set_count = 0
        walk_forward_window_count = 0
        passed_walk_forward_window_count = 0
        failed_reasons: dict[str, int] = {}

        def quality_int(payload: dict[str, Any], *keys: str) -> int:
            for key in keys:
                raw = payload.get(key)
                if raw is None:
                    continue
                try:
                    return max(int(raw), 0)
                except (TypeError, ValueError):
                    continue
            return 0

        def window_counts(payload: dict[str, Any]) -> tuple[int, int]:
            windows = payload.get("windows")
            if isinstance(windows, list):
                total = len(windows)
                passed_windows = sum(
                    1
                    for window in windows
                    if isinstance(window, dict) and bool(window.get("passed"))
                )
                return total, passed_windows
            total = quality_int(payload, "window_count", "total_windows")
            passed_windows = quality_int(
                payload,
                "passed_window_count",
                "passing_window_count",
                "passed_windows",
            )
            return total, min(passed_windows, total) if total else 0

        for row in items:
            quality = _json_loads(row.get("walk_forward_quality_json"))
            if not isinstance(quality, dict):
                quality = {}
            row_status = str(row.get("status") or "")
            is_active = row_status == "active"
            if bool(quality.get("passed")):
                passed += 1
            row_window_count, row_passed_windows = window_counts(quality)
            if row_window_count > 0:
                walk_forward_set_count += 1
                walk_forward_window_count += row_window_count
                passed_walk_forward_window_count += row_passed_windows
                if is_active:
                    active_walk_forward_set_count += 1
            overfit = _clean_key(row.get("overfit_risk"))
            if overfit == "low":
                low_overfit += 1
            elif overfit in {"", "unknown", "missing", "n/a", "none"}:
                unknown_overfit += 1
                if is_active:
                    active_unknown_overfit += 1
            if overfit == "high":
                if is_active:
                    active_high_overfit += 1
            if overfit == "high" or row_status == "rejected":
                high_overfit += 1
            for reason in list(quality.get("reasons") or []):
                key = _clean_key(reason)
                if key:
                    failed_reasons[key] = failed_reasons.get(key, 0) + 1

            oos_trades = int(row.get("out_of_sample_trade_count") or 0)
            oos_expectancy = _safe_float(row.get("out_of_sample_expectancy_r"))
            oos_pf = _safe_float(row.get("out_of_sample_profit_factor"))
            oos_dd = _safe_float(row.get("out_of_sample_max_drawdown_r"))
            in_expectancy = _safe_float(row.get("in_sample_expectancy_r"))
            if oos_trades > 0:
                oos_trade_counts.append(oos_trades)
                oos_expectancies.append(oos_expectancy)
                oos_profit_factors.append(oos_pf)
                oos_drawdowns.append(oos_dd)
                train_test_gaps.append(max(in_expectancy - oos_expectancy, 0.0))

        total = len(items)
        missing_oos_count = total - len(oos_trade_counts)
        oos_coverage_rate = len(oos_trade_counts) / total * 100.0 if total else 0.0
        active_oos_count = sum(
            1
            for row in active
            if int(row.get("out_of_sample_trade_count") or 0) > 0
        )
        active_missing_oos_count = len(active) - active_oos_count
        active_oos_coverage_rate = (
            active_oos_count / len(active) * 100.0 if active else 0.0
        )
        missing_walk_forward_count = total - walk_forward_set_count
        active_missing_walk_forward_count = (
            len(active) - active_walk_forward_set_count
        )
        walk_forward_coverage_rate = (
            walk_forward_set_count / total * 100.0 if total else 0.0
        )
        active_walk_forward_coverage_rate = (
            active_walk_forward_set_count / len(active) * 100.0 if active else 0.0
        )
        walk_forward_window_pass_rate = (
            passed_walk_forward_window_count / walk_forward_window_count * 100.0
            if walk_forward_window_count
            else 0.0
        )
        validation_reasons: list[str] = []
        if active:
            if active_missing_oos_count:
                validation_reasons.append("active_out_of_sample_missing")
            if active_missing_walk_forward_count:
                validation_reasons.append("active_walk_forward_windows_missing")
            if active_unknown_overfit:
                validation_reasons.append("active_overfit_unknown")
            if active_high_overfit:
                validation_reasons.append("active_overfit_high")
        validation_status = (
            "fail"
            if validation_reasons
            else (
                "warn"
                if rejected
                or high_overfit
                or (active and active_walk_forward_coverage_rate < 100.0)
                or (walk_forward_set_count and walk_forward_window_pass_rate < 70.0)
                else "pass"
            )
        )
        top_failed_reasons = [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                failed_reasons.items(),
                key=lambda item: (-int(item[1]), item[0]),
            )[:8]
        ]
        repair_priorities = _pattern_repair_priorities(
            failed_reasons,
            active_set_count=len(active),
            rejected_set_count=len(rejected),
        )
        return {
            "status": "ok",
            "validation_status": validation_status,
            "validation_reasons": validation_reasons,
            "db_path": str(path),
            "source_scope": source_scope,
            "optimized_set_count": total,
            "active_set_count": len(active),
            "rejected_set_count": len(rejected),
            "walk_forward_passed_count": passed,
            "walk_forward_pass_rate_pct": round(passed / total * 100.0, 6),
            "walk_forward_set_count": walk_forward_set_count,
            "missing_walk_forward_set_count": missing_walk_forward_count,
            "walk_forward_coverage_rate_pct": round(
                walk_forward_coverage_rate,
                6,
            ),
            "active_walk_forward_set_count": active_walk_forward_set_count,
            "active_missing_walk_forward_set_count": (
                active_missing_walk_forward_count
            ),
            "active_walk_forward_coverage_rate_pct": round(
                active_walk_forward_coverage_rate,
                6,
            ),
            "walk_forward_window_count": walk_forward_window_count,
            "passed_walk_forward_window_count": passed_walk_forward_window_count,
            "walk_forward_window_pass_rate_pct": round(
                walk_forward_window_pass_rate,
                6,
            ),
            "low_overfit_count": low_overfit,
            "high_overfit_count": high_overfit,
            "unknown_overfit_count": unknown_overfit,
            "active_high_overfit_count": active_high_overfit,
            "active_unknown_overfit_count": active_unknown_overfit,
            "missing_out_of_sample_set_count": missing_oos_count,
            "out_of_sample_coverage_rate_pct": round(oos_coverage_rate, 6),
            "active_missing_out_of_sample_set_count": active_missing_oos_count,
            "active_out_of_sample_coverage_rate_pct": round(
                active_oos_coverage_rate,
                6,
            ),
            "out_of_sample_total_trade_count": sum(oos_trade_counts),
            "avg_out_of_sample_expectancy_r": round(_avg(oos_expectancies), 6),
            "worst_out_of_sample_expectancy_r": round(
                min(oos_expectancies),
                6,
            )
            if oos_expectancies
            else 0.0,
            "min_out_of_sample_profit_factor": round(
                min(oos_profit_factors),
                6,
            )
            if oos_profit_factors
            else 0.0,
            "worst_out_of_sample_max_drawdown_r": round(min(oos_drawdowns), 6)
            if oos_drawdowns
            else 0.0,
            "avg_train_test_expectancy_gap_r": round(_avg(train_test_gaps), 6),
            "top_failed_reasons": top_failed_reasons,
            "repair_priorities": repair_priorities,
            "failed_reasons": dict(sorted(failed_reasons.items())),
        }

    def _regime_scorecards(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            regime = (
                _clean_key(metadata.get("regime"))
                or _clean_key(metadata.get("market_regime"))
                or _clean_key(metadata.get("regime_label"))
            )
            if not regime:
                continue
            grouped.setdefault(regime, []).append(row)
        scorecards: list[dict[str, Any]] = []
        for regime, rows in sorted(grouped.items()):
            net_pnls = [_safe_float(row.get("net_pnl")) for row in rows]
            pnl_pct_values = [_safe_float(row.get("pnl_pct")) for row in rows]
            wins = [value for value in net_pnls if value > 0]
            losses = [value for value in net_pnls if value < 0]
            gross_loss = abs(sum(losses))
            profit_factor = (
                sum(wins) / gross_loss
                if gross_loss > 0
                else (999.0 if wins else 0.0)
            )
            scorecards.append(
                {
                    "regime": regime,
                    "sample_count": len(rows),
                    "expectancy_pct": round(_avg(pnl_pct_values), 6),
                    "profit_factor": round(profit_factor, 6),
                    "win_rate_pct": round(len(wins) / len(rows) * 100.0, 6)
                    if rows
                    else 0.0,
                }
            )
        worst_expectancy = min(
            [row["expectancy_pct"] for row in scorecards],
            default=0.0,
        )
        best = max(
            scorecards,
            key=lambda row: _safe_float(row.get("expectancy_pct")),
            default={},
        )
        worst = min(
            scorecards,
            key=lambda row: _safe_float(row.get("expectancy_pct")),
            default={},
        )
        negative_regime_count = sum(
            1 for row in scorecards if _safe_float(row.get("expectancy_pct")) < 0
        )
        covered = sum(int(row["sample_count"]) for row in scorecards)
        coverage_rate = covered / len(outcomes) * 100.0 if outcomes else 0.0
        if not scorecards:
            status = "missing"
        elif coverage_rate < 50.0:
            status = "warn"
        elif negative_regime_count > 0:
            status = "warn"
        elif len(scorecards) < 2:
            status = "warn"
        else:
            status = "pass"
        return {
            "status": status,
            "regime_count": len(scorecards),
            "covered_sample_count": covered,
            "regime_coverage_rate_pct": round(coverage_rate, 6),
            "best_regime": str(best.get("regime") or ""),
            "worst_regime": str(worst.get("regime") or ""),
            "negative_regime_count": negative_regime_count,
            "worst_expectancy_pct": round(worst_expectancy, 6),
            "scorecards": scorecards,
        }

    def _canonical_lane_key(
        self,
        row: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        venue = _clean_key(row.get("venue"))
        lane = _clean_key(metadata.get("lane"))
        market = _clean_key(metadata.get("market"))
        side = _clean_key(metadata.get("side"))
        horizon = _clean_key(
            metadata.get("horizon")
            or metadata.get("time_horizon")
            or metadata.get("block_horizon")
        )
        asset_type = _clean_key(metadata.get("asset_type"))
        name = str(metadata.get("name") or metadata.get("symbol_name") or "")
        if venue == "kis":
            upper_name = name.upper()
            horizon_alias = {
                "core": "core_etf",
                "coreetf": "core_etf",
                "core_etf": "core_etf",
                "etf": "core_etf",
            }.get(horizon, horizon)
            if (
                asset_type in {"etf", "etn"}
                or horizon_alias == "core_etf"
                or " ETF" in f" {upper_name}"
                or " ETN" in f" {upper_name}"
                or any(
                    upper_name.startswith(prefix)
                    for prefix in KIS_ETF_NAME_PREFIXES
                )
            ):
                return "core_etf"
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
            return horizon_aliases.get(horizon_alias, horizon_alias or "unknown")
        if venue == "binance":
            if lane in {
                "futures:long",
                "futures_long",
                "futures_long_perp",
                "perp_long",
            }:
                return "futures_long"
            if lane in {
                "futures:short",
                "futures_short",
                "futures_short_perp",
                "perp_short",
            }:
                return "futures_short"
            if lane == "volatile_attack":
                return "volatile_attack"
            if market == "upbit_spot" or lane in {"upbit_spot", "upbit_spot_long"}:
                return "upbit_spot"
            if market == "spot" or lane in {"spot", "spot_long"}:
                return "spot"
            if market in {"futures", "perp", "perpetual"}:
                if side == "short":
                    return "futures_short"
                if side == "long":
                    return "futures_long"
                return "futures"
            if lane:
                return lane
            return market or side or horizon or "unknown"
        return lane or horizon or market or "unknown"

    def _lane_scorecards(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            lane = self._canonical_lane_key(row, metadata)
            if not lane or lane == "unknown":
                continue
            grouped.setdefault(lane, []).append(row)

        min_samples = int(self.config.min_sample_count)
        scorecards: list[dict[str, Any]] = []
        weak_lanes: list[str] = []
        scale_candidate_lanes: list[str] = []
        qualified_lanes: list[str] = []
        insufficient_lanes: list[str] = []
        cost_evidence_weak_lanes: list[str] = []
        entry_quality_weak_lanes: list[str] = []
        validation_evidence_weak_lanes: list[str] = []
        validation_repair_weak_lanes: list[str] = []
        lane_actions: dict[str, dict[str, Any]] = {}

        for lane, rows in sorted(grouped.items()):
            metadata_by_id = {
                id(row): self._metadata_for_outcome(row)
                for row in rows
            }
            net_pnls = [_safe_float(row.get("net_pnl")) for row in rows]
            pnl_pct_values = [_safe_float(row.get("pnl_pct")) for row in rows]
            gross_pnl = sum(_safe_float(row.get("gross_pnl")) for row in rows)
            total_cost = sum(_safe_float(row.get("cost_total")) for row in rows)
            wins = [value for value in net_pnls if value > 0]
            losses = [value for value in net_pnls if value < 0]
            gross_loss = abs(sum(losses))
            profit_factor = (
                sum(wins) / gross_loss
                if gross_loss > 0
                else (999.0 if wins else 0.0)
            )
            cumulative_pct = 0.0
            peak_pct = 0.0
            max_drawdown_pct = 0.0
            for value in pnl_pct_values:
                cumulative_pct += value
                peak_pct = max(peak_pct, cumulative_pct)
                max_drawdown_pct = min(max_drawdown_pct, cumulative_pct - peak_pct)
            cumulative_return_pct = sum(pnl_pct_values)
            lane_monte_carlo = self._monte_carlo(pnl_pct_values)
            risk_of_ruin_pct = _safe_float(
                lane_monte_carlo.get("risk_of_ruin_pct"),
                100.0,
            )
            recovery_factor = (
                cumulative_return_pct / abs(max_drawdown_pct)
                if max_drawdown_pct < 0
                else (999.0 if cumulative_return_pct > 0 else 0.0)
            )
            cost_drag = (
                total_cost / abs(gross_pnl) * 100.0
                if abs(gross_pnl) > 0
                else (999.0 if total_cost > 0 else 0.0)
            )
            cost_buckets = [
                _cost_precision_bucket(row, metadata_by_id.get(id(row), {}))
                for row in rows
            ]
            recorded_cost_count = sum(
                1 for bucket in cost_buckets if bucket == "recorded"
            )
            hybrid_cost_count = sum(
                1 for bucket in cost_buckets if bucket == "hybrid"
            )
            estimated_cost_count = sum(
                1 for bucket in cost_buckets if bucket == "estimated"
            )
            partial_cost_count = sum(
                1 for bucket in cost_buckets if bucket == "partial"
            )
            missing_cost_count = (
                len(cost_buckets)
                - recorded_cost_count
                - hybrid_cost_count
                - estimated_cost_count
                - partial_cost_count
            )
            cost_precision_counts = {
                "recorded": recorded_cost_count,
                "hybrid": hybrid_cost_count,
                "estimated": estimated_cost_count,
                "partial": partial_cost_count,
                "missing": missing_cost_count,
            }
            cost_precision_verified_rate = (
                recorded_cost_count / len(cost_buckets) * 100.0
                if cost_buckets
                else 0.0
            )
            cost_evidence_weak = (
                len(cost_buckets) >= min_samples
                and cost_precision_verified_rate < 60.0
            )
            validation_evidence_profile = _lane_validation_evidence_profile(
                rows,
                metadata_by_id=metadata_by_id,
                min_samples_for_grade=min_samples,
            )
            validation_evidence_weak = bool(
                validation_evidence_profile["scale_blocked_by_validation_evidence"]
            )
            entry_quality_scores: list[float] = []
            bad_entry_quality_count = 0
            entry_quality_label_counts: dict[str, int] = {}
            bad_entry_quality_label_counts: dict[str, int] = {}
            good_entry_quality_label_counts: dict[str, int] = {}
            validation_repair_enforced_count = 0
            validation_repair_scale_up_blocked_count = 0
            validation_repair_waiting_entry_count = 0
            validation_repair_rejected_count = 0
            validation_repair_budget_multipliers: list[float] = []
            validation_repair_action_counts: dict[str, int] = {}
            validation_repair_adjustment_reason_counts: dict[str, int] = {}
            validation_evidence_required_evidence_counts: dict[str, int] = {}
            validation_evidence_required_check_counts: dict[str, int] = {}
            validation_evidence_pass_hook_counts: dict[str, int] = {}
            validation_evidence_pass_gap_counts: dict[str, int] = {}
            validation_evidence_pass_criteria_counts: dict[str, int] = {}
            validation_evidence_artifact_counts: dict[str, int] = {}

            def add_entry_quality_label(
                counts: dict[str, int],
                raw_label: Any,
            ) -> None:
                label = str(raw_label or "").strip()[:80]
                if not label:
                    return
                counts[label] = counts.get(label, 0) + 1

            def compact_entry_quality_counts(
                counts: dict[str, int],
            ) -> dict[str, int]:
                return {
                    label: count
                    for label, count in sorted(
                        counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:8]
                    if count > 0
                }

            def dominant_entry_quality_label(counts: dict[str, int]) -> str:
                compact = compact_entry_quality_counts(counts)
                return next(iter(compact), "")

            def add_compact_list_counts(
                counts: dict[str, int],
                raw_values: Any,
                *,
                limit: int = 160,
            ) -> None:
                values = (
                    list(raw_values)
                    if isinstance(raw_values, (list, tuple))
                    else []
                )
                for raw_value in values:
                    label = str(raw_value or "").strip()[:limit]
                    if label:
                        counts[label] = counts.get(label, 0) + 1

            def compact_count_keys(
                counts: dict[str, int],
                *,
                limit: int = 8,
            ) -> list[str]:
                return [
                    label
                    for label, count in sorted(
                        counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:limit]
                    if count > 0
                ]

            for row in rows:
                metadata = metadata_by_id.get(id(row), {})
                validation_evidence = (
                    metadata.get("validation_evidence")
                    if isinstance(metadata.get("validation_evidence"), dict)
                    else {}
                )
                if validation_evidence:
                    add_compact_list_counts(
                        validation_evidence_required_evidence_counts,
                        validation_evidence.get("required_evidence"),
                    )
                    add_compact_list_counts(
                        validation_evidence_required_check_counts,
                        validation_evidence.get("required_checks"),
                    )
                    add_compact_list_counts(
                        validation_evidence_pass_hook_counts,
                        validation_evidence.get("pass_collection_hooks"),
                    )
                    add_compact_list_counts(
                        validation_evidence_pass_gap_counts,
                        validation_evidence.get("pass_current_gaps"),
                    )
                    add_compact_list_counts(
                        validation_evidence_pass_criteria_counts,
                        validation_evidence.get("pass_criteria"),
                    )
                    add_compact_list_counts(
                        validation_evidence_artifact_counts,
                        validation_evidence.get("verification_artifacts"),
                    )
                repair_enforcement = (
                    metadata.get("validation_repair_enforcement")
                    if isinstance(metadata.get("validation_repair_enforcement"), dict)
                    else {}
                )
                if repair_enforcement:
                    validation_repair_enforced_count += 1
                    if bool(repair_enforcement.get("scale_up_blocked")):
                        validation_repair_scale_up_blocked_count += 1
                    if bool(repair_enforcement.get("waiting_entry_required")):
                        validation_repair_waiting_entry_count += 1
                    if bool(repair_enforcement.get("rejected")):
                        validation_repair_rejected_count += 1
                    budget_multiplier = _safe_float(
                        repair_enforcement.get("budget_multiplier")
                    )
                    if budget_multiplier > 0:
                        validation_repair_budget_multipliers.append(
                            budget_multiplier
                        )
                    for raw_action_id in list(
                        repair_enforcement.get("repair_action_ids") or []
                    )[:8]:
                        action_id = str(raw_action_id or "").strip()[:160]
                        if action_id:
                            validation_repair_action_counts[action_id] = (
                                validation_repair_action_counts.get(action_id, 0)
                                + 1
                            )
                    for raw_adjustment in list(
                        repair_enforcement.get("adjustments") or []
                    )[:8]:
                        if not isinstance(raw_adjustment, dict):
                            continue
                        reason = str(raw_adjustment.get("reason") or "").strip()[:160]
                        if reason:
                            validation_repair_adjustment_reason_counts[reason] = (
                                validation_repair_adjustment_reason_counts.get(
                                    reason,
                                    0,
                                )
                                + 1
                            )
                label = (
                    row.get("entry_quality_label")
                    or metadata.get("entry_quality")
                    or metadata.get("entry_quality_label")
                    or metadata.get("entry_setup")
                    or metadata.get("entry_style")
                    or ""
                )
                score = _safe_float(
                    row.get("entry_quality_score")
                    or metadata.get("entry_quality_score")
                )
                if score <= 0 and _entry_quality_is_bad(label, score):
                    score = 35.0
                if score > 0:
                    entry_quality_scores.append(score)
                add_entry_quality_label(entry_quality_label_counts, label)
                if _entry_quality_is_bad(label, score):
                    bad_entry_quality_count += 1
                    add_entry_quality_label(bad_entry_quality_label_counts, label)
                elif label and score >= 70.0:
                    add_entry_quality_label(good_entry_quality_label_counts, label)
            entry_quality_sample_count = len(entry_quality_scores)
            avg_entry_quality_score = _avg(entry_quality_scores)
            bad_entry_quality_rate = (
                bad_entry_quality_count / entry_quality_sample_count * 100.0
                if entry_quality_sample_count
                else 0.0
            )
            entry_quality_weak = (
                entry_quality_sample_count >= min_samples
                and (
                    avg_entry_quality_score < 55.0
                    or bad_entry_quality_rate >= 50.0
                )
            )
            validation_repair_avg_budget_multiplier = (
                _avg(validation_repair_budget_multipliers)
                if validation_repair_budget_multipliers
                else 0.0
            )
            validation_repair_weak = (
                validation_repair_enforced_count > 0
                and (
                    validation_repair_scale_up_blocked_count > 0
                    or validation_repair_waiting_entry_count > 0
                    or validation_repair_rejected_count > 0
                )
            )
            sample_count = len(rows)
            expectancy = _avg(pnl_pct_values)
            win_rate = len(wins) / sample_count * 100.0 if sample_count else 0.0
            risk_budget_summary = _lane_risk_budget_summary(
                sample_count=sample_count,
                min_samples=min_samples,
                expectancy_pct=expectancy,
                win_rate_pct=win_rate,
                profit_factor=profit_factor,
                max_drawdown_pct=max_drawdown_pct,
                recovery_factor=recovery_factor,
                risk_of_ruin_pct=risk_of_ruin_pct,
                cost_precision_verified_rate=cost_precision_verified_rate,
                cost_evidence_weak=cost_evidence_weak,
                entry_quality_sample_count=entry_quality_sample_count,
                avg_entry_quality_score=avg_entry_quality_score,
                entry_quality_weak=entry_quality_weak,
                validation_evidence_profile=validation_evidence_profile,
                validation_evidence_weak=validation_evidence_weak,
                validation_repair_weak=validation_repair_weak,
                validation_repair_avg_budget_multiplier=(
                    validation_repair_avg_budget_multiplier
                ),
            )
            if sample_count < min_samples:
                grade = "insufficient"
                action = "small_probe_until_sample_builds"
                insufficient_lanes.append(lane)
            elif cost_evidence_weak and (
                expectancy > 0.0
                and win_rate >= 48.0
                and profit_factor >= 1.05
                and recovery_factor >= 0.5
                and risk_of_ruin_pct <= 10.0
                and cost_drag <= 55.0
            ):
                grade = "qualified"
                action = "cost_evidence_repair_before_scale"
                qualified_lanes.append(lane)
                cost_evidence_weak_lanes.append(lane)
            elif (
                expectancy > 0.4
                and win_rate >= 52.0
                and profit_factor >= 1.5
                and recovery_factor >= 1.0
                and max_drawdown_pct >= -7.0
                and risk_of_ruin_pct <= 5.0
                and cost_drag <= 35.0
                and not cost_evidence_weak
                and not entry_quality_weak
                and not validation_evidence_weak
                and not validation_repair_weak
            ):
                grade = "scale_candidate"
                action = "eligible_to_press_when_validation_clear"
                scale_candidate_lanes.append(lane)
            elif (
                expectancy > 0.0
                and win_rate >= 48.0
                and profit_factor >= 1.05
                and recovery_factor >= 0.5
                and risk_of_ruin_pct <= 10.0
                and cost_drag <= 55.0
            ):
                grade = "qualified"
                action = "normal_or_selective_press"
                qualified_lanes.append(lane)
            else:
                grade = "weak"
                action = "de_risk_or_waiting_entry"
                weak_lanes.append(lane)
            if entry_quality_weak:
                entry_quality_weak_lanes.append(lane)
                if lane in scale_candidate_lanes:
                    scale_candidate_lanes.remove(lane)
                if grade in {"scale_candidate", "qualified"}:
                    grade = "qualified"
                    action = (
                        "cost_and_entry_quality_repair_before_scale"
                        if cost_evidence_weak
                        else "entry_quality_repair_before_scale"
                    )
                    if lane not in qualified_lanes:
                        qualified_lanes.append(lane)
                else:
                    if lane not in weak_lanes:
                        weak_lanes.append(lane)
            if validation_evidence_weak:
                validation_evidence_weak_lanes.append(lane)
                if lane in scale_candidate_lanes:
                    scale_candidate_lanes.remove(lane)
                if grade in {"scale_candidate", "qualified"}:
                    grade = "qualified"
                    if action in {
                        "eligible_to_press_when_validation_clear",
                        "normal_or_selective_press",
                    }:
                        action = "validation_evidence_repair_before_scale"
                    if lane not in qualified_lanes:
                        qualified_lanes.append(lane)
                else:
                    if lane not in weak_lanes:
                        weak_lanes.append(lane)
            if validation_repair_weak:
                validation_repair_weak_lanes.append(lane)
                if lane in scale_candidate_lanes:
                    scale_candidate_lanes.remove(lane)
                if grade in {"scale_candidate", "qualified"}:
                    grade = "qualified"
                    action = "validation_repair_enforced_before_scale"
                    if lane not in qualified_lanes:
                        qualified_lanes.append(lane)
                else:
                    if lane not in weak_lanes:
                        weak_lanes.append(lane)
            authority_multiplier = 1.0
            if grade == "scale_candidate":
                authority_multiplier = 1.25
            elif grade == "qualified":
                authority_multiplier = 1.0
            elif grade == "insufficient":
                authority_multiplier = 0.75
            else:
                authority_multiplier = 0.5
            if (
                cost_evidence_weak
                or entry_quality_weak
                or validation_evidence_weak
                or validation_repair_weak
                or cost_drag >= 55.0
                or risk_of_ruin_pct >= 10.0
                or max_drawdown_pct <= -7.0
            ):
                authority_multiplier = min(authority_multiplier, 0.5)
            if validation_repair_weak and validation_repair_avg_budget_multiplier > 0:
                authority_multiplier = min(
                    authority_multiplier,
                    validation_repair_avg_budget_multiplier,
                )
            if (
                risk_of_ruin_pct >= 20.0
                or max_drawdown_pct <= -12.0
                or (sample_count >= min_samples and profit_factor < 1.0)
                or validation_repair_rejected_count > 0
            ):
                authority_multiplier = min(authority_multiplier, 0.25)
            risk_budget_multiplier = _safe_float(
                risk_budget_summary.get("risk_budget_multiplier"),
                1.0,
            )
            if risk_budget_multiplier > 0.0:
                authority_multiplier = min(
                    authority_multiplier,
                    risk_budget_multiplier,
                )
            scorecard = {
                "lane": lane,
                "sample_count": sample_count,
                "expectancy_pct": round(expectancy, 6),
                "win_rate_pct": round(win_rate, 6),
                "profit_factor": round(profit_factor, 6),
                "max_drawdown_pct": round(max_drawdown_pct, 6),
                "recovery_factor": round(recovery_factor, 6),
                "cumulative_return_pct": round(cumulative_return_pct, 6),
                "risk_of_ruin_pct": round(risk_of_ruin_pct, 6),
                "sequence_risk_level": lane_monte_carlo.get("sequence_risk_level"),
                "max_consecutive_loss_p95": lane_monte_carlo.get(
                    "max_consecutive_loss_p95"
                ),
                "total_net_pnl": round(sum(net_pnls), 6),
                "total_gross_pnl": round(gross_pnl, 6),
                "total_cost": round(total_cost, 6),
                "cost_drag_pct_of_gross_pnl": round(cost_drag, 6),
                "recorded_cost_sample_count": recorded_cost_count,
                "hybrid_cost_sample_count": hybrid_cost_count,
                "estimated_cost_sample_count": estimated_cost_count,
                "partial_cost_sample_count": partial_cost_count,
                "missing_cost_sample_count": missing_cost_count,
                "cost_precision_counts": cost_precision_counts,
                "cost_precision_verified_rate": round(
                    cost_precision_verified_rate,
                    6,
                ),
                "cost_precision_verified_rate_pct": round(
                    cost_precision_verified_rate,
                    6,
                ),
                "scale_blocked_by_cost_precision": cost_evidence_weak,
                "entry_quality_sample_count": entry_quality_sample_count,
                "avg_entry_quality_score": round(avg_entry_quality_score, 6),
                "bad_entry_quality_rate_pct": round(bad_entry_quality_rate, 6),
                "entry_quality_label_counts": compact_entry_quality_counts(
                    entry_quality_label_counts
                ),
                "bad_entry_quality_label_counts": compact_entry_quality_counts(
                    bad_entry_quality_label_counts
                ),
                "good_entry_quality_label_counts": compact_entry_quality_counts(
                    good_entry_quality_label_counts
                ),
                "dominant_bad_entry_quality_label": (
                    dominant_entry_quality_label(bad_entry_quality_label_counts)
                ),
                "dominant_good_entry_quality_label": (
                    dominant_entry_quality_label(good_entry_quality_label_counts)
                ),
                "scale_blocked_by_entry_quality": entry_quality_weak,
                **validation_evidence_profile,
                "validation_repair_enforced_count": validation_repair_enforced_count,
                "validation_repair_scale_up_blocked_count": (
                    validation_repair_scale_up_blocked_count
                ),
                "validation_repair_waiting_entry_count": (
                    validation_repair_waiting_entry_count
                ),
                "validation_repair_rejected_count": (
                    validation_repair_rejected_count
                ),
                "validation_repair_avg_budget_multiplier": round(
                    validation_repair_avg_budget_multiplier,
                    6,
                ),
                "validation_repair_action_counts": compact_entry_quality_counts(
                    validation_repair_action_counts
                ),
                "validation_repair_adjustment_reason_counts": (
                    compact_entry_quality_counts(
                        validation_repair_adjustment_reason_counts
                    )
                ),
                "scale_blocked_by_validation_repair": validation_repair_weak,
                "validation_evidence_required_evidence": compact_count_keys(
                    validation_evidence_required_evidence_counts
                ),
                "validation_evidence_required_checks": compact_count_keys(
                    validation_evidence_required_check_counts
                ),
                "validation_evidence_pass_collection_hooks": compact_count_keys(
                    validation_evidence_pass_hook_counts
                ),
                "validation_evidence_pass_current_gaps": compact_count_keys(
                    validation_evidence_pass_gap_counts
                ),
                "validation_evidence_pass_criteria": compact_count_keys(
                    validation_evidence_pass_criteria_counts
                ),
                "validation_evidence_verification_artifacts": compact_count_keys(
                    validation_evidence_artifact_counts
                ),
                **risk_budget_summary,
                "authority_multiplier": round(authority_multiplier, 6),
                "max_budget_multiplier": round(authority_multiplier, 6),
                "grade": grade,
                "action": action,
            }
            scorecards.append(scorecard)
            entry_quality_requirements = (
                [
                    "avoid_late_chase_entries",
                    "prefer_pullback_or_reclaim_trigger",
                    "record_entry_quality_evidence_before_scale",
                ]
                if entry_quality_weak
                else []
            )
            validation_repair_requirements = (
                [
                    "respect_validation_repair_enforcement_until_repair_passes",
                    "keep_probe_or_waiting_entry_when_repair_blocks_scale_up",
                ]
                if validation_repair_weak
                else []
            )
            validation_evidence_requirements = (
                [
                    "require_backtest_walk_forward_oos_live_shadow_before_scale",
                    "keep_probe_or_waiting_entry_until_validation_evidence_passes",
                ]
                if validation_evidence_weak
                else []
            )
            lane_actions[lane] = {
                "grade": grade,
                "action": action,
                "sample_count": sample_count,
                "expectancy_pct": round(expectancy, 6),
                "win_rate_pct": round(win_rate, 6),
                "profit_factor": round(profit_factor, 6),
                "max_drawdown_pct": round(max_drawdown_pct, 6),
                "recovery_factor": round(recovery_factor, 6),
                "cumulative_return_pct": round(cumulative_return_pct, 6),
                "risk_of_ruin_pct": round(risk_of_ruin_pct, 6),
                "sequence_risk_level": lane_monte_carlo.get("sequence_risk_level"),
                "cost_drag_pct_of_gross_pnl": round(cost_drag, 6),
                "cost_precision_verified_rate": round(
                    cost_precision_verified_rate,
                    6,
                ),
                "cost_precision_verified_rate_pct": round(
                    cost_precision_verified_rate,
                    6,
                ),
                "cost_precision_counts": cost_precision_counts,
                "scale_blocked_by_cost_precision": cost_evidence_weak,
                "entry_quality_sample_count": entry_quality_sample_count,
                "avg_entry_quality_score": round(avg_entry_quality_score, 6),
                "bad_entry_quality_rate_pct": round(bad_entry_quality_rate, 6),
                "entry_quality_label_counts": compact_entry_quality_counts(
                    entry_quality_label_counts
                ),
                "bad_entry_quality_label_counts": compact_entry_quality_counts(
                    bad_entry_quality_label_counts
                ),
                "good_entry_quality_label_counts": compact_entry_quality_counts(
                    good_entry_quality_label_counts
                ),
                "dominant_bad_entry_quality_label": (
                    dominant_entry_quality_label(bad_entry_quality_label_counts)
                ),
                "dominant_good_entry_quality_label": (
                    dominant_entry_quality_label(good_entry_quality_label_counts)
                ),
                "scale_blocked_by_entry_quality": entry_quality_weak,
                **validation_evidence_profile,
                "validation_repair_enforced_count": validation_repair_enforced_count,
                "validation_repair_scale_up_blocked_count": (
                    validation_repair_scale_up_blocked_count
                ),
                "validation_repair_waiting_entry_count": (
                    validation_repair_waiting_entry_count
                ),
                "validation_repair_rejected_count": (
                    validation_repair_rejected_count
                ),
                "validation_repair_avg_budget_multiplier": round(
                    validation_repair_avg_budget_multiplier,
                    6,
                ),
                "validation_repair_action_counts": compact_entry_quality_counts(
                    validation_repair_action_counts
                ),
                "validation_repair_adjustment_reason_counts": (
                    compact_entry_quality_counts(
                        validation_repair_adjustment_reason_counts
                    )
                ),
                "scale_blocked_by_validation_repair": validation_repair_weak,
                "validation_evidence_required_evidence": compact_count_keys(
                    validation_evidence_required_evidence_counts
                ),
                "validation_evidence_required_checks": compact_count_keys(
                    validation_evidence_required_check_counts
                ),
                "validation_evidence_pass_collection_hooks": compact_count_keys(
                    validation_evidence_pass_hook_counts
                ),
                "validation_evidence_pass_current_gaps": compact_count_keys(
                    validation_evidence_pass_gap_counts
                ),
                "validation_evidence_pass_criteria": compact_count_keys(
                    validation_evidence_pass_criteria_counts
                ),
                "validation_evidence_verification_artifacts": compact_count_keys(
                    validation_evidence_artifact_counts
                ),
                **risk_budget_summary,
                "authority_multiplier": round(authority_multiplier, 6),
                "max_budget_multiplier": round(authority_multiplier, 6),
                "requires_waiting_entry": (
                    grade in {"weak", "insufficient"}
                    or cost_drag >= 55.0
                    or risk_of_ruin_pct >= 10.0
                    or cost_evidence_weak
                    or entry_quality_weak
                    or validation_evidence_weak
                    or validation_repair_weak
                ),
                "entry_quality_requirements": entry_quality_requirements,
                "validation_evidence_requirements": validation_evidence_requirements,
                "validation_repair_requirements": validation_repair_requirements,
            }
            if validation_evidence_weak:
                lane_actions[lane]["entry_quality_requirements"] = list(
                    dict.fromkeys(
                        [
                            *lane_actions[lane].get("entry_quality_requirements", []),
                            "require_backtest_walk_forward_oos_live_shadow_before_scale",
                            "keep_probe_or_waiting_entry_until_validation_evidence_passes",
                        ]
                    )
                )
            if validation_repair_weak:
                lane_actions[lane]["entry_quality_requirements"] = list(
                    dict.fromkeys(
                        [
                            *lane_actions[lane].get("entry_quality_requirements", []),
                            "respect_validation_repair_enforcement_until_repair_passes",
                            "keep_probe_or_waiting_entry_when_repair_blocks_scale_up",
                        ]
                    )
                )

        if not scorecards:
            status = "missing"
        elif (
            weak_lanes
            or insufficient_lanes
            or cost_evidence_weak_lanes
            or entry_quality_weak_lanes
            or validation_evidence_weak_lanes
            or validation_repair_weak_lanes
        ):
            status = "warn"
        else:
            status = "pass"
        return {
            "version": "lane_scorecards_v1",
            "status": status,
            "lane_count": len(scorecards),
            "weak_lanes": weak_lanes[:12],
            "scale_candidate_lanes": scale_candidate_lanes[:12],
            "qualified_lanes": qualified_lanes[:12],
            "insufficient_lanes": insufficient_lanes[:12],
            "cost_evidence_weak_lanes": cost_evidence_weak_lanes[:12],
            "entry_quality_weak_lanes": entry_quality_weak_lanes[:12],
            "validation_evidence_weak_lanes": validation_evidence_weak_lanes[:12],
            "validation_repair_weak_lanes": validation_repair_weak_lanes[:12],
            "lane_actions": lane_actions,
            "scorecards": scorecards,
        }

    def _stress_scenario_result(
        self,
        scenario_id: str,
        returns_pct: list[float],
    ) -> dict[str, Any]:
        equity = 1.0
        curve = [equity]
        for value in returns_pct:
            equity *= max(1.0 + value / 100.0, 0.0)
            curve.append(equity)
        _drawdown_cash, drawdown_pct = _max_drawdown(curve)
        return {
            "scenario_id": scenario_id,
            "sample_count": len(returns_pct),
            "final_return_pct": round((equity - 1.0) * 100.0, 6),
            "max_drawdown_pct": round(drawdown_pct, 6),
        }

    def _stress_metrics(
        self,
        metrics: dict[str, Any],
        outcomes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        crisis_returns_by_scenario: dict[str, list[float]] = {}
        covered_crisis_sample_count = 0
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            crisis_returns = (
                metadata.get("crisis_returns_pct")
                or metadata.get("stress_returns_pct")
                or metadata.get("crisis_replay_returns_pct")
            )
            crisis_returns = _json_loads(crisis_returns)
            if not isinstance(crisis_returns, dict):
                continue
            row_has_crisis_return = False
            for raw_scenario, raw_return in crisis_returns.items():
                scenario_id = _clean_key(raw_scenario)
                if not scenario_id:
                    continue
                crisis_returns_by_scenario.setdefault(scenario_id, []).append(
                    _safe_float(raw_return)
                )
                row_has_crisis_return = True
            if row_has_crisis_return:
                covered_crisis_sample_count += 1

        if crisis_returns_by_scenario:
            scenarios = [
                self._stress_scenario_result(scenario_id, returns_pct)
                for scenario_id, returns_pct in sorted(
                    crisis_returns_by_scenario.items()
                )
            ]
            worst = min(
                scenarios,
                key=lambda row: _safe_float(row.get("max_drawdown_pct")),
            )
            coverage_rate = (
                covered_crisis_sample_count / len(outcomes) * 100.0
                if outcomes
                else 0.0
            )
            worst_drawdown = _safe_float(worst.get("max_drawdown_pct"))
            if worst_drawdown < -abs(float(self.config.max_drawdown_limit_pct)) * 1.5:
                status = "fail"
            elif coverage_rate < 50.0:
                status = "warn"
            elif worst_drawdown < -abs(float(self.config.max_drawdown_limit_pct)):
                status = "warn"
            else:
                status = "pass"
            return {
                "status": status,
                "scenario_source": "metadata_crisis_returns",
                "scenario_count": len(scenarios),
                "covered_crisis_sample_count": covered_crisis_sample_count,
                "stress_coverage_rate_pct": round(coverage_rate, 6),
                "worst_crisis_scenario_id": str(worst.get("scenario_id") or ""),
                "worst_drawdown_pct": worst_drawdown,
                "scenarios": scenarios,
            }

        returns_pct = [
            _safe_float(value)
            for value in list(metrics.get("returns_pct") or [])
        ]
        if not returns_pct:
            return {
                "status": "missing",
                "scenario_source": "none",
                "scenario_count": 0,
                "covered_crisis_sample_count": 0,
            }
        scenarios = []
        for scenario_id, win_multiplier, loss_multiplier in [
            ("liquidity_shock", 0.5, 2.0),
            ("fee_slippage_shock", 0.75, 1.5),
            ("trend_reversal", -0.25, 1.25),
        ]:
            stressed = [
                value * win_multiplier if value > 0 else value * loss_multiplier
                for value in returns_pct
            ]
            scenarios.append(self._stress_scenario_result(scenario_id, stressed))
        worst_drawdown = min(
            [row["max_drawdown_pct"] for row in scenarios],
            default=0.0,
        )
        worst = min(
            scenarios,
            key=lambda row: _safe_float(row.get("max_drawdown_pct")),
        )
        if worst_drawdown < -abs(float(self.config.max_drawdown_limit_pct)) * 1.5:
            status = "fail"
        elif worst_drawdown < -abs(float(self.config.max_drawdown_limit_pct)):
            status = "warn"
        else:
            status = "pass"
        return {
            "status": status,
            "scenario_source": "synthetic_live_return_shock",
            "scenario_count": len(scenarios),
            "covered_crisis_sample_count": 0,
            "stress_coverage_rate_pct": 0.0,
            "worst_crisis_scenario_id": str(worst.get("scenario_id") or ""),
            "worst_drawdown_pct": round(worst_drawdown, 6),
            "scenarios": scenarios,
        }

    def _capacity_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        ratios: list[float] = []
        liquidity_rows: list[dict[str, Any]] = []
        metadata_rows: list[dict[str, Any]] = []
        target_depth_bps = 30.0

        def depth_at_target_bps(raw_depths: Any) -> float:
            depths = _json_loads(raw_depths)
            if not isinstance(depths, dict):
                return 0.0
            candidates: list[tuple[float, float]] = []
            for raw_bps, raw_depth in depths.items():
                key = str(raw_bps).replace("bps", "").strip()
                bps = _safe_float(key, default=-1.0)
                depth = _safe_float(raw_depth)
                if bps >= 0 and depth > 0:
                    candidates.append((bps, depth))
            if not candidates:
                return 0.0
            at_or_inside = [
                (bps, depth) for bps, depth in candidates if bps <= target_depth_bps
            ]
            if at_or_inside:
                return max(at_or_inside, key=lambda item: item[0])[1]
            return min(candidates, key=lambda item: item[0])[1]

        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            capacity = _safe_float(
                metadata.get("capacity_usdt")
                or metadata.get("capacity_krw")
                or metadata.get("orderbook_capacity_usdt")
            )
            depth_capacity = (
                _safe_float(metadata.get("orderbook_depth_30bps_usdt"))
                or _safe_float(metadata.get("orderbook_depth_usdt"))
                or depth_at_target_bps(metadata.get("orderbook_depth_usdt_by_bps"))
            )
            daily_turnover = _safe_float(
                metadata.get("daily_turnover_usdt")
                or metadata.get("daily_volume_usdt")
                or metadata.get("quote_volume_usdt")
            )
            participation = _safe_float(
                metadata.get("max_participation_rate"),
                default=0.01,
            )
            if participation <= 0:
                participation = 0.01
            turnover_capacity = daily_turnover * participation if daily_turnover > 0 else 0.0
            notional = _safe_float(
                metadata.get("notional_usdt")
                or metadata.get("notional_krw")
                or metadata.get("block_notional_usdt")
                or metadata.get("position_notional")
            )
            practical_capacity = 0.0
            if depth_capacity > 0 and turnover_capacity > 0:
                practical_capacity = min(depth_capacity, turnover_capacity)
            elif depth_capacity > 0:
                practical_capacity = depth_capacity
            elif turnover_capacity > 0:
                practical_capacity = turnover_capacity
            if practical_capacity > 0 and notional > 0:
                ratio = practical_capacity / notional
                ratios.append(ratio)
                liquidity_rows.append(
                    {
                        "symbol": str(row.get("symbol") or ""),
                        "block_id": str(row.get("block_id") or ""),
                        "notional": round(notional, 6),
                        "depth_capacity": round(depth_capacity, 6),
                        "turnover_capacity": round(turnover_capacity, 6),
                        "practical_capacity": round(practical_capacity, 6),
                        "capacity_ratio": round(ratio, 6),
                    }
                )
                continue
            if capacity > 0 and notional > 0:
                ratio = capacity / notional
                ratios.append(ratio)
                metadata_rows.append(
                    {
                        "symbol": str(row.get("symbol") or ""),
                        "block_id": str(row.get("block_id") or ""),
                        "notional": round(notional, 6),
                        "metadata_capacity": round(capacity, 6),
                        "capacity_ratio": round(ratio, 6),
                        "capacity_source": str(metadata.get("capacity_source") or ""),
                    }
                )
        coverage_rate = len(ratios) / len(outcomes) * 100.0 if outcomes else 0.0

        def capacity_status(min_ratio: float) -> str:
            if not ratios:
                return "missing"
            if min_ratio < 5.0:
                return "fail"
            if coverage_rate < 50.0:
                return "warn"
            if min_ratio < 20.0:
                return "warn"
            return "pass"

        if liquidity_rows:
            sorted_liquidity_rows = sorted(
                liquidity_rows,
                key=lambda item: _safe_float(item.get("capacity_ratio")),
            )
            tightest = sorted_liquidity_rows[0]
            practical_capacities = [
                _safe_float(item.get("practical_capacity"))
                for item in liquidity_rows
            ]
            min_ratio = min(ratios) if ratios else 0.0
            status = capacity_status(min_ratio)
            return {
                "status": status,
                "capacity_method": "orderbook_depth_and_turnover",
                "covered_sample_count": len(ratios),
                "capacity_coverage_rate_pct": round(coverage_rate, 6),
                "liquidity_sample_count": len(liquidity_rows),
                "target_depth_bps": target_depth_bps,
                "min_capacity_ratio": round(min_ratio, 6),
                "avg_capacity_ratio": round(_avg(ratios), 6),
                "min_practical_capacity_usdt": round(min(practical_capacities), 6),
                "avg_practical_capacity_usdt": round(_avg(practical_capacities), 6),
                "tightest_symbol": str(tightest.get("symbol") or ""),
                "tightest_block_id": str(tightest.get("block_id") or ""),
                "examples": sorted_liquidity_rows[:8],
            }
        min_ratio = min(ratios) if ratios else 0.0
        proxy_status = capacity_status(min_ratio)
        status = "warn" if proxy_status == "fail" else proxy_status
        sorted_metadata_rows = sorted(
            metadata_rows,
            key=lambda item: _safe_float(item.get("capacity_ratio")),
        )
        tightest_metadata = sorted_metadata_rows[0] if sorted_metadata_rows else {}
        return {
            "status": status,
            "capacity_method": "metadata_capacity_ratio",
            "proxy_status": proxy_status,
            "hard_gate_eligible": False,
            "covered_sample_count": len(ratios),
            "capacity_coverage_rate_pct": round(coverage_rate, 6),
            "liquidity_sample_count": 0,
            "min_capacity_ratio": round(min_ratio, 6),
            "avg_capacity_ratio": round(_avg(ratios), 6) if ratios else 0.0,
            **(
                {
                    "tightest_symbol": str(tightest_metadata.get("symbol") or ""),
                    "tightest_block_id": str(tightest_metadata.get("block_id") or ""),
                    "examples": sorted_metadata_rows[:8],
                }
                if tightest_metadata
                else {}
            ),
        }

    def _correlation_proxy(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        series_by_symbol: dict[str, list[float]] = {}
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            raw_series = (
                metadata.get("return_window_pct")
                or metadata.get("returns_window_pct")
                or metadata.get("rolling_returns_pct")
            )
            raw_series = _json_loads(raw_series)
            if not isinstance(raw_series, list):
                continue
            values = [_safe_float(value) for value in raw_series]
            if len(values) < 3:
                continue
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                series_by_symbol[symbol] = values
        if len(series_by_symbol) == 1:
            symbol, values = next(iter(series_by_symbol.items()))
            coverage_rate = (
                len(series_by_symbol) / len(outcomes) * 100.0 if outcomes else 0.0
            )
            return {
                "status": "warn",
                "method": "rolling_return_window_single_symbol",
                "covered_sample_count": 1,
                "correlation_coverage_rate_pct": round(coverage_rate, 6),
                "pair_count": 0,
                "max_abs_correlation": 0.0,
                "top_correlation": 0.0,
                "top_pair": [],
                "single_symbol": symbol,
                "single_symbol_window_size": len(values),
                "pair_adequacy": "needs_at_least_two_symbols",
            }
        if len(series_by_symbol) >= 2:
            pairs: list[dict[str, Any]] = []
            symbols = sorted(series_by_symbol)
            for left_index, left_symbol in enumerate(symbols):
                for right_symbol in symbols[left_index + 1:]:
                    correlation = _pearson_correlation(
                        series_by_symbol[left_symbol],
                        series_by_symbol[right_symbol],
                    )
                    pairs.append(
                        {
                            "pair": [left_symbol, right_symbol],
                            "correlation": round(correlation, 6),
                            "abs_correlation": round(abs(correlation), 6),
                            "window_size": min(
                                len(series_by_symbol[left_symbol]),
                                len(series_by_symbol[right_symbol]),
                            ),
                        }
                    )
            top = max(
                pairs,
                key=lambda item: _safe_float(item.get("abs_correlation")),
                default={},
            )
            max_abs_correlation = _safe_float(top.get("abs_correlation"))
            coverage_rate = (
                len(series_by_symbol) / len(outcomes) * 100.0 if outcomes else 0.0
            )
            if max_abs_correlation >= 0.85:
                status = "fail"
            elif coverage_rate < 50.0:
                status = "warn"
            elif max_abs_correlation >= 0.65:
                status = "warn"
            else:
                status = "pass"
            return {
                "status": status,
                "method": "rolling_return_window",
                "covered_sample_count": len(series_by_symbol),
                "correlation_coverage_rate_pct": round(coverage_rate, 6),
                "pair_count": len(pairs),
                "max_abs_correlation": max_abs_correlation,
                "top_correlation": _safe_float(top.get("correlation")),
                "top_pair": list(top.get("pair") or []),
                "pairs": sorted(
                    pairs,
                    key=lambda item: _safe_float(item.get("abs_correlation")),
                    reverse=True,
                )[:20],
            }

        cluster_counts: dict[str, int] = {}
        cluster_source_counts: dict[str, int] = {}
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            cluster, source = self._correlation_cluster_for_outcome(row, metadata)
            if not cluster:
                continue
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
            cluster_source_counts[source] = cluster_source_counts.get(source, 0) + 1
        total = sum(cluster_counts.values())
        top_cluster = ""
        top_count = 0
        if cluster_counts:
            top_cluster, top_count = max(cluster_counts.items(), key=lambda item: item[1])
        top_share = top_count / total * 100.0 if total else 0.0
        coverage_rate = total / len(outcomes) * 100.0 if outcomes else 0.0
        if not cluster_counts:
            status = "missing"
        elif total < 2:
            status = "warn"
        elif coverage_rate < 50.0:
            status = "warn"
        elif top_share > 75.0:
            status = "fail"
        elif top_share > 60.0:
            status = "warn"
        else:
            status = "pass"
        return {
            "status": status,
            "covered_sample_count": total,
            "correlation_coverage_rate_pct": round(coverage_rate, 6),
            "cluster_count": len(cluster_counts),
            "top_cluster": top_cluster,
            "top_cluster_share_pct": round(top_share, 6),
            "clusters": dict(sorted(cluster_counts.items())),
            "cluster_source_counts": dict(sorted(cluster_source_counts.items())),
            "method": "metadata_cluster_concentration_proxy",
        }

    def _correlation_cluster_for_outcome(
        self,
        row: dict[str, Any],
        metadata: dict[str, Any],
    ) -> tuple[str, str]:
        explicit = (
            _clean_key(metadata.get("correlation_cluster"))
            or _clean_key(metadata.get("asset_cluster"))
            or _clean_key(metadata.get("sector"))
        )
        if explicit:
            return explicit, "explicit_metadata"

        quote = metadata.get("quote") if isinstance(metadata.get("quote"), dict) else {}
        raw_quote = quote.get("raw") if isinstance(quote.get("raw"), dict) else {}
        sector = _clean_key(
            raw_quote.get("bstp_kor_isnm")
            or metadata.get("industry")
            or metadata.get("industry_name")
        )
        if sector:
            return f"sector:{sector}", "kis_quote_sector"

        venue = _clean_key(row.get("venue"))
        if venue == "kis":
            representative_market = _clean_key(
                raw_quote.get("rprs_mrkt_kor_name")
                or metadata.get("representative_market")
                or metadata.get("market_index")
            )
            lane = self._canonical_lane_key(row, metadata)
            if lane == "core_etf":
                market_cluster = representative_market or "broad_index"
                return f"etf:{market_cluster}", "kis_etf_lane"
            if representative_market:
                return f"market:{representative_market}", "kis_representative_market"
            return "kr_equity:unclassified", "kis_symbol_fallback"
        return "", ""

    def _factor_exposure_metrics(self, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        factor_totals: dict[str, float] = {}
        weighted_totals: dict[str, float] = {}
        covered = 0
        for row in outcomes:
            metadata = self._metadata_for_outcome(row)
            exposures = metadata.get("factor_exposures") or metadata.get("factors")
            if not isinstance(exposures, dict):
                continue
            notional = _safe_float(
                metadata.get("notional_usdt")
                or metadata.get("notional_krw")
                or metadata.get("block_notional_usdt")
                or metadata.get("position_notional"),
                default=1.0,
            )
            if notional <= 0:
                notional = 1.0
            row_has_factor = False
            for key, value in exposures.items():
                factor = _clean_key(key)
                exposure = abs(_safe_float(value))
                if factor and exposure > 0:
                    factor_totals[factor] = factor_totals.get(factor, 0.0) + exposure
                    weighted_totals[factor] = (
                        weighted_totals.get(factor, 0.0) + exposure * notional
                    )
                    row_has_factor = True
            if row_has_factor:
                covered += 1
        total_exposure = sum(factor_totals.values())
        total_weighted_exposure = sum(weighted_totals.values())
        top_factor = ""
        top_value = 0.0
        if factor_totals:
            top_factor, top_value = max(
                factor_totals.items(),
                key=lambda item: item[1],
            )
        top_share = top_value / total_exposure * 100.0 if total_exposure else 0.0
        weighted_top_factor = ""
        weighted_top_value = 0.0
        if weighted_totals:
            weighted_top_factor, weighted_top_value = max(
                weighted_totals.items(),
                key=lambda item: item[1],
            )
        weighted_top_share = (
            weighted_top_value / total_weighted_exposure * 100.0
            if total_weighted_exposure
            else 0.0
        )
        coverage_rate = covered / len(outcomes) * 100.0 if outcomes else 0.0
        if not factor_totals:
            status = "missing"
        elif coverage_rate < 50.0:
            status = "warn"
        elif top_share > 85.0:
            status = "fail"
        elif top_share > 70.0:
            status = "warn"
        else:
            status = "pass"
        return {
            "status": status,
            "covered_sample_count": covered,
            "factor_coverage_rate_pct": round(coverage_rate, 6),
            "factor_count": len(factor_totals),
            "top_factor": top_factor,
            "dominant_factor": weighted_top_factor or top_factor,
            "top_factor_share_pct": round(top_share, 6),
            "weighted_top_factor_share_pct": round(weighted_top_share, 6),
            "factor_totals": {
                key: round(value, 6)
                for key, value in sorted(factor_totals.items())
            },
            "weighted_factor_totals": {
                key: round(value, 6)
                for key, value in sorted(weighted_totals.items())
            },
        }

    def _monte_carlo(self, returns_pct: list[float]) -> dict[str, Any]:
        sample_count = len(returns_pct)
        min_samples = int(self.config.min_sample_count)
        iterations = max(min(int(self.config.monte_carlo_iterations), 5000), 1)
        if not returns_pct:
            return {
                "status": "missing",
                "iterations": 0,
                "sample_count": 0,
                "min_sample_count": min_samples,
                "sample_adequacy": "missing",
                "final_return_p05_pct": 0.0,
                "final_return_median_pct": 0.0,
                "final_return_p95_pct": 0.0,
                "final_return_expected_shortfall_p05_pct": 0.0,
                "max_drawdown_p05_pct": 0.0,
                "max_drawdown_median_pct": 0.0,
                "max_drawdown_expected_shortfall_p05_pct": 0.0,
                "max_consecutive_loss_p95": 0,
                "probability_loss_streak_ge_3_pct": 0.0,
                "risk_of_ruin_pct": 0.0,
                "ruin_event_count": 0,
                "earliest_trade_index_to_ruin": 0,
                "median_trade_index_to_ruin": 0,
                "sequence_risk_level": "missing",
            }
        rng = random.Random(int(self.config.monte_carlo_seed))
        final_returns: list[float] = []
        drawdowns: list[float] = []
        loss_streaks: list[int] = []
        ruin_trade_indices: list[int] = []
        ruin_count = 0
        loss_streak_ge_3_count = 0
        for _ in range(iterations):
            equity = 1.0
            curve = [equity]
            sampled_returns: list[float] = []
            for _trade_index in range(sample_count):
                sampled_return_pct = rng.choice(returns_pct)
                sampled_returns.append(sampled_return_pct)
                trade_return = sampled_return_pct / 100.0
                equity *= max(1.0 + trade_return, 0.0)
                curve.append(equity)
            _drawdown_cash, drawdown_pct = _max_drawdown(curve)
            loss_streak = _max_consecutive_losses(sampled_returns)
            final_returns.append((equity - 1.0) * 100.0)
            drawdowns.append(drawdown_pct)
            loss_streaks.append(loss_streak)
            if drawdown_pct <= -abs(float(self.config.ruin_drawdown_pct)):
                ruin_count += 1
                breach_index = _first_drawdown_breach_index(
                    curve,
                    float(self.config.ruin_drawdown_pct),
                )
                if breach_index:
                    ruin_trade_indices.append(breach_index)
            if loss_streak >= 3:
                loss_streak_ge_3_count += 1
        drawdown_es = _expected_shortfall(drawdowns, 5)
        loss_streak_p95 = _percentile([float(value) for value in loss_streaks], 95)
        loss_streak_prob = loss_streak_ge_3_count / iterations * 100.0
        if ruin_count:
            sequence_risk_level = "critical"
        elif drawdown_es <= -abs(float(self.config.ruin_drawdown_pct)) * 0.75:
            sequence_risk_level = "high"
        elif loss_streak_prob >= 25.0 or drawdown_es <= -10.0:
            sequence_risk_level = "medium"
        else:
            sequence_risk_level = "low"
        sample_adequacy = "sufficient" if sample_count >= min_samples else "weak"
        return {
            "status": "ok",
            "iterations": iterations,
            "sample_count": sample_count,
            "min_sample_count": min_samples,
            "sample_adequacy": sample_adequacy,
            "final_return_p05_pct": round(_percentile(final_returns, 5), 6),
            "final_return_median_pct": round(_percentile(final_returns, 50), 6),
            "final_return_p95_pct": round(_percentile(final_returns, 95), 6),
            "final_return_expected_shortfall_p05_pct": round(
                _expected_shortfall(final_returns, 5),
                6,
            ),
            "max_drawdown_p05_pct": round(_percentile(drawdowns, 5), 6),
            "max_drawdown_median_pct": round(_percentile(drawdowns, 50), 6),
            "max_drawdown_expected_shortfall_p05_pct": round(drawdown_es, 6),
            "max_consecutive_loss_p95": int(math.ceil(loss_streak_p95)),
            "probability_loss_streak_ge_3_pct": round(loss_streak_prob, 6),
            "risk_of_ruin_pct": round(ruin_count / iterations * 100.0, 6),
            "ruin_event_count": ruin_count,
            "earliest_trade_index_to_ruin": min(ruin_trade_indices)
            if ruin_trade_indices
            else 0,
            "median_trade_index_to_ruin": int(
                round(_percentile([float(value) for value in ruin_trade_indices], 50))
            )
            if ruin_trade_indices
            else 0,
            "sequence_risk_level": sequence_risk_level,
        }

    def _build_disciplines(
        self,
        *,
        metrics: dict[str, Any],
        monte_carlo: dict[str, Any],
    ) -> list[dict[str, Any]]:
        sample_count = int(metrics.get("sample_count") or 0)
        min_samples = int(self.config.min_sample_count)
        cost_total = _safe_float(metrics.get("cost_total"))
        pf = _safe_float(metrics.get("profit_factor"))
        max_dd = _safe_float(metrics.get("max_drawdown_pct"))
        sharpe = _safe_float(metrics.get("sharpe_ratio"))
        sortino = _safe_float(metrics.get("sortino_ratio"))
        calmar = _safe_float(metrics.get("calmar_ratio"))
        recovery = _safe_float(metrics.get("recovery_factor"))
        kelly = _safe_float(metrics.get("kelly_fraction"))
        ruin = _safe_float(metrics.get("risk_of_ruin_pct"), 100.0)
        stress = (
            metrics.get("stress") if isinstance(metrics.get("stress"), dict) else {}
        )
        capacity = (
            metrics.get("capacity") if isinstance(metrics.get("capacity"), dict) else {}
        )
        regime = (
            metrics.get("regime_scorecards")
            if isinstance(metrics.get("regime_scorecards"), dict)
            else {}
        )
        correlation = (
            metrics.get("correlation_proxy")
            if isinstance(metrics.get("correlation_proxy"), dict)
            else {}
        )
        factor_exposure = (
            metrics.get("factor_exposure")
            if isinstance(metrics.get("factor_exposure"), dict)
            else {}
        )
        pattern_lab = (
            metrics.get("pattern_lab")
            if isinstance(metrics.get("pattern_lab"), dict)
            else {}
        )
        data_quality = (
            metrics.get("data_quality")
            if isinstance(metrics.get("data_quality"), dict)
            else {}
        )
        cost_simulation = (
            metrics.get("cost_simulation")
            if isinstance(metrics.get("cost_simulation"), dict)
            else {}
        )
        kelly_sizing = (
            metrics.get("kelly_sizing")
            if isinstance(metrics.get("kelly_sizing"), dict)
            else {}
        )
        drawdown_budget = (
            metrics.get("drawdown_budget")
            if isinstance(metrics.get("drawdown_budget"), dict)
            else {}
        )
        risk_adjusted = (
            metrics.get("risk_adjusted_performance")
            if isinstance(metrics.get("risk_adjusted_performance"), dict)
            else {}
        )
        profitability = (
            metrics.get("profitability_quality")
            if isinstance(metrics.get("profitability_quality"), dict)
            else {}
        )
        recovery_profile = (
            metrics.get("recovery_profile")
            if isinstance(metrics.get("recovery_profile"), dict)
            else {}
        )
        ruin_profile = (
            metrics.get("ruin_profile")
            if isinstance(metrics.get("ruin_profile"), dict)
            else {}
        )
        stress_worst_dd = _safe_float(stress.get("worst_drawdown_pct"))
        stress_status = str(stress.get("status") or "")
        capacity_min_ratio = _safe_float(capacity.get("min_capacity_ratio"))
        capacity_status = str(capacity.get("status") or "")
        regime_count = int(regime.get("regime_count") or 0)
        regime_worst_expectancy = _safe_float(regime.get("worst_expectancy_pct"))
        regime_status = str(regime.get("status") or "")
        top_cluster_share = _safe_float(correlation.get("top_cluster_share_pct"))
        correlation_method = str(correlation.get("method") or "")
        max_abs_correlation = _safe_float(correlation.get("max_abs_correlation"))
        correlation_status = str(correlation.get("status") or "")
        top_factor_share = _safe_float(factor_exposure.get("top_factor_share_pct"))
        factor_status = str(factor_exposure.get("status") or "")
        pattern_status = str(pattern_lab.get("status") or "")
        pattern_source_scope = str(pattern_lab.get("source_scope") or "")
        pattern_is_proxy = pattern_status == "proxy"
        pattern_proxy_label = (
            "KIS live-forward"
            if pattern_source_scope == "kis_live_forward_proxy"
            else "live-forward"
        )
        proxy_sample_count = int(pattern_lab.get("sample_count") or sample_count)
        proxy_pf = _safe_float(
            pattern_lab.get("proxy_profit_factor") or metrics.get("profit_factor")
        )
        proxy_expectancy = _safe_float(
            pattern_lab.get("proxy_expectancy_pct") or metrics.get("expectancy_pct")
        )
        active_sets = int(pattern_lab.get("active_set_count") or 0)
        rejected_sets = int(pattern_lab.get("rejected_set_count") or 0)
        high_overfit_count = int(pattern_lab.get("high_overfit_count") or 0)
        unknown_overfit_count = int(pattern_lab.get("unknown_overfit_count") or 0)
        missing_oos_count = int(pattern_lab.get("missing_out_of_sample_set_count") or 0)
        active_missing_oos_count = int(
            pattern_lab.get("active_missing_out_of_sample_set_count") or 0
        )
        oos_coverage_rate = _safe_float(
            pattern_lab.get("out_of_sample_coverage_rate_pct")
        )
        active_oos_coverage_rate = _safe_float(
            pattern_lab.get("active_out_of_sample_coverage_rate_pct")
        )
        active_missing_wfa_count = int(
            pattern_lab.get("active_missing_walk_forward_set_count") or 0
        )
        active_wfa_coverage_rate = _safe_float(
            pattern_lab.get("active_walk_forward_coverage_rate_pct")
        )
        window_pass_rate = _safe_float(
            pattern_lab.get("walk_forward_window_pass_rate_pct")
        )
        wf_pass_rate = _safe_float(pattern_lab.get("walk_forward_pass_rate_pct"))
        avg_gap = _safe_float(pattern_lab.get("avg_train_test_expectancy_gap_r"))
        min_oos_pf = _safe_float(pattern_lab.get("min_out_of_sample_profit_factor"))
        worst_oos_exp = _safe_float(
            pattern_lab.get("worst_out_of_sample_expectancy_r")
        )

        def row(
            discipline_id: str,
            status: str,
            evidence: str,
            action: str,
            metric: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            definition = next(item for item in DISCIPLINE_DEFINITIONS if item["id"] == discipline_id)
            return {
                **definition,
                "status": status,
                "evidence": evidence,
                "action": action,
                "metric": metric or {},
            }

        sample_status = (
            "pass"
            if sample_count >= min_samples
            else ("warn" if sample_count else "missing")
        )
        data_quality_status = str(data_quality.get("status") or sample_status)
        data_quality_issue_count = int(data_quality.get("issue_count") or 0)
        data_quality_issue_rate = _safe_float(data_quality.get("issue_rate_pct"))
        risk_sample_adequacy = str(risk_adjusted.get("sample_adequacy") or "")
        risk_primary_flag = str(risk_adjusted.get("primary_risk_flag") or "")

        def risk_ratio_status(value: float, threshold: float) -> str:
            if not sample_count:
                return "missing"
            if risk_primary_flag == "negative_edge":
                return "fail"
            if value >= threshold:
                return "warn" if risk_sample_adequacy == "weak" else "pass"
            if value > 0:
                return "warn"
            return "fail"

        sequence_risk_level = str(monte_carlo.get("sequence_risk_level") or "")
        monte_carlo_sample_adequacy = str(
            monte_carlo.get("sample_adequacy") or ""
        )
        monte_carlo_status = (
            "missing"
            if not sample_count
            else (
                "fail"
                if sequence_risk_level == "critical"
                else (
                    "warn"
                    if sequence_risk_level in {"high", "medium"}
                    or monte_carlo_sample_adequacy == "weak"
                    else (
                        "pass"
                        if ruin <= 1.0
                        else (
                            "warn"
                            if ruin <= 5.0
                            else "fail"
                        )
                    )
                )
            )
        )
        disciplines = [
            row(
                "data_validation",
                data_quality_status,
                (
                    f"검증 가능한 쥬 실거래 표본 {sample_count}건 중 "
                    f"데이터 이슈 {data_quality_issue_count}개"
                    f"({data_quality_issue_rate:.2f}%)를 감지했습니다."
                ),
                "stale quote, upstream error, fallback source, 비용 누락은 live authority에서 크기와 진입 권한을 낮추는 입력으로 사용합니다.",
                {
                    **data_quality,
                    "min_sample_count": min_samples,
                    "sample_coverage_status": sample_status,
                },
            ),
            row(
                "overfit_validation",
                (
                    "warn"
                    if pattern_is_proxy and proxy_sample_count >= min_samples
                    else (
                        "missing"
                        if pattern_is_proxy
                        else (
                            "pass"
                            if pattern_status == "ok"
                            and active_sets > 0
                            and high_overfit_count == 0
                            and unknown_overfit_count == 0
                            and avg_gap <= 0.2
                            else (
                                "warn"
                                if pattern_status == "ok" and active_sets > 0
                                else (
                                    "fail"
                                    if pattern_status == "ok" and rejected_sets > 0
                                    else "missing"
                                )
                            )
                        )
                    )
                ),
                (
                    f"{pattern_proxy_label} proxy 표본 {proxy_sample_count}건을 "
                    "실행 후 검증 근거로 사용합니다. venue-native 최적화 세트가 "
                    "아니므로 과최적화 통과로 승격하지 않습니다."
                    if pattern_is_proxy
                    else (
                        "패턴랩 최적화 세트 기준 active "
                        f"{active_sets}개, rejected {rejected_sets}개, "
                        f"high overfit {high_overfit_count}개, "
                        f"unknown overfit {unknown_overfit_count}개, "
                        f"OOS coverage {oos_coverage_rate:.2f}%, "
                        f"평균 train/test gap {avg_gap:.3f}R입니다."
                    )
                ),
                (
                    "KIS 전용 parameter heatmap, multiple-testing penalty, rolling OOS 저장소를 만들어야 proxy 의존을 줄일 수 있습니다."
                    if pattern_is_proxy
                    else "parameter heatmap과 multiple-testing penalty를 추가해 과최적화 판정을 더 엄밀하게 만듭니다."
                ),
                pattern_lab,
            ),
            row(
                "walk_forward_analysis",
                (
                    "warn"
                    if pattern_is_proxy and proxy_sample_count >= min_samples
                    else (
                        "missing"
                        if pattern_is_proxy
                        else (
                            "pass"
                            if pattern_status == "ok"
                            and active_sets > 0
                            and active_missing_wfa_count == 0
                            and active_wfa_coverage_rate >= 100.0
                            and wf_pass_rate >= 70.0
                            else (
                                "fail"
                                if pattern_status == "ok"
                                and active_sets > 0
                                and (
                                    active_wfa_coverage_rate <= 0
                                    or active_missing_wfa_count >= active_sets
                                )
                                else (
                                    "warn"
                                    if pattern_status == "ok" and wf_pass_rate > 0
                                    else (
                                        "fail"
                                        if pattern_status == "ok"
                                        else "missing"
                                    )
                                )
                            )
                        )
                    )
                ),
                (
                    f"{pattern_proxy_label} proxy는 {proxy_sample_count}건의 "
                    "실거래 forward 표본만 보며 rolling walk-forward window는 아직 없습니다."
                    if pattern_is_proxy
                    else (
                        "패턴랩 optimized set의 walk-forward 통과율은 "
                        f"{wf_pass_rate:.2f}%, active rolling WFA coverage는 "
                        f"{active_wfa_coverage_rate:.2f}%, window 통과율은 "
                        f"{window_pass_rate:.2f}%입니다."
                    )
                ),
                (
                    "KIS 전용 리포트/밸류/수급/시세 feature snapshot으로 rolling WFA를 별도 계산해야 합니다."
                    if pattern_is_proxy
                    else "active set은 rolling WFA window 증거가 있어야 신규 리스크 검증을 통과합니다."
                ),
                pattern_lab,
            ),
            row(
                "out_of_sample_test",
                (
                    "warn"
                    if pattern_is_proxy and proxy_sample_count >= min_samples
                    else (
                        "missing"
                        if pattern_is_proxy
                        else (
                            "pass"
                            if pattern_status == "ok"
                            and active_sets > 0
                            and missing_oos_count == 0
                            and wf_pass_rate >= 70.0
                            and min_oos_pf >= 1.05
                            and worst_oos_exp > 0
                            else (
                                "fail"
                                if pattern_status == "ok"
                                and active_sets > 0
                                and (
                                    active_oos_coverage_rate <= 0
                                    or active_missing_oos_count >= active_sets
                                )
                                else (
                                "warn"
                                if pattern_status == "ok" and active_sets > 0
                                else (
                                    "fail"
                                    if pattern_status == "ok" and rejected_sets > 0
                                    else (
                                        "pass"
                                        if sample_count >= min_samples
                                        else ("warn" if sample_count else "missing")
                                    )
                                )
                                )
                            )
                        )
                    )
                ),
                (
                    f"{pattern_proxy_label} proxy 기준 표본 {proxy_sample_count}건, "
                    f"expectancy {proxy_expectancy:.3f}%, PF {proxy_pf:.3f}입니다. "
                    "학습 구간과 분리된 국장 OOS 세트는 아직 필요합니다."
                    if pattern_is_proxy
                    else (
                        "패턴랩 OOS 기준 최저 expectancy "
                        f"{worst_oos_exp:.3f}R, 최저 PF {min_oos_pf:.3f}, "
                        f"통과율 {wf_pass_rate:.2f}%, "
                        f"OOS coverage {oos_coverage_rate:.2f}%"
                        f"(active {active_oos_coverage_rate:.2f}%)입니다."
                        if pattern_status == "ok"
                        else "실거래 성과는 백테스트 이후의 forward sample 역할을 합니다."
                    )
                ),
                "패턴/리서치별 in-sample과 out-of-sample을 계속 분리 저장해 live proxy 의존도를 낮춥니다.",
                pattern_lab if pattern_status in {"ok", "proxy"} else {"sample_count": sample_count},
            ),
            row(
                "monte_carlo",
                monte_carlo_status,
                (
                    "거래 결과 bootstrap 기준 파산확률 "
                    f"{ruin:.2f}%, p95 연속손실 "
                    f"{int(monte_carlo.get('max_consecutive_loss_p95') or 0)}회, "
                    "하위 5% MDD expected shortfall "
                    f"{_safe_float(monte_carlo.get('max_drawdown_expected_shortfall_p05_pct')):.2f}%입니다."
                ),
                "risk_of_ruin, 연속손실 확률, tail MDD가 상승하면 신규 블록 수량과 volatile lane budget을 자동 축소합니다.",
                monte_carlo,
            ),
            row(
                "stress_test",
                stress_status
                or (
                    "pass"
                    if sample_count
                    and stress_worst_dd >= -abs(float(self.config.max_drawdown_limit_pct))
                    else (
                        "warn"
                        if sample_count
                        and stress_worst_dd
                        >= -abs(float(self.config.max_drawdown_limit_pct)) * 1.5
                        else ("fail" if sample_count else "missing")
                    )
                ),
                (
                    "실거래 수익률에 유동성/비용/반전 shock을 적용한 "
                    f"최악 stress MDD는 {stress_worst_dd:.2f}%입니다."
                ),
                "실제 위기 구간 replay를 추가하기 전까지는 live-return shock replay를 보조 게이트로 사용합니다.",
                stress,
            ),
            row(
                "cost_simulation",
                str(
                    cost_simulation.get("status")
                    or ("pass" if cost_total > 0 else ("warn" if sample_count else "missing"))
                ),
                (
                    "총 gross PnL 대비 비용 드래그는 "
                    f"{_safe_float(cost_simulation.get('cost_drag_pct_of_gross_pnl')):.2f}%이고, "
                    "비용 2배 stress 후 net PnL은 "
                    f"{_safe_float((cost_simulation.get('stressed_net_pnl_by_cost_multiplier') or {}).get('2x')):.6f}입니다."
                    if cost_simulation
                    else f"성과 DB에 기록된 총 비용은 {cost_total:.6f}입니다."
                ),
                "비용 드래그, 비용 원천 누락, 2x/3x 비용 stress가 나빠지면 쥬는 해당 lane의 sizing과 진입 빈도를 낮춰야 합니다.",
                cost_simulation or {"cost_total": cost_total},
            ),
            row(
                "capacity_analysis",
                capacity_status
                or (
                    "pass"
                    if capacity_min_ratio >= 20.0
                    else (
                        "warn"
                        if capacity_min_ratio >= 5.0
                        else (
                            "missing"
                            if not capacity.get("covered_sample_count")
                            else "fail"
                        )
                    )
                ),
                (
                    "metadata capacity/notional 기준 최소 수용 배율은 "
                    f"{capacity_min_ratio:.2f}배입니다."
                ),
                "orderbook depth와 거래대금 참여율을 붙이면 proxy가 실제 capacity curve로 승격됩니다.",
                capacity,
            ),
            row(
                "kelly_sizing",
                str(
                    kelly_sizing.get("status")
                    or (
                        "pass"
                        if sample_count >= min_samples and kelly > 0
                        else (
                            "warn"
                            if sample_count and kelly > 0
                            else ("fail" if sample_count >= min_samples else "missing")
                        )
                    )
                ),
                (
                    "현재 Kelly sizing은 "
                    f"full={_safe_float(kelly_sizing.get('full_kelly_fraction')):.4f}, "
                    f"0.25 Kelly={_safe_float(kelly_sizing.get('fractional_kelly_025')):.4f}, "
                    f"권장 risk={_safe_float(kelly_sizing.get('recommended_risk_pct')):.2f}%입니다."
                    if kelly_sizing
                    else f"현재 fractional Kelly 입력값은 kelly={kelly:.4f}, 0.25 Kelly={metrics.get('fractional_kelly_025', 0):.4f}입니다."
                ),
                "쥬 sizing은 raw Kelly를 그대로 쓰지 않고 fractional Kelly, 표본 품질, MDD, 파산확률, per-block cap을 통과한 권장 risk만 사용해야 합니다.",
                kelly_sizing
                or {
                    "kelly_fraction": kelly,
                    "fractional_kelly_025": metrics.get("fractional_kelly_025", 0),
                },
            ),
            row(
                "mdd_limit",
                str(
                    drawdown_budget.get("status")
                    or (
                        "pass"
                        if max_dd >= -abs(float(self.config.max_drawdown_limit_pct))
                        else "fail"
                    )
                ),
                (
                    "현재 drawdown은 "
                    f"{_safe_float(drawdown_budget.get('current_drawdown_pct')):.2f}%, "
                    "최대 MDD는 "
                    f"{_safe_float(drawdown_budget.get('max_drawdown_pct')):.2f}%, "
                    "권장 risk multiplier는 "
                    f"{_safe_float(drawdown_budget.get('risk_multiplier')):.2f}입니다."
                    if drawdown_budget
                    else f"실거래 equity curve 기준 MDD는 {max_dd:.2f}%입니다."
                ),
                "MDD budget이 소진될수록 쥬는 신규 진입과 volatile lane을 줄이고 회복 우선 모드로 전환해야 합니다.",
                drawdown_budget
                or {
                    "max_drawdown_pct": max_dd,
                    "limit_pct": -abs(float(self.config.max_drawdown_limit_pct)),
                },
            ),
            row(
                "sharpe_ratio",
                risk_ratio_status(sharpe, float(self.config.sharpe_min)),
                (
                    "위험조정 성과는 "
                    f"Sharpe={_safe_float(risk_adjusted.get('sharpe_ratio')):.3f}, "
                    f"vol={_safe_float(risk_adjusted.get('volatility_pct')):.3f}%, "
                    f"grade={risk_adjusted.get('quality_grade')}입니다."
                    if risk_adjusted
                    else f"거래 단위 Sharpe는 {sharpe:.3f}입니다."
                ),
                "Sharpe는 총 변동성 대비 효율이며, 쥬는 이를 Sortino/Calmar와 함께 묶어서 공격 가능성을 판단해야 합니다.",
                risk_adjusted or {"sharpe_ratio": sharpe},
            ),
            row(
                "sortino_ratio",
                risk_ratio_status(sortino, float(self.config.sortino_min)),
                (
                    "하방 위험조정 성과는 "
                    f"Sortino={_safe_float(risk_adjusted.get('sortino_ratio')):.3f}, "
                    "downside deviation="
                    f"{_safe_float(risk_adjusted.get('downside_deviation_pct')):.3f}%, "
                    f"primary flag={risk_adjusted.get('primary_risk_flag')}입니다."
                    if risk_adjusted
                    else f"하방 변동성 기준 Sortino는 {sortino:.3f}입니다."
                ),
                "쥬는 손실 회피가 중요하므로 Sharpe보다 Sortino 악화를 더 크게 반영합니다.",
                risk_adjusted or {"sortino_ratio": sortino},
            ),
            row(
                "calmar_ratio",
                risk_ratio_status(calmar, float(self.config.calmar_min)),
                (
                    "MDD 대비 수익 효율은 "
                    f"Calmar={_safe_float(risk_adjusted.get('calmar_ratio')):.3f}, "
                    "return/drawdown="
                    f"{_safe_float(risk_adjusted.get('return_to_drawdown_ratio')):.3f}입니다."
                    if risk_adjusted
                    else f"Calmar는 {calmar:.3f}입니다."
                ),
                "수익이 MDD 대비 낮으면 공격 lane 증액을 보류하고 회복 우선 모드로 둡니다.",
                risk_adjusted or {"calmar_ratio": calmar},
            ),
            row(
                "profit_factor",
                str(
                    profitability.get("status")
                    or (
                        "pass"
                        if pf >= float(self.config.profit_factor_good)
                        else (
                            "warn"
                            if pf >= float(self.config.profit_factor_min)
                            else ("fail" if sample_count else "missing")
                        )
                    )
                ),
                (
                    "수익팩터는 "
                    f"{_safe_float(profitability.get('profit_factor')):.3f}, "
                    "손실흡수비는 "
                    f"{_safe_float(profitability.get('loss_absorption_ratio')):.3f}, "
                    f"edge={profitability.get('edge_grade')}입니다."
                    if profitability
                    else f"Profit Factor는 {pf:.3f}입니다."
                ),
                "PF 1.5 이상은 양호, 2.0 이상은 우수 후보로 보되 표본 수, 비용 누락, 평균손익비를 함께 확인합니다.",
                profitability or {"profit_factor": pf},
            ),
            row(
                "recovery_factor",
                str(
                    recovery_profile.get("status")
                    or (
                        "pass"
                        if recovery >= float(self.config.recovery_factor_min)
                        else (
                            "warn"
                            if recovery > 0
                            else ("fail" if sample_count else "missing")
                        )
                    )
                ),
                (
                    "Recovery Factor는 "
                    f"{_safe_float(recovery_profile.get('recovery_factor')):.3f}, "
                    "최대낙폭 회복 거래 수는 "
                    f"{recovery_profile.get('recovery_trade_count')}입니다."
                    if recovery_profile
                    else f"Recovery Factor는 {recovery:.3f}입니다."
                ),
                "회복력이 낮거나 최대낙폭을 아직 회복하지 못했으면 쥬는 sizing 회복 속도를 늦춥니다.",
                recovery_profile or {"recovery_factor": recovery},
            ),
            row(
                "risk_of_ruin",
                str(
                    ruin_profile.get("status")
                    or (
                        "pass"
                        if sample_count and ruin <= 1.0
                        else (
                            "warn"
                            if sample_count and ruin <= 5.0
                            else ("fail" if sample_count else "missing")
                        )
                    )
                ),
                (
                    "Monte Carlo 기준 파산확률은 "
                    f"{_safe_float(ruin_profile.get('risk_of_ruin_pct')):.2f}%이고, "
                    "첫 한도 침범 중앙 거래 인덱스는 "
                    f"{ruin_profile.get('median_trade_index_to_ruin')}입니다."
                    if ruin_profile
                    else f"Monte Carlo drawdown threshold 기준 파산확률은 {ruin:.2f}%입니다."
                ),
                (
                    "ruin severity와 governor action을 live authority에 전달해 신규 블록 수, "
                    "lane budget, per-block risk를 줄이거나 정지합니다."
                ),
                ruin_profile
                or {
                    "risk_of_ruin_pct": ruin,
                    "ruin_drawdown_pct": float(self.config.ruin_drawdown_pct),
                },
            ),
            row(
                "regime_test",
                regime_status
                or (
                    "pass"
                    if regime_count >= 2 and regime_worst_expectancy >= 0
                    else (
                        "warn"
                        if regime_count >= 1
                        else "missing"
                    )
                ),
                (
                    f"metadata regime 기준 {regime_count}개 레짐을 분해했고 "
                    f"최저 기대수익률은 {regime_worst_expectancy:.3f}%입니다."
                ),
                "레짐별 표본이 쌓이면 risk_on/risk_off/chop/rotation별 sizing과 lane 권한을 분리합니다.",
                regime,
            ),
            row(
                "correlation",
                correlation_status
                or (
                    "fail"
                    if correlation_method == "rolling_return_window"
                    and max_abs_correlation >= 0.85
                    else (
                        "warn"
                        if correlation_method == "rolling_return_window"
                        and max_abs_correlation >= 0.65
                        else (
                            "pass"
                            if correlation_method == "rolling_return_window"
                            and correlation.get("covered_sample_count")
                            else (
                                "pass"
                                if correlation.get("covered_sample_count")
                                and top_cluster_share <= 60.0
                                else (
                                    "warn"
                                    if correlation.get("covered_sample_count")
                                    and top_cluster_share <= 75.0
                                    else (
                                        "fail"
                                        if correlation.get("covered_sample_count")
                                        else "missing"
                                    )
                                )
                            )
                        )
                    )
                ),
                (
                    "rolling return window가 1개 종목에만 있어 아직 pair correlation을 "
                    "만들 수 없습니다. "
                    f"single symbol={correlation.get('single_symbol')}, "
                    f"window={correlation.get('single_symbol_window_size')}."
                    if correlation_method == "rolling_return_window_single_symbol"
                    else (
                    "rolling return window 기준 최대 절대 상관계수는 "
                    f"{max_abs_correlation:.3f}, top pair는 "
                    f"{correlation.get('top_pair')}입니다."
                    if correlation_method == "rolling_return_window"
                    else (
                        "metadata cluster concentration proxy 기준 최상위 cluster 비중은 "
                        f"{top_cluster_share:.2f}%입니다."
                    )
                    )
                ),
                (
                    "같은 revision에서 두 종목 이상 price window 표본을 쌓은 뒤 "
                    "rolling pair correlation으로 승격합니다."
                    if correlation_method == "rolling_return_window_single_symbol"
                    else "다음 단계에서는 OHLCV rolling correlation matrix와 활성 블록 exposure를 결합합니다."
                ),
                correlation,
            ),
            row(
                "factor_exposure",
                factor_status
                or (
                    "pass"
                    if factor_exposure.get("covered_sample_count")
                    and top_factor_share <= 70.0
                    else (
                        "warn"
                        if factor_exposure.get("covered_sample_count")
                        and top_factor_share <= 85.0
                        else (
                            "fail"
                            if factor_exposure.get("covered_sample_count")
                            else "missing"
                        )
                    )
                ),
                (
                    "metadata factor exposure 기준 최상위 factor 비중은 "
                    f"{top_factor_share:.2f}%입니다."
                ),
                "KIS fundamentals와 crypto factor tags를 공통 exposure vector로 계속 축적합니다.",
                factor_exposure,
            ),
        ]
        return disciplines

    def _summarize_disciplines(self, disciplines: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {"pass": 0, "warn": 0, "fail": 0, "missing": 0}
        for item in disciplines:
            status = _normalize_discipline_status(item.get("status"))
            counts[status] += 1
        expected_total = max(len(DISCIPLINE_DEFINITIONS), len(disciplines))
        if len(disciplines) < expected_total:
            counts["missing"] += expected_total - len(disciplines)
        total = expected_total or 1
        score = (counts["pass"] + counts["warn"] * 0.5) / total * 100.0

        present_ids = {
            str(item.get("id") or "").strip()
            for item in disciplines
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        core_counts = {"pass": 0, "warn": 0, "fail": 0, "missing": 0}
        if present_ids:
            completed_disciplines = _with_absent_disciplines_as_missing(disciplines)
            core_ids = set(OPERATIONAL_READINESS_DISCIPLINE_IDS)
            for item in completed_disciplines:
                discipline_id = str(item.get("id") or "").strip()
                if discipline_id not in core_ids:
                    continue
                status = _normalize_discipline_status(item.get("status"))
                core_counts[status] += 1
            hard_fail_count = core_counts["fail"]
            hard_blocking_count = core_counts["fail"] + core_counts["missing"]
        else:
            hard_fail_count = counts["fail"]
            hard_blocking_count = counts["fail"]

        if hard_blocking_count > 0:
            readiness = "blocked_by_validation"
        elif present_ids and core_counts["warn"] > 0:
            readiness = "probe"
        elif counts["fail"] > 0:
            readiness = "probe"
        elif counts["pass"] >= 10 and counts["missing"] <= 4:
            readiness = "scale_ready"
        elif present_ids:
            readiness = "normal"
        elif counts["pass"] >= 6:
            readiness = "normal"
        elif counts["pass"] >= 3:
            readiness = "probe"
        else:
            readiness = "research_only"
        if hard_blocking_count > 0:
            diagnostic_status = "blocked"
        elif counts["fail"] > 0:
            diagnostic_status = "risk_repair"
        elif counts["missing"] > 0:
            diagnostic_status = "incomplete"
        elif counts["warn"] > 0:
            diagnostic_status = "watch"
        else:
            diagnostic_status = "clear"
        return {
            "total_score": round(score, 2),
            "readiness": readiness,
            "diagnostic_status": diagnostic_status,
            "pass_count": counts["pass"],
            "warn_count": counts["warn"],
            "fail_count": counts["fail"],
            "missing_count": counts["missing"],
            "diagnostic_pass_count": counts["pass"],
            "diagnostic_warn_count": counts["warn"],
            "diagnostic_fail_count": counts["fail"],
            "diagnostic_missing_count": counts["missing"],
            "core_gate_ids": list(OPERATIONAL_READINESS_DISCIPLINE_IDS),
            "core_expected_count": len(OPERATIONAL_READINESS_DISCIPLINE_IDS),
            "core_pass_count": core_counts["pass"],
            "core_warn_count": core_counts["warn"],
            "core_fail_count": core_counts["fail"],
            "core_missing_count": core_counts["missing"],
            "hard_fail_count": hard_fail_count,
            "hard_missing_count": core_counts["missing"],
            "hard_blocking_count": hard_blocking_count,
        }

    @staticmethod
    def _build_remediation_plan(
        disciplines: list[dict[str, Any]],
        *,
        config: TradingValidationConfig | None = None,
    ) -> dict[str, Any]:
        resolved_config = config or TradingValidationConfig()
        priority = {"fail": 0, "missing": 1, "warn": 2, "pass": 3}
        completed_disciplines = _with_absent_disciplines_as_missing(disciplines)
        category_specs = [
            {
                "id": "immediate_ops_controls",
                "label": "운영 즉시조치",
                "purpose": "데이터, 비용, 유동성, 상관/팩터 쏠림처럼 다음 블록 전에 바로 확인할 항목",
                "discipline_ids": {
                    "data_validation",
                    "cost_simulation",
                    "capacity_analysis",
                    "correlation",
                    "factor_exposure",
                },
            },
            {
                "id": "research_validation_work",
                "label": "연구/백테스트 보강",
                "purpose": "과최적화, walk-forward, OOS, stress, regime 증거를 다시 쌓을 항목",
                "discipline_ids": {
                    "overfit_validation",
                    "walk_forward_analysis",
                    "out_of_sample_test",
                    "stress_test",
                    "regime_test",
                },
            },
            {
                "id": "sizing_risk_controls",
                "label": "사이징/리스크 제한",
                "purpose": "켈리, MDD, 파산확률, 위험조정 성과를 근거로 크기와 빈도를 조정할 항목",
                "discipline_ids": {
                    "monte_carlo",
                    "kelly_sizing",
                    "mdd_limit",
                    "sharpe_ratio",
                    "sortino_ratio",
                    "calmar_ratio",
                    "profit_factor",
                    "recovery_factor",
                    "risk_of_ruin",
                },
            },
        ]
        category_by_discipline = {
            discipline_id: str(spec["id"])
            for spec in category_specs
            for discipline_id in spec["discipline_ids"]
        }

        weak = sorted(
            [
                row
                for row in completed_disciplines
                if _normalize_discipline_status(row.get("status")) != "pass"
            ],
            key=lambda row: (
                priority.get(_normalize_discipline_status(row.get("status")), 9),
                str(row.get("label") or row.get("id") or ""),
            ),
        )

        def compact(row: dict[str, Any]) -> dict[str, Any]:
            metric = row.get("metric") if isinstance(row.get("metric"), dict) else {}
            return {
                "discipline_id": str(row.get("id") or ""),
                "label": str(row.get("label") or row.get("id") or ""),
                "status": _normalize_discipline_status(row.get("status")),
                "evidence": str(row.get("evidence") or ""),
                "action": str(row.get("action") or row.get("purpose") or ""),
                "metric_status": str(metric.get("status") or metric.get("validation_status") or ""),
            }

        def work_item(row: dict[str, Any]) -> dict[str, Any]:
            discipline_id = str(row.get("id") or "")
            status = _normalize_discipline_status(row.get("status"))
            metric = row.get("metric") if isinstance(row.get("metric"), dict) else {}
            category_id = category_by_discipline.get(discipline_id, "validation_work")
            owner = "validation_lab"
            cadence = "next_validation_run"
            lane_policy_hint = "keep_lane_probe_until_validated"
            blocks_scaling = "no_scale_up_until_validated"
            blocks_new_entries = "scale_up"
            runner_hint = "tradecraft-live-evaluator refresh_trading_validation"
            verification_artifact = (
                "latest trading_validation payload contains this discipline with "
                "status=pass and non-empty metric/evidence."
            )
            exit_criteria = (
                f"{discipline_id} status becomes pass and evidence is present in "
                "the latest validation payload."
            )
            priority_code = "p2"
            evidence_targets: dict[str, Any] = {
                "min_live_alpha_samples": int(resolved_config.min_sample_count),
                "next_validation_status": "pass",
            }
            validation_mode = "refresh_validation"
            live_shadow_required = False
            scale_up_blocked = True
            allowed_entry_posture = "probe_or_waiting_entry"
            if discipline_id == "data_validation":
                owner = "data_pipeline"
                cadence = "before_next_manager_run"
                lane_policy_hint = "quote_verified_only"
                blocks_scaling = "no_scale_up_until_data_clean"
                blocks_new_entries = "scale_up_and_unverified_immediate_entries"
                runner_hint = (
                    "sync_live_performance_and_edges -> refresh_trading_validation"
                )
                verification_artifact = (
                    "live_performance data_quality shows invalid_price_count=0, "
                    "upstream_error_count=0, and latest data_validation status=pass."
                )
                exit_criteria = (
                    "latest data_validation is pass, invalid_price_count=0, "
                    "upstream_error_count=0, and missing_cost samples are handled "
                    "by cost_simulation."
                )
                priority_code = "p0"
                validation_mode = "data_repair_before_trade"
                allowed_entry_posture = "verified_quote_waiting_entry"
                evidence_targets.update(
                    {
                        "max_invalid_price_count": 0,
                        "max_upstream_error_count": 0,
                        "max_hard_issue_rate_pct": 0.0,
                        "requires_cost_handoff": True,
                    }
                )
            elif discipline_id == "cost_simulation":
                owner = "cost_model"
                cadence = "before_next_manager_run"
                lane_policy_hint = "cost_verified_waiting_entry"
                blocks_scaling = "reduce_cost_weak_lanes"
                blocks_new_entries = "cost_weak_immediate_entries"
                runner_hint = (
                    "sync precise fills/costs -> sync_live_performance_and_edges "
                    "-> refresh_trading_validation"
                )
                verification_artifact = (
                    "latest cost_simulation metric includes recorded fee/tax/spread/"
                    "slippage/funding components and remains net-positive under "
                    "2x cost stress."
                )
                exit_criteria = (
                    "latest cost_simulation is pass or warn, cost components are "
                    "recorded, and 2x cost stress stays net-positive for the lane."
                )
                priority_code = "p0" if status == "fail" else "p1"
                validation_mode = "cost_evidence_repair"
                allowed_entry_posture = "cost_verified_waiting_entry"
                evidence_targets.update(
                    {
                        "min_recorded_cost_coverage_pct": 60.0,
                        "required_cost_components": [
                            "fees",
                            "taxes_or_funding",
                            "spread",
                            "slippage",
                        ],
                        "min_cost_stress_net_pnl_multiplier": "2x_positive",
                    }
                )
            elif discipline_id in {
                "overfit_validation",
                "walk_forward_analysis",
                "out_of_sample_test",
            }:
                owner = "pattern_lab"
                cadence = "next_research_cycle"
                lane_policy_hint = "shadow_or_waiting_only_until_wfa_rebuilt"
                blocks_scaling = "no_scale_up_until_wfa_oos_clean"
                blocks_new_entries = "scale_up_and_unvalidated_immediate_entries"
                runner_hint = (
                    "crypto_pattern_lab/kr_equity_pattern_lab rebuild active "
                    "sets, WFA windows, OOS slices, then refresh_trading_validation"
                )
                verification_artifact = (
                    "pattern lab DB contains active strategy sets with OOS evidence, "
                    "rolling WFA windows, and no high-overfit active set."
                )
                exit_criteria = (
                    "active strategy set has OOS evidence, rolling WFA windows, "
                    "and no high-overfit active set for this venue/lane."
                )
                priority_code = "p0" if status == "fail" else "p1"
                validation_mode = "backtest_wfa_oos_rebuild"
                live_shadow_required = True
                allowed_entry_posture = "shadow_or_waiting_entry_only"
                evidence_targets.update(
                    {
                        "min_active_strategy_sets": 1,
                        "min_walk_forward_windows": 3,
                        "min_walk_forward_pass_rate_pct": 70.0,
                        "min_out_of_sample_slices": 1,
                        "min_out_of_sample_profit_factor": 1.05,
                        "max_train_test_expectancy_gap_r": 0.2,
                        "requires_live_shadow_before_scale_up": True,
                    }
                )
            elif discipline_id in {"stress_test", "regime_test"}:
                owner = "scenario_lab"
                cadence = "daily_or_after_regime_change"
                lane_policy_hint = "regime_confirmed_only"
                blocks_scaling = "regime_mismatch_probe_only"
                blocks_new_entries = "regime_mismatch_scale_up"
                runner_hint = (
                    "update market_pulse/pattern_lab scenario metrics, then "
                    "refresh_trading_validation"
                )
                verification_artifact = (
                    "scenario/regime metrics include current regime coverage, "
                    "stress drawdown, and weak-regime lane notes."
                )
                exit_criteria = (
                    "latest scenario/regime validation is pass or warn and weak "
                    "regime notes are reflected in lane authority."
                )
                priority_code = "p1"
                validation_mode = "scenario_regime_replay"
                live_shadow_required = discipline_id == "regime_test"
                allowed_entry_posture = "regime_matched_probe"
                evidence_targets.update(
                    {
                        "min_regime_buckets": 2,
                        "max_stress_mdd_pct": float(
                            resolved_config.max_drawdown_limit_pct
                        ),
                        "requires_current_regime_coverage": True,
                    }
                )
            elif discipline_id in {
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
                owner = "risk_engine"
                cadence = "every_validation_run"
                lane_policy_hint = "risk_budget_probe_until_ratios_recover"
                blocks_scaling = "fractional_kelly_probe_only"
                blocks_new_entries = "risk_budget_expansion"
                runner_hint = (
                    "refresh_trading_validation, then rebuild live_authority "
                    "risk_budget_passport from lane scorecards"
                )
                verification_artifact = (
                    "latest risk metrics include fractional Kelly, MDD, risk of "
                    "ruin, PF/recovery ratios, and a positive recommended risk "
                    "fraction without hard pressure."
                )
                exit_criteria = (
                    "risk metrics are pass or warn, recommended risk fraction is "
                    "positive, and the lane has no ruin/MDD hard pressure."
                )
                priority_code = "p0" if status == "fail" else "p1"
                validation_mode = "risk_budget_recalibration"
                allowed_entry_posture = "fractional_kelly_probe"
                evidence_targets.update(
                    {
                        "max_mdd_pct": float(resolved_config.max_drawdown_limit_pct),
                        "max_risk_of_ruin_pct": 5.0,
                        "max_full_kelly_used_fraction": 0.25,
                        "min_profit_factor": float(resolved_config.profit_factor_min),
                        "min_recovery_factor": float(
                            resolved_config.recovery_factor_min
                        ),
                    }
                )
            elif discipline_id == "capacity_analysis":
                owner = "execution_engine"
                cadence = "before_size_increase"
                lane_policy_hint = "depth_checked_only"
                blocks_scaling = "cap_by_depth_and_turnover"
                blocks_new_entries = "size_increase"
                runner_hint = (
                    "refresh liquidity/depth/turnover evidence, then "
                    "refresh_trading_validation"
                )
                verification_artifact = (
                    "capacity metric includes intended notional coverage, "
                    "depth/turnover ratio, and min capacity ratio above threshold."
                )
                exit_criteria = (
                    "capacity_analysis is pass or warn with depth/turnover "
                    "coverage for the lane's intended notional."
                )
                priority_code = "p0" if status == "fail" else "p1"
                validation_mode = "capacity_depth_check"
                allowed_entry_posture = "depth_checked_probe"
                evidence_targets.update(
                    {
                        "min_capacity_ratio": 5.0,
                        "requires_orderbook_depth": True,
                        "requires_turnover_participation_check": True,
                    }
                )
            elif discipline_id in {"correlation", "factor_exposure"}:
                owner = "portfolio_risk"
                cadence = "before_new_correlated_block"
                lane_policy_hint = "avoid_unpriced_concentration"
                blocks_scaling = "cap_correlated_exposure"
                blocks_new_entries = "correlated_or_factor_concentrated_entries"
                runner_hint = (
                    "refresh portfolio exposure/regime/factor snapshots, then "
                    "refresh_trading_validation"
                )
                verification_artifact = (
                    "correlation/factor metrics include active exposure buckets, "
                    "concentration flags, and pass/warn status for current blocks."
                )
                exit_criteria = (
                    "correlation/factor exposure is pass or warn and active "
                    "exposure is distributed across symbols, sectors, or factors."
                )
                priority_code = "p1"
                validation_mode = "portfolio_exposure_check"
                allowed_entry_posture = "exposure_capped_probe"
                evidence_targets.update(
                    {
                        "max_top_cluster_share_pct": 60.0,
                        "max_top_factor_share_pct": 70.0,
                        "requires_active_block_exposure_snapshot": True,
                    }
                )
            if status == "warn" and discipline_id not in {
                "data_validation",
                "cost_simulation",
            }:
                scale_up_blocked = discipline_id in {
                    "overfit_validation",
                    "walk_forward_analysis",
                    "out_of_sample_test",
                    "monte_carlo",
                    "kelly_sizing",
                    "mdd_limit",
                    "risk_of_ruin",
                }
            repair_action_id = (
                f"validation_repair.{validation_mode}.{discipline_id}"
            )
            automation_hook_by_mode = {
                "data_repair_before_trade": "sync_live_performance_and_edges",
                "cost_evidence_repair": "sync_live_performance_and_edges",
                "backtest_wfa_oos_rebuild": "pattern_lab_rebuild_wfa_oos",
                "scenario_regime_replay": "market_pulse_scenario_replay",
                "risk_budget_recalibration": "refresh_risk_budget_passport",
                "capacity_depth_check": "refresh_depth_capacity_snapshot",
                "portfolio_exposure_check": "refresh_portfolio_exposure_snapshot",
                "refresh_validation": "refresh_trading_validation",
            }
            execution_weight_by_mode = {
                "data_repair_before_trade": "lightweight",
                "cost_evidence_repair": "lightweight",
                "backtest_wfa_oos_rebuild": "external_runner",
                "scenario_regime_replay": "scheduled_research",
                "risk_budget_recalibration": "lightweight",
                "capacity_depth_check": "market_data_light",
                "portfolio_exposure_check": "lightweight",
                "refresh_validation": "lightweight",
            }
            automation_hook = automation_hook_by_mode.get(
                validation_mode,
                "refresh_trading_validation",
            )
            execution_weight = execution_weight_by_mode.get(
                validation_mode,
                "lightweight",
            )
            current_gap = {
                "fail": "evidence_failed_threshold",
                "missing": "evidence_missing",
                "warn": "evidence_thin_or_not_scalable",
            }.get(status, "validation_refresh_needed")
            pass_path = {
                "version": "validation_pass_path_v1",
                "current_gap": current_gap,
                "required_evidence": evidence_targets,
                "collection_hook": automation_hook,
                "collection_cadence": cadence,
                "verification_artifact": verification_artifact,
                "pass_criteria": exit_criteria,
                "jue_behavior_until_pass": {
                    "allowed_entry_posture": allowed_entry_posture,
                    "blocks_scaling": blocks_scaling,
                    "blocks_new_entries": blocks_new_entries,
                    "scale_up_blocked": scale_up_blocked,
                    "live_shadow_required": live_shadow_required,
                },
                "m1_runtime_profile": {
                    "execution_weight": execution_weight,
                    "prefer_incremental_refresh": execution_weight
                    in {"lightweight", "market_data_light"},
                    "avoid_full_rebuild_in_manager_prompt": (
                        execution_weight == "external_runner"
                    ),
                },
            }
            return {
                "task_id": f"validation:{discipline_id}:{status}",
                "repair_action_id": repair_action_id,
                "discipline_id": discipline_id,
                "category_id": category_id,
                "status": status,
                "priority": priority_code,
                "owner": owner,
                "cadence": cadence,
                "automation_hook": automation_hook,
                "execution_weight": execution_weight,
                "lane_policy_hint": lane_policy_hint,
                "blocks_scaling": blocks_scaling,
                "blocks_new_entries": blocks_new_entries,
                "runner_hint": runner_hint,
                "verification_artifact": verification_artifact,
                "exit_criteria": exit_criteria,
                "pass_path": pass_path,
                "validation_mode": validation_mode,
                "evidence_targets": evidence_targets,
                "live_shadow_required": live_shadow_required,
                "scale_up_blocked": scale_up_blocked,
                "allowed_entry_posture": allowed_entry_posture,
                "metric_status": str(
                    metric.get("status") or metric.get("validation_status") or ""
                ),
            }

        categories: list[dict[str, Any]] = []
        for spec in category_specs:
            ids = spec["discipline_ids"]
            items = [compact(row) for row in weak if str(row.get("id") or "") in ids]
            categories.append(
                {
                    "id": spec["id"],
                    "label": spec["label"],
                    "purpose": spec["purpose"],
                    "weak_count": len(items),
                    "fail_count": sum(1 for item in items if item["status"] == "fail"),
                    "items": items,
                }
            )

        failed_count = sum(
            1
            for row in weak
            if _normalize_discipline_status(row.get("status")) == "fail"
        )
        missing_count = sum(
            1
            for row in weak
            if _normalize_discipline_status(row.get("status")) == "missing"
        )
        weak_count = len(weak)
        status = "needs_work" if weak_count else "clear"
        top_priority = [compact(row) for row in weak[:8]]
        primary_next_action = top_priority[0]["action"] if top_priority else ""
        work_queue_all = [work_item(row) for row in weak]
        work_priority = {"p0": 0, "p1": 1, "p2": 2}
        work_queue = sorted(
            work_queue_all,
            key=lambda row: (
                work_priority.get(str(row.get("priority") or ""), 9),
                priority.get(str(row.get("status") or ""), 9),
                str(row.get("category_id") or ""),
                str(row.get("discipline_id") or ""),
            ),
        )[:12]
        work_ids = {str(row.get("discipline_id") or "") for row in work_queue}
        for row in work_queue_all:
            discipline_id = str(row.get("discipline_id") or "")
            if discipline_id in work_ids:
                continue
            if discipline_id in {
                "data_validation",
                "cost_simulation",
                "capacity_analysis",
                "walk_forward_analysis",
                "kelly_sizing",
                "risk_of_ruin",
                "mdd_limit",
            } and len(work_queue) < 12:
                work_queue.append(row)
                work_ids.add(discipline_id)
        risk_discipline_ids = {
            "monte_carlo",
            "kelly_sizing",
            "mdd_limit",
            "risk_of_ruin",
        }
        data_cost_capacity_ids = {
            "data_validation",
            "cost_simulation",
            "capacity_analysis",
        }
        research_discipline_ids = {
            "overfit_validation",
            "walk_forward_analysis",
            "out_of_sample_test",
        }
        risk_fail = any(
            str(row.get("discipline_id") or "") in {
                *risk_discipline_ids,
            }
            and str(row.get("status") or "") == "fail"
            for row in work_queue_all
        )
        risk_gap = any(
            str(row.get("discipline_id") or "") in risk_discipline_ids
            and str(row.get("status") or "") in {"fail", "missing"}
            for row in work_queue_all
        )
        data_or_cost_gap = any(
            str(row.get("discipline_id") or "") in data_cost_capacity_ids
            and str(row.get("status") or "") in {"fail", "missing"}
            for row in work_queue_all
        )
        research_gap = any(
            str(row.get("discipline_id") or "") in research_discipline_ids
            and str(row.get("status") or "") in {"fail", "missing"}
            for row in work_queue_all
        )
        core_missing_ids = [
            str(row.get("discipline_id") or "")
            for row in work_queue_all
            if str(row.get("discipline_id") or "")
            in set(OPERATIONAL_READINESS_DISCIPLINE_IDS)
            and str(row.get("status") or "") == "missing"
        ]
        core_fail_ids = [
            str(row.get("discipline_id") or "")
            for row in work_queue_all
            if str(row.get("discipline_id") or "")
            in set(OPERATIONAL_READINESS_DISCIPLINE_IDS)
            and str(row.get("status") or "") == "fail"
        ]
        trade_blocking = bool(core_missing_ids or core_fail_ids)
        if failed_count:
            status = "blocked" if trade_blocking else "probe_rebuild"
        if status == "probe_rebuild":
            primary_next_action = (
                "스케일업은 검증 통과 전까지 막되, 거래를 멈추지 말고 "
                "작은 대기진입 probe 블록으로 비용 낮은 lane과 실행 가능한 "
                "가격 구조를 계속 시험해 표본을 쌓습니다."
            )
        elif status == "needs_work" and not trade_blocking:
            primary_next_action = (
                "스케일업은 표본과 지표가 더 쌓일 때까지 조절하되, 좋은 "
                "위치의 대기진입 probe 블록과 작게 검증 가능한 후보를 계속 "
                "만들어 표본을 확장합니다."
            )
        blocking_scope = (
            "trade"
            if trade_blocking
            else ("scale_up_only" if weak_count else "none")
        )
        scale_up_blocked_ids = [
            str(row.get("discipline_id") or "")
            for row in work_queue_all
            if bool(row.get("scale_up_blocked"))
        ]
        execution_weight_counts: dict[str, int] = {}
        automation_hooks: list[str] = []
        for row in work_queue_all:
            execution_weight = str(row.get("execution_weight") or "unknown")
            execution_weight_counts[execution_weight] = (
                execution_weight_counts.get(execution_weight, 0) + 1
            )
            automation_hook = str(row.get("automation_hook") or "").strip()
            if automation_hook and automation_hook not in automation_hooks:
                automation_hooks.append(automation_hook)
        entry_mode = "normal"
        if risk_fail:
            entry_mode = "risk_off_recovery"
        elif data_or_cost_gap or research_gap or risk_gap:
            entry_mode = "verified_waiting_probe"
        return {
            "status": status,
            "trade_blocking": trade_blocking,
            "blocking_scope": blocking_scope,
            "weak_count": weak_count,
            "failed_count": failed_count,
            "missing_count": missing_count,
            "primary_next_action": primary_next_action,
            "lane_policy_hints": {
                "version": "validation_lane_policy_hints_v2",
                "trade_blocking": trade_blocking,
                "blocking_scope": blocking_scope,
                "scale_up_allowed": weak_count == 0,
                "entry_mode": entry_mode,
                "requires_verified_quotes": data_or_cost_gap,
                "requires_capacity_check": any(
                    str(row.get("discipline_id") or "") == "capacity_analysis"
                    and str(row.get("status") or "") in {"fail", "missing"}
                    for row in work_queue_all
                ),
                "requires_shadow_or_waiting_entry": research_gap,
                "risk_budget_mode": "probe" if risk_gap else "normal",
                "core_missing_ids": core_missing_ids[:6],
                "core_fail_ids": core_fail_ids[:6],
                "scale_up_blocked_discipline_ids": scale_up_blocked_ids[:12],
                "weak_lane_default": "observe_or_waiting_probe",
            },
            "pass_path_summary": {
                "version": "validation_pass_path_summary_v1",
                "weak_count": weak_count,
                "scale_up_blocked_count": len(scale_up_blocked_ids),
                "automation_hooks": automation_hooks[:8],
                "execution_weight_counts": execution_weight_counts,
                "m1_runtime_guidance": (
                    "Keep lightweight checks incremental; run pattern-lab/"
                    "WFA/OOS rebuilds outside manager prompts, then feed only "
                    "compressed pass/fail evidence back to Jue."
                ),
            },
            "work_queue": work_queue,
            "categories": categories,
            "top_priority": top_priority,
        }

    def _operator_guidance(
        self,
        disciplines: list[dict[str, Any]],
        metrics: dict[str, Any] | None = None,
    ) -> list[str]:
        priority = {"fail": 0, "missing": 1, "warn": 2, "pass": 3}
        completed_disciplines = _with_absent_disciplines_as_missing(disciplines)
        weak = sorted(
            [
                row
                for row in completed_disciplines
                if _normalize_discipline_status(row.get("status")) != "pass"
            ],
            key=lambda row: priority.get(
                _normalize_discipline_status(row.get("status")),
                9,
            ),
        )
        attribution = (
            metrics.get("failure_attribution")
            if isinstance(metrics, dict)
            and isinstance(metrics.get("failure_attribution"), dict)
            else {}
        )
        recovery_focus = (
            attribution.get("recovery_focus")
            if isinstance(attribution, dict)
            else []
        )
        guidance = self._pattern_lab_operator_guidance(metrics)
        if isinstance(recovery_focus, list) and recovery_focus:
            guidance.append(f"실패 귀속: {recovery_focus[0]}")
        guidance.extend(
            f"{row['label']}: {row['action']}"
            for row in weak[:6]
        )
        return guidance[:7]

    @staticmethod
    def _pattern_lab_operator_guidance(metrics: dict[str, Any] | None) -> list[str]:
        if not isinstance(metrics, dict):
            return []
        pattern_lab = metrics.get("pattern_lab")
        if not isinstance(pattern_lab, dict):
            return []
        action_by_reason = {
            "active_walk_forward_windows_missing": (
                "active set의 rolling WFA window를 재생성해야 합니다"
            ),
            "active_out_of_sample_missing": (
                "active set의 OOS evidence를 재생성해야 합니다"
            ),
            "active_overfit_unknown": (
                "active set의 overfit risk를 다시 산정해야 합니다"
            ),
            "active_overfit_high": (
                "high-overfit active set을 rejected로 내리고 재최적화해야 합니다"
            ),
            "out_of_sample_expectancy_negative": (
                "rejected set의 OOS 기대값이 음수라서 진입 가설/청산 규칙을 재분리해야 합니다"
            ),
            "out_of_sample_profit_factor_low": (
                "rejected set의 OOS 수익팩터가 낮아서 비용·익절·손절 구조를 재검증해야 합니다"
            ),
            "out_of_sample_missing": (
                "OOS 표본이 부족한 set은 active 후보에서 제외하고 추가 실거래/리플레이 표본을 쌓아야 합니다"
            ),
            "walk_forward_pass_rate_low": (
                "rolling WFA 통과율이 낮아서 구간별 regime/feature를 나눠 재최적화해야 합니다"
            ),
        }
        out: list[str] = []

        reasons = pattern_lab.get("validation_reasons")
        if not isinstance(reasons, list):
            reasons = []
        failed_reasons = pattern_lab.get("failed_reasons")
        if not reasons and isinstance(failed_reasons, dict):
            reasons = [
                str(reason)
                for reason, _count in sorted(
                    failed_reasons.items(),
                    key=lambda item: (-int(item[1] or 0), str(item[0])),
                )
            ][:3]

        for raw_reason in reasons[:3]:
            reason = str(raw_reason or "").strip()
            if not reason:
                continue
            action = action_by_reason.get(
                reason,
                "패턴랩 validation failure를 먼저 복구해야 합니다",
            )
            out.append(f"패턴랩: {action} ({reason})")
        return out
