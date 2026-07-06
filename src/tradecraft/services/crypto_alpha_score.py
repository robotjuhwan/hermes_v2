from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bounded(value: float, *, floor: float = 0.0, ceiling: float = 100.0) -> float:
    return max(min(float(value), ceiling), floor)


def score_crypto_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    change_pct = _to_float(candidate.get("change_pct_24h"))
    volume_expansion = _to_float(candidate.get("volume_expansion_ratio"))
    spread_bps = _to_float(candidate.get("spread_bps"))
    depth_usdt = _to_float(
        candidate.get("orderbook_depth_usdt")
        or candidate.get("depth_usdt")
        or candidate.get("book_depth_usdt")
    )
    wick_risk = _to_float(candidate.get("wick_risk_score"))
    funding_rate = _to_float(candidate.get("funding_rate"))
    open_interest = _to_float(candidate.get("open_interest") or candidate.get("open_interest_usdt"))
    squeeze_score = _to_float(candidate.get("squeeze_risk_score") or candidate.get("squeeze_score"))
    alpha_event_score = _to_float(candidate.get("alpha_event_score"))
    pattern_prior_score = _to_float(
        candidate.get("qualified_pattern_score") or candidate.get("pattern_prior_score")
    )
    live_multiplier = _to_float(candidate.get("live_authority_multiplier") or 1.0)
    if live_multiplier <= 0:
        live_multiplier = 1.0

    drivers: list[str] = []
    risks: list[str] = []
    score = 35.0

    if abs(change_pct) >= 6.0:
        score += min(abs(change_pct) * 0.8, 15.0)
        drivers.append("strong_momentum")
    if volume_expansion >= 1.5:
        score += min(volume_expansion * 5.0, 18.0)
        drivers.append("volume_expansion")
    if depth_usdt >= 100_000:
        score += 8.0
        drivers.append("book_depth")
    elif 0 < depth_usdt < 10_000:
        score -= 20.0
        risks.append("depth_too_thin")
    if spread_bps <= 20.0:
        score += 4.0
    elif spread_bps >= 80.0:
        score -= 24.0
        risks.append("spread_too_wide")
    elif spread_bps >= 40.0:
        score -= 10.0
        risks.append("spread_cost_high")
    if wick_risk >= 75.0:
        score -= 18.0
        risks.append("wick_risk_high")
    elif wick_risk and wick_risk <= 45.0:
        score += 5.0
        drivers.append("wick_risk_contained")
    if abs(funding_rate) >= 0.0001:
        score += min(abs(funding_rate) * 25_000.0, 8.0)
        drivers.append("funding_dislocation")
    if open_interest >= 10_000_000:
        score += min(open_interest / 10_000_000.0, 8.0)
        drivers.append("open_interest_confirmed")
    if squeeze_score >= 65.0:
        score += min(squeeze_score * 0.18, 16.0)
        drivers.append("squeeze_setup")
    if alpha_event_score >= 50.0:
        score += min(alpha_event_score * 0.16, 14.0)
        drivers.append("alpha_event")
    if pattern_prior_score > 0:
        score += min(pattern_prior_score * 0.15, 12.0)
        drivers.append("qualified_pattern_prior")

    score *= min(max(live_multiplier, 0.25), 1.5)
    reject = (
        "spread_too_wide" in risks
        or "depth_too_thin" in risks
        or ("wick_risk_high" in risks and spread_bps >= 40.0)
    )
    if reject:
        score = min(score, 55.0)

    if change_pct > 1.0 and "wick_risk_high" not in risks:
        directional_bias = "long"
    elif change_pct < -1.0 or ("wick_risk_high" in risks and change_pct > 0):
        directional_bias = "short"
    else:
        directional_bias = "neutral"

    return {
        "version": "crypto_alpha_score_v3",
        "total_score": round(_bounded(score), 4),
        "directional_bias": directional_bias,
        "drivers": drivers[:8],
        "risks": risks[:8],
        "reject": reject,
    }
