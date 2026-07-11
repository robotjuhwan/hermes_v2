from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable


class ManagerPromptContractViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagerPromptCoreV2:
    decision_inputs: list[str]
    candidates: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    version: str = "manager_prompt_core_v2"

    @classmethod
    def from_prompt(cls, prompt: dict[str, Any]) -> ManagerPromptCoreV2:
        values: dict[str, list[Any]] = {}
        for key in ("decision_inputs", "candidates", "blocks"):
            value = prompt.get(key, [])
            if not isinstance(value, list):
                raise ManagerPromptContractViolation(
                    f"prompt_budget_contract_violation: {key} must be a list"
                )
            values[key] = value
        if not all(isinstance(item, str) for item in values["decision_inputs"]):
            raise ManagerPromptContractViolation(
                "prompt_budget_contract_violation: decision_inputs items must be strings"
            )
        for key in ("candidates", "blocks"):
            if not all(isinstance(item, dict) for item in values[key]):
                raise ManagerPromptContractViolation(
                    f"prompt_budget_contract_violation: {key} items must be objects"
                )
        return cls(
            decision_inputs=list(values["decision_inputs"]),
            candidates=[dict(item) for item in values["candidates"]],
            blocks=[dict(item) for item in values["blocks"]],
        )


@dataclass(frozen=True)
class ManagerPromptBundle:
    runtime_prompt: dict[str, Any]
    audit_prompt: dict[str, Any]
    core: ManagerPromptCoreV2
    compaction_meta: dict[str, dict[str, int]]
    version: str = "manager_prompt_bundle_v1"


AuditPromptBuilder = Callable[[dict[str, Any]], dict[str, Any]]


def _audit_item_counts(value: Any) -> tuple[int, int]:
    if isinstance(value, list):
        return len(value), len(value)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        items = value.get("items") or []
        return int(value.get("item_count") or len(items)), len(items)
    return 0, 0


def build_manager_prompt_bundle(
    prompt: dict[str, Any],
    *,
    audit_prompt_builder: AuditPromptBuilder,
) -> ManagerPromptBundle:
    if not isinstance(prompt, dict):
        raise ManagerPromptContractViolation(
            "prompt_budget_contract_violation: prompt must be an object"
        )
    runtime_prompt = deepcopy(prompt)
    core = ManagerPromptCoreV2.from_prompt(runtime_prompt)
    runtime_prompt["decision_inputs"] = core.decision_inputs
    runtime_prompt["candidates"] = core.candidates
    runtime_prompt["blocks"] = core.blocks
    audit_prompt = audit_prompt_builder(deepcopy(runtime_prompt))
    if not isinstance(audit_prompt, dict):
        raise ManagerPromptContractViolation(
            "prompt_budget_contract_violation: audit prompt must be an object"
        )
    compaction_meta: dict[str, dict[str, int]] = {}
    for key, runtime_items in (
        ("decision_inputs", core.decision_inputs),
        ("candidates", core.candidates),
        ("blocks", core.blocks),
    ):
        audit_count, retained_count = _audit_item_counts(audit_prompt.get(key))
        item_count = len(runtime_items)
        if audit_count <= 0:
            audit_count = item_count
        compaction_meta[key] = {
            "item_count": item_count,
            "retained_item_count": min(retained_count, item_count),
            "omitted_item_count": max(item_count - retained_count, 0),
        }
    generated_runtime_meta = {
        "version": "manager_prompt_compaction_meta_v1",
        "sections": compaction_meta,
    }
    existing_runtime_meta = runtime_prompt.get("compaction_meta")
    if not (
        isinstance(existing_runtime_meta, dict)
        and isinstance(existing_runtime_meta.get("sections"), dict)
    ):
        runtime_prompt["compaction_meta"] = generated_runtime_meta
    audit_prompt["compaction_meta"] = deepcopy(generated_runtime_meta)
    return ManagerPromptBundle(
        runtime_prompt=runtime_prompt,
        audit_prompt=audit_prompt,
        core=core,
        compaction_meta=compaction_meta,
    )
