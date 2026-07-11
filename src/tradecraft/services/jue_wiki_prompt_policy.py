from __future__ import annotations

import json
from typing import Any

from tradecraft.services.jue_wiki_context import strip_direct_raw_rag_context

_PROTECTED_MARKERS = (
    "jue_wiki",
    "jue_wiki_decision_gate",
)


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def extract_wiki_context_packet(
    wrapper: Any,
    *,
    allow_legacy_flat: bool = False,
) -> dict[str, Any]:
    if not isinstance(wrapper, dict):
        raise ValueError("jue_wiki_context_wrapper_invalid")
    nested = wrapper.get("jue_wiki_context_packet")
    if isinstance(nested, dict):
        return nested
    if allow_legacy_flat and wrapper.get("version") == "wiki_context_packet_v1":
        return wrapper
    raise ValueError("jue_wiki_context_packet_missing")


def preserve_wiki_context_packet(
    prompt: dict[str, Any],
    provider_wrapper: Any,
) -> None:
    if not isinstance(prompt.get("jue_wiki"), dict):
        return
    try:
        packet = extract_wiki_context_packet(provider_wrapper)
    except ValueError:
        return
    prompt["jue_wiki"]["jue_wiki_context_packet"] = _copy(packet)


def _required_gate(prompt: dict[str, Any]) -> dict[str, Any]:
    wrapper = prompt.get("jue_wiki")
    nested = extract_wiki_context_packet(wrapper, allow_legacy_flat=True)
    source_gate = (
        prompt.get("jue_wiki_decision_gate")
        if isinstance(prompt.get("jue_wiki_decision_gate"), dict)
        else {}
    )
    eligible = bool(
        nested.get("coverage_status") == "sufficient"
        and nested.get("required_eligible") is True
        and str(nested.get("snapshot_id") or source_gate.get("snapshot_id") or "").strip()
    )
    return {
        "allow_new_risk": eligible,
        "allow_exit_actions": True,
        "reason": "wiki_context_eligible" if eligible else "wiki_required_mode_ineligible",
        "read_mode": "required",
        "snapshot_id": str(
            nested.get("snapshot_id") or source_gate.get("snapshot_id") or ""
        )[:120],
        "version": "wiki_decision_gate_v1",
    }


def attach_jue_wiki_decision_gate(
    prompt: dict[str, Any],
    jue_wiki: dict[str, Any] | None,
    *,
    trusted_read_mode: str,
    venue: str,
) -> None:
    read_mode = str(trusted_read_mode or "shadow").strip().lower()
    payload = jue_wiki if isinstance(jue_wiki, dict) else {}
    supplied = (
        dict(payload.get("jue_wiki_decision_gate"))
        if isinstance(payload.get("jue_wiki_decision_gate"), dict)
        else {}
    )
    if read_mode != "required":
        gate = {
            "allow_new_risk": True,
            "allow_exit_actions": True,
            "reason": "wiki_context_advisory",
            "read_mode": read_mode,
            "snapshot_id": "",
            "version": "wiki_decision_gate_v1",
        }
    else:
        invalid = ""
        if not supplied:
            invalid = "wiki_required_gate_missing"
        elif supplied.get("version") != "wiki_decision_gate_v1":
            invalid = "wiki_required_gate_invalid:version"
        elif supplied.get("read_mode") != "required":
            invalid = "wiki_required_gate_invalid:read_mode"
        elif type(supplied.get("allow_new_risk")) is not bool:
            invalid = "wiki_required_gate_invalid:allow_new_risk"
        elif supplied.get("allow_exit_actions") is not True:
            invalid = "wiki_required_gate_invalid:allow_exit_actions"
        elif not isinstance(supplied.get("reason"), str) or not supplied["reason"]:
            invalid = "wiki_required_gate_invalid:reason"
        elif len(supplied["reason"]) > 120:
            invalid = "wiki_required_gate_invalid:reason"
        elif not isinstance(supplied.get("snapshot_id"), str) or len(
            supplied["snapshot_id"]
        ) > 120:
            invalid = "wiki_required_gate_invalid:snapshot_id"
        elif supplied["allow_new_risk"] is True and supplied["reason"] != "wiki_context_eligible":
            invalid = "wiki_required_gate_invalid:reason"
        elif supplied["allow_new_risk"] is False and not supplied["reason"].startswith(
            "wiki_required_"
        ):
            invalid = "wiki_required_gate_invalid:reason"
        elif supplied["allow_new_risk"] is True and not supplied["snapshot_id"].strip():
            invalid = "wiki_required_gate_invalid:snapshot_id"
        if invalid:
            gate = {
                "allow_new_risk": False,
                "allow_exit_actions": True,
                "reason": invalid,
                "read_mode": "required",
                "snapshot_id": "",
                "version": "wiki_decision_gate_v1",
            }
        else:
            gate = {
                "allow_new_risk": supplied["allow_new_risk"],
                "allow_exit_actions": True,
                "reason": supplied["reason"],
                "read_mode": "required",
                "snapshot_id": supplied["snapshot_id"],
                "version": "wiki_decision_gate_v1",
            }
        if payload.get("status") != "ok" and gate["allow_new_risk"] is True:
            gate = {
                **gate,
                "allow_new_risk": False,
                "reason": "wiki_required_context_unavailable",
            }
    prompt["jue_wiki_decision_gate"] = gate
    prompt["jue_wiki_decision_gate_policy"] = {
        "instruction": (
            "When allow_new_risk is false, create and risk-increasing actions are "
            "rejected. Close, pause, reduce-only, reconciliation, stop tightening, "
            f"and {venue} kill-switch behavior remain valid."
        )
    }
    markers = [
        str(value)
        for value in prompt.get("decision_inputs", [])
        if isinstance(value, str)
    ]
    if "jue_wiki_decision_gate" not in markers:
        markers.append("jue_wiki_decision_gate")
    prompt["decision_inputs"] = markers


def apply_jue_wiki_prompt_policy(
    prompt: dict[str, Any],
    *,
    target_read_mode: str,
    source_to_required: bool = False,
) -> dict[str, Any]:
    """Apply the final production Wiki prompt boundary as a pure transformation."""

    read_mode = str(target_read_mode or "shadow").strip().lower()
    if read_mode not in {"shadow", "prefer", "required"}:
        raise ValueError("jue_wiki_prompt_policy_read_mode_invalid")
    transformed = _copy(prompt)
    wrapper = transformed.get("jue_wiki")
    if not isinstance(wrapper, dict):
        if read_mode in {"shadow", "prefer"} and not source_to_required:
            return transformed
        raise ValueError("jue_wiki_prompt_policy_packet_missing")
    if not isinstance(transformed.get("jue_wiki_application"), dict):
        if read_mode in {"shadow", "prefer"} and not source_to_required:
            return transformed
        raise ValueError("jue_wiki_prompt_policy_application_missing")
    if source_to_required:
        read_mode = "required"
        packet = extract_wiki_context_packet(wrapper, allow_legacy_flat=True)
        source_read_mode = str(packet.get("read_mode") or "shadow")
        packet["read_mode"] = "required"
        packet["required_eligible"] = True
        wrapper["read_mode"] = "required"
        transformed["jue_wiki_shadow_qualification_assumption"] = {
            "version": "wiki_shadow_qualification_assumption_v1",
            "source_read_mode": source_read_mode,
            "target_read_mode": "required",
            "assumed_required_eligible": True,
            "live_settings_changed": False,
        }
        transformed["jue_wiki_decision_gate"] = _required_gate(transformed)
    gate = transformed.get("jue_wiki_decision_gate")
    if not isinstance(gate, dict):
        raise ValueError("jue_wiki_prompt_policy_gate_missing")
    if read_mode == "required":
        previous_audit = (
            transformed.get("jue_wiki_raw_rag_strip_audit")
            if isinstance(transformed.get("jue_wiki_raw_rag_strip_audit"), dict)
            else {}
        )
        transformed, removed = strip_direct_raw_rag_context(transformed)
        gate = transformed["jue_wiki_decision_gate"]
        removed_paths = list(
            dict.fromkeys(
                [
                    *[
                        str(value)
                        for value in previous_audit.get("removed_paths", [])
                        if str(value)
                    ],
                    *removed,
                ]
            )
        )
        transformed["jue_wiki_raw_rag_strip_audit"] = {
            "read_mode": "required",
            "snapshot_id": str(gate.get("snapshot_id") or "")[:120],
            "removed_path_count": len(removed_paths),
            "removed_paths": [str(path)[:160] for path in removed_paths[:32]],
        }
    decision_inputs = [
        str(value)
        for value in transformed.get("decision_inputs", [])
        if isinstance(value, str)
    ]
    for marker in _PROTECTED_MARKERS:
        if marker not in decision_inputs:
            decision_inputs.append(marker)
    if read_mode == "required" and "jue_wiki_raw_rag_strip_audit" not in decision_inputs:
        decision_inputs.append("jue_wiki_raw_rag_strip_audit")
    transformed["decision_inputs"] = list(dict.fromkeys(decision_inputs))
    return transformed
