from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from tradecraft.services.binance_lane import (
    binance_block_lane,
    binance_market_side_lane,
    normalize_binance_display_lane,
    normalize_binance_horizon,
)
from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    normalize_market,
    normalize_position_side,
    upbit_market_symbol,
    upbit_market_to_usdt_symbol,
)
from tradecraft.services.crypto_alpha_score import score_crypto_candidate
from tradecraft.services.performance_policy import (
    performance_profit_factor,
    performance_recovery_factor,
)


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


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _market_side_lane(row: dict[str, Any]) -> str:
    return binance_market_side_lane(
        row,
        normalize_market=normalize_market,
        normalize_position_side=normalize_position_side,
    )


def scorecard_allows_budget_scale(
    scorecard: dict[str, Any],
    *,
    min_samples: int,
    win_rate_threshold: float,
) -> bool:
    sample_count = _safe_int(scorecard.get("sample_count"))
    if sample_count < max(min_samples, 1):
        return False
    if _safe_float(scorecard.get("pnl_usdt") or scorecard.get("realized_pnl_usdt")) <= 0:
        return False
    if _safe_float(scorecard.get("avg_r_multiple")) <= 0:
        return False
    if _safe_float(scorecard.get("win_rate_pct")) < win_rate_threshold:
        return False
    if _safe_float(scorecard.get("profit_factor")) < 1.2:
        return False
    if sample_count >= 3 and _safe_float(scorecard.get("recovery_factor")) < 1.0:
        return False
    if _safe_float(scorecard.get("max_drawdown_r_multiple")) <= -2.0:
        return False
    return True


def lane_card_is_distressed(
    lane_card: dict[str, Any],
    *,
    distressed_min_samples: int,
    lane_min_samples: int,
    max_win_rate_pct: float,
    max_profit_factor: float,
) -> bool:
    sample_count = _safe_int(lane_card.get("sample_count"))
    min_samples = max(
        _safe_int(distressed_min_samples),
        _safe_int(lane_min_samples),
        1,
    )
    if sample_count < min_samples:
        return False
    pnl = _safe_float(lane_card.get("pnl_usdt"))
    win_rate = _safe_float(lane_card.get("win_rate_pct"))
    avg_r = _safe_float(lane_card.get("avg_r_multiple"))
    profit_factor = _safe_float(lane_card.get("profit_factor"))
    max_drawdown_r = _safe_float(lane_card.get("max_drawdown_r_multiple"))
    max_win_rate = _safe_float(max_win_rate_pct)
    max_profit_factor_value = _safe_float(max_profit_factor)
    weak_threshold = (
        win_rate <= max(max_win_rate, 0.0)
        and profit_factor < max(max_profit_factor_value, 0.0)
    )
    severe_small_sample_threshold = (
        avg_r <= -0.35
        and max_drawdown_r <= -4.0
        and win_rate <= max(max_win_rate + 5.0, 0.0)
        and profit_factor < max(max_profit_factor_value, 0.65)
    )
    return pnl < 0 and avg_r < 0 and (weak_threshold or severe_small_sample_threshold)


def weak_lane_profit_protection_trigger(
    weak_lane_context: dict[str, Any],
    *,
    weak_trigger_r: float,
    distressed_trigger_r: float,
) -> tuple[float, str]:
    weak_trigger = max(_safe_float(weak_trigger_r), 0.0)
    if bool((weak_lane_context or {}).get("distressed")):
        distressed_trigger = max(_safe_float(distressed_trigger_r), 0.0)
        if distressed_trigger > 0 and (
            weak_trigger <= 0 or distressed_trigger < weak_trigger
        ):
            return distressed_trigger, "distressed_performance_lane"
    return weak_trigger, "weak_performance_lane"


def budget_scope_from_scorecard_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sample_count = sum(_safe_int(row.get("sample_count")) for row in rows)
    if sample_count <= 0:
        return {}
    realized_pnl = sum(_safe_float(row.get("pnl_usdt")) for row in rows)
    weighted_win = sum(
        _safe_float(row.get("win_rate_pct")) * _safe_int(row.get("sample_count"))
        for row in rows
    )
    weighted_r = sum(
        _safe_float(row.get("avg_r_multiple")) * _safe_int(row.get("sample_count"))
        for row in rows
    )
    gross_profit = sum(_safe_float(row.get("gross_profit_usdt")) for row in rows)
    gross_loss = sum(_safe_float(row.get("gross_loss_usdt")) for row in rows)
    max_drawdown_usdt = min(_safe_float(row.get("max_drawdown_usdt")) for row in rows)
    max_drawdown_r = min(
        _safe_float(row.get("max_drawdown_r_multiple")) for row in rows
    )
    recovery_factor = performance_recovery_factor(
        total_return=realized_pnl,
        max_drawdown=max_drawdown_usdt,
    )
    return {
        "sample_count": sample_count,
        "realized_pnl_usdt": realized_pnl,
        "win_rate_pct": weighted_win / sample_count,
        "avg_r_multiple": weighted_r / sample_count,
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": performance_profit_factor([gross_profit, gross_loss]),
        "max_drawdown_usdt": max_drawdown_usdt,
        "max_drawdown_r_multiple": max_drawdown_r,
        "recovery_factor": recovery_factor,
    }


def performance_scope_for_budget(
    performance: dict[str, Any],
    *,
    market: str,
    side: str = "",
    lane: str = "",
) -> dict[str, Any]:
    market_key = normalize_market(market)
    side_key = normalize_position_side(side) if str(side or "").strip() else ""
    lane_key = str(lane or "").strip().lower()
    lane_cards = performance.get("lane_scorecards")
    lane_candidates = [lane_key] if lane_key else []
    if lane_key in {"short", "mid", "long"} and market_key != "futures" and side_key:
        lane_candidates = [f"{market_key}:{side_key}:{lane_key}", lane_key]
    if (
        lane_key
        and lane_key != "futures"
        and isinstance(lane_cards, list)
        and lane_cards
    ):
        scoped_lanes = [
            row
            for row in lane_cards
            if isinstance(row, dict)
            and str(row.get("lane") or "").strip().lower() in lane_candidates
        ]
        if not scoped_lanes:
            return {}
        return budget_scope_from_scorecard_rows(scoped_lanes)
    cards = performance.get("side_scorecards")
    if isinstance(cards, list) and cards:
        if side_key:
            exact_key = f"{market_key}:{side_key}"
            scoped_cards = [
                row
                for row in cards
                if isinstance(row, dict)
                and str(row.get("side") or "").strip().lower() == exact_key
            ]
        else:
            scoped_cards = [
                row
                for row in cards
                if isinstance(row, dict)
                and str(row.get("side") or "").strip().lower().startswith(f"{market_key}:")
            ]
        if not scoped_cards:
            return {}
        return budget_scope_from_scorecard_rows(scoped_cards)
    if lane_key and isinstance(lane_cards, list) and lane_cards:
        scoped_lanes = [
            row
            for row in lane_cards
            if isinstance(row, dict)
            and str(row.get("lane") or "").strip().lower() in lane_candidates
        ]
        if not scoped_lanes:
            return {}
        return budget_scope_from_scorecard_rows(scoped_lanes)
    return {
        "sample_count": _safe_int(performance.get("sample_count")),
        "realized_pnl_usdt": _safe_float(performance.get("realized_pnl_usdt")),
        "win_rate_pct": _safe_float(performance.get("win_rate_pct")),
        "avg_r_multiple": _safe_float(performance.get("avg_r_multiple")),
        "profit_factor": _safe_float(performance.get("profit_factor")),
        "max_drawdown_usdt": _safe_float(performance.get("max_drawdown_usdt")),
        "max_drawdown_r_multiple": _safe_float(
            performance.get("max_drawdown_r_multiple")
        ),
        "recovery_factor": _safe_float(performance.get("recovery_factor")),
    }


def quote_budget_details_from_amount(
    *,
    market: str,
    quote_budget: float,
    upbit_usdt_krw_rate: float = 1.0,
) -> dict[str, Any]:
    normalized_market = normalize_market(market)
    amount = round(max(_safe_float(quote_budget), 0.0), 2)
    if normalized_market == UPBIT_SPOT_MARKET:
        rate = max(_safe_float(upbit_usdt_krw_rate), 1.0)
        return {
            "quote_budget": amount,
            "quote_currency": "KRW",
            "quote_budget_krw": amount,
            "quote_budget_usdt": round(amount / rate, 6),
        }
    return {
        "quote_budget": amount,
        "quote_currency": "USDT",
        "quote_budget_usdt": amount,
        "quote_budget_krw": 0.0,
    }


def candidate_quote_budget_usdt(
    *,
    market: str,
    cash_usdt: float,
    futures_quote_budget_pct: float,
    futures_min_quote_budget_usdt: float,
    futures_max_quote_budget_usdt: float,
    spot_quote_budget_pct: float,
    spot_min_quote_budget_usdt: float,
    spot_max_quote_budget_usdt: float,
    performance_multiplier: float = 1.0,
    min_notional_usdt: float = 5.0,
) -> float:
    cash = _safe_float(cash_usdt)
    if cash <= 0:
        return 10.0
    normalized_market = normalize_market(market)
    if normalized_market == "futures":
        pct = max(_safe_float(futures_quote_budget_pct), 0.0)
        min_budget = max(_safe_float(futures_min_quote_budget_usdt), 0.0)
        max_budget = max(_safe_float(futures_max_quote_budget_usdt), 0.0)
    else:
        pct = max(_safe_float(spot_quote_budget_pct), 0.0)
        min_budget = max(_safe_float(spot_min_quote_budget_usdt), 0.0)
        max_budget = max(_safe_float(spot_max_quote_budget_usdt), 0.0)
    base = cash * (pct / 100.0)
    budget = max(base, min_budget)
    if max_budget > 0:
        budget = min(budget, max_budget)
    multiplier = _safe_float(performance_multiplier) or 1.0
    if multiplier != 1.0:
        budget *= multiplier
        if multiplier > 1.0 and max_budget > 0:
            budget = min(budget, max_budget)
        if multiplier < 1.0:
            budget = max(budget, _safe_float(min_notional_usdt))
    return round(max(budget, 0.0), 2)


def candidate_upbit_quote_budget_details(
    *,
    cash_krw: float,
    cash_usdt: float,
    upbit_usdt_krw_rate: float,
    quote_budget_pct: float,
    min_quote_budget_krw: float,
    max_quote_budget_krw: float,
    performance_multiplier: float = 1.0,
) -> dict[str, Any]:
    rate = max(_safe_float(upbit_usdt_krw_rate), 1.0)
    usable_cash_krw = _safe_float(cash_krw)
    if usable_cash_krw <= 0:
        usable_cash_krw = _safe_float(cash_usdt) * rate
    if usable_cash_krw <= 0:
        usable_cash_krw = max(_safe_float(min_quote_budget_krw), 5_000.0)
    pct = max(_safe_float(quote_budget_pct), 0.0)
    min_budget = max(_safe_float(min_quote_budget_krw), 0.0)
    max_budget = max(_safe_float(max_quote_budget_krw), 0.0)
    base = usable_cash_krw * (pct / 100.0)
    budget_krw = max(base, min_budget)
    if max_budget > 0:
        budget_krw = min(budget_krw, max_budget)
    multiplier = _safe_float(performance_multiplier) or 1.0
    if multiplier != 1.0:
        budget_krw *= multiplier
        if multiplier > 1.0 and max_budget > 0:
            budget_krw = min(budget_krw, max_budget)
        if multiplier < 1.0:
            budget_krw = max(
                budget_krw,
                max(_safe_float(min_quote_budget_krw), 5_000.0) * multiplier,
            )
    details = quote_budget_details_from_amount(
        market=UPBIT_SPOT_MARKET,
        quote_budget=round(max(budget_krw, 0.0), 2),
        upbit_usdt_krw_rate=rate,
    )
    details["performance_budget_multiplier"] = round(multiplier, 4)
    return details


def growth_governor_row_lanes(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    market = normalize_market(row.get("market") or row.get("venue"))
    side = normalize_position_side(row.get("side"))
    horizon = normalize_binance_horizon(row.get("horizon"), market=market)
    lane = normalize_binance_display_lane(
        lane=row.get("lane") or metadata.get("lane") or calculated.get("lane"),
        market=market,
        horizon=horizon,
        side=side,
    )
    setup_tokens = {
        re.sub(r"[\s/]+", "_", str(value or "").strip().lower())
        for value in (
            row.get("strategy_family"),
            row.get("entry_setup"),
            row.get("setup"),
            row.get("evidence_key"),
            metadata.get("strategy_family"),
            metadata.get("entry_setup"),
            metadata.get("setup"),
            metadata.get("evidence_key"),
            calculated.get("strategy_family"),
            calculated.get("entry_setup"),
            calculated.get("setup"),
            calculated.get("evidence_key"),
        )
        if str(value or "").strip()
    }
    lanes = {
        f"{market}:{side}",
        binance_block_lane(market=market, horizon=horizon, side=side),
    }
    if lane:
        lanes.add(lane)
    lanes.update(setup_tokens)
    return {item for item in lanes if item}


def manager_candidate_lane_authority_bonus(row: dict[str, Any]) -> float:
    context = (
        row.get("lane_authority_candidate")
        if isinstance(row.get("lane_authority_candidate"), dict)
        else {}
    )
    bias = str(context.get("selection_bias") or "").strip().lower()
    grade = str(context.get("grade") or "").strip().lower()
    expectancy = _safe_float(context.get("expectancy_pct"))
    win_rate = _safe_float(context.get("win_rate_pct"))
    profit_factor = _safe_float(context.get("profit_factor"))
    sample_count = _safe_int(context.get("sample_count"))
    if bias == "positive_sample_building":
        bonus = 24.0
        bonus += min(max(profit_factor - 1.0, 0.0) * 4.0, 8.0)
        bonus += min(max(win_rate - 50.0, 0.0) * 0.3, 4.0)
        bonus += min(sample_count / 3.0, 4.0)
        return bonus
    if bias == "scale_candidate":
        return 18.0
    if bias == "avoid_weak_lane" or grade in {"weak", "observe_only"}:
        penalty = 18.0
        penalty += min(max(-expectancy, 0.0) * 6.0, 12.0)
        if profit_factor > 0:
            penalty += min(max(1.0 - profit_factor, 0.0) * 14.0, 12.0)
        penalty += min(max(35.0 - win_rate, 0.0) * 0.35, 8.0)
        return -penalty
    return 0.0


def manager_candidate_near_duplicate_penalty(row: dict[str, Any]) -> float:
    duplicate = (
        row.get("near_duplicate_active_block")
        if isinstance(row.get("near_duplicate_active_block"), dict)
        else {}
    )
    status = str(duplicate.get("status") or "").strip().lower()
    if status not in {"review_required", "near_duplicate"}:
        return 0.0
    return 85.0


def manager_candidate_freshness_penalty(row: dict[str, Any]) -> float:
    timestamp: datetime | None = None
    for key in (
        "updated_at",
        "captured_at",
        "scored_at",
        "research_updated_at",
        "candidate_updated_at",
    ):
        timestamp = _parse_iso_datetime(row.get(key))
        if timestamp is not None:
            break
    if timestamp is None:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        timestamp = _parse_iso_datetime(
            metadata.get("updated_at") or metadata.get("candidate_updated_at")
        )
    if timestamp is None:
        return 0.0
    age_hours = (
        datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
    ).total_seconds() / 3600.0
    if age_hours <= 2.0:
        return 0.0
    if age_hours <= 6.0:
        return (age_hours - 2.0) * 1.5
    if age_hours <= 24.0:
        return 6.0 + (age_hours - 6.0) * 1.2
    if age_hours <= 72.0:
        return 27.6 + (age_hours - 24.0) * 0.45
    return 55.0


def manager_candidate_performance_cooldown_penalty(
    row: dict[str, Any],
    *,
    entry_gate_policy: dict[str, Any] | None = None,
) -> float:
    if not isinstance(entry_gate_policy, dict):
        return 0.0
    symbol = str(row.get("symbol") or "").upper().strip()
    market = normalize_market(row.get("market") or row.get("venue"))
    lane = _market_side_lane(row)
    cooldown_symbols = entry_gate_policy.get("cooldown_symbols")
    cooldown_symbols = cooldown_symbols if isinstance(cooldown_symbols, dict) else {}
    cooldown_symbol_keys = {
        str(item or "").upper().strip()
        for item in entry_gate_policy.get("cooldown_symbol_keys") or []
        if str(item or "").strip()
    }
    symbol_aliases = {symbol}
    if market == UPBIT_SPOT_MARKET:
        symbol_aliases.add(upbit_market_to_usdt_symbol(symbol))
    else:
        symbol_aliases.add(upbit_market_symbol(symbol))
    symbol_penalty = 0.0
    if symbol_aliases.intersection(cooldown_symbol_keys) or any(
        alias in cooldown_symbols for alias in symbol_aliases
    ):
        symbol_penalty = 55.0

    cooldown_lanes = entry_gate_policy.get("cooldown_lanes")
    cooldown_lanes = cooldown_lanes if isinstance(cooldown_lanes, dict) else {}
    cooldown_lane_keys = {
        str(item or "").lower().strip()
        for item in entry_gate_policy.get("cooldown_lane_keys") or []
        if str(item or "").strip()
    }
    lane_penalty = 0.0
    lane_tokens = {lane}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    display_lane = normalize_binance_display_lane(
        lane=row.get("lane") or metadata.get("lane") or calculated.get("lane"),
        market=market,
        horizon=str(row.get("horizon") or ""),
        side=row.get("side") or row.get("stance"),
    )
    if display_lane:
        lane_tokens.add(display_lane)
    side = normalize_position_side(row.get("side") or row.get("stance"))
    horizon = normalize_binance_horizon(row.get("horizon"), market=market)
    if market != "futures" and side and horizon:
        lane_tokens.add(f"{market}:{side}:{horizon}")
    lane_tokens.update(growth_governor_row_lanes(row))
    if lane_tokens.intersection(cooldown_lane_keys) or any(
        token in cooldown_lanes for token in lane_tokens
    ):
        lane_penalty = 35.0
    return symbol_penalty + lane_penalty


def manager_candidate_performance_budget_penalty(row: dict[str, Any]) -> float:
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    sizing = (
        calculated.get("sizing_inputs")
        if isinstance(calculated.get("sizing_inputs"), dict)
        else {}
    )
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    multiplier = (
        _safe_float(sizing.get("performance_budget_multiplier"))
        or _safe_float(calculated.get("performance_budget_multiplier"))
        or _safe_float(metadata.get("performance_budget_multiplier"))
    )
    if multiplier <= 0 or multiplier >= 1.0:
        return 0.0
    return min(max((1.0 - multiplier) * 50.0, 0.0), 45.0)


def candidate_pattern_performance_scorecard(row: dict[str, Any]) -> dict[str, Any]:
    for source in (
        row,
        row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
        row.get("calculated") if isinstance(row.get("calculated"), dict) else {},
        row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else {},
    ):
        card = (
            source.get("pattern_performance_scorecard")
            if isinstance(source, dict)
            and isinstance(source.get("pattern_performance_scorecard"), dict)
            else {}
        )
        if card:
            return dict(card)
    return {}


def manager_candidate_pattern_performance_penalty(row: dict[str, Any]) -> float:
    card = candidate_pattern_performance_scorecard(row)
    if not card:
        return 0.0
    sample_count = _safe_int(card.get("sample_count"))
    if sample_count < 3:
        return 0.0
    pnl = _safe_float(card.get("pnl_usdt"))
    avg_r = _safe_float(card.get("avg_r_multiple"))
    win_rate = _safe_float(card.get("win_rate_pct"))
    profit_factor = _safe_float(card.get("profit_factor"))
    recovery_factor = _safe_float(card.get("recovery_factor"))
    max_drawdown_r = _safe_float(card.get("max_drawdown_r_multiple"))
    penalty = 0.0
    if pnl < 0:
        penalty += min(abs(pnl) * 8.0, 22.0)
    if avg_r < 0:
        penalty += min(abs(avg_r) * 18.0, 18.0)
    if win_rate <= 35.0:
        penalty += min((35.0 - win_rate) * 0.7, 18.0)
    if 0 < profit_factor < 1.0:
        penalty += min((1.0 - profit_factor) * 22.0, 18.0)
    if recovery_factor <= 0.0:
        penalty += 8.0
    if max_drawdown_r <= -2.0:
        penalty += min(abs(max_drawdown_r) * 5.0, 16.0)
    return min(max(penalty, 0.0), 75.0)


def manager_candidate_pattern_performance_bonus(row: dict[str, Any]) -> float:
    card = candidate_pattern_performance_scorecard(row)
    if not card:
        return 0.0
    if not scorecard_allows_budget_scale(
        card,
        min_samples=5,
        win_rate_threshold=50.0,
    ):
        return 0.0
    return min(
        4.0
        + _safe_float(card.get("avg_r_multiple")) * 4.0
        + max(_safe_float(card.get("profit_factor")) - 1.0, 0.0) * 3.0,
        16.0,
    )


def manager_candidate_execution_blocker_penalty(row: dict[str, Any]) -> float:
    blockers = (
        row.get("execution_blockers")
        if isinstance(row.get("execution_blockers"), dict)
        else {}
    )
    status = str(blockers.get("status") or "").strip().lower()
    if status not in {"would_reject_current_gates", "blocked_by_current_gates"}:
        return 0.0
    penalty = _safe_float(blockers.get("ranking_penalty"))
    if penalty <= 0:
        penalty = 75.0 + max(_safe_int(blockers.get("blocker_count")) - 1, 0) * 20.0
    return min(max(penalty, 0.0), 160.0)


def manager_candidate_empirical_edge_score(
    row: dict[str, Any],
    *,
    entry_gate_policy: dict[str, Any] | None = None,
) -> float:
    calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
    pattern_inputs = (
        calculated.get("pattern_inputs")
        if isinstance(calculated.get("pattern_inputs"), dict)
        else {}
    )
    prior = (
        pattern_inputs.get("prior")
        if isinstance(pattern_inputs.get("prior"), dict)
        else {}
    )
    prior_quality = (
        pattern_inputs.get("prior_quality")
        if isinstance(pattern_inputs.get("prior_quality"), dict)
        else {}
    )
    crosscheck = (
        calculated.get("pattern_live_crosscheck")
        if isinstance(calculated.get("pattern_live_crosscheck"), dict)
        else {}
    )
    alpha_score = (
        row.get("alpha_score_v3")
        if isinstance(row.get("alpha_score_v3"), dict)
        else score_crypto_candidate(row)
    )
    volatile_context = (
        calculated.get("volatile_attack_context")
        if isinstance(calculated.get("volatile_attack_context"), dict)
        else {}
    )
    confidence = _safe_float(row.get("confidence"))
    if confidence > 1.0:
        confidence = confidence / 100.0
    score = _safe_float(row.get("score")) * 0.45
    score += min(max(confidence, 0.0), 1.0) * 14.0
    score += _safe_float(alpha_score.get("total_score")) * 0.18
    score += min(_safe_float(volatile_context.get("score")) * 0.12, 12.0)
    reward_risk = _safe_float(calculated.get("reward_risk"))
    if reward_risk > 1.0:
        score += min((reward_risk - 1.0) * 5.0, 10.0)
    status = str(crosscheck.get("status") or "").strip().lower()
    if status == "aligned":
        score += 18.0
    elif status == "wait":
        score += 6.0
    elif status == "contradicted":
        score -= 35.0
    if prior:
        if bool(prior_quality.get("passed")):
            score += 8.0
        else:
            score -= 8.0
        score += min(_safe_float(prior.get("objective_score")) * 0.12, 16.0)
        score += min(max(_safe_float(prior.get("expectancy_r")), 0.0) * 20.0, 10.0)
        score += min(
            max(_safe_float(prior.get("out_of_sample_expectancy_r")), 0.0) * 24.0,
            12.0,
        )
        score += min(max(_safe_float(prior.get("profit_factor")) - 1.0, 0.0) * 8.0, 8.0)
        score += min(_safe_int(prior.get("trade_count")) / 8.0, 8.0)
    if bool(alpha_score.get("reject")):
        score -= 40.0
    score += manager_candidate_lane_authority_bonus(row)
    score -= manager_candidate_near_duplicate_penalty(row)
    score -= manager_candidate_freshness_penalty(row)
    score -= manager_candidate_performance_cooldown_penalty(
        row,
        entry_gate_policy=entry_gate_policy,
    )
    score -= manager_candidate_performance_budget_penalty(row)
    score -= manager_candidate_pattern_performance_penalty(row)
    score += manager_candidate_pattern_performance_bonus(row)
    score -= manager_candidate_execution_blocker_penalty(row)
    return round(score, 4)


def rank_manager_candidates_by_edge(
    candidates: list[dict[str, Any]],
    *,
    entry_gate_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda row: (
            manager_candidate_empirical_edge_score(
                row,
                entry_gate_policy=entry_gate_policy,
            ),
            _safe_float(row.get("score")),
            _safe_float(row.get("confidence")),
        ),
        reverse=True,
    )
