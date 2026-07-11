from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any


SectionSizeRows = Callable[[dict[str, Any]], list[dict[str, Any]]]
PromptChars = Callable[[dict[str, Any]], int]
JUE_WIKI_BUDGET_PROTECTED_KEYS = (
    "jue_wiki",
    "account",
    "live_authority",
    "market_pulse",
    "crypto_market_pulse",
    "raw_context_refs",
    "raw_rag",
)


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _trim_large_string(value: str, *, max_chars: int) -> str:
    if _json_chars(value) <= max_chars:
        return value
    suffix = "...[trimmed_for_prompt_budget]"
    keep_chars = max(max_chars - len(suffix), 0)
    return f"{value[:keep_chars]}{suffix}"


def _trim_non_protected_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, str):
        return _trim_large_string(value, max_chars=max_chars)
    if isinstance(value, dict):
        trimmed = dict(value)
        for key, nested_value in list(trimmed.items()):
            if isinstance(nested_value, str):
                trimmed[key] = _trim_large_string(nested_value, max_chars=max_chars)
        return trimmed
    return value


def enforce_manager_prompt_budget(
    payload: dict[str, Any],
    *,
    max_chars: int,
    protected_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    protected = set(protected_keys)
    trimmed = dict(payload)
    per_value_limit = max(int(max_chars) // max(len(trimmed), 1), 120)
    for key, value in list(trimmed.items()):
        if key in protected:
            continue
        trimmed[key] = _trim_non_protected_value(value, max_chars=per_value_limit)
    return trimmed


def enforce_manager_prompt_budget_with_report(
    payload: dict[str, Any],
    *,
    max_chars: int,
    protected_keys: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    trimmed = enforce_manager_prompt_budget(
        payload,
        max_chars=max_chars,
        protected_keys=protected_keys,
    )
    sections: dict[str, Any] = {}
    for key, value in trimmed.items():
        sections[key] = {
            "chars": len(json.dumps(value, ensure_ascii=False, default=str)),
            "protected": key in protected_keys,
        }
    total_chars = len(json.dumps(trimmed, ensure_ascii=False, default=str))
    return trimmed, {
        "max_chars": max_chars,
        "total_chars": total_chars,
        "sections": sections,
        "status": "ok" if total_chars <= max_chars else "over_budget",
    }


def attach_jue_wiki_budget_report(
    prompt: dict[str, Any],
    *,
    max_chars: int,
) -> None:
    if "jue_wiki" not in prompt:
        return
    report_payload = {
        key: value for key, value in prompt.items() if key != "jue_wiki_budget_report"
    }
    original_total_chars = _json_chars(report_payload)
    _, report = enforce_manager_prompt_budget_with_report(
        report_payload,
        max_chars=max_chars,
        protected_keys=JUE_WIKI_BUDGET_PROTECTED_KEYS,
    )
    projected_total_chars = int(report.get("total_chars") or 0)
    report["original_total_chars"] = original_total_chars
    report["projected_total_chars"] = projected_total_chars
    report["projected_status"] = str(report.get("status") or "")
    report["total_chars"] = original_total_chars
    report["status"] = "ok" if original_total_chars <= max_chars else "over_budget"
    prompt["jue_wiki_budget_report"] = report


def attach_prompt_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
    section_size_rows: SectionSizeRows,
    prompt_chars: PromptChars,
    policy: str,
    required_sections: Iterable[str] = (),
    section_limit: int = 18,
) -> None:
    target = max(int(target_chars), 1_000)
    warn = max(int(warn_chars), target)
    max_allowed = max(int(max_chars), warn)
    sections = section_size_rows(prompt)
    selected_sections = list(sections[: max(int(section_limit), 0)])
    selected_names = {str(row.get("section") or "") for row in selected_sections}
    for required in required_sections:
        required_name = str(required or "")
        if not required_name or required_name in selected_names:
            continue
        match = next(
            (row for row in sections if str(row.get("section") or "") == required_name),
            None,
        )
        if match is not None:
            selected_sections.append(match)
            selected_names.add(required_name)
    summary = {
        "version": "prompt_budget_v1",
        "target_chars": target,
        "warn_chars": warn,
        "max_chars": max_allowed,
        "total_chars": 0,
        "over_target": False,
        "over_warn": False,
        "over_max": False,
        "sections": selected_sections,
        "policy": str(policy or ""),
    }
    prompt["prompt_budget"] = summary
    total_chars = int(prompt_chars(prompt))
    summary["total_chars"] = total_chars
    summary["over_target"] = total_chars > target
    summary["over_warn"] = total_chars > warn
    summary["over_max"] = total_chars > max_allowed


def prompt_budget_error(prompt: dict[str, Any]) -> str:
    budget = prompt.get("prompt_budget")
    if not isinstance(budget, dict) or not bool(budget.get("over_max")):
        return ""
    total = int(budget.get("total_chars") or 0)
    max_allowed = int(budget.get("max_chars") or 0)
    return (
        "prompt_budget_contract_violation: prompt_budget_exceeded: "
        f"total_chars={total} max_chars={max_allowed}"
    )


def format_prompt_budget_alert_message(
    *,
    venue: str,
    run_id: int,
    error_message: str,
    prompt: dict[str, Any],
) -> str:
    budget = prompt.get("prompt_budget")
    budget = budget if isinstance(budget, dict) else {}
    total = int(budget.get("total_chars") or 0)
    target = int(budget.get("target_chars") or 0)
    warn = int(budget.get("warn_chars") or 0)
    max_allowed = int(budget.get("max_chars") or 0)
    return "\n".join(
        [
            f"[HERMES] {venue} 쥬 판단 입력 상한 초과",
            f"- run_id: {run_id}",
            f"- total: {total:,} chars",
            f"- target/warn/max: {target:,} / {warn:,} / {max_allowed:,}",
            f"- error: {error_message}",
            "- 조치: LLM 호출은 중단했고 error run으로 기록했습니다.",
        ]
    )
