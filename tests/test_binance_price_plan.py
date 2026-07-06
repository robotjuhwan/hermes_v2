from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tradecraft.services.binance_price_plan import design_crypto_candidate_price_plan


def test_binance_block_trader_does_not_reown_price_plan_helpers() -> None:
    source = Path("src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def _reward_risk_meets_minimum(",
        "def _validation_repair_notional_floor(",
        "def _crypto_reward_risk(",
    ):
        assert marker not in source


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "min_candidate_stop_pct": 1.2,
        "min_liquidation_distance_pct": 20.0,
        "upbit_min_quote_budget_krw": 5_000.0,
        "volatile_attack_budget_multiplier": 0.2,
        "volatile_attack_min_reward_risk": 2.0,
        "volatile_attack_stop_multiplier": 1.35,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _callbacks(*, volatile: bool = False) -> dict[str, Any]:
    def quote_budget_details(**kwargs: Any) -> dict[str, Any]:
        amount = float(kwargs.get("quote_budget") or 100.0)
        if kwargs.get("market") == "upbit_spot":
            return {
                "quote_budget": 50_000.0,
                "quote_currency": "KRW",
                "quote_budget_usdt": 50.0,
                "quote_budget_krw": 50_000.0,
                "performance_budget_multiplier": 1.0,
            }
        return {
            "quote_budget": amount,
            "quote_currency": "USDT",
            "quote_budget_usdt": amount,
            "quote_budget_krw": 0.0,
            "performance_budget_multiplier": 1.0,
        }

    return {
        "volatile_context_builder": lambda **_: {
            "enabled": volatile,
            "score": 88.0 if volatile else 0.0,
        },
        "pattern_prior_quality": lambda _prior: {"passed": False, "failed": []},
        "pattern_live_crosscheck": lambda **_: {
            "status": "no_pattern_prior",
            "wait_reasons": [],
            "contradictions": [],
            "live_authority": {},
        },
        "live_authority_validation_gate": lambda _authority: {
            "status": "",
            "readiness": "",
            "reason": "",
        },
        "quote_budget_details": quote_budget_details,
        "quote_budget_details_from_amount": quote_budget_details,
        "cash_reference_usdt": lambda **_: 1_000.0,
    }


def test_price_plan_turns_observe_only_spot_into_waiting_entry() -> None:
    plan = design_crypto_candidate_price_plan(
        candidate={"symbol": "BNBUSDT", "entry_quality": "actionable_now"},
        features={
            "symbol": "BNBUSDT",
            "price": 600.0,
            "bid_price": 599.8,
            "ask_price": 600.2,
            "spread_bps": 6.0,
            "book_fresh": True,
            "change_pct_24h": 2.0,
            "entry_quality": "actionable_now",
        },
        market="spot",
        side="long",
        horizon="short",
        account={"spot_cash_usdt": 1_000.0},
        config=_config(),
        min_reward_risk_floor=1.5,
        pattern_prior={},
        live_authority={
            "status": "ok",
            "live_grade": "observe_only",
            "allow_scale_up": False,
        },
        **_callbacks(),
    )

    assert plan["entry_style"] == "wait_for_price"
    assert plan["raw_entry_quality"] == "wait_for_live_confluence"
    assert plan["entry_trigger_operator"] == "<="
    assert plan["entry_price"] < 600.0


def test_price_plan_volatile_attack_uses_waiting_wider_rr_smaller_budget() -> None:
    base_features = {
        "symbol": "MEMEUSDT",
        "price": 1.0,
        "bid_price": 0.999,
        "ask_price": 1.001,
        "spread_bps": 18.0,
        "book_fresh": True,
        "change_pct_24h": 22.0,
        "quote_volume_usdt": 75_000_000,
        "volume_expansion_ratio": 3.2,
        "wick_risk_score": 48.0,
        "squeeze_risk_score": 76.0,
        "entry_quality": "actionable_now",
    }

    regular = design_crypto_candidate_price_plan(
        candidate={"symbol": "MEMEUSDT", "entry_quality": "actionable_now"},
        features=base_features,
        market="spot",
        side="long",
        horizon="short",
        account={"spot_cash_usdt": 1_000.0},
        config=_config(),
        min_reward_risk_floor=1.5,
        pattern_prior={},
        live_authority={},
        **_callbacks(volatile=False),
    )
    volatile = design_crypto_candidate_price_plan(
        candidate={
            "symbol": "MEMEUSDT",
            "entry_quality": "actionable_now",
            "lane": "volatile_attack",
        },
        features=base_features,
        market="spot",
        side="long",
        horizon="short",
        account={"spot_cash_usdt": 1_000.0},
        config=_config(),
        min_reward_risk_floor=1.5,
        pattern_prior={},
        live_authority={},
        **_callbacks(volatile=True),
    )

    assert volatile["entry_style"] == "wait_for_price"
    assert volatile["lane"] == "volatile_attack"
    assert volatile["risk_pct"] > regular["risk_pct"]
    assert volatile["sizing_inputs"]["target_reward_risk"] >= 2.0
    assert volatile["quote_budget_usdt"] < regular["quote_budget_usdt"]
    assert volatile["market_inputs"]["volume_expansion_ratio"] == pytest.approx(3.2)


def test_upbit_price_plan_uses_krw_book_price_not_source_usdt_candidate_price() -> None:
    plan = design_crypto_candidate_price_plan(
        candidate={
            "symbol": "KRW-JTO",
            "price": 0.8094,
            "entry_quality": "wait_pullback",
        },
        features={
            "symbol": "KRW-JTO",
            "price": 1233.0,
            "bid_price": 1232.0,
            "ask_price": 1234.0,
            "spread_bps": 16.2,
            "book_fresh": True,
            "book_market": "upbit_spot",
            "change_pct_24h": -1.5,
        },
        market="upbit_spot",
        side="long",
        horizon="short",
        account={"upbit_cash_krw": 500_000.0},
        config=_config(),
        min_reward_risk_floor=1.5,
        pattern_prior={},
        live_authority={},
        **_callbacks(),
    )

    assert plan["quote_currency"] == "KRW"
    assert plan["market_inputs"]["last_price"] == pytest.approx(1233.0)
    assert plan["entry_price"] > 1_000.0
    assert plan["stop_price"] < plan["entry_price"] < plan["target_price"]
