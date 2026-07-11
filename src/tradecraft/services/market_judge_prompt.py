from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from tradecraft.services.manager_prompt_contract import ManagerPromptContractViolation


@dataclass(frozen=True)
class MarketJudgePromptCoreV1:
    account: dict[str, Any]
    symbols: list[dict[str, Any]]
    strategy_summary: dict[str, Any]
    version: str = "market_judge_prompt_core_v1"

    @classmethod
    def from_prompt(cls, prompt: dict[str, Any]) -> MarketJudgePromptCoreV1:
        account = prompt.get("account")
        symbols = prompt.get("symbols")
        strategy_summary = prompt.get("strategy_summary")
        if not isinstance(account, dict):
            raise ManagerPromptContractViolation(
                "prompt_budget_contract_violation: account must be an object"
            )
        if not isinstance(symbols, list):
            raise ManagerPromptContractViolation(
                "prompt_budget_contract_violation: symbols must be a list"
            )
        if not all(isinstance(item, dict) for item in symbols):
            raise ManagerPromptContractViolation(
                "prompt_budget_contract_violation: symbols items must be objects"
            )
        if not isinstance(strategy_summary, dict):
            raise ManagerPromptContractViolation(
                "prompt_budget_contract_violation: strategy_summary must be an object"
            )
        return cls(
            account=deepcopy(account),
            symbols=[deepcopy(item) for item in symbols],
            strategy_summary=deepcopy(strategy_summary),
        )


@dataclass(frozen=True)
class MarketJudgePromptBundle:
    runtime_prompt: dict[str, Any]
    audit_prompt: dict[str, Any]
    core: MarketJudgePromptCoreV1
    compaction_meta: dict[str, Any]
    version: str = "market_judge_prompt_bundle_v1"


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _compact_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
    depth: int = 0,
) -> Any:
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[:string_limit]
    if isinstance(value, list):
        if depth >= 8:
            return []
        return [
            _compact_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for item in value[:list_limit]
        ]
    if isinstance(value, dict):
        if depth >= 8:
            return {}
        return {
            str(key): _compact_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for key, item in value.items()
        }
    return value


def _optional_evidence_counts(prompt: dict[str, Any]) -> dict[str, int]:
    symbols = prompt.get("symbols") if isinstance(prompt.get("symbols"), list) else []
    rag_count = 0
    for symbol in symbols:
        if isinstance(symbol, dict) and isinstance(symbol.get("rag"), list):
            rag_count += len(symbol["rag"])
    memory = prompt.get("investment_memory")
    memory_rows = 0
    if isinstance(memory, dict):
        for value in memory.values():
            if isinstance(value, list):
                memory_rows += len(value)
    return {
        "symbol_count": len(symbols),
        "rag_item_count": rag_count,
        "memory_row_count": memory_rows,
    }


def _compact_runtime_optional_evidence(
    prompt: dict[str, Any],
    *,
    string_limit: int,
    list_limit: int,
) -> None:
    symbols = prompt.get("symbols")
    if isinstance(symbols, list):
        compacted_symbols: list[dict[str, Any]] = []
        for raw_symbol in symbols:
            if not isinstance(raw_symbol, dict):
                continue
            symbol = deepcopy(raw_symbol)
            for key in ("rag", "valuation", "prior_judgment", "source_diagnostics"):
                if key in symbol:
                    symbol[key] = _compact_value(
                        symbol[key],
                        string_limit=string_limit,
                        list_limit=list_limit,
                    )
            for key in ("strategy", "quote", "position"):
                if key in symbol:
                    symbol[key] = _compact_value(
                        symbol[key],
                        string_limit=max(string_limit, 240),
                        list_limit=max(list_limit, 4),
                    )
            compacted_symbols.append(symbol)
        prompt["symbols"] = compacted_symbols

    protected = {"account", "symbols", "strategy_summary"}
    for key in list(prompt):
        if key in protected:
            continue
        if key in {
            "market_pulse",
            "investment_memory",
            "previous_judgment",
            "research",
        } or key.startswith("jue_wiki"):
            prompt[key] = _compact_value(
                prompt[key],
                string_limit=string_limit,
                list_limit=list_limit,
            )


def compact_market_judge_audit_prompt(prompt: dict[str, Any]) -> dict[str, Any]:
    audit = deepcopy(prompt)
    raw_symbols = audit.get("symbols") if isinstance(audit.get("symbols"), list) else []
    audit["symbols"] = {
        "item_count": len(raw_symbols),
        "items": [
            _compact_value(item, string_limit=400, list_limit=4)
            for item in raw_symbols[:12]
            if isinstance(item, dict)
        ],
    }
    for key in ("market_pulse", "investment_memory", "previous_judgment"):
        if key in audit:
            audit[key] = _compact_value(
                audit[key],
                string_limit=400,
                list_limit=8,
            )
    return audit


def finalize_market_judge_prompt(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
) -> MarketJudgePromptBundle:
    if not isinstance(prompt, dict):
        raise ManagerPromptContractViolation(
            "prompt_budget_contract_violation: prompt must be an object"
        )
    target = max(int(target_chars), 1)
    warn = max(int(warn_chars), target)
    maximum = max(int(max_chars), warn)
    runtime = deepcopy(prompt)
    MarketJudgePromptCoreV1.from_prompt(runtime)
    original_counts = _optional_evidence_counts(runtime)
    original_chars = _json_chars(runtime)

    if original_chars > target:
        for string_limit, list_limit in ((1_200, 8), (400, 4), (120, 2)):
            _compact_runtime_optional_evidence(
                runtime,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            if _json_chars(runtime) <= target:
                break

    included_counts = _optional_evidence_counts(runtime)
    compaction_meta = {
        "version": "market_judge_prompt_compaction_meta_v1",
        "original_chars": original_chars,
        "original_counts": original_counts,
        "included_counts": included_counts,
        "omitted_counts": {
            key: max(original_counts[key] - included_counts.get(key, 0), 0)
            for key in original_counts
        },
    }
    runtime["compaction_meta"] = deepcopy(compaction_meta)
    runtime["prompt_budget"] = {
        "target_chars": target,
        "warn_chars": warn,
        "max_chars": maximum,
        "original_chars": original_chars,
        "compacted": original_chars != _json_chars(runtime),
    }
    runtime_chars = _json_chars(runtime)
    runtime["prompt_budget"]["runtime_chars"] = runtime_chars
    runtime["prompt_budget"]["over_warn"] = runtime_chars > warn
    runtime_chars = _json_chars(runtime)
    runtime["prompt_budget"]["runtime_chars"] = runtime_chars

    if runtime_chars > maximum:
        raise ManagerPromptContractViolation(
            "prompt_budget_contract_violation: market judge runtime prompt "
            f"{runtime_chars} chars exceeds max {maximum}"
        )

    core = MarketJudgePromptCoreV1.from_prompt(runtime)
    audit = compact_market_judge_audit_prompt(prompt)
    audit["compaction_meta"] = deepcopy(compaction_meta)
    audit["prompt_budget"] = deepcopy(runtime["prompt_budget"])
    return MarketJudgePromptBundle(
        runtime_prompt=runtime,
        audit_prompt=audit,
        core=core,
        compaction_meta=compaction_meta,
    )
