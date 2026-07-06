from __future__ import annotations

from tradecraft.services.binance_performance_policy import (
    budget_scope_from_scorecard_rows,
    candidate_quote_budget_usdt,
    candidate_upbit_quote_budget_details,
    lane_card_is_distressed,
    manager_candidate_empirical_edge_score,
    performance_scope_for_budget,
    quote_budget_details_from_amount,
    rank_manager_candidates_by_edge,
    weak_lane_profit_protection_trigger,
)


def test_rank_manager_candidates_by_edge_promotes_empirical_edge_over_raw_score() -> None:
    weak = {
        "symbol": "WEAKUSDT",
        "market": "spot",
        "side": "long",
        "score": 96,
        "confidence": 0.9,
        "calculated": {
            "reward_risk": 1.55,
            "pattern_live_crosscheck": {"status": "no_pattern_prior"},
        },
    }
    edge = {
        "symbol": "EDGEUSDT",
        "market": "spot",
        "side": "long",
        "score": 72,
        "confidence": 0.7,
        "calculated": {
            "reward_risk": 2.25,
            "pattern_live_crosscheck": {"status": "aligned"},
            "pattern_inputs": {
                "prior_quality": {"passed": True},
                "prior": {
                    "objective_score": 120.0,
                    "trade_count": 42,
                    "expectancy_r": 0.44,
                    "out_of_sample_expectancy_r": 0.32,
                    "profit_factor": 1.82,
                },
            },
        },
    }

    ranked = rank_manager_candidates_by_edge([weak, edge])

    assert ranked[0]["symbol"] == "EDGEUSDT"
    assert manager_candidate_empirical_edge_score(edge) > manager_candidate_empirical_edge_score(weak)


def test_manager_candidate_empirical_edge_score_demotes_cooldown_and_weak_pattern() -> None:
    strong_but_blocked = {
        "symbol": "WLDUSDT",
        "market": "spot",
        "side": "long",
        "score": 98,
        "confidence": 0.95,
        "pattern_performance_scorecard": {
            "sample_count": 5,
            "pnl_usdt": -1.25,
            "avg_r_multiple": -0.42,
            "win_rate_pct": 20.0,
            "profit_factor": 0.35,
            "recovery_factor": -1.0,
        },
        "execution_blockers": {
            "status": "would_reject_current_gates",
            "ranking_penalty": 95.0,
        },
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    steady = {
        "symbol": "ZECUSDT",
        "market": "futures",
        "side": "short",
        "score": 72,
        "confidence": 0.72,
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    entry_gate_policy = {
        "cooldown_symbol_keys": ["WLDUSDT"],
        "cooldown_lane_keys": ["spot:long"],
    }

    assert (
        manager_candidate_empirical_edge_score(
            strong_but_blocked,
            entry_gate_policy=entry_gate_policy,
        )
        < manager_candidate_empirical_edge_score(steady, entry_gate_policy=entry_gate_policy)
    )
    assert rank_manager_candidates_by_edge(
        [strong_but_blocked, steady],
        entry_gate_policy=entry_gate_policy,
    )[0]["symbol"] == "ZECUSDT"


def test_manager_candidate_empirical_edge_score_rewards_positive_lane_authority() -> None:
    positive_futures_short = {
        "symbol": "PAXGUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "score": 72,
        "confidence": 0.72,
        "lane_authority_candidate": {
            "selection_bias": "positive_sample_building",
            "sample_count": 7,
            "expectancy_pct": 0.25,
            "win_rate_pct": 57.1,
            "profit_factor": 2.88,
        },
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "wait"},
        },
    }
    weak_spot = {
        "symbol": "BIOUSDT",
        "market": "spot",
        "side": "long",
        "horizon": "short",
        "score": 98,
        "confidence": 0.94,
        "lane_authority_candidate": {
            "grade": "weak",
            "expectancy_pct": -1.2,
            "win_rate_pct": 15.6,
            "profit_factor": 0.04,
        },
        "calculated": {
            "reward_risk": 1.8,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }

    assert (
        manager_candidate_empirical_edge_score(positive_futures_short)
        > manager_candidate_empirical_edge_score(weak_spot)
    )


def test_lane_card_distress_policy_detects_weak_and_severe_small_sample_lanes() -> None:
    assert lane_card_is_distressed(
        {
            "sample_count": 6,
            "pnl_usdt": -0.53,
            "avg_r_multiple": -0.51,
            "win_rate_pct": 16.7,
            "profit_factor": 0.514,
            "max_drawdown_r_multiple": -5.45,
        },
        distressed_min_samples=5,
        lane_min_samples=3,
        max_win_rate_pct=20.0,
        max_profit_factor=0.5,
    ) is True
    assert lane_card_is_distressed(
        {
            "sample_count": 4,
            "pnl_usdt": -0.53,
            "avg_r_multiple": -0.51,
            "win_rate_pct": 16.7,
            "profit_factor": 0.3,
            "max_drawdown_r_multiple": -5.45,
        },
        distressed_min_samples=5,
        lane_min_samples=3,
        max_win_rate_pct=20.0,
        max_profit_factor=0.5,
    ) is False
    assert lane_card_is_distressed(
        {
            "sample_count": 12,
            "pnl_usdt": 0.2,
            "avg_r_multiple": -0.51,
            "win_rate_pct": 16.7,
            "profit_factor": 0.3,
            "max_drawdown_r_multiple": -5.45,
        },
        distressed_min_samples=5,
        lane_min_samples=3,
        max_win_rate_pct=20.0,
        max_profit_factor=0.5,
    ) is False


def test_weak_lane_profit_protection_trigger_prefers_distressed_lower_trigger() -> None:
    assert weak_lane_profit_protection_trigger(
        {"matched": True, "distressed": True},
        weak_trigger_r=0.8,
        distressed_trigger_r=0.55,
    ) == (0.55, "distressed_performance_lane")
    assert weak_lane_profit_protection_trigger(
        {"matched": True, "distressed": True},
        weak_trigger_r=0.8,
        distressed_trigger_r=1.2,
    ) == (0.8, "weak_performance_lane")
    assert weak_lane_profit_protection_trigger(
        {"matched": True, "distressed": False},
        weak_trigger_r=0.8,
        distressed_trigger_r=0.55,
    ) == (0.8, "weak_performance_lane")


def test_quote_budget_details_from_amount_converts_upbit_krw_to_usdt() -> None:
    assert quote_budget_details_from_amount(
        market="upbit_spot",
        quote_budget=15_000,
        upbit_usdt_krw_rate=1_500,
    ) == {
        "quote_budget": 15_000,
        "quote_currency": "KRW",
        "quote_budget_krw": 15_000,
        "quote_budget_usdt": 10.0,
    }
    assert quote_budget_details_from_amount(
        market="futures",
        quote_budget=25.678,
    ) == {
        "quote_budget": 25.68,
        "quote_currency": "USDT",
        "quote_budget_usdt": 25.68,
        "quote_budget_krw": 0.0,
    }


def test_candidate_quote_budget_usdt_uses_market_limits_and_multiplier() -> None:
    assert candidate_quote_budget_usdt(
        market="futures",
        cash_usdt=502,
        futures_quote_budget_pct=10,
        futures_min_quote_budget_usdt=25,
        futures_max_quote_budget_usdt=150,
        spot_quote_budget_pct=5,
        spot_min_quote_budget_usdt=50,
        spot_max_quote_budget_usdt=300,
        performance_multiplier=1.5,
    ) == 75.3
    assert candidate_quote_budget_usdt(
        market="spot",
        cash_usdt=100,
        futures_quote_budget_pct=10,
        futures_min_quote_budget_usdt=25,
        futures_max_quote_budget_usdt=150,
        spot_quote_budget_pct=5,
        spot_min_quote_budget_usdt=50,
        spot_max_quote_budget_usdt=300,
        performance_multiplier=0.4,
        min_notional_usdt=5,
    ) == 20.0
    assert candidate_quote_budget_usdt(
        market="spot",
        cash_usdt=0,
        futures_quote_budget_pct=10,
        futures_min_quote_budget_usdt=25,
        futures_max_quote_budget_usdt=150,
        spot_quote_budget_pct=5,
        spot_min_quote_budget_usdt=50,
        spot_max_quote_budget_usdt=300,
    ) == 10.0


def test_candidate_upbit_quote_budget_details_uses_krw_cash_and_floor() -> None:
    details = candidate_upbit_quote_budget_details(
        cash_krw=100_000,
        cash_usdt=0,
        upbit_usdt_krw_rate=1_500,
        quote_budget_pct=10,
        min_quote_budget_krw=10_000,
        max_quote_budget_krw=150_000,
        performance_multiplier=0.4,
    )

    assert details["quote_budget"] == 4_000
    assert details["quote_currency"] == "KRW"
    assert details["quote_budget_usdt"] == 2.666667
    assert details["performance_budget_multiplier"] == 0.4


def test_budget_scope_from_scorecard_rows_aggregates_weighted_performance() -> None:
    scope = budget_scope_from_scorecard_rows(
        [
            {
                "sample_count": 2,
                "pnl_usdt": 4.0,
                "win_rate_pct": 50.0,
                "avg_r_multiple": 1.0,
                "gross_profit_usdt": 5.0,
                "gross_loss_usdt": -1.0,
                "max_drawdown_usdt": -1.0,
                "max_drawdown_r_multiple": -0.5,
            },
            {
                "sample_count": 1,
                "pnl_usdt": -2.0,
                "win_rate_pct": 0.0,
                "avg_r_multiple": -1.0,
                "gross_profit_usdt": 0.0,
                "gross_loss_usdt": -2.0,
                "max_drawdown_usdt": -2.0,
                "max_drawdown_r_multiple": -1.5,
            },
        ]
    )

    assert scope["sample_count"] == 3
    assert scope["realized_pnl_usdt"] == 2.0
    assert scope["win_rate_pct"] == 100.0 / 3.0
    assert scope["avg_r_multiple"] == 1.0 / 3.0
    assert scope["gross_profit_usdt"] == 5.0
    assert scope["gross_loss_usdt"] == -3.0
    assert scope["profit_factor"] == 5.0 / 3.0
    assert scope["max_drawdown_usdt"] == -2.0
    assert scope["max_drawdown_r_multiple"] == -1.5
    assert scope["recovery_factor"] == 1.0


def test_performance_scope_for_budget_prefers_matching_lane_then_side() -> None:
    performance = {
        "lane_scorecards": [
            {
                "lane": "spot:long:short",
                "sample_count": 2,
                "pnl_usdt": 3.0,
                "win_rate_pct": 100.0,
                "avg_r_multiple": 1.0,
                "gross_profit_usdt": 3.0,
                "gross_loss_usdt": 0.0,
                "max_drawdown_usdt": 0.0,
                "max_drawdown_r_multiple": 0.0,
            },
            {
                "lane": "short",
                "sample_count": 1,
                "pnl_usdt": -1.0,
                "win_rate_pct": 0.0,
                "avg_r_multiple": -1.0,
                "gross_profit_usdt": 0.0,
                "gross_loss_usdt": -1.0,
                "max_drawdown_usdt": -1.0,
                "max_drawdown_r_multiple": -1.0,
            },
        ],
        "side_scorecards": [
            {
                "side": "futures:short",
                "sample_count": 4,
                "pnl_usdt": 8.0,
                "win_rate_pct": 75.0,
                "avg_r_multiple": 1.2,
                "gross_profit_usdt": 9.0,
                "gross_loss_usdt": -1.0,
                "max_drawdown_usdt": -0.5,
                "max_drawdown_r_multiple": -0.25,
            }
        ],
    }

    spot_short = performance_scope_for_budget(
        performance,
        market="spot",
        side="long",
        lane="short",
    )
    futures_short = performance_scope_for_budget(
        performance,
        market="futures",
        side="short",
    )

    assert spot_short["sample_count"] == 3
    assert spot_short["realized_pnl_usdt"] == 2.0
    assert spot_short["profit_factor"] == 3.0
    assert futures_short["sample_count"] == 4
    assert futures_short["realized_pnl_usdt"] == 8.0
