from __future__ import annotations

from typing import Any


def _workflow(payload: dict[str, Any]) -> dict[str, Any]:
    workflow = payload.get("jue_workflow")
    return workflow if isinstance(workflow, dict) else {}


def build_codex_instruction_pack(
    payload: dict[str, Any],
    *,
    component: str,
    model: str,
    reasoning_effort: str,
) -> dict[str, str]:
    workflow = _workflow(payload)
    workflow_id = str(workflow.get("workflow_id") or "generic")
    scope = str(workflow.get("scope") or component or "HERMES runtime")
    language_policy = (
        workflow.get("language_policy")
        if isinstance(workflow.get("language_policy"), dict)
        else {}
    )
    authority = (
        workflow.get("authority") if isinstance(workflow.get("authority"), dict) else {}
    )
    safety_gates = (
        workflow.get("safety_gates") if isinstance(workflow.get("safety_gates"), list) else []
    )

    base_instructions = (
        "You are HERMES/Jue running inside the Codex native runtime. "
        "You are an active trading partner for block-based trading research, "
        "judgment, and reflection. You never bypass HERMES safety gates or "
        "adapters; you return structured decisions only."
    )

    developer_lines = [
        f"Workflow: {workflow_id}",
        f"Scope: {scope}",
        f"Runtime model: {model}",
        f"Reasoning effort: {reasoning_effort}",
        "Think in English for analysis and structure.",
        "Respond to the user in Korean when user-visible text is required.",
        "Separate evidence, thesis, risk, execution price structure, and next action.",
        "Do not invent account balances, fills, prices, research citations, or block state.",
        "If required evidence is absent, mark the gap explicitly and keep the action executable.",
    ]
    if language_policy:
        developer_lines.append(f"Language policy: {language_policy}")
    if authority:
        developer_lines.append(f"Authority boundaries: {authority}")
    if safety_gates:
        developer_lines.append(
            "Safety gates that always outrank strategy: "
            + ", ".join(str(item) for item in safety_gates)
        )

    return {
        "base_instructions": base_instructions,
        "developer_instructions": "\n".join(developer_lines),
    }
