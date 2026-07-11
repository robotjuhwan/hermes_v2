from __future__ import annotations

from tradecraft.services.binance_manager_prompt import (
    finalize_prompt_budget,
    prompt_budget_error,
    prompt_chars,
)


def test_finalize_prompt_budget_preserves_runtime_core_sequence_types() -> None:
    prompt = {
        "native_thread_mode": "daily",
        "native_thread_key": "binance:block_manager:{date}",
        "critical_response_contract": {
            f"required_rule_{index}": {"required": True}
            for index in range(2_000)
        },
        "decision_inputs": ["account", "candidates", "blocks"],
        "candidates": [
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "side": "long",
                "calculated": {
                    "market_inputs": {
                        "bid_price": 100.0,
                        "ask_price": 101.0,
                    }
                },
            },
            {"symbol": "ETHUSDT", "market": "futures", "side": "short"},
        ],
        "blocks": [
            {"block_id": "block-1", "symbol": "BTCUSDT", "status": "open"}
        ],
        "universe": ["BTCUSDT", "ETHUSDT"],
        "memory": {"raw_context": "MEMORY_BLOAT " * 20_000},
        "jue_wiki_application": {"raw_context": "WIKI_BLOAT " * 20_000},
        "jue_wiki_budget_report": {"status": "ok", "max_chars": 24_000},
        "recent_performance": {"window": {"limit": 20}, "sample_count": 4},
        "output_schema": {"create_blocks": [{"symbol": "BTCUSDT"}]},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=10_000,
        warn_chars=12_000,
        max_chars=14_000,
    )

    assert prompt["native_thread_key"] == "binance:block_manager:{date}"
    assert isinstance(prompt["decision_inputs"], list)
    assert isinstance(prompt["candidates"], list)
    assert isinstance(prompt["blocks"], list)
    assert isinstance(prompt["universe"], list)
    assert prompt["candidates"][0]["calculated"]["market_inputs"] == {
        "bid_price": 100.0,
        "ask_price": 101.0,
    }
    assert prompt["jue_wiki_budget_report"]["status"] == "ok"
    assert prompt["recent_performance"]["window"]["limit"] == 20
    assert prompt_budget_error(prompt) == ""
    assert prompt_chars(prompt) <= 14_000
