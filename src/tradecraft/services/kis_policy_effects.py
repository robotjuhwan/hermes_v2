from __future__ import annotations

import re
from typing import Any


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def normalized_gate_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def contains_any_token(value: Any, tokens: tuple[str, ...] | set[str]) -> bool:
    raw = str(value or "").strip().lower()
    compact = normalized_gate_token(value)
    return any(
        str(token).strip().lower()
        and (
            str(token).strip().lower() in compact
            or str(token).strip().lower() in raw
        )
        for token in tokens
    )


def policy_effects(
    impacts: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for impact in impacts[:8]:
        if not isinstance(impact, dict):
            continue
        effect = impact.get("effect") if isinstance(impact.get("effect"), dict) else {}
        if effect:
            rows.append((impact, effect))
    return rows


def policy_rule_ids(impacts: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for impact in impacts:
        rule_id = str(impact.get("rule_id") or "")
        if rule_id and rule_id not in out:
            out.append(rule_id)
    return out


def normalize_impacts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in list(value or []) if isinstance(row, dict)]


def dedupe_policy_impacts(impacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for impact in impacts:
        key = str(impact.get("rule_id") or impact.get("policy_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(impact)
    return out[:8]


def policy_rule_impacts_for_symbol(
    symbol: str,
    evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    by_symbol = (
        evaluation.get("by_symbol")
        if isinstance(evaluation.get("by_symbol"), dict)
        else {}
    )
    return dedupe_policy_impacts(
        [
            *normalize_impacts(evaluation.get("global")),
            *normalize_impacts(by_symbol.get(str(symbol or ""))),
        ]
    )


def candidate_policy_impacts_for_strategy(
    strategy_payload: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    candidates = (
        strategy_payload.get("candidates")
        if isinstance(strategy_payload.get("candidates"), list)
        else []
    )
    candidate_symbols = [
        str(row.get("symbol") or "").strip()
        for row in candidates[:80]
        if isinstance(row, dict) and str(row.get("symbol") or "").strip()
    ]
    global_impacts = normalize_impacts(evaluation.get("global"))
    replicate_global = len(candidate_symbols) <= 12
    out: dict[str, list[dict[str, Any]]] = {}
    if global_impacts and not replicate_global:
        out["_global"] = dedupe_policy_impacts(global_impacts)[:4]
    by_symbol = (
        evaluation.get("by_symbol")
        if isinstance(evaluation.get("by_symbol"), dict)
        else {}
    )
    for symbol in candidate_symbols:
        impacts = dedupe_policy_impacts(
            [
                *(global_impacts if replicate_global else []),
                *normalize_impacts(by_symbol.get(symbol)),
            ]
        )
        if impacts:
            out[symbol] = impacts[:4]
    return out


def policy_rule_impacts_for_block(
    block_id: str,
    evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    by_block = (
        evaluation.get("by_block")
        if isinstance(evaluation.get("by_block"), dict)
        else {}
    )
    return dedupe_policy_impacts(
        [
            *normalize_impacts(evaluation.get("global")),
            *normalize_impacts(by_block.get(str(block_id or ""))),
        ]
    )


def append_policy_reason(value: Any, impacts: list[dict[str, Any]]) -> str:
    base = _clean_text(value, limit=1600)
    summaries: list[str] = []
    for impact in impacts[:3]:
        policy_id = str(impact.get("policy_id") or impact.get("rule_id") or "policy")
        effect = impact.get("effect") if isinstance(impact.get("effect"), dict) else {}
        reason = str(effect.get("risk_note") or impact.get("reason") or "").strip()
        if not reason:
            continue
        summaries.append(f"{policy_id}: {reason}")
    if not summaries:
        return base
    suffix = "정책룰 반영 - " + " / ".join(summaries)
    if not base:
        return _clean_text(suffix, limit=2000)
    return _clean_text(f"{base}\n{suffix}", limit=2000)
