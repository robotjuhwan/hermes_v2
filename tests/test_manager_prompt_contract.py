from __future__ import annotations

import pytest

from tradecraft.services.manager_prompt_contract import (
    ManagerPromptContractViolation,
    build_manager_prompt_bundle,
)


def test_manager_prompt_bundle_keeps_runtime_and_audit_shapes_separate() -> None:
    runtime_prompt = {
        "decision_inputs": ["account", "candidates", "blocks"],
        "candidates": [{"symbol": "005930"}, {"symbol": "000660"}],
        "blocks": [{"block_id": "blk-1"}],
    }

    def build_audit(value: dict) -> dict:
        value["candidates"] = {
            "item_count": 2,
            "items": value["candidates"][:1],
        }
        return value

    bundle = build_manager_prompt_bundle(
        runtime_prompt,
        audit_prompt_builder=build_audit,
    )

    assert bundle.runtime_prompt["decision_inputs"] == [
        "account",
        "candidates",
        "blocks",
    ]
    assert bundle.runtime_prompt["candidates"] == [
        {"symbol": "005930"},
        {"symbol": "000660"},
    ]
    assert bundle.runtime_prompt["blocks"] == [{"block_id": "blk-1"}]
    assert bundle.audit_prompt["candidates"] == {
        "item_count": 2,
        "items": [{"symbol": "005930"}],
    }
    assert bundle.compaction_meta["candidates"] == {
        "item_count": 2,
        "retained_item_count": 1,
        "omitted_item_count": 1,
    }


def test_manager_prompt_bundle_rejects_storage_shape_in_runtime_core() -> None:
    with pytest.raises(
        ManagerPromptContractViolation,
        match="prompt_budget_contract_violation: candidates must be a list",
    ):
        build_manager_prompt_bundle(
            {
                "decision_inputs": ["candidates"],
                "candidates": {"item_count": 1, "items": []},
                "blocks": [],
            },
            audit_prompt_builder=lambda value: value,
        )
