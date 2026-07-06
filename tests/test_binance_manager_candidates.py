from __future__ import annotations

from typing import Any

import pytest

from tradecraft.services.binance_manager_candidates import (
    BinanceManagerCandidateFinalizeHooks,
    BinanceProvidedCandidateBuildHooks,
    BinanceResearchCandidateBuildHooks,
    build_research_manager_candidates,
    build_provided_manager_candidates,
    candidate_identity,
    candidate_derivatives_available,
    candidate_execution_blocker_context,
    candidate_is_explicit_long_candidate,
    diversify_manager_candidates_by_lane,
    finalize_manager_candidates,
    futures_shadow_candidate,
    crypto_research_feature_index,
    crypto_research_market_feature_index,
    features_for_candidate_market,
    manager_candidate_packet_overlay_for_row,
    manager_candidate_packet_overlay_rows,
    manager_candidate_packets,
    market_from_crypto_research_candidate,
    side_from_crypto_research_candidate,
    spot_shadow_candidate,
    upbit_shadow_candidate,
)


def test_candidate_identity_normalizes_market_side_and_horizon() -> None:
    assert candidate_identity(
        {
            "symbol": " ethusdt ",
            "venue": "binance_futures",
            "direction": "SHORT",
            "horizon": "mid",
        }
    ) == ("ETHUSDT", "futures", "short", "futures")


def test_diversify_manager_candidates_by_lane_picks_one_from_each_lane_first() -> None:
    candidates = [
        {"symbol": "AUSDT", "market": "futures", "side": "short"},
        {"symbol": "BUSDT", "market": "futures", "side": "short"},
        {"symbol": "CUSDT", "market": "spot", "side": "long"},
        {"symbol": "DUSDT", "market": "futures", "side": "long"},
    ]

    selected, diversified = diversify_manager_candidates_by_lane(candidates, max_items=3)

    assert [row["symbol"] for row in selected] == ["CUSDT", "DUSDT", "AUSDT"]
    assert diversified is True


def test_diversify_manager_candidates_keeps_under_limit_rows_unchanged() -> None:
    candidates = [
        {"symbol": "BTCUSDT", "market": "spot", "side": "long", "horizon": "short"},
        {"symbol": "BTCUSDT", "market": "spot", "side": "long", "horizon": "short"},
        {"symbol": "ETHUSDT", "market": "spot", "side": "long", "horizon": "mid"},
    ]

    selected, diversified = diversify_manager_candidates_by_lane(candidates, max_items=3)

    assert selected == candidates
    assert diversified is False


def test_candidate_execution_blocker_context_compacts_rejection_details() -> None:
    def symbol_cooldown(row: dict[str, Any]) -> dict[str, Any]:
        assert row["symbol"] == "ETHUSDT"
        return {
            "reason": "symbol_performance_cooldown:ETHUSDT:futures:short",
            "symbol_performance_cooldown": {
                "symbol": "ETHUSDT",
                "matched_lane": "futures:short",
                "sample_count": 7,
                "pnl_usdt": -3.25,
                "profit_factor": 0.42,
                "recovery_required": "fresh positive samples before another block",
                "ignored_blob": {"large": "payload"},
            },
        }

    context = candidate_execution_blocker_context(
        {"symbol": "ETHUSDT"},
        checks=[("symbol_performance_cooldown", symbol_cooldown)],
    )

    assert context["version"] == "binance_candidate_execution_blocker_v1"
    assert context["status"] == "would_reject_current_gates"
    assert context["blocker_count"] == 1
    assert context["ranking_penalty"] == 75.0
    assert context["blockers"] == [
        {
            "kind": "symbol_performance_cooldown",
            "reason": "symbol_performance_cooldown:ETHUSDT:futures:short",
            "symbol": "ETHUSDT",
            "matched_lane": "futures:short",
            "sample_count": 7,
            "pnl_usdt": -3.25,
            "profit_factor": 0.42,
            "recovery_required": "fresh positive samples before another block",
        }
    ]


def test_candidate_execution_blocker_context_caps_blockers_and_penalty() -> None:
    def reject_with(kind: str):
        def checker(_row: dict[str, Any]) -> dict[str, Any]:
            return {"reason": f"{kind}:bad", kind: {"sample_count": 1}}

        return checker

    checks = [(f"kind_{index}", reject_with(f"kind_{index}")) for index in range(6)]

    context = candidate_execution_blocker_context({"symbol": "X"}, checks=checks)

    assert context["blocker_count"] == 6
    assert len(context["blockers"]) == 4
    assert context["ranking_penalty"] == 150.0


def test_candidate_execution_blocker_context_returns_empty_without_rejections() -> None:
    assert candidate_execution_blocker_context(
        {"symbol": "BTCUSDT"},
        checks=[("noop", lambda _row: {})],
    ) == {}


def test_crypto_research_candidate_side_and_market_helpers() -> None:
    assert side_from_crypto_research_candidate({"direction": "bearish"}) == "short"
    assert side_from_crypto_research_candidate({"stance": "long_watch"}) == "long"
    assert candidate_is_explicit_long_candidate({"side": "long_watch"}) is True
    assert candidate_is_explicit_long_candidate({"side": "bearish"}) is False
    assert candidate_derivatives_available({"derivatives_status": "available"}) is True

    assert (
        market_from_crypto_research_candidate(
            candidate={},
            features={"derivatives_status": "available"},
            side="short",
        )
        == "futures"
    )
    assert (
        market_from_crypto_research_candidate(
            candidate={"venue": "upbit"},
            features={"derivatives_status": "available"},
            side="short",
        )
        == "upbit_spot"
    )


def test_shadow_candidate_builders_preserve_source_and_mark_metadata() -> None:
    candidate = {
        "symbol": "ETHUSDT",
        "market": "futures",
        "side": "long",
        "reason_md": "Momentum reclaim with improving book depth.",
        "metadata": {"existing": True},
    }

    spot = spot_shadow_candidate(candidate)
    assert spot["market"] == "spot"
    assert spot["venue"] == "spot"
    assert spot["side"] == "long"
    assert spot["source_market"] == "futures"
    assert spot["metadata"]["existing"] is True
    assert spot["metadata"]["spot_shadow"] is True
    assert "Spot long shadow" in spot["reason_md"]

    futures = futures_shadow_candidate({**candidate, "market": "spot"})
    assert futures["market"] == "futures"
    assert futures["horizon"] == "futures"
    assert futures["metadata"]["futures_shadow"] is True

    upbit = upbit_shadow_candidate(candidate, source_symbol="ETHUSDT")
    assert upbit["symbol"] == "KRW-ETH"
    assert upbit["market"] == "upbit_spot"
    assert upbit["source_symbol"] == "ETHUSDT"
    assert upbit["metadata"]["upbit_shadow"] is True


def test_build_provided_manager_candidates_normalizes_dedupes_and_skips_unsupported() -> None:
    design_calls: list[dict[str, Any]] = []

    def pattern_prior_for_candidate(**kwargs: Any) -> dict[str, Any]:
        return {
            "symbol": kwargs["symbol"],
            "side": kwargs["side"],
        }

    def design_price_plan(**kwargs: Any) -> dict[str, Any]:
        design_calls.append(kwargs)
        return {
            "entry_price": kwargs["features"].get("price"),
            "pattern_prior": kwargs["pattern_prior"],
        }

    def merge_candidate_price_plan(**kwargs: Any) -> dict[str, Any]:
        return {
            "symbol": kwargs["symbol"],
            "market": kwargs["market"],
            "side": kwargs["side"],
            "horizon": kwargs["horizon"],
            "calculated": kwargs["price_plan"],
        }

    candidates, skipped, seen = build_provided_manager_candidates(
        provided_candidates=[
            {
                "symbol": "btcusdt",
                "market": "spot",
                "side": "long",
                "horizon": "short",
            },
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "stance": "long",
                "horizon": "short",
            },
            {
                "symbol": "KRW-XRP",
                "market": "upbit_spot",
                "side": "short",
            },
            "bad row",
        ],
        feature_index={"BTCUSDT": {"price": 100.0, "spread_bps": 8}},
        market_feature_index={
            ("BTCUSDT", "spot"): {
                "price": 101.0,
                "bid_price": 100.9,
                "ask_price": 101.1,
            }
        },
        crypto_patterns={},
        live_authority={"status": "ok"},
        account={"spot_cash_usdt": 200.0},
        hooks=BinanceProvidedCandidateBuildHooks(
            pattern_prior_for_candidate=pattern_prior_for_candidate,
            design_price_plan=design_price_plan,
            merge_candidate_price_plan=merge_candidate_price_plan,
        ),
    )

    assert candidates == [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "calculated": {
                "entry_price": 101.0,
                "pattern_prior": {"symbol": "BTCUSDT", "side": "long"},
            },
        }
    ]
    assert skipped == [
        {
            "symbol": "KRW-XRP",
            "market": "upbit_spot",
            "reason": "upbit_spot_short_unsupported",
        }
    ]
    assert seen == {("BTCUSDT", "spot", "long", "short")}
    assert len(design_calls) == 1
    assert design_calls[0]["features"]["bid_price"] == 100.9


def test_build_provided_manager_candidates_skips_unpriced_upbit_candidate() -> None:
    def pattern_prior_for_candidate(**kwargs: Any) -> dict[str, Any]:
        return {"symbol": kwargs["symbol"], "side": kwargs["side"]}

    def design_price_plan(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["market"] == "upbit_spot"
        assert "price" not in kwargs["features"]
        return {}

    def merge_candidate_price_plan(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("unpriced provided candidates must not be merged")

    candidates, skipped, seen = build_provided_manager_candidates(
        provided_candidates=[
            {
                "symbol": "KRW-AAVE",
                "market": "upbit_spot",
                "side": "long",
                "horizon": "intraday",
            }
        ],
        feature_index={"AAVEUSDT": {"price": 85.0}},
        market_feature_index={
            ("AAVEUSDT", "upbit_spot"): {
                "price": 85.0,
                "bid_price": 84.9,
                "ask_price": 85.1,
            }
        },
        crypto_patterns={},
        live_authority={"status": "ok"},
        account={"upbit_spot_cash_usdt": 100.0},
        hooks=BinanceProvidedCandidateBuildHooks(
            pattern_prior_for_candidate=pattern_prior_for_candidate,
            design_price_plan=design_price_plan,
            merge_candidate_price_plan=merge_candidate_price_plan,
        ),
    )

    assert candidates == []
    assert skipped == [
        {
            "symbol": "KRW-AAVE",
            "market": "upbit_spot",
            "reason": "upbit_spot_missing_krw_price_inputs",
        }
    ]
    assert seen == set()


def test_build_research_manager_candidates_builds_primary_and_shadow_candidates() -> None:
    def pattern_prior_for_candidate(**kwargs: Any) -> dict[str, Any]:
        return {"symbol": kwargs["symbol"], "side": kwargs["side"]}

    def cash_reference_usdt(*, market: str, account: dict[str, Any]) -> float:
        return float(account.get(f"{market}_cash_usdt", 0.0))

    def design_price_plan(**kwargs: Any) -> dict[str, Any]:
        return {
            "entry_price": kwargs["features"].get("price", 0.0),
            "market": kwargs["market"],
            "pattern_prior": kwargs["pattern_prior"],
        }

    def merge_candidate_price_plan(**kwargs: Any) -> dict[str, Any]:
        return {
            "symbol": kwargs["symbol"],
            "market": kwargs["market"],
            "side": kwargs["side"],
            "horizon": kwargs["horizon"],
            "spot_shadow": bool(kwargs["candidate"].get("spot_shadow")),
            "upbit_shadow": bool(kwargs["candidate"].get("upbit_shadow")),
            "calculated": kwargs["price_plan"],
        }

    candidates, skipped, seen, counts = build_research_manager_candidates(
        research_candidates=[
            {
                "symbol": "ethusdt",
                "direction": "long",
                "market": "futures",
                "horizon": "futures",
            },
            {"symbol": "badusdt", "side": "short", "market": "spot"},
            {"symbol": "outsiderusdt", "side": "long", "market": "spot"},
        ],
        feature_index={"ETHUSDT": {"price": 2500.0, "derivatives_status": "available"}},
        market_feature_index={
            ("ETHUSDT", "futures"): {"price": 2501.0},
            ("ETHUSDT", "spot"): {"price": 2499.0},
            ("KRW-ETH", "upbit_spot"): {"price": 3_400_000.0},
        },
        crypto_patterns={},
        live_authority={"status": "ok"},
        market_universe={
            "spot": ["ETHUSDT"],
            "futures": ["ETHUSDT"],
            "upbit_spot": ["KRW-ETH"],
        },
        account={
            "spot_cash_usdt": 100.0,
            "futures_cash_usdt": 100.0,
            "upbit_spot_cash_usdt": 100.0,
        },
        seen=set(),
        max_items=10,
        current_candidate_count=0,
        hooks=BinanceResearchCandidateBuildHooks(
            pattern_prior_for_candidate=pattern_prior_for_candidate,
            design_price_plan=design_price_plan,
            merge_candidate_price_plan=merge_candidate_price_plan,
            cash_reference_usdt=cash_reference_usdt,
            volatile_attack_context=lambda **_kwargs: {},
        ),
    )

    assert [(row["symbol"], row["market"], row["side"]) for row in candidates] == [
        ("ETHUSDT", "futures", "long"),
        ("ETHUSDT", "spot", "long"),
        ("KRW-ETH", "upbit_spot", "long"),
    ]
    assert candidates[1]["spot_shadow"] is True
    assert candidates[2]["upbit_shadow"] is True
    assert skipped == [
        {"symbol": "BADUSDT", "market": "spot", "reason": "spot_short_unsupported"},
        {
            "symbol": "OUTSIDERUSDT",
            "market": "spot",
            "reason": "outside_runtime_universe",
        },
    ]
    assert counts == {
        "spot_shadow_count": 1,
        "futures_shadow_count": 0,
        "upbit_shadow_count": 1,
    }
    assert ("ETHUSDT", "futures", "long", "futures") in seen
    assert ("ETHUSDT", "spot", "long", "short") in seen
    assert ("KRW-ETH", "upbit_spot", "long", "short") in seen


def test_build_research_manager_candidates_skips_upbit_shadow_without_krw_book() -> None:
    def pattern_prior_for_candidate(**kwargs: Any) -> dict[str, Any]:
        return {"symbol": kwargs["symbol"], "side": kwargs["side"]}

    def cash_reference_usdt(*, market: str, account: dict[str, Any]) -> float:
        return 100.0 if market == "upbit_spot" else float(account.get(f"{market}_cash_usdt", 0.0))

    def design_price_plan(**kwargs: Any) -> dict[str, Any]:
        price = kwargs["features"].get("price", 0.0)
        if price <= 0:
            return {}
        return {"entry_price": price, "market": kwargs["market"]}

    def merge_candidate_price_plan(**kwargs: Any) -> dict[str, Any]:
        return {
            "symbol": kwargs["symbol"],
            "market": kwargs["market"],
            "side": kwargs["side"],
            "horizon": kwargs["horizon"],
            "upbit_shadow": bool(kwargs["candidate"].get("upbit_shadow")),
            "calculated": kwargs["price_plan"],
        }

    candidates, skipped, _seen, counts = build_research_manager_candidates(
        research_candidates=[
            {
                "symbol": "aaveusdt",
                "direction": "long",
                "market": "spot",
                "horizon": "intraday",
            }
        ],
        feature_index={
            "AAVEUSDT": {
                "price": 85.0,
                "last_price": 85.0,
                "bid_price": 84.9,
                "ask_price": 85.1,
                "book_fresh": True,
                "derivatives_status": "available",
            }
        },
        market_feature_index={
            ("AAVEUSDT", "spot"): {"price": 85.0, "bid_price": 84.9, "ask_price": 85.1}
        },
        crypto_patterns={},
        live_authority={"status": "ok"},
        market_universe={
            "spot": ["AAVEUSDT"],
            "futures": [],
            "upbit_spot": ["KRW-AAVE"],
        },
        account={
            "spot_cash_usdt": 100.0,
            "futures_cash_usdt": 0.0,
            "upbit_spot_cash_usdt": 100.0,
        },
        seen=set(),
        max_items=10,
        current_candidate_count=0,
        hooks=BinanceResearchCandidateBuildHooks(
            pattern_prior_for_candidate=pattern_prior_for_candidate,
            design_price_plan=design_price_plan,
            merge_candidate_price_plan=merge_candidate_price_plan,
            cash_reference_usdt=cash_reference_usdt,
            volatile_attack_context=lambda **_kwargs: {},
        ),
    )

    assert [(row["symbol"], row["market"]) for row in candidates] == [
        ("AAVEUSDT", "spot")
    ]
    assert {"symbol": "KRW-AAVE", "market": "upbit_spot", "reason": "upbit_shadow_missing_price_inputs"} in skipped
    assert counts["upbit_shadow_count"] == 0


def test_manager_candidate_packet_overlays_selected_budget_and_edge() -> None:
    selected = [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "calculated": {
                "performance_budget_multiplier": 1.23456,
                "empirical_edge_score": 77.777,
            },
        }
    ]

    overlays = manager_candidate_packet_overlay_rows(selected)
    overlay = manager_candidate_packet_overlay_for_row(
        {"symbol": "BTCUSDT", "market": "spot", "side": "long"},
        overlays,
    )

    assert overlay == {
        "performance_budget_multiplier": 1.2346,
        "empirical_edge_score": 77.777,
    }


def test_manager_candidate_packets_compacts_raw_packets_and_applies_overlays() -> None:
    selected = [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "metadata": {"performance_budget_multiplier": 1.5},
        }
    ]

    packets = manager_candidate_packets(
        crypto_research={
            "candidate_packets": {
                "top_movers": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "change_pct_24h": 8.2,
                        "very_long": "x" * 300,
                    }
                ]
            }
        },
        selected_candidates=selected,
        compact_value=lambda row, **_kwargs: dict(row),
        volatile_attack_context=lambda **_kwargs: {},
        volatile_candidate_limit=4,
    )

    assert packets["top_movers"][0]["symbol"] == "BTCUSDT"
    assert packets["top_movers"][0]["performance_budget_multiplier"] == 1.5
    assert packets["top_movers"][0]["alpha_score_v3"]["version"] == "crypto_alpha_score_v3"


def test_manager_candidate_packets_builds_fallback_packets_from_features() -> None:
    packets = manager_candidate_packets(
        crypto_research={
            "items": [
                {
                    "symbol": "AAAUSDT",
                    "features": {
                        "change_pct_24h": 2.0,
                        "quote_volume_usdt": 100,
                        "spread_bps": 5,
                        "squeeze_risk_score": 70,
                    },
                },
                {
                    "symbol": "BBBUSDT",
                    "features": {
                        "change_pct_24h": -9.0,
                        "quote_volume_usdt": 200,
                        "spread_bps": 8,
                        "entry_quality": "failed_breakout",
                    },
                },
            ],
        },
        selected_candidates=[
            {"symbol": "CCCUSDT", "score": 91, "market": "futures", "side": "short"}
        ],
        compact_value=lambda row, **_kwargs: dict(row),
        volatile_attack_context=lambda **kwargs: (
            {"enabled": True, "score": 88}
            if kwargs["features"].get("symbol") == "BBBUSDT"
            else {"enabled": False}
        ),
        volatile_candidate_limit=1,
    )

    assert packets["top_movers"][0]["symbol"] == "BBBUSDT"
    assert packets["volatile_candidates"] == [
        {
            "symbol": "BBBUSDT",
            "market": "spot",
            "side": "",
            "score": 88.0,
            "change_pct_24h": -9.0,
            "quote_volume_usdt": 200.0,
            "volume_expansion_ratio": 0.0,
            "spread_bps": 8.0,
            "squeeze_risk_score": 0.0,
            "alpha_score_v3": packets["volatile_candidates"][0]["alpha_score_v3"],
        }
    ]
    assert packets["regime_leaders"][0]["symbol"] == "CCCUSDT"
    assert packets["failed_breakout"][0]["symbol"] == "BBBUSDT"
    assert packets["squeeze_setup"][0]["symbol"] == "AAAUSDT"


def test_crypto_research_feature_index_extracts_symbol_features() -> None:
    index = crypto_research_feature_index(
        {
            "items": [
                {"symbol": "btcusdt", "features": {"price": 100, "spread_bps": 3}},
                {"symbol": "", "features": {"price": 1}},
                {"symbol": "ETHUSDT", "features": "bad"},
            ]
        }
    )

    assert index == {"BTCUSDT": {"price": 100, "spread_bps": 3}}


def test_crypto_research_market_feature_index_accepts_tuple_and_pipe_keys() -> None:
    index = crypto_research_market_feature_index(
        {
            "_book_features_by_market": {
                ("BTCUSDT", "futures"): {"bid_price": 99, "ask_price": 101},
                "KRW-ETH|upbit": {"bid_price": 1_000_000, "ask_price": 1_001_000},
                "BAD": {"bid_price": 1},
            }
        }
    )

    assert index[("BTCUSDT", "futures")] == {"bid_price": 99, "ask_price": 101}
    assert index[("KRW-ETH", "upbit_spot")] == {
        "bid_price": 1_000_000,
        "ask_price": 1_001_000,
    }


def test_features_for_candidate_market_prefers_market_specific_book_fields() -> None:
    feature_index = {
        "ETHUSDT": {
            "price": 2000,
            "bid_price": 1990,
            "ask_price": 2010,
            "spread_bps": 50,
            "volume_expansion_ratio": 2,
        }
    }
    market_feature_index = {
        ("KRW-ETH", "upbit_spot"): {
            "bid_price": 2_800_000,
            "ask_price": 2_805_000,
            "spread_bps": 17,
            "book_market": "upbit_spot",
        }
    }

    features = features_for_candidate_market(
        symbol="KRW-ETH",
        market="upbit_spot",
        feature_index=feature_index,
        market_feature_index=market_feature_index,
    )

    assert features["price"] == pytest.approx(2_802_500)
    assert features["source_usdt_price"] == pytest.approx(2000)
    assert features["volume_expansion_ratio"] == 2
    assert features["bid_price"] == 2_800_000
    assert features["ask_price"] == 2_805_000
    assert features["spread_bps"] == 17
    assert features["book_market"] == "upbit_spot"


def test_features_for_upbit_candidate_prefers_krw_book_mid_over_source_price() -> None:
    feature_index = {
        "AAVEUSDT": {
            "price": 85.0,
            "last_price": 85.0,
            "volume_expansion_ratio": 2.0,
        }
    }
    market_feature_index = {
        ("KRW-AAVE", "upbit_spot"): {
            "price": 85.2,
            "last_price": 85.2,
            "bid_price": 115_000.0,
            "ask_price": 116_000.0,
            "spread_bps": 86.6,
            "book_fresh": True,
            "book_market": "upbit_spot",
        }
    }

    features = features_for_candidate_market(
        symbol="KRW-AAVE",
        market="upbit_spot",
        feature_index=feature_index,
        market_feature_index=market_feature_index,
    )

    assert features["source_usdt_price"] == pytest.approx(85.0)
    assert features["price"] == pytest.approx(115_500.0)
    assert features["last_price"] == pytest.approx(115_500.0)
    assert features["current_price"] == pytest.approx(115_500.0)
    assert features["bid_price"] == pytest.approx(115_000.0)
    assert features["ask_price"] == pytest.approx(116_000.0)


def test_features_for_upbit_candidate_rejects_market_price_without_book_mid() -> None:
    feature_index = {
        "MORPHOUSDT": {
            "price": 2.14,
            "last_price": 2.14,
        }
    }
    market_feature_index = {
        ("KRW-MORPHO", "upbit_spot"): {
            "price": 2.14,
            "last_price": 2.14,
            "spread_bps": 0.0,
            "book_market": "upbit_spot",
        }
    }

    features = features_for_candidate_market(
        symbol="KRW-MORPHO",
        market="upbit_spot",
        feature_index=feature_index,
        market_feature_index=market_feature_index,
    )

    assert features["source_usdt_price"] == pytest.approx(2.14)
    assert "price" not in features
    assert "last_price" not in features
    assert "current_price" not in features


def test_features_for_upbit_candidate_requires_krw_market_book_for_price() -> None:
    feature_index = {
        "AAVEUSDT": {
            "price": 85.0,
            "last_price": 85.0,
            "bid_price": 84.9,
            "ask_price": 85.1,
            "spread_bps": 23.5,
            "book_fresh": True,
            "book_market": "spot",
            "volume_expansion_ratio": 2.0,
        }
    }

    features = features_for_candidate_market(
        symbol="KRW-AAVE",
        market="upbit_spot",
        feature_index=feature_index,
        market_feature_index={},
    )

    assert features["source_usdt_price"] == pytest.approx(85.0)
    assert "price" not in features
    assert "last_price" not in features
    assert "bid_price" not in features
    assert "ask_price" not in features
    assert "book_fresh" not in features
    assert features["volume_expansion_ratio"] == 2.0


def test_features_for_upbit_candidate_ignores_source_symbol_market_features() -> None:
    feature_index = {
        "AAVEUSDT": {
            "price": 85.0,
            "last_price": 85.0,
            "volume_expansion_ratio": 2.0,
        }
    }
    market_feature_index = {
        ("AAVEUSDT", "upbit_spot"): {
            "price": 85.2,
            "bid_price": 85.1,
            "ask_price": 85.3,
            "book_fresh": True,
            "book_market": "upbit_spot",
        }
    }

    features = features_for_candidate_market(
        symbol="KRW-AAVE",
        market="upbit_spot",
        feature_index=feature_index,
        market_feature_index=market_feature_index,
    )

    assert features["source_usdt_price"] == pytest.approx(85.0)
    assert "price" not in features
    assert "bid_price" not in features
    assert "ask_price" not in features
    assert "book_fresh" not in features
    assert "book_market" not in features
    assert features["volume_expansion_ratio"] == 2.0


def test_build_research_manager_candidates_skips_direct_upbit_without_krw_book() -> None:
    def pattern_prior_for_candidate(**kwargs: Any) -> dict[str, Any]:
        return {"symbol": kwargs["symbol"], "side": kwargs["side"]}

    def cash_reference_usdt(*, market: str, account: dict[str, Any]) -> float:
        return 100.0 if market == "upbit_spot" else float(account.get(f"{market}_cash_usdt", 0.0))

    def design_price_plan(**kwargs: Any) -> dict[str, Any]:
        price = kwargs["features"].get("price", 0.0)
        if price <= 0:
            return {}
        return {"entry_price": price, "market": kwargs["market"]}

    def merge_candidate_price_plan(**kwargs: Any) -> dict[str, Any]:
        return {
            "symbol": kwargs["symbol"],
            "market": kwargs["market"],
            "side": kwargs["side"],
            "horizon": kwargs["horizon"],
            "calculated": kwargs["price_plan"],
        }

    candidates, skipped, _seen, counts = build_research_manager_candidates(
        research_candidates=[
            {
                "symbol": "KRW-AAVE",
                "direction": "long",
                "market": "upbit_spot",
                "horizon": "intraday",
            }
        ],
        feature_index={
            "AAVEUSDT": {
                "price": 85.0,
                "last_price": 85.0,
                "bid_price": 84.9,
                "ask_price": 85.1,
                "derivatives_status": "available",
            }
        },
        market_feature_index={
            ("AAVEUSDT", "upbit_spot"): {
                "price": 85.0,
                "bid_price": 84.9,
                "ask_price": 85.1,
                "book_fresh": True,
            }
        },
        crypto_patterns={},
        live_authority={"status": "ok"},
        market_universe={
            "spot": [],
            "futures": [],
            "upbit_spot": ["KRW-AAVE"],
        },
        account={
            "spot_cash_usdt": 0.0,
            "futures_cash_usdt": 0.0,
            "upbit_spot_cash_usdt": 100.0,
        },
        seen=set(),
        max_items=10,
        current_candidate_count=0,
        hooks=BinanceResearchCandidateBuildHooks(
            pattern_prior_for_candidate=pattern_prior_for_candidate,
            design_price_plan=design_price_plan,
            merge_candidate_price_plan=merge_candidate_price_plan,
            cash_reference_usdt=cash_reference_usdt,
            volatile_attack_context=lambda **_kwargs: {},
        ),
    )

    assert candidates == []
    assert {
        "symbol": "KRW-AAVE",
        "market": "upbit_spot",
        "reason": "upbit_spot_missing_krw_price_inputs",
    } in skipped
    assert counts["upbit_shadow_count"] == 0


def test_finalize_manager_candidates_annotates_ranks_and_summarizes_candidates() -> None:
    candidates = [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "calculated": {"lane": "volatile_attack"},
        },
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "calculated": {},
        },
    ]

    def near_duplicate(row: dict[str, Any], _active: list[dict[str, Any]]) -> dict[str, Any]:
        return {"block_id": "B1"} if row["symbol"] == "BTCUSDT" else {}

    def lane_authority(_authority: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        return {"action": "allow_probe"} if row["symbol"] == "BTCUSDT" else {}

    def execution_blocker(row: dict[str, Any]) -> dict[str, Any]:
        return {"reason": "spread_too_wide"} if row["symbol"] == "ETHUSDT" else {}

    def edge_score(row: dict[str, Any], *, entry_gate_policy: dict[str, Any] | None) -> float:
        _ = entry_gate_policy
        return 91.0 if row["symbol"] == "BTCUSDT" else 12.0

    def annotate_pattern(
        row: dict[str, Any],
        *,
        entry_gate_policy: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        _ = entry_gate_policy
        return {**row, "pattern_scorecard_attached": True}, True

    def rank_rows(
        rows: list[dict[str, Any]],
        *,
        entry_gate_policy: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        _ = entry_gate_policy
        return sorted(rows, key=lambda row: row.get("empirical_edge_score", 0), reverse=True)

    def diversify(
        rows: list[dict[str, Any]],
        *,
        max_items: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        return rows[:max_items], True

    def lane_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"count": len(rows), "symbols": [row["symbol"] for row in rows]}

    hooks = BinanceManagerCandidateFinalizeHooks(
        candidate_near_duplicate_active_block_context=near_duplicate,
        candidate_lane_authority_context=lane_authority,
        manager_candidate_empirical_edge_score=edge_score,
        candidate_execution_blocker_context=execution_blocker,
        annotate_candidate_pattern_performance=annotate_pattern,
        rank_manager_candidates_by_edge=rank_rows,
        diversify_manager_candidates_by_lane=diversify,
        lane_distribution=lane_distribution,
        manager_candidate_packets=lambda **kwargs: {
            "top_movers": [{"symbol": kwargs["selected_candidates"][0]["symbol"]}]
        },
        manager_candidate_stage_counts=lambda **kwargs: {
            "manager_candidates": len(kwargs["selected_candidates"])
        },
        market_side_lane=lambda row: row.get("calculated", {}).get("lane", ""),
    )

    selected, metadata = finalize_manager_candidates(
        candidates=candidates,
        hooks=hooks,
        max_items=2,
        active_blocks=[{"block_id": "B1"}],
        live_authority={"status": "ok"},
        entry_gate_policy={"min_edge": 50},
        crypto_research={"candidates": [{}, {}, {}]},
        crypto_patterns={"optimized_strategy_sets": [{}, {}]},
        market_universe={"spot": ["BTCUSDT"], "futures": ["ETHUSDT"]},
        provided_candidate_count=4,
        spot_shadow_count=1,
        futures_shadow_count=2,
        upbit_shadow_count=3,
        skipped=[{"symbol": "SKIP0"} for _ in range(10)],
    )

    assert [row["symbol"] for row in selected] == ["BTCUSDT", "ETHUSDT"]
    assert selected[0]["near_duplicate_active_block"] == {"block_id": "B1"}
    assert selected[0]["lane_authority_candidate"] == {"action": "allow_probe"}
    assert selected[0]["metadata"]["lane_authority_candidate"] == {
        "action": "allow_probe"
    }
    assert selected[0]["calculated"]["empirical_edge_score"] == 91.0
    assert selected[1]["execution_blockers"] == {"reason": "spread_too_wide"}
    assert selected[1]["metadata"]["execution_blockers"] == {
        "reason": "spread_too_wide"
    }
    assert all(row["pattern_scorecard_attached"] for row in selected)
    assert metadata["candidate_count"] == 2
    assert metadata["candidate_lane_diversified"] is True
    assert metadata["near_duplicate_candidate_count"] == 1
    assert metadata["lane_authority_candidate_count"] == 1
    assert metadata["execution_blocked_candidate_count"] == 1
    assert metadata["pattern_performance_candidate_count"] == 2
    assert metadata["volatile_attack_candidate_count"] == 1
    assert metadata["provided_candidate_count"] == 4
    assert metadata["research_candidate_count"] == 3
    assert metadata["optimized_strategy_set_count"] == 2
    assert metadata["spot_shadow_candidate_count"] == 1
    assert metadata["futures_shadow_candidate_count"] == 2
    assert metadata["upbit_shadow_candidate_count"] == 3
    assert len(metadata["skipped"]) == 8
