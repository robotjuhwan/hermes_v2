from __future__ import annotations

import json

import pytest

from tradecraft.services.manager_prompt_contract import ManagerPromptContractViolation
from tradecraft.services.market_judge_prompt import finalize_market_judge_prompt


@pytest.mark.parametrize("repeat", [1, 20, 200])
def test_market_judge_prompt_budget_preserves_core_types(repeat: int) -> None:
    prompt = {
        "account": {"orderable_cash_krw": 1_000_000},
        "symbols": [
            {
                "symbol": f"{index:06d}",
                "quote": {"price": 70_000},
                "strategy": {"score": 0.8},
                "rag": [{"content": "근거" * repeat * 200}],
            }
            for index in range(60)
        ],
        "strategy_summary": {"status": "ok", "sources": ["naver_reports"]},
        "market_pulse": {"status": "ok"},
        "investment_memory": {
            "status": "ok",
            "notes": ["기억" * repeat * 100],
        },
    }

    bundle = finalize_market_judge_prompt(
        prompt,
        target_chars=120_000,
        warn_chars=150_000,
        max_chars=190_000,
    )
    runtime = bundle.runtime_prompt

    assert isinstance(runtime["symbols"], list)
    assert isinstance(runtime["account"], dict)
    assert isinstance(runtime["strategy_summary"], dict)
    assert isinstance(runtime["market_pulse"], dict)
    assert isinstance(runtime["investment_memory"], dict)
    assert all(isinstance(item, dict) for item in runtime["symbols"])
    assert len(json.dumps(runtime, ensure_ascii=False)) <= 190_000
    assert bundle.audit_prompt["symbols"]["item_count"] == 60


def test_market_judge_prompt_rejects_irreducible_core_overflow() -> None:
    with pytest.raises(
        ManagerPromptContractViolation,
        match="prompt_budget_contract_violation",
    ):
        finalize_market_judge_prompt(
            {
                "account": {"irreducible": "x" * 20_000},
                "symbols": [{"symbol": "005930"}],
                "strategy_summary": {},
            },
            target_chars=6_000,
            warn_chars=8_000,
            max_chars=10_000,
        )


def test_market_judge_prompt_rejects_core_type_changes() -> None:
    with pytest.raises(
        ManagerPromptContractViolation,
        match="symbols must be a list",
    ):
        finalize_market_judge_prompt(
            {"account": {}, "symbols": {"item_count": 1}, "strategy_summary": {}},
            target_chars=120_000,
            warn_chars=150_000,
            max_chars=190_000,
        )
