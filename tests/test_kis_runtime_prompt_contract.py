from __future__ import annotations

from tradecraft.services.kis_manager_prompt import finalize_prompt_budget


def test_kis_prompt_budget_never_changes_runtime_core_sequence_types() -> None:
    marker = "KIS_RUNTIME_CORE_BLOAT "
    prompt = {
        "decision_inputs": ["account", "candidates", "blocks"],
        "candidates": [
            {"symbol": f"{index:06d}", "reason": marker * 100}
            for index in range(20)
        ],
        "blocks": [
            {
                "block_id": f"blk-{index}",
                "symbol": f"{index:06d}",
                "thesis": marker * 100,
            }
            for index in range(20)
        ],
        "investment_memory": {"raw": marker * 5_000},
        "output_schema": {"raw": marker * 2_000},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=12_000,
    )

    assert isinstance(prompt["decision_inputs"], list)
    assert isinstance(prompt["candidates"], list)
    assert isinstance(prompt["blocks"], list)
    assert prompt["candidates"][0]["symbol"] == "000000"
    assert prompt["blocks"][0]["block_id"] == "blk-0"
    assert prompt["compaction_meta"]["sections"]["candidates"]["item_count"] == 20
    assert prompt["compaction_meta"]["sections"]["blocks"]["item_count"] == 20
