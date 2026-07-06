from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from tradecraft.services.binance_lane import (
    BINANCE_MANAGER_LANES,
    binance_market_side_lane,
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

BOOK_MARKET_FEATURES_KEY = "_book_features_by_market"
BOOK_FIELD_KEYS = {
    "bid_price",
    "bid",
    "ask_price",
    "ask",
    "spread_bps",
    "book_source",
    "book_fetched_at",
    "book_market",
    "book_fresh",
}
PRICE_FIELD_KEYS = {"price", "last_price", "current_price", "close"}


@dataclass(frozen=True)
class BinanceManagerCandidateFinalizeHooks:
    candidate_near_duplicate_active_block_context: Callable[
        [dict[str, Any], list[dict[str, Any]]],
        dict[str, Any],
    ]
    candidate_lane_authority_context: Callable[
        [dict[str, Any], dict[str, Any]],
        dict[str, Any],
    ]
    manager_candidate_empirical_edge_score: Callable[..., float]
    candidate_execution_blocker_context: Callable[[dict[str, Any]], dict[str, Any]]
    annotate_candidate_pattern_performance: Callable[..., tuple[dict[str, Any], bool]]
    rank_manager_candidates_by_edge: Callable[..., list[dict[str, Any]]]
    diversify_manager_candidates_by_lane: Callable[
        [list[dict[str, Any]]],
        tuple[list[dict[str, Any]], bool],
    ]
    lane_distribution: Callable[[list[dict[str, Any]]], dict[str, Any]]
    manager_candidate_packets: Callable[..., dict[str, Any]]
    manager_candidate_stage_counts: Callable[..., dict[str, int]]
    market_side_lane: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class BinanceProvidedCandidateBuildHooks:
    pattern_prior_for_candidate: Callable[..., dict[str, Any]]
    design_price_plan: Callable[..., dict[str, Any]]
    merge_candidate_price_plan: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class BinanceResearchCandidateBuildHooks:
    pattern_prior_for_candidate: Callable[..., dict[str, Any]]
    design_price_plan: Callable[..., dict[str, Any]]
    merge_candidate_price_plan: Callable[..., dict[str, Any]]
    cash_reference_usdt: Callable[..., float]
    volatile_attack_context: Callable[..., dict[str, Any]]


def candidate_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    market = normalize_market(row.get("market") or row.get("venue"))
    side = normalize_position_side(row.get("side") or row.get("direction"))
    horizon = normalize_binance_horizon(row.get("horizon"), market=market)
    return (
        str(row.get("symbol") or "").upper().strip(),
        market,
        side,
        horizon,
    )


def diversify_manager_candidates_by_lane(
    candidates: list[dict[str, Any]],
    *,
    max_items: int,
) -> tuple[list[dict[str, Any]], bool]:
    limit = max(int(max_items), 1)
    if len(candidates) <= limit:
        return candidates[:limit], False

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def append(row: dict[str, Any]) -> None:
        key = candidate_identity(row)
        if key in seen or len(selected) >= limit:
            return
        seen.add(key)
        selected.append(row)

    for lane in BINANCE_MANAGER_LANES:
        for row in candidates:
            if binance_market_side_lane(
                row,
                normalize_market=normalize_market,
                normalize_position_side=normalize_position_side,
            ) == lane:
                append(row)
                break

    for row in candidates:
        append(row)

    return selected[:limit], selected[:limit] != candidates[:limit]


def candidate_execution_blocker_context(
    row: dict[str, Any],
    *,
    checks: list[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]]
    | tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]], ...],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    compact_keys = (
        "symbol",
        "matched_lane",
        "matched_entry_quality_lane",
        "entry_quality",
        "sample_count",
        "min_samples",
        "pnl_usdt",
        "win_rate_pct",
        "avg_r_multiple",
        "profit_factor",
        "recovery_factor",
        "max_drawdown_r_multiple",
    )
    for kind, checker in checks:
        rejection = checker(row)
        if not isinstance(rejection, dict) or not rejection:
            continue
        detail = rejection.get(kind)
        detail = detail if isinstance(detail, dict) else {}
        compact: dict[str, Any] = {
            "kind": kind,
            "reason": str(rejection.get("reason") or kind),
        }
        for key in compact_keys:
            value = detail.get(key)
            if value not in ({}, [], "", None):
                compact[key] = value
        recovery_required = _clean_text(detail.get("recovery_required"), limit=180)
        if recovery_required:
            compact["recovery_required"] = recovery_required
        blockers.append(compact)
    if not blockers:
        return {}
    return {
        "version": "binance_candidate_execution_blocker_v1",
        "status": "would_reject_current_gates",
        "blocker_count": len(blockers),
        "blockers": blockers[:4],
        "ranking_penalty": min(75.0 + max(len(blockers) - 1, 0) * 20.0, 150.0),
        "instruction": (
            "Prefer another executable candidate unless fresh evidence explicitly "
            "repairs this blocker before the manager action is applied."
        ),
    }


def _clean_text(value: Any, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _book_mid_price(features: dict[str, Any]) -> float:
    bid = _safe_float(features.get("bid_price") or features.get("bid"))
    ask = _safe_float(features.get("ask_price") or features.get("ask"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return ask or bid


def side_from_crypto_research_candidate(candidate: dict[str, Any]) -> str:
    raw = str(
        candidate.get("side")
        or candidate.get("stance")
        or candidate.get("direction")
        or ""
    ).strip().lower()
    if "short" in raw or raw in {"sell", "bearish", "down"}:
        return "short"
    return "long"


def candidate_is_explicit_long_candidate(candidate: dict[str, Any]) -> bool:
    raw = str(
        candidate.get("side")
        or candidate.get("stance")
        or candidate.get("direction")
        or ""
    ).strip().lower()
    return raw in {"long", "buy", "bullish", "up", "long_watch"} or "long" in raw


def spot_shadow_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    shadow = dict(candidate)
    shadow["market"] = "spot"
    shadow["venue"] = "spot"
    shadow["side"] = "long"
    shadow["source_market"] = normalize_market(candidate.get("market") or candidate.get("venue"))
    shadow["spot_shadow"] = True
    reason = _clean_text(candidate.get("reason_md"), limit=900)
    shadow["reason_md"] = (
        f"{reason} Spot long shadow added so Jue can compare cash exposure "
        "against the futures long lane."
    ).strip()
    metadata = dict(shadow.get("metadata") if isinstance(shadow.get("metadata"), dict) else {})
    metadata["source_market"] = shadow["source_market"]
    metadata["spot_shadow"] = True
    shadow["metadata"] = metadata
    return shadow


def upbit_shadow_candidate(
    candidate: dict[str, Any],
    *,
    source_symbol: str,
) -> dict[str, Any]:
    shadow = dict(candidate)
    upbit_symbol = upbit_market_symbol(source_symbol or candidate.get("symbol"))
    shadow["symbol"] = upbit_symbol
    shadow["market"] = UPBIT_SPOT_MARKET
    shadow["venue"] = UPBIT_SPOT_MARKET
    shadow["side"] = "long"
    shadow["source_symbol"] = str(source_symbol or candidate.get("symbol") or "").upper().strip()
    shadow["source_market"] = normalize_market(candidate.get("market") or candidate.get("venue"))
    shadow["upbit_shadow"] = True
    reason = _clean_text(candidate.get("reason_md"), limit=900)
    shadow["reason_md"] = (
        f"{reason} Upbit KRW spot shadow added so Jue can compare Korean fiat "
        "spot exposure against Binance spot/futures lanes."
    ).strip()
    metadata = dict(shadow.get("metadata") if isinstance(shadow.get("metadata"), dict) else {})
    metadata["source_market"] = shadow["source_market"]
    metadata["source_symbol"] = shadow["source_symbol"]
    metadata["upbit_shadow"] = True
    shadow["metadata"] = metadata
    return shadow


def futures_shadow_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    shadow = dict(candidate)
    shadow["market"] = "futures"
    shadow["venue"] = "futures"
    shadow["side"] = "long"
    shadow["horizon"] = "futures"
    shadow["source_market"] = normalize_market(candidate.get("market") or candidate.get("venue"))
    shadow["futures_shadow"] = True
    reason = _clean_text(candidate.get("reason_md"), limit=900)
    shadow["reason_md"] = (
        f"{reason} Futures long shadow added so Jue can compare leveraged directional "
        "exposure against the spot long lane."
    ).strip()
    metadata = dict(shadow.get("metadata") if isinstance(shadow.get("metadata"), dict) else {})
    metadata["source_market"] = shadow["source_market"]
    metadata["futures_shadow"] = True
    shadow["metadata"] = metadata
    return shadow


def candidate_derivatives_available(features: dict[str, Any]) -> bool:
    return str(features.get("derivatives_status") or "").strip().lower() == "available"


def market_from_crypto_research_candidate(
    *,
    candidate: dict[str, Any],
    features: dict[str, Any],
    side: str,
) -> str:
    raw_market = candidate.get("market") or candidate.get("venue")
    if raw_market:
        return normalize_market(raw_market)
    if side == "short" and candidate_derivatives_available(features):
        return "futures"
    return "spot"


def crypto_research_feature_index(
    crypto_research: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in crypto_research.get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper().strip()
        features = item.get("features") if isinstance(item.get("features"), dict) else {}
        if symbol and features:
            rows[symbol] = dict(features)
    return rows


def crypto_research_market_feature_index(
    crypto_research: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    raw_rows = crypto_research.get(BOOK_MARKET_FEATURES_KEY)
    if not isinstance(raw_rows, dict):
        return rows
    for key, features in raw_rows.items():
        if not isinstance(features, dict):
            continue
        if isinstance(key, tuple) and len(key) == 2:
            symbol = str(key[0] or "").upper().strip()
            market = normalize_market(key[1])
        else:
            symbol_text, _, market_text = str(key or "").partition("|")
            symbol = symbol_text.upper().strip()
            market = normalize_market(market_text)
        if symbol:
            rows[(symbol, market)] = dict(features)
    return rows


def features_for_candidate_market(
    *,
    symbol: str,
    market: str,
    feature_index: dict[str, dict[str, Any]],
    market_feature_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    normalized_market = normalize_market(market)
    source_symbol = (
        upbit_market_to_usdt_symbol(symbol)
        if normalized_market == UPBIT_SPOT_MARKET
        else symbol
    )
    features = dict(feature_index.get(source_symbol, {}))
    if normalized_market == UPBIT_SPOT_MARKET:
        source_usdt_price = _safe_float(
            feature_index.get(source_symbol, {}).get("price")
            or feature_index.get(source_symbol, {}).get("last_price")
        )
        for field in (*BOOK_FIELD_KEYS, *PRICE_FIELD_KEYS):
            features.pop(field, None)
        if source_usdt_price > 0:
            features["source_usdt_price"] = source_usdt_price
    has_market_features_for_symbol = any(
        (
            key_symbol == symbol
            if normalized_market == UPBIT_SPOT_MARKET
            else key_symbol in {symbol, source_symbol}
        )
        for key_symbol, _ in market_feature_index
    )
    if has_market_features_for_symbol:
        for field in BOOK_FIELD_KEYS:
            features.pop(field, None)
        if normalized_market == UPBIT_SPOT_MARKET:
            for field in PRICE_FIELD_KEYS:
                features.pop(field, None)
    if normalized_market == UPBIT_SPOT_MARKET:
        market_features = market_feature_index.get((symbol, normalized_market), {}) or {}
    else:
        market_features = (
            market_feature_index.get((symbol, normalized_market), {})
            or market_feature_index.get((source_symbol, normalized_market), {})
            or {}
        )
    features.update(market_features)
    if normalized_market == UPBIT_SPOT_MARKET:
        book_mid = _book_mid_price(features)
        for field in PRICE_FIELD_KEYS:
            features.pop(field, None)
        if book_mid > 0:
            features["price"] = book_mid
            features["last_price"] = book_mid
            features["current_price"] = book_mid
    return features


def build_provided_manager_candidates(
    *,
    provided_candidates: list[dict[str, Any]],
    feature_index: dict[str, dict[str, Any]],
    market_feature_index: dict[tuple[str, str], dict[str, Any]],
    crypto_patterns: dict[str, Any],
    live_authority: dict[str, Any],
    account: dict[str, Any],
    hooks: BinanceProvidedCandidateBuildHooks,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[tuple[str, str, str, str]]]:
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in provided_candidates:
        if not isinstance(row, dict):
            continue
        candidate = dict(row)
        symbol = str(candidate.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        source_symbol = upbit_market_to_usdt_symbol(symbol)
        market = normalize_market(candidate.get("market") or candidate.get("venue"))
        if market == UPBIT_SPOT_MARKET:
            symbol = upbit_market_symbol(symbol)
        side = normalize_position_side(candidate.get("side") or candidate.get("stance"))
        if market == UPBIT_SPOT_MARKET and side == "short":
            skipped.append(
                {
                    "symbol": symbol,
                    "market": market,
                    "reason": "upbit_spot_short_unsupported",
                }
            )
            continue
        horizon = normalize_binance_horizon(candidate.get("horizon"), market=market)
        key = (symbol, market, side, horizon)
        if key in seen:
            continue
        features = features_for_candidate_market(
            symbol=symbol,
            market=market,
            feature_index=feature_index,
            market_feature_index=market_feature_index,
        )
        pattern_prior = hooks.pattern_prior_for_candidate(
            crypto_patterns=crypto_patterns,
            symbol=source_symbol,
            side=side,
        )
        price_plan = hooks.design_price_plan(
            candidate=candidate,
            features=features,
            market=market,
            side=side,
            horizon=horizon,
            account=account,
            pattern_prior=pattern_prior,
            live_authority=live_authority,
        )
        if not price_plan:
            reason = (
                "upbit_spot_missing_krw_price_inputs"
                if market == UPBIT_SPOT_MARKET
                else "missing_price_inputs"
            )
            skipped.append({"symbol": symbol, "market": market, "reason": reason})
            continue
        seen.add(key)
        candidates.append(
            hooks.merge_candidate_price_plan(
                candidate=candidate,
                symbol=symbol,
                market=market,
                side=side,
                horizon=horizon,
                price_plan=price_plan,
            )
        )
    return candidates, skipped, seen


def _synthesized_volatile_attack_research_candidates(
    *,
    research_candidates: list[dict[str, Any]],
    feature_index: dict[str, dict[str, Any]],
    volatile_packet_rows: list[dict[str, Any]] | None,
    hooks: BinanceResearchCandidateBuildHooks,
    limit: int,
) -> list[dict[str, Any]]:
    existing_symbols = {
        str(row.get("symbol") or "").upper().strip()
        for row in research_candidates
        if isinstance(row, dict)
    }
    packet_by_symbol: dict[str, dict[str, Any]] = {}
    for row in volatile_packet_rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in existing_symbols:
            packet_by_symbol[symbol] = dict(row)

    rows: list[dict[str, Any]] = []
    source_symbols = list(dict.fromkeys([*packet_by_symbol.keys(), *feature_index.keys()]))
    for symbol in source_symbols:
        packet = packet_by_symbol.get(symbol, {})
        features = {**feature_index.get(symbol, {}), **packet}
        normalized_symbol = str(symbol or "").upper().strip()
        if not normalized_symbol or normalized_symbol in existing_symbols:
            continue
        change_pct = _safe_float(
            features.get("change_pct_24h") or features.get("price_change_pct_24h")
        )
        derivatives_available = candidate_derivatives_available(features)
        side = "short" if change_pct < 0 and derivatives_available else "long"
        market = "futures" if side == "short" else "spot"
        context_candidate = {"lane": "volatile_attack"} if packet else {}
        context = hooks.volatile_attack_context(
            candidate=context_candidate,
            features=features,
            spread_bps=_safe_float(features.get("spread_bps")),
            change_pct_24h=change_pct,
            market=market,
        )
        if not bool(context.get("enabled")):
            continue
        context_score = _safe_float(context.get("score"))
        reason_parts = [
            str(item)
            for item in context.get("reasons", [])
            if item
        ][:6]
        rows.append(
            {
                "symbol": normalized_symbol,
                "market": market,
                "side": side,
                "stance": f"{side}_watch",
                "horizon": "futures" if market == "futures" else "intraday",
                "score": max(60.0, context_score),
                "confidence": 0.58,
                "lane": "volatile_attack",
                "volatile_attack": True,
                "volatile_attack_score": context_score,
                "entry_quality": features.get("entry_quality") or "wait_for_price",
                "reason_md": (
                    "Synthesized volatile_attack candidate from market research "
                    f"features: {', '.join(reason_parts) or 'large volatility'}."
                ),
                "metadata": {
                    "synthetic_source": (
                        "crypto_research_volatile_packet"
                        if packet
                        else "crypto_research_feature_packet"
                    ),
                    "volatile_attack_context": {
                        "score": context_score,
                        "reasons": reason_parts,
                        "change_pct_24h": change_pct,
                        "volume_expansion_ratio": _safe_float(
                            context.get("volume_expansion_ratio")
                        ),
                        "squeeze_risk_score": _safe_float(
                            context.get("squeeze_risk_score")
                        ),
                        "spread_bps": _safe_float(context.get("spread_bps")),
                    },
                },
            }
        )
    rows.sort(
        key=lambda row: (
            _safe_float(row.get("volatile_attack_score")),
            abs(_safe_float(feature_index.get(row.get("symbol"), {}).get("change_pct_24h"))),
        ),
        reverse=True,
    )
    return rows[: max(_safe_int(limit), 1)]


def build_research_manager_candidates(
    *,
    research_candidates: list[dict[str, Any]],
    candidate_packets: dict[str, Any] | None = None,
    feature_index: dict[str, dict[str, Any]],
    market_feature_index: dict[tuple[str, str], dict[str, Any]],
    crypto_patterns: dict[str, Any],
    live_authority: dict[str, Any],
    market_universe: dict[str, list[str]],
    account: dict[str, Any],
    seen: set[tuple[str, str, str, str]],
    max_items: int,
    current_candidate_count: int,
    hooks: BinanceResearchCandidateBuildHooks,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[tuple[str, str, str, str]],
    dict[str, int],
]:
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    spot_shadow_count = 0
    futures_shadow_count = 0
    upbit_shadow_count = 0
    candidate_limit = max(int(max_items), 1)
    packet_source = candidate_packets if isinstance(candidate_packets, dict) else {}
    synthetic_volatile_candidates = _synthesized_volatile_attack_research_candidates(
        research_candidates=research_candidates,
        feature_index=feature_index,
        volatile_packet_rows=(
            packet_source.get("volatile_candidates")
            if isinstance(packet_source.get("volatile_candidates"), list)
            else []
        ),
        hooks=hooks,
        limit=min(8, candidate_limit),
    )
    research_rows = synthetic_volatile_candidates + [
        row for row in research_candidates if isinstance(row, dict)
    ]

    def append_candidate(
        *,
        candidate: dict[str, Any],
        symbol: str,
        source_symbol: str,
        market: str,
        side: str,
        horizon: str,
        features: dict[str, Any],
    ) -> bool:
        candidate_for_plan = dict(candidate)
        volatile_context = hooks.volatile_attack_context(
            candidate=candidate_for_plan,
            features=features,
            spread_bps=_safe_float(
                features.get("spread_bps") or candidate_for_plan.get("spread_bps")
            ),
            change_pct_24h=_safe_float(
                features.get("change_pct_24h")
                or candidate_for_plan.get("change_pct_24h")
                or features.get("price_change_pct_24h")
            ),
            market=market,
        )
        if bool(volatile_context.get("enabled")):
            metadata = (
                dict(candidate_for_plan.get("metadata"))
                if isinstance(candidate_for_plan.get("metadata"), dict)
                else {}
            )
            metadata["volatile_attack_synthesized"] = True
            metadata["volatile_attack_context"] = {
                "score": _safe_float(volatile_context.get("score")),
                "reasons": [
                    str(item)
                    for item in volatile_context.get("reasons", [])
                    if item
                ][:8],
                "change_pct_24h": _safe_float(volatile_context.get("change_pct_24h")),
                "volume_expansion_ratio": _safe_float(
                    volatile_context.get("volume_expansion_ratio")
                ),
                "squeeze_risk_score": _safe_float(
                    volatile_context.get("squeeze_risk_score")
                ),
                "spread_bps": _safe_float(volatile_context.get("spread_bps")),
            }
            candidate_for_plan.update(
                {
                    "lane": "volatile_attack",
                    "volatile_attack": True,
                    "volatile_attack_score": _safe_float(volatile_context.get("score")),
                    "metadata": metadata,
                }
            )
        pattern_prior = hooks.pattern_prior_for_candidate(
            crypto_patterns=crypto_patterns,
            symbol=source_symbol,
            side=side,
        )
        price_plan = hooks.design_price_plan(
            candidate=candidate_for_plan,
            features=features,
            market=market,
            side=side,
            horizon=horizon,
            account=account,
            pattern_prior=pattern_prior,
            live_authority=live_authority,
        )
        if not price_plan:
            return False
        seen.add((symbol, market, side, horizon))
        candidates.append(
            hooks.merge_candidate_price_plan(
                candidate=candidate_for_plan,
                symbol=symbol,
                market=market,
                side=side,
                horizon=horizon,
                price_plan=price_plan,
            )
        )
        return True

    for row in research_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        source_symbol = upbit_market_to_usdt_symbol(symbol)
        base_features = feature_index.get(source_symbol, {})
        side = side_from_crypto_research_candidate(row)
        market = market_from_crypto_research_candidate(
            candidate=row,
            features=base_features,
            side=side,
        )
        if market == UPBIT_SPOT_MARKET:
            symbol = upbit_market_symbol(symbol)
        if market == "spot" and side == "short":
            skipped.append(
                {"symbol": symbol, "market": market, "reason": "spot_short_unsupported"}
            )
            continue
        if market == UPBIT_SPOT_MARKET and side == "short":
            skipped.append(
                {
                    "symbol": symbol,
                    "market": market,
                    "reason": "upbit_spot_short_unsupported",
                }
            )
            continue
        if symbol not in set(market_universe.get(market) or []):
            skipped.append(
                {"symbol": symbol, "market": market, "reason": "outside_runtime_universe"}
            )
            continue
        horizon = normalize_binance_horizon(row.get("horizon"), market=market)
        key = (symbol, market, side, horizon)
        if key in seen:
            continue
        features = features_for_candidate_market(
            symbol=symbol,
            market=market,
            feature_index=feature_index,
            market_feature_index=market_feature_index,
        )
        if not append_candidate(
            candidate=row,
            symbol=symbol,
            source_symbol=source_symbol,
            market=market,
            side=side,
            horizon=horizon,
            features=features,
        ):
            reason = (
                "upbit_spot_missing_krw_price_inputs"
                if market == UPBIT_SPOT_MARKET
                else "missing_price_inputs"
            )
            skipped.append(
                {"symbol": symbol, "market": market, "reason": reason}
            )
            continue

        if (
            market == "futures"
            and side == "long"
            and candidate_is_explicit_long_candidate(row)
            and symbol in set(market_universe.get("spot") or [])
            and hooks.cash_reference_usdt(market="spot", account=account) > 0
        ):
            shadow = spot_shadow_candidate(row)
            shadow_horizon = normalize_binance_horizon(
                shadow.get("horizon"),
                market="spot",
            )
            shadow_key = (symbol, "spot", "long", shadow_horizon)
            if shadow_key not in seen:
                spot_features = features_for_candidate_market(
                    symbol=symbol,
                    market="spot",
                    feature_index=feature_index,
                    market_feature_index=market_feature_index,
                )
                if append_candidate(
                    candidate=shadow,
                    symbol=symbol,
                    source_symbol=symbol,
                    market="spot",
                    side="long",
                    horizon=shadow_horizon,
                    features=spot_features,
                ):
                    spot_shadow_count += 1
                else:
                    skipped.append(
                        {
                            "symbol": symbol,
                            "market": "spot",
                            "reason": "spot_shadow_missing_price_inputs",
                        }
                    )

        if (
            market == "spot"
            and side == "long"
            and candidate_derivatives_available(base_features)
            and symbol in set(market_universe.get("futures") or [])
            and hooks.cash_reference_usdt(market="futures", account=account) > 0
        ):
            shadow = futures_shadow_candidate(row)
            shadow_horizon = normalize_binance_horizon(
                shadow.get("horizon"),
                market="futures",
            )
            shadow_key = (symbol, "futures", "long", shadow_horizon)
            if shadow_key not in seen:
                futures_features = features_for_candidate_market(
                    symbol=symbol,
                    market="futures",
                    feature_index=feature_index,
                    market_feature_index=market_feature_index,
                )
                if append_candidate(
                    candidate=shadow,
                    symbol=symbol,
                    source_symbol=symbol,
                    market="futures",
                    side="long",
                    horizon=shadow_horizon,
                    features=futures_features,
                ):
                    futures_shadow_count += 1
                else:
                    skipped.append(
                        {
                            "symbol": symbol,
                            "market": "futures",
                            "reason": "futures_shadow_missing_price_inputs",
                        }
                    )

        upbit_symbol = upbit_market_symbol(source_symbol)
        if (
            side == "long"
            and upbit_symbol in set(market_universe.get(UPBIT_SPOT_MARKET) or [])
            and hooks.cash_reference_usdt(market=UPBIT_SPOT_MARKET, account=account) > 0
        ):
            shadow = upbit_shadow_candidate(row, source_symbol=source_symbol)
            shadow_horizon = normalize_binance_horizon(
                shadow.get("horizon"),
                market=UPBIT_SPOT_MARKET,
            )
            shadow_key = (upbit_symbol, UPBIT_SPOT_MARKET, "long", shadow_horizon)
            if shadow_key not in seen:
                upbit_features = features_for_candidate_market(
                    symbol=upbit_symbol,
                    market=UPBIT_SPOT_MARKET,
                    feature_index=feature_index,
                    market_feature_index=market_feature_index,
                )
                if append_candidate(
                    candidate=shadow,
                    symbol=upbit_symbol,
                    source_symbol=source_symbol,
                    market=UPBIT_SPOT_MARKET,
                    side="long",
                    horizon=shadow_horizon,
                    features=upbit_features,
                ):
                    upbit_shadow_count += 1
                else:
                    skipped.append(
                        {
                            "symbol": upbit_symbol,
                            "market": UPBIT_SPOT_MARKET,
                            "reason": "upbit_shadow_missing_price_inputs",
                        }
                    )
        if current_candidate_count + len(candidates) >= candidate_limit:
            break

    return candidates, skipped, seen, {
        "spot_shadow_count": spot_shadow_count,
        "futures_shadow_count": futures_shadow_count,
        "upbit_shadow_count": upbit_shadow_count,
    }


def manager_candidate_packet_overlay_rows(
    selected_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        calculated = (
            candidate.get("calculated")
            if isinstance(candidate.get("calculated"), dict)
            else candidate.get("calculated_price_plan")
            if isinstance(candidate.get("calculated_price_plan"), dict)
            else {}
        )
        sizing_inputs = (
            calculated.get("sizing_inputs")
            if isinstance(calculated.get("sizing_inputs"), dict)
            else {}
        )
        metadata = (
            candidate.get("metadata")
            if isinstance(candidate.get("metadata"), dict)
            else {}
        )
        performance_budget_multiplier = (
            _safe_float(candidate.get("performance_budget_multiplier"))
            or _safe_float(calculated.get("performance_budget_multiplier"))
            or _safe_float(sizing_inputs.get("performance_budget_multiplier"))
            or _safe_float(metadata.get("performance_budget_multiplier"))
        )
        empirical_edge_score = (
            _safe_float(candidate.get("empirical_edge_score"))
            or _safe_float(calculated.get("empirical_edge_score"))
            or _safe_float(metadata.get("empirical_edge_score"))
        )
        overlay: dict[str, Any] = {}
        if performance_budget_multiplier > 0:
            overlay["performance_budget_multiplier"] = round(
                performance_budget_multiplier,
                4,
            )
        if empirical_edge_score > 0:
            overlay["empirical_edge_score"] = round(empirical_edge_score, 4)
        if not overlay:
            continue
        overlays.append(
            {
                "symbol": symbol,
                "market": normalize_market(candidate.get("market") or candidate.get("venue")),
                "side": normalize_position_side(candidate.get("side") or candidate.get("stance")),
                "overlay": overlay,
            }
        )
    return overlays


def manager_candidate_packet_overlay_for_row(
    row: dict[str, Any],
    selected_overlays: list[dict[str, Any]],
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return {}
    market = normalize_market(row.get("market") or row.get("venue"))
    side = normalize_position_side(row.get("side") or row.get("stance"))
    for overlay_row in selected_overlays:
        if overlay_row.get("symbol") != symbol:
            continue
        if market and overlay_row.get("market") and overlay_row.get("market") != market:
            continue
        if side and overlay_row.get("side") and overlay_row.get("side") != side:
            continue
        overlay = overlay_row.get("overlay")
        return dict(overlay) if isinstance(overlay, dict) else {}
    return {}


def manager_candidate_packet_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(row.get("symbol") or "").upper().strip(),
        "market": str(row.get("market") or row.get("venue") or "spot"),
        "side": str(row.get("side") or row.get("stance") or ""),
        "score": _safe_float(row.get("score") or row.get("volatile_attack_score")),
        "change_pct_24h": _safe_float(row.get("change_pct_24h")),
        "quote_volume_usdt": _safe_float(row.get("quote_volume_usdt")),
        "volume_expansion_ratio": _safe_float(row.get("volume_expansion_ratio")),
        "spread_bps": _safe_float(row.get("spread_bps")),
        "squeeze_risk_score": _safe_float(
            row.get("squeeze_risk_score") or row.get("squeeze_score")
        ),
        "alpha_score_v3": score_crypto_candidate(row),
    }


def manager_candidate_packets(
    *,
    crypto_research: dict[str, Any],
    selected_candidates: list[dict[str, Any]],
    compact_value: Callable[..., Any],
    volatile_attack_context: Callable[..., dict[str, Any]],
    volatile_candidate_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    keys = (
        "top_movers",
        "volatile_candidates",
        "regime_leaders",
        "failed_breakout",
        "squeeze_setup",
    )
    raw_packets = (
        crypto_research.get("candidate_packets")
        if isinstance(crypto_research.get("candidate_packets"), dict)
        else {}
    )
    selected_overlays = manager_candidate_packet_overlay_rows(selected_candidates)
    packets: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        rows = raw_packets.get(key) if isinstance(raw_packets, dict) else []
        packets[key] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            compact = compact_value(row, string_limit=160, list_limit=5)
            if not isinstance(compact, dict):
                continue
            compact.setdefault("alpha_score_v3", score_crypto_candidate(row))
            overlay = manager_candidate_packet_overlay_for_row(row, selected_overlays)
            if overlay:
                compact.update(overlay)
            packets[key].append(compact)
            if len(packets[key]) >= _packet_limit_for_key(
                key,
                volatile_candidate_limit=volatile_candidate_limit,
            ):
                break

    feature_rows: list[dict[str, Any]] = []
    for item in crypto_research.get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper().strip()
        features = item.get("features") if isinstance(item.get("features"), dict) else {}
        row = {"symbol": symbol, **features}
        if symbol:
            feature_rows.append(row)

    if not packets["top_movers"]:
        packets["top_movers"] = [
            manager_candidate_packet_row(row)
            for row in sorted(
                feature_rows,
                key=lambda value: abs(_safe_float(value.get("change_pct_24h"))),
                reverse=True,
            )[
                : _packet_limit_for_key(
                    "top_movers",
                    volatile_candidate_limit=volatile_candidate_limit,
                )
            ]
        ]

    volatile_rows: list[dict[str, Any]] = []
    for row in feature_rows:
        context = volatile_attack_context(
            candidate={},
            features=row,
            spread_bps=_safe_float(row.get("spread_bps")),
            change_pct_24h=_safe_float(row.get("change_pct_24h")),
            market="spot",
        )
        if context.get("enabled"):
            volatile_rows.append({**row, "volatile_attack_score": context.get("score")})
    if not packets["volatile_candidates"]:
        packets["volatile_candidates"] = [
            manager_candidate_packet_row(row)
            for row in sorted(
                volatile_rows,
                key=lambda value: _safe_float(value.get("volatile_attack_score")),
                reverse=True,
            )[
                : _packet_limit_for_key(
                    "volatile_candidates",
                    volatile_candidate_limit=volatile_candidate_limit,
                )
            ]
        ]
    if not packets["regime_leaders"]:
        packets["regime_leaders"] = [
            manager_candidate_packet_row(row)
            for row in sorted(
                selected_candidates,
                key=lambda value: _safe_float(value.get("score")),
                reverse=True,
            )[
                : _packet_limit_for_key(
                    "regime_leaders",
                    volatile_candidate_limit=volatile_candidate_limit,
                )
            ]
        ]
    if not packets["failed_breakout"]:
        packets["failed_breakout"] = [
            manager_candidate_packet_row(row)
            for row in feature_rows
            if "failed" in str(
                row.get("entry_quality") or row.get("timeframe_alignment") or ""
            ).lower()
        ][
            : _packet_limit_for_key(
                "failed_breakout",
                volatile_candidate_limit=volatile_candidate_limit,
            )
        ]
    if not packets["squeeze_setup"]:
        packets["squeeze_setup"] = [
            manager_candidate_packet_row(row)
            for row in sorted(
                [
                    row
                    for row in feature_rows
                    if _safe_float(
                        row.get("squeeze_risk_score") or row.get("squeeze_score")
                    )
                    >= 65
                ],
                key=lambda value: _safe_float(
                    value.get("squeeze_risk_score") or value.get("squeeze_score")
                ),
                reverse=True,
            )[
                : _packet_limit_for_key(
                    "squeeze_setup",
                    volatile_candidate_limit=volatile_candidate_limit,
                )
            ]
        ]
    return packets


def _packet_limit_for_key(key: str, *, volatile_candidate_limit: int) -> int:
    if key == "volatile_candidates":
        return max(_safe_int(volatile_candidate_limit), 1)
    return 12


def finalize_manager_candidates(
    *,
    candidates: list[dict[str, Any]],
    hooks: BinanceManagerCandidateFinalizeHooks,
    max_items: int,
    active_blocks: list[dict[str, Any]] | None,
    live_authority: dict[str, Any],
    entry_gate_policy: dict[str, Any] | None,
    crypto_research: dict[str, Any],
    crypto_patterns: dict[str, Any],
    market_universe: dict[str, list[str]],
    provided_candidate_count: int,
    spot_shadow_count: int,
    futures_shadow_count: int,
    upbit_shadow_count: int,
    skipped: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates, overlap_marked_count = _annotate_active_block_overlap(
        candidates,
        active_blocks=active_blocks,
        duplicate_context=hooks.candidate_near_duplicate_active_block_context,
    )
    candidates, lane_authority_candidate_count = _annotate_lane_authority(
        candidates,
        live_authority=live_authority,
        entry_gate_policy=entry_gate_policy,
        lane_authority_context=hooks.candidate_lane_authority_context,
        edge_score=hooks.manager_candidate_empirical_edge_score,
    )
    candidates, execution_blocked_candidate_count = _annotate_execution_blockers(
        candidates,
        entry_gate_policy=entry_gate_policy,
        execution_blocker_context=hooks.candidate_execution_blocker_context,
        edge_score=hooks.manager_candidate_empirical_edge_score,
    )
    candidates, pattern_performance_candidate_count = _annotate_pattern_performance(
        candidates,
        entry_gate_policy=entry_gate_policy,
        annotate_candidate_pattern_performance=hooks.annotate_candidate_pattern_performance,
    )

    ranked_candidates = hooks.rank_manager_candidates_by_edge(
        candidates,
        entry_gate_policy=entry_gate_policy,
    )
    selected_candidates, diversified = hooks.diversify_manager_candidates_by_lane(
        ranked_candidates,
        max_items=max_items,
    )
    raw_lane_distribution = hooks.lane_distribution(ranked_candidates)
    selected_lane_distribution = hooks.lane_distribution(selected_candidates)
    candidate_packets = hooks.manager_candidate_packets(
        crypto_research=crypto_research,
        selected_candidates=selected_candidates,
    )
    stage_counts = hooks.manager_candidate_stage_counts(
        crypto_research=crypto_research,
        market_universe=market_universe,
        selected_candidates=selected_candidates,
    )
    volatile_attack_candidate_count = sum(
        1
        for row in selected_candidates
        if hooks.market_side_lane(row) == "volatile_attack"
    )

    return selected_candidates, {
        "source": "crypto_research_price_design_v1",
        "candidate_count": len(selected_candidates),
        "candidate_total_before_limit": len(candidates),
        "edge_ranked": True,
        "stage_counts": stage_counts,
        "candidate_packets": candidate_packets,
        "volatile_attack_candidate_count": volatile_attack_candidate_count,
        "candidate_lane_diversified": diversified,
        "lane_distribution": selected_lane_distribution,
        "raw_lane_distribution": raw_lane_distribution,
        "spot_shadow_candidate_count": spot_shadow_count,
        "futures_shadow_candidate_count": futures_shadow_count,
        "upbit_shadow_candidate_count": upbit_shadow_count,
        "near_duplicate_candidate_count": overlap_marked_count,
        "lane_authority_candidate_count": lane_authority_candidate_count,
        "execution_blocked_candidate_count": execution_blocked_candidate_count,
        "pattern_performance_candidate_count": pattern_performance_candidate_count,
        "provided_candidate_count": provided_candidate_count,
        "research_candidate_count": _context_list_count(crypto_research, "candidates"),
        "optimized_strategy_set_count": _context_list_count(
            crypto_patterns,
            "optimized_strategy_sets",
        ),
        "skipped": skipped[:8],
        "contract": (
            "Candidates include calculated entry/target/stop defaults plus the input "
            "numbers used to calculate them. Jue may override final prices, but must "
            "carry the calculated_price_plan and explain the override_reason. "
            "calculated.pattern_live_crosscheck shows whether optimized pattern geometry "
            "is aligned, waiting for live confirmation, or contradicted by live context."
        ),
    }


def _annotate_active_block_overlap(
    candidates: list[dict[str, Any]],
    *,
    active_blocks: list[dict[str, Any]] | None,
    duplicate_context: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if not active_blocks:
        return candidates, 0
    annotated_candidates: list[dict[str, Any]] = []
    count = 0
    for row in candidates:
        duplicate = duplicate_context(row, active_blocks)
        if duplicate:
            row = {**row, "near_duplicate_active_block": duplicate}
            count += 1
        annotated_candidates.append(row)
    return annotated_candidates, count


def _annotate_lane_authority(
    candidates: list[dict[str, Any]],
    *,
    live_authority: dict[str, Any],
    entry_gate_policy: dict[str, Any] | None,
    lane_authority_context: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    edge_score: Callable[..., float],
) -> tuple[list[dict[str, Any]], int]:
    if not live_authority:
        return candidates, 0
    annotated_candidates: list[dict[str, Any]] = []
    count = 0
    for row in candidates:
        lane_context = lane_authority_context(live_authority, row)
        if lane_context:
            row = {**row, "lane_authority_candidate": lane_context}
            count += 1
            row = _attach_empirical_edge_score(
                row,
                edge_score(row, entry_gate_policy=entry_gate_policy),
                lane_context=lane_context,
            )
        annotated_candidates.append(row)
    return annotated_candidates, count


def _annotate_execution_blockers(
    candidates: list[dict[str, Any]],
    *,
    entry_gate_policy: dict[str, Any] | None,
    execution_blocker_context: Callable[[dict[str, Any]], dict[str, Any]],
    edge_score: Callable[..., float],
) -> tuple[list[dict[str, Any]], int]:
    annotated_candidates: list[dict[str, Any]] = []
    count = 0
    for row in candidates:
        blocker_context = execution_blocker_context(row)
        if blocker_context:
            row = {**row, "execution_blockers": blocker_context}
            metadata = dict(row.get("metadata")) if isinstance(row.get("metadata"), dict) else {}
            metadata["execution_blockers"] = blocker_context
            row["metadata"] = metadata
            count += 1
        if blocker_context or isinstance(row.get("lane_authority_candidate"), dict):
            row = _attach_empirical_edge_score(
                row,
                edge_score(row, entry_gate_policy=entry_gate_policy),
            )
        annotated_candidates.append(row)
    return annotated_candidates, count


def _annotate_pattern_performance(
    candidates: list[dict[str, Any]],
    *,
    entry_gate_policy: dict[str, Any] | None,
    annotate_candidate_pattern_performance: Callable[
        ...,
        tuple[dict[str, Any], bool],
    ],
) -> tuple[list[dict[str, Any]], int]:
    annotated_candidates: list[dict[str, Any]] = []
    count = 0
    for row in candidates:
        row, annotated = annotate_candidate_pattern_performance(
            row,
            entry_gate_policy=entry_gate_policy,
        )
        if annotated:
            count += 1
        annotated_candidates.append(row)
    return annotated_candidates, count


def _attach_empirical_edge_score(
    row: dict[str, Any],
    edge_score: float,
    *,
    lane_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row["empirical_edge_score"] = edge_score
    calculated = dict(row.get("calculated")) if isinstance(row.get("calculated"), dict) else {}
    calculated["empirical_edge_score"] = edge_score
    row["calculated"] = calculated
    row["calculated_price_plan"] = calculated
    metadata = dict(row.get("metadata")) if isinstance(row.get("metadata"), dict) else {}
    if lane_context is not None:
        metadata["lane_authority_candidate"] = lane_context
    metadata["empirical_edge_score"] = edge_score
    row["metadata"] = metadata
    return row


def _context_list_count(payload: dict[str, Any], key: str) -> int:
    rows = payload.get(key) if isinstance(payload, dict) else None
    return len(rows) if isinstance(rows, list) else 0
