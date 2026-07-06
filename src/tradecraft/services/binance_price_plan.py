from __future__ import annotations

from typing import Any, Callable

from tradecraft.services.binance_lane import binance_block_lane
from tradecraft.services.binance_order_math import (
    candidate_last_price,
    candidate_stop_pct,
    candidate_volatility_pct,
    reward_risk,
    round_candidate_price,
    safe_float,
)
from tradecraft.services.binance_symbol import UPBIT_SPOT_MARKET


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _config_float(config: Any, name: str, default: float = 0.0) -> float:
    return safe_float(getattr(config, name, default))


def _feature_book_mid_price(features: dict[str, Any]) -> float:
    bid = safe_float(features.get("bid_price") or features.get("bid"))
    ask = safe_float(features.get("ask_price") or features.get("ask"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return ask or bid


def _upbit_krw_reference_price(features: dict[str, Any]) -> float:
    for key in ("price", "last_price", "current_price", "close"):
        price = safe_float(features.get(key))
        if price > 0:
            return price
    return _feature_book_mid_price(features)


def design_crypto_candidate_price_plan(
    *,
    candidate: dict[str, Any],
    features: dict[str, Any],
    market: str,
    side: str,
    horizon: str,
    account: dict[str, Any],
    config: Any,
    min_reward_risk_floor: float,
    pattern_prior: dict[str, Any] | None = None,
    live_authority: dict[str, Any] | None = None,
    volatile_context_builder: Callable[..., dict[str, Any]],
    pattern_prior_quality: Callable[[dict[str, Any]], dict[str, Any]],
    pattern_live_crosscheck: Callable[..., dict[str, Any]],
    live_authority_validation_gate: Callable[[dict[str, Any] | None], dict[str, Any]],
    quote_budget_details: Callable[..., dict[str, Any]],
    quote_budget_details_from_amount: Callable[..., dict[str, Any]],
    cash_reference_usdt: Callable[..., float],
) -> dict[str, Any]:
    book_fresh = bool(features.get("book_fresh"))
    bid_price = safe_float(features.get("bid_price") or features.get("bid")) if book_fresh else 0.0
    ask_price = safe_float(features.get("ask_price") or features.get("ask")) if book_fresh else 0.0
    last_price = (
        _upbit_krw_reference_price(features)
        if market == UPBIT_SPOT_MARKET
        else candidate_last_price(candidate=candidate, features=features)
    )
    if last_price <= 0:
        for key in ("entry_price", "entry_price_usdt", "entry_trigger_price"):
            price = safe_float(candidate.get(key))
            if price > 0:
                last_price = price
                break
    if last_price <= 0:
        return {}
    spread_bps = safe_float(features.get("spread_bps") or candidate.get("spread_bps"))
    if spread_bps <= 0 and bid_price > 0 and ask_price > 0:
        spread_bps = max((ask_price - bid_price) / ((ask_price + bid_price) / 2) * 10_000, 0.0)
    block_template = (
        candidate.get("block_template")
        if isinstance(candidate.get("block_template"), dict)
        else {}
    )
    entry_quality = str(
        candidate.get("entry_quality") or block_template.get("entry_style") or ""
    ).strip().lower()
    if not entry_quality:
        entry_quality = str(features.get("entry_quality") or "actionable_now").strip().lower()
    change_pct_24h = safe_float(
        features.get("change_pct_24h")
        or candidate.get("change_pct_24h")
        or features.get("price_change_pct_24h")
    )
    volatile_context = volatile_context_builder(
        candidate=candidate,
        features=features,
        spread_bps=spread_bps,
        change_pct_24h=change_pct_24h,
        market=market,
    )
    volatile_attack = bool(volatile_context.get("enabled"))
    if volatile_attack and entry_quality in {"actionable_now", "immediate", ""}:
        entry_quality = "wait_for_price"
    volatility_pct = candidate_volatility_pct(
        change_pct_24h=change_pct_24h,
        spread_bps=spread_bps,
        horizon=horizon,
        market=market,
    )
    prior = pattern_prior or {}
    prior_quality = pattern_prior_quality(prior)
    authority = live_authority or {}
    pattern_crosscheck = pattern_live_crosscheck(
        pattern_prior=prior,
        prior_quality=prior_quality,
        features=features,
        market=market,
        side=side,
        live_authority=authority,
    )
    pattern_params = prior.get("parameter_set") if isinstance(prior.get("parameter_set"), dict) else {}
    prior_stop_pct = safe_float(pattern_params.get("stop_pct")) * 100.0
    prior_target_pct = safe_float(pattern_params.get("target_pct")) * 100.0
    pattern_geometry_usable = (
        bool(prior_quality.get("passed"))
        and prior_stop_pct > 0
        and prior_target_pct > prior_stop_pct
    )
    waits_for_price = entry_quality in {
        "wait_pullback",
        "pullback",
        "wait_breakout",
        "breakout",
        "wait_for_price",
        "wait_for_live_confluence",
    }
    authority_status = str(authority.get("status") or "").strip().lower()
    authority_grade = str(authority.get("live_grade") or authority.get("grade") or "").strip().lower()
    authority_label = authority_grade or authority_status
    allow_scale_up = authority.get("allow_scale_up")
    has_explicit_live_authority = bool(authority_status or authority_label)
    validation_gate = live_authority_validation_gate(live_authority)
    validation_gate_status = validation_gate["status"]
    if (
        market == "spot"
        and side == "long"
        and (
            authority_label in {"observe_only", "restricted"}
            or (has_explicit_live_authority and allow_scale_up is False)
        )
    ):
        waits_for_price = True
        if entry_quality in {"actionable_now", "immediate", ""}:
            entry_quality = "wait_for_live_confluence"
    if prior and pattern_crosscheck.get("status") in {"wait", "contradicted"}:
        waits_for_price = True
        if entry_quality == "actionable_now":
            entry_quality = "wait_for_live_confluence"
    if (
        validation_gate_status
        and validation_gate_status != "clear"
        and entry_quality in {"actionable_now", "immediate", ""}
    ):
        waits_for_price = True
        entry_quality = "wait_for_live_confluence"
    pullback_pct = min(max(volatility_pct * 0.42, 0.25), 3.8)
    breakout_pct = min(max(volatility_pct * 0.18, 0.12), 1.2)
    if side == "long":
        immediate_entry = ask_price if ask_price > 0 else last_price
        if entry_quality in {"wait_breakout", "breakout"}:
            entry_price = last_price * (1 + breakout_pct / 100)
        elif waits_for_price:
            entry_price = last_price * (1 - pullback_pct / 100)
        else:
            entry_price = immediate_entry
    else:
        immediate_entry = bid_price if bid_price > 0 else last_price
        if entry_quality in {"wait_breakout", "breakout"}:
            entry_price = last_price * (1 - breakout_pct / 100)
        elif waits_for_price:
            entry_price = last_price * (1 + pullback_pct / 100)
        else:
            entry_price = immediate_entry

    default_stop_pct = candidate_stop_pct(
        volatility_pct=volatility_pct,
        horizon=horizon,
        market=market,
        min_candidate_stop_pct=_config_float(config, "min_candidate_stop_pct"),
    )
    if volatile_attack:
        stop_multiplier = max(_config_float(config, "volatile_attack_stop_multiplier"), 1.0)
        default_stop_pct = min(max(default_stop_pct * stop_multiplier, 0.1), 16.0)
    min_reward_risk = max(safe_float(min_reward_risk_floor), 1.5)
    if volatile_attack:
        min_reward_risk = max(
            min_reward_risk,
            _config_float(config, "volatile_attack_min_reward_risk"),
        )
    default_target_reward_risk = min_reward_risk + 0.05
    default_target_pct = default_stop_pct * default_target_reward_risk
    stop_pct = default_stop_pct
    target_pct = default_target_pct
    pattern_geometry_used = False
    if pattern_geometry_usable and prior_target_pct / prior_stop_pct >= min_reward_risk:
        spread_pct = max(spread_bps / 100.0, 0.0)
        stop_pct = min(max(prior_stop_pct, spread_pct * 2.0, 0.1), 9.0)
        target_pct = min(max(prior_target_pct, stop_pct * min_reward_risk), 18.0)
        pattern_geometry_used = True
    if volatile_attack:
        spread_pct = max(spread_bps / 100.0, 0.0)
        stop_pct = min(
            max(
                stop_pct,
                default_stop_pct,
                spread_pct * 2.0,
                _config_float(config, "min_candidate_stop_pct") * 1.2,
            ),
            16.0,
        )
        target_pct = min(
            max(
                target_pct,
                stop_pct * max(_config_float(config, "volatile_attack_min_reward_risk"), 2.0),
            ),
            35.0,
        )
    if side == "long":
        stop_price = entry_price * (1 - stop_pct / 100)
        target_price = entry_price * (1 + target_pct / 100)
        liquidation_price = (
            entry_price * (1 - max(_config_float(config, "min_liquidation_distance_pct") * 2.5, 35.0) / 100)
            if market == "futures"
            else 0.0
        )
    else:
        stop_price = entry_price * (1 + stop_pct / 100)
        target_price = entry_price * (1 - target_pct / 100)
        liquidation_price = (
            entry_price * (1 + max(_config_float(config, "min_liquidation_distance_pct") * 2.5, 35.0) / 100)
            if market == "futures"
            else 0.0
        )

    explicit_entry = safe_float(candidate.get("entry_price") or candidate.get("entry_price_usdt"))
    explicit_target = safe_float(candidate.get("target_price") or candidate.get("target_price_usdt"))
    explicit_stop = safe_float(candidate.get("stop_price") or candidate.get("stop_price_usdt"))
    explicit_geometry_used = False
    if (
        explicit_entry > 0
        and explicit_target > 0
        and explicit_stop > 0
        and reward_risk(
            side=side,
            entry_price=explicit_entry,
            stop_price=explicit_stop,
            target_price=explicit_target,
        )
        > 0
    ):
        entry_price = explicit_entry
        target_price = explicit_target
        stop_price = explicit_stop
        explicit_geometry_used = True

    entry_price = round_candidate_price(entry_price)
    stop_price = round_candidate_price(stop_price)
    target_price = round_candidate_price(target_price)
    liquidation_price = round_candidate_price(liquidation_price) if liquidation_price > 0 else 0.0
    rr = reward_risk(
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )
    budget_lane = "volatile_attack" if volatile_attack else binance_block_lane(
        market=market,
        horizon=horizon,
        side=side,
    )
    budget_details = quote_budget_details(
        market=market,
        account=account,
        side=side,
        lane=budget_lane,
    )
    performance_budget_multiplier = budget_details.get("performance_budget_multiplier", 1.0)
    quote_budget = safe_float(budget_details.get("quote_budget"))
    if volatile_attack:
        multiplier = min(max(_config_float(config, "volatile_attack_budget_multiplier"), 0.05), 1.0)
        minimum = 5.0
        if market == UPBIT_SPOT_MARKET:
            minimum = max(_config_float(config, "upbit_min_quote_budget_krw"), 5_000.0)
        quote_budget = round(max(quote_budget * multiplier, minimum), 2)
        budget_details = quote_budget_details_from_amount(
            market=market,
            quote_budget=quote_budget,
        )
        budget_details["performance_budget_multiplier"] = performance_budget_multiplier
    trigger_operator = "<=" if side == "long" else ">="
    if entry_quality in {"wait_breakout", "breakout"}:
        trigger_operator = ">=" if side == "long" else "<="
    entry_style = "wait_for_price" if waits_for_price else "immediate"
    method_version = (
        "crypto_research_price_design_v2_pattern_confluence"
        if prior
        else "crypto_research_price_design_v1"
    )
    if volatile_attack:
        method_version = f"{method_version}_volatile_attack"
    lane = "volatile_attack" if volatile_attack else binance_block_lane(
        market=market,
        horizon=horizon,
        side=side,
    )
    return {
        "method_version": method_version,
        "lane": lane,
        "volatile_attack": volatile_attack,
        "volatile_attack_context": volatile_context,
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "liquidation_price": liquidation_price,
        "reward_risk": rr,
        "risk_pct": round(stop_pct, 4),
        "target_pct": round(target_pct, 4),
        "default_risk_pct": round(default_stop_pct, 4),
        "default_target_pct": round(default_target_pct, 4),
        "entry_offset_pct": round((entry_price / last_price - 1) * 100, 4),
        "entry_style": entry_style,
        "raw_entry_quality": entry_quality,
        "entry_trigger_price": entry_price if entry_style == "wait_for_price" else 0.0,
        "entry_trigger_operator": trigger_operator if entry_style == "wait_for_price" else "",
        "quote_budget": budget_details["quote_budget"],
        "quote_currency": budget_details["quote_currency"],
        "quote_budget_usdt": budget_details["quote_budget_usdt"],
        "quote_budget_krw": budget_details.get("quote_budget_krw", 0.0),
        "performance_budget_multiplier": budget_details.get("performance_budget_multiplier", 1.0),
        "leverage": 1,
        "margin_type": "isolated" if market == "futures" else "",
        "market_inputs": {
            "last_price": last_price,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "spread_bps": spread_bps,
            "book_source": _clean_text(features.get("book_source"), limit=80) if book_fresh else "",
            "book_fetched_at": _clean_text(features.get("book_fetched_at"), limit=80) if book_fresh else "",
            "book_market": _clean_text(features.get("book_market"), limit=20) if book_fresh else "",
            "book_fresh": book_fresh,
            "change_pct_24h": change_pct_24h,
            "quote_volume_usdt": safe_float(
                features.get("quote_volume_usdt") or candidate.get("quote_volume_usdt")
            ),
            "volume_expansion_ratio": safe_float(
                features.get("volume_expansion_ratio")
                or candidate.get("volume_expansion_ratio")
                or features.get("volume_expansion")
                or candidate.get("volume_expansion")
            ),
            "orderbook_depth_usdt": safe_float(
                features.get("orderbook_depth_usdt")
                or candidate.get("orderbook_depth_usdt")
                or features.get("book_depth_usdt")
                or candidate.get("book_depth_usdt")
            ),
        },
        "technical_inputs": {
            "entry_quality": entry_quality,
            "entry_quality_score": safe_float(
                features.get("entry_quality_score") or candidate.get("entry_quality_score")
            ),
            "timeframe_alignment": _clean_text(features.get("timeframe_alignment"), limit=80),
            "volatility_pct_estimate": round(volatility_pct, 4),
            "wick_risk_score": safe_float(
                features.get("wick_risk_score") or candidate.get("wick_risk_score")
            ),
            "squeeze_risk_score": safe_float(
                features.get("squeeze_risk_score") or candidate.get("squeeze_risk_score")
            ),
            "volatile_attack_score": safe_float(volatile_context.get("score")),
        },
        "derivatives_inputs": {
            "derivatives_status": _clean_text(features.get("derivatives_status"), limit=80),
            "funding_rate": safe_float(features.get("funding_rate")),
            "open_interest": safe_float(features.get("open_interest")),
            "liquidation_price_estimate": liquidation_price,
        },
        "pattern_inputs": {
            "prior": prior,
            "prior_quality": prior_quality,
            "geometry_used": pattern_geometry_used,
            "explicit_candidate_geometry_used": explicit_geometry_used,
            "prior_stop_pct": round(prior_stop_pct, 4),
            "prior_target_pct": round(prior_target_pct, 4),
        },
        "pattern_live_crosscheck": pattern_crosscheck,
        "sizing_inputs": {
            "quote_budget": budget_details["quote_budget"],
            "quote_currency": budget_details["quote_currency"],
            "quote_budget_usdt": budget_details["quote_budget_usdt"],
            "quote_budget_krw": budget_details.get("quote_budget_krw", 0.0),
            "performance_budget_multiplier": budget_details.get("performance_budget_multiplier", 1.0),
            "cash_reference_usdt": cash_reference_usdt(market=market, account=account),
            "cash_reference_krw": safe_float(account.get("upbit_cash_krw"))
            if market == UPBIT_SPOT_MARKET
            else 0.0,
            "risk_floor_reward_risk": min_reward_risk,
            "target_reward_risk": round(target_pct / stop_pct, 4) if stop_pct > 0 else 0.0,
            "volatile_attack_budget_multiplier": (
                _config_float(config, "volatile_attack_budget_multiplier")
                if volatile_attack
                else 1.0
            ),
        },
        "decision_notes": [
            f"entry_style={entry_style} from entry_quality={entry_quality or 'unknown'}",
            f"risk_pct={round(stop_pct, 3)} target_pct={round(target_pct, 3)} reward_risk={rr}",
            f"lane={lane} score={round(safe_float(volatile_context.get('score')), 2)}",
            f"pattern_live_crosscheck={pattern_crosscheck.get('status')}",
            f"explicit_candidate_geometry_used={explicit_geometry_used}",
            "final create_blocks may override these prices only with override_reason",
        ],
    }
